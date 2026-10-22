# -*- coding: utf-8 -*-
"""AoE4World 公共数据 API 封装。

只走 /api/v0（Cloudflare 对 www 页面做 JS 质询，API 子路径直连返回 JSON）。
注意：分页是 page 参数，per_page 固定 50；limit 参数不生效。

**没有 requests 也能跑**：所有请求都走这里内部收口的写法，
装了 requests 就用它，没装就退回标准库 urllib。
"""
from __future__ import annotations

import json as _json
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Iterable, Optional

try:  # 可选依赖：没有就退化到 urllib
    import requests  # type: ignore
except Exception:  # noqa: BLE001
    requests = None  # type: ignore

BASE = "https://aoe4world.com/api/v0"
# 声明真实的客户端身份（他们明确反「伪装浏览器 UA」的行为，写清楚 + 低频访问即可）。
UA = "AoE4ReplayStudio/1.0 (personal replay library; +local)"

# 断流/429 的冷却状态，供前端显示
STATE = {"blocked_until": 0.0, "last_error": "", "requests": 0}

if requests is not None:
    _session = requests.Session()
    _session.headers.update({"User-Agent": UA, "Accept": "application/json"})
else:
    _session = None


class _Resp:
    """统一 requests.Response 和 urllib 结果的薄壳。"""

    __slots__ = ("status_code", "text")

    def __init__(self, status_code: int, text: str) -> None:
        self.status_code = status_code
        self.text = text

    def json(self):
        return _json.loads(self.text)


def _http_get(url: str, params: Optional[dict] = None, timeout: int = 30) -> _Resp:
    """GET 一个 JSON 接口。有 requests 用 requests，没有就用 urllib。"""
    if _session is not None:
        r = _session.get(url, params=params or {}, timeout=timeout)
        return _Resp(r.status_code, r.text)

    full = url
    if params:
        full += ("&" if "?" in url else "?") + urllib.parse.urlencode(params)
    req = urllib.request.Request(full, headers={"User-Agent": UA, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return _Resp(resp.status, resp.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        return _Resp(e.code, e.read().decode("utf-8", "replace"))


class ApiError(RuntimeError):
    pass


def _get(path: str, params: Optional[dict] = None, tries: int = 3) -> dict:
    url = BASE + path
    last = ""
    for i in range(tries):
        wait = max(0.0, STATE["blocked_until"] - time.time())
        if wait:
            time.sleep(min(wait, 8))
        try:
            r = _http_get(url, params, timeout=30)
            STATE["requests"] += 1
            if r.status_code == 200:
                STATE["blocked_until"] = 0.0
                STATE["last_error"] = ""
                return r.json()
            last = f"HTTP {r.status_code}"
            if r.status_code == 429:
                # 服务端在防爬，退避 30s 并标记；缓存优先
                STATE["blocked_until"] = time.time() + 30
                STATE["last_error"] = r.text[:160] or "HTTP 429"
                time.sleep(1.5 * (i + 1))
                continue
            if r.status_code in (400, 404):
                break
        except Exception as e:  # noqa: BLE001
            last = str(e)
            time.sleep(0.8 * (i + 1))
    raise ApiError(f"GET {path} failed: {last}")


def throttle_state() -> dict:
    left = max(0.0, STATE["blocked_until"] - time.time())
    return {"blocked_seconds": round(left, 1), "last_error": STATE["last_error"], "requests": STATE["requests"]}


def player(profile_id: int) -> dict:
    return _get(f"/players/{profile_id}")


def games_page(profile_id: int, page: int, leaderboard: str | None = None) -> tuple[list[dict], dict]:
    """拉单独一页（per_page 固定 50），返回 (games, 分页信息)。

    分页信息里有 total_count / next_page，前端「还有多少场」直接用，
    不用自己算 len//50。
    """
    params = {"page": max(1, int(page))}
    if leaderboard:
        params["leaderboard"] = leaderboard
    payload = _get(f"/players/{profile_id}/games", params)
    batch = payload.get("games") or []
    info = {
        "page": int(payload.get("page") or page),
        "per_page": int(payload.get("per_page") or 50),
        "total_count": int(payload.get("total_count") or 0),
        "next_page": payload.get("next_page"),
    }
    return batch, info


def games(profile_id: int, pages: int = 2, leaderboard: str | None = None) -> list[dict]:
    """拉最近 N 页（每页 50 场），按时间倒序返回。"""
    out: list[dict] = []
    for page in range(1, pages + 1):
        params = {"page": page}
        if leaderboard:
            params["leaderboard"] = leaderboard
        payload = _get(f"/players/{profile_id}/games", params)
        batch = payload.get("games") or []
        if not batch:
            break
        out.extend(batch)
        if len(batch) < 50:
            break
    seen, uniq = set(), []
    for g in out:
        gid = g.get("game_id")
        if gid in seen:
            continue
        seen.add(gid)
        uniq.append(g)
    uniq.sort(key=lambda g: g.get("started_at") or "", reverse=True)
    return uniq


def game_detail(game_id: int) -> dict:
    return _get(f"/games/{game_id}")


def replay_probe(game_id: int) -> int:
    """探测录像链接接口返回值：200=可用，404=没有，其它=被拦。

    目前 /api/v0/games/{id}/replay 返回 404 —— 官方未开放录像下载。
    这里保留探测函数，接口一变就能第一时间接上。
    """
    try:
        r = _http_get(f"{BASE}/games/{game_id}/replay", timeout=20)
        return r.status_code
    except Exception:  # noqa: BLE001
        return -1


def search_player(name: str) -> list[dict]:
    payload = _get("/players/search", {"query": name})
    return payload if isinstance(payload, list) else payload.get("players", [])


def flatten_players(game: dict) -> Iterable[dict]:
    for team in game.get("teams") or []:
        for row in team:
            yield row.get("player") or row
