#!/usr/bin/env python3
"""agi_test_loop_feedback.py — интеграция детектора повторов (focus_agent)
с error-pattern learner (grow point SELF_IMPROVE 18.08: «повторы tool calls
→ фидбек в learner — подтверждённый паттерн зацикливания»).

Контракт:
- learner.feedback_loop_evidence(repeats, data=None): каждый повтор
  [{"tool": ...}] усиливает streak "tool_call_loop:<tool>" и пишет журнал
  feedback type="loop". Пусто/мусор — no-op. data=None → грузит/сохраняет
  PATTERNS_FILE модуля (подменяется в тестах на temp).
- focus.report_repeats_to_learner(repeats, learner_name=...): ленивый импорт
  learner, тихий отказ {"error": ...} при недоступности. Вызывается из
  auto_focus_cycle при обнаруженных повторах → result["loop_feedback"].
"""
import json, os, sys, sqlite3, tempfile, time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import agi_error_pattern_learner as L
import agi_focus_agent as F

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


def fresh_learner():
    """Свежий temp PATTERNS_FILE, подменённый в модуле learner."""
    tmp = Path(tempfile.mkdtemp(prefix="agi_loop_"))
    pf = tmp / "patterns.json"
    L.PATTERNS_FILE = pf
    return pf


print("== 1. Создаёт streak tool_call_loop:<tool> ==")
pf = fresh_learner()
rep = [{"tool": "browser_navigate", "args": {"url": "x"}, "count": 4}]
r = L.feedback_loop_evidence(rep)
check("patterns в отчёте", r.get("patterns") == ["tool_call_loop:browser_navigate"], str(r))
check("saved True", r.get("saved") is True, str(r))
data = json.loads(pf.read_text())
check("streak == 1", data["streaks"].get("tool_call_loop:browser_navigate") == 1,
      str(data.get("streaks")))

print("== 2. Аккумулирует при повторном эпизоде ==")
L.feedback_loop_evidence(rep)
data = json.loads(pf.read_text())
check("streak == 2", data["streaks"].get("tool_call_loop:browser_navigate") == 2,
      str(data.get("streaks")))

print("== 3. Несколько тулов → несколько паттернов ==")
pf = fresh_learner()
L.feedback_loop_evidence([{"tool": "a", "count": 3}, {"tool": "b", "count": 5}])
data = json.loads(pf.read_text())
check("два паттерна", set(data["streaks"]) == {"tool_call_loop:a", "tool_call_loop:b"},
      str(data["streaks"]))

print("== 4. Пустой список → no-op ==")
pf = fresh_learner()
r = L.feedback_loop_evidence([])
check("patterns пуст", r.get("patterns") == [], str(r))
check("saved False", r.get("saved") is False, str(r))
check("файл не создан", not pf.exists(), "файл создан зря")

print("== 5. Мусорные записи пропускаются ==")
pf = fresh_learner()
L.feedback_loop_evidence([{"tool": "ok_tool", "count": 3}, {"count": 7},
                          {"tool": 42, "count": 1}, None, "junk"])
data = json.loads(pf.read_text())
check("только валидный тул", set(data["streaks"]) == {"tool_call_loop:ok_tool"},
      str(data["streaks"]))

print("== 6. Журнал feedback type=loop с tool/count ==")
pf = fresh_learner()
L.feedback_loop_evidence([{"tool": "terminal", "count": 6}])
data = json.loads(pf.read_text())
fb = data.get("feedback", [])
check("есть запись loop", any(x.get("type") == "loop" and x.get("tool") == "terminal"
                              and x.get("count") == 6 for x in fb), str(fb))

print("== 7. Кап журнала ==")
pf = fresh_learner()
for i in range(60):
    L.feedback_loop_evidence([{"tool": f"t{i}", "count": 3}])
data = json.loads(pf.read_text())
check("журнал <= 50", len(data["feedback"]) <= 50, str(len(data["feedback"])))

