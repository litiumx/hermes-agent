#!/usr/bin/env python3
"""agi_honeytoken.py — детект несанкционированного ЧТЕНИЯ/выноса памяти.

v1 (15.08.2026): grow point SELF_IMPROVE_2026-08-15 #3 (arXiv 2608.11436).
Verified Memory CAS защищает ЗАПИСЬ (нельзя подменить запись), но не
отвечает на вопрос «читали ли память / выносили ли её содержимое».
Решение — приманки-хонейтокены: записи с уникальным маркером
AGI_HONEY_<8hex>, которые легитимный агент НИКОГДА не использует
(в note — правдоподобный фейковый секрет, чтобы вынос всей записи был
заметен). Если маркер всплыл в тексте (экспорт сессии, email, diff,
лог, ответ стороннему каналу) — это индикатор утечки памяти.

Контекст: MemGhost (ложная память через email) показал, что вектор
«внешний контент → память → действия» реален. Хонейтокены закрывают
обратный вектор: «память → внешний контент».

API:
  plant(n=3, note=None, store_path=None) -> list[dict]
  check_exfil(text, store_path=None) -> list[dict]
  verify(store_path=None) -> dict
  status(store_path=None) -> dict

CLI:
  plant <n>            — посадить N приманок
  check <text|file>    — искать маркеры в тексте или файле (exit 1 = утечка)
  verify               — целостность стора (exit 1 = пропажа)
  status               — сводка

Безопасность: чистый Python + JSON, без сети/shell. Маркеры не секреты —
это детекторы; их наличие в сторе безопасно.
"""
import json
import os
import sys
import time
import uuid
from pathlib import Path

HERMES_HOME = os.environ.get("HERMES_HOME", "/root/.hermes")
HONEY_FILE = Path(os.environ.get(
    "AGI_HONEY_FILE", os.path.join(HERMES_HOME, "data/honeytokens.json")))
MARKER_PREFIX = "AGI_HONEY_"
NOTE_TEMPLATE = ("honeytoken {marker}: api_key=sk-honey-{marker}-{hex8} "
                 "user=admin@corp.local (decoy — never use)")


def _store_path(store_path=None) -> Path:
    return Path(store_path) if store_path is not None else HONEY_FILE


def _load(store_path=None) -> dict:
    """Загрузка стора. Битый/отсутствующий файл = пустой стор (safe).

    planted_total — счётчик ВСЕХ посаженных приманок (для детекта удаления:
    если записей меньше, чем было посажено — стор тронут). Legacy-сторы
    без поля получают planted_total = len(honeytokens)."""
    p = _store_path(store_path)
    if not p.exists():
        return {"honeytokens": [], "planted_total": 0}
    try:
        data = json.loads(p.read_text())
    except (json.JSONDecodeError, OSError, ValueError):
        return {"honeytokens": [], "planted_total": 0}
    if not isinstance(data, dict) or not isinstance(data.get("honeytokens"), list):
        return {"honeytokens": [], "planted_total": 0}
    if not isinstance(data.get("planted_total"), int):
        data["planted_total"] = len(data["honeytokens"])
    return data


def _save(data: dict, store_path=None) -> None:
    p = _store_path(store_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2))


def _new_marker(existing: set) -> str:
    while True:
        m = MARKER_PREFIX + uuid.uuid4().hex[:8]
        if m not in existing:
            return m


def plant(n: int = 3, note: str = None, store_path=None) -> list:
    """Посадить n новых приманок. Возвращает созданные записи."""
    n = max(1, int(n))
    data = _load(store_path)
    existing = {t["marker"] for t in data["honeytokens"]
                if isinstance(t, dict) and t.get("marker")}
    created = []
    now = time.time()
    for _ in range(n):
        marker = _new_marker(existing)
        rec = {
            "marker": marker,
            "planted_at": now,
            "note": note if note else NOTE_TEMPLATE.format(marker=marker, hex8=uuid.uuid4().hex[:8]),
        }
        data["honeytokens"].append(rec)
        existing.add(marker)
        created.append(rec)
    data["planted_total"] = data.get("planted_total", len(data["honeytokens"]) - n) + n
    _save(data, store_path)
    return created


def _read_text(text) -> str:
    """Аргумент-строка: если это существующий файл — читаем его."""
    if isinstance(text, Path):
        text = str(text)
    if isinstance(text, str) and text:
        p = Path(text)
        if p.exists() and p.is_file():
            try:
                return p.read_text(errors="replace")
            except OSError:
                return ""
    return text or ""


def check_exfil(text, store_path=None) -> list:
    """Найти маркеры приманок в тексте/файле. Возвращает утёкшие записи."""
    data = _load(store_path)
    tokens = data.get("honeytokens", [])
    if not tokens:
        return []
    haystack = _read_text(text)
    if not haystack:
        return []
    leaked = []
    for t in tokens:
        if isinstance(t, dict) and t.get("marker") and t["marker"] in haystack:
            leaked.append(t)
    return leaked


def verify(store_path=None) -> dict:
    """Целостность стора: повреждённые записи + удаления.

    missing — записи с битой структурой (нет marker/planted_at);
    removed — разница planted_total и фактического числа записей
    (удаление без следа)."""
    data = _load(store_path)
    tokens = data.get("honeytokens", [])
    missing = [t for t in tokens
               if not isinstance(t, dict) or not t.get("marker")
               or not t.get("planted_at")]
    valid = len(tokens) - len(missing)
    planted = data.get("planted_total", len(tokens))
    removed = max(0, planted - valid)
    return {"total": len(tokens), "missing": missing, "removed": removed}


def status(store_path=None) -> dict:
    """Сводка: количество, возраст самой старой приманки."""
    data = _load(store_path)
    tokens = [t for t in data.get("honeytokens", [])
              if isinstance(t, dict) and t.get("marker")]
    now = time.time()
    ages = [now - t.get("planted_at", now) for t in tokens]
    oldest = max(ages) / 3600.0 if ages else 0.0
    return {
        "total": len(tokens),
        "oldest_age_h": round(oldest, 2),
        "store": str(_store_path(store_path)),
    }


def _cli(argv):
    if len(argv) < 2:
        print(__doc__)
        return 2
    cmd = argv[1]
    if cmd == "plant":
        n = int(argv[2]) if len(argv) > 2 else 3
        created = plant(n)
        for t in created:
            print(f"planted {t['marker']}")
        print(f"total: {status()['total']}")
        return 0
    if cmd == "check":
        if len(argv) < 3:
            print("usage: agi_honeytoken.py check <text|file>")
            return 2
        leaked = check_exfil(argv[2])
        if leaked:
            print(f"LEAK: {len(leaked)} honeytoken(s) in content:")
            for t in leaked:
                print(f"  {t['marker']} (planted {t.get('planted_at', '?')})")
            return 1
        print("clean: no honeytokens found")
        return 0
    if cmd == "verify":
        res = verify()
        if res["missing"]:
            print(f"MISSING: {len(res['missing'])} broken/incomplete record(s)")
            return 1
        print(f"ok: {res['total']} honeytokens intact")
        return 0
    if cmd == "status":
        st = status()
        print(f"honeytokens: {st['total']}, oldest {st['oldest_age_h']}h, store: {st['store']}")
        return 0
    print(f"unknown command: {cmd}")
    return 2


if __name__ == "__main__":
    sys.exit(_cli(sys.argv))
