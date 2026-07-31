#!/usr/bin/env python3
"""agi_code_reviewer.py — авто-ревью своих AGI-коммитов.

Фичи:
- Анализ последнего коммита (git diff HEAD~1..HEAD)
- Проверки: синтаксис Python, потенциально опасные паттерны, стиль
- Сохранение отчёта в data/reviews/
- CLI: `last`, `all`, `<commit-hash>`, `recent N`
"""

import json, os, re, subprocess, sys
from datetime import datetime
from pathlib import Path

HERMES_DIR = Path("/root/.hermes")
SCRIPTS_DIR = HERMES_DIR / "scripts"
REVIEWS_DIR = HERMES_DIR / "data" / "reviews"

# Опасные паттерны (regex → описание)
DANGER_PATTERNS = {
    r"\bos\.system\s*\(": "os.system — лучше subprocess.run",
    r"\bsubprocess\.(call|Popen|run)\s*\([^)]*shell\s*=\s*True": "shell=True — риск инъекции",
    r"\beval\s*\(": "eval() — потенциальная инъекция",
    r"\bexec\s*\(": "exec() — потенциальная инъекция",
    r"input\s*\(\s*\)": "input() без аргумента — нет подсказки",
    r"except\s*:": "голый except: — ловит всё включая SystemExit",
    r"except\s+Exception\s*:": "except Exception — лучше конкретнее",
    r"os\.chmod\s*\([^)]*0o?777": "chmod 777 — слишком широкие права",
}

# Хорошие паттерны (regex → похвала)
GOOD_PATTERNS = {
    r"def\s+\w+\s*\([^)]*\)\s*->\s*\w+": "type hints",
    r'"""': "docstring",
    r"if\s+__name__\s*==\s*[\"']__main__[\"']": "правильная точка входа",
    r"Path\(.*\)\s*/": "Path вместо os.path (современно)",
}


def run(cmd: str) -> str:
    """Выполнить команду и вернуть stdout."""
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=10)
    return r.stdout.strip() + "\n" + r.stderr.strip()


def get_diff(commit_ref: str = "HEAD~1..HEAD") -> str:
    """Получить diff для коммита/диапазона."""
    cmd = f"cd {HERMES_DIR} && git diff {commit_ref} --stat && echo '---DIFF---' && git diff {commit_ref}"
    return run(cmd)


def review_diff(diff_text: str) -> dict:
    """Проанализировать diff и вернуть отчёт."""
    findings = {"danger": [], "good": [], "syntax_errors": [], "stats": {}}

    # Извлечь .py файлы из stat
    py_files = re.findall(r"scripts/(agi_\w+\.py)", diff_text)
    findings["stats"]["py_files_changed"] = list(set(py_files))

    # Проверка синтаксиса всех изменённых .py файлов
    for fname in set(py_files):
        fpath = SCRIPTS_DIR / Path(fname).name
        if fpath.exists():
            try:
                compile(fpath.read_text(), str(fpath), "exec")
            except SyntaxError as e:
                findings["syntax_errors"].append(f"{fname}:{e.lineno} — {e.msg}")

    # Поиск опасных паттернов в diff (только добавленные строки)
    added_lines = [l for l in diff_text.split("\n") if l.startswith("+") and not l.startswith("+++")]
    for line in added_lines:
        for pat, desc in DANGER_PATTERNS.items():
            if re.search(pat, line):
                findings["danger"].append({"pattern": desc, "line": line.strip()[:100]})

    # Поиск хороших паттернов
    added_code = "\n".join(added_lines)
    for pat, desc in GOOD_PATTERNS.items():
        if re.search(pat, added_code):
            findings["good"].append(desc)

    # Статистика
    findings["stats"]["added_lines"] = len(added_lines)
    findings["stats"]["deleted_lines"] = len([l for l in diff_text.split("\n") if l.startswith("-") and not l.startswith("---")])

    # Оценка
    danger_count = len(findings["danger"])
    syntax_count = len(findings["syntax_errors"])
    if syntax_count > 0:
        findings["verdict"] = "🔴 FAIL — ошибки синтаксиса"
    elif danger_count > 2:
        findings["verdict"] = "🟡 WARN — много рискованных паттернов"
    elif danger_count > 0:
        findings["verdict"] = "🟢 OK — есть замечания"
    else:
        findings["verdict"] = "✅ CLEAN — без замечаний"

    return findings


def save_report(commit_ref: str, findings: dict) -> Path:
    """Сохранить отчёт в data/reviews/."""
    REVIEWS_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    fname = REVIEWS_DIR / f"review_{ts}_{commit_ref.replace('..', '_').replace('~', 't')}.json"
    report = {
        "timestamp": datetime.now().isoformat(),
        "commit_ref": commit_ref,
        "findings": findings,
    }
    fname.write_text(json.dumps(report, indent=2, ensure_ascii=False))
    return fname


def print_report(findings: dict):
    """Вывести отчёт в консоль."""
    print(f"\n{'='*50}")
    print(f"  📋 CODE REVIEW — {findings['verdict']}")
    print(f"{'='*50}")

    if findings["syntax_errors"]:
        print("\n🔴 Синтаксические ошибки:")
        for e in findings["syntax_errors"]:
            print(f"   ❌ {e}")

    if findings["danger"]:
        print(f"\n🟡 Замечания ({len(findings['danger'])}):")
        for d in findings["danger"]:
            print(f"   ⚠️  {d['pattern']}")
            print(f"      → {d['line']}")

    if findings["good"]:
        print(f"\n✅ Хорошие практики ({len(findings['good'])}):")
        for g in set(findings["good"]):
            print(f"   👍 {g}")

    s = findings["stats"]
    print(f"\n📊 Статистика:")
    print(f"   Добавлено строк: {s.get('added_lines', 0)}")
    print(f"   Удалено строк: {s.get('deleted_lines', 0)}")
    print(f"   Изменено .py файлов: {len(s.get('py_files_changed', []))}")
    if s.get("py_files_changed"):
        print(f"   Файлы: {', '.join(s['py_files_changed'])}")


if __name__ == "__main__":
    import argparse

    p = argparse.ArgumentParser(description="AGI Code Reviewer")
    p.add_argument("target", nargs="?", default="last",
                   help="commit/range: 'last' (HEAD~1..HEAD), 'all' (all AGI), <hash>, <range>")
    p.add_argument("--save", action="store_true", help="Сохранить отчёт в JSON")
    args = p.parse_args()

    if args.target == "last":
        ref = "HEAD~1..HEAD"
    elif args.target == "all":
        # Найти все AGI-коммиты
        log = run(f"cd {HERMES_DIR} && git log --oneline --grep='\\[agi\\]' --format='%H'")
        hashes = [h for h in log.split("\n") if h]
        if hashes:
            ref = f"{hashes[-1]}~1..{hashes[0]}"
        else:
            print("❌ Нет AGI-коммитов")
            sys.exit(1)
    else:
        ref = args.target

    print(f"🔍 Ревью: {ref}")
    diff = get_diff(ref)

    if "---DIFF---" not in diff:
        print("❌ Нет изменений для ревью")
        sys.exit(0)

    findings = review_diff(diff)
    print_report(findings)

    if args.save:
        path = save_report(ref, findings)
        print(f"\n📁 Отчёт сохранён: {path}")
