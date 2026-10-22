# -*- coding: utf-8 -*-
"""统一后端：FastAPI 存在就用 FastAPI（自动出 /docs），否则退回内置 http.server。

这样不用担心环境里装了哪个框架 —— 两个分支实现的是同一组接口：
    GET  /                        前端页面
    GET  /api/state               配置 + 库存 + 对局列表 + 队列（一个请求全拿走）
    POST /api/refresh             强制拉一次 API
    POST /api/download            批量入队 {game_ids:[...]}
    POST /api/retry               失败重试
    POST /api/cancel              清空队列
    GET  /api/queue               只看队列
    POST /api/play                启动回放 {game_id}
    GET  /api/game/{id}           单场详情（含己方/对手拆解）
    POST /api/actor/start         开独立浏览器窗口
    POST /api/actor/stop          关掉它
    POST /api/actor/open          在新窗口打开某场回放的网页
    POST /api/manual_url          手动登记一个带签名的下载地址
    POST /api/settings            改配置（profile_id / 路径 / 端口）
    GET  /api/export              导出已下载清单 CSV
"""
from __future__ import annotations

import csv
import io
import json
import os
import re
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse, parse_qs

from . import __version__
from . import api as world_api
from . import browser_setup, custom, gamever, gz, launcher, relay
from .actor import BrowserActor, UrlLedger
from .config import DATA_DIR, ROOT, Config, guess_network_dir
from .civs import civ_name
from .downloads import DownloadManager, ResolveError
from .library import PLAYBACK_PREFIX, Store
from .mimic import Mimic
from .recorder import Recorder
from .replay_watch import GameWatcher, NetworkWatcher, PlaybackWatcher

WEB_DIR = ROOT / "web"
MAPS_FILE = DATA_DIR / "maps.json"
THUMB_DIR = DATA_DIR / "mapthumbs"


def _load_maps() -> dict:
    """地图 slug → 本地缩略图 URL；没本地缓存时退回 AoE4World 线上地址。"""
    try:
        local = json.loads((DATA_DIR / "mapthumbs_local.json").read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        local = {}
    try:
        remote = json.loads(MAPS_FILE.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        remote = {}
    out = {}
    for slug, path in (local or {}).items():
        out[slug] = path
    for slug, url in (remote or {}).items():
        out.setdefault(slug, url)
    return out


MAP_THUMBS = _load_maps()
CIV_DIR = DATA_DIR / "civflags"


def _load_civs() -> dict:
    try:
        return json.loads((DATA_DIR / "civflags_local.json").read_text(encoding="utf-8")) or {}
    except Exception:  # noqa: BLE001
        return {}


CIV_FLAGS = _load_civs()

# 图片名白名单：缩略图/文明旗都是 <slug>.png，不需要子目录、不需要别的扩展名。
# 这里必须卡死 —— Windows 上 `\` 也是路径分隔符，`/mapthumbs/..\..\data\config.json`
# 这种请求能把 data/config.json 读出去（rsplit("/") 切不开反斜杠）。
_SAFE_IMAGE_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}\.png$")

# 请求体上限：动作脚本整段导入撑死几百 KB，8MB 已经宽到离谱，
# 但不设上限就等于让一个 Content-Length 头把内存吃光。
MAX_BODY = 8 * 1024 * 1024

# 写操作只认本机页面和本机扩展发来的请求。
_LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}
_EXT_SCHEMES = {"chrome-extension", "moz-extension"}


def _safe_image(base: Path, name: str) -> Path | None:
    """把 URL 里的文件名限制在 base 目录下的 .png 文件。拿不到就返回 None。"""
    if not _SAFE_IMAGE_RE.fullmatch(name or ""):
        return None
    try:
        root = base.resolve()
        fp = (root / name).resolve()
        fp.relative_to(root)          # 符号链接/大小写/相对段都过一遍
    except (OSError, ValueError):
        return None
    return fp if fp.is_file() else None


def _reject_post(origin: str | None, content_type: str | None) -> str | None:
    """POST 的门禁。返回拒收原因，放行返回 None。

    防的是「浏览器里随便哪个网页都能往 127.0.0.1 发表单」：
      * 表单 POST 只能是 urlencoded / multipart / text-plain —— 要求 JSON 就挡住了；
      * 再看 Origin，非同源直接拒。没有 Origin 的是本机脚本 / 扩展 / native host
        （curl 之类不带 Origin），那种请求没有可被借用的受害者浏览器，放行。
    """
    ctype = (content_type or "").split(";")[0].strip().lower()
    if ctype != "application/json":
        return "只接受 application/json"
    if origin:
        u = urlparse(origin)
        if u.scheme in _EXT_SCHEMES:
            return None
        if u.scheme in ("http", "https") and (u.hostname or "") in _LOCAL_HOSTS:
            return None
        return f"跨站请求被拒（Origin: {origin}）"
    return None


def _entry_key(entry: dict) -> str:
    r"""条目稳定标识，用于「从列表移除」。

    有 game_id 就用它（下载过的、network 缓存、手动登记的都能对上）；
    没有 game_id 的（playback\rogue\*.rec、temp.rec）用文件路径，
    这样移除后再扫描也不会重新冒出来。
    """
    gid = entry.get("game_id")
    if gid:
        return f"g:{int(gid)}"
    p = entry.get("path") or entry.get("file") or ""
    return f"p:{p}"


def _fmt_utc(iso: str | None) -> str:
    if not iso:
        return ""
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone()
        return dt.strftime("%Y-%m-%d %H:%M")
    except Exception:  # noqa: BLE001
        return iso


def _mmss(seconds: int | None) -> str:
    if not seconds:
        return "0:00"
    return f"{seconds // 60}:{seconds % 60:02d}"


def _map_slug(name: str | None) -> str:
    """'Hidden Valley' → 'hidden_valley'，和 AoE4World 的资源名对齐。"""
    import re

    return re.sub(r"[^a-z0-9]+", "_", (name or "").lower()).strip("_")


