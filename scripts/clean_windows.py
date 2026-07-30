#!/usr/bin/env python3
"""Clean Windows PC via SSH — npm cache + broken npx caches + disk check."""
import subprocess, sys

SSH_KEY = "/root/.ssh/tailscale_windows"
WINDOWS = "aleksey@100.105.159.88"

def run_ps(cmd):
    """Run PowerShell command on Windows via SSH."""
    ssh_cmd = [
        "ssh", "-i", SSH_KEY,
        "-o", "ConnectTimeout=15",
        "-o", "StrictHostKeyChecking=no",
        WINDOWS,
        f"powershell -NoProfile -Command \"{cmd}\""
    ]
    r = subprocess.run(ssh_cmd, capture_output=True, text=True, timeout=60)
    return r.stdout.strip(), r.stderr.strip(), r.returncode

def check_disk():
    out, err, rc = run_ps("Get-PSDrive C | Select-Object @{N='UsedGB';E={[math]::Round($_.Used/1GB,1)}}, @{N='FreeGB';E={[math]::Round($_.Free/1GB,1)}}, @{N='TotalGB';E={[math]::Round(($_.Used+$_.Free)/1GB,1)}} | Format-List")
    print(out)

def clean_npm():
    print("[1/4] Cleaning npm cache...")
    out, err, rc = run_ps("npm cache clean --force")
    print(out or err or "done")

def clean_npx():
    print("[2/4] Removing broken npx caches...")
    out, err, rc = run_ps("if (Test-Path $env:LOCALAPPDATA\\npm-cache\\_npx) { Remove-Item -Recurse -Force $env:LOCALAPPDATA\\npm-cache\\_npx; Write-Output 'npx cache removed' } else { Write-Output 'npx cache already clean' }")
    print(out or err or "done")

def clean_npm_logs():
    print("[3/4] Cleaning npm logs...")
    out, err, rc = run_ps("if (Test-Path $env:LOCALAPPDATA\\npm-cache\\_logs) { Remove-Item -Recurse -Force $env:LOCALAPPDATA\\npm-cache\\_logs; Write-Output 'npm logs removed' } else { Write-Output 'npm logs already clean' }")
    print(out or err or "done")

def clean_temp():
    print("[4/4] Cleaning Temp...")
    out, err, rc = run_ps("if (Test-Path $env:TEMP\\npm-*) { Remove-Item -Recurse -Force $env:TEMP\\npm-*; Write-Output 'npm temp removed' } else { Write-Output 'no npm temp found' }")
    print(out or err or "done")

def main():
    print("=== Windows Cleanup ===")
    print()
    print("BEFORE:")
    check_disk()
    print()
    clean_npm()
    clean_npx()
    clean_npm_logs()
    clean_temp()
    print()
    print("AFTER:")
    check_disk()
    print()
    print("=== Done ===")

if __name__ == "__main__":
    main()
