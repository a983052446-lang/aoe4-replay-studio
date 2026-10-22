import sys, json, requests
sys.path.insert(0, ".")
from aoereplay.api import UA

ids = ["166901","167634","207587","207675","314963","72188"]
# 试试 aoe4world 的 mods 接口
for path in ("/api/v0/mods", "/api/v0/mods/{id}"):
    url = "https://aoe4world.com" + (path.format(id=ids[0]) if "{id}" in path else path)
    try:
        r = requests.get(url, headers={"User-Agent": UA, "Accept": "application/json"}, timeout=20)
        print(f"{path} -> HTTP {r.status_code}, {len(r.content)}B")
        if r.status_code == 200:
            print("   ", r.text[:300])
    except Exception as e:
        print(f"{path} -> ERR {e}")
