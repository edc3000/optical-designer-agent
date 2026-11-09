"""Optical designer agent: Python tools on top of Optical-Layout-Designer (optolayout)."""
import os
import sys
from pathlib import Path

# the original project, used as a library (never modified)
OPTOLAYOUT_ROOT = Path(os.environ.get(
    "OPTOLAYOUT_ROOT", Path(__file__).resolve().parents[3] / "Optical-Layout-Designer")).resolve()
if not (OPTOLAYOUT_ROOT / "optolayout").is_dir():
    raise ImportError(f"Optical-Layout-Designer not found at {OPTOLAYOUT_ROOT} (set OPTOLAYOUT_ROOT)")
if str(OPTOLAYOUT_ROOT) not in sys.path:
    sys.path.insert(0, str(OPTOLAYOUT_ROOT))
