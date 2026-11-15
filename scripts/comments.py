#!/usr/bin/env python3
"""Comment/docstring shadow tool.

Keeps committed code annotated while agent working code stays stripped.

The annotated tree is mirrored under ``.comments/`` (gitignored). ``strip``
saves the annotated working tree to the shadow and removes comments and
docstrings from the working tree. ``add`` merges the annotated shadow back
onto the current stripped code.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import io
import json
import os
import re
import subprocess
import tempfile
import tokenize
from collections.abc import Iterator
from pathlib import Path

SHADOW_DIR = ".comments"
MANIFEST = "manifest.json"
EXCLUDED_DIRS = {
    ".comments",
    ".pixi",
    ".venv",
    "__pycache__",
    "build",
    "dist",
    "_build",
}
PRAGMA_PREFIXES = (
    "# noqa",
    "# type: ignore",
    "# pragma:",
    "# fmt:",
    "# nosec",
    "# isort:",
    "# pyright:",
    "# pyrefly:",
    "# mypy:",
)
ENCODING_RE = re.compile(r"^#.*coding[:=]\s*[-\w.]+")


def strip_source(source: str) -> str:
    """Return ``source`` with docstrings and comments removed.

    Deterministic and idempotent: ``strip_source(strip_source(x))`` equals
    ``strip_source(x)``.
    """
    tree = ast.parse(source)
    without_docstrings = _strip_docstrings(source, tree)
    return _strip_comments(without_docstrings)


def _docstring_nodes(tree: ast.AST) -> list[tuple[ast.Expr, ast.AST]]:
    """Return ``(docstring Expr, parent node)`` pairs in ``tree``."""
    nodes: list[tuple[ast.Expr, ast.AST]] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = getattr(node, "body", None)
            if body and isinstance(body[0], ast.Expr):
                value = body[0].value
                if isinstance(value, ast.Constant) and isinstance(value.value, str):
                    nodes.append((body[0], node))
    return nodes


def _line_ending(line: str) -> str:
    """Return the line ending of ``line`` (``""`` when absent)."""
    if line.endswith("\r\n"):
        return "\r\n"
    if line.endswith("\n"):
        return "\n"
    return ""


def _strip_docstrings(source: str, tree: ast.AST) -> str:
    """Remove docstrings from ``source`` using the parsed ``tree``."""
    lines = source.splitlines(keepends=True)
    nodes = sorted(_docstring_nodes(tree), key=lambda pair: pair[0].lineno, reverse=True)
    for expr, parent in nodes:
        lineno = expr.lineno
        end_lineno = expr.end_lineno or lineno
        col = expr.col_offset
        end_col = expr.end_col_offset or col
        first = lines[lineno - 1]
        prefix = first[:col]
        if lineno == end_lineno:
            # Same-line form: ``def f(): """doc"""``.
            lines[lineno - 1] = prefix + "pass" + first[end_col:]
        elif len(getattr(parent, "body", [])) == 1 and prefix.strip() == "":
            # The docstring is the only statement: replace it with ``pass``.
            ending = _line_ending(lines[end_lineno - 1])
            lines[lineno - 1 : end_lineno] = [" " * col + "pass" + ending]
        elif prefix.strip() != "":
            # The first docstring line shares a line with code.
            last = lines[end_lineno - 1]
            suffix = last[end_col:].strip()
            if suffix.startswith(";"):
                suffix = suffix[1:].lstrip()
            if suffix:
                lines[lineno - 1] = (prefix + suffix).rstrip()
            else:
                lines[lineno - 1] = (prefix + "pass").rstrip()
            del lines[lineno:end_lineno]
        else:
            del lines[lineno - 1 : end_lineno]
    return "".join(lines)


def _is_pragma(comment: str, lineno: int) -> bool:
    """Return True when ``comment`` must survive stripping."""
    if lineno == 1 and comment.startswith("#!"):
        return True
    if lineno <= 2 and ENCODING_RE.match(comment):
        return True
    normalized = "# " + comment[1:].lstrip() if comment.startswith("#") else comment
    lowered = normalized.lower()
    return any(lowered.startswith(prefix) for prefix in PRAGMA_PREFIXES)


def _strip_comments(source: str) -> str:
    """Remove non-pragma comments from ``source``."""
    lines = source.splitlines(keepends=True)
    comments = [
        token
        for token in tokenize.generate_tokens(io.StringIO(source).readline)
        if token.type == tokenize.COMMENT and not _is_pragma(token.string, token.start[0])
    ]
    for token in sorted(comments, key=lambda item: item.start[0], reverse=True):
        row, col = token.start
        line = lines[row - 1]
        prefix = line[:col]
        if prefix.strip() == "":
            del lines[row - 1]
        else:
            lines[row - 1] = prefix.rstrip() + _line_ending(line)
    return "".join(lines)


def _sha256(path: Path) -> str:
    """Return the SHA-256 hex digest of ``path``."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sha256_text(text: str) -> str:
    """Return the SHA-256 hex digest of ``text`` encoded as UTF-8."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _read_text(path: Path) -> str:
    """Read ``path`` as UTF-8 without newline translation."""
    with path.open("r", encoding="utf-8", newline="") as handle:
        return handle.read()


def _write_text(path: Path, text: str) -> None:
    """Write ``text`` to ``path`` as UTF-8 without newline translation."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        handle.write(text)


