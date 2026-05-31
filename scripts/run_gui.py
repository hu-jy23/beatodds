#!/usr/bin/env python3
"""Run the local BeatOdds GUI."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from gui.server import main

if __name__ == "__main__":
    main()
