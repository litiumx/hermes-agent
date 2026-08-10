#!/usr/bin/env python3
"""Standalone-тесты: фильтр строковых литералов для shell=True (цикл 11, фикс).

Dogfooding выявил false positives: ревьюер флагал shell=True внутри
docstring'ов, print(...) и тестовых фикстур (тройные кавычки). Нужен
мини-лексер _code_only_lines: возвращает строки diff без содержимого
строковых литералов (одинарные/двойные/тройные кавычки, экранирование).
"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, "/home/sandbox/hermes-agent/scripts")
import agi_code_reviewer as cr

TMP = Path(tempfile.mkdtemp())
cr.REVIEWS_DIR = TMP / "reviews"


def stripped(line, in_triple=False):
    """Обёртка: вернуть (код_без_литералов, новое_состояние_triple)."""
    return cr._code_only_line(line, in_triple)


# --- 1. Реальный код: shell=True в вызове → остаётся ---
code, st = stripped("+    shell=True,")
assert "shell=True" in code, code
print("TEST 1 PASS: реальный код shell=True сохраняется")

# --- 2. print("...shell=True...") → строка вырезается ---
code, st = stripped('+print("a shell=True b")')
assert "shell=True" not in code, code
print("TEST 2 PASS: shell=True внутри print(\"...\") вырезан")

# --- 3. Однострочный docstring/строковый литерал ---
code, st = stripped('+    """shell=True в доке"""')
assert "shell=True" not in code, code
print("TEST 3 PASS: однострочный docstring вырезан")

# --- 4. Многострочный docstring: состояние triple переживает строки ---
code, st = stripped('+"""Док: shell=True', in_triple=False)
assert "shell=True" not in code and st is True, (code, st)
code, st = stripped('+продолжение доки shell=True', in_triple=st)
assert "shell=True" not in code and st is True, (code, st)
code, st = stripped('+конец"""', in_triple=st)
assert "shell=True" not in code and st is False, (code, st)
print("TEST 4 PASS: многострочный docstring вырезан целиком")

# --- 5. Тестовая фикстура (diff внутри тройных кавычек) → вырезана ---
code, st = stripped('+""" scripts/agi_m.py | 3 ++', in_triple=False)
assert st is True, st
code, st = stripped('+    shell=True,', in_triple=st)
assert "shell=True" not in code, code
code, st = stripped('+"""', in_triple=st)
assert st is False, st
print("TEST 5 PASS: тестовая фикстура (тройные кавычки) вырезана")

# --- 6. Экранированная кавычка внутри строки не ломает парсер ---
code, st = stripped(r'+s = "x\"y" + "shell=True"')
assert "shell=True" not in code, code
print("TEST 6 PASS: экранированные кавычки обработаны")

# --- 7. Одинарные кавычки ---
code, st = stripped("+s = 'shell=True'")
assert "shell=True" not in code, code
code, st = stripped("+s = 'x'  # shell=True")
assert "shell=True" not in code, code
print("TEST 7 PASS: одинарные кавычки и хвостовой комментарий вырезаны")

# --- 8. Интеграция: review_diff не флагает фикстуру, но ловит код ---
diff_fixture = ''' scripts/agi_f.py | 4 ++
---DIFF---
+multi = """фикстура
+    shell=True,
+"""
+subprocess.run(
+    ["ls"],
+    shell=True,
+)
'''
r = cr.review_diff(diff_fixture, scripts_dir=TMP)
shell_hits = [d for d in r["danger"] if "shell=True" in d["pattern"]]
assert len(shell_hits) == 1, shell_hits
assert "shell=True вне вызова" in shell_hits[0]["pattern"], shell_hits
print("TEST 8 PASS: интеграция — фикстура не флагается, код ловится")

print("\nALL TESTS PASS (8)")
