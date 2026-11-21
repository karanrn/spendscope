#!/usr/bin/env python3
"""Shortcut for `python -m spendscope labels`; see spendscope/cli.py."""
import sys

from spendscope.cli import main

main(["labels", *sys.argv[1:]])