def _load_manifest(root: Path) -> dict:
    """Load the shadow manifest, returning an empty one when absent."""
    path = root / SHADOW_DIR / MANIFEST
    if not path.exists():
        return {"version": 1, "files": {}}
    with path.open("r", encoding="utf-8") as handle:
        manifest = json.load(handle)
    manifest.setdefault("version", 1)
    manifest.setdefault("files", {})
    return manifest


def _save_manifest(root: Path, manifest: dict) -> None:
    """Write the shadow manifest."""
    path = root / SHADOW_DIR / MANIFEST
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        handle.write(json.dumps(manifest, indent=2, sort_keys=True) + "\n")


def _iter_py_files(root: Path, paths: list[str]) -> Iterator[Path]:
    """Yield ``*.py`` files under ``paths``, skipping excluded directories."""
    found: dict[str, Path] = {}
    for relative in paths:
        base = root / relative
        if base.is_file():
            if base.suffix == ".py" and not base.name.startswith("."):
                found[base.relative_to(root).as_posix()] = base
            continue
        if not base.is_dir():
            continue
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = [
                name for name in dirnames if name not in EXCLUDED_DIRS and not name.startswith(".")
            ]
            for name in filenames:
                if not name.endswith(".py") or name.startswith("."):
                    continue
                path = Path(dirpath) / name
                found[path.relative_to(root).as_posix()] = path
    for relative in sorted(found):
        yield found[relative]


def _shadow_path(root: Path, relative: str) -> Path:
    """Return the shadow path for ``relative``."""
    return root / SHADOW_DIR / relative


def _merge(shadow: Path, working: Path) -> tuple[int, str, str]:
    """Three-way merge the annotated shadow onto the stripped working file.

    Returns ``(returncode, merged_text, stderr)``. ``git merge-file`` exit 0
    means clean, 1 means conflicts, anything else is an error.
    """
    shadow_text = _read_text(shadow)
    baseline = strip_source(shadow_text)
    with tempfile.TemporaryDirectory() as directory:
        baseline_path = Path(directory) / "baseline.py"
        _write_text(baseline_path, baseline)
        try:
            process = subprocess.run(
                [
                    "git",
                    "merge-file",
                    "-p",
                    "-L",
                    "annotated",
                    "-L",
                    "baseline",
                    "-L",
                    "edited",
                    str(shadow),
                    str(baseline_path),
                    str(working),
                ],
                capture_output=True,
            )
        except FileNotFoundError:
            return 2, "", "git merge-file unavailable"
    merged = process.stdout.decode("utf-8", errors="replace")
    error = process.stderr.decode("utf-8", errors="replace").strip()
    return process.returncode, merged, error


def cmd_strip(root: Path, paths: list[str]) -> int:
    """Strip comments/docstrings from the working tree into the shadow."""
    manifest = _load_manifest(root)
    files = manifest.setdefault("files", {})
    stripped = unchanged = refused = 0
    exit_code = 0
    for path in _iter_py_files(root, paths):
        relative = path.relative_to(root).as_posix()
        try:
            source = _read_text(path)
        except UnicodeDecodeError:
            print(f"skip {relative}: non-UTF-8")
            continue
        current_sha = _sha256(path)
        entry = files.get(relative)
        try:
            if entry is None:
                stripped_source = strip_source(source)
                _write_text(_shadow_path(root, relative), source)
                _write_text(path, stripped_source)
                files[relative] = {
                    "state": "stripped",
                    "annotated_sha": current_sha,
                    "stripped_sha": _sha256_text(stripped_source),
                }
                stripped += 1
            elif entry.get("state") == "annotated":
                stripped_source = strip_source(source)
                _write_text(_shadow_path(root, relative), source)
                _write_text(path, stripped_source)
                entry["state"] = "stripped"
                entry["annotated_sha"] = current_sha
                entry["stripped_sha"] = _sha256_text(stripped_source)
                stripped += 1
            elif entry.get("state") == "stripped":
                if current_sha == entry.get("stripped_sha"):
                    unchanged += 1
                elif current_sha == entry.get("annotated_sha"):
                    stripped_source = strip_source(source)
                    _write_text(path, stripped_source)
                    entry["stripped_sha"] = _sha256_text(stripped_source)
                    stripped += 1
                else:
                    print(
                        f"refusing {relative}: working file edited while stripped; "
                        "run 'comments-add' first"
                    )
                    refused += 1
                    exit_code = 1
            else:
                print(f"refusing {relative}: unknown state {entry.get('state')!r}")
                refused += 1
                exit_code = 1
        except (SyntaxError, tokenize.TokenError) as error:
            print(f"skip {relative}: {error}")
            exit_code = 1
    for relative in list(files):
        if not (root / relative).exists():
            del files[relative]
            shadow = _shadow_path(root, relative)
            if shadow.exists():
                shadow.unlink()
    _save_manifest(root, manifest)
    print(f"{stripped} stripped, {unchanged} unchanged, {refused} refused/conflicted")
    return exit_code


