#!/usr/bin/env python3
"""Cache Monitor v2 — парсит usage из JSONL сессий Hermes."""
import json, os, glob, sys
from datetime import datetime
from collections import defaultdict

LOG_DIR = "/root/.hermes/sessions"
SUMMARY_FILE = "/root/.hermes/DEEPSEEK_CACHE.md"
ALERT_THRESHOLD = 70  # cache hit rate below this = alert

def scan_sessions():
    results = defaultdict(int)
    for d in [LOG_DIR]:
        if not os.path.isdir(d):
            continue
        for f in glob.glob(os.path.join(d, "**/*.jsonl"), recursive=True):
            try:
                with open(f, 'r', errors='ignore') as fh:
                    for line in fh:
                        try:
                            data = json.loads(line)
                            if data.get("type") == "post_call":
                                usage = data.get("usage", {})
                                results["total_calls"] += 1
                                results["prompt_tokens"] += usage.get("prompt_tokens", 0)
                                results["completion_tokens"] += usage.get("completion_tokens", 0)
                                results["cache_read"] += usage.get("cache_read_tokens", 0)
                                results["cache_write"] += usage.get("cache_write_tokens", 0)
                                results["reasoning"] += usage.get("reasoning_tokens", 0)
                                results["input_tokens"] += usage.get("input_tokens", 0)
                                results["output_tokens"] += usage.get("output_tokens", 0)
                        except:
                            pass
            except:
                pass

    # Calculate hit rate
    total_input = results["prompt_tokens"]
    cached = results["cache_read"]
    results["hit_rate"] = (cached / total_input * 100) if total_input > 0 else 0
    results["alert"] = results["hit_rate"] < ALERT_THRESHOLD and total_input > 0
    return results

def generate_report(data):
    now = datetime.now().strftime("%Y-%m-%d %H:%M MSK")
    hit_rate = data["hit_rate"]
    cached = data["cache_read"]
    total_input = data["prompt_tokens"]
    savings = cached * 0.000014  # $0.14/1M tokens

    lines = [
        f"# DeepSeek Cache Report — {now}",
        "",
        f"**API Calls:** {data['total_calls']} | **Hit rate:** {hit_rate:.1f}%",
        f"**Status:** {'✅ Excellent' if hit_rate >= 80 else '⚠️ OK' if hit_rate >= 50 else '🔴 LOW — check prompts'}",
        "",
        f"| Metric | Tokens |",
        f"|--------|--------|",
        f"| Prompt tokens | {total_input:,} |",
        f"| Cache read (hit) | {cached:,} |",
        f"| Cache write | {data['cache_write']:,} |",
        f"| Completion tokens | {data['completion_tokens']:,} |",
        f"| Reasoning tokens | {data['reasoning']:,} |",
        f"| Net input tokens | {data['input_tokens']:,} |",
        f"| Net output tokens | {data['output_tokens']:,} |",
        "",
        f"**Est. savings via cache:** ${savings:,.4f}",
        "",
        "## Optimization Tips",
        "- Keep system prompt static (Layer 0-8 in SOUL.md)",
        "- Minimize changes at conversation start",
        "- Use Flash for cache-friendly simple tasks",
        f"{'⚠️ ALERT: hit rate below {ALERT_THRESHOLD}%! Check SOUL.md structure.' if data['alert'] else ''}",
    ]

    report = "\n".join(lines)
    with open(SUMMARY_FILE, 'w') as f:
        f.write(report)
    return report

if __name__ == "__main__":
    data = scan_sessions()
    report = generate_report(data)
    print(report)
