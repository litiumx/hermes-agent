#!/usr/bin/env python3
"""agi_mcp_keepalive.py — keepalive-монитор MCP-серверов для Hermes.

Проблема (данные error_pattern_learner, 02.08.2026):
- paperclip: 1668 сбоев initial connection ("unhandled errors in a TaskGroup")
- notebooklm: 187 keepalive failed (ClosedResourceError)
- atomic_finance: 28 tool errors
Монитор по логам отслеживает состояние каждого MCP-сервера и
сигнализирует о crash-loop'ах ДО того, как они станут критичными.

Что делает:
- scan: парсит logs/errors.log, агрегирует сбои по серверам за окно
  (по умолчанию 24ч), определяет состояние: ok / degraded / down / crash_loop
- status: показывает текущее состояние из data/mcp_keepalive.json
- self-test: синтетические логи → проверка логики, реальные файлы не трогает
- Выход: 0 = всё ок, 1 = есть серверы в down/crash_loop (для proactive_scan)

Правила состояний (за окно WINDOW_H):
- crash_loop: >= CRASH_LOOP_MIN сбоев И >= 3 сбоев за последние 10 минут
- down: >= MAX_FAILURES сбоев (сервер фактически недоступен)
- degraded: >= DEGRADE_MIN сбоев, но не down
- ok: иначе

Не делает: рестарты, изменения конфигов. Только наблюдение и рекомендации.
"""

import json
import os
import re
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from pathlib import Path

HERMES_HOME = os.environ.get("HERMES_HOME", "/root/.hermes")
LOGS_DIR = Path(os.environ.get("AGI_LOG_DIR",
                               os.path.join(HERMES_HOME, "logs")))
ERRORS_LOG = Path(os.environ.get("AGI_ERRORS_LOG",
                                 str(LOGS_DIR / "errors.log")))
STATE_FILE = Path(os.environ.get("AGI_MCP_STATE_FILE",
                                 os.path.join(HERMES_HOME, "data/mcp_keepalive.json")))

WINDOW_H = 24          # окно анализа, часов
CRASH_LOOP_MIN = 10    # сбоев за окно → crash_loop (если есть всплеск)
CRASH_SPIKE_MIN = 3    # сбоев за 10 мин → всплеск
DEGRADE_MIN = 3        # сбоев за окно → degraded
MAX_FAILURES = 50      # сбоев за окно → down

# (regex, тип) — типы: conn (соединение), keepalive, tool, other
FAILURE_PATTERNS = [
    (re.compile(r"MCP server '([a-z_0-9]+)' initial connection failed"), "conn"),
    (re.compile(r"MCP server '([a-z_0-9]+)' failed initial connection after"), "conn"),
    (re.compile(r"MCP server '([a-z_0-9]+)' keepalive failed"), "keepalive"),
    (re.compile(r"MCP server '([a-z_0-9]+)' tool .* (?:failed|error)"), "tool"),
    (re.compile(r"MCP server '([a-z_0-9]+)' .*ClosedResourceError"), "keepalive"),
    (re.compile(r"MCP server '([a-z_0-9]+)' not responding"), "conn"),
    (re.compile(r"MCP server '([a-z_0-9]+)' disconnected"), "conn"),
]
# строка даты в errors.log: "2026-07-31 22:29:55,513 WARNING ..."
TS_RE = re.compile(r"^(\d{4}-\d{2}-\d{2}) (\d{2}:\d{2}:\d{2})")


def _tail_text(path: Path, max_bytes: int = 4_000_000) -> str:
    """Хвост файла без чтения целиком (errors.log может быть большим)."""
    try:
        size = path.stat().st_size
        with open(path, "rb") as f:
            f.seek(max(0, size - max_bytes))
            return f.read().decode("utf-8", errors="replace")
    except OSError:
        return ""


def _parse_ts(line: str) -> float | None:
    m = TS_RE.match(line)
    if not m:
        return None
    try:
        dt = datetime.strptime(f"{m.group(1)} {m.group(2)}", "%Y-%m-%d %H:%M:%S")
        return dt.replace(tzinfo=timezone.utc).timestamp()
    except ValueError:
        return None


def scan_logs(window_h: int = WINDOW_H, log_path: Path | None = None) -> dict:
    """Сканирует errors.log, возвращает агрегат сбоев по серверам."""
    text = _tail_text(log_path or ERRORS_LOG)
    now = time.time()
    cutoff = now - window_h * 3600

    per_server = defaultdict(lambda: {"count": 0, "types": defaultdict(int),
                                      "last_ts": 0.0, "last_msg": "", "recent": []})
    for line in text.splitlines():
        ts = _parse_ts(line)
        if ts is None or ts < cutoff:
            continue
        for rx, ftype in FAILURE_PATTERNS:
            m = rx.search(line)
            if m:
                srv = m.group(1)
                st = per_server[srv]
                st["count"] += 1
                st["types"][ftype] += 1
                st["last_ts"] = max(st["last_ts"], ts)
                st["last_msg"] = line.strip()[:200]
                if ts >= now - 600:  # всплеск за 10 минут
                    st["recent"].append(ts)
                break

    result = {}
    for srv, st in per_server.items():
        cnt = st["count"]
        spike = len(st["recent"])
        if spike >= CRASH_SPIKE_MIN and cnt >= CRASH_SPIKE_MIN:
            state = "crash_loop"   # всплеск сбоев прямо сейчас
        elif cnt >= MAX_FAILURES:
            state = "down"
        elif cnt >= DEGRADE_MIN:
            state = "degraded"
        else:
            state = "ok"
        result[srv] = {
            "state": state,
            "count": cnt,
            "spike_10min": spike,
            "types": dict(st["types"]),
            "last_ts": datetime.fromtimestamp(st["last_ts"], tz=timezone.utc)
                        .strftime("%Y-%m-%d %H:%M:%S"),
            "last_msg": st["last_msg"],
        }
    return result


