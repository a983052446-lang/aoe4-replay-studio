# -*- coding: utf-8 -*-
"""回放投送与启动。

启动命令沿用已经验证过的 path：
    steam.exe -applaunch 1466860 -dev -replay "playback:AgeIV_Replay_<game_id>"

游戏只在启动时读 -replay，运行中无法切回放 —— 所以这里检测到进程在跑
就明确告诉前端「先退游戏」，不偷偷杀进程。
"""
from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path

from . import gz
from .config import GAME_EXE, STEAM_APP_ID
from .library import PLAYBACK_PREFIX, file_fingerprint


def is_game_running() -> bool:
    try:
        r = subprocess.run(
            ["tasklist", "/FI", f"IMAGENAME eq {GAME_EXE}", "/NH"],
            capture_output=True,
            text=True,
            errors="replace",
            timeout=10,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return GAME_EXE.lower() in (r.stdout or "").lower()
    except Exception:  # noqa: BLE001
        return False


def deploy(src: Path, game_id: int, playback_dir: Path, as_cloud_name: bool = False, deploy_name: str | None = None) -> dict:
    """把文件放进 playback 目录。

    默认写 AgeIV_Replay_<game_id>；自定义对局（rogue/*.rec、temp.rec）传 deploy_name
    保留原名 —— 游戏对这类文件同样只看 -replay playback:<名字>。
    """
    playback_dir.mkdir(parents=True, exist_ok=True)
    raw = src.read_bytes()
    if deploy_name:
        dst = playback_dir / deploy_name
        if dst.exists() and dst.stat().st_size == src.stat().st_size:
            return {"name": dst.name, "mode": "named", "note": "已存在，跳过复制"}
        tmp = dst.with_suffix(".copying")
        tmp.write_bytes(raw)
        os.replace(tmp, dst)
        return {"name": dst.name, "mode": "named", "note": "已写入 playback（保留原名）"}
    if as_cloud_name:
        matched, _gid, _h = gz.parse_name(src)
        if matched:
            dst = playback_dir / src.name
            mode = "raw"
        else:
            data = gz.uncompressed(raw)
            digest = __import__("hashlib").sha256(data).hexdigest()
            dst = playback_dir / f"M_{game_id}_{digest}.gz"
            payload = gz.wrap(data, raw) if src.suffix.lower() == ".gz" else data
            tmp = dst.with_suffix(".part")
            tmp.write_bytes(payload)
            os.replace(tmp, dst)
            return {"name": dst.name, "mode": "wrapped", "note": "已还原云同步命名"}
    else:
        dst = playback_dir / f"{PLAYBACK_PREFIX}{game_id}"
        mode = "chunky"
    if dst.exists() and dst.stat().st_size == src.stat().st_size and not as_cloud_name:
        return {"name": dst.name, "mode": mode, "note": "已存在，跳过复制"}
    if as_cloud_name and dst.exists() and dst.resolve() == src.resolve():
        return {"name": dst.name, "mode": "raw", "note": "就是同一个文件"}
    if as_cloud_name:
        shutil.copy2(src, dst)
    else:
        # 保持 playback 认的格式：,,AOE4_RE 包裹（云端 gzip 只解最外层）
        data = gz.to_playback(raw) if not gz.is_playback_format(raw) else raw
        tmp = dst.with_suffix(".copying")
        tmp.write_bytes(data)
        os.replace(tmp, dst)
    return {"name": dst.name, "mode": mode, "note": "已写入 playback"}


def launch(steam_exe: str, replay_name: str, dev_mode: bool = True) -> list[str]:
    if not steam_exe or not os.path.exists(steam_exe):
        raise FileNotFoundError("找不到 steam.exe")
    cmd = [steam_exe, "-applaunch", STEAM_APP_ID]
    if dev_mode:
        cmd.append("-dev")
    cmd += ["-replay", "playback:" + replay_name]
    subprocess.Popen(cmd, close_fds=True)
    return cmd


def play(src: Path, game_id: int, playback_dir: Path, steam_exe: str, deploy_name: str | None = None) -> dict:
    if is_game_running():
        return {
            "ok": False,
            "reason": "game_running",
            "message": "AoE4 正在运行。回放只能在游戏启动时加载，请先退出游戏再点播放。",
        }
    info = deploy(src, game_id, playback_dir, deploy_name=deploy_name)
    cmd = launch(steam_exe, info["name"])
    return {
        "ok": True,
        "deployed": info,
        "command": " ".join(f'"{c}"' if " " in c else c for c in cmd),
        "started_at": time.time(),
    }


def unlink_playback(game_id: int, playback_dir: Path) -> bool:
    target = playback_dir / f"{PLAYBACK_PREFIX}{game_id}"
    try:
        if target.exists() or target.is_symlink():
            target.unlink()
            return True
    except OSError:
        return False
    return False


def fingerprint(path: Path) -> str:
    return file_fingerprint(path)
