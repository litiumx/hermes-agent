#!/usr/bin/env python3
"""Standalone-тесты: многострочный shell=True и дедупликация good (цикл 11).

Регрессия на цикл 6: многострочный subprocess.run(
    ["ls"],
    shell=True,
)
не ловился (паттерн требовал вызов и shell=True на одной строке).
Плюс: дедупликация good-паттернов и пропуск комментариев с shell=True.
"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, "/home/sandbox/hermes-agent/scripts")
import agi_code_reviewer as cr

TMP = Path(tempfile.mkdtemp())
cr.REVIEWS_DIR = TMP / "reviews"

# --- 1. Многострочный subprocess.run + shell=True на отдельной строке → danger ---
multi = """ scripts/agi_m.py | 3 ++
---DIFF---
+subprocess.run(
+    ["ls"],
+    shell=True,
+)
"""
r = cr.review_diff(multi, scripts_dir=TMP)
shell_dangers = [d for d in r["danger"] if "shell" in d["pattern"].lower()]
assert shell_dangers, f"многострочный shell=True не найден: {r['danger']}"
print("TEST 1 PASS: многострочный shell=True → danger")

# --- 2. Однострочный subprocess(..., shell=True) — ровно 1 danger (без дубля) ---
one = """ scripts/agi_o.py | 1 +
---DIFF---
+subprocess.run(cmd, shell=True)
"""
r = cr.review_diff(one, scripts_dir=TMP)
assert len(r["danger"]) == 1, r["danger"]
print("TEST 2 PASS: однострочный shell=True — один danger, без дублей")

# --- 3. Комментарий "# shell=True" НЕ должен флагаться ---
cmt = """ scripts/agi_c.py | 1 +
---DIFF---
+    # shell=True опасен, но это комментарий
+import os
"""
r = cr.review_diff(cmt, scripts_dir=TMP)
assert r["danger"] == [], r["danger"]
print("TEST 3 PASS: комментарий с shell=True не флагается")

# --- 4. Дедупликация good: две функции с type hints → "type hints" один раз ---
two = """ scripts/agi_t.py | 2 ++
---DIFF---
+def a(x: int) -> str:
+def b(y: str) -> int:
"""
r = cr.review_diff(two, scripts_dir=TMP)
assert r["good"].count("type hints") == 1, r["good"]
print("TEST 4 PASS: good-паттерны дедуплицированы")

print("\nALL TESTS PASS (4)")
