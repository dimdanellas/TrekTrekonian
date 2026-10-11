#!/usr/bin/env python3
"""Launch Automated News Package Editor."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from newsedit.ui.app import main  # noqa: E402

if __name__ == "__main__":
    main()
