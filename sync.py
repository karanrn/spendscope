#!/usr/bin/env python3
"""Shortcut for `python -m spendscope sync`; see spendscope/cli.py."""
import sys

from spendscope.cli import main

main(["sync", *sys.argv[1:]])
