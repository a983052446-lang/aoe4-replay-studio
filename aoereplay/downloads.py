# -*- coding: utf-8 -*-
"""下载队列（Relic 官方接口版）。

状态机：
    queued → resolving → downloading → done
                     ↘ missing   （服务器上没有这场录像）
                     ↘ error     （可重试）

地址来源是 `relay.py`（getReplayFiles，一次最多 10 个 ID，签名 3 分钟有效），
所以同一批任务会合并成一次查询，减少请求数、避开 429。
支持并发 3 路 + 实时进度回调，前端轮询 /api/queue。
"""
from __future__ import annotations

import gzip
import os
import threading
import time
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

try:  # 可选依赖：没有 requests 就用 urllib 流式下载
    import requests  # type: ignore
except Exception:  # noqa: BLE001
    requests = None  # type: ignore

from . import gz, relay
from .library import PLAYBACK_PREFIX


class ResolveError(RuntimeError):
    def __init__(self, message: str, state: str = "error"):
        super().__init__(message)
        self.state = state


@dataclass
class Job:
    game_id: int
    label: str = ""
    status: str = "queued"
    message: str = ""
    received: int = 0
    total: int = 0
    path: str = ""
    updated: float = field(default_factory=time.time)
    attempts: int = 0
    run: int = 0          # 第几轮；取消/重试都会 +1，旧轮次的任务自己退场，不会写脏文件

    def percent(self) -> float:
        if not self.total:
            return 0.0
        return max(0.0, min(100.0, self.received * 100.0 / self.total))

    def as_dict(self) -> dict:
        return {
            "game_id": self.game_id,
            "label": self.label,
            "status": self.status,
            "message": self.message,
            "received": self.received,
            "total": self.total,
            "percent": round(self.percent(), 1),
            "path": self.path,
            "updated": self.updated,
            "attempts": self.attempts,
        }


