#!/usr/bin/env python3
"""agi_code_reviewer.py — авто-ревью своих AGI-коммитов (цикл 6, 09.08).

Фичи:
- Анализ последнего коммита (git diff HEAD~1..HEAD)
- Проверки: синтаксис Python, потенциально опасные паттерны, стиль
- Детекция эксфильтрации (curl|sh, чтение кредов/секретов) и персистентности
  (crontab, /etc/cron.d, git hooks, .bashrc, чужой pip index) — цикл 12,
  APPLY #1 из SELF_IMPROVE_2026-08-10 (AISI security)
- Сохранение отчёта в data/reviews/
- CLI: `last`, `all`, `<commit-hash>`, `recent N`

Безопасность: subprocess БЕЗ shell=True (list-аргументы) — инъекция через
commit_ref невозможна. Пути репозитория/отчётов переопределяются env:
AGI_REPO_DIR (по умолчанию /root/.hermes), AGI_REVIEWS_DIR.
"""

import json
import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

REPO_DIR = Path(os.environ.get("AGI_REPO_DIR", "/root/.hermes"))
SCRIPTS_DIR = REPO_DIR / "scripts"
REVIEWS_DIR = Path(os.environ.get("AGI_REVIEWS_DIR", str(REPO_DIR / "data" / "reviews")))

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

# Эксфильтрация (APPLY #1, SELF_IMPROVE_2026-08-10, AISI 08.2026):
# скачивание+выполнение кода, чтение кредов/секретов. Проверяются только
# на коде БЕЗ строковых литералов (мини-лексер) — docstring'и не флагаются.
EXFIL_PATTERNS = {
    r"\b(curl|wget)\b[^\n]*\|\s*(ba)?sh\b": "curl/wget | sh — скачивание и выполнение (supply chain)",
    r"\bbase64\b[^\n]*\|\s*(ba)?sh\b": "base64 -d | sh — обфусцированный код",
    r"cat\s+[^\n]*\.aws/(credentials|config)\b": "чтение AWS-кредов",
    r"cat\s+[^\n]*\.kube/config\b": "чтение kubeconfig",
    r"cat\s+[^\n]*\.ssh/id_(rsa|ed25519|ecdsa)\b": "чтение SSH-ключей",
    r"cat\s+[^\n]*\.env\b": "чтение .env (секреты)",
}

# Персистентность: инъекция крона, автозапуск, git hooks, перехват кредов,
# дописывание в rc-файлы, чужой pip index (typosquat-вектор).
PERSIST_PATTERNS = {
    r"crontab\s*-\s*$": "crontab - — замена/инъекция крона из stdin",
    r"/etc/cron\.d/": "запись в /etc/cron.d — персистентность",
    r"/etc/rc\.local": "запись в rc.local — автозапуск",
    r"\.git/hooks/": "запись в .git/hooks — перехват git-операций",
    r"git\s+config\b[^\n]*credential": "git config credential — перехват кредов",
    r">>\s*~?/\.(bashrc|profile|zshrc)\b": "дописывание в .bashrc/.profile — персистентность",
    r"pip[23]?\s+install\b[^\n]*(--index-url|--extra-index-url)": "pip install с чужим index — возможный typosquat",
}

# Хорошие паттерны (regex → похвала)
GOOD_PATTERNS = {
    r"def\s+\w+\s*\([^)]*\)\s*->\s*\w+": "type hints",
    r'"""': "docstring",
    r"if\s+__name__\s*==\s*[\"']__main__[\"']": "правильная точка входа",
    r"Path\(.*\)\s*/": "Path вместо os.path (современно)",
}

DIFF_MARKER = "---DIFF---"

# Маркеры тройных кавычек для мини-лексера строковых литералов
_TRIPLE = ['"""', "'''"]


def _code_only_line(line: str, in_triple: bool = False):
    """Вырезать содержимое строковых литералов из строки diff.

    Возвращает (код_без_литералов, новое_состояние_triple).
    Нужно, чтобы паттерны вроде shell=True не матчили docstring'и,
    print("...") и тестовые фикстуры (тройные кавычки). Экранирование
    \\" и \\' учитывается. Состояние in_triple переживает многострочные
    литералы.
    """
    if in_triple:
        # Мы внутри тройных кавычек — ищем закрывающую последовательность
        for close in _TRIPLE:
            idx = line.find(close)
            if idx >= 0:
                rest = line[idx + 3:]
                return _code_only_line(rest, False)
        return "", True  # строка целиком внутри литерала

    out = []
    i = 0
    n = len(line)
    while i < n:
        # Тройные кавычки — открытие многострочного литерала
        if line.startswith('"""', i) or line.startswith("'''", i):
            marker = line[i:i + 3]
            rest = line[i + 3:]
            idx = rest.find(marker)
            if idx >= 0:
                i += 3 + idx + 3  # закрылись в этой же строке
                continue
            return "".join(out), True  # перенесём состояние дальше
        ch = line[i]
        if ch == "#":
            break  # комментарий до конца строки (вне строковых литералов)
        if ch in "\"'":
            quote = ch
            j = i + 1
            while j < n:
                if line[j] == "\\" and j + 1 < n:
                    j += 2
                    continue
                if line[j] == quote:
                    break
                j += 1
            i = j + 1  # пропустить закрывающую кавычку (или конец строки)
            continue
        out.append(ch)
        i += 1
    return "".join(out), False


