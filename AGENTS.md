---
title: "chronos — Agent Notes"
date: 2026-08-28
tags:
  - chronos
  - agents
  - workflow
---

# chronos — Agent Notes

## Project Goal

Chronos (`chronos-logger` on PyPI) is a high-performance logging and parallel-execution suite for Python, built on Loguru + Rich + psutil. It gives developers professional-grade observability (Rich terminal UI, tree tracebacks, memory/system metrics, standard-library interception) and traceable concurrency (thread/process pools with task recovery and failure logs) with zero boilerplate.

## Working Model

This project is maintained primarily by a **single agent** working across **many sessions**. There is no rotating team; accountability comes from the artifacts each session leaves behind. The docs are the record — if a decision isn't recorded, it didn't happen. When in doubt, write it down.

## Commands

```bash
pixi install          # Install deps (use pixi, not plain pip)
pixi run test         # Full test suite
pixi run test-quick   # Unit + component only (no network)
pixi run lint         # ruff check
pixi run fmt          # ruff format
pixi run typecheck    # mypy strict mode
pixi run docs         # Build docs (Sphinx + sphinx-immaterial)
pixi run build        # Build wheel
```

## Package layout

- **Package root**: `src/chronos/` (src-layout, not flat). Imports are `from chronos.xxx`.
- **Modules**: `logger.py` (Loguru-based logging suite), `parallel.py` (parallel execution).
- **No CLI entry point** — library-only; no `[project.scripts]` in `pyproject.toml`.

## Quality Gates

Run continuously during development, **never batch them at the end**:

```bash
pixi run lint         # ruff check — style and syntax
pixi run fmt          # ruff format — formatting
pixi run typecheck    # mypy strict mode — type safety
```

Which gates to run depends on the change type:

| Change type | Required gates |
|---|---|
| Docs-only (README, CHANGELOG, docs/, comments) | `lint`, `fmt`, `typecheck`, `docs` build |
| Code change (src/, tests/) | `lint`, `fmt`, `typecheck`, `test-quick` |
| Behavior/API/CLI change (public interface, extraction, routing, config) | `lint`, `fmt`, `typecheck`, full `test` suite |

## Testing

Three tiers, each test marked with its marker (`tests/conftest.py` fails collection on unmarked tests):

| Tier | Marker | Command | Scope |
|------|--------|---------|-------|
| Unit | `@pytest.mark.unit` | `pixi run test-unit` | Pure function, no I/O |
| Component | `@pytest.mark.component` | `pixi run test-component` | Multiple functions, no network |
| System | `@pytest.mark.system` | `pixi run test-system` | End-to-end workflow |

Run the **full `test` suite only when code behavior changed meaningfully** (core logic, public API). Docs-only changes run the fast gates + `docs` build and skip the full test suite.

## Documentation Structure

- `docs/agent-work/` — agent design/analysis notes (`<YYYY-MM-DD>-<slug>.md`).
- `docs/user-work/` — user-provided context, requirements (`<YYYY-MM-DD>-<slug>.md`).
- `docs/chronos/` — published project documentation (Sphinx + sphinx-immaterial, MyST Markdown).

Before any commit, update the affected docs: top-level `README.md` / `CHANGELOG.md` / `STATUS.md` / `AGENTS.md`, plus `docs/chronos/` pages and any `docs/agent-work/` or `docs/user-work/` notes referencing the changed behavior. If a change alters user-facing behavior, public API, or configuration, the corresponding docs MUST be updated in the same commit.

## Status tracking

- **STATUS.md** — live status board (capabilities, progress, next work). Update before significant commits.
- **CHANGELOG.md** — reverse-chronological log of logical change batches.
- **README.md** — public face (features, quick-start). Update when the public interface changes.

## Committing

Commit after each logical batch of changes, not at the end of a long session. Use the **git-commit skill** for staging + message generation. Note: no global git identity is configured on this machine — committer resolves to `cminalo@mac-studio.local` unless set per-repo.

## When Unsure — Ask

If requirements are ambiguous, an approach is unclear, or a decision has long-term impact — ask the user before proceeding.