class Hub:
    """全局单例：配置、库存、API 缓存、下载队列、浏览器演员。"""

    def __init__(self, cfg: Config | None = None):
        self.cfg = cfg or Config()
        self.store = Store(self.cfg.db_path)
        self.ledger = UrlLedger(self.cfg.data_dir / "url-ledger.json")
        self.games: list[dict] = []
        self.api_state = {"ok": False, "message": "还没拉过数据", "at": 0}
        self._pages_loaded = 2      # 启动时 refresh(pages=2) 拉了两页
        self._total_count = 0       # AoE4World 报的总场数（分页信息里的 total_count）
        self._api_total = 0
        self._profile_cache: dict = {}   # AoE4World 玩家档案（名字/国家/主打文明）
        self.last_probe_note = ""
        self.lock = threading.Lock()
        self.downloads = DownloadManager(
            self.cfg.download_dir,
            self.cfg.library_dir,
            profile_id=self.cfg.profile_id,
            on_done=self._on_download_done,
        )
        self.recorder = None
        self.mimic = None
        self.game_watcher = None
        self.playback_watcher = None
        self.actor = None
        self.actor_log: list[dict] = []
        self.last_play: dict | None = None
        self._auto_play: dict[int, bool] = {}   # 点了播放、还在等下载的那几场
        self._custom_manual: list[dict] = []    # 手动登记的自定义对局
        self._killer: threading.Thread | None = None
        self.scripts_dir = self.cfg.data_dir / "scripts"
        self.scripts_dir.mkdir(parents=True, exist_ok=True)

    def wire_actor(self) -> None:
        """浏览器演员与它上面的录制/回放能力（测试时可跳过）。"""
        self.actor = BrowserActor(self.cfg, self.ledger, log=lambda m: print(m, flush=True))
        self.recorder = Recorder(port_supplier=lambda: self.actor.port if self.actor.running() else None)
        self.mimic = Mimic(port_supplier=lambda: self.actor.port if self.actor.running() else None)
        self.game_watcher = GameWatcher(on_event=self._on_game_event)
        self.playback_watcher = PlaybackWatcher(self.cfg.playback_dir, on_event=self._on_game_event)
        self.playback_watcher.on_new_id = self._on_new_replay_id
        self.net_watcher = NetworkWatcher(self.cfg.network_dir, on_new_id=self._on_new_replay_id)

    def _ensure_actor(self) -> None:
        if getattr(self, "actor", None) is None:
            self.wire_actor()

    # ---------- 录制联动 ----------
    def _on_game_event(self, ev: dict) -> None:
        self.actor_log.append({**ev, "t": time.time()})
        if self.recorder:
            self.recorder.add_external(ev, source="game")

    def _on_new_replay_id(self, game_id: int) -> None:
        """playback 里冒出 AgeIV_Replay_<id> → 游戏刚下过这一场，自动登记。"""
        try:
            self.rescan()
            self.add_custom_id(str(game_id))
            print(f"[watch] 已自动登记对局 {game_id}", flush=True)
        except Exception as e:  # noqa: BLE001
            print(f"[watch] 自动登记 {game_id} 失败: {e}", flush=True)

    def start_id_watcher(self) -> dict:
        """常驻监视：network\\ 里 M_<id>_<hash>（点开别人回放时会生成）+ playback 里的新文件。"""
        self._ensure_actor()
        if not self.playback_watcher.recording:
            self.playback_watcher.start()
        if not self.net_watcher.recording:
            self.net_watcher.start()
        # 立刻扫一遍，把已经在 network 里的 ID 也认出来
        already = self.net_watcher.scan_once()
        return {
            "ok": True,
            "watching": True,
            "network_dir": str(self.cfg.network_dir),
            "playback_dir": str(self.cfg.playback_dir),
            "found_now": already,
        }

    def rescan_network(self) -> dict:
        """手动扫一次 network 目录，返回里面所有 M_<id> 的对局。"""
        ids: dict[int, dict] = {}
        try:
            for p in self.cfg.network_dir.iterdir():
                m = NetworkWatcher.M_RE.match(p.name)
                if not m:
                    continue
                gid = int(m.group(1))
                st = p.stat()
                cur = ids.get(gid)
                if cur is None or st.st_size > cur["size"]:
                    ids[gid] = {"size": st.st_size, "file": p.name, "mtime": st.st_mtime}
        except OSError as e:
            return {"ok": False, "message": str(e)}
        return {"ok": True, "count": len(ids), "games": [{"game_id": k, **v} for k, v in sorted(ids.items(), reverse=True)]}

    def start_recording(self) -> dict:
        self._ensure_actor()
        res = self.recorder.start()
        if res.get("ok"):
            self.game_watcher.start()
            self.playback_watcher.start()
        return res

    def stop_recording(self) -> dict:
        self._ensure_actor()
        self.game_watcher.stop()
        self.playback_watcher.stop()
        res = self.recorder.stop()
        res["count"] = len(self.recorder.actions)
        return res

    def save_script(self, name: str = "") -> dict:
        self._ensure_actor()
        name = (name or time.strftime("session-%Y%m%d-%H%M%S")).strip()
        path = self.scripts_dir / f"{name}.json"
        self.recorder.save(path, name)
        return {"ok": True, "path": str(path), "actions": len(self.recorder.actions)}

    def list_scripts(self) -> list[dict]:
        out = []
        for p in sorted(self.scripts_dir.glob("*.json"), key=lambda x: -x.stat().st_mtime):
            try:
                d = json.loads(p.read_text(encoding="utf-8"))
                out.append(
                    {
                        "name": d.get("name") or p.stem,
                        "path": str(p),
                        "actions": len(d.get("actions") or []),
                        "created": d.get("created") or time.strftime("%Y-%m-%d %H:%M", time.localtime(p.stat().st_mtime)),
                    }
                )
            except Exception:  # noqa: BLE001
                continue
        return out[:50]

    def recorder_state(self) -> dict:
        self._ensure_actor()
        return {
            "recorder": self.recorder.stats(),
            "mimic": self.mimic.state(),
            "game": {
                "watcher": self.game_watcher.recording,
                "playback_watcher": self.playback_watcher.recording,
                "last_replay": self.game_watcher.last_replay,
                "events": self.actor_log[-12:],
            },
            "scripts": self.list_scripts(),
            "has_page": bool(self.actor and self.actor.running()),
        }

    def recent_actions(self, n: int = 40) -> list[dict]:
        self._ensure_actor()
        return self.recorder.actions[-n:]

    def play_mimic(self, name: str = "", mode: str = "script", speed: float = 1.0) -> dict:
        self._ensure_actor()
        script = None
        if name:
            path = self.scripts_dir / (name if name.endswith(".json") else f"{name}.json")
            if path.exists():
                script = json.loads(path.read_text(encoding="utf-8"))
        if script is None:
            script = self.recorder.script("live")
        if not (script.get("actions") or []):
            return {"ok": False, "message": "没有可回放的动作：先录制，或在右侧选一个脚本"}
        return self.mimic.play(script, mode=mode, speed=speed)

    def ingest_external_script(self, payload: dict) -> dict:
        """扩展导出 / 手工导入的动作文件。"""
        self._ensure_actor()
        if not self.recorder.recording:
            self.recorder.started_at = time.time() - 1
            self.recorder.recording = True
        res = self.recorder.ingest_script(payload)
        name = (payload.get("name") or "imported").replace(":", "-")
        self.recorder.save(self.scripts_dir / f"{name}.json", name)
        return {**res, "scripts": len(self.list_scripts())}

    # ---------- 数据 ----------
    def refresh(self, pages: int = 2) -> dict:
        """重新拉取前 N 页。**只补不减**：已加载到第 5 页时，
        一次 refresh(pages=2) 不该把第 3~5 页的对局从列表里抹掉。"""
        try:
            fresh = world_api.games(self.cfg.profile_id, pages=pages)
            try:
                _b, info = world_api.games_page(self.cfg.profile_id, 1)
                tot = int(info.get("total_count") or 0)
            except Exception:  # noqa: BLE001
                tot = self._api_total
            with self.lock:
                if self.games and self._pages_loaded > pages:
                    seen = {int(g["game_id"]) for g in fresh}
                    keep = [g for g in self.games if int(g["game_id"]) not in seen]
                    merged = fresh + keep
                    merged.sort(key=lambda g: g.get("started_at") or "", reverse=True)
                    self.games = merged
                else:
                    self.games = fresh
                    self._pages_loaded = max(self._pages_loaded, pages)
                self._api_total = tot
            self.store.cache_games(self.games)
            self.api_state = {"ok": True, "message": f"{len(self.games)} 场", "at": time.time()}
        except Exception as e:  # noqa: BLE001
            msg = str(e)
            with self.lock:
                if not self.games:
                    self.games = self.store.cached_games()
            self.api_state = {"ok": False, "message": f"API 不可用（显示缓存）：{msg}", "at": time.time()}
        self.rescan()
        return self.api_state

    def api_total_count(self) -> int:
        """总场数（AoE4World 的 total_count），前端用它算「还有多少场没加载」。"""
        try:
            _batch, info = world_api.games_page(self.cfg.profile_id, 1)
            with self.lock:
                self._api_total = int(info.get("total_count") or 0)
            return self._api_total
        except Exception:  # noqa: BLE001
            with self.lock:
                return self._api_total

    def load_more(self, pages: int = 1) -> dict:
        """往后多拉几页（每页 50 场），追加到内存列表里。

        跟 refresh 分开的原因：refresh 会用前 N 页整体覆盖 self.games，
        后台轮询每 poll_seconds 秒跑的是 refresh(pages=1)，会把这批结果抹掉。
        所以这里用 self._pages_loaded 记住已经拉到第几页，刷新后还能接着往下翻。
        """
        with self.lock:
            have = {int(g["game_id"]) for g in self.games}
            loaded = max(self._pages_loaded, (len(self.games) + 49) // 50)
        api_info = {"ok": True, "message": "", "at": time.time()}
        added = 0
        total_count = self._total_count
        next_page = loaded + 1
        for _ in range(max(1, pages)):
            page_no = next_page
            try:
                batch, info = world_api.games_page(self.cfg.profile_id, page_no)
            except Exception as e:  # noqa: BLE001
                api_info = {"ok": False, "message": f"第 {page_no} 页拉取失败：{e}", "at": time.time()}
                break
            total_count = info.get("total_count") or total_count
            fresh = [g for g in batch if int(g["game_id"]) not in have]
            if not fresh:
                next_page = 0          # 到底了
                break
            for g in fresh:
                have.add(int(g["game_id"]))
            with self.lock:
                merged = self.games + fresh
                merged.sort(key=lambda g: g.get("started_at") or "", reverse=True)
                self.games = merged
                self._pages_loaded = page_no
                self._total_count = total_count
            added += len(fresh)
            nxt = info.get("next_page")
            if not nxt or len(batch) < (info.get("per_page") or 50):
                next_page = 0
                break
            next_page = int(nxt)
        with self.lock:
            total = len(self.games)
            self._api_total = total_count
        return {
            "added": added,
            "total": total,
            "total_count": total_count,
            "pages_loaded": self._pages_loaded,
            "next_page": next_page,
            "api": api_info,
        }

    def rescan(self) -> None:
        try:
            self.store.scan(self.cfg.playback_dir, self.cfg.library_dir)
        except Exception as e:  # noqa: BLE001
            print(f"[store] 扫描失败: {e}", flush=True)

    def _on_download_done(self, game_id: int, path: Path) -> None:
        self.rescan()
        # 点播放时本地还没有 → 下完自动启动
        if self._auto_play.pop(game_id, False):
            try:
                self._launch(game_id, path)
                print(f"[play] {game_id} 下载完成，已自动启动回放", flush=True)
            except Exception as e:  # noqa: BLE001
                print(f"[play] {game_id} 自动启动失败: {e}", flush=True)

    def _keep_fresh_loop(self) -> None:
        if self.cfg.poll_seconds <= 0:
            return
        while True:
            time.sleep(self.cfg.poll_seconds)
            try:
                self.refresh(pages=1)
            except Exception:  # noqa: BLE001
                pass

    def start_background(self) -> None:
        if self._killer is None:
            self._killer = threading.Thread(target=self._keep_fresh_loop, daemon=True)
            self._killer.start()

    # ---------- 组装 ----------
    def profile_card(self) -> dict:
        """顶部横幅要的信息：名字 / 国旗国家 / 主打文明。

        数据来自 AoE4World 的 /players/<id>，带内存缓存（改 profile_id 时失效）。
        「主打文明」取该玩家排位场次最多的那个文明，和 AoE4World 页面上的
        「MAINLY PLAYS XXX」是同一个口径。
        """
        pid = self.cfg.profile_id
        with self.lock:
            cached = self._profile_cache
        if cached and cached.get("profile_id") == pid:
            return cached
        if not pid:
            return {}

        try:
            raw = world_api.player(pid)
        except Exception as e:  # noqa: BLE001
            out = {
                "profile_id": pid,
                "name": self.cfg.profile_name or "",
                "country": "",
                "top_civ": {},
                "error": str(e)[:120],
            }
            return out

        # 主打文明：优先 rm_solo，其次 rm_1v1（旧字段），最后任意有数据的模式
        modes = raw.get("modes") or {}
        civs = []
        for key in ("rm_solo", "rm_1v1", "rm_2v2", "rm_3v3", "rm_4v4", "qm_1v1", "qm_2v2", "qm_3v3", "qm_4v4"):
            m = modes.get(key) or {}
            c = m.get("civilizations") or []
            if c:
                civs = c
                break
        civs = sorted(civs, key=lambda c: c.get("games_count") or 0, reverse=True)
        top_civ = {}
        if civs:
            cid = civs[0].get("civilization") or ""
            top_civ = {
                "civ": cid,
                "civ_zh": civ_name(cid),
                "games": civs[0].get("games_count") or 0,
                "icon": CIV_FLAGS.get(cid, ""),
            }

        out = {
            "profile_id": pid,
            "name": raw.get("name") or self.cfg.profile_name or "",
            "country": (raw.get("country") or "").lower(),
            "steam_id": raw.get("steam_id") or "",
            "site_url": raw.get("site_url") or f"https://aoe4world.com/players/{pid}",
            "top_civ": top_civ,
            "error": "",
        }
        with self.lock:
            self._profile_cache = out
        # 名字变了就跟着更新显示（config 里那份是启动时读的）
        if out["name"] and out["name"] != self.cfg.profile_name:
            self.cfg.profile_name = out["name"]
        return out

    def state(self, pages: int | None = None) -> dict:
        self.rescan()
        owned = self.store.owned()
        with self.lock:
            games = list(self.games)
        queue = self.downloads.snapshot()
        queue_by_game = {j["game_id"]: j for j in queue["jobs"]}
        rows = []
        for g in games:
            gid = int(g["game_id"])
            row = self._row(g, owned.get(gid), queue_by_game.get(gid))
            rows.append(row)
        totals = {
            "games": len(rows),
            "owned": sum(1 for r in rows if r["owned"]),
            "missing": sum(1 for r in rows if not r["owned"]),
        }
        return {
            "config": self.cfg.snapshot(),
            "profile": self.profile_card(),
            "api": {**self.api_state, **world_api.throttle_state()},
            "actor": {
                "running": bool(self.actor and self.actor.running()),
                "port": self.actor.port if self.actor else None,
                "ledger": self.ledger.stats(),
            },
            "totals": totals,
            "paging": {
                "loaded": len(rows),
                "total_count": self._api_total,
                "pages_loaded": self._pages_loaded,
                "has_more": bool(self._api_total and len(rows) < self._api_total),
            },
            "games": rows,
            "favorite_ids": sorted(self.store.favorite_ids()),
            "favorite_count": len(self.store.favorite_ids()),
            "queue": queue,
            "last_play": self.last_play,
            "recording": self.recorder_state() if self.recorder else None,
            "game_running": launcher.is_game_running(),
            "server_time": time.time(),
            "capabilities": {
                "api_ok": self.api_state["ok"],
                "replay_endpoint": world_api.replay_probe(self.games[0]["game_id"]) if self.games else None,
            },
        }

    # ---------- 录制相关动作 ----------
    def actor_start(self) -> dict:
        self._ensure_actor()
        res = self.actor.start("https://aoe4world.com")
        if res.get("ok"):
            browser_setup.seed_profile(self.cfg.profile)
        return res

    def actor_stop(self) -> dict:
        self._ensure_actor()
        if self.recorder and self.recorder.recording:
            self.stop_recording()
        self.actor.stop()
        return {"ok": True}

    def _row(self, g: dict, own: dict | None, job: dict | None) -> dict:
        me_id = self.cfg.profile_id
        me, mates, mates_raw, opps = None, [], [], []
        for team in g.get("teams") or []:
            for entry in team:
                p = entry.get("player") or entry
                item = {
                    "profile_id": p.get("profile_id"),
                    "name": p.get("name"),
                    "civ": p.get("civilization"),
                    "civ_zh": civ_name(p.get("civilization") or ""),
                    "civ_icon": CIV_FLAGS.get(p.get("civilization") or "", ""),
                    "rating": p.get("rating"),
                    "rating_diff": p.get("rating_diff"),
                    "result": p.get("result"),
                    "country": p.get("country"),
                }
                if p.get("profile_id") == me_id:
                    me = item
                    for e in team:
                        q = e.get("player") or e
                        if q.get("profile_id") == me_id:
                            continue
                        mates.append(q.get("name"))
                        mates_raw.append(
                            {
                                "name": q.get("name"),
                                "civ": q.get("civilization"),
                                "civ_zh": civ_name(q.get("civilization") or ""),
                                "civ_icon": CIV_FLAGS.get(q.get("civilization") or "", ""),
                                "rating": q.get("rating"),
                                "result": q.get("result"),
                            }
                        )
                    opps = [
                        {
                            "name": (e.get("player") or e).get("name"),
                            "civ": (e.get("player") or e).get("civilization"),
                            "civ_zh": civ_name(((e.get("player") or e).get("civilization")) or ""),
                            "civ_icon": CIV_FLAGS.get(((e.get("player") or e).get("civilization")) or "", ""),
                            "rating": (e.get("player") or e).get("rating"),
                            "rating_diff": (e.get("player") or e).get("rating_diff"),
                            "result": (e.get("player") or e).get("result"),
                        }
                        for other in (g.get("teams") or [])
                        if other is not team
                        for e in other
                    ]
                    # 顺带把对手的文明图标也塞进 30 行内可见的缓存
                    opp_names = [o["name"] for o in opps]
        return {
            "game_id": int(g["game_id"]),
            "started_at": g.get("started_at"),
            "started_local": _fmt_utc(g.get("started_at")),
            "duration": g.get("duration"),
            "duration_text": _mmss(g.get("duration")),
            "map": g.get("map"),
            "map_thumb": MAP_THUMBS.get(_map_slug(g.get("map")), ""),
            "kind": g.get("kind"),
            "leaderboard": g.get("leaderboard"),
            "server": g.get("server"),
            "patch": g.get("patch"),
            "average_rating": g.get("average_rating"),
            "me": me,
            "mates": mates,
            "matesRaw": mates_raw,
            "opponents": opps,
            "own_block": own,
            "owned": bool(own),
            "owned_info": own,
            "status": "downloaded" if own else (job["status"] if job else "available"),
            "job": job,
            "version_blocked": gamever.ignored_replays().get(int(g["game_id"])),
            "playable": (None if not g.get("patch") else int(g["patch"]) == int(self._client_build() or g["patch"])),
            "client_build": self._client_build(),
            "site_url": f"https://aoe4world.com/players/{self.cfg.profile_id}/games?game={int(g['game_id'])}",
        }

    # ---------- 动作 ----------
    def enqueue(self, ids: list[int], force: bool = False) -> dict:
        """入队下载。

        force=False（默认）时，已在本地库里的对局会被跳过 —— 前端「下载选中」
        /「下载全部缺失」都走这条路径，不重复下载已有文件。
        force=True 是「强制重下」，给需要重新抓一遍的场合用。
        同时挡掉已经在队列里的，避免点两次排两份。
        """
        owned = self.store.owned()
        with self.lock:
            by_id = {int(g["game_id"]): g for g in self.games}
        active = self.downloads.active_ids() if hasattr(self.downloads, "active_ids") else set()
        items = []
        skipped_owned, skipped_queued = [], []
        for gid in ids:
            if gid in owned and not force:
                skipped_owned.append(gid)
                continue
            if gid in active:
                skipped_queued.append(gid)
                continue
            g = by_id.get(gid) or {}
            label = f"{_fmt_utc(g.get('started_at'))} {g.get('map') or ''}".strip() or str(gid)
            items.append((gid, label))
        res = self.downloads.enqueue(items, force=force)
        return {
            "added": res if isinstance(res, list) else [],
            "skipped_owned": skipped_owned,
            "skipped_queued": skipped_queued,
            "force": bool(force),
        }

    # ---------- 收藏 ----------
    def favorite_ids(self) -> set[int]:
        return self.store.favorite_ids()

    def favorite_snapshot(self, game_id: int) -> dict:
        """收藏时把这场的关键信息存一份，日后 API 拉不到也还能看。"""
        with self.lock:
            g = next((x for x in self.games if int(x["game_id"]) == int(game_id)), None)
        if g:
            row = self._row(g, None, None)
            return {
                "map": row.get("map"),
                "kind": row.get("kind"),
                "started_local": row.get("started_local"),
                "duration_text": row.get("duration_text"),
                "me": row.get("me"),
                "matesRaw": row.get("matesRaw"),
                "opponents": row.get("opponents"),
                "average_rating": row.get("average_rating"),
                "patch": row.get("patch"),
            }
        # 排位列表里没有（比如自定义对局的 game_id），退回自定义对局的扫描结果
        try:
            for e in custom.scan_network(self.cfg.network_dir, set()):
                if int(e.get("game_id") or 0) == int(game_id):
                    return {
                        "map": e.get("map"),
                        "kind": e.get("kind"),
                        "started_local": e.get("started_local"),
                        "players": e.get("players") or [],
                        "mode_label": e.get("mode_label"),
                        "pack_id": e.get("pack_id"),
                    }
        except Exception:  # noqa: BLE001
            pass
        return {}

    def toggle_favorite(self, game_id: int, favorite: bool | None = None) -> dict:
        cur = game_id in self.store.favorite_ids()
        want = (not cur) if favorite is None else bool(favorite)
        if want:
            self.store.add_favorite(game_id, self.favorite_snapshot(game_id))
        else:
            self.store.remove_favorite(game_id)
        return {"game_id": int(game_id), "favorite": want, "count": len(self.store.favorite_ids())}

    def set_favorite_note(self, game_id: int, note: str) -> dict:
        return self.store.set_favorite_note(game_id, note)

    # ---------- 从列表移除（只隐藏，不删文件）----------
    def hide_entries(self, keys: list[str]) -> dict:
        """把选中的条目从列表里去掉。**磁盘上的文件一个都不动。**

        想恢复：POST /api/custom/unhide（不传 keys 就全部恢复）。
        """
        gids = []
        for k in keys or []:
            if k.startswith("g:"):
                try:
                    gids.append(int(k[2:]))
                except ValueError:
                    gids.append(None)
            else:
                gids.append(None)
        res = self.store.hide(keys or [], gids, reason="user_removed")
        # 顺便把手动登记记录也清掉，否则下次列表还会把它补回来
        for gid in gids:
            if gid is not None:
                self._custom_manual = [r for r in self._custom_manual if r.get("game_id") != gid]
        return res

    def unhide_all(self) -> dict:
        return self.store.unhide(None)

    def hidden_count(self) -> int:
        return len(self.store.hidden_keys())

    def favorites(self) -> dict:
        """收藏列表：把库里的快照 + 当前实况合并成前端能直接渲染的形状。"""
        owned = self.store.owned()
        queue = self.downloads.snapshot()
        queue_by_game = {j["game_id"]: j for j in queue["jobs"]}
        with self.lock:
            live = {int(g["game_id"]): g for g in self.games}
        out = []
        for fav in self.store.favorites():
            gid = fav["game_id"]
            snap = fav.get("snapshot") or {}
            g = live.get(gid)
            if g:
                row = self._row(g, owned.get(gid), queue_by_game.get(gid))
                row["stale"] = False
            else:
                # 排位列表里没有它了 —— 用收藏当时的快照兜底，别显示成空白行
                row = {
                    "game_id": gid,
                    "map": snap.get("map") or "—",
                    "kind": snap.get("kind"),
                    "started_at": "",
                    "started_local": snap.get("started_local") or "",
                    "duration_text": snap.get("duration_text") or "",
                    "map_thumb": MAP_THUMBS.get(_map_slug(snap.get("map")), ""),
                    "average_rating": snap.get("average_rating"),
                    "patch": snap.get("patch"),
                    "me": snap.get("me"),
                    "mates": [],
                    "matesRaw": snap.get("matesRaw") or [],
                    "opponents": snap.get("opponents") or [],
                    "owned": gid in owned,
                    "owned_info": owned.get(gid),
                    "status": "downloaded" if gid in owned else (queue_by_game.get(gid) or {}).get("status", "available"),
                    "job": queue_by_game.get(gid),
                    "version_blocked": None,
                    "playable": None,
                    "client_build": self._client_build(),
                    "site_url": f"https://aoe4world.com/players/{self.cfg.profile_id}/games?game={gid}",
                    "players": snap.get("players") or [],
                    "mode_label": snap.get("mode_label"),
                    "stale": True,
                }
            row["favorite"] = True
            row["note"] = fav.get("note") or ""
            row["favorited_at"] = fav.get("created")
            row["note_updated"] = fav.get("updated")
            out.append(row)
        return {"entries": out, "count": len(out)}

    def recent_missing(self, days: int = 7, limit: int = 0, probe: bool = True) -> dict:
        """「近 N 天缺失」：按时间倒序取最近的、本地还没有的对局。

        days=0 表示不限时间；limit>0 时最多取这么多场（0 = 全要）。
        先把已下载的过滤掉再截断，所以 limit=20 拿到的确实是 20 场缺的。

        probe=True 时会先向 Relic 批量验证「服务器上到底有没有副本」，
        只把真正拿得到地址的入队 —— 否则这功能只是按「本地有没有」猜，
        会把一批下不来的场次白排进队列（跟已下线的「下载全部缺失」一个毛病）。
        """
        from datetime import datetime, timedelta, timezone

        own = self.store.owned()
        with self.lock:
            games = list(self.games)
        cutoff = None
        if days and days > 0:
            cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        picked: list[int] = []
        for g in games:
            gid = int(g["game_id"])
            if gid in own:
                continue
            if cutoff is not None:
                try:
                    ts = datetime.fromisoformat(str(g.get("started_at") or "").replace("Z", "+00:00"))
                except Exception:  # noqa: BLE001
                    continue
                if ts < cutoff:
                    continue
            picked.append(gid)
            if limit and len(picked) >= limit:
                break

        unavailable: list[int] = []
        unknown: list[int] = []
        alive = picked
        if probe and picked:
            try:
                ok, unavailable, unknown = self.downloads.probe_ok_ids(picked)
                alive = ok
            except Exception as e:  # noqa: BLE001
                # 探测整体炸掉时宁可保守：把这批都当「不知道」，别误判成没副本
                alive = []
                unknown = list(picked)
                self.last_probe_note = f"探测失败：{e}"

        res = self.enqueue(alive, force=False)
        res.update({
            "picked": len(picked),
            "days": days,
            "limit": limit,
            "probed": bool(probe),
            "available": len(alive),
            "unavailable": unavailable,   # 接口正常应答、确实没副本
            "unknown": unknown,           # 请求失败，可重试 —— 不能当没副本
        })
        return res

    def custom_games(self) -> dict:
        """自定义 / 非排位对局：network 缓存 + 本地 rogue/temp + 你登记过的 ID。

        只对最近 100 条做 API 补全（玩家/文明/评分），其余按需 —— 见 enrich_custom()。
        """
        owned = self.store.owned()
        entries = custom.scan_local(self.cfg.playback_dir)
        net = [e for e in custom.scan_network(self.cfg.network_dir, set(owned)) if e["playable"]]
        entries = net + entries

        # 手动登记条目与扫描结果按 game_id 去重：
        #   * 扫描结果是 network 全量（有玩家名单）→ 用它，丢掉 manual
        #   * 扫描结果里没有 → 才把 manual 补进来（它可能已经带了解析好的玩家）
        # 注意不能只看「在不在列表里」：刚 加入并解析 的那场文件已经写进回放库，
        # 但 rescan 还没跑到，此时 network 里没有它，manual 必须补进来。
        have = {int(e["game_id"]) for e in entries if e.get("game_id") and e.get("source") == "network"}
        manual_first: list[dict] = []
        for row in reversed(self._custom_manual):
            gid = row.get("game_id")
            if gid is None or int(gid) in have:
                continue
            manual_first.append(row)
            have.add(int(gid))
        entries = manual_first + entries

        # 「从列表移除」的过滤：只隐藏条目，不删文件。
        # key 规则：有 game_id 用 "g:<id>"，否则用 "p:<路径>"（rogue/.rec 这类）。
        hidden = self.store.hidden_keys()
        if hidden:
            entries = [e for e in entries if _entry_key(e) not in hidden]
        # 给前端带上 key，移除时原样回传
        for e in entries:
            e["key"] = _entry_key(e)
        # 首次只补一屏（8 条），其余交给前端滚动时 /api/custom/enrich 懒加载
        head = entries[:8]
        ids = [e["game_id"] for e in head if e.get("source") == "network" and e.get("game_id")]
        if ids:
            try:
                info = custom.enrich_from_api(ids, self.cfg.network_dir, self.cfg.profile_id, limit=8)
                for e in head:
                    d = info.get(e.get("game_id") or 0)
                    if d and not d.get("missing"):
                        e["api"] = d
                        e["map"] = d.get("map") or e["map"]
                        e["kind"] = d.get("kind") or e.get("kind")
                        if d.get("started_at"):
                            e["started_local"] = _fmt_utc(d["started_at"])
            except Exception:  # noqa: BLE001
                pass
        # 其余条目：先从缓存补（不再发请求）
        cached = custom.load_game_cache(self.cfg.network_dir)
        for e in entries[8:]:
            gid = e.get("game_id")
            d = cached.get(str(gid)) if gid else None
            if d and not d.get("missing"):
                e["api"] = d
                e["map"] = d.get("map") or e["map"]
                e["kind"] = d.get("kind") or e.get("kind")
                if d.get("started_at"):
                    e["started_local"] = _fmt_utc(d["started_at"])

        # 只留「自定义」——靠文件里的 section_cac_conditions 判断，
        # 不依赖 AoE4World（那边根本没有自定义对局的数据）
        kept: list[dict] = []
        counts = {"custom": 0, "ranked": 0, "quick": 0, "ai": 0}
        for e in entries:
            src = e.get("source")
            kind = (e.get("kind") or "").lower()
            if src == "network" and e.get("path"):
                # 网络缓存：直接读文件判断（带缓存）
                try:
                    is_cust = custom.fast_custom(Path(e["path"]), self.cfg.network_dir)
                except Exception:  # noqa: BLE001
                    is_cust = False
                cls = "custom" if is_cust else ("ranked" if kind.startswith("rm_") else "quick" if kind.startswith("qm_") else "custom" if not kind else "quick")
                if not is_cust and not kind:
                    # 文件说不是自定义、又没有 API 数据 —— 保守起见不算自定义
                    cls = "quick"
            else:
                # rogue / temp / manual：本地录制，算自定义
                cls = "custom"
            e["cls"] = cls
            # 号主判定：玩家列表里有没有我的 steam_id
            my_sid = str(self.cfg.steam_id or "")
            pls = e.get("players") or []
            if my_sid and pls:
                e["is_mine"] = any(pl.get("steam_id") == my_sid for pl in pls)
            elif my_sid and not pls:
                e["is_mine"] = None      # 解析不出玩家，无法判断
            else:
                e["is_mine"] = None
            counts[cls] = counts.get(cls, 0) + 1
            if cls == "custom":
                kept.append(e)

        custom.save_cls_cache(self.cfg.network_dir)
        # 玩法包命名
        pnames = self.pack_names()
        for e in kept:
            pid = e.get("pack_id")
            if pid:
                e["pack_name"] = pnames.get(str(pid), "")
        return {
            "entries": kept,
            "count": len(kept),
            "all_count": len(entries),
            "class_counts": counts,
            "enriched": len(ids),
            "hidden_count": self.hidden_count(),
        }
    def enrich_custom(self, game_ids: list[int]) -> dict:
        """按需补查（滚动到更多时调用）。"""
        ids = [int(i) for i in game_ids][:30]
        info = custom.enrich_from_api(ids, self.cfg.network_dir, self.cfg.profile_id, limit=30)
        return {"ok": True, "count": len(info), "data": {str(k): v for k, v in info.items()}}

    def pack_names(self) -> dict:
        import json as _json

        p = self.cfg.data_dir / "pack_names.json"
        try:
            return _json.loads(p.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            return {}

    def set_pack_name(self, pack_id: int, name: str) -> dict:
        import json as _json

        names = self.pack_names()
        names[str(pack_id)] = name
        p = self.cfg.data_dir / "pack_names.json"
        p.write_text(_json.dumps(names, ensure_ascii=False, indent=1), encoding="utf-8")
        return {"ok": True, "pack_id": pack_id, "name": name}

    def add_custom_id(self, raw: str, fetch: bool = True) -> dict:
        """贴一个 matchhistory_id 或回放 URL，直接下载并解析出玩家名单。

        流程（fetch=True，默认）：
          ID → 查地址（三态）→ 下载到 data/replays → 解压 → parse_players
             → 回填 map / players / mode / pack_id → 列表立刻能看到完整名单

        为什么要下载：玩家、地图、文明、模式**全部写在回放文件内部**，
        AoE4World 又查不到自定义对局（服务器不收录），所以不下载就什么都没有。
        单场一般 0.7~1.2 MB，下载一次之后文件留在库里，点 ▶ 不会重下。

        fetch=False 只登记不入库（调试用），此时行里必然没有玩家数据。
        """
        gid = None
        m = re.search(r"/M_(\d+)_[0-9a-fA-F]{64}\.gz", raw or "")
        if m:
            gid = int(m.group(1))
        elif (raw or "").strip().isdigit():
            gid = int(raw.strip())
        if not gid:
            return {"ok": False, "message": "认不出对局 ID（需要数字，或含 M_<id>_<hash>.gz 的地址）"}

        # 用三态接口：能区分「确实没副本」和「查询失败」，别让一次网络抖动说成没有
        try:
            st = relay.files_for_status([gid], prefer_profile=self.cfg.profile_id).get(gid) or {}
        except Exception as e:  # noqa: BLE001
            st = {"status": "unknown", "error": str(e)[:120]}
        status = st.get("status") or "unknown"
        info = st.get("file")

        # 注意：这里不能用 dict 字面量 + .get()，因为三个值都会提前求值，
        # info 为 None 时 f-string 里的 info['copies'] 会直接炸（曾经就是这么 500 的）。
        if status == "ok" and info:
            note = f"服务器上有 {info.get('copies', 1)} 份副本"
        elif status == "missing":
            note = "服务器上没有这场录像"
        else:
            note = f"查询失败，稍后重试：{st.get('error') or '未知错误'}"

        row = {
            "source": "manual",
            "file": "",
            "path": "",
            "size": None,
            "started_at": "",
            "started_local": time.strftime("%Y-%m-%d %H:%M"),
            # 不叫「手动登记」——那看着像状态；在地图位写清楚它还没下载
            "map": "未下载",
            "civ_zh": "",
            "kind": "manual",
            "game_id": gid,
            "playable": status in ("ok", "unknown"),
            "probe": status,
            "note": note,
        }
        # 已在本地？标出来能立刻播；但「本地已有」不等于「已解析」——
        # 老版本登记过的场次文件在库里却没走过解析，玩家名单是空的，
        # 所以下面仍然要尝试从本地文件解析一遍。
        owned = self.store.owned()
        already_local = gid in owned
        local_path = self.store.best_file(gid) if already_local else None
        if already_local:
            row.update({
                "playable": True,
                "path": str(local_path) if local_path else "",
                "map": "本地已有",
                "note": "已在本地库，点 ▶ 直接播放",
                "probe": "ok",
            })

        # ---- 解析（本地已有就直接读本地，否则下载）----
        # 玩家/地图/文明/模式全都写在文件里，不解析就什么都看不到。
        fetched = False
        fetch_error = ""
        try:
            if local_path is not None and local_path.exists():
                hist = self._parse_file(gid, local_path)
                if hist:
                    row.update(hist)
                    fetched = True
                    row["note"] = "已在本地库，已解析出玩家名单"
                else:
                    fetch_error = "本地文件解析不出玩家"
            elif fetch and status == "ok" and info:
                hist = self._fetch_and_parse(gid, info["url"])
                if hist:
                    row.update(hist)
                    fetched = True
                    row["note"] = f"已下载并解析（{info.get('copies', 1)} 份副本可选）"
                else:
                    fetch_error = "下载成功但解析不出玩家"
        except Exception as e:  # noqa: BLE001
            fetch_error = f"{type(e).__name__}: {e}"[:160]
        if fetch_error and not fetched:
            row["note"] = f"解析失败：{fetch_error}（点 ▶ 可重试）"

        self._custom_manual = [r for r in self._custom_manual if r.get("game_id") != gid]
        self._custom_manual.insert(0, row)
        return {
            "ok": True,
            "entry": row,
            "resolved": status == "ok",
            "probe": status,
            "fetched": fetched,
            "fetch_error": fetch_error,
        }

    def _fetch_and_parse(self, gid: int, url: str) -> dict:
        """下载一份回放 → 落进回放库 → 解压 → 解析玩家/地图/模式/玩法包。

        返回可以直接 merge 进行里的字段；下载的文件保留在库里，点 ▶ 不再重下。
        """
        import gzip as _gzip
        import urllib.request

        self.cfg.library_dir.mkdir(parents=True, exist_ok=True)
        tmp = self.cfg.download_dir / f"{PLAYBACK_PREFIX}{gid}.part"
        self.cfg.download_dir.mkdir(parents=True, exist_ok=True)

        req = urllib.request.Request(url, headers={"User-Agent": relay.UA})
        with urllib.request.urlopen(req, timeout=90) as r, open(tmp, "wb") as fh:
            total = 0
            while True:
                chunk = r.read(262144)
                if not chunk:
                    break
                fh.write(chunk)
                total += len(chunk)

        raw = tmp.read_bytes()
        if len(raw) < 4096:
            tmp.unlink(missing_ok=True)
            raise RuntimeError(f"文件过小（{len(raw)} 字节）")

        # 只解一层 gzip → 00 00 ,,AOE4_RE；再多解一层就丢版本头（见开发记录 2.3）
        data = gz.to_playback(raw)
        if not gz.is_playback_format(data):
            tmp.unlink(missing_ok=True)
            raise RuntimeError(f"格式不对（头 {data[:8].hex()}）")

        final = self.cfg.library_dir / f"{PLAYBACK_PREFIX}{gid}"
        tmp.write_bytes(data)
        os.replace(tmp, final)
        # 立刻重扫库存，让「已下载」状态与本列表一致（否则要点播放才认）
        self.rescan()
        return self._parse_bytes(gid, final, data)

    def _parse_file(self, gid: int, path: Path) -> dict:
        """本地已有文件：直接读出来解析（不再下载）。

        这条路专门解决「文件早就在库里、却从来没解析过」的情况 ——
        老版本把「已在本地」当成「不用管」，结果玩家名单永远是空的。
        """
        raw = path.read_bytes()
        data = gz.to_playback(raw) if not gz.is_playback_format(raw) else raw
        return self._parse_bytes(gid, path, data)

    def _parse_bytes(self, gid: int, path: Path, data: bytes) -> dict:
        """从已就绪的回放字节里解析出列表要用的字段。"""
        players = custom.parse_players(data)
        meta = custom.read_meta(path)
        is_custom_match = b"section_cac_conditions" in data
        pack = custom.extract_mode_pack(data) if is_custom_match else {}

        civs = "、".join({p.get("civ_zh") or p.get("civ") or "" for p in players if p.get("civ_zh")})[:80]
        return {
            "path": str(path),
            "file": path.name,
            "size": len(data),
            "map": meta.get("map") or (pack.get("pack_kind") and "自定义") or "—",
            "started_local": meta.get("date") or time.strftime("%Y-%m-%d %H:%M"),
            "civ_zh": civs,
            "players": players,
            "mode_label": custom.infer_mode(players, is_custom_match),
            "pack_id": pack.get("pack_id"),
            "pack_hash": pack.get("pack_hash") or "",
            "pack_kind": pack.get("pack_kind") or "",
            "note": "已下载并解析",
        }

    def play_custom(self, index: int = -1, game_id: int | None = None, path: str | None = None) -> dict:
        """播放自定义对局：按本地路径或 game_id。"""
        src = None
        if path:
            src = Path(path)
        elif game_id:
            src = self.store.best_file(game_id)
            if src is None:
                self._auto_play[game_id] = True
                added = self.enqueue([game_id]).get("added") or []
                return {
                    "ok": True,
                    "stage": "downloading",
                    "queued": bool(added),
                    "message": "本地没有，已开始下载，下完自动播放。",
                }
        if src is None or not src.exists():
            return {"ok": False, "message": "找不到这个回放文件"}

        # rogue/temp 的名字不是 AgeIV_Replay_<id>，投送时保留原名，游戏照样能读
        name = src.stem
        gid = game_id or 0
        res = launcher.play(src, gid, self.cfg.playback_dir, self.cfg.steam_exe, deploy_name=name)
        res["stage"] = "launched" if res.get("ok") else "blocked"
        res["game_id"] = gid or name
        res["source"] = str(src)
        self.last_play = res
        return res

    def probe_availability(self, ids: list[int]) -> dict:
        """给界面标「可下载 / 无副本 / 查不到」——只查地址不下载。

        三态分开报：unknown（请求失败）不能算成「无副本」，
        否则一次网络抖动就会让你以为录像没了。
        """
        owned = self.store.owned()
        need = [i for i in ids if i not in owned]
        ok, missing, unknown = self.downloads.probe_ok_ids(need) if need else ([], [], [])
        st = self.downloads.probe(need) if need else {}
        return {
            "resolved": {
                str(gid): {
                    "copies": (st[gid].get("file") or {}).get("copies"),
                    "has_mine": (st[gid].get("file") or {}).get("has_mine"),
                    "profile_id": (st[gid].get("file") or {}).get("profile_id"),
                }
                for gid in ok
            },
            "unavailable": missing,      # 真没副本
            "unknown": unknown,          # 请求失败，可重试
            "owned": [i for i in ids if i in owned],
            "relay": relay.state(),
        }

    def play(self, game_id: int) -> dict:
        """点播放：版本不符直接拦；本地没有就先下载，下完自动启动。

        stage: blocked（版本不符）/ downloading（已入队）/ launched（已启动）/ error
        """
        # 1) 版本先行 —— 引擎会忽略不符版本的回放，别让人白等一轮下载+启动
        patch = self._patch_of(game_id)
        cur = self._client_build()
        if patch and cur and int(patch) != int(cur):
            res = {
                "ok": False,
                "stage": "blocked",
                "reason": "version_mismatch",
                "game_id": game_id,
                "patch": int(patch),
                "client": int(cur),
                "message": (
                    f"这场回放是 {patch} 版本，当前客户端 {cur}，"
                    "游戏会直接忽略它（界面会停在 DevOnlySkipIntroFlow）。"
                ),
            }
            self.last_play = res
            return res

        # 2) 本地已经有 → 直接启动，绝不再下载
        path = self.store.best_file(game_id)
        if path is not None:
            left = self._auto_play.pop(game_id, False)
            if left:
                print(f"[play] {game_id} 已存在本地，取消残留的自动下载标记", flush=True)
            return self._launch(game_id, path)

        # 3) 索引里没有，但 file 表里可能记录了路径（还没重扫）
        for p in self.store.paths_for(game_id):
            try:
                if p.exists() and p.is_file():
                    self.store.assign(p, game_id, "", "library", p.stat().st_size, p.stat().st_mtime)
                    self.rescan()
                    return self._launch(game_id, p)
            except OSError:
                continue

        # 4) 真的没有 → 入队，下完由 _on_download_done 接着启动
        self._auto_play[game_id] = True
        added = self.enqueue([game_id]).get("added") or []
        res = {
            "ok": True,
            "stage": "downloading",
            "game_id": game_id,
            "queued": bool(added),
            "message": "本地没有，已开始下载，下完自动启动回放。",
        }
        if not added:
            self._auto_play.pop(game_id, None)
            res["stage"] = "error"
            res["message"] = "下载入队失败"
        return res

    def _launch(self, game_id: int, path) -> dict:
        res = launcher.play(path, game_id, self.cfg.playback_dir, self.cfg.steam_exe)
        res["game_id"] = game_id
        res["source"] = str(path)
        res["stage"] = "launched" if res.get("ok") else "blocked"
        if res.get("ok"):
            self.rescan()
            threading.Thread(target=self._confirm_launch, args=(game_id,), daemon=True).start()
        self.last_play = res
        return res

    def _patch_of(self, game_id: int) -> int | None:
        with self.lock:
            for g in self.games:
                if int(g["game_id"]) == int(game_id):
                    return g.get("patch")
        return None

    def _client_build(self) -> int | None:
        """当前客户端 build：读 exe 文件版本，失败退回日志里最近一次打到的值。"""
        if getattr(self, "_build_cache", None) is None:
            self._build_cache = gamever.client_build_from_exe()
        return self._build_cache

    def _confirm_launch(self, game_id: int) -> None:
        res = gamever.watch_after(ms=7000)
        if res.get("ignored"):
            self.last_play = {
                **(self.last_play or {}),
                "version_blocked": {
                    "replay_version": res["replay_version"],
                    "client_build": res["client_build"],
                },
                "warning": (
                    f"游戏拒绝了这场回放：它是 {res['replay_version']} 版本，"
                    f"客户端是 {res['client_build']}。界面停在 DevOnlySkipIntroFlow 就是这个原因。"
                ),
            }
            print(f"[play] {game_id} 版本不符，已被引擎忽略", flush=True)

    def version_map(self) -> dict:
        return {str(k): v for k, v in gamever.ignored_replays().items()}

    def open_in_actor(self, game_id: int) -> dict:
        self._ensure_actor()
        if not self.actor.running():
            self.actor.start("https://aoe4world.com")
            time.sleep(1.5)
        url = f"https://aoe4world.com/players/{self.cfg.profile_id}/games?game={game_id}"
        self.actor.open_page(url)
        return {"ok": True, "url": url, "hint": "在新窗口里点这把游戏的 Watch Replay，链接会被自动记账。"}

    # ---------- 监视 / 拟人回放 路由辅助 ----------
    def route(self, path: str, body: dict | None = None):
        """录制相关的 POST/GET，两个后端共用一份实现。返回 (code, payload)。"""
        body = body or {}
        try:
            if path == "/api/recorder/state":
                return 200, self.recorder_state()
            if path == "/api/recorder/start":
                return 200, self.start_recording()
            if path == "/api/recorder/stop":
                return 200, self.stop_recording()
            if path == "/api/recorder/actions":
                return 200, {"actions": self.recent_actions(int(body.get("n") or 40))}
            if path == "/api/recorder/save":
                return 200, self.save_script(body.get("name") or "")
            if path == "/api/probe":
                return 200, self.probe_availability([int(i) for i in (body.get("game_ids") or [])])
            if path == "/api/custom":
                return 200, self.custom_games()
            if path == "/api/custom/watch":
                return 200, self.start_id_watcher()
            if path == "/api/custom/enrich":
                return 200, self.enrich_custom(body.get("game_ids") or [])
            if path == "/api/custom/hide":
                return 200, self.hide_entries(body.get("keys") or [])
            if path == "/api/custom/unhide":
                return 200, self.unhide_all()
            if path == "/api/custom/pack_name":
                return 200, self.set_pack_name(int(body.get("pack_id") or 0), body.get("name") or "")
            if path == "/api/custom/add":
                return 200, self.add_custom_id(body.get("raw") or "")
            if path == "/api/custom/play":
                return 200, self.play_custom(
                    game_id=int(body["game_id"]) if body.get("game_id") else None,
                    path=body.get("path"),
                )
            if path == "/api/recorder/poll":
                self._ensure_actor()
                return 200, self.recorder.from_live_events()
            if path == "/api/recorder/ingest":
                return 200, self.ingest_external_script(body)
            if path == "/api/mimic/play":
                return 200, self.play_mimic(body.get("name") or "", body.get("mode") or "script", float(body.get("speed") or 1.0))
            if path == "/api/mimic/stop":
                self._ensure_actor()
                return 200, self.mimic.stop()
            if path == "/api/mimic/state":
                self._ensure_actor()
                return 200, self.mimic.state()
        except Exception as e:  # noqa: BLE001
            return 500, {"ok": False, "message": str(e)}
        return 404, {"ok": False, "message": "not found"}

    def export_csv(self) -> str:
        owned = self.store.owned()
        with self.lock:
            games = list(self.games)
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["game_id", "时间", "地图", "模式", "时长", "我方文明", "结果", "已下载", "文件"])
        for g in games:
            gid = int(g["game_id"])
            me = next(
                (
                    (e.get("player") or e)
                    for team in (g.get("teams") or [])
                    for e in team
                    if (e.get("player") or e).get("profile_id") == self.cfg.profile_id
                ),
                {},
            )
            p = self.store.best_file(gid) if gid in owned else None
            w.writerow(
                [
                    gid,
                    _fmt_utc(g.get("started_at")),
                    g.get("map"),
                    g.get("kind"),
                    _mmss(g.get("duration")),
                    civ_name(me.get("civilization") or ""),
                    me.get("result", ""),
                    "是" if gid in owned else "否",
                    str(p) if p else "",
                ]
            )
        return buf.getvalue()


# --------------------------------------------------------------------------
# 两个分支共用的设置写入
# --------------------------------------------------------------------------
def apply_settings(hub: Hub, body: dict) -> dict:
    cfg = hub.cfg
    notes: list[str] = []
    if body.get("profile_id"):
        try:
            pid = int(body["profile_id"])
            if pid != cfg.profile_id:
                cfg.profile_id = pid
                try:
                    cfg.profile_name = world_api.player(pid).get("name") or cfg.profile_name
                except Exception:  # noqa: BLE001
                    pass
        except (TypeError, ValueError):
            notes.append("玩家 ID 不是数字，已忽略")

    for field in ("steam_exe", "ui_browser", "actor_browser", "download_dir", "library_dir"):
        v = body.get(field)
        if v:
            setattr(cfg, field, Path(v) if field.endswith("_dir") else v)
    # 老前端只发 browser_exe：按「面板浏览器」处理（它才是需要 CDP 的那个）
    if body.get("browser_exe") and not body.get("actor_browser"):
        cfg.actor_browser = body["browser_exe"]

    # Steam ID：号主判定全靠它，填错等于所有对局都判成「无法判断」
    sid = (body.get("steam_id") or "").strip()
    if sid and sid != cfg.steam_id:
        if sid.isdigit() and len(sid) >= 15:
            cfg.steam_id = sid
        else:
            notes.append(f"Steam ID 应是一串 17 位数字，已忽略「{sid}」")

    # 轮询间隔：0 = 关闭自动刷新，别把 0 当成「没填」
    if "poll_seconds" in body and body["poll_seconds"] is not None:
        try:
            ps = int(body["poll_seconds"])
            if ps < 0:
                raise ValueError
            cfg.poll_seconds = ps
        except (TypeError, ValueError):
            notes.append("轮询间隔要填 0 或正整数，已忽略")

    # 目录：playback 和 network 是同一次安装的产物，改 playback 时 network 默认跟着同级走
    new_playback = body.get("playback_dir")
    if new_playback:
        cfg.playback_dir = Path(new_playback)
    new_network = body.get("network_dir")
    if new_network:
        cfg.network_dir = Path(new_network)
        cfg._network_pinned = True
    elif new_playback:
        cfg.network_dir = guess_network_dir(cfg.playback_dir)
        cfg._network_pinned = False

    cfg.save()
    hub.downloads.dest_dir = cfg.download_dir
    hub.downloads.library_dir = cfg.library_dir
    hub.refresh(pages=1)
    return {"notes": notes}


# --------------------------------------------------------------------------
# FastAPI 分支
# --------------------------------------------------------------------------
def build_fastapi(hub: Hub):
    from fastapi import FastAPI, HTTPException
    from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
    from pydantic import BaseModel

    app = FastAPI(title="AoE4 Replay Studio", version="1.0.0")

    class IdsBody(BaseModel):
        game_ids: list[int] = []
        force: bool = False

    class PlayBody(BaseModel):
        game_id: int

    class ManualBody(BaseModel):
        url: str
        authorization: str | None = None

    class SettingsBody(BaseModel):
        profile_id: int | None = None
        steam_id: str | None = None         # 号主判定用的 Steam ID
        steam_exe: str | None = None
        ui_browser: str | None = None       # 打开界面：任意浏览器
        actor_browser: str | None = None    # 面板 / 录制：必须 Chromium 系
        browser_exe: str | None = None      # 兼容老前端 → 当作 actor_browser
        download_dir: str | None = None
        library_dir: str | None = None
        playback_dir: str | None = None
        network_dir: str | None = None      # 空 = 跟随 playback 同级
        poll_seconds: int | None = None

    @app.middleware("http")
    async def _post_guard(request, call_next):
        if request.method in ("POST", "PUT", "PATCH", "DELETE"):
            why = _reject_post(request.headers.get("origin"), request.headers.get("content-type"))
            if why:
                return JSONResponse({"ok": False, "message": why}, status_code=403)
        return await call_next(request)

    @app.get("/")
    def index():
        return FileResponse(WEB_DIR / "app.html")

    @app.get("/api/ping")
    def ping():
        """给浏览器扩展 / native host 找端口用：很轻，不碰 API 也不碰数据库。"""
        return {"ok": True, "app": "aoe4-replay-studio", "version": __version__}

    @app.get("/mapthumbs/{name}")
    def thumb(name: str):
        fp = _safe_image(THUMB_DIR, name)
        if fp is None:
            raise HTTPException(status_code=404, detail="not found")
        return FileResponse(fp, media_type="image/png")

    @app.get("/civflags/{name}")
    def civflag(name: str):
        fp = _safe_image(CIV_DIR, name)
        if fp is None:
            raise HTTPException(status_code=404, detail="not found")
        return FileResponse(fp, media_type="image/png")

    @app.get("/api/state")
    def state():
        return hub.state()

    @app.post("/api/refresh")
    def refresh():
        return {"api": hub.refresh(pages=2)}

    class MoreBody(BaseModel):
        pages: int = 1

    @app.post("/api/more")
    def more(body: MoreBody):
        return hub.load_more(pages=max(1, min(body.pages, 10)))

    @app.post("/api/download")
    def download(body: IdsBody):
        return hub.enqueue(body.game_ids, force=body.force)

    class FavBody(BaseModel):
        game_id: int
        favorite: bool | None = None

    class NoteBody(BaseModel):
        game_id: int
        note: str = ""

    @app.get("/api/favorites")
    def favorites():
        return hub.favorites()

    @app.post("/api/favorite")
    def favorite(body: FavBody):
        return hub.toggle_favorite(body.game_id, body.favorite)

    @app.post("/api/favorite/note")
    def favorite_note(body: NoteBody):
        return hub.set_favorite_note(body.game_id, body.note)

    class RecentBody(BaseModel):
        days: int = 7
        limit: int = 20
        probe: bool = True

    @app.post("/api/download/recent")
    def download_recent(body: RecentBody):
        return hub.recent_missing(days=body.days, limit=body.limit, probe=body.probe)

    @app.post("/api/probe")
    def probe(body: IdsBody):
        return hub.probe_availability(body.game_ids)

    @app.post("/api/retry")
    def retry(body: IdsBody):
        hub.downloads.retry(body.game_ids)
        return {"ok": True}

    @app.post("/api/cancel")
    def cancel():
        hub.downloads.cancel_all()
        return {"ok": True}

    @app.get("/api/queue")
    def queue():
        return hub.downloads.snapshot()

    @app.post("/api/play")
    def play(body: PlayBody):
        return hub.play(body.game_id)

    @app.post("/api/actor/start")
    def actor_start():
        return hub.actor_start()

    @app.post("/api/actor/stop")
    def actor_stop():
        return hub.actor_stop()

    @app.post("/api/actor/open")
    def actor_open(body: PlayBody):
        return hub.open_in_actor(body.game_id)

    @app.post("/api/manual_url")
    def manual_url(body: ManualBody):
        try:
            headers = {"Authorization": body.authorization} if body.authorization else {}
            gid = hub.actor.record_manual(body.url, headers)
        except ResolveError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        return {"ok": True, "game_id": gid}

    @app.post("/api/settings")
    def settings(body: SettingsBody):
        res = apply_settings(hub, body.model_dump())
        return {"ok": True, "config": hub.cfg.snapshot(), "notes": res.get("notes") or []}

    @app.get("/api/export")
    def export():
        return PlainTextResponse(
            hub.export_csv(),
            media_type="text/csv; charset=utf-8-sig",
            headers={"Content-Disposition": 'attachment; filename="aoe4_replays.csv"'},
        )

    @app.get("/api/custom")
    def custom_list():
        return hub.custom_games()

    class CustomAdd(BaseModel):
        raw: str = ""

    class CustomPlay(BaseModel):
        path: str | None = None
        game_id: int | None = None

    @app.post("/api/custom/add")
    def custom_add(body: CustomAdd):
        return hub.add_custom_id(body.raw)

    @app.post("/api/custom/play")
    def custom_play(body: CustomPlay):
        return hub.play_custom(path=body.path, game_id=body.game_id)

    @app.post("/api/custom/watch")
    def custom_watch():
        return hub.start_id_watcher()

    class EnrichBody(BaseModel):
        game_ids: list[int] = []

    @app.post("/api/custom/enrich")
    def custom_enrich(body: EnrichBody):
        return hub.enrich_custom(body.game_ids)

    class KeysBody(BaseModel):
        keys: list[str] = []

    @app.post("/api/custom/hide")
    def custom_hide(body: KeysBody):
        return hub.hide_entries(body.keys)

    @app.post("/api/custom/unhide")
    def custom_unhide():
        return hub.unhide_all()

    class RecorderBody(BaseModel):
        name: str | None = None
        mode: str | None = None
        speed: float | None = None
        n: int | None = None
        actions: list[dict] | None = None
        viewport: dict | None = None

    @app.post("/api/recorder/{action}")
    def recorder(action: str, body: RecorderBody | None = None):
        payload = (body.model_dump() if body else {}) or {}
        if action == "ingest":
            payload.setdefault("kind", "lcs-action-script")
        code, data = hub.route(f"/api/recorder/{action}", payload)
        if code >= 400:
            raise HTTPException(status_code=code, detail=data.get("message") if isinstance(data, dict) else data)
        return data

    @app.get("/api/recorder/{action}")
    def recorder_get(action: str):
        code, data = hub.route(f"/api/recorder/{action}", {})
        if code >= 400:
            raise HTTPException(status_code=code, detail=data.get("message") if isinstance(data, dict) else data)
        return data

    @app.post("/api/mimic/{action}")
    def mimic(action: str, body: RecorderBody | None = None):
        code, data = hub.route(f"/api/mimic/{action}", (body.model_dump() if body else {}) or {})
        if code >= 400:
            raise HTTPException(status_code=code, detail=data.get("message") if isinstance(data, dict) else data)
        return data

    @app.get("/api/mimic/state")
    def mimic_state():
        code, data = hub.route("/api/mimic/state", {})
        return data

    @app.get("/api/game/{game_id}")
    def game_detail(game_id: int):
        try:
            return world_api.game_detail(game_id)
        except Exception as e:  # noqa: BLE001
            raise HTTPException(status_code=502, detail=str(e)) from e

    @app.exception_handler(Exception)
    async def _fallback(request, exc):  # noqa: ANN001
        return JSONResponse({"ok": False, "message": str(exc)}, status_code=500)

    return app


# --------------------------------------------------------------------------
# 无框架分支（标准库 http.server）
# --------------------------------------------------------------------------
def build_stdlib(hub: Hub):
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt, *args):  # noqa: N802
            pass

        def _json(self, payload: Any, code: int = 200) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _body(self) -> dict:
            try:
                n = int(self.headers.get("Content-Length") or 0)
            except (TypeError, ValueError):
                return {}
            if n <= 0:
                return {}
            try:
                return json.loads(self.rfile.read(n).decode("utf-8"))
            except Exception:  # noqa: BLE001
                return {}

        def do_GET(self):  # noqa: N802
            u = urlparse(self.path)
            q = parse_qs(u.query)
            try:
                if u.path in ("/", "/index.html"):
                    data = (WEB_DIR / "app.html").read_bytes()
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                    return
                if u.path == "/api/ping":
                    return self._json({"ok": True, "app": "aoe4-replay-studio", "version": __version__})
                if u.path == "/api/state":
                    return self._json(hub.state())
                if u.path.startswith("/mapthumbs/") or u.path.startswith("/civflags/"):
                    base = CIV_DIR if u.path.startswith("/civflags/") else THUMB_DIR
                    # 不 unquote：%2f / %5c 留成普通字符，白名单自然挡住
                    fp = _safe_image(base, u.path.rsplit("/", 1)[-1])
                    if fp is None:
                        return self._json({"ok": False, "message": "not found"}, 404)
                    data = fp.read_bytes()
                    self.send_response(200)
                    self.send_header("Content-Type", "image/png")
                    self.send_header("Content-Length", str(len(data)))
                    self.send_header("Cache-Control", "max-age=86400")
                    self.end_headers()
                    self.wfile.write(data)
                    return
                if u.path == "/api/queue":
                    return self._json(hub.downloads.snapshot())
                if u.path == "/api/custom":
                    return self._json(hub.custom_games())
                if u.path == "/api/favorites":
                    return self._json(hub.favorites())
                if u.path == "/api/export":
                    data = hub.export_csv().encode("utf-8-sig")
                    self.send_response(200)
                    self.send_header("Content-Type", "text/csv; charset=utf-8")
                    self.send_header("Content-Disposition", 'attachment; filename="aoe4_replays.csv"')
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                    return
                if u.path.startswith("/api/game/"):
                    gid = int(u.path.rsplit("/", 1)[-1])
                    return self._json(world_api.game_detail(gid))
                if u.path.startswith("/api/mimic/") or u.path.startswith("/api/recorder/"):
                    code, data = hub.route(u.path, {})
                    return self._json(data, code)
                return self._json({"ok": False, "message": "not found"}, 404)
            except Exception as e:  # noqa: BLE001
                return self._json({"ok": False, "message": str(e)}, 500)

        def do_POST(self):  # noqa: N802
            u = urlparse(self.path)
            why = _reject_post(self.headers.get("Origin"), self.headers.get("Content-Type"))
            if why:
                # 拒收前把请求体读掉，否则 keep-alive 连接里剩下的字节会被当成下一个请求
                try:
                    n = int(self.headers.get("Content-Length") or 0)
                    if 0 < n <= MAX_BODY:
                        self.rfile.read(n)
                except (TypeError, ValueError, OSError):
                    pass
                return self._json({"ok": False, "message": why}, 403)
            try:
                n = int(self.headers.get("Content-Length") or 0)
            except (TypeError, ValueError):
                n = 0
            if n > MAX_BODY:
                return self._json({"ok": False, "message": "请求体过大"}, 413)
            body = self._body()
            try:
                if u.path == "/api/refresh":
                    return self._json({"api": hub.refresh(pages=2)})
                if u.path == "/api/more":
                    return self._json(hub.load_more(pages=max(1, min(int(body.get("pages") or 1), 10))))
                if u.path == "/api/download":
                    return self._json(hub.enqueue(
                        [int(i) for i in body.get("game_ids", [])],
                        force=bool(body.get("force")),
                    ))
                if u.path == "/api/favorites":
                    return self._json(hub.favorites())
                if u.path == "/api/favorite":
                    return self._json(hub.toggle_favorite(
                        int(body["game_id"]),
                        body.get("favorite") if "favorite" in body else None,
                    ))
                if u.path == "/api/favorite/note":
                    return self._json(hub.set_favorite_note(int(body["game_id"]), body.get("note") or ""))
                if u.path == "/api/download/recent":
                    return self._json(hub.recent_missing(
                        days=int(body.get("days") or 0),
                        limit=int(body.get("limit") or 0),
                        probe=bool(body.get("probe", True)),
                    ))
                if u.path == "/api/probe":
                    return self._json(hub.probe_availability([int(i) for i in body.get("game_ids", [])]))
                if u.path == "/api/custom/add":
                    return self._json(hub.add_custom_id(body.get("raw") or ""))
                if u.path == "/api/custom/play":
                    return self._json(hub.play_custom(
                        game_id=int(body["game_id"]) if body.get("game_id") else None,
                        path=body.get("path"),
                    ))
                if u.path == "/api/custom/watch":
                    return self._json(hub.start_id_watcher())
                if u.path == "/api/custom/enrich":
                    return self._json(hub.enrich_custom(body.get("game_ids") or []))
                if u.path == "/api/custom/hide":
                    return self._json(hub.hide_entries(body.get("keys") or []))
                if u.path == "/api/custom/unhide":
                    return self._json(hub.unhide_all())
                if u.path == "/api/custom/pack_name":
                    return self._json(hub.set_pack_name(int(body.get("pack_id") or 0), body.get("name") or ""))
                if u.path == "/api/retry":
                    hub.downloads.retry([int(i) for i in body.get("game_ids", [])])
                    return self._json({"ok": True})
                if u.path == "/api/cancel":
                    hub.downloads.cancel_all()
                    return self._json({"ok": True})
                if u.path == "/api/play":
                    return self._json(hub.play(int(body["game_id"])))
                if u.path == "/api/actor/start":
                    return self._json(hub.actor_start())
                if u.path == "/api/actor/stop":
                    return self._json(hub.actor_stop())
                if u.path == "/api/actor/open":
                    return self._json(hub.open_in_actor(int(body["game_id"])))
                if u.path == "/api/manual_url":
                    headers = {"Authorization": body["authorization"]} if body.get("authorization") else {}
                    return self._json({"ok": True, "game_id": hub.actor.record_manual(body["url"], headers)})
                if u.path == "/api/settings":
                    res = apply_settings(hub, body)
                    return self._json({"ok": True, "config": hub.cfg.snapshot(), "notes": res.get("notes") or []})
                if u.path.startswith("/api/mimic/") or u.path.startswith("/api/recorder/"):
                    code, data = hub.route(u.path, body)
                    return self._json(data, code)
                return self._json({"ok": False, "message": "not found"}, 404)
            except Exception as e:  # noqa: BLE001
                return self._json({"ok": False, "message": str(e)}, 500)

    return ThreadingHTTPServer((hub.cfg.host, hub.cfg.port), Handler)


def make_server(hub: Hub, prefer: str = "auto"):
    if prefer != "stdlib":
        try:
            import fastapi  # noqa: F401
            import uvicorn  # noqa: F401

            return build_fastapi(hub), "fastapi"
        except Exception:  # noqa: BLE001
            if prefer == "fastapi":
                raise
    return build_stdlib(hub), "stdlib"


