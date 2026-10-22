# -*- coding: utf-8 -*-
"""路径与账号配置。全部可被环境变量覆盖，改 data/config.json 也生效。"""
from __future__ import annotations

import json
import os
import re
import shutil
import winreg
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)
CONFIG_PATH = DATA_DIR / "config.json"

STEAM_APP_ID = "1466860"
GAME_EXE = "RelicCardinal.exe"

DEFAULTS = {
    # 首次运行（没有 config.json）时用的占位值。
    # profile_id 用 AoE4World 的公开模板档案 14579308 只是为了让界面一打开有内容看；
    # 使用者应当立刻在「设置」里换成自己的 profile_id / steam_id。
    "profile_id": 14579308,
    "profile_name": "玩家",
    "steam_id": "",                  # 空 = 未设置，号主判定一律返回「无法判断」
    "host": "127.0.0.1",
    "port": 8787,
    "lang": "zh",
    "poll_seconds": 60,              # 页面空闲时后台自动刷新间隔（秒），0 = 关闭
    "download_dir": "",              # 空 = <项目>/data/replays
    "library_dir": "",               # 空 = 探测到的「回放库」
    "playback_dir": "",              # 空 = 自动探测 AoE4 playback 目录
    "network_dir": "",               # 空 = 自动探测 AoE4 network 目录（自定义对局主要来源）
    "steam_exe": "",                 # 空 = 注册表 / 常见路径
    "ui_browser": "",                # 空 = 自动探测（任意浏览器，含 Firefox）
    "actor_browser": "",             # 空 = 自动探测（必须 Chromium 系）
    "browser_exe": "",               # 老字段，读时兼容，新保存不再单独用
}


def guess_network_dir(playback_dir: Path) -> Path:
    """network 目录通常就在 playback 的同级。

    用户把 playback 改到别处时，network 也该跟着走 —— 两者是同一次安装的产物。
    同级不存在就退回默认探测。
    """
    try:
        cand = Path(playback_dir).parent / "network"
        if cand.exists():
            return cand
    except Exception:  # noqa: BLE001
        pass
    return detect_network_dir()


def _load_file() -> dict:
    if CONFIG_PATH.exists():
        try:
            return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def detect_documents() -> Path:
    """OneDrive 重定向后 Documents 可能在 OneDrive 下，三种都试。"""
    home = Path(os.path.expanduser("~"))
    cands = [home / "Documents"]
    for env in ("OneDrive", "OneDriveConsumer", "OneDriveCommercial"):
        v = os.environ.get(env)
        if v:
            cands.append(Path(v) / "Documents")
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders",
        ) as k:
            v, _ = winreg.QueryValueEx(k, "Personal")
            cands.insert(0, Path(os.path.expandvars(v)))
    except Exception:
        pass
    for c in cands:
        if (c / "My Games" / "Age of Empires IV").exists():
            return c
    return cands[0]


def detect_playback_dir() -> Path:
    return detect_documents() / "My Games" / "Age of Empires IV" / "playback"


def detect_network_dir() -> Path:
    """network 目录：游戏下载服务器回放时落 M_<id>_<hash> 的地方。

    在游戏里点开别人的对局，第一次会下载几秒并写到这个目录，
    文件名里直接带 matchhistory_id —— 这是拿 ID 最直接的来源。
    """
    return detect_documents() / "My Games" / "Age of Empires IV" / "network"


def detect_library_dir() -> Path:
    """回放库目录：优先沿用「上一次用过的旧启动器留下的库」，否则用项目内 data/library。

    老版本这里写死了 `ROOT.parent / "启动器" / "回放库"` —— 那是原作者的目录布局。
    分发版里改成「存在才用」，不存在就用本地默认，任何人拿到都不会因为缺目录出错。
    """
    for cand in (ROOT.parent / "启动器" / "回放库", ROOT.parent / "回放库"):
        if cand.exists():
            return cand
    return DATA_DIR / "library"


def detect_steam() -> str:
    for key in (r"Software\Valve\Steam", r"Software\WOW6432Node\Valve\Steam"):
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key) as k:
                p = winreg.QueryValueEx(k, "SteamExe")[0]
                if p and os.path.exists(p):
                    return p
        except Exception:
            pass
    for c in (
        r"C:\Program Files (x86)\Steam\steam.exe",
        r"C:\Program Files\Steam\steam.exe",
        r"D:\Steam\steam.exe",
        r"E:\Steam\steam.exe",
    ):
        if os.path.exists(c):
            return c
    return ""


