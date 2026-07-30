#!/usr/bin/env python3
"""
Само-улучшение из ошибок. Раз в неделю анализирует логи ошибок
и патчит SOUL.md чтобы избежать повторения. 0 токенов.
"""
import re, os, json, sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from collections import Counter

HERMES_HOME = Path(os.environ.get("HERMES_HOME", os.path.expanduser("~/.hermes")))
SOUL_PATH = HERMES_HOME / "SOUL.md"
LOGS_DIR = HERMES_HOME / "logs"
ERROR_REPORT_DIR = HERMES_HOME

# Паттерны ошибок → правила для SOUL.md
ERROR_RULES = {
    "gateway instance is already running": {
        "rule": "При рестарте gateway: всегда делай `hermes gateway stop` перед `hermes gateway start`.\n   Проверяй gateway.pid и убивай старый процесс если завис.",
        "section": "tool_protocol",
    },
    "context_length_exceeded": {
        "rule": "При длинных сессиях: используй /compact до того как контекст достигнет 70%.\n   Разбивай большие задачи на подзадачи и spawn субагентов.",
        "section": "execution_protocol",
    },
    "rate_limit": {
        "rule": "При 429 ошибках: жди 30 секунд, переключайся на fallback модель (GitHub Models).\n   Не делай больше 50 API-вызовов в минуту.",
        "section": "tool_protocol",
    },
    "timeout": {
        "rule": "Для команд дольше 5 минут: используй terminal(background=true, notify_on_complete=true).\n   Не жди foreground — это блокирует агента.",
        "section": "tool_protocol",
    },
    "playwright.*не отвечает": {
        "rule": "Перед вызовом Playwright: проверяй SSH-туннель до Windows.\n   Команда: ssh aleksey@100.105.159.88 'netstat -an | grep 8931'",
        "section": "tool_protocol",
    },
    "MCP.*not.*responding|MCP.*disconnect": {
        "rule": "Перед вызовом любого MCP-инструмента: проверяй что сервер отвечает.\n   Используй `curl` или `nc -z` для проверки порта.",
        "section": "tool_protocol",
    },
    "file.*not found|No such file": {
        "rule": "Перед чтением/редактированием файла: ВСЕГДА делай search_files(target='files') или ls.\n   НИКОГДА не предполагай что файл существует.",
        "section": "execution_protocol",
    },
    "permission denied": {
        "rule": "Перед записью в системную директорию: проверяй права через `ls -la`.\n   Используй `sudo` только когда явно разрешено.",
        "section": "execution_protocol",
    },
}

def scan_logs():
    """Сканирует все логи за неделю, считает типы ошибок."""
    patterns = Counter()
    one_week_ago = datetime.now() - timedelta(days=7)

    log_files = list(LOGS_DIR.glob("*.log")) if LOGS_DIR.exists() else []
    if not log_files:
        print("No log files found")
        return patterns

    for lf in log_files:
        try:
            content = lf.read_text(errors="ignore")
            for err_name, _ in ERROR_RULES.items():
                count = len(re.findall(err_name, content, re.IGNORECASE))
                if count > 0:
                    patterns[err_name] += count
        except Exception:
            continue

    # Читаем также ERROR-отчёты
    for er in ERROR_REPORT_DIR.glob("ERRORS_*.md"):
        try:
            content = er.read_text(errors="ignore")
            for err_name, _ in ERROR_RULES.items():
                count = len(re.findall(err_name, content, re.IGNORECASE))
                if count > 0:
                    patterns[err_name] += count
        except Exception:
            continue

    return patterns

def patch_soul(patterns: Counter):
    """Добавляет правила в SOUL.md на основе найденных ошибок."""
    if not SOUL_PATH.exists():
        print(f"SOUL.md not found at {SOUL_PATH}")
        return 0

    soul = SOUL_PATH.read_text()
    patches_applied = 0
    sections = {}

    for err_name, count in patterns.most_common():
        if count < 3:  # Минимум 3 повторения чтобы патчить
            continue
        rule_info = ERROR_RULES.get(err_name)
        if not rule_info:
            continue

        section = rule_info["section"]
        rule_text = rule_info["rule"]

        # Проверяем нет ли уже такого правила
        if rule_text[:50] in soul:
            continue

        if section not in sections:
            sections[section] = []
        sections[section].append(rule_text)
        patches_applied += 1

    if patches_applied == 0:
        return 0

    # Вставляем правила в соответствующие секции
    for section, rules in sections.items():
        # Ищем закрывающий тег секции
        tag = f"</{section}>"
        if tag not in soul:
            # Ищем открывающий тег
            open_tag = f"<{section}>"
            if open_tag not in soul:
                continue
            # Вставляем перед концом секции (до следующего <!-- или до EOF)
            insert_pos = soul.find(open_tag) + len(open_tag)
            # Ищем начало следующей секции
            next_section = soul.find("<!--", insert_pos)
            if next_section == -1:
                next_section = len(soul)
        else:
            insert_pos = soul.find(tag)

        if insert_pos == -1:
            continue

        rule_block = "\n\n<!-- AUTO-PATCHED by self_improve.py — based on error patterns -->\n" + \
                     "\n".join(f"   {i+1}. {r}" for i, r in enumerate(rules)) + "\n"

        soul = soul[:insert_pos] + rule_block + soul[insert_pos:]

    SOUL_PATH.write_text(soul)
    return patches_applied

def generate_report(patterns: Counter, patches: int):
    """Пишет отчёт о само-улучшении."""
    now = datetime.now(timezone(timedelta(hours=3)))
    report = [
        f"# 🧬 Само-улучшение — {now.strftime('%d.%m.%Y %H:%M MSK')}",
        "",
        "## Найденные паттерны ошибок",
        "| Ошибка | Частота |",
        "|--------|---------|",
    ]
    for err, count in patterns.most_common(10):
        report.append(f"| {err} | {count} |")

    report.append("")
    report.append(f"## Результат")
    if patches > 0:
        report.append(f"✅ {patches} правил добавлено в SOUL.md")
    else:
        report.append("ℹ️ Недостаточно данных для патчинга (нужно ≥3 повторений)")

    report.append("")
    report.append("---")
    report.append("*Сгенерировано self_improve.py*")

    report_path = HERMES_HOME / f"SELF_IMPROVE_{now.strftime('%Y-%m-%d')}.md"
    report_path.write_text("\n".join(report))
    print(f"Report: {report_path}")
    return report_path

def main():
    print("Scanning error patterns...")
    patterns = scan_logs()
    print(f"Found {len(patterns)} error types")

    if not patterns:
        print("No patterns to analyze")
        return

    print("\nTop errors:")
    for err, count in patterns.most_common(10):
        print(f"  {err}: {count}")

    patches = patch_soul(patterns)
    report_path = generate_report(patterns, patches)

    if patches > 0:
        print(f"\n✅ Patched SOUL.md with {patches} new rules")
        print("Restart Hermes or /reload-soul for changes to take effect")
    else:
        print("\nNo patches needed (errors below threshold or already patched)")

if __name__ == "__main__":
    main()
