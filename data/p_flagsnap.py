# -*- coding: utf-8 -*-
"""把 AoE4World 页面上渲染出来的每面旗帜截成小图，存到 data/flagcheck/，
再逐张看颜色 —— 判断「灰白布纹」是站点原样还是我们缓存存坏了。"""
import asyncio
import base64
import json
import os
import subprocess
import sys
import time

import requests

sys.path.insert(0, os.path.abspath("."))
from aoereplay.actor import ws_connect  # noqa: E402

CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
PROFILE = os.path.abspath("data/shot-profile")
port = 9451
URL = "https://aoe4world.com/players/PROFILE_ID/games?game=GAME_ID"
OUT = os.path.abspath("data/flagcheck")
os.makedirs(OUT, exist_ok=True)

proc = subprocess.Popen([CHROME, f"--user-data-dir={PROFILE}", f"--remote-debugging-port={port}",
                         "--headless=new", "--disable-gpu", "--hide-scrollbars",
                         "--window-size=1600,1200", "--no-first-run", "--remote-allow-origins=*",
                         URL])
for _ in range(50):
    try:
        requests.get(f"http://127.0.0.1:{port}/json/version", timeout=1)
        break
    except Exception:
        time.sleep(0.4)
time.sleep(6)
page = [t for t in requests.get(f"http://127.0.0.1:{port}/json/list", timeout=5).json() if t.get("type") == "page"][0]


async def cdp(method, params=None, _id=[0]):
    _id[0] += 1
    mid = _id[0]
    async with ws_connect(page["webSocketDebuggerUrl"], max_size=1 << 26, ping_interval=None) as sock:
        await sock.send(json.dumps({"id": mid, "method": method, "params": params or {}}))
        while True:
            m = json.loads(await sock.recv())
            if m.get("id") == mid:
                return m.get("result")


async def ev(expr, await_promise=False, timeout=40):
    async with ws_connect(page["webSocketDebuggerUrl"], max_size=1 << 26, ping_interval=None) as sock:
        await sock.send(json.dumps({"id": 1, "method": "Runtime.evaluate",
                                    "params": {"expression": expr, "awaitPromise": await_promise, "returnByValue": True}}))
        end = time.time() + timeout
        while time.time() < end:
            try:
                m = json.loads(await asyncio.wait_for(sock.recv(), timeout=max(0.5, end - time.time())))
            except asyncio.TimeoutError:
                return {}
            if m.get("id") == 1:
                return m.get("result") or {}
    return {}


r = asyncio.run(ev("""JSON.stringify(Array.from(document.querySelectorAll('img')).filter(i=>i.src.includes('/flags/')).slice(0,8).map(i=>{
  const b = i.getBoundingClientRect();
  return [i.src.split('/flags/')[1], Math.round(b.x), Math.round(b.y), Math.round(b.width), Math.round(b.height)];
}))"""))
info = json.loads((r.get("result") or {}).get("value") or "[]")
print("flag boxes:", info)

for name, x, y, w, h in info:
    if w < 2 or h < 2:
        continue
    shot = asyncio.run(cdp("Page.captureScreenshot", {"format": "png",
                                                      "clip": {"x": x, "y": y, "width": w, "height": h, "scale": 3}}))
    data = (shot or {}).get("data")
    if data:
        fn = os.path.join(OUT, name.split("-")[0] + "_site.png")
        open(fn, "wb").write(base64.b64decode(data))
        print("saved", fn, w, "x", h)

proc.terminate()
