#!/usr/bin/env python3
"""agi_test_loop_suggestion.py — suggestion для паттернов зацикливания
(grow point SELF_IMPROVE 18.08: «Suggestion tool_call_loop:<tool> в
_SUGGESTIONS»).

Проблема: predict_risks выдаёт для "tool_call_loop:<tool>" generic
"Проверить соответствующие сервисы." — бесполезно для зацикливания.
Контракт:
- L._suggestion_for(pattern): для "tool_call_loop:<tool>" — специфичная
  рекомендация с TWO-STRIKE RULE и именем тула; для известных паттернов —
  прежний _SUGGESTIONS; для неизвестных — прежний generic fallback.
- predict_risks использует _suggestion_for → loop-риск несёт
  TWO-STRIKE-рекомендацию.
"""
import json, os, sys, tempfile, time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import agi_error_pattern_learner as L

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


print("== 1. _suggestion_for: loop-паттерн → TWO-STRIKE + имя тула ==")
s = L._suggestion_for("tool_call_loop:terminal")
check("содержит TWO-STRIKE", "TWO-STRIKE" in s, s)
check("содержит имя тула", "terminal" in s, s)
check("не generic", "Проверить соответствующие сервисы" not in s, s)

print("== 2. _suggestion_for: известный паттерн → прежний совет ==")
s = L._suggestion_for("gateway_already_running")
check("static совет сохранён", s == L._SUGGESTIONS["gateway_already_running"], s)

print("== 3. _suggestion_for: неизвестный паттерн → generic fallback ==")
s = L._suggestion_for("alien_pattern_xyz")
check("generic", s == "Проверить соответствующие сервисы.", s)

print("== 4. _suggestion_for: loop без тула (tool_call_loop:) — без краха ==")
s = L._suggestion_for("tool_call_loop:")
check("TWO-STRIKE есть", "TWO-STRIKE" in s, s)
check("плейсхолдер '?' вместо пустого тула", "tool call '?'" in s, s)

print("== 5. _suggestion_for: loop с необычным именем тула — без краха ==")
s = L._suggestion_for("tool_call_loop:web_search foo/bar")
check("TWO-STRIKE есть", "TWO-STRIKE" in s, s)
check("имя сохранено", "web_search foo/bar" in s, s)

print("== 6. _suggestion_for: префикс без двоеточия — НЕ loop ==")
s = L._suggestion_for("tool_call_loop")
check("generic fallback", s == "Проверить соответствующие сервисы.", s)

print("== 6b. _suggestion_for: имя тула с {} — без краха (.replace) ==")
s = L._suggestion_for("tool_call_loop:read_{file}")
check("TWO-STRIKE есть", "TWO-STRIKE" in s, s)
check("имя сохранено", "read_{file}" in s, s)

print("== 7. predict_risks: loop-риск несёт TWO-STRIKE совет ==")
d = {"history": [], "streaks": {}, "learned_patterns": [], "last_update": 0}
for i in range(3):
    L.feedback_loop_evidence([{"tool": "web_search", "count": 4}], data=d)
risks = L.predict_risks(d)
hit = [x for x in risks if x.get("pattern") == "tool_call_loop:web_search"]
check("риск есть", len(hit) == 1, str(risks))
check("risk high", hit and hit[0]["risk"] == "high", str(hit))
check("suggestion TWO-STRIKE", hit and "TWO-STRIKE" in hit[0]["suggestion"], str(hit))
check("suggestion с тулом", hit and "web_search" in hit[0]["suggestion"], str(hit))

print("== 8. predict_risks: известный паттерн — совет не сломан ==")
d = {"history": [{"timestamp": time.time(), "patterns": {"gateway_already_running": 1}}] * 3,
     "streaks": {"gateway_already_running": 3},
     "learned_patterns": [], "last_update": 0}
risks = L.predict_risks(d)
hit = [x for x in risks if x.get("pattern") == "gateway_already_running"]
check("риск есть", len(hit) == 1, str(risks))
check("совет из _SUGGESTIONS",
      hit and hit[0]["suggestion"] == L._SUGGESTIONS["gateway_already_running"],
      str(hit))

print("== 9. predict_risks: loop streak < 3 → риска нет ==")
d = {"history": [{"timestamp": time.time(), "patterns": {"tool_call_loop:read_file": 1}}] * 2,
     "streaks": {"tool_call_loop:read_file": 2},
     "learned_patterns": [], "last_update": 0}
risks = L.predict_risks(d)
hit = [x for x in risks if x.get("pattern") == "tool_call_loop:read_file"]
check("нет риска", len(hit) == 0, str(risks))

print("== 10. predict_risks: loop-паттерн с пустым тулом в data — без краха ==")
d = {"history": [{"timestamp": time.time(), "patterns": {"tool_call_loop:": 1}}] * 3,
     "streaks": {"tool_call_loop:": 3},
     "learned_patterns": [], "last_update": 0}
risks = L.predict_risks(d)
hit = [x for x in risks if x.get("pattern") == "tool_call_loop:"]
check("риск есть", len(hit) == 1, str(risks))
check("suggestion TWO-STRIKE", hit and "TWO-STRIKE" in hit[0]["suggestion"], str(hit))

print(f"\nИТОГ: PASS={PASS} FAIL={FAIL}")
sys.exit(1 if FAIL else 0)