def detect_browser() -> str:
    """探测 Chromium 系浏览器（浏览器面板 / CDP 录制需要它）。

    注意：这里**故意不返回 Firefox**。带 --remote-debugging-port 的 CDP 只有
    Chromium 内核认，Firefox 用的是另一套协议，塞进去只会连不上。
    """
    for c in (
        os.path.join(os.environ.get("PROGRAMFILES", ""), "Google", "Chrome", "Application", "chrome.exe"),
        os.path.join(os.environ.get("PROGRAMFILES(X86)", ""), "Google", "Chrome", "Application", "chrome.exe"),
        os.path.join(os.environ.get("LOCALAPPDATA", ""), "Google", "Chrome", "Application", "chrome.exe"),
        os.path.join(os.environ.get("PROGRAMFILES", ""), "Microsoft", "Edge", "Application", "msedge.exe"),
        os.path.join(os.environ.get("PROGRAMFILES(X86)", ""), "Microsoft", "Edge", "Application", "msedge.exe"),
        os.path.join(os.environ.get("PROGRAMFILES", ""), "BraveSoftware", "Brave-Browser", "Application", "brave.exe"),
    ):
        if c and os.path.exists(c):
            return c
    return shutil.which("chrome") or shutil.which("msedge") or ""


def detect_any_browser() -> str:
    """探测任意能开网页的浏览器 —— 只用来「打开界面 / 打开外部链接」，不需要 CDP。"""
    exe = detect_browser()
    if exe:
        return exe
    for c in (
        os.path.join(os.environ.get("PROGRAMFILES", ""), "Mozilla Firefox", "firefox.exe"),
        os.path.join(os.environ.get("PROGRAMFILES(X86)", ""), "Mozilla Firefox", "firefox.exe"),
        os.path.join(os.environ.get("LOCALAPPDATA", ""), "Mozilla Firefox", "firefox.exe"),
    ):
        if c and os.path.exists(c):
            return c
    return shutil.which("firefox") or shutil.which("chrome") or shutil.which("msedge") or ""


def browser_supports_cdp(exe: str) -> bool:
    """这个浏览器能不能跑 CDP（--remote-debugging-port）。"""
    if not exe:
        return False
    name = os.path.basename(exe).lower()
    return name in {"chrome.exe", "msedge.exe", "brave.exe", "chromium.exe",
                    "vivaldi.exe", "opera.exe", "thorium.exe"} or "chrome" in name


def _safe_dir(raw: str | None, fallback: Path) -> Path:
    r"""把配置里的目录字符串转成安全路径。

    要防的坑：`Path("D:") / "x"` 得到的是 `D:x`（相对路径！），
    程序在 `D:\something` 下运行时会被解析成 `D:\something\D\x` ——
    在项目目录里平白多出一个 `D\` 文件夹，而且里面是真实回放文件。
    残值来源通常是手工改 config.json 写了一半被中断。

    判定：路径必须「有盘符 + 绝对」，否则一律退回默认目录。
    """
    s = (raw or "").strip().strip('"')
    if not s:
        return fallback
    p = Path(s)
    # Windows 下 D:、D:foo 这类不是绝对路径，不能用
    if not p.is_absolute() and not s.startswith(("\\\\", "/")):
        return fallback
    if re.fullmatch(r"[A-Za-z]:", s):
        return fallback
    return p


def _as_int(raw, fallback: int, lo: int | None = None, hi: int | None = None) -> int:
    """把配置里的值转成整数，坏值一律退回默认。

    手改 config.json 写错一个字符（"port": "87 87"、留成 ""）不该让整个程序
    在启动那一秒抛 ValueError —— 用户看到的只会是闪退，不知道错在哪。
    """
    try:
        v = int(str(raw).strip())
    except (TypeError, ValueError):
        return fallback
    if lo is not None and v < lo:
        return fallback
    if hi is not None and v > hi:
        return fallback
    return v


