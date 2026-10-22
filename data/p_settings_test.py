# -*- coding: utf-8 -*-
"""验证设置接口新增的三项：steam_id / network_dir / poll_seconds，以及校验提示。"""
import json
import sys
import urllib.request

PORT = sys.argv[1] if len(sys.argv) > 1 else "8788"
BASE = f"http://127.0.0.1:{PORT}"


def post(path, body):
    req = urllib.request.Request(BASE + path, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(req, timeout=180).read())


def state():
    return json.loads(urllib.request.urlopen(BASE + "/api/state", timeout=120).read())["config"]


c0 = state()
print("改前:")
for k in ("steam_id", "network_dir", "poll_seconds", "network_exists", "network_files"):
    print(f"   {k:16} = {c0.get(k)}")

print("\n[1] 非法 Steam ID + 负数轮询")
r = post("/api/settings", {"steam_id": "abc", "poll_seconds": -5})
print("   notes =", r["notes"])
print("   steam_id =", r["config"]["steam_id"], "| poll =", r["config"]["poll_seconds"])

print("\n[2] 合法值：轮询 30，Steam ID 保持")
r = post("/api/settings", {"steam_id": "76561199000000000", "poll_seconds": 30})
print("   notes =", r["notes"])
print("   steam_id =", r["config"]["steam_id"], "| poll =", r["config"]["poll_seconds"])

print("\n[3] 只改 playback → network 应自动跟到同级")
r = post("/api/settings", {"playback_dir": r"F:\FakeGame\playback"})
print("   playback =", r["config"]["playback_dir"])
print("   network  =", r["config"]["network_dir"], "(不存在:", r["config"]["network_exists"], ")")

print("\n[4] 恢复原值")
real = r"{}".format(c0["playback_dir"])
r = post("/api/settings", {"playback_dir": real, "network_dir": c0["network_dir"], "poll_seconds": 60})
print("   playback =", r["config"]["playback_dir"])
print("   network  =", r["config"]["network_dir"], "| files =", r["config"]["network_files"])
print("   poll     =", r["config"]["poll_seconds"])
