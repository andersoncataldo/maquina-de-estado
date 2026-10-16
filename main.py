#!/usr/bin/env python3
"""Ponto de entrada da GUI:  python main.py"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from gui.app import main  # noqa: E402

if __name__ == "__main__":
    main()
