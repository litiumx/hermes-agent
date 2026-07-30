#!/usr/bin/env python3
"""Запускает Cloudflare Tunnel, сохраняет URL в /root/.hermes/tunnel_url.txt"""
import subprocess, re, time, sys, os

LOG = "/root/.hermes/logs/tunnel.log"
URL_FILE = "/root/.hermes/tunnel_url.txt"

# Убиваем старые туннели
subprocess.run(["pkill", "-f", "cloudflared tunnel"], capture_output=True)
time.sleep(2)

proc = subprocess.Popen(
    ["cloudflared", "tunnel", "--url", "http://localhost:4124"],
    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
)

url = None
for line in proc.stdout:
    with open(LOG, "a") as f:
        f.write(line + "\n")
    m = re.search(r'https://[a-z-]+\.trycloudflare\.com', line)
    if m:
        url = m.group(0)
        with open(URL_FILE, "w") as f:
            f.write(url)
        print(f"URL: {url}", flush=True)

proc.wait()
