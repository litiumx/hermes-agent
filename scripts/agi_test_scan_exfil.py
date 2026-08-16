#!/usr/bin/env python3
"""agi_test_scan_exfil.py — тесты exfil-сканера для proactive_scan (цикл 36).

Проверяет agi_scan_exfil.py:
- scan_exports(): поиск маркеров хонейтокенов в файлах каталога (экспорты
  сессий/email), изоляция tempfile, без сети;
- exfil_block(): CLI-блок для proactive_scan (clean / leak / пустой стор).

Запуск: pytest или напрямую python3 (pytest.main).
"""
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import pytest

import agi_scan_exfil as asx


def _make_store(tmp, markers):
    """Создать стор хонейтокенов с заданными маркерами."""
    p = Path(tmp) / "honey.json"
    recs = [{"marker": m, "planted_at": 1000.0, "note": f"decoy {m}"}
            for m in markers]
    p.write_text(json.dumps({"honeytokens": recs, "planted_total": len(recs)}))
    return str(p)


def test_empty_store_no_files(tmp_path):
    """Пустой стор + пустой каталог → ничего не найдено."""
    store = _make_store(tmp_path, [])
    assert asx.scan_exports([str(tmp_path)], store_path=store) == []


def test_marker_detected(tmp_path):
    """Файл с маркером в тексте → утечка найдена с именем файла и маркером."""
    store = _make_store(tmp_path, ["AGI_HONEY_abcd1234"])
    d = tmp_path / "exports"
    d.mkdir()
    (d / "session.md").write_text("отчёт по сессии, ключ AGI_HONEY_abcd1234 тут")
    res = asx.scan_exports([str(d)], store_path=store)
    assert len(res) == 1
    assert res[0]["file"].endswith("session.md")
    assert "AGI_HONEY_abcd1234" in res[0]["markers"]


def test_clean_files_not_detected(tmp_path):
    """Файлы без маркеров → чисто."""
    store = _make_store(tmp_path, ["AGI_HONEY_abcd1234"])
    d = tmp_path / "exports"
    d.mkdir()
    (d / "a.json").write_text('{"ok": true}')
    (d / "b.txt").write_text("обычный текст")
    assert asx.scan_exports([str(d)], store_path=store) == []


def test_missing_dir_returns_empty(tmp_path):
    """Несуществующий каталог → [] (не падать)."""
    store = _make_store(tmp_path, ["AGI_HONEY_abcd1234"])
    assert asx.scan_exports([str(tmp_path / "nope")], store_path=store) == []


def test_broken_store_returns_empty(tmp_path):
    """Битый JSON стора → [] (safe, как в agi_honeytoken)."""
    p = Path(tmp_path) / "broken.json"
    p.write_text("{not json")
    d = tmp_path / "exports"
    d.mkdir()
    (d / "x.md").write_text("AGI_HONEY_abcd1234")
    assert asx.scan_exports([str(d)], store_path=str(p)) == []


def test_oversize_file_skipped(tmp_path):
    """Файл больше max_size → пропускается (не грузим гиганты в память)."""
    store = _make_store(tmp_path, ["AGI_HONEY_abcd1234"])
    d = tmp_path / "exports"
    d.mkdir()
    big = d / "big.log"
    big.write_text("AGI_HONEY_abcd1234" + "x" * 1000)
    res = asx.scan_exports([str(d)], store_path=store, max_size=100)
    assert res == []


def test_unsupported_extension_skipped(tmp_path):
    """Файлы вне списка расширений (.py, .bin) не сканируются."""
    store = _make_store(tmp_path, ["AGI_HONEY_abcd1234"])
    d = tmp_path / "exports"
    d.mkdir()
    (d / "script.py").write_text("AGI_HONEY_abcd1234")
    (d / "blob.bin").write_text("AGI_HONEY_abcd1234")
    assert asx.scan_exports([str(d)], store_path=store) == []


def test_nested_files_scanned(tmp_path):
    """Рекурсивный обход подкаталогов находит вложенные экспорты."""
    store = _make_store(tmp_path, ["AGI_HONEY_abcd1234"])
    d = tmp_path / "exports"
    sub = d / "2026" / "08"
    sub.mkdir(parents=True)
    (sub / "email.txt").write_text("письмо AGI_HONEY_abcd1234")
    res = asx.scan_exports([str(d)], store_path=store)
    assert len(res) == 1
    assert res[0]["file"].endswith("email.txt")


def test_multiple_markers_one_file(tmp_path):
    """Один файл с двумя маркерами → оба в списке."""
    store = _make_store(tmp_path, ["AGI_HONEY_aaaa1111", "AGI_HONEY_bbbb2222"])
    d = tmp_path / "exports"
    d.mkdir()
    (d / "both.md").write_text("AGI_HONEY_aaaa1111 и AGI_HONEY_bbbb2222")
    res = asx.scan_exports([str(d)], store_path=store)
    assert len(res) == 1
    assert len(res[0]["markers"]) == 2


def test_exfil_block_clean(tmp_path, capsys):
    """Блок: чисто → строка «Exfil: чисто», без исключений."""
    store = _make_store(tmp_path, ["AGI_HONEY_abcd1234"])
    d = tmp_path / "exports"
    d.mkdir()
    (d / "ok.md").write_text("всё хорошо")
    block = asx.exfil_block([str(d)], store_path=store)
    assert "Exfil" in block and "чисто" in block
    assert "LEAK" not in block.upper()


def test_exfil_block_leak(tmp_path):
    """Блок: утечка → имя файла и маркер в блоке, без исключений."""
    store = _make_store(tmp_path, ["AGI_HONEY_abcd1234"])
    d = tmp_path / "exports"
    d.mkdir()
    (d / "leak.json").write_text("AGI_HONEY_abcd1234")
    block = asx.exfil_block([str(d)], store_path=store)
    assert "LEAK" in block.upper()
    assert "leak.json" in block
    assert "AGI_HONEY_abcd1234" in block


def test_exfil_block_empty_store(tmp_path):
    """Блок: стор пуст → подсказка посадить приманки (не молчание)."""
    store = _make_store(tmp_path, [])
    d = tmp_path / "exports"
    d.mkdir()
    (d / "a.md").write_text("текст")
    block = asx.exfil_block([str(d)], store_path=store)
    assert "plant" in block.lower() or "приман" in block.lower()


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
