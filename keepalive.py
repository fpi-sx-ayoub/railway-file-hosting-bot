"""
Keepalive script - يستدعى تلقائياً من web_panel ليبقى الخدمة نشطة على Render Free plan.
يقوم بـ self-ping كل 10 دقائق على /ping endpoint.
"""
import os
import time
import threading
import requests

def keepalive_loop(url):
    while True:
        try:
            time.sleep(600)  # كل 10 دقائق
            requests.get(url, timeout=15)
            print(f"[KeepAlive] pinged {url}")
        except Exception as e:
            print(f"[KeepAlive] error: {e}")

def start_keepalive():
    url = os.getenv("RENDER_EXTERNAL_URL", "")
    if url:
        ping_url = url.rstrip("/") + "/ping"
        t = threading.Thread(target=keepalive_loop, args=(ping_url,), daemon=True)
        t.start()
        print(f"[KeepAlive] enabled for {ping_url}")
    else:
        print("[KeepAlive] no RENDER_EXTERNAL_URL, skipping")
