# -*- coding: utf-8 -*-
"""把 AoE4World 的地图缩略图缓存到本地。

理由：
  * 页面按 static.aoe4world.com 拉图，脱网或对方改 CDN 就白块
  * 图片名带内容哈希（每个地图不同），只能从页面抓一次
  * 本地化后加载也快，截图/离线都能看到

抓取方式：用内置浏览器打开 games 页，滚动收集所有 /assets/maps/*.png，
下载到 data/mapthumbs/<map_name>.<ext>，并把映射写进 data/maps.json。
"""
from __future__ import annotations

import json
import re
import time
import urllib.request
from pathlib import Path

UA = "AoE4ReplayStudio/1.0 (personal replay library)"


def download_to(url: str, dest: Path, timeout: int = 20) -> bool:
    if dest.exists() and dest.stat().st_size > 500:
        return True
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = r.read()
        if len(data) < 500:
            return False
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        return True
    except Exception:  # noqa: BLE001
        return False


def slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", (name or "").lower()).strip("_")


def cache_all(data_dir: Path, mapping: dict[str, str]) -> dict:
    """下载所有映射到的图，返回 {slug: 本地相对路径}。

    落盘名一律过 slug()：地图名是抓下来的字符串，带空格 / 中文 / 甚至 `../` 都可能，
    直接当文件名会写到 data/mapthumbs 外面去，也会把 URL 搞脏。
    """
    out_dir = data_dir / "mapthumbs"
    out_dir.mkdir(parents=True, exist_ok=True)
    local: dict[str, str] = {}
    ok = 0
    for name, url in sorted(mapping.items()):
        key = slug(name)
        if not key:
            continue
        dest = out_dir / f"{key}.png"
        if download_to(url, dest):
            local[key] = f"/mapthumbs/{key}.png"
            ok += 1
    return {"ok": ok, "total": len(mapping), "local": local}


def load_mapping(data_dir: Path) -> dict:
    p = data_dir / "maps.json"
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def load_local(data_dir: Path) -> dict:
    p = data_dir / "mapthumbs_local.json"
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def save_local(data_dir: Path, local: dict) -> None:
    (data_dir / "mapthumbs_local.json").write_text(
        json.dumps(local, ensure_ascii=False, indent=1), encoding="utf-8"
    )
