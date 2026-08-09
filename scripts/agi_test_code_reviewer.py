#!/usr/bin/env python3
"""Standalone-тесты agi_code_reviewer (цикл 6, 09.08).

Покрытие: run() без shell (инъекция не исполняется), get_diff на temp-git-репо,
review_diff (danger/good/verdict/статистика/синтаксис/edge), save_report JSON.
Без сети: только локальный git в tempdir.
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, "/home/sandbox/hermes-agent/scripts")
import agi_code_reviewer as cr

TMP = Path(tempfile.mkdtemp())
cr.REVIEWS_DIR = TMP / "reviews"  # изоляция от реальных данных

# --- 1. run(): list-аргументы, БЕЗ shell=True ---
out = cr.run(["echo", "agi-review"])
assert "agi-review" in out, out
print("TEST 1 PASS: run(list) без shell")

# --- 2. run(): инъекция через аргумент НЕ исполняется ---
pwn = TMP / "pwned.txt"
out = cr.run(["echo", f"ok; touch {pwn}"])
assert not pwn.exists(), "shell-инъекция исполнилась!"
assert "ok; touch" in out  # литерал в выводе, а не команда
print("TEST 2 PASS: run() — инъекция не исполняется")

# --- 3. review_diff: чистый diff → CLEAN + good patterns ---
clean = """ scripts/agi_new.py | 3 ++
---DIFF---
+def handle(x: int) -> str:
+    \"\"\"doc\"\"\"
+    return str(x)
+if __name__ == "__main__":
+    pass
"""
r = cr.review_diff(clean, scripts_dir=TMP)
assert r["verdict"].startswith("✅"), r["verdict"]
assert r["danger"] == [] and r["syntax_errors"] == []
assert "type hints" in r["good"] and "docstring" in r["good"]
print("TEST 3 PASS: clean diff → CLEAN + type hints/docstring/__main__")

# --- 4. review_diff: danger-паттерны → OK (1-2) / WARN (>2) ---
danger2 = """ scripts/agi_x.py | 1 +
---DIFF---
+os.system("ls")
+import subprocess; subprocess.run(cmd, shell=True)
"""
r = cr.review_diff(danger2, scripts_dir=TMP)
assert r["verdict"].startswith("🟢"), r["verdict"]
assert len(r["danger"]) == 2, r["danger"]
danger3 = danger2 + '+eval("1+1")\n'
r = cr.review_diff(danger3, scripts_dir=TMP)
assert r["verdict"].startswith("🟡"), r["verdict"]
assert len(r["danger"]) == 3
print("TEST 4 PASS: danger 2→OK, 3→WARN")

# --- 5. review_diff: синтаксическая ошибка в изменённом файле → FAIL ---
bad_src = TMP / "agi_bad.py"
bad_src.write_text("def broken(:\n")
diff_bad = """ scripts/agi_bad.py | 1 +
---DIFF---
+def broken(:
"""
r = cr.review_diff(diff_bad, scripts_dir=TMP)
assert r["verdict"].startswith("🔴"), r["verdict"]
assert r["syntax_errors"], r["syntax_errors"]
print("TEST 5 PASS: syntax error → FAIL")

# --- 6. review_diff: статистика (added/deleted, dedupe py_files) ---
diff_stat = """ scripts/agi_a.py | 5 ++---
 scripts/agi_a.py | 1 +
---DIFF---
+add1
+add2
-del1
"""
r = cr.review_diff(diff_stat, scripts_dir=TMP)
assert r["stats"]["added_lines"] == 2, r["stats"]
assert r["stats"]["deleted_lines"] == 1, r["stats"]
assert r["stats"]["py_files_changed"] == ["agi_a.py"], r["stats"]
print("TEST 6 PASS: статистика и dedupe файлов")

# --- 7. review_diff: пустой diff → CLEAN без падений ---
r = cr.review_diff("", scripts_dir=TMP)
assert r["verdict"].startswith("✅") and r["stats"]["added_lines"] == 0
print("TEST 7 PASS: пустой diff → CLEAN, нули")

# --- 8. save_report: JSON с ref/timestamp/findings в reviews_dir ---
r = cr.review_diff("+import os\n", scripts_dir=TMP)
p = cr.save_report("HEAD~1..HEAD", r, reviews_dir=TMP / "reviews")
data = json.loads(p.read_text())
assert data["commit_ref"] == "HEAD~1..HEAD"
assert data["findings"]["stats"]["added_lines"] == 1
assert "review_" in p.name and "HEAD" in p.name  # .. заменены в имени
print("TEST 8 PASS: save_report пишет валидный JSON")

# --- 9. get_diff: temp git-репо, HEAD~1..HEAD содержит файл и diff ---
repo = TMP / "repo"
repo.mkdir()
subprocess.run(["git", "init", "-q", str(repo)], check=True)
subprocess.run(["git", "-C", str(repo), "config", "user.email", "t@t"], check=True)
subprocess.run(["git", "-C", str(repo), "config", "user.name", "t"], check=True)
f = repo / "x.txt"
f.write_text("v1\n")
subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
subprocess.run(["git", "-C", str(repo), "commit", "-qm", "c1"], check=True)
f.write_text("v2\n")
subprocess.run(["git", "-C", str(repo), "commit", "-qam", "c2"], check=True)
stat, diff = cr.get_diff("HEAD~1..HEAD", repo_dir=str(repo))
assert "x.txt" in stat, stat
assert "+v2" in diff and "-v1" in diff, diff
print("TEST 9 PASS: get_diff на temp-репо (stat + diff)")

# --- 10. get_diff: пустой диапазон → пустые строки без падения ---
stat, diff = cr.get_diff("HEAD..HEAD", repo_dir=str(repo))
assert stat == "" and diff == "", (stat, diff)
print("TEST 10 PASS: get_diff пустой диапазон → пусто")

print("\nALL TESTS PASS (10)")
