from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def pytest_configure(config):
    """Ensure src/ is importable when pytest is run from the repo root."""
    import sys

    src = str(ROOT / "src")
    if src not in sys.path:
        sys.path.insert(0, src)
