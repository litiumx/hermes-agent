#!/usr/bin/env python3
"""DeepSeek API Usage Proxy — перехватывает ответы и логирует usage для cache_monitor."""
import json, sys, os
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.request import urlopen, Request
from datetime import datetime

DEEPSEEK_API = "https://api.deepseek.com"
LISTEN_PORT = 8123
LOG_FILE = "/root/.hermes/logs/deepseek-usage.jsonl"

class ProxyHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        # Read request
        content_len = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(content_len)
        
        # Forward to DeepSeek
        url = f"{DEEPSEEK_API}{self.path}"
        req = Request(url, data=body, method='POST')
        req.add_header('Content-Type', 'application/json')
        if 'Authorization' in self.headers:
            req.add_header('Authorization', self.headers['Authorization'])
        
        try:
            with urlopen(req, timeout=120) as resp:
                resp_body = resp.read()
                self.send_response(resp.status)
                self.send_header('Content-Type', resp.headers.get('Content-Type', 'application/json'))
                self.end_headers()
                self.wfile.write(resp_body)
                
                # Log usage
                try:
                    data = json.loads(resp_body)
                    usage = data.get("usage", {})
                    if usage:
                        log_entry = {
                            "ts": datetime.now().isoformat(),
                            "model": data.get("model", "?"),
                            "prompt_tokens": usage.get("prompt_tokens", 0),
                            "completion_tokens": usage.get("completion_tokens", 0),
                            "cache_read_tokens": usage.get("prompt_cache_hit_tokens", 0),
                            "cache_miss_tokens": usage.get("prompt_cache_miss_tokens", 0),
                        }
                        os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
                        with open(LOG_FILE, 'a') as f:
                            f.write(json.dumps(log_entry) + '\n')
                except:
                    pass
        except Exception as e:
            self.send_response(502)
            self.end_headers()
            self.wfile.write(json.dumps({"error": str(e)}).encode())
    
    def log_message(self, format, *args):
        pass  # Suppress default logging

if __name__ == "__main__":
    server = HTTPServer(('127.0.0.1', LISTEN_PORT), ProxyHandler)
    print(f"DeepSeek Usage Proxy on 127.0.0.1:{LISTEN_PORT} -> {DEEPSEEK_API}")
    server.serve_forever()
