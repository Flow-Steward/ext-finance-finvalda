from __future__ import annotations

import sys
from pathlib import Path

BUNDLE_ROOT = Path(__file__).resolve().parents[1]
for path in (BUNDLE_ROOT, BUNDLE_ROOT / "tests"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))