class Config:
    def __init__(self) -> None:
        self.file = _load_file()
        self.data_dir = DATA_DIR
        self.profile_id = _as_int(self.file.get("profile_id"), DEFAULTS["profile_id"], lo=1)
        self.profile_name = self.file.get("profile_name", DEFAULTS["profile_name"])
        self.host = os.environ.get("AOE4RS_HOST", self.file.get("host", DEFAULTS["host"]))
        self.port = _as_int(os.environ.get("AOE4RS_PORT", self.file.get("port", DEFAULTS["port"])),
                            DEFAULTS["port"], lo=1, hi=65535)
        self.lang = self.file.get("lang", DEFAULTS["lang"])
        self.poll_seconds = _as_int(self.file.get("poll_seconds"), DEFAULTS["poll_seconds"], lo=0)
        self.download_dir = _safe_dir(self.file.get("download_dir"), DATA_DIR / "replays")
        self.library_dir = _safe_dir(self.file.get("library_dir"), detect_library_dir())
        self.playback_dir = _safe_dir(self.file.get("playback_dir"), detect_playback_dir())
        # network 可单独指定；没指定就跟 playback 同级，跟着 playback 一起搬
        self._network_pinned = bool(self.file.get("network_dir"))
        if self._network_pinned:
            self.network_dir = _safe_dir(self.file["network_dir"], detect_network_dir())
        else:
            self.network_dir = guess_network_dir(self.playback_dir)
        self.steam_exe = self.file.get("steam_exe") or detect_steam()
        # 两个浏览器配置分开（2026-10-22 拆分）：
        #   ui_browser    —— 打开界面 / 打开外部网页，任意浏览器都行，Firefox 也行
        #   actor_browser —— 浏览器面板 + CDP 录制，必须 Chromium 系
        # 以前合并成一个 browser_exe，结果指定 Firefox 看界面会把浏览器面板一起弄坏。
        legacy = self.file.get("browser_exe") or ""
        self.ui_browser = (
            self.file.get("ui_browser")
            or (legacy if legacy and os.path.exists(legacy) else detect_any_browser())
        )
        if self.file.get("actor_browser"):
            self.actor_browser = self.file["actor_browser"]
        elif browser_supports_cdp(legacy):
            # 老配置里的 browser_exe 是 Chromium 系 → 直接接着用
            self.actor_browser = legacy
        else:
            self.actor_browser = detect_browser()

        self.profile = DATA_DIR / "browser-profile"      # 独立浏览器配置（不碰日常 Chrome）
        # 手改配置时很容易把 17 位数字写成 JSON number → 读出来是 int，和字符串比较永远不等，
        # 结果每场都判成「无法判断」。统一成字符串再比。
        self.steam_id = str(self.file.get("steam_id") or DEFAULTS["steam_id"]).strip()
        self.db_path = DATA_DIR / "replay.db"
        for d in (self.download_dir, self.library_dir, self.playback_dir, self.profile, self.network_dir):
            try:
                d.mkdir(parents=True, exist_ok=True)
            except OSError:
                pass

    def save(self) -> None:
        payload = dict(DEFAULTS)
        payload.update(
            {
                "profile_id": self.profile_id,
                "profile_name": self.profile_name,
                "steam_id": self.steam_id,
                "host": self.host,
                "port": self.port,
                "lang": self.lang,
                "poll_seconds": self.poll_seconds,
                "download_dir": str(self.download_dir),
                "library_dir": str(self.library_dir),
                "playback_dir": str(self.playback_dir),
                # network 只在用户明确指定时才落盘；留空表示「跟随 playback 同级」
                "network_dir": str(self.network_dir) if self._network_pinned else "",
                "steam_exe": self.steam_exe,
                # 新版写两个字段；browser_exe 留空不再写，避免与拆分后的语义打架
                "ui_browser": self.ui_browser,
                "actor_browser": self.actor_browser,
                "browser_exe": self.actor_browser,
            }
        )
        tmp = CONFIG_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, CONFIG_PATH)

    def snapshot(self) -> dict:
        def _count(d: Path, pattern: str = "*") -> int:
            try:
                return sum(1 for _ in Path(d).glob(pattern))
            except OSError:
                return 0

        return {
            "profile_id": self.profile_id,
            "profile_name": self.profile_name,
            "steam_id": self.steam_id,
            "download_dir": str(self.download_dir),
            "library_dir": str(self.library_dir),
            "playback_dir": str(self.playback_dir),
            "network_dir": str(self.network_dir),
            "steam_exe": self.steam_exe,
            # 兼容老前端：browser_exe 现在等于 actor_browser
            "browser_exe": self.actor_browser,
            "ui_browser": self.ui_browser,
            "actor_browser": self.actor_browser,
            "poll_seconds": self.poll_seconds,
            "playback_exists": self.playback_dir.exists(),
            "network_exists": self.network_dir.exists(),
            "network_files": _count(self.network_dir, "M_*"),
            "steam_exists": bool(self.steam_exe and os.path.exists(self.steam_exe)),
            "browser_exists": bool(self.actor_browser and os.path.exists(self.actor_browser)),
            "ui_browser_exists": bool(self.ui_browser and os.path.exists(self.ui_browser)),
            "actor_browser_supports_cdp": browser_supports_cdp(self.actor_browser),
        }
