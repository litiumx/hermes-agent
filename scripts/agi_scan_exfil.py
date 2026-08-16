#!/usr/bin/env python3
"""agi_scan_exfil.py — exfil-проверка экспортов для proactive_scan (цикл 36).

Grow point из SELF_IMPROVE_2026-08-16 (цикл 35): «check_exfil honeytoken на
экспортах сессий/email в proactive_scan». Хонейтокены (agi_honeytoken.py)
закрывают вектор «память → внешний контент»: если маркер приманки всплыл
в экспорте сессии, email-файле или логе — память выносили.

Этот модуль — мост (по образцу agi_scan_context.py):
- scan_exports(): обход каталогов экспортов (рекурсивно, лимиты по размеру
  файла и числу файлов), прогон каждого файла через check_exfil;
- exfil_block(): готовый текстовый блок для стартового скана.

Дефолтные каталоги: $HERMES_HOME/data/exports, $HERMES_HOME/data/sessions
(переопределяются через AGI_EXFIL_DIRS, разделитель ':').
Расширения: .json .md .txt .log .eml — экспорты сессий/email/логи.
Всё молча падает в дефолты — скан не должен валиться.
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import agi_honeytoken as ht

HERMES_HOME = os.environ.get("HERMES_HOME", "/root/.hermes")
DEFAULT_DIRS = [
    os.path.join(HERMES_HOME, "data/exports"),
    os.path.join(HERMES_HOME, "data/sessions"),
]
EXTENSIONS = {".json", ".md", ".txt", ".log", ".eml"}
DEFAULT_MAX_SIZE = 2 * 1024 * 1024   # 2MB на файл
DEFAULT_MAX_FILES = 200              # лимит файлов за скан


def _target_dirs(dirs=None) -> list:
    """Каталоги для скана: аргумент > env AGI_EXFIL_DIRS > дефолт."""
    if dirs is not None:
        return list(dirs)
    env = os.environ.get("AGI_EXFIL_DIRS", "").strip()
    if env:
        return [d for d in env.split(":") if d]
    return DEFAULT_DIRS


def _iter_files(dirs, max_files):
    """Рекурсивный обход: файлы с разрешёнными расширениями, лимит по числу."""
    seen = 0
    for d in dirs:
        root = Path(d)
        if not root.is_dir():
            continue
        try:
            for p in sorted(root.rglob("*")):
                if seen >= max_files:
                    return
                if p.is_file() and p.suffix.lower() in EXTENSIONS:
                    seen += 1
                    yield p
        except OSError:
            continue


def scan_exports(dirs=None, store_path=None,
                 max_size=DEFAULT_MAX_SIZE, max_files=DEFAULT_MAX_FILES) -> list:
    """Найти файлы экспортов, содержащие маркеры хонейтокенов.

    Возвращает список: [{"file": str, "markers": [marker, ...]}].
    Пустой стор / нет каталогов / битый стор → [].
    """
    found = []
    for p in _iter_files(_target_dirs(dirs), max_files):
        try:
            if p.stat().st_size > max_size:
                continue
        except OSError:
            continue
        leaked = ht.check_exfil(str(p), store_path=store_path)
        if leaked:
            markers = [t["marker"] for t in leaked
                       if isinstance(t, dict) and t.get("marker")]
            if markers:
                found.append({"file": str(p), "markers": markers})
    return found


def exfil_block(dirs=None, store_path=None,
                max_size=DEFAULT_MAX_SIZE, max_files=DEFAULT_MAX_FILES) -> str:
    """Блок «🛡️ Exfil» для proactive_scan. Без исключений."""
    try:
        data = ht._load(store_path)
        total = len([t for t in data.get("honeytokens", [])
                     if isinstance(t, dict) and t.get("marker")])
    except Exception:
        total = 0
    if total == 0:
        return ("🛡️ Exfil: стор хонейтокенов пуст — посади приманки: "
                "python3 scripts/agi_honeytoken.py plant 3")
    try:
        leaks = scan_exports(dirs=dirs, store_path=store_path,
                             max_size=max_size, max_files=max_files)
    except Exception:
        leaks = []
    if not leaks:
        return "🛡️ Exfil: чисто (%d приманок, %d каталогов)" % (
            total, len(_target_dirs(dirs)))
    lines = ["🔴 Exfil: LEAK — %d файл(ов) содержат приманки:" % len(leaks)]
    for f in leaks:
        lines.append("  %s -> %s" % (f["file"], ", ".join(f["markers"])))
    return "\n".join(lines)


def main():
    print(exfil_block())


if __name__ == "__main__":
    main()
