#!/usr/bin/env python3
"""Shortcut for `python -m expense_tracker sync`; see expense_tracker/cli.py."""
import sys

from expense_tracker.cli import main

main(["sync", *sys.argv[1:]])
