#!/usr/bin/env python3
"""agi_test_focus_agent.py — тесты компакции и кулдаунов focus_agent.
Все тесты изолированы в tempdir (не трогают /root/.hermes/data)."""
import json, os, sys, tempfile, time
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import agi_focus_agent as f

PASS = 0
FAIL = 0

def check(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  OK  {name}")
    else:
        FAIL += 1
        print(f"  FAIL {name} {extra}")

def make_env():
    """Подменить пути модуля на tempdir, вернуть (tmp, kb_path, hist_path)."""
    tmp = Path(tempfile.mkdtemp(prefix="agi_focus_test_"))
    f.KB_FILE = tmp / "knowledge_block.json"
    f.HISTORY_FILE = tmp / "focus_history.json"
    return tmp, f.KB_FILE, f.HISTORY_FILE

def seed_kb(entries):
    f.save_kb({"created": datetime.now().isoformat(), "knowledge": entries})

def ts_ago(days):
    return (datetime.now() - timedelta(days=days)).isoformat()

print("== 1. Дедуп по topic: свежайшая побеждает, источники склеиваются ==")
make_env()
seed_kb([
    {"topic": "docker", "content": "old", "source": "a", "timestamp": ts_ago(10)},
    {"topic": "docker", "content": "new", "source": "b", "timestamp": ts_ago(1)},
    {"topic": "unique", "content": "x", "source": "c", "timestamp": ts_ago(2)},
])
r = f.compact_knowledge()
check("deduped=1", r["deduped"] == 1, str(r))
check("after=2", r["after"] == 2, str(r))
kb = f.load_kb()
docker = [e for e in kb["knowledge"] if e["topic"] == "docker"][0]
check("свежайший контент", docker["content"] == "new", str(docker))
check("источники склеены", docker["source"] == "a,b", str(docker["source"]))
check("changed=True", r["changed"])

print("== 2. Старые записи НЕ прунятся пока лимит не превышен ==")
make_env()
seed_kb([
    {"topic": f"t{i}", "content": "c", "source": "s", "timestamp": ts_ago(40)}
    for i in range(5)
])
r = f.compact_knowledge(max_entries=10, max_age_days=30)
check("pruned_old=0 при малом KB", r["pruned_old"] == 0, str(r))
check("after=5 (старьё сохранено)", r["after"] == 5, str(r))
check("changed=False (ничего не тронуто)", r["changed"] is False, str(r))

print("== 3. Прунинг старья при превышении лимита ==")
make_env()
seed_kb([
    {"topic": f"old{i}", "content": "c", "source": "s", "timestamp": ts_ago(60)}
    for i in range(8)
] + [
    {"topic": f"new{i}", "content": "c", "source": "s", "timestamp": ts_ago(1)}
    for i in range(5)
])
r = f.compact_knowledge(max_entries=10, max_age_days=30)
check("pruned_old=8", r["pruned_old"] == 8, str(r))
check("after=5 (только свежие)", r["after"] == 5, str(r))
check("capped=0", r["capped"] == 0, str(r))

print("== 4. Финальный кап: свежие > лимита, capped считается честно ==")
make_env()
seed_kb([
    {"topic": f"t{i}", "content": "c", "source": "s", "timestamp": ts_ago(1)}
    for i in range(12)
])
r = f.compact_knowledge(max_entries=10, max_age_days=30)
check("capped=2", r["capped"] == 2, str(r))
check("after=10", r["after"] == 10, str(r))
check("pruned_old=0", r["pruned_old"] == 0, str(r))
kb = f.load_kb()
check("остались самые свежие", kb["knowledge"][0]["topic"] == "t11", kb["knowledge"][0]["topic"])

print("== 5. Пустой KB — no-op, без записи в history ==")
make_env()
r = f.compact_knowledge()
check("changed=False", r["changed"] is False, str(r))
check("after=0", r["after"] == 0)
check("history не создан", not f.HISTORY_FILE.exists())

print("== 6. История компакции пишется с полной статистикой ==")
make_env()
seed_kb([
    {"topic": f"t{i}", "content": "c", "source": "s", "timestamp": ts_ago(1)}
    for i in range(12)
])
f.compact_knowledge(max_entries=10)
hist = json.load(open(f.HISTORY_FILE))
check("тип compaction", hist[-1]["type"] == "compaction", str(hist[-1]))
check("capped в истории", hist[-1]["capped"] == 2, str(hist[-1]))
check("deduped в истории", "deduped" in hist[-1])

print("== 7. Кулдаун компакции в auto_focus_cycle ==")
make_env()
seed_kb([{"topic": "a", "content": "x", "source": "s", "timestamp": ts_ago(1)}])
# свежая компакция в истории → следующий вызов пропустит
f._log_event({"time": datetime.now().isoformat(), "type": "compaction"})
f.get_context_usage = lambda: (700, 900_000)  # >порога
r = f.auto_focus_cycle()
check("action=compact_cooldown", r["action"] == "compact_cooldown", str(r["action"]))

print("== 8. Без свежей компакции — срабатывает ==")
make_env()
seed_kb([{"topic": "a", "content": "x", "source": "s", "timestamp": ts_ago(1)}])
# старая компакция (50ч назад) → кулдаун прошёл
f._log_event({"time": (datetime.now() - timedelta(hours=50)).isoformat(), "type": "compaction"})
f.get_context_usage = lambda: (700, 900_000)
r = f.auto_focus_cycle()
check("action в (compacted,compact_advised)", r["action"] in ("compacted", "compact_advised"), str(r["action"]))

print("== 9. Кулдаун советов watch ==")
make_env()
seed_kb([{"topic": "a", "content": "x", "source": "s", "timestamp": ts_ago(1)}])
f.get_context_usage = lambda: (500, 550_000)  # >warn, <act
r1 = f.auto_focus_cycle()
check("первый watch", r1["action"] == "watch", str(r1["action"]))
r2 = f.auto_focus_cycle()
check("второй — watch_cooldown", r2["action"] == "watch_cooldown", str(r2["action"]))

print("== 10. Нормальный контекст — none ==")
make_env()
seed_kb([{"topic": "a", "content": "x", "source": "s", "timestamp": ts_ago(1)}])
f.get_context_usage = lambda: (100, 50_000)
r = f.auto_focus_cycle()
check("action=none", r["action"] == "none", str(r["action"]))

print(f"\nИТОГ: {PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
