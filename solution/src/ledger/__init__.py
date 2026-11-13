import sys
from pathlib import Path

_pkg_root = Path(__file__).resolve().parent.parent
if str(_pkg_root) not in sys.path:
    sys.path.insert(0, str(_pkg_root))

__version__ = "1.0.0"