class DownloadManager:
    def __init__(
        self,
        dest_dir: Path,
        library_dir: Path,
        profile_id: int | None = None,
        on_done: Callable[[int, Path], None] | None = None,
        workers: int = 3,
    ):
        self.dest_dir = dest_dir
        self.library_dir = library_dir
        self.profile_id = profile_id
        self.on_done = on_done
        self.workers = workers
        self._jobs: dict[int, Job] = {}
        self._lock = threading.Lock()
        self._wake = threading.Condition(self._lock)   # 空闲 worker 在这里等，不再靠「队列空就退出」
        self._queue: list[int] = []
        self._active = 0
        self._threads: list[threading.Thread] = []
        self._stop = threading.Event()
        self._cancel_epoch = 0      # 每次「全部取消」+1；轮次对不上的任务一律作废
        self.dest_dir.mkdir(parents=True, exist_ok=True)
        self.library_dir.mkdir(parents=True, exist_ok=True)

    # ---------- 对外 ----------
    def enqueue(self, items: list[tuple[int, str]], force: bool = False) -> list[dict]:
        """入队。force=True 时连「已下载」的任务也重新抓（界面上的「重新下载」）。"""
        added = []
        with self._lock:
            for gid, label in items:
                job = self._jobs.get(gid)
                if job and job.status in ("queued", "resolving", "downloading"):
                    continue
                if job and job.status == "done" and not force:
                    continue
                job = job or Job(game_id=gid)
                job.label = label or job.label
                job.run += 1              # 作废旧轮次：正在跑的那条就算还活着也不会写这个文件
                job.status = "queued"
                job.message = ""
                job.received = job.total = 0
                job.updated = time.time()
                self._jobs[gid] = job
                self._queue.append(gid)
                added.append(job.as_dict())
        if added:
            self._ensure_workers()
        return added

    def active_ids(self) -> set[int]:
        """还在排/pulling 的 game_id，用来避免重复入队。"""
        with self._lock:
            return {
                gid
                for gid, j in self._jobs.items()
                if j.status in ("queued", "resolving", "downloading")
            }

    def retry(self, game_ids: list[int]) -> None:
        self.enqueue([(gid, "") for gid in game_ids], force=True)

    def snapshot(self) -> dict:
        with self._lock:
            jobs = sorted(self._jobs.values(), key=lambda j: j.updated, reverse=True)
            return {
                "active": self._active,
                "queued": len(self._queue),
                "relay": relay.state(),
                "jobs": [j.as_dict() for j in jobs[:200]],
            }

    def cancel_all(self) -> None:
        """取消：清空队列 + 让在跑的轮次作废。

        老的写法是 set() 完立刻 clear() —— 等于什么都没取消：worker 看到的永远是
        「没停」，批次里已经开下的照样下完、还照样回调 on_done 触发自动播放。
        现在改成给每个 job 的 run +1：worker 每一步都回头确认自己那一轮还是不是当前轮。
        """
        with self._lock:
            self._cancel_epoch += 1
            self._queue.clear()
            for j in self._jobs.values():
                if j.status in ("queued", "resolving", "downloading"):
                    j.run += 1
                    j.status = "error"
                    j.message = "已取消"
                    j.received = j.total = 0
                    j.updated = time.time()

    def probe(self, ids: list[int]) -> dict:
        """只查地址、不下载，给界面标「可下载 / 无副本」。

        返回三态 {gid: {status, file, error}}，见 relay.files_for_status 的说明。
        注意别把 status=unknown 当成 missing —— 那是请求失败，过一会重试就行。
        """
        return relay.files_for_status(ids, prefer_profile=self.profile_id, gap=0.35)

    def probe_ok_ids(self, ids: list[int]) -> tuple[list[int], list[int], list[int]]:
        """探测批量，返回 (可下载, 真无副本, 不知道) 三个 id 列表。"""
        res = self.probe(ids)
        ok, missing, unknown = [], [], []
        for gid in ids:
            st = (res.get(gid) or {}).get("status") or "unknown"
            {"ok": ok, "missing": missing}.get(st, unknown).append(gid)
        return ok, missing, unknown

    # ---------- 内部 ----------
    def _ensure_workers(self) -> None:
        """起够 worker，但**绝不超过** workers 个。

        老代码是 range(max(1, workers - len(threads)))：每次入队至少新起一条线程，
        排几个任务就有几条线程在下载 —— 并发数直接失控，429 是这么招来的。
        """
        self._threads = [t for t in self._threads if t.is_alive()]
        while len(self._threads) < max(1, self.workers):
            with self._lock:
                if len([t for t in self._threads if t.is_alive()]) >= max(1, self.workers):
                    break
                t = threading.Thread(target=self._worker, daemon=True)
                self._threads.append(t)
            t.start()

    def _take_batch(self, timeout: float = 0.5) -> list[Job]:
        """一次取最多 10 个（Relic 接口上限），合并成一次地址查询。

        队列空时在 Condition 上等着，而不是让线程退出 —— 退出窗口里入队的那批
        任务会永远没人处理。
        """
        with self._wake:
            if not self._queue:
                self._wake.wait(timeout)
            batch: list[Job] = []
            while self._queue and len(batch) < relay.BATCH:
                gid = self._queue.pop(0)
                job = self._jobs.get(gid)
                if job and job.status == "queued":
                    job.status = "resolving"
                    job.updated = time.time()
                    batch.append(job)
            self._active += len(batch)
            return batch

    def _worker(self) -> None:
        while not self._stop.is_set():
            batch = self._take_batch()
            if not batch:
                continue
            try:
                self._process(batch)
            except Exception as e:  # noqa: BLE001
                # 一条 worker 挂了不能带走整条队列
                for j in batch:
                    if j.status in ("queued", "resolving", "downloading"):
                        j.status = "error"
                        j.message = f"下载线程异常：{e}"[:200]
                        j.updated = time.time()
            finally:
                with self._lock:
                    self._active = max(0, self._active - len(batch))
                with self._wake:
                    self._wake.notify_all()

    def _alive(self, job: Job, run: int) -> bool:
        """这一步还该不该继续（没被取消、没被新一轮顶掉）。"""
        if self._stop.is_set():
            return False
        with self._lock:
            cur = self._jobs.get(job.game_id)
            return bool(cur is job and job.run == run and job.status != "error")

    def _process(self, batch: list[Job]) -> None:
        runs = {j.game_id: j.run for j in batch}
        ids = [j.game_id for j in batch]
        try:
            resolved = relay.files_for_status(ids, prefer_profile=self.profile_id)
        except Exception as e:  # noqa: BLE001
            for j in batch:
                if not self._alive(j, runs[j.game_id]):
                    continue
                j.status = "error"
                j.message = f"查地址失败：{e}"[:200]
                j.updated = time.time()
            return
        for j in batch:
            if not self._alive(j, runs[j.game_id]):
                continue
            item = resolved.get(j.game_id) or {"status": "unknown", "file": None, "error": "无返回"}
            st = item.get("status")
            if st == "ok":
                try:
                    self._download(j, item["file"], runs[j.game_id])
                except Exception as e:  # noqa: BLE001
                    j.status = "error"
                    j.message = str(e)[:200]
                    j.updated = time.time()
            elif st == "missing":
                # 接口正常应答、确实没有这场 —— 这是「无副本」，不是失败
                j.status = "missing"
                j.message = "服务器没有这场录像"
                j.updated = time.time()
            else:
                # unknown：请求本身失败了。绝不能标 missing（旧版这里的坑），
                # 标 error，让它留在「重试失败」按钮能捞到的范围里
                j.status = "error"
                j.message = f"查地址失败（可重试）：{item.get('error') or '未知错误'}"[:200]
                j.updated = time.time()

    def _download_with_requests(self, job: Job, info: dict, tmp: Path, run: int) -> bool:
        with requests.get(info["url"], headers={"User-Agent": relay.UA}, stream=True, timeout=90) as r:
            if r.status_code != 200:
                job.status = "error"
                job.message = f"HTTP {r.status_code}"
                return False
            job.total = int(r.headers.get("Content-Length") or 0)
            with open(tmp, "wb") as fh:
                for chunk in r.iter_content(262144):
                    if not self._alive(job, run):
                        fh.close()
                        tmp.unlink(missing_ok=True)
                        if job.status != "error":
                            job.status = "error"
                            job.message = "已取消"
                        return False
                    if chunk:
                        fh.write(chunk)
                        job.received += len(chunk)
                        job.updated = time.time()
        return True

    def _download_with_urllib(self, job: Job, info: dict, tmp: Path, run: int) -> bool:
        """没装 requests 时的下载路径（标准库，流式写盘）。"""
        req = urllib.request.Request(info["url"], headers={"User-Agent": relay.UA})
        try:
            with urllib.request.urlopen(req, timeout=90) as r:
                job.total = int(r.headers.get("Content-Length") or 0)
                with open(tmp, "wb") as fh:
                    while True:
                        if not self._alive(job, run):
                            fh.close()
                            tmp.unlink(missing_ok=True)
                            if job.status != "error":
                                job.status = "error"
                                job.message = "已取消"
                            return False
                        chunk = r.read(262144)
                        if not chunk:
                            break
                        fh.write(chunk)
                        job.received += len(chunk)
                        job.updated = time.time()
        except Exception as e:  # noqa: BLE001
            tmp.unlink(missing_ok=True)
            job.status = "error"
            job.message = f"下载失败：{e}"[:200]
            return False
        return True

    def _download(self, job: Job, info: dict, run: int) -> None:
        job.status = "downloading"
        job.message = f"副本 {info.get('copies', 1)} 份" + ("（自己那份）" if info.get("has_mine") else "")
        job.received = 0
        job.updated = time.time()
        # 临时文件名带轮次：同一场被重试时不会两个线程抢写同一个 .part
        tmp = self.dest_dir / f"{PLAYBACK_PREFIX}{job.game_id}.{run}.part"
        ok = (
            self._download_with_requests(job, info, tmp, run)
            if requests is not None
            else self._download_with_urllib(job, info, tmp, run)
        )
        if not ok or job.status == "error":
            tmp.unlink(missing_ok=True)
            return
        try:
            raw = tmp.read_bytes()
        except OSError as e:
            tmp.unlink(missing_ok=True)
            job.status = "error"
            job.message = f"读临时文件失败：{e}"[:120]
            return
        if len(raw) < 4096:
            tmp.unlink(missing_ok=True)
            job.status = "error"
            job.message = f"文件过小（{len(raw)} 字节）"
            return
        # 云端是 gzip(*,,AOE4_RE 包裹*)，只解一层；再往里剥会丢版本头，
        # 引擎会报 Ignoring replay ... version [X] 然后跳过。
        try:
            data = gz.to_playback(raw)
        except Exception as e:  # noqa: BLE001
            tmp.unlink(missing_ok=True)
            job.status = "error"
            job.message = f"解压失败：{e}"[:120]
            return
        if not gz.is_playback_format(data):
            tmp.unlink(missing_ok=True)
            job.status = "error"
            job.message = f"格式不对（头 {data[:8].hex()}）"
            return
        if not self._alive(job, run):
            tmp.unlink(missing_ok=True)
            return
        final = self.library_dir / f"{PLAYBACK_PREFIX}{job.game_id}"
        # 先写同目录临时文件再 os.replace：直接 write_bytes 中途断电/磁盘满会留下
        # 一个截断的 AgeIV_Replay_<id>，下一次扫描会把它当成「已下载」，从此再也修不好
        staging = final.with_suffix(f".{run}.staging")
        try:
            staging.write_bytes(data)
            if staging.stat().st_size != len(data):
                raise OSError("写入长度不符")
            os.replace(staging, final)
        except OSError as e:
            staging.unlink(missing_ok=True)
            tmp.unlink(missing_ok=True)
            job.status = "error"
            job.message = f"入库失败：{e}"[:120]
            return
        tmp.unlink(missing_ok=True)
        job.path = str(final)
        job.status = "done"
        job.message = f"已入库 {len(data) / 1048576:.2f} MB"
        job.received = job.total = len(data)
        job.updated = time.time()
        if self.on_done:
            try:
                self.on_done(job.game_id, final)
            except Exception:  # noqa: BLE001
                pass
