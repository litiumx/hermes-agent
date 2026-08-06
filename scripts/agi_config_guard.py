#!/usr/bin/env python3
"""agi_config_guard.py — реальная проверка целостности конфигов.

Закрывает дыру error_pattern_learner: там config_corrupt детектится ТОЛЬКО
по регексу в логах (config.*corrupt|JSONDecodeError) — ложные срабатывания
и пропуски. Этот скрипт валидирует ФАКТИЧЕСКИЕ файлы:

- config.yaml — полный YAML-парсинг (не regex)
- data/*.json, session/*.json, *.json в корне — JSON-парсинг
- файлы >1MB пропускаются (не конфиги)

Использование:
  python3 agi_config_guard.py            # статус, exit 0/1
  python3 agi_config_guard.py --json     # машиночитаемо (для интеграций)
  python3 agi_config_guard.py --write    # записать результат в error_patterns.json
  python3 agi_config_guard.py --strict   # exit 2, если YAML не проверен (нет PyYAML)

Интеграция: вызывается proactive_scan.py / cron; --write кормит
error_patterns.json (config_corrupt: N) чтобы self_directed_queue
получал реальные риски, а не regex-шум.
"""
import json
import os
import sys
from pathlib import Path

try:
    import yaml
except ImportError:
    yaml = None  # PyYAML нет — YAML-проверка пропускается, JSON всё равно

ROOT = Path("/root/.hermes")
CONFIG_FILE = ROOT / "config.yaml"
PATTERNS_FILE = ROOT / "data" / "error_patterns.json"
MAX_FILE_BYTES = 1_000_000  # 1MB — больше не конфиг
# Куда смотрим: корень + data + session, но не .git/worktrees/cache
SCAN_DIRS = [ROOT, ROOT / "data", ROOT / "session"]
SKIP_DIR_PARTS = {".git", ".worktrees", "cache", "node_modules", "logs", "audio_cache"}


def _is_json_candidate(p: Path) -> bool:
    return p.suffix == ".json" and p.stat().st_size <= MAX_FILE_BYTES


def _collect_files() -> list:
    files = []
    seen = set()
    for d in SCAN_DIRS:
        if not d.exists():
            continue
        for p in sorted(d.rglob("*")):
            if not p.is_file():
                continue
            if any(part in SKIP_DIR_PARTS for part in p.parts):
                continue
            if p.suffix not in (".json", ".yaml", ".yml"):
                continue
            try:
                if p.stat().st_size > MAX_FILE_BYTES:
                    continue
            except OSError:
                continue
            if p.resolve() in seen:
                continue
            seen.add(p.resolve())
            files.append(p)
    return files


def _check_yaml(p: Path) -> str:
    if yaml is None:
        return "skip:PyYAML"
    try:
        yaml.safe_load(p.read_text())
        return "ok"
    except yaml.YAMLError as e:
        # первая строка ошибки — точная причина
        return f"corrupt:{str(e).splitlines()[0][:120]}"


def _check_json(p: Path) -> str:
    try:
        if p.stat().st_size == 0:
            # Пустой файл — валидный placeholder (состояние ещё не записано),
            # не мусор в отчёте. Не "corrupt" и не "ok".
            return "empty"
        json.loads(p.read_text())
        return "ok"
    except json.JSONDecodeError as e:
        return f"corrupt:line {e.lineno} col {e.colno}: {e.msg}"
    except (UnicodeDecodeError, OSError) as e:
        return f"corrupt:{str(e)[:120]}"


def validate() -> dict:
    """Вернуть {path: status} для всех конфиг-файлов."""
    results = {}
    for p in _collect_files():
        if p.suffix in (".yaml", ".yml"):
            results[str(p)] = _check_yaml(p)
        else:
            results[str(p)] = _check_json(p)
    return results


def _write_to_patterns(n_corrupt: int) -> bool:
    """Обновить error_patterns.json: config_corrupt = реальное число битых.

    Атомарно: пишем во временный файл в той же директории, затем os.replace.
    Прямая write_text при краше посередине оставила бы БИТЫЙ patterns.json —
    ироничная поломка для скрипта целостности.
    """
    if not PATTERNS_FILE.exists():
        return False
    try:
        data = json.loads(PATTERNS_FILE.read_text())
    except json.JSONDecodeError:
        return False  # сам patterns.json битый — не трогаем
    data["config_corrupt_real"] = n_corrupt
    data["config_corrupt_checked_at"] = __import__("time").time()
    tmp = PATTERNS_FILE.with_name(PATTERNS_FILE.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False))
    os.replace(tmp, PATTERNS_FILE)  # атомарно на той же ФС
    return True


def main(argv=None) -> int:
    if argv is None:
        argv = sys.argv[1:]
    flags = set(argv)
    results = validate()
    corrupt = {p: s for p, s in results.items() if s.startswith("corrupt")}
    empties = {p: s for p, s in results.items() if s.startswith("empty")}
    skipped = sum(1 for s in results.values() if s.startswith("skip"))

    if "--json" in flags:
        out = {
            "checked": len(results),
            "corrupt": corrupt,
            "empty": len(empties),
            "skipped": skipped,
            "ok": True if not corrupt else False,
        }
        print(json.dumps(out, indent=2, ensure_ascii=False))
    else:
        print(f"🔧 Config Guard: проверено {len(results)} файлов"
              f"{f' ({skipped} YAML пропущено: нет PyYAML)' if skipped else ''}")
        if corrupt:
            print(f"  🔴 БИТЫХ: {len(corrupt)}")
            for p, s in corrupt.items():
                print(f"    {p}: {s}")
        else:
            print("  ✅ Все конфиги валидны")
        if empties:
            print(f"  ⚪ Пустых (placeholder): {len(empties)}")

    if "--write" in flags:
        ok_write = _write_to_patterns(len(corrupt))
        if "--json" not in flags:
            if ok_write:
                print(f"  → error_patterns.json обновлён (config_corrupt_real={len(corrupt)})")
            else:
                print("  → error_patterns.json не найден/битый — запись пропущена")

    # --strict: PyYAML нет, а YAML-файлы есть → честный fail вместо тихого skip
    if "--strict" in flags and skipped:
        n_yaml = sum(1 for s in results.values() if s.startswith("skip"))
        print(f"  ❌ --strict: {n_yaml} YAML-файлов не проверено (PyYAML не установлен)")
        return 2
    return 1 if corrupt else 0


if __name__ == "__main__":
    sys.exit(main())
