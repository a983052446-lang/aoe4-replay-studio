# -*- coding: utf-8 -*-
"""回放：把录制下来的动作，在同一个浏览器/游戏里重新演一遍。

三种模式
--------
script  真实派发 CDP 输入事件（鼠标移动/按下/抬起、按键、输入框赋值、滚动），
        页面上看到的和真人操作一致，能触发真实的下载/跳转。

visual  只做「拟人演示」：鼠标指针跟过去、点击涟漪、动作说明气泡。
        不触发真实点击 —— 拿来做教程、解释「我刚才做了什么」，或者给不可信的
        动作做预演（先看一眼会发生什么，再决定是否真跑）。

both    两条一起跑：画面里有真人示范，真实操作也在发生。

游戏侧的动作（回放启动）不是浏览器事件，由 replay_watch 从进程命令行里读，
见 aoereplay/replay_watch.py。
"""
from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

from .actor import ws_connect
from .recorder import VISUAL_JS


class Mimic:
    def __init__(self, port_supplier, log=print):
        self._port = port_supplier
        self.log = log
        self.playing = False
        self._stop = False
        self._thread = None
        self.cursor = 0
        self.total = 0
        self.mode = "script"
        self.last_caption = ""

    # ---------- 控制 ----------
    def play(self, script: dict, mode: str = "script", speed: float = 1.0) -> dict:
        if self.playing:
            return {"ok": False, "message": "已经在回放了"}
        port = self._port()
        if not port:
            return {"ok": False, "message": "浏览器面板没启动"}
        self.playing = True
        self._stop = False
        self.mode = mode
        self.cursor = 0
        self.total = len(script.get("actions") or [])
        self._thread = __import__("threading").Thread(
            target=self._run, args=(port, script, mode, max(0.2, speed)), daemon=True
        )
        self._thread.start()
        return {"ok": True, "total": self.total, "mode": mode}

    def stop(self) -> dict:
        self._stop = True
        return {"ok": True}

    def state(self) -> dict:
        return {
            "playing": self.playing,
            "mode": self.mode,
            "cursor": self.cursor,
            "total": self.total,
            "percent": round(self.cursor * 100 / self.total, 1) if self.total else 0,
            "caption": self.last_caption,
        }

    # ---------- 执行 ----------
    def _run(self, port: int, script: dict, mode: str, speed: float) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self._play(port, script, mode, speed))
        except Exception as e:  # noqa: BLE001
            self.log(f"[mimic] 中断: {e}")
        finally:
            self.playing = False
            loop.close()

    async def _play(self, port: int, script: dict, mode: str, speed: float) -> None:
        if ws_connect is None:
            self.log("[mimic] 缺 websockets 库")
            return
        import urllib.request

        targets = json.loads(urllib.request.urlopen(f"http://127.0.0.1:{port}/json/list", timeout=5).read())
        pages = [t for t in targets if t.get("type") == "page" and "devtools" not in (t.get("url") or "")]
        if not pages:
            self.log("[mimic] 没有可用页面")
            return
        vp = script.get("viewport") or {}
        vw, vh = vp.get("w") or 0, vp.get("h") or 0
        if not (vw and vh):
            # 浏览器扩展导出的脚本没有顶层 viewport，只有每条动作上的。
            # 拿不到就用第一条带视口的动作兜底 —— 否则 vw/vh=0 → sx=sy=1，
            # 窗口尺寸一变，每次点击都落在错误的位置。
            for a in script.get("actions") or []:
                av = a.get("viewport") if isinstance(a, dict) else None
                if isinstance(av, dict) and av.get("w") and av.get("h"):
                    vw, vh = av["w"], av["h"]
                    break
        async with ws_connect(pages[0]["webSocketDebuggerUrl"], max_size=1 << 26, ping_interval=None) as sock:
            do_visual = mode in ("visual", "both")
            if do_visual:
                await self._call(sock, 900, "Runtime.evaluate", {"expression": VISUAL_JS, "returnByValue": True})
            await self._call(sock, 901, "Runtime.evaluate", {"expression": "JSON.stringify([innerWidth,innerHeight])", "returnByValue": True})
            box = await self._recv_value(sock, 901)
            try:
                w, h = json.loads(box) if box else (vw or 1600, vh or 900)
            except Exception:  # noqa: BLE001
                w, h = vw or 1600, vh or 900
            sx = (w / vw) if vw else 1.0
            sy = (h / vh) if vh else 1.0

            for i, a in enumerate(script.get("actions") or []):
                if self._stop:
                    break
                self.cursor = i + 1
                await asyncio.sleep(min(3.0, (a.get("delay") or 0) / 1000.0 / speed))
                await self._one(sock, a, sx, sy, mode)
            if do_visual:
                await self._call(sock, 999, "Runtime.evaluate", {"expression": "window.__lcsMimic && window.__lcsMimic.done()", "returnByValue": True})
        self.log("[mimic] 回放结束")

    async def _one(self, sock, a: dict, sx: float, sy: float, mode: str) -> None:
        kind = a.get("kind")
        if kind in ("click", "wheel"):
            # 指针坐标要按视口缩放
            x, y = a.get("x", 0) * sx, a.get("y", 0) * sy
        elif kind == "scroll":
            # 滚动位置是文档坐标，不随视口缩放（缩放只会让页面滚过头）
            x, y = a.get("x", 0), a.get("y", 0)
        else:
            x = y = None
        self.last_caption = self._caption(a)

        if mode in ("visual", "both") and kind != "input":
            await self._call(
                sock, 910, "Runtime.evaluate",
                {
                    "expression": f"window.__lcsMimic && window.__lcsMimic.move({x or 0},{y or 0});"
                    f"window.__lcsMimic && window.__lcsMimic.caption({json.dumps(self.last_caption, ensure_ascii=False)},{x or 0},{y or 0})",
                    "returnByValue": True,
                },
            )

        if mode in ("script", "both"):
            if kind == "click":
                btn = "right" if a.get("button") == 2 else ("middle" if a.get("button") == 1 else "left")
                await self._call(sock, 920, "Input.dispatchMouseEvent", {"type": "mouseMoved", "x": x, "y": y, "button": "none"})
                await self._call(sock, 921, "Input.dispatchMouseEvent", {"type": "mousePressed", "x": x, "y": y, "button": btn, "clickCount": 1})
                await self._call(sock, 922, "Input.dispatchMouseEvent", {"type": "mouseReleased", "x": x, "y": y, "button": btn, "clickCount": 1})
            elif kind == "key":
                mods = (8 if a.get("alt") else 0) | (2 if a.get("ctrl") else 0) | (1 if a.get("shift") else 0) | (4 if a.get("meta") else 0)
                params = {"type": "keyDown", "key": a.get("key") or "", "code": a.get("code") or "", "modifiers": mods}
                await self._call(sock, 930, "Input.dispatchKeyEvent", params)
                await self._call(
                    sock, 931, "Input.dispatchKeyEvent",
                    {"type": "keyUp", "key": a.get("key") or "", "code": a.get("code") or "", "modifiers": mods},
                )
            elif kind == "input":
                sel = (a.get("selector") or "").replace("'", "\\'")
                await self._call(
                    sock, 940, "Runtime.evaluate",
                    {
                        "expression": f"(()=>{{const e=document.querySelector('{sel}');"
                        f"if(!e)return 'no-el';e.focus();e.value={json.dumps(a.get('value') or '')};"
                        f"e.dispatchEvent(new Event('input',{{bubbles:true}}));return 'ok';}})()",
                        "returnByValue": True,
                    },
                )
            elif kind == "scroll":
                await self._call(
                    sock, 950, "Runtime.evaluate",
                    {"expression": f"window.scrollTo({a.get('x', 0)},{a.get('y', 0)});'ok'", "returnByValue": True},
                )

        if mode in ("visual", "both") and kind == "click" and x is not None:
            await self._call(
                sock, 960, "Runtime.evaluate",
                {"expression": f"window.__lcsMimic && window.__lcsMimic.ring({x},{y});'ok'", "returnByValue": True},
            )

    def _caption(self, a: dict) -> str:
        k = a.get("kind")
        if k == "click":
            return f"点击 {a.get('target') or '元素'}"
        if k == "key":
            mod = "".join(filter(None, ["Ctrl+" if a.get("ctrl") else "", "Shift+" if a.get("shift") else ""]))
            return f"按键 {mod}{a.get('key')}"
        if k == "input":
            return f"输入 {a.get('target') or ''} ← {(a.get('value') or '')[:24]}"
        if k == "scroll":
            return f"滚动到 y={a.get('y')}"
        if k == "fetch":
            return f"请求 {a.get('method')} …{str(a.get('url'))[-46:]}"
        return k or ""

    # ---------- CDP 小工具 ----------
    async def _call(self, sock, mid: int, method: str, params: dict) -> None:
        await sock.send(json.dumps({"id": mid, "method": method, "params": params}))

    async def _recv_value(self, sock, want_id: int):
        end = time.time() + 5
        while time.time() < end:
            try:
                raw = await asyncio.wait_for(sock.recv(), timeout=max(0.3, end - time.time()))
            except asyncio.TimeoutError:
                return None
            try:
                msg = json.loads(raw)
            except Exception:  # noqa: BLE001
                continue
            if msg.get("id") == want_id:
                return ((msg.get("result") or {}).get("result") or {}).get("value")
        return None


def load(path: Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))
