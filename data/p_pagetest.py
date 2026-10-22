# -*- coding: utf-8 -*-
"""验证分页：/api/more 追加、/api/state 的 paging 字段、refresh 不回退。"""
import json
import sys
import urllib.request

PORT = sys.argv[1] if len(sys.argv) > 1 else "8788"
BASE = f"http://127.0.0.1:{PORT}"


def post(path, body=None):
    req = urllib.request.Request(BASE + path, data=json.dumps(body or {}).encode(),
                                 headers={"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(req, timeout=120).read())


def get(path):
    return json.loads(urllib.request.urlopen(BASE + path, timeout=120).read())


st = get("/api/state")
print("初始: games=", len(st["games"]), "paging=", st.get("paging"))

r = post("/api/more", {"pages": 1})
print("\n翻 1 页:", {k: r[k] for k in ("added", "total", "total_count", "pages_loaded", "next_page")})

r5 = post("/api/more", {"pages": 5})
print("再翻 5 页:", {k: r5[k] for k in ("added", "total", "total_count", "pages_loaded", "next_page")})

st2 = get("/api/state")
print("现在: games=", len(st2["games"]), "paging=", st2.get("paging"))

print("\n模拟后台轮询 refresh(pages=2)：")
post("/api/refresh")
st3 = get("/api/state")
print("refresh 后: games=", len(st3["games"]), "paging=", st3.get("paging"))

r2 = post("/api/more", {"pages": 1})
print("\nrefresh 之后再翻 1 页:", {k: r2[k] for k in ("added", "total", "pages_loaded", "next_page")})
