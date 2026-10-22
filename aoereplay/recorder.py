# -*- coding: utf-8 -*-
"""操作录制：把你自己的鼠标/键盘/浏览器动作录成可回放的动作序列。

两种接入方式
------------
1. 工具内置浏览器（推荐，零配置）
   `BrowserActor` 已经在跑 CDP（--remote-debugging-port），本模块直接订阅同一端口，
   注入一个 capture 脚本，把 click / input / key / scroll / navigation / fetch 全抓下来。

2. 你的日常 Chrome（不换浏览器）
   用 `extension/` 里的 MV3 扩展 + native messaging host，动作同样是这套 JSON，
   通过 /api/recorder/ingest 灌进来。扩展只读页面事件，不读取页面内容。

录制到的每一条动作带四元组：
    kind   —— click / input / key / scroll / nav / fetch
    cdp    —— 可以直接丢回 CDP `Input.dispatch*` 的坐标/按键（脚本级复现）
    visual —— 归一化坐标 0~1 + 元素描述（视觉级复现，换分辨率也能对上）
    delay  —— 与上一条的间隔（毫秒）

回放（mimic）有两种口味：
    script  —— 真派发 CDP 输入事件，最快、最像
    visual  —— 只移动鼠标 + 高亮框 + 文字提示，不触发真实点击（演示/教学用）
双轨 = 两者同时进行，画面里有真人示范，同时真实操作也在跑。
"""
from __future__ import annotations

import asyncio
import json
import os
import threading
import time
from pathlib import Path

from .actor import ws_connect

# 注入到页面的监听器。只上报「结构性动作 + 坐标 + 请求元信息」，不读页面正文。
CAPTURE_JS = r"""
(() => {
  if (window.__lcsCaptureInstalled) return 'already';
  window.__lcsCaptureInstalled = true;
  window.__lcsEvents = [];
  const push = (e) => {
    e.ts = Date.now();
    window.__lcsEvents.push(e);
    if (window.__lcsEvents.length > 400) window.__lcsEvents.shift();
  };
  const desc = (el) => {
    if (!el) return '';
    const t = (el.innerText || el.getAttribute('aria-label') || el.title || el.value || '').trim();
    return (el.tagName || '').toLowerCase() + (el.id ? '#' + el.id : '') + (t ? ' · ' + t.slice(0, 40) : '');
  };
  const norm = (x, y) => [ +(x / Math.max(1, innerWidth)).toFixed(4), +(y / Math.max(1, innerHeight)).toFixed(4) ];

  addEventListener('mousedown', ev => {
    const [nx, ny] = norm(ev.clientX, ev.clientY);
    push({ kind: 'click', button: ev.button, x: ev.clientX, y: ev.clientY, nx, ny,
           target: desc(ev.target), selector: cssPath(ev.target) });
  }, true);

  addEventListener('keydown', ev => {
    if (ev.ctrlKey && ev.key === 'Shift' && ev.key === 'C') return;
    push({ kind: 'key', key: ev.key, code: ev.code, ctrl: ev.ctrlKey, shift: ev.shiftKey,
           alt: ev.altKey, meta: ev.metaKey, target: desc(ev.target) });
  }, true);

  addEventListener('input', ev => {
    const t = ev.target;
    if (!t || typeof t.value !== 'string') return;
    push({ kind: 'input', value: t.value.slice(0, 200), selector: cssPath(t), target: desc(t) });
  }, true);

  let scrollTick = 0;
  addEventListener('scroll', () => {
    const now = Date.now();
    if (now - scrollTick < 120) return;
    scrollTick = now;
    push({ kind: 'scroll', x: scrollX, y: scrollY });
  }, { passive: true });

  addEventListener('wheel', ev => push({ kind: 'wheel', dx: ev.deltaX, dy: ev.deltaY }), { passive: true });

  const of = window.fetch;
  window.fetch = function (input, init) {
    try {
      const url = typeof input === 'string' ? input : input.url;
      const method = (init && init.method) || (input && input.method) || 'GET';
      push({ kind: 'fetch', url: String(url).slice(0, 300), method });
    } catch (e) {}
    return of.apply(this, arguments);
  };

  function cssPath(el) {
    if (!el || el.nodeType !== 1) return '';
    const parts = [];
    let cur = el, depth = 0;
    while (cur && cur.nodeType === 1 && depth < 5) {
      let s = cur.tagName.toLowerCase();
      if (cur.id) { s += '#' + cur.id; parts.unshift(s); break; }
      const cls = (cur.className && typeof cur.className === 'string') ? cur.className.trim().split(/\s+/)[0] : '';
      if (cls) s += '.' + cls;
      parts.unshift(s); cur = cur.parentElement; depth++;
    }
    return parts.join(' > ');
  }
  return 'installed';
})()
"""