def cmd_add(root: Path) -> int:
    """Merge the annotated shadow back onto the stripped working tree."""
    manifest = _load_manifest(root)
    files = manifest.setdefault("files", {})
    added = unchanged = conflicted = 0
    exit_code = 0
    for relative in sorted(files):
        entry = files[relative]
        shadow = _shadow_path(root, relative)
        working = root / relative
        if not shadow.exists():
            print(f"stale {relative}: shadow missing")
            del files[relative]
            continue
        if not working.exists():
            print(f"deleted {relative}")
            del files[relative]
            shadow.unlink()
            continue
        current_sha = _sha256(working)
        shadow_sha = _sha256(shadow)
        state = entry.get("state")
        try:
            if state == "annotated":
                if current_sha != entry.get("annotated_sha"):
                    source = _read_text(working)
                    stripped_source = strip_source(source)
                    _write_text(shadow, source)
                    entry["annotated_sha"] = current_sha
                    entry["stripped_sha"] = _sha256_text(stripped_source)
                unchanged += 1
            elif state == "stripped":
                if current_sha == entry.get("stripped_sha"):
                    shadow_text = _read_text(shadow)
                    stripped_shadow = strip_source(shadow_text)
                    _write_text(working, shadow_text)
                    entry["state"] = "annotated"
                    entry["annotated_sha"] = shadow_sha
                    entry["stripped_sha"] = _sha256_text(stripped_shadow)
                    added += 1
                elif current_sha == entry.get("annotated_sha"):
                    shadow_text = _read_text(shadow)
                    stripped_shadow = strip_source(shadow_text)
                    entry["state"] = "annotated"
                    entry["annotated_sha"] = shadow_sha
                    entry["stripped_sha"] = _sha256_text(stripped_shadow)
                    unchanged += 1
                else:
                    returncode, merged, error = _merge(shadow, working)
                    if returncode == 0:
                        stripped_merged = strip_source(merged)
                        _write_text(working, merged)
                        _write_text(shadow, merged)
                        entry["state"] = "annotated"
                        entry["annotated_sha"] = _sha256_text(merged)
                        entry["stripped_sha"] = _sha256_text(stripped_merged)
                        added += 1
                    elif returncode == 1:
                        shadow_text = _read_text(shadow)
                        stripped_shadow = strip_source(shadow_text)
                        _write_text(working, merged)
                        entry["state"] = "annotated"
                        entry["annotated_sha"] = _sha256_text(merged)
                        entry["stripped_sha"] = _sha256_text(stripped_shadow)
                        print(f"conflict {relative}: resolve markers, then run 'comments-strip'")
                        conflicted += 1
                        exit_code = 1
                    else:
                        print(f"merge error {relative}: {error}")
                        exit_code = 1
            else:
                print(f"skip {relative}: unknown state {state!r}")
                exit_code = 1
        except UnicodeDecodeError:
            print(f"skip {relative}: non-UTF-8")
            exit_code = 1
        except (SyntaxError, tokenize.TokenError) as error:
            print(f"skip {relative}: {error}")
            exit_code = 1
    for relative in list(files):
        if not _shadow_path(root, relative).exists():
            del files[relative]
    _save_manifest(root, manifest)
    print(f"{added} added, {unchanged} unchanged, {conflicted} conflicted")
    return exit_code


def main(argv: list[str] | None = None) -> int:
    """Run the command-line interface."""
    parser = argparse.ArgumentParser(description="Comment/docstring shadow tool.")
    commands = parser.add_subparsers(dest="command", required=True)
    strip_parser = commands.add_parser("strip", help="save annotated shadow, strip working tree")
    strip_parser.add_argument("--root", default=".", help="project root (default: .)")
    strip_parser.add_argument(
        "--paths",
        nargs="+",
        default=["src", "tests", "scripts"],
        help="paths to scan (default: src tests scripts)",
    )
    add_parser = commands.add_parser("add", help="restore annotated shadow onto working tree")
    add_parser.add_argument("--root", default=".", help="project root (default: .)")
    args = parser.parse_args(argv)
    root = Path(args.root).resolve()
    if args.command == "strip":
        return cmd_strip(root, args.paths)
    return cmd_add(root)


if __name__ == "__main__":
    raise SystemExit(main())