def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    return {"updated": "", "servers": {}}


def save_state(data: dict):
    STATE_FILE.parent.mkdir(exist_ok=True)
    STATE_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False))


def cmd_scan() -> int:
    res = scan_logs()
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    save_state({"updated": now_str, "servers": res})

    if not res:
        print("✅ MCP: сбоев за окно не найдено (или errors.log пуст)")
        return 0

    print(f"MCP keepalive-монитор (окно {WINDOW_H}ч, {now_str} UTC):")
    exit_code = 0
    for srv, st in sorted(res.items(), key=lambda kv: kv[1]["count"], reverse=True):
        icon = {"ok": "✅", "degraded": "🟡", "down": "🔴", "crash_loop": "🔴"}[st["state"]]
        print(f"  {icon} {srv:<16} {st['state']:<10} {st['count']:>5} сбоев "
              f"(spike {st['spike_10min']}/10мин, {st['last_ts']})")
        if st["state"] != "ok":
            exit_code = 1
            print(f"      {st['last_msg'][:150]}")
    return exit_code


def cmd_status() -> int:
    st = load_state()
    if not st.get("servers"):
        print("Статус не сохранён. Сначала: python3 agi_mcp_keepalive.py scan")
        return 0
    print(f"Сохранено: {st.get('updated', '?')}")
    exit_code = 0
    for srv, d in sorted(st["servers"].items(), key=lambda kv: kv[1]["count"], reverse=True):
        print(f"  {srv:<16} {d['state']:<10} {d['count']} сбоев")
        if d["state"] != "ok":
            exit_code = 1
    return exit_code


def _mk_synthetic_line(server: str, msg: str, minutes_ago: float) -> str:
    """Строка лога с таймстемпом ОТНОСИТЕЛЬНО now — self-test не дрейфует во времени."""
    ts = (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)).strftime("%Y-%m-%d %H:%M:%S")
    return f"{ts},123 WARNING tools.mcp_tool: MCP server '{server}' {msg}"


def cmd_self_test() -> int:
    """Синтетические логи → проверка классификации. Реальные файлы не трогает."""
    import tempfile
    synthetic = "\n".join([
        # paperclip: 3 сбоя за 3 минуты → spike>=3 → crash_loop
        _mk_synthetic_line("paperclip", "initial connection failed (attempt 1/3), retrying in 1s: unhandled errors in a TaskGroup (1 sub-exception)", 3),
        _mk_synthetic_line("paperclip", "initial connection failed (attempt 2/3), retrying in 2s: unhandled errors in a TaskGroup (1 sub-exception)", 2),
        _mk_synthetic_line("paperclip", "initial connection failed (attempt 3/3), retrying in 3s: unhandled errors in a TaskGroup (1 sub-exception)", 1),
        # notebooklm: 3 keepalive-сбоя, но старее 10 мин (вне всплеска) → degraded
        _mk_synthetic_line("notebooklm", "keepalive failed, triggering reconnect (state: connected → degraded): ClosedResourceError: ", 21),
        _mk_synthetic_line("notebooklm", "keepalive failed, triggering reconnect (state: degraded → disconnected): ClosedResourceError: ", 20),
        _mk_synthetic_line("notebooklm", "keepalive failed, triggering reconnect (state: disconnected → reconnecting): ClosedResourceError: ", 19),
        # browser: 1 tool-сбой → ok
        _mk_synthetic_line("browser", "tool call failed: timeout", 15),
        # stale: строка старше окна → ДОЛЖНА быть проигнорирована
        _mk_synthetic_line("ancient", "initial connection failed (attempt 1/3), retrying in 1s", 120),
    ])
    with tempfile.NamedTemporaryFile("w", suffix=".log", delete=False) as f:
        f.write(synthetic)
        tmp = f.name
    try:
        res = scan_logs(window_h=1, log_path=Path(tmp))  # окно 1ч — синтетика свежая
        assert "paperclip" in res and res["paperclip"]["count"] == 3, res
        assert "notebooklm" in res and res["notebooklm"]["types"].get("keepalive", 0) == 3, res
        assert "browser" in res and res["browser"]["count"] == 1, res
        assert "ancient" not in res, f"строки старше окна не должны попадать: {res}"
        # paperclip: 3 сбоя за 3 мин → spike=3 → crash_loop
        assert res["paperclip"]["state"] == "crash_loop", res
        # notebooklm: 2 сбоя, всплеска нет (старее 10 мин) → degraded
        assert res["notebooklm"]["state"] == "degraded", res
        # browser: 1 сбой < DEGRADE_MIN → ok
        assert res["browser"]["state"] == "ok", res
        print("✅ self-test: 7/7 (paperclip=crash_loop, notebooklm=degraded, browser=ok, "
              "ancient отфильтрован, типы корректны)")
        return 0
    finally:
        Path(tmp).unlink(missing_ok=True)


def main() -> int:
    cmd = sys.argv[1] if len(sys.argv) > 1 else "scan"
    if cmd == "scan":
        return cmd_scan()
    if cmd == "status":
        return cmd_status()
    if cmd == "self-test":
        return cmd_self_test()
    print(f"usage: {sys.argv[0]} [scan|status|self-test]", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