DRAIN_JS = "JSON.stringify(window.__lcsEvents.splice(0, 200))"
NAV_JS = "JSON.stringify([location.href, innerWidth, innerHeight, devicePixelRatio])"

# 视觉回放：鼠标 + 点击涟漪 + 说明气泡，不触发真实点击
VISUAL_JS = r"""
(() => {
  if (window.__lcsMimic) return 'already';
  const mk = (id, css) => { const d = document.createElement('div'); d.id = id; d.style.cssText = css; document.documentElement.appendChild(d); return d; };
  const dot = mk('__lcs_dot', 'position:fixed;left:0;top:0;width:16px;height:16px;border-radius:50%;' +
    'background:#c9a961;box-shadow:0 0 0 3px #c9a96155,0 0 18px #c9a96199;pointer-events:none;z-index:2147483646;' +
    'transform:translate(-50%,-50%);transition:none;');
  const cap = mk('__lcs_cap', 'position:fixed;left:0;top:0;max-width:340px;padding:6px 10px;border-radius:6px;' +
    'background:#12182aee;color:#e6edf6;font:12px/1.5 "Microsoft YaHei UI",sans-serif;border:1px solid #2a3550;' +
    'pointer-events:none;z-index:2147483647;opacity:0;transition:opacity .15s;');
  window.__lcsMimic = {
    move(x, y) { dot.style.left = x + 'px'; dot.style.top = y + 'px'; },
    caption(text, x, y) {
      cap.textContent = text || '';
      cap.style.opacity = text ? 1 : 0;
      cap.style.left = Math.min(Math.max(8, x + 18), innerWidth - 348) + 'px';
      cap.style.top = Math.min(Math.max(8, y + 18), innerHeight - 60) + 'px';
    },
    ring(x, y) {
      const r = document.createElement('div');
      r.style.cssText = 'position:fixed;left:' + x + 'px;top:' + y + 'px;width:14px;height:14px;border-radius:50%;' +
        'border:2px solid #c9a961;pointer-events:none;z-index:2147483645;transform:translate(-50%,-50%);' +
        'animation:__lcsr .5s ease-out forwards;';
      document.documentElement.appendChild(r);
      setTimeout(() => r.remove(), 600);
    },
    done() { dot.remove(); cap.remove(); window.__lcsMimic = null; }
  };
  if (!document.getElementById('__lcs_style')) {
    const s = document.createElement('style'); s.id = '__lcs_style';
    s.textContent = '@keyframes __lcsr{to{width:56px;height:56px;opacity:0}}';
    document.head.appendChild(s);
  }
  return 'ready';
})()
"""


