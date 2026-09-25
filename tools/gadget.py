#!/usr/bin/env python3
"""gadget: make this board the phone's USB keyboard and mouse (same as `ihc gadget`).

    sudo python3 tools/gadget.py up      # then: python3 tools/hidtest.py --gadget
    python3 tools/gadget.py status
    sudo python3 tools/gadget.py down
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))  # allow running from a checkout without `pip install -e .`

from ihc.gadget_cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
