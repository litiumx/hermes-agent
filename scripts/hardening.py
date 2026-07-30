#!/usr/bin/env python3
"""Production Hardening Script — финальная проверка и hardening."""
import os, sys, subprocess, json
from pathlib import Path
from datetime import datetime

HERMES_DIR = Path("/root/.hermes")
CHECKS = []

def check(name, ok=True, detail=""):
    CHECKS.append({"name": name, "ok": ok, "detail": detail})
    icon = "✅" if ok else "❌"
    print(f"  {icon} {name}: {detail}")

print(f"🔧 Production Hardening — {datetime.now().strftime('%Y-%m-%d %H:%M MSK')}")
print()

# 1. Gateway status
gw = subprocess.run(["systemctl", "is-active", "hermes-gateway"], capture_output=True, text=True)
check("Gateway active", gw.stdout.strip() == "active", gw.stdout.strip())

# 2. MCP servers
try:
    mcp = subprocess.run(["hermes", "mcp", "list"], capture_output=True, text=True, timeout=15)
    enabled = mcp.stdout.count("enabled")
    check("MCP servers", enabled >= 4, f"{enabled} enabled")
except:
    check("MCP servers", False, "cannot check")

# 3. Docker sandbox
try:
    docker = subprocess.run(["docker", "images", "hermes-sandbox"], capture_output=True, text=True)
    has_image = "hermes-sandbox" in docker.stdout
    check("Docker sandbox image", has_image, "hermes-sandbox:latest" if has_image else "missing")
except:
    check("Docker sandbox image", False, "docker unavailable")

# 4. Skills present
skills_dir = HERMES_DIR / "skills"
required_skills = [
    "plan-pushback-validator",
    "post-execution-self-checker",
    "goal-clarify-analyzer",
    "adversarial-review",
    "effort-control",
]
missing = [s for s in required_skills if not (skills_dir / s).exists()]
check("Opus 4.8 skills", len(missing) == 0, f"missing: {missing}" if missing else f"all {len(required_skills)} present")

# 5. Hooks
hooks_yaml = HERMES_DIR / "hooks.yaml"
has_hooks = hooks_yaml.exists()
check("Hooks config", has_hooks, str(hooks_yaml))

# 6. Cache monitor
cache_file = HERMES_DIR / "DEEPSEEK_CACHE.md"
has_cache = cache_file.exists()
check("Cache report", has_cache, "exists" if has_cache else "missing")

# 7. Budget guard
budget_db = HERMES_DIR / "state" / "budget_state.db"
has_budget = budget_db.exists() and budget_db.stat().st_size > 0
check("Budget DB", has_budget, f"{budget_db.stat().st_size} bytes" if has_budget else "empty")

# 8. Backup
backup = Path("/root/.hermes.backup-20260722-0228")
has_backup = backup.exists()
check("Backup", has_backup, str(backup) if has_backup else "missing")

# 9. Security patterns
sec_patterns = HERMES_DIR / "security_patterns.json"
has_sec = sec_patterns.exists()
check("Security patterns", has_sec, "exists" if has_sec else "missing")

# 10. Mid-conversation injector
injector = HERMES_DIR / "scripts" / "mid_conversation_injector.py"
has_inj = injector.exists()
check("Mid-conv injector", has_inj, "exists" if has_inj else "missing")

print()
total = len(CHECKS)
passed = sum(1 for c in CHECKS if c["ok"])
print(f"Result: {passed}/{total} checks passed")

# Save report
report = {
    "timestamp": datetime.now().isoformat(),
    "total": total,
    "passed": passed,
    "checks": CHECKS,
}
with open(HERMES_DIR / "state" / "hardening_report.json", "w") as f:
    json.dump(report, f, indent=2)
print(f"Report saved to state/hardening_report.json")