class Recorder:
    """订阅一个 CDP 端口，把动作按时间轴收集。"""

    def __init__(self, port_supplier, log=print):
        self._port = port_supplier
        self.log = log
        self.actions: list[dict] = []
        self.started_at = 0.0
        self.recording = False
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._attached: set[str] = set()
        self._page_info = {"url": "", "w": 0, "h": 0, "dpr": 1}
        self._inject_lock = threading.Lock()
        self._injected: set[str] = set()

    # ---------- 生命周期 ----------
    def start(self) -> dict:
        port = self._port()
        if not port:
            return {"ok": False, "message": "内置浏览器还没启动，先点左上角「浏览器面板」"}
        if ws_connect is None:
            # 没装 websockets 就别报告「录制中」——否则界面亮着 REC，stop 回来 0 条，
            # 用户只会以为是自己没点到。
            return {"ok": False, "message": "缺 websockets 库，装一下：python run.py --install-deps"}
        # 这两组是「已附着/已注入的 target」缓存：不复位的话，同一个标签页第二次录制
        # 会被当成已经连过了，一个动作都收不到。
        self._attached.clear()
        self._injected.clear()
        self.actions.clear()
        self.started_at = time.time()
        self.recording = True
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, args=(port,), daemon=True)
        self._thread.start()
        return {"ok": True, "port": port}

    def stop(self) -> dict:
        self.recording = False
        self._stop.set()
        return {"ok": True, "count": len(self.actions), "duration": round(time.time() - self.started_at, 2)}

    # ---------- 采集 ----------
    def _run(self, port: int) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self._loop(port))
        except Exception as e:  # noqa: BLE001
            self.log(f"[recorder] 采集停止: {e}")
        finally:
            loop.close()
    async def _loop(self, port: int) -> None:
        if ws_connect is None:
            self.log("[recorder] 缺 websockets 库")
            return
        import urllib.request

        while not self._stop.is_set():
            try:
                targets = json.loads(
                    urllib.request.urlopen(f"http://127.0.0.1:{port}/json/list", timeout=3).read()
                )
            except Exception:  # noqa: BLE001
                await asyncio.sleep(1.0)
                continue
            for t in targets:
                if t.get("type") != "page":
                    continue
                url = t.get("url") or ""
                if "devtools" in url or url.startswith("chrome://"):
                    continue
                ws = t.get("webSocketDebuggerUrl")
                if not ws or ws in self._attached:
                    continue
                self._attached.add(ws)
                asyncio.create_task(self._attach(ws))
            await asyncio.sleep(1.0)

    async def _attach(self, ws_url: str) -> None:
        try:
            async with ws_connect(ws_url, max_size=1 << 26, ping_interval=None) as sock:
                await sock.send(json.dumps({"id": 1, "method": "Page.enable"}))
                await sock.send(json.dumps({"id": 2, "method": "Network.enable"}))
                await self._inject(sock)
                while not self._stop.is_set():
                    try:
                        raw = await asyncio.wait_for(sock.recv(), timeout=1.0)
                    except asyncio.TimeoutError:
                        await self._inject(sock)
                        await self._drain(sock)
                        continue
                    try:
                        msg = json.loads(raw)
                    except Exception:  # noqa: BLE001
                        continue
                    self._on_event(msg)
                self._inject_left = False
        except Exception as e:  # noqa: BLE001
            self.log(f"[recorder] attach 断开: {e}")

    async def _inject(self, sock) -> None:
        # 只发一次，后续靠 drain 轮询
        if getattr(sock, "_lcs_injected", False):
            return
        await sock.send(
            json.dumps({"id": 10, "method": "Runtime.evaluate", "params": {"expression": CAPTURE_JS, "returnByValue": True}})
        )
        sock._lcs_injected = True  # type: ignore[attr-defined]
        await self._drain(sock)

    async def _drain(self, sock) -> None:
        await sock.send(
            json.dumps({"id": 11, "method": "Runtime.evaluate", "params": {"expression": DRAIN_JS, "returnByValue": True}})
        )
        await sock.send(
            json.dumps({"id": 12, "method": "Runtime.evaluate", "params": {"expression": NAV_JS, "returnByValue": True}})
        )

    def _on_event(self, msg: dict) -> None:
        mid = msg.get("id")
        res = (msg.get("result") or {}).get("result") or {}
        if mid == 11 and res.get("value"):
            try:
                batch = json.loads(res["value"])
            except Exception:  # noqa: BLE001
                return
            for a in batch:
                self._add(a)
        elif mid == 12 and res.get("value"):
            try:
                nav = json.loads(res["value"])
            except Exception:  # noqa: BLE001
                return
            self._page_info = {"url": nav[0], "w": nav[1], "h": nav[2], "dpr": nav[3]}
        method = msg.get("method")
        if method == "Network.requestWillBeSent":
            req = (msg.get("params") or {}).get("request") or {}
            url = req.get("url") or ""
            if "blob.core.windows.net" in url or url.endswith(".gz"):
                self._add(
                    {
                        "kind": "fetch",
                        "url": url,
                        "method": req.get("method"),
                        "headers": {
                            k: v
                            for k, v in (req.get("headers") or {}).items()
                            if k.lower() in ("authorization", "x-authorization", "referer", "cookie")
                        },
                    }
                )
        elif method == "Network.responseReceived":
            r = (msg.get("params") or {}).get("response") or {}
            url = r.get("url") or ""
            if "blob.core.windows.net" in url:
                self._add(
                    {
                        "kind": "response",
                        "url": url,
                        "status": r.get("status"),
                        "size": r.get("encodedDataLength") or 0,
                    }
                )

    def _add(self, a: dict) -> None:
        if not self.recording:
            return
        base = self.started_at
        a["t"] = round((a.get("ts", time.time() * 1000) / 1000.0 - base) * 1000)
        prev = self.actions[-1]["t"] if self.actions else 0
        a["delay"] = max(0, int(a["t"] - prev))
        a.setdefault("page", self._page_info.get("url", ""))
        self.actions.append(a)

    def _normalize_order(self) -> None:
        """按 t 排序并重算 delay。

        兜底补抓（_poll_once）捞回来的是「几百毫秒前」的事件，直接追加到尾部会让时间轴
        倒流；而 delay = max(0, t - 上一条) 一倒流就全被夹成 0，导出脚本的节奏彻底失真。
        """
        if not self.actions:
            return
        self.actions.sort(key=lambda a: a.get("t", 0))
        prev = 0
        for a in self.actions:
            t = int(a.get("t", 0))
            a["delay"] = max(0, t - prev)
            prev = t

    def add_external(self, a, source: str = "external") -> dict | None:
        """外部来源（浏览器扩展 / 游戏进程监视）灌进来的动作。

        来源不可信：扩展、手工导入的 JSON 都可能塞进字符串 t、null 元素。
        这里一律挡掉，不能让一条脏数据把整段录制（甚至整个 /api/recorder/ingest）搞挂。
        """
        if not isinstance(a, dict):
            return None
        a = dict(a)
        a["source"] = source
        try:
            ts = float(a["ts"]) if a.get("ts") is not None else None
        except (TypeError, ValueError):
            ts = None
        a["t"] = round((ts / 1000.0 - self.started_at) * 1000) if ts else round((time.time() - self.started_at) * 1000)
        prev = self.actions[-1].get("t", 0) if self.actions else 0
        a["delay"] = max(0, int(a["t"] - prev))
        if self.recording:
            self.actions.append(a)
        return a

    def ingest_script(self, payload: dict) -> dict:
        """整段导出（扩展录制结束 / 手工导入文件）合并进来。"""
        if not isinstance(payload, dict):
            return {"ok": False, "message": "payload 必须是对象"}
        actions = payload.get("actions")
        if isinstance(actions, dict):          # 有人会写成 {"0":{...}}，别按 key 迭代
            actions = list(actions.values())
        if not isinstance(actions, list):
            actions = []
        vp = payload.get("viewport")
        if isinstance(vp, dict):
            self._page_info = {**self._page_info, **vp}
        merged = 0
        for a in actions:
            if self.add_external(a, source=payload.get("name") or "import"):
                merged += 1
        self._normalize_order()
        return {"ok": True, "merged": merged, "total": len(self.actions)}

    def stats(self) -> dict:
        kinds: dict[str, int] = {}
        for a in self.actions:
            k = a.get("kind") or "?"
            kinds[k] = kinds.get(k, 0) + 1
        return {
            "recording": self.recording,
            "count": len(self.actions),
            "duration": round(time.time() - self.started_at, 1) if self.started_at else 0,
            "kinds": kinds,
            "viewport": self._page_info,
            "first": self.actions[0] if self.actions else None,
            "last": self.actions[-1] if self.actions else None,
        }

    # ---------- 导出 ----------
    def from_live_events(self, window_seconds: float = 0.5) -> dict:
        """兜底：直接从页面里取最近 0.5 秒内发生的事件（注入失败时也能救回来）。"""
        port = self._port()
        if not port or ws_connect is None:
            return {"ok": False, "message": "没有可用的调试端口"}
        base = self.started_at or time.time()
        now_ms = base * 1000
        res = self._poll_once(port, now_ms)
        return res

    def _poll_once(self, port: int, base_ms: float) -> dict:
        import asyncio
        import urllib.request

        expr = "JSON.stringify(window.__lcsEvents ? window.__lcsEvents.splice(0,300) : [])"

        async def go():
            targets = json.loads(urllib.request.urlopen(f"http://127.0.0.1:{port}/json/list", timeout=3).read())
            pages = [t for t in targets if t.get("type") == "page" and "devtools" not in (t.get("url") or "")]
            added = 0
            for p in pages:
                ws_url = p.get("webSocketDebuggerUrl")
                if not ws_url:
                    continue
                try:
                    async with ws_connect(ws_url, max_size=1 << 24, ping_interval=None) as sock:
                        await sock.send(json.dumps({"id": 1, "method": "Runtime.evaluate", "params": {"expression": expr, "returnByValue": True}}))
                        end = time.time() + 3
                        while time.time() < end:
                            try:
                                m = json.loads(await asyncio.wait_for(sock.recv(), timeout=max(0.3, end - time.time())))
                            except asyncio.TimeoutError:
                                break
                            if m.get("id") == 1:
                                val = ((m.get("result") or {}).get("result") or {}).get("value")
                                for a in json.loads(val or "[]"):
                                    if not isinstance(a, dict):
                                        continue
                                    try:
                                        ts = float(a.get("ts") or base_ms)
                                    except (TypeError, ValueError):
                                        ts = base_ms
                                    a["t"] = max(0, round(ts - base_ms))
                                    self.actions.append(a)
                                    added += 1
                                break
                except Exception as e:  # noqa: BLE001
                    self.log(f"[recorder] 补抓失败（跳过这个页面）: {e}")
                    continue
            return added

        try:
            added = asyncio.run(go())
            self._normalize_order()          # 补抓的旧事件插进来后，重排 + 重算 delay
            return {"ok": True, "added": added, "total": len(self.actions)}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "message": str(e)}

    def script(self, name: str = "session") -> dict:
        self._normalize_order()
        return {
            "version": 1,
            "kind": "lcs-action-script",
            "name": name,
            "created": time.strftime("%Y-%m-%d %H:%M:%S"),
            "viewport": self._page_info,
            "actions": self.actions,
        }

    def save(self, path: Path, name: str = "session") -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.script(name), ensure_ascii=False, indent=1), encoding="utf-8")
        return path


def load_script(path: Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))
