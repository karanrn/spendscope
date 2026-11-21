#!/usr/bin/env python3
"""Shortcut for `python -m spendscope serve`; see spendscope/cli.py."""
import sys

from spendscope.cli import main

main(["serve", *sys.argv[1:]])
