#!/usr/bin/env python3
"""agi_test_mcp_keepalive.py — standalone-тесты для agi_mcp_keepalive.py.

Покрытие: _parse_ts, классификация состояний (ok/degraded/down/crash_loop),
оконная фильтрация, типы сбоев, save/load state, self-test регрессия.
Все тесты изолированы: tempdir-логи, STATE_FILE мокается, реальные файлы не трогаются.
Запуск: python3 agi_test_mcp_keepalive.py
"""

import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import agi_mcp_keepalive as mk


def mk_line(server: str, msg: str, minutes_ago: float) -> str:
    ts = (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)).strftime("%Y-%m-%d %H:%M:%S")
    return f"{ts},123 WARNING tools.mcp_tool: MCP server '{server}' {msg}"


def scan_lines(lines, window_h=24):
    with tempfile.NamedTemporaryFile("w", suffix=".log", delete=False) as f:
        f.write("\n".join(lines))
        tmp = f.name
    try:
        return mk.scan_logs(window_h=window_h, log_path=Path(tmp))
    finally:
        Path(tmp).unlink(missing_ok=True)


class TestParseTs(unittest.TestCase):
    def test_valid_line(self):
        ts = mk._parse_ts("2026-08-08 10:00:00,123 WARNING x")
        self.assertIsNotNone(ts)
        self.assertAlmostEqual(ts, datetime(2026, 8, 8, 10, 0, tzinfo=timezone.utc).timestamp(), delta=1)

    def test_invalid_line(self):
        self.assertIsNone(mk._parse_ts("garbage without timestamp"))
        self.assertIsNone(mk._parse_ts("2026-13-99 99:99:99 bogus"))


class TestClassification(unittest.TestCase):
    def test_ok_single_failure(self):
        res = scan_lines([mk_line("browser", "tool call failed: timeout", 5)])
        self.assertEqual(res["browser"]["state"], "ok")
        self.assertEqual(res["browser"]["count"], 1)

    def test_degraded_no_spike(self):
        # 3 сбоя, но все старше 10 минут → degraded, НЕ crash_loop
        lines = [mk_line("nlm", "keepalive failed, triggering reconnect: ClosedResourceError: ", m)
                 for m in (11, 12, 13)]
        res = scan_lines(lines)
        self.assertEqual(res["nlm"]["state"], "degraded")
        self.assertEqual(res["nlm"]["spike_10min"], 0)

    def test_crash_loop_spike(self):
        # 3 сбоя за 3 минуты → всплеск → crash_loop
        lines = [mk_line("paperclip", "initial connection failed (attempt %d/3)" % i, i) for i in (1, 2, 3)]
        res = scan_lines(lines)
        self.assertEqual(res["paperclip"]["state"], "crash_loop")
        self.assertEqual(res["paperclip"]["spike_10min"], 3)

    def test_down_many_failures(self):
        # 55+ сбоев, но ВСЕ старше 10 минут (вне всплеска) → down, НЕ crash_loop
        lines = [mk_line("atomic", "tool call failed: boom", 11 + i * 0.5) for i in range(mk.MAX_FAILURES + 5)]
        res = scan_lines(lines)
        self.assertEqual(res["atomic"]["state"], "down")
        self.assertEqual(res["atomic"]["count"], mk.MAX_FAILURES + 5)

    def test_window_filtering(self):
        # 2 сбоя 2 часа назад при окне 1ч → сервер не должен появиться
        lines = [mk_line("stale_srv", "initial connection failed", 120),
                 mk_line("stale_srv", "keepalive failed", 121)]
        res = scan_lines(lines, window_h=1)
        self.assertNotIn("stale_srv", res)

    def test_types_counted(self):
        lines = [
            mk_line("mix", "initial connection failed (attempt 1/3)", 5),
            mk_line("mix", "keepalive failed, triggering reconnect", 4),
            mk_line("mix", "tool call failed: timeout", 3),
        ]
        res = scan_lines(lines)
        t = res["mix"]["types"]
        self.assertEqual(t.get("conn", 0), 1)
        self.assertEqual(t.get("keepalive", 0), 1)
        self.assertEqual(t.get("tool", 0), 1)
        self.assertEqual(res["mix"]["count"], 3)

    def test_multiline_same_server_aggregated(self):
        lines = [mk_line("srv", "initial connection failed (attempt 1/3)", 5),
                 mk_line("srv", "initial connection failed (attempt 2/3)", 4),
                 mk_line("srv", "initial connection failed (attempt 3/3)", 3)]
        res = scan_lines(lines)
        self.assertEqual(res["srv"]["count"], 3)


class TestState(unittest.TestCase):
    def test_save_load_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            orig = mk.STATE_FILE
            mk.STATE_FILE = Path(tmp) / "state.json"
            try:
                data = {"updated": "2026-08-08T00:00:00", "servers": {"x": {"state": "down", "count": 55}}}
                mk.save_state(data)
                loaded = mk.load_state()
                self.assertEqual(loaded, data)
                self.assertTrue(mk.STATE_FILE.exists())
            finally:
                mk.STATE_FILE = orig

    def test_load_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            orig = mk.STATE_FILE
            mk.STATE_FILE = Path(tmp) / "nope.json"
            try:
                self.assertEqual(mk.load_state(), {"updated": "", "servers": {}})
            finally:
                mk.STATE_FILE = orig

    def test_load_broken_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            orig = mk.STATE_FILE
            p = Path(tmp) / "state.json"
            p.write_text("{broken")
            mk.STATE_FILE = p
            try:
                self.assertEqual(mk.load_state(), {"updated": "", "servers": {}})
            finally:
                mk.STATE_FILE = orig


class TestSelfTestRegression(unittest.TestCase):
    def test_cmd_self_test_passes(self):
        self.assertEqual(mk.cmd_self_test(), 0)


if __name__ == "__main__":
    suite = unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__])
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)
