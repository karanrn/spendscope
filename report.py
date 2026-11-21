#!/usr/bin/env python3
"""Shortcut for `python -m spendscope report`; see spendscope/cli.py."""
import sys

from spendscope.cli import main

main(["report", *sys.argv[1:]])
