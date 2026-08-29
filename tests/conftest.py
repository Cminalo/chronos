import pytest

TIER_MARKERS = {"unit", "component", "system", "live"}


def pytest_collection_modifyitems(config, items):
    """Fail collection if any test lacks a tier marker.

    Keeps `pixi run test-quick` (unit or component) from silently skipping
    unmarked tests.
    """
    unmarked = [
        item for item in items if not any(m.name in TIER_MARKERS for m in item.iter_markers())
    ]
    if unmarked:
        names = "\n".join(item.nodeid for item in unmarked)
        raise pytest.UsageError(
            f"{len(unmarked)} test(s) missing a tier marker "
            f"(@pytest.mark.unit/component/system):\n{names}"
        )
