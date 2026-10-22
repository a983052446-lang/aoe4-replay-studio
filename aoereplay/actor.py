# -*- coding: utf-8 -*-
"""用独立浏览器 profile 抓「带签名的实时录像链接」。

为什么需要它
------------
游戏内回放列表来自服务器，playback 目录里只有「曾经下载过」的那些。
CloudFileMetadataCache 里的裸 blob URL 现在直接请求是 409
（storage account 关了匿名读取），必须拿到带 SAS 的临时地址才能下。

做法
----
单独开一个 Chrome/Edge 用户配置（data/browser-profile，不碰你日常那个），
登录一次 Xbox/Steam 后一直复用。它做两件事：

1. 会话抓包：监听所有网络响应，凡是 .gz / blob.core.windows.net / cloudfiles 的
   地址都记下来，按 game_id 归到台账（data/url-ledger.json）。
2. 触发下载：用 CDP 在当前页执行
       fetch(cloudfileMetadataUrl, {headers:{Authorization:'XBL3.0 x=...'}})
   或在游戏里把某场回放点开一次，让游戏自己去换签名地址。
   换出来的地址被台账接住，队列随即就能下载。

CDP 走标准 --remote-debugging-port，不依赖第三方库；websockets 库缺失时
退化为「只记录、不主动执行」，功能仍然可用。
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

try:  # 可选依赖：只影响操作录制，核心不受影响
    import requests  # type: ignore
except Exception:  # noqa: BLE001
    requests = None  # type: ignore

try:  # 可选依赖
    import websockets  # type: ignore
    from websockets.asyncio.client import connect as ws_connect  # type: ignore
except Exception:  # noqa: BLE001
    websockets = None
    ws_connect = None

from .config import browser_supports_cdp
from .downloads import ResolveError


class _CDPResp:
    """requests.Response 的最小替身，只有 code / json()。"""

    __slots__ = ("status_code", "text")

    def __init__(self, status_code: int, text: str) -> None:
        self.status_code = status_code
        self.text = text

    def json(self):
        import json as _j

        return _j.loads(self.text)


def _cdp_http(method: str, url: str, timeout: float = 5) -> _CDPResp:
    """访问本机 CDP 的 HTTP 端口。有 requests 用它，没有就用 urllib。"""
    if requests is not None:
        r = requests.request(method, url, timeout=timeout)
        return _CDPResp(r.status_code, r.text)
    req = urllib.request.Request(url, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return _CDPResp(resp.status, resp.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        return _CDPResp(e.code, e.read().decode("utf-8", "replace"))


URL_HINTS = (".gz", "blob.core.windows.net", "cloudfiles", "aoelive", "replay")
PORT_CANDIDATES = (9333, 9334, 9335)


class UrlLedger:
    """game_id -> 已见过的候选下载地址（内存 + 落盘）。"""

    def __init__(self, path: Path):
        self.path = path
        self._data: dict[str, list[dict]] = {}
        self._lock = threading.Lock()
        if path.exists():
            try:
                self._data = json.loads(path.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                self._data = {}

    def add(self, url: str, headers: dict | None = None, source: str = "session") -> int | None:
        gid = game_id_from_url(url)
        if gid is None:
            return None
        entry = {"url": url, "headers": headers or {}, "source": source, "ts": time.time()}
        with self._lock:
            bucket = self._data.setdefault(str(gid), [])
            if any(e["url"] == url for e in bucket):
                return gid
            bucket.insert(0, entry)
            self._data[str(gid)] = bucket[:12]
            self._flush()
        return gid

    def get(self, game_id: int) -> list[dict]:
        with self._lock:
            return list(self._data.get(str(game_id), []))

    def stats(self) -> dict:
        with self._lock:
            return {"games": len(self._data), "urls": sum(len(v) for v in self._data.values())}

    def _flush(self) -> None:
        try:
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self._data, ensure_ascii=False, indent=1), encoding="utf-8")
            os.replace(tmp, self.path)
        except OSError:
            pass


def game_id_from_url(url: str) -> int | None:
    import re

    m = re.search(r"/M_(\d+)_[0-9a-fA-F]{64}\.gz", url)
    if m:
        return int(m.group(1))
    return None


class BrowserActor:
    def __init__(self, cfg, ledger: UrlLedger, log=print):
        self.cfg = cfg
        self.ledger = ledger
        self.log = log
        self.port: int | None = None
        self.proc: subprocess.Popen | None = None
        self._task: asyncio.Task | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._stop = False

    # ---------- 进程 ----------
    def running(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def start(self, start_url: str | None = None) -> dict:
        if self.running():
            return {"ok": True, "already": True, "port": self.port}
        exe = self.cfg.actor_browser
        if not exe or not os.path.exists(exe):
            return {
                "ok": False,
                "message": "没找到 Chromium 系浏览器（Chrome / Edge / Brave），"
                           "请在设置里填「面板浏览器」的路径",
            }
        if not browser_supports_cdp(exe):
            return {
                "ok": False,
                "message": f"{os.path.basename(exe)} 不支持 CDP（--remote-debugging-port）。"
                           "浏览器面板和录制只能用 Chrome / Edge / Brave 这类 Chromium 内核浏览器。",
            }
        self.cfg.profile.mkdir(parents=True, exist_ok=True)
        self.port = _free_port()
        args = [
            exe,
            f"--user-data-dir={self.cfg.profile}",
            f"--remote-debugging-port={self.port}",
            "--remote-allow-origins=*",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-features=CalculateNativeWinOcclusion",
            "--window-size=1440,900",
            start_url or "about:blank",
        ]
        self.proc = subprocess.Popen(args, close_fds=True)
        deadline = time.time() + 20
        ok = False
        while time.time() < deadline:
            try:
                r = _cdp_http("GET", f"http://127.0.0.1:{self.port}/json/version", 2)
                if r.status_code == 200:
                    ok = True
                    break
            except Exception:  # noqa: BLE001
                pass
            # 进程已经退了就别干等 20 秒：最常见的原因是上一个 Chrome 还占着同一个
            # profile 目录，新进程直接把命令转给它然后自己退出。
            if self.proc.poll() is not None:
                self.proc = None
                return {"ok": False, "message": "浏览器启动后立刻退出（多半是同一份浏览器配置已被另一个窗口占用）"}
            time.sleep(0.35)
        if not ok:
            # 超时也必须收拾干净：留着进程的话 running() 仍然是 True，
            # 下一次 start() 会直接返回「已经在跑了」，而端口其实是死的。
            self._kill_proc()
            return {"ok": False, "message": "浏览器起来了但调试端口没开（已结束该进程，可重试）", "port": self.port}
        self._stop = False
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()
        return {"ok": True, "port": self.port, "profile": str(self.cfg.profile)}

    def _kill_proc(self) -> None:
        try:
            if self.proc and self.proc.poll() is None:
                subprocess.run(
                    ["taskkill", "/PID", str(self.proc.pid), "/T", "/F"],
                    capture_output=True,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
                try:
                    self.proc.wait(timeout=5)
                except Exception:  # noqa: BLE001
                    pass
        except Exception:  # noqa: BLE001
            pass
        self.proc = None

    def stop(self) -> None:
        self._stop = True
        self._kill_proc()

    # ---------- CDP ----------
    def _run_loop(self) -> None:
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._session_loop())
        except Exception as e:  # noqa: BLE001
            self.log(f"[actor] session loop 结束: {e}")
        finally:
            self._loop.close()

    async def _session_loop(self) -> None:
        if ws_connect is None:
            self.log("[actor] 缺 websockets 库：只做被动记录，无法主动执行抓链接脚本。")
            while not self._stop:
                await asyncio.sleep(2)
            return
        seen_targets: set[str] = set()
        while not self._stop:
            try:
                targets = _cdp_http("GET", f"http://127.0.0.1:{self.port}/json/list", 3).json()
            except Exception:  # noqa: BLE001
                await asyncio.sleep(1.5)
                continue
            for t in targets:
                if t.get("type") != "page":
                    continue
                ws = t.get("webSocketDebuggerUrl")
                if not ws or ws in seen_targets:
                    continue
                seen_targets.add(ws)
                asyncio.create_task(self._attach(ws))
            await asyncio.sleep(1.5)

    async def _attach(self, ws_url: str) -> None:
        try:
            async with ws_connect(ws_url, max_size=32 * 1024 * 1024, ping_interval=None) as sock:
                await sock.send(json.dumps({"id": 1, "method": "Network.enable"}))
                await sock.send(json.dumps({"id": 2, "method": "Page.enable"}))
                async for raw in sock:
                    if self._stop:
                        return
                    try:
                        msg = json.loads(raw)
                    except Exception:  # noqa: BLE001
                        continue
                    method = msg.get("method")
                    if method == "Network.requestWillBeSent":
                        self._peek(msg.get("params", {}).get("request", {}))
                    elif method == "Network.responseReceived":
                        self._peek(msg.get("params", {}).get("response", {}))
        except Exception as e:  # noqa: BLE001
            self.log(f"[actor] attach 断开: {e}")

    def _peek(self, req: dict) -> None:
        url = req.get("url") or ""
        if not url or not any(h in url.lower() for h in URL_HINTS):
            return
        headers = req.get("headers") or {}
        keep = {
            k: v
            for k, v in headers.items()
            if k.lower() in ("authorization", "x-authorization", "xbl3.0", "cookie", "referer")
        }
        gid = self.ledger.add(url, keep, source="session")
        if gid:
            self.log(f"[actor] 捕获 {gid} 的地址（{len(url)} 字符）")

    async def _eval_on_any_page(self, expression: str, timeout: float = 30) -> dict:
        if ws_connect is None:
            raise ResolveError("缺少 websockets 库，无法主动执行页面脚本", state="needs_login")
        targets = _cdp_http("GET", f"http://127.0.0.1:{self.port}/json/list", 5).json()
        pages = [t for t in targets if t.get("type") == "page" and t.get("webSocketDebuggerUrl")]
        if not pages:
            raise ResolveError("浏览器里没有打开的页面", state="needs_login")
        # 挑 aoe4world 的页面，别闭眼用 pages[0]：targets 的顺序由浏览器给，
        # 很可能是 about:blank 或别的标签排在前面 —— 在那里取登录态必然 401。
        pages.sort(key=lambda t: 0 if "aoe4world.com" in (t.get("url") or "") else 1)
        async with ws_connect(pages[0]["webSocketDebuggerUrl"], max_size=1 << 26, ping_interval=None) as sock:
            await sock.send(
                json.dumps(
                    {
                        "id": 1,
                        "method": "Runtime.evaluate",
                        "params": {"expression": expression, "awaitPromise": True, "returnByValue": True},
                    }
                )
            )
            end = time.time() + timeout
            while time.time() < end:
                raw = await asyncio.wait_for(sock.recv(), timeout=max(1.0, end - time.time()))
                msg = json.loads(raw)
                if msg.get("id") == 1:
                    res = msg.get("result", {}).get("result", {})
                    return {"value": res.get("value"), "error": msg.get("result", {}).get("exceptionDetails")}
        raise ResolveError("页面脚本超时", state="error")

    # ---------- 对外 ----------
    def record_manual(self, url: str, headers: dict | None = None) -> int:
        gid = self.ledger.add(url, headers or {}, source="manual")
        if gid is None:
            raise ResolveError("这个地址里没有 M_<game_id>_<hash>.gz，认不出是哪一场")
        return gid

    def open_page(self, url: str) -> None:
        """在面板里开新标签；面板没起来就交给系统默认浏览器（不需要 CDP）。"""
        try:
            _cdp_http("PUT", f"http://127.0.0.1:{self.port}/json/new?{url}", 5)
        except Exception:  # noqa: BLE001
            subprocess.Popen([self.cfg.ui_browser or self.cfg.actor_browser, url])

    def resolve(self, game_id: int) -> dict:
        hits = self.ledger.get(game_id)
        for h in hits:
            entry = {"url": h["url"], "headers": h.get("headers") or {}, "source": h.get("source")}
            if h.get("source") == "session":
                return entry
        raise ResolveError(
            f"{game_id} 还没抓到带签名的下载地址：先在浏览器面板登录并打开这场回放，或粘贴地址。",
            state="needs_login",
        )


def _free_port() -> int:
    import socket

    for p in PORT_CANDIDATES:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", p))
                return p
            except OSError:
                continue
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]
