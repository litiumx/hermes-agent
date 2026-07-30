#!/usr/bin/env python3
"""
Telegram Channel RSS Parser → топик-агрегатор
Читает RSS-ленты каналов, пересылает новые посты в топик группы.
"""
import json, hashlib, urllib.request, sys, os
from datetime import datetime
from pathlib import Path
import xml.etree.ElementTree as ET

# Конфигурация
CHANNELS = [
    # "https://rsshub.app/telegram/channel/НАЗВАНИЕ_КАНАЛА",
]

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
GROUP_CHAT_ID = os.environ.get("TG_GROUP_CHAT_ID", "")
TOPIC_ID = os.environ.get("TG_AGGREGATOR_TOPIC_ID", "")

STATE_FILE = Path.home() / ".hermes/state/rss_last_ids.json"

def load_state():
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {}

def save_state(state):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2))

def fetch_rss(url):
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Hermes-RSS/1.0"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.read().decode("utf-8")
    except Exception as e:
        print(f"❌ {url}: {e}")
        return None

def parse_rss(xml_data):
    items = []
    try:
        root = ET.fromstring(xml_data)
        for item in root.iter("item"):
            items.append({
                "title": item.findtext("title", ""),
                "link": item.findtext("link", ""),
                "description": item.findtext("description", ""),
                "pub_date": item.findtext("pubDate", item.findtext("published", "")),
            })
    except Exception as e:
        print(f"❌ Parse error: {e}")
    return items

def send_to_telegram(text):
    if not TELEGRAM_TOKEN or not GROUP_CHAT_ID:
        print("⚠️ TELEGRAM_TOKEN or GROUP_CHAT_ID not set")
        return False
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    data = urllib.parse.urlencode({
        "chat_id": GROUP_CHAT_ID,
        "text": text[:4000],
        "parse_mode": "HTML",
        **({"message_thread_id": TOPIC_ID} if TOPIC_ID else {}),
    }).encode()
    try:
        req = urllib.request.Request(url, data=data)
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read()).get("ok", False)
    except Exception as e:
        print(f"❌ Send error: {e}")
        return False

def main():
    state = load_state()
    total = 0
    
    for channel_url in CHANNELS:
        name = channel_url.rsplit("/", 1)[-1]
        xml = fetch_rss(channel_url)
        if not xml:
            continue
        
        items = parse_rss(xml)
        if not items:
            continue
        
        last_id = state.get(name, "")
        
        for item in reversed(items):
            item_id = hashlib.md5((item["link"] or item["title"]).encode()).hexdigest()
            if item_id == last_id:
                continue
            
            text = f"📡 <b>{item['title']}</b>\n\n{item.get('description', '')[:2000]}\n\n🔗 {item.get('link', '')}"
            if send_to_telegram(text):
                state[name] = item_id
                total += 1
                print(f"✅ {name}: {item['title'][:60]}")
        
        save_state(state)
    
    print(f"Отправлено: {total} новых постов")

if __name__ == "__main__":
    main()