def run(cmd_list) -> str:
    """Выполнить команду списком аргументов БЕЗ shell (безопасно)."""
    r = subprocess.run(cmd_list, capture_output=True, text=True, timeout=10)
    return r.stdout.strip() + "\n" + r.stderr.strip()


def get_diff(commit_ref: str, repo_dir=None) -> tuple:
    """Вернуть (stat, diff) для коммита/диапазона. repo_dir — тестовый оверрайд."""
    repo = Path(repo_dir) if repo_dir else REPO_DIR
    stat = run(["git", "-C", str(repo), "diff", commit_ref, "--stat"])
    diff = run(["git", "-C", str(repo), "diff", commit_ref])
    return stat.strip(), diff.strip()


def _file_line_context(fpath, lineno):
    """Состояние triple-кавычек ПЕРЕД строкой lineno в реальном файле.

    Diff-фрагмент может не содержать открытия docstring'а (оно вне hunk'а) —
    тогда состояние строкового литерала восстанавливается из файла на диске
    (staged-версия). Если файл недоступен — False (полагаемся на diff).
    """
    try:
        lines = Path(fpath).read_text().split("\n")
    except OSError:
        return False
    in_triple = False
    for raw in lines[: lineno - 1]:
        _, in_triple = _code_only_line(raw, in_triple)
    return in_triple


def review_diff(diff_text: str, scripts_dir=None) -> dict:
    """Проанализировать diff и вернуть отчёт. scripts_dir — тестовый оверрайд."""
    findings = {"danger": [], "good": [], "syntax_errors": [], "exfil": [], "persist": [], "stats": {}}
    sdir = Path(scripts_dir) if scripts_dir else SCRIPTS_DIR

    # Извлечь .py файлы из stat
    py_files = re.findall(r"scripts/(agi_\w+\.py)", diff_text)
    findings["stats"]["py_files_changed"] = sorted(set(py_files))

    # Проверка синтаксиса всех изменённых .py файлов
    for fname in set(py_files):
        fpath = sdir / Path(fname).name
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

    # Многострочные вызовы subprocess: shell=True на отдельной строке
    # (паттерн выше требует вызов и shell=True на одной строке). Комментарии,
    # docstring'и и строковые литералы пропускаем через мини-лексер — без
    # false positives и дублей. Там же проверяем эксфильтрацию и
    # персистентность (только на коде без литералов).
    # Состояние triple-кавычек гоняется по ВСЕМ строкам диффа (контекстные +
    # добавленные): docstring, открытый в неизменённой части диффа, не даёт
    # false positives на добавленных строках.
    flagged = {d["line"] for d in findings["danger"]}
    in_triple = False
    cur_file, cur_lineno = None, 0
    for line in diff_text.split("\n"):
        if line.startswith("+++"):
            m = re.match(r"\+\+\+ b/(\S+)", line)
            cur_file = m.group(1) if m else None
            cur_lineno = 0
            continue
        if line.startswith("---"):
            continue
        m = re.match(r"@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@", line)
        if m:
            cur_lineno = int(m.group(1))
            continue
        if line.startswith("+"):
            is_added, content = True, line[1:]
        elif line.startswith("-"):
            continue  # удалённые строки не влияют на состояние нового файла
        elif line.startswith(" "):
            is_added, content = False, line[1:]
        else:
            continue  # заголовки diff
        # Любая существующая строка (added/context, включая комментарии)
        # присутствует в файле на диске — номер строки обязан расти
        cur_lineno += 1
        if re.match(r"\s*#", content):
            continue  # комментарии: состояние triple не меняют
        if is_added:
            # Файл на диске — источник истины о состоянии литералов
            # (открытие доки может быть вне hunk'а диффа)
            if cur_file and cur_file.endswith(".py"):
                if _file_line_context(sdir / Path(cur_file).name, cur_lineno):
                    in_triple = True
                    continue
        else:
            # Контекстные строки тоже сверяем с файлом: чистая строка \"\"\"
            # лексером трактуется как открытие, а в файле это закрытие доки
            if cur_file and cur_file.endswith(".py"):
                in_triple = _file_line_context(sdir / Path(cur_file).name, cur_lineno + 1)
                continue
        code, in_triple = _code_only_line(content, in_triple)
        if not is_added:
            continue
        if re.search(r"shell\s*=\s*True", code):
            trimmed = line.strip()[:100]
            if trimmed not in flagged:
                findings["danger"].append({
                    "pattern": "shell=True вне вызова на той же строке (многострочный subprocess?)",
                    "line": trimmed,
                })
        for pat, desc in EXFIL_PATTERNS.items():
            if re.search(pat, code):
                findings["exfil"].append({"pattern": desc, "line": line.strip()[:100]})
        for pat, desc in PERSIST_PATTERNS.items():
            if re.search(pat, code):
                findings["persist"].append({"pattern": desc, "line": line.strip()[:100]})

    # Дедупликация эксфильтрации/персистентности (паттерн+строка)
    def _dedup(items):
        seen, out = set(), []
        for it in items:
            k = (it["pattern"], it["line"])
            if k not in seen:
                seen.add(k)
                out.append(it)
        return out

    findings["exfil"] = _dedup(findings["exfil"])
    findings["persist"] = _dedup(findings["persist"])

    # Поиск хороших паттернов (дедупликация — один паттерн = одна похвала)
    added_code = "\n".join(added_lines)
    for pat, desc in GOOD_PATTERNS.items():
        if re.search(pat, added_code) and desc not in findings["good"]:
            findings["good"].append(desc)

    # Статистика
    findings["stats"]["added_lines"] = len(added_lines)
    findings["stats"]["deleted_lines"] = len(
        [l for l in diff_text.split("\n") if l.startswith("-") and not l.startswith("---")])

    # Оценка
    danger_count = len(findings["danger"])
    syntax_count = len(findings["syntax_errors"])
    security_count = len(findings["exfil"]) + len(findings["persist"])
    if syntax_count > 0:
        findings["verdict"] = "🔴 FAIL — ошибки синтаксиса"
    elif security_count > 0:
        findings["verdict"] = "🔴 SEC — потенциальная эксфильтрация/персистентность (проверить вручную)"
    elif danger_count > 2:
        findings["verdict"] = "🟡 WARN — много рискованных паттернов"
    elif danger_count > 0:
        findings["verdict"] = "🟢 OK — есть замечания"
    else:
        findings["verdict"] = "✅ CLEAN — без замечаний"

    return findings


