# -*- coding: utf-8 -*-
"""测 /api/download/recent 的「先探测再入队」：拿小批量（5 场）跑一次，
看 probe 是否真的把「服务器没副本」的那些挑出去。"""
import json
import sys
import time
import urllib.request

PORT = sys.argv[1] if len(sys.argv) > 1 else "8788"
BASE = f"http://127.0.0.1:{PORT}"


def post(path, body=None, timeout=180):
    req = urllib.request.Request(BASE + path, data=json.dumps(body or {}).encode(),
                                 headers={"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(req, timeout=timeout).read())


t0 = time.time()
r = post("/api/download/recent", {"days": 30, "limit": 5, "probe": True})
dt = time.time() - t0
print(f"耗时 {dt:.2f}s")
print("picked     =", r["picked"])
print("available  =", r["available"])
print("unavailable=", r["unavailable"])
print("added      =", [a["game_id"] for a in r["added"]])
print("skipped_queued =", r.get("skipped_queued"))
print("probed     =", r.get("probed"))

st = json.loads(urllib.request.urlopen(BASE + "/api/state", timeout=60).read())
print("\n队列 active=", st["queue"]["active"], "queued=", st["queue"]["queued"])
