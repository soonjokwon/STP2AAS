"""Compatibility shim — the viewer lives in `step2aas.viewer`.

    python scripts/visualize_aasx.py out/model.aasx --open
    python -m step2aas.viewer out/model.aasx --open
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from step2aas.viewer import main

if __name__ == "__main__":
    raise SystemExit(main())
