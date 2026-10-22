# -*- coding: utf-8 -*-
"""只监视、不负责拦截：把你「在游戏里点开回放」这件事，变成可回放的动作。

游戏进程的 -replay 参数只在启动瞬间存在，启动器里是拼出来的，
但你手动从游戏菜单点回放时，命令行同样带着它。用 WMI 每 150ms 扫一次
RelicCardinal.exe 的 CommandLine，一旦出现 -replay playback:<名字>，
就记一条「游戏动作」，交给 Recorder 拼进动作序列。

同理，回放文件在 playback 目录里新出现/被改写，也算一条动作。
"""
from __future__ import annotations

import re
import subprocess
import threading
import time
from pathlib import Path

CL = 'Get-CimInstance Win32_Process -Filter "Name=\'RelicCardinal.exe\'" | ' \
     'Select-Object -ExpandProperty CommandLine'


class GameWatcher:
    def __init__(self, on_event, interval: float = 0.4, log=print):
        self.on_event = on_event
        self.interval = interval
        self.log = log
        self.recording = False
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._seen: set[str] = set()
        self.last_replay: str | None = None

    def start(self) -> None:
        self.recording = True
        self._stop.clear()
        self._seen.clear()
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self.recording = False
        self._stop.set()

    def _query(self) -> list[str]:
        try:
            r = subprocess.run(
                ["powershell", "-NoProfile", "-Command", CL],
                capture_output=True,
                text=True,
                errors="replace",
                timeout=6,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            return [ln.strip() for ln in (r.stdout or "").splitlines() if ln.strip()]
        except Exception:  # noqa: BLE001
            return []

    def _loop(self) -> None:
        while not self._stop.is_set():
            if self.recording:
                for cl in self._query():
                    if "-replay" not in cl or cl in self._seen:
                        continue
                    self._seen.add(cl)
                    name = cl.split("-replay", 1)[1].strip().strip('"')
                    self.last_replay = name
                    if self.on_event:
                        self.on_event({"kind": "game", "action": "launch_replay", "arg": name, "cmdline": cl[:300]})
            time.sleep(self.interval)


class NetworkWatcher:
    """监视 network\\ 目录里的 M_<game_id>_<hash> 文件。

    这是「在游戏里点开别人对局回放」时真正落盘的位置：
        第一次点 → 几秒下载 → network\\M_<id>_<64位哈希>
        第二次点 → 秒开，读的就是这个缓存
    文件名里直接带 matchhistory_id，不用抓包也不用读内存。
    """

    M_RE = re.compile(r"^M_(\d+)_([0-9a-fA-F]{64})")

    def __init__(self, network_dir: Path, on_new_id, log=print):
        self.dir = network_dir
        self.on_new_id = on_new_id
        self.log = log
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.recording = False
        self._known: dict[str, float] = {}
        self._seen_ids: set[int] = set()

    def snapshot(self) -> None:
        try:
            self._known = {p.name: p.stat().st_mtime for p in self.dir.iterdir() if p.is_file()}
        except OSError:
            self._known = {}

    def scan_once(self) -> list[int]:
        """立刻扫一遍，返回新发现的 game_id（同时把已有的全部算作已见）。"""
        found: list[int] = []
        try:
            for p in self.dir.iterdir():
                if not p.is_file():
                    continue
                m = self.M_RE.match(p.name)
                if not m:
                    continue
                gid = int(m.group(1))
                fresh_file = p.name not in self._known
                try:
                    self._known[p.name] = p.stat().st_mtime
                except OSError:
                    pass
                if fresh_file and gid not in self._seen_ids:
                    self._seen_ids.add(gid)
                    found.append(gid)
        except OSError:
            pass
        return found

    def start(self) -> None:
        self.snapshot()
        # 基线里已有的 ID 不算“新的”
        for name in self._known:
            m = self.M_RE.match(name)
            if m:
                self._seen_ids.add(int(m.group(1)))
        self.recording = True
        self._stop.clear()
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self.recording = False
        self._stop.set()

    def _loop(self) -> None:
        while not self._stop.is_set():
            if self.recording:
                for gid in self.scan_once():
                    self.log(f"[net] 发现新对局 ID: {gid}")
                    try:
                        self.on_new_id(gid)
                    except Exception as e:  # noqa: BLE001
                        self.log(f"[net] 回调失败: {e}")
            time.sleep(1.0)


class PlaybackWatcher:
    """playback 目录里出现新回放文件 → 记一条动作。

    `AgeIV_Replay_<id>` 这种名字说明游戏从服务器下载过一场（录制名是时间戳），
    所以新出现的这类文件 = 拿到一个真实的 matchhistory_id。
    """

    ID_RE = re.compile(r"^AgeIV_Replay_(\d+)$")

    def __init__(self, playback_dir: Path, on_event, log=print):
        self.dir = playback_dir
        self.on_event = on_event
        self.log = log
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.recording = False
        self._known: dict[str, float] = {}
        self.on_new_id = None       # 回调：callable(game_id) —— 给「自定义对局」自动登记用
        self._seen_ids: set[int] = set()

    def snapshot(self) -> None:
        """开录时把现有文件当基线，避免把历史文件全报一遍。"""
        try:
            self._known = {
                p.name: p.stat().st_mtime
                for p in self.dir.iterdir()
                if p.is_file() and p.name.startswith("AgeIV_Replay_")
            }
        except OSError:
            self._known = {}

    def start(self) -> None:
        self.snapshot()
        self.recording = True
        self._stop.clear()
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self.recording = False
        self._stop.set()

    def _loop(self) -> None:
        while not self._stop.is_set():
            if self.recording:
                try:
                    current = {}
                    for p in self.dir.iterdir():
                        if not p.is_file() or not p.name.startswith("AgeIV_Replay_"):
                            continue
                        st = p.stat()
                        current[p.name] = st.st_mtime
                        if p.name not in self._known:
                            if self.on_event:
                                self.on_event({"kind": "game", "action": "replay_file", "file": p.name, "size": st.st_size})
                            # 新出现的带 ID 的文件 = 游戏刚下过一场
                            m = self.ID_RE.match(p.name)
                            if m:
                                gid = int(m.group(1))
                                if gid not in self._seen_ids:
                                    self._seen_ids.add(gid)
                                    self.log(f"[watch] 发现新回放 ID: {gid}")
                                    if self.on_new_id:
                                        try:
                                            self.on_new_id(gid)
                                        except Exception as e:  # noqa: BLE001
                                            self.log(f"[watch] 回调失败: {e}")
                    self._known = {**self._known, **current}
                except OSError:
                    pass
            time.sleep(1.2)
