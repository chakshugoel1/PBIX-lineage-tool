"""Entry point for the PBIX Lineage Tool desktop app."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from gui.main_window import main

if __name__ == "__main__":
    main()
