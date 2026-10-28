"""Entry point for PyInstaller (and `python run.py`)."""

import sys

from mooer_prime.app import main

if __name__ == "__main__":
    sys.exit(main())
