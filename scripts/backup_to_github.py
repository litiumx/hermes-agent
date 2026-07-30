#!/usr/bin/env python3
"""Daily backup to GitHub private repo: litiumx/hermes-backup"""
import subprocess, sys, datetime
from pathlib import Path

REPO = "git@github.com-backup:litiumx/hermes-backup.git"
BRANCH = "main"
BACKUP_DIR = Path("/tmp/hermes-backup")

# Источники для бекапа
SOURCES = [
    ("/root/.hermes/SOUL.md", "soul.md"),
    ("/root/.hermes/MEMORY.md", "memory.md"),
    ("/root/.hermes/USER.md", "user.md"),
    ("/root/.hermes/config.yaml", "config.yaml"),
    ("/root/.hermes/.env", ".env"),
    ("/root/.hermes/gateway.env", "gateway.env"),
    ("/root/.hermes/cron/", "cron/"),
    ("/root/.hermes/skills/", "skills/"),
    ("/root/knowledge/", "vault/"),
    ("/root/workspace/AGENTS.md", "workspace/AGENTS.md"),
    ("/root/hermes_merged_research.md", "research.md"),
]

def run(cmd, cwd=None):
    result = subprocess.run(cmd, shell=True, cwd=cwd, capture_output=True, text=True)
    return result.returncode, result.stdout.strip(), result.stderr.strip()

def backup():
    if BACKUP_DIR.exists():
        run(f"rm -rf {BACKUP_DIR}")
    
    BACKUP_DIR.mkdir(parents=True)
    
    # Clone repo
    url = REPO  # SSH: git@github.com-backup:litiumx/hermes-backup.git
    code, _, err = run(f"git clone --depth 1 -b {BRANCH} {url} {BACKUP_DIR}")
    if code != 0:
        code2, _, err2 = run(f"git clone {url} {BACKUP_DIR}")
        if code2 != 0:
            print(f"❌ Clone failed: {err[:200]}")
            return False
    
    # Copy sources
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d_%H:%M")
    snap_dir = BACKUP_DIR / "snapshots" / timestamp
    snap_dir.mkdir(parents=True, exist_ok=True)
    
    for src, dst in SOURCES:
        src_path = Path(src)
        dst_path = snap_dir / dst
        dst_path.parent.mkdir(parents=True, exist_ok=True)
        
        if src_path.is_dir():
            run(f"cp -r {src_path}/* {dst_path}/ 2>/dev/null")
        elif src_path.is_file():
            run(f"cp {src_path} {dst_path} 2>/dev/null")
    
    # Config without secrets (strip tokens)
    env_file = snap_dir / ".env"
    if env_file.exists():
        content = env_file.read_text()
        for line in content.split("\n"):
            if "KEY=" in line or "TOKEN=" in line or "PASSWORD=" in line:
                content = content.replace(line, f"{line.split('=')[0]}=***REDACTED***")
        env_file.write_text(content)
    
    # Git commit and push
    run("git config user.email 'hermes@agencyforge.local'", cwd=BACKUP_DIR)
    run("git config user.name 'Hermes Agent'", cwd=BACKUP_DIR)
    run("git add -A", cwd=BACKUP_DIR)
    
    code, _, _ = run(f"git commit -m 'Backup {timestamp}'", cwd=BACKUP_DIR)
    if code == 0:
        code, out, err = run("git push origin main", cwd=BACKUP_DIR)
        if code == 0:
            print(f"✅ Backup pushed: {timestamp}")
            return True
        else:
            print(f"❌ Push failed: {err[:200]}")
    else:
        print("📭 No changes to commit")
    
    return True

if __name__ == "__main__":
    success = backup()
    sys.exit(0 if success else 1)
