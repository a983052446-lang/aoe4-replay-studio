# -*- coding: utf-8 -*-
"""本地回放库存与 game_id 对账。

三个来源统一成一个「库存」：
  * playback/          游戏自己的目录，文件名 AgeIV_Replay_<game_id>
  * library/           回放库（历史文件都堆在这里）
  * relink/            指回上面两者的软链接（不占空间，让两边互相可见）

能识别出 game_id 的条目就是「已下载」。识别不出来但确实见过这个文件的，
会记一条 hash 指纹，保证下次还是同一个 game_id（避免重复下载）。
"""
from __future__ import annotations

import functools
import hashlib
import os
import sqlite3
import threading
import time
from pathlib import Path
from typing import Iterable

from . import gz

PLAYBACK_PREFIX = "AgeIV_Replay_"
SKIP_NAMES = {"temp.rec", "temp_campaign.rec", "rogue", "relink"}
HASH_CHUNK = 1 << 20


def _looks_like_replay(path: Path) -> bool:
    name = path.name
    if name.startswith(PLAYBACK_PREFIX):
        return True
    low = name.lower()
    if low.endswith(".gz") or low.endswith(".rec") or low.endswith(".replay"):
        return True
    if gz.parse_name(path)[0]:
        return True
    return False


def file_fingerprint(path: Path) -> str:
    """体积 + 头 64KB + 尾 64KB 的 sha256，便宜且足够区分。"""
    size = path.stat().st_size
    h = hashlib.sha256()
    h.update(str(size).encode())
    try:
        with open(path, "rb") as fh:
            h.update(fh.read(65536))
            if size > 131072:
                fh.seek(-65536, os.SEEK_END)
                h.update(fh.read(65536))
    except OSError:
        pass
    return h.hexdigest()


def _serialized(fn):
    """把一次 Store 调用串起来，出错就回滚。

    这个连接是 check_same_thread=False 的单条 sqlite3 连接，同时被 HTTP 请求线程、
    下载完成回调和 playback/network 两个监视线程使用。sqlite 的连接本身不是线程安全的：
    scan() 里那一串 INSERT 可能被另一个线程的 commit() 拦腰截断，异常退出时还会把
    事务一直挂在那儿（后面所有写入都卡在同一个未提交事务里）。
    """
    @functools.wraps(fn)
    def wrapper(self, *args, **kwargs):
        with self._lock:
            try:
                return fn(self, *args, **kwargs)
            except Exception:
                try:
                    self.db.rollback()
                except Exception:  # noqa: BLE001
                    pass
                raise

    return wrapper


