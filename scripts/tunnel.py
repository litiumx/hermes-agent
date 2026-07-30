#!/usr/bin/env python3
"""Simple HTTPS tunnel via serveo.net для артефактов FinForge."""
import subprocess, sys, re

LOCAL_PORT = sys.argv[1] if len(sys.argv) > 1 else "4123"

print(f"Creating tunnel to localhost:{LOCAL_PORT} via serveo.net...")

proc = subprocess.Popen(
    ["ssh", "-o", "StrictHostKeyChecking=no",
     "-o", "ServerAliveInterval=60",
     "-R", f"80:localhost:{LOCAL_PORT}",
     "serveo.net"],
    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
)

while True:
    line = proc.stdout.readline()
    if not line:
        break
    print(line.strip())
    m = re.search(r'https://[\w.-]+\.serveo\.net', line)
    if m:
        print(f"\n✅ HTTP URL: http://{m.group(0).replace('https://','')}")
        print(f"✅ HTTPS URL: {m.group(0)}")
        print("Keep this running. Press Ctrl+C to stop.\n")
        break

try:
    proc.wait()
except KeyboardInterrupt:
    proc.terminate()
    print("\nTunnel closed.")
