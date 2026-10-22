# -*- coding: utf-8 -*-
"""用内置 Chrome 打开 AoE4World 对局页，抓所有旗帜 img 的 src + 实际渲染尺寸，
确认「灰白布纹」是不是站点本来的样子（还是我们存坏了）。"""
import asyncio
import json
import os
import subprocess
import sys
import time

import requests

sys.path.insert(0, ".")
from aoereplay.actor import ws_connect  # noqa: E402

CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
PROFILE = os.path.abspath("data/shot-profile")
port = 9449
URL = sys.argv[1] if len(sys.argv) > 1 else "https://aoe4world.com/players/PROFILE_ID/games?game=GAME_ID"
proc = subprocess.Popen([CHROME, f"--user-data-dir={PROFILE}", f"--remote-debugging-port={port}",
                         "--headless=new", "--disable-gpu", "--hide-scrollbars",
                         "--window-size=1600,1200", "--no-first-run", "--remote-allow-origins=*",
                         URL, "--lang=zh-CN"])
for _ in range(50):
    try:
        requests.get(f"http://127.0.0.1:{port}/json/version", timeout=1)
        break
    except Exception:
        time.sleep(0.4)

page = [t for t in requests.get(f"http://127.0.0.1:{port}/json/list", timeout=5).json() if t.get("type") == "page"][0]
print("page:", page.get("url"))


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


time.sleep(6)
r = asyncio.run(ev("""JSON.stringify(Array.from(document.querySelectorAll('img')).map(i=>i.src).filter(s=>s.includes('/flags/')))"""))
print("flags on page:", (r.get("result") or {}).get("value"))

r2 = asyncio.run(ev("""JSON.stringify(Array.from(document.querySelectorAll('img')).map(i=>i.src).filter(s=>s.includes('/maps/')).slice(0,3))"""))
print("maps on page:", (r2.get("result") or {}).get("value"))

# 抓一张旗子的实际像素，看看真实颜色
r3 = asyncio.run(ev("""(async () => {
  const imgs = Array.from(document.querySelectorAll('img')).filter(i => i.src.includes('/flags/'));
  const out = [];
  for (const im of imgs) {
    const c = document.createElement('canvas'); c.width = im.naturalWidth || 1; c.height = im.naturalHeight || 1;
    const g = c.getContext('2d'); try { g.drawImage(im, 0, 0); } catch (e) { out.push([im.src, 'ta int']); continue; }
    const d = g.getImageData(0, 0, c.width, c.height).data;
    let r = 0, g2 = 0, b = 0, n = 0;
    for (let i = 0; i < d.length; i += 4 * 37) { r += d[i]; g2 += d[i+1]; b += d[i+2]; n++; }
    out.push([im.src.split('/').pop(), im.naturalWidth, im.naturalHeight, Math.round(r/n), Math.round(g2/n), Math.round(b/n)]);
  }
  return JSON.stringify(out);
})()""", await_promise=True, timeout=60))
print("pixels:", (r3.get("result") or {}).get("value"))

proc.terminate()
