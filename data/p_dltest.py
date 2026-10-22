# -*- coding: utf-8 -*-
"""验证 /api/download/recent 与 /api/download 的去重行为（不真正下载，只看入队结果）。"""
import json
import sys
import urllib.request

PORT = sys.argv[1] if len(sys.argv) > 1 else "8788"
BASE = f"http://127.0.0.1:{PORT}"


def post(path, body):
    req = urllib.request.Request(BASE + path, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(req, timeout=60).read())


def get(path):
    return json.loads(urllib.request.urlopen(BASE + path, timeout=90).read())


st = get("/api/state")
games = st["games"]
owned = [g["game_id"] for g in games if g.get("owned")]
print("games", len(games), "| owned in list", len(owned), owned[:9])

# 1) 拿一个已下载的 + 一个没下载的
missing = [g["game_id"] for g in games if not g.get("owned")]
print("\n[1] 下载选中：混入 1 个已下载的")
r = post("/api/download", {"game_ids": owned[:1] + missing[:1]})
print("   added=", [a["game_id"] for a in r["added"]], "| skipped_owned=", r["skipped_owned"])

# 2) 再来一次同样的请求，应该全部跳（已在队列）
print("\n[2] 同一请求再点一次（已在队列）")
r2 = post("/api/download", {"game_ids": owned[:1] + missing[:1]})
print("   added=", [a["game_id"] for a in r2["added"]], "| skipped_owned=", r2["skipped_owned"],
      "| skipped_queued=", r2.get("skipped_queued"))

# 3) 最近 7 天缺的，限 3 场
print("\n[3] 最近 7 天缺的，限 3 场")
r3 = post("/api/download/recent", {"days": 7, "limit": 3})
print("   picked=", r3["picked"], "| added=", [a["game_id"] for a in r3["added"]],
      "| skipped_queued=", r3.get("skipped_queued"))

# 4) 边界：days=0 / limit=0
print("\n[4] days=0 limit=0（不限）")
r4 = post("/api/download/recent", {"days": 0, "limit": 0})
print("   picked=", r4["picked"], "| added=", len(r4["added"]))

q = get("/api/queue")
print("\n队列: active=", q["active"], "queued=", q["queued"], "jobs=", len(q["jobs"]))
for j in q["jobs"][:6]:
    print("  ", j["game_id"], j["status"], j.get("percent"))