print("== 8. Переданный data не портится без файла ==")
pf = fresh_learner()
d = {"history": [], "streaks": {}, "learned_patterns": [], "last_update": 0}
r = L.feedback_loop_evidence([{"tool": "x", "count": 3}], data=d)
check("streak в переданном data", d["streaks"].get("tool_call_loop:x") == 1, str(d))
check("saved True (temp файл)", r.get("saved") is True, str(r))
check("файл записан", pf.exists(), "нет файла")

print("== 9. focus: report_repeats_to_learner реально доставляет ==")
pf = fresh_learner()
r = F.report_repeats_to_learner([{"tool": "browser_navigate", "count": 4}])
check("patterns доставлены", r.get("patterns") == ["tool_call_loop:browser_navigate"], str(r))
data = json.loads(pf.read_text())
check("learner записал", data["streaks"].get("tool_call_loop:browser_navigate") == 1,
      str(data.get("streaks")))

print("== 10. focus: тихий отказ при недоступном learner ==")
r = F.report_repeats_to_learner([{"tool": "a", "count": 3}],
                                learner_name="agi_no_such_module_xyz")
check("error в отчёте", "error" in r and "learner unavailable" in r["error"], str(r))

print("== 11. focus: нет функции в модуле → error ==")
r = F.report_repeats_to_learner([{"tool": "a", "count": 3}], learner_name="json")
check("error про функцию", "error" in r, str(r))

print("== 12. Integration: auto_focus_cycle отдаёт loop_feedback ==")
tmp = Path(tempfile.mkdtemp(prefix="agi_loop_db_"))
db = tmp / "state.db"
conn = sqlite3.connect(db)
conn.execute("""CREATE TABLE messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT, role TEXT,
    content TEXT, tool_calls TEXT, created_at TEXT, tokens INTEGER DEFAULT 0)""")
now = time.time()
for i in range(4):
    conn.execute(
        "INSERT INTO messages (session_id, role, content, tool_calls, created_at) VALUES (?,?,?,?,?)",
        ("s1", "assistant", "c",
         json.dumps([{"name": "browser_navigate", "arguments": "{}"}]),
         str(now - 100)))
conn.commit()
conn.close()
pf = fresh_learner()
old_db, old_kb, old_hist = F.SESSION_STATE, F.KB_FILE, F.HISTORY_FILE
F.SESSION_STATE, F.KB_FILE, F.HISTORY_FILE = db, tmp / "kb.json", tmp / "hist.json"
try:
    res = F.auto_focus_cycle()
finally:
    F.SESSION_STATE, F.KB_FILE, F.HISTORY_FILE = old_db, old_kb, old_hist
check("repeated_calls найден",
      res.get("repeated_calls") and res["repeated_calls"][0]["tool"] == "browser_navigate",
      str(res.get("repeated_calls")))
check("loop_feedback в результате",
      isinstance(res.get("loop_feedback"), dict) and res["loop_feedback"].get("patterns"),
      str(res.get("loop_feedback")))
data = json.loads(pf.read_text())
check("learner получил паттерн",
      data["streaks"].get("tool_call_loop:browser_navigate", 0) >= 1,
      str(data.get("streaks")))

print("== 13. Полная петля: 3 эпизода → риск в predict_risks ==")
pf = fresh_learner()
d = {"history": [], "streaks": {}, "learned_patterns": [], "last_update": 0}
for i in range(3):
    L.feedback_loop_evidence([{"tool": "web_search", "count": 4}], data=d)
check("streak == 3", d["streaks"].get("tool_call_loop:web_search") == 3, str(d["streaks"]))
risks = L.predict_risks(d)
hit = [x for x in risks if x.get("pattern") == "tool_call_loop:web_search"]
check("паттерн в рисках", len(hit) == 1, str(risks))
check("risk == high (свежий, не falling)", hit and hit[0]["risk"] == "high", str(hit))
check("decay_score > 0", hit and hit[0]["decay_score"] > 0, str(hit))

print(f"\nИТОГ: PASS={PASS} FAIL={FAIL}")
sys.exit(1 if FAIL else 0)