def save_report(commit_ref: str, findings: dict, reviews_dir=None) -> Path:
    """Сохранить отчёт в data/reviews/. reviews_dir — тестовый оверрайд."""
    rdir = Path(reviews_dir) if reviews_dir else REVIEWS_DIR
    rdir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_ref = commit_ref.replace("..", "_").replace("~", "t")
    fname = rdir / f"review_{ts}_{safe_ref}.json"
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

    if findings["exfil"]:
        print(f"\n🔴 Эксфильтрация ({len(findings['exfil'])}):")
        for d in findings["exfil"]:
            print(f"   ❗ {d['pattern']}")
            print(f"      → {d['line']}")

    if findings["persist"]:
        print(f"\n🔴 Персистентность ({len(findings['persist'])}):")
        for d in findings["persist"]:
            print(f"   ❗ {d['pattern']}")
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


def main(argv=None) -> int:
    import argparse

    p = argparse.ArgumentParser(description="AGI Code Reviewer")
    p.add_argument("target", nargs="?", default="last",
                   help="commit/range: 'last' (HEAD~1..HEAD), 'all' (all AGI), <hash>, <range>")
    p.add_argument("--save", action="store_true", help="Сохранить отчёт в JSON")
    args = p.parse_args(argv)

    if args.target == "last":
        ref = "HEAD~1..HEAD"
    elif args.target == "all":
        log = run(["git", "-C", str(REPO_DIR), "log", "--oneline", "--grep=\\[agi\\]", "--format=%H"])
        hashes = [h for h in log.split("\n") if h]
        if not hashes:
            print("❌ Нет AGI-коммитов")
            return 1
        # Без ~1: у старейшего AGI-коммита в shallow-клоне нет родителя
        ref = f"{hashes[-1]}..{hashes[0]}"
    else:
        ref = args.target

    print(f"🔍 Ревью: {ref}")
    stat, diff = get_diff(ref)

    if not diff:
        print("❌ Нет изменений для ревью")
        return 0

    findings = review_diff(stat + "\n" + DIFF_MARKER + "\n" + diff)
    print_report(findings)

    if args.save:
        path = save_report(ref, findings)
        print(f"\n📁 Отчёт сохранён: {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
