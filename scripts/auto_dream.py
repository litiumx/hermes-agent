#!/usr/bin/env python3
"""Cron wrapper for auto_dream — runs the full cycle and prints report path."""
import sys
sys.path.insert(0, "/root/.hermes")
from memory.auto_dream import cycle, write_report
import json

stats = cycle()
print(f"DREAM REPORT: /root/.hermes/dreams/DREAMING_*.md")
print(json.dumps(stats, indent=2))
