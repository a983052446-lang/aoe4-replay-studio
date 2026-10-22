# -*- coding: utf-8 -*-
"""Relic 官方录像接口。

`https://aoe-api.worldsedgelink.com/community/leaderboard/getReplayFiles`
带上明确的客户端标识就能查任意对局（不限新旧）的带宽签名下载地址。

要点（来自 AoE4 Replay Launcher 1.1.2 的实现）：
  * 一次最多 10 个 matchID，多了会被拒
  * 返回 {result, expiryUnix, replayFiles:[{profile_id, matchhistory_id, url}]}
  * 同一场有多个参与者各自的副本，优先取自己那份内容最全
  * 429 要指数退避（5s → 60s 封顶），不然会被封一阵
"""
from __future__ import annotations

import time
import urllib.parse
import urllib.request

API = "https://aoe-api.worldsedgelink.com/community/leaderboard/getReplayFiles"
# 明确声明客户端身份，服务器据此放行（伪装浏览器 UA 反而会被拒）
UA = "AoE4ReplayLauncher-ChromeExt/1.0 (https://github.com/spartain-aoe/aoe4world-replay-extension)"

BATCH = 10
STATE = {"blocked_until": 0.0, "streak": 0, "last_error": ""}


def _call(ids: list[int], timeout: int = 40) -> dict:
    wait = STATE["blocked_until"] - time.time()
    if wait > 0:
        time.sleep(min(wait, 8))
    q = f"matchIDs=[{','.join(str(i) for i in ids)}]&title=age4"
    req = urllib.request.Request(f"{API}?{q}", headers={"User-Agent": UA, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            import json

            data = json.loads(r.read().decode("utf-8"))
        STATE["streak"] = 0
        STATE["blocked_until"] = 0.0
        STATE["last_error"] = ""
        return data
    except urllib.error.HTTPError as e:
        if e.code == 429:
            STATE["streak"] += 1
            delay = min(5.0 * (2 ** (STATE["streak"] - 1)), 60.0)
            STATE["blocked_until"] = time.time() + delay
            STATE["last_error"] = f"429，退避 {delay:.0f}s"
        else:
            STATE["last_error"] = f"HTTP {e.code}"
        raise
    except Exception as e:  # noqa: BLE001
        STATE["last_error"] = str(e)
        raise


def _result_ok(data: dict) -> bool:
    """接口的业务状态：result.code 为 0 / "SUCCESS" / 缺省都算成功。"""
    res = data.get("result")
    if not isinstance(res, dict):
        return True
    code = res.get("code")
    if code in (None, 0, "0", "SUCCESS", "success"):
        return True
    return False


def _result_message(data: dict) -> str:
    res = data.get("result")
    if isinstance(res, dict):
        return str(res.get("message") or res.get("code") or "")
    return ""


def files_for(
    ids: list[int],
    prefer_profile: int | None = None,
    with_status: bool = False,
    gap: float = 0.0,
    retries: int = 2,
) -> dict:
    """返回 {game_id: {"url":..., "profile_id":..., "expiry": ...}}。

    **默认仍只返回拿得到地址的 game_id**（老调用方的契约不变）。
    传 with_status=True 时返回三态结构，见 files_for_status()。

    排序策略：自己那份 → 副本数多的 → 文件名哈希大。
    实测同一场的几个副本大小差很多（文件里是各参与者的录像），
    哈希区分不了谁的是谁的，但「副本多」通常意味着这份是完整对局。
    """
    res = files_for_status(ids, prefer_profile=prefer_profile, gap=gap, retries=retries)
    if with_status:
        return res
    return {gid: v["file"] for gid, v in res.items() if v["status"] == "ok"}


def files_for_status(
    ids: list[int],
    prefer_profile: int | None = None,
    gap: float = 0.0,
    retries: int = 2,
) -> dict:
    """三态版：{game_id: {"status": ok|missing|unknown, "file": {...}|None, "error": str}}

    为什么要三态（2026-10-22 实测踩到的坑）：
        旧实现里 `except Exception: continue` 把「这一批请求失败」和
        「服务器确实没这场副本」压成了同一种结果 —— 都是「返回值里没有这个 id」。
        拿 60 场连发 6 批去探测，撞上瞬时错误的批次整批丢失，
        21 场明明有 14~16 个副本的对局被误判成「无副本」。

        所以这里把两种失败拆开：
          * ok      —— 接口正常应答，且有带 url 的副本。可用。
          * missing —— 接口正常应答（整批成功），但这场确实没有任何副本。
          * unknown —— 请求本身失败（超时/429/HTTP 错）或重试后仍失败。
                        **绝不当成 missing**，调用方可以稍后重试。

    gap:    批与批之间的间隔秒数，连续探测大批量时用它避开限流
    retries:单批失败后的重试次数（指数退避由 _call 内部的 429 逻辑负责）
    """
    ids = [int(i) for i in ids]
    out: dict[int, dict] = {}

    for bi, start in enumerate(range(0, len(ids), BATCH)):
        if bi and gap > 0:
            time.sleep(gap)
        chunk = ids[start : start + BATCH]
        data = None
        last_err = ""
        for attempt in range(max(1, retries)):
            try:
                data = _call(chunk)
                last_err = ""
                break
            except Exception as e:  # noqa: BLE001
                last_err = f"{type(e).__name__}: {e}"[:160]
                if attempt + 1 < max(1, retries):
                    time.sleep(1.0 + 1.5 * attempt)

        if data is None:
            # 整批请求失败 → 这 10 个都是「不知道」，不是「没有」
            for gid in chunk:
                out[gid] = {"status": "unknown", "file": None, "error": last_err}
            continue

        # HTTP 200 也可能是业务失败：Relic 把错误塞在 result.code/message 里，
        # 这时候 replayFiles 要么缺失、要么为空 —— 直接按「没有副本」记成 missing
        # 正是这套三态判定要避免的假阴性。
        if not _result_ok(data) or "replayFiles" not in data:
            msg = _result_message(data) or last_err or "接口未返回 replayFiles"
            for gid in chunk:
                out[gid] = {"status": "unknown", "file": None, "error": msg[:160]}
            continue

        expiry = data.get("expiryUnix") or 0
        bucket: dict[int, list[dict]] = {}
        for f in data.get("replayFiles") or []:
            url = f.get("url")
            gid = f.get("matchhistory_id")
            if not url or gid is None:
                continue
            bucket.setdefault(int(gid), []).append(f)

        for gid in chunk:
            rows = bucket.get(gid) or []
            if not rows:
                # 接口正常应答了，但这场确实一份副本都没有
                out[gid] = {"status": "missing", "file": None, "error": ""}
                continue
            mine = [r for r in rows if prefer_profile and r.get("profile_id") == prefer_profile]
            pick = (mine or rows)[0]
            out[gid] = {
                "status": "ok",
                "error": "",
                "file": {
                    "url": pick["url"],
                    "profile_id": pick.get("profile_id"),
                    "copies": len(rows),
                    "expiry": expiry,
                    "has_mine": bool(mine),
                    "alternatives": [r["url"] for r in rows if r is not pick][:8],
                },
            }
    return out


def state() -> dict:
    return {
        "blocked_seconds": round(max(0.0, STATE["blocked_until"] - time.time()), 1),
        "streak": STATE["streak"],
        "last_error": STATE["last_error"],
    }


def filename(url: str) -> str:
    return urllib.parse.urlparse(url).path.rsplit("/", 1)[-1]