class Store:
    def __init__(self, db_path: Path):
        self.db_path = db_path
        self._lock = threading.RLock()
        self.db = sqlite3.connect(str(db_path), check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(
            """
            CREATE TABLE IF NOT EXISTS files(
                path TEXT PRIMARY KEY,
                game_id INTEGER,
                fp TEXT,
                origin TEXT,
                size INTEGER,
                mtime REAL,
                first_seen REAL
            );
            CREATE INDEX IF NOT EXISTS idx_files_game ON files(game_id);
            CREATE INDEX IF NOT EXISTS idx_files_fp ON files(fp);
            CREATE TABLE IF NOT EXISTS games(
                game_id INTEGER PRIMARY KEY,
                payload TEXT,
                first_seen REAL
            );
            -- 收藏：game_id 是唯一键，note 是用户自己写的说明
            CREATE TABLE IF NOT EXISTS favorites(
                game_id INTEGER PRIMARY KEY,
                note TEXT DEFAULT '',
                snapshot TEXT DEFAULT '',
                created REAL,
                updated REAL
            );
            -- 「从列表移除」：只是不显示，本地文件一个都不删。
            -- 用 key 而不是 game_id，因为 rogue/*.rec 这类条目没有 game_id，
            -- 只能按「路径 + 文件名」识别。
            CREATE TABLE IF NOT EXISTS hidden(
                key TEXT PRIMARY KEY,
                game_id INTEGER,
                reason TEXT DEFAULT '',
                created REAL
            );
            """
        )
        self.db.commit()

    # ---------- 指纹 ----------
    @_serialized
    def lookup_fp(self, fp: str) -> int | None:
        row = self.db.execute(
            "SELECT game_id FROM files WHERE fp=? AND game_id IS NOT NULL LIMIT 1", (fp,)
        ).fetchone()
        return int(row["game_id"]) if row else None

    @_serialized
    def assign(self, path: Path, game_id: int | None, fp: str, origin: str, size: int, mtime: float) -> None:
        self.db.execute(
            """INSERT INTO files(path,game_id,fp,origin,size,mtime,first_seen)
               VALUES(?,?,?,?,?,?,?)
               ON CONFLICT(path) DO UPDATE SET
                 game_id=excluded.game_id,
                 fp=excluded.fp, origin=excluded.origin, size=excluded.size,
                 mtime=excluded.mtime""",
            (str(path), game_id, fp, origin, size, mtime, time.time()),
        )

    # ---------- 库存扫描 ----------
    @_serialized
    def scan(self, playback_dir: Path, library_dir: Path) -> None:
        seen_paths: set[str] = set()
        for folder, origin in ((playback_dir, "playback"), (library_dir, "library")):
            if not folder.exists():
                continue
            for entry in folder.iterdir():
                name = entry.name
                if name in SKIP_NAMES or name.startswith("."):
                    continue
                try:
                    is_link = entry.is_symlink()
                    target = entry.resolve() if is_link else entry
                    if not target.is_file():
                        continue
                    if not is_link and not _looks_like_replay(target):
                        continue
                except OSError:
                    continue
                key = str(entry)
                seen_paths.add(key)
                try:
                    st = target.stat()
                except OSError:
                    continue

                gid = None
                if name.startswith(PLAYBACK_PREFIX) and name[len(PLAYBACK_PREFIX):].isdigit():
                    gid = int(name[len(PLAYBACK_PREFIX):])
                else:
                    matched, gid_name, _ = gz.parse_name(target)
                    if matched:
                        gid = gid_name

                fp = file_fingerprint(target)
                if gid is None:
                    gid = self.lookup_fp(fp)
                    if gid is not None:
                        self._maybe_upgrade(playback_dir, library_dir, target, gid, fp, origin)
                self.assign(entry, gid, fp, origin, st.st_size, st.st_mtime)
        # 清掉已消失的（软链接被删）
        rows = self.db.execute("SELECT path FROM files").fetchall()
        for r in rows:
            if r["path"] not in seen_paths and not Path(r["path"]).exists():
                self.db.execute("DELETE FROM files WHERE path=?", (r["path"],))
        self.db.commit()

    def _maybe_upgrade(self, playback_dir: Path, library_dir: Path, src: Path, gid: int, fp: str, origin: str) -> None:
        """认领出来的 game_id 顺手补一个标准名，下次就不用再算指纹。"""
        target_dir = playback_dir if origin == "playback" else library_dir
        std = target_dir / f"{PLAYBACK_PREFIX}{gid}"
        if std.exists():
            return
        try:
            os.link(src, std)
        except OSError:
            try:
                if not os.path.islink(std):
                    os.symlink(src, std)
            except OSError:
                return
        self.assign(std, gid, fp, origin, src.stat().st_size, src.stat().st_mtime)

    # ---------- 查询 ----------
    @_serialized
    def owned(self) -> dict[int, dict]:
        rows = self.db.execute(
            """SELECT game_id, COUNT(*) n, MAX(size) size, GROUP_CONCAT(origin) origins,
                      MAX(mtime) mtime
               FROM files WHERE game_id IS NOT NULL GROUP BY game_id"""
        ).fetchall()
        out: dict[int, dict] = {}
        for r in rows:
            out[int(r["game_id"])] = {
                "files": r["n"],
                "size": r["size"],
                "origins": sorted(set((r["origins"] or "").split(","))),
                "mtime": r["mtime"],
            }
        return out

    @_serialized
    def paths_for(self, game_id: int) -> list[Path]:
        rows = self.db.execute(
            "SELECT path FROM files WHERE game_id=? ORDER BY mtime DESC", (game_id,)
        ).fetchall()
        return [Path(r["path"]) for r in rows]

    @_serialized
    def best_file(self, game_id: int) -> Path | None:
        """优先取 playback 里的标准名（游戏直接可读），其次回放库，最后任意。"""
        for p in self.paths_for(game_id):
            if p.name == f"{PLAYBACK_PREFIX}{game_id}" and p.exists():
                return p
        for p in self.paths_for(game_id):
            try:
                if p.exists() and p.is_file():
                    return p
            except OSError:
                continue
        return None

    @_serialized
    def cache_games(self, games: Iterable[dict]) -> None:
        import json

        now = time.time()
        for g in games:
            self.db.execute(
                """INSERT INTO games(game_id,payload,first_seen) VALUES(?,?,?)
                   ON CONFLICT(game_id) DO UPDATE SET payload=excluded.payload""",
                (int(g["game_id"]), json.dumps(g, ensure_ascii=False), now),
            )
        self.db.commit()

    @_serialized
    def cached_games(self) -> list[dict]:
        import json

        rows = self.db.execute("SELECT payload FROM games").fetchall()
        return [json.loads(r["payload"]) for r in rows]

    # ---------- 收藏 ----------
    @_serialized
    def favorite_ids(self) -> set[int]:
        rows = self.db.execute("SELECT game_id FROM favorites").fetchall()
        return {int(r["game_id"]) for r in rows}

    @_serialized
    def favorites(self) -> list[dict]:
        """收藏列表，按最近更新倒序。snapshot 是收藏当时的对局摘要（防止日后 API 拉不到就变空白）。"""
        import json

        rows = self.db.execute(
            "SELECT game_id,note,snapshot,created,updated FROM favorites ORDER BY updated DESC"
        ).fetchall()
        out = []
        for r in rows:
            snap = {}
            try:
                snap = json.loads(r["snapshot"] or "{}")
            except Exception:  # noqa: BLE001
                snap = {}
            out.append(
                {
                    "game_id": int(r["game_id"]),
                    "note": r["note"] or "",
                    "snapshot": snap,
                    "created": r["created"],
                    "updated": r["updated"],
                }
            )
        return out

    @_serialized
    def add_favorite(self, game_id: int, snapshot: dict | None = None) -> dict:
        import json

        now = time.time()
        self.db.execute(
            """INSERT INTO favorites(game_id,note,snapshot,created,updated)
               VALUES(?, '', ?, ?, ?)
               ON CONFLICT(game_id) DO UPDATE SET
                 snapshot=CASE WHEN excluded.snapshot<>'' THEN excluded.snapshot ELSE favorites.snapshot END,
                 updated=favorites.updated""",
            (int(game_id), json.dumps(snapshot or {}, ensure_ascii=False), now, now),
        )
        self.db.commit()
        return {"game_id": int(game_id), "favorite": True}

    @_serialized
    def remove_favorite(self, game_id: int) -> dict:
        self.db.execute("DELETE FROM favorites WHERE game_id=?", (int(game_id),))
        self.db.commit()
        return {"game_id": int(game_id), "favorite": False}

    @_serialized
    def set_favorite_note(self, game_id: int, note: str) -> dict:
        now = time.time()
        cur = self.db.execute("SELECT game_id FROM favorites WHERE game_id=?", (int(game_id),)).fetchone()
        if not cur:
            self.add_favorite(game_id)
        self.db.execute(
            "UPDATE favorites SET note=?, updated=? WHERE game_id=?",
            ((note or "")[:4000], now, int(game_id)),
        )
        self.db.commit()
        return {"game_id": int(game_id), "note": (note or "")[:4000]}

    # ---------- 从列表移除（不删文件）----------
    @_serialized
    def hidden_keys(self) -> set[str]:
        rows = self.db.execute("SELECT key FROM hidden").fetchall()
        return {r["key"] for r in rows}

    @_serialized
    def hide(self, keys: list[str], game_ids: list[int] | None = None, reason: str = "") -> dict:
        """把条目从列表里藏起来。**不碰磁盘上的任何文件。**"""
        now = time.time()
        n = 0
        gid_of = dict(zip(keys, game_ids or [None] * len(keys)))
        for k in keys:
            if not k:
                continue
            self.db.execute(
                """INSERT INTO hidden(key,game_id,reason,created) VALUES(?,?,?,?)
                   ON CONFLICT(key) DO UPDATE SET reason=excluded.reason, created=excluded.created""",
                (k, gid_of.get(k), reason, now),
            )
            n += 1
        self.db.commit()
        return {"hidden": n, "total": len(self.hidden_keys())}

    @_serialized
    def unhide(self, keys: list[str] | None = None) -> dict:
        if keys:
            for k in keys:
                self.db.execute("DELETE FROM hidden WHERE key=?", (k,))
        else:
            self.db.execute("DELETE FROM hidden")
        self.db.commit()
        return {"total": len(self.hidden_keys())}

    @_serialized
    def close(self) -> None:
        try:
            self.db.commit()
            self.db.close()
        except Exception:
            pass


def link_into(src: Path, dst: Path) -> str:
    """把 src 以软链接方式放进 dst（同盘秒完成，跨盘也支持）。"""
    if dst.exists() or dst.is_symlink():
        try:
            if dst.is_symlink() and dst.resolve() == src.resolve():
                return "exists"
        except OSError:
            pass
        dst = dst.with_name(dst.name + ".alt")
    try:
        os.symlink(src, dst)
        return "symlink"
    except OSError:
        try:
            os.link(src, dst)
            return "hardlink"
        except OSError:
            return "failed"
