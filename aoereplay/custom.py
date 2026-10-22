# -*- coding: utf-8 -*-
"""自定义 / 非排位对局。

AoE4World 只收录匹配对局（qm_*/rm_*），自定义对局的 ID 根本不在它那儿。
能拿到的一条来源是本地文件，两条是半自动的：

1. **本地 rogue/**：游戏给自定义/战役对局单独存的 `.rec`，
   文件名是「日期-地图-文明」，可直接投送 playback 播放。
   格式是 `\\x00\\x00<xx>\\x1dAOE4_RE`（和排位那份 `,,AOE4_RE` 略不同），
   同样被引擎接受。

2. **temp.rec**：最近一局的临时录制，`\\x00\\x00,,AOE4_RE` 格式。

3. **手动 ID / URL**：你从游戏内对局历史里抄到 matchhistory_id，
   贴进来就能用 Relic 官方接口换签名地址下载（和排位完全同一条下载链路）。

判定「是不是自定义」的标准：
    AoE4World 的 games 列表里没有这个 game_id，且本地文件属于 rogue/
"""
from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

from .civs import CIV_ZH, civ_name, civ_slug, resolve_civ

ROGUE_RE = re.compile(
    r"^(?P<date>\d{4}-\d{2}-\d{2})-(?P<time>\d{2}H\d{2}M)-(?P<map>.+)-(?P<civ>.+)\.rec$"
)
NET_RE = re.compile(r"^M_(\d+)_([0-9a-fA-F]{64})")


def _fmt_mtime(p: Path) -> str:
    try:
        return datetime.fromtimestamp(p.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
    except OSError:
        return ""


def _parse_rogue_name(name: str) -> dict:
    m = ROGUE_RE.match(name)
    if not m:
        return {}
    d, t = m.group("date"), m.group("time").replace("H", ":")
    started = ""
    try:
        started = datetime.strptime(f"{d} {t}", "%Y-%m-%d %H:%M").isoformat()
    except ValueError:
        started = ""
    return {
        "started_at": started,
        "map": m.group("map"),
        "civ_zh": m.group("civ"),
    }


# AoE4 的模式只有四类：
#   人机（skirmish/ai）、排位（rm_*）、快匹（qm_*）、自定义（其余全算）
RANKED_PREFIX = "rm_"
QUICK_PREFIX = "qm_"


# 已知文明 id（回放里的写法），用于过滤设置项名
KNOWN_CIVS = {
    "english", "french", "rus", "mongols", "chinese", "holy_roman_empire", "hre",
    "abbasid", "abbasid_dynasty", "delhi_sultanate", "delhi", "khmer", "malians", "malian",
    "ottomans", "ottoman", "byzantines", "byzantine", "japanese", "ayyubids", "ayyubid",
    "jeanne_darc", "zhu_xis_legacy", "order_of_the_dragon", "house_of_lancaster",
    "tughlaq_dynasty", "sengoku_daimyo", "macedonian_dynasty", "knights_templar",
    "templar", "golden_horde", "sultans_army", "army_of_the_sultan", "hindu_rajas",
    "jin_dynasty", "vijayanagara", "danish", "tarkhan", "mongol",
}


_PLAYERS_CACHE: dict[str, list] = {}


def _players_cache_path(network_dir: Path) -> Path:
    return network_dir.parent / "lcs_players_cache.json"


def _players_cached(p: Path, network_dir: Path, data: bytes | None = None) -> list[dict]:
    """解析玩家列表（带磁盘缓存）。

    缓存命中但缺 `civ_raw` 的条目要重算 —— 那是旧版解析器写的，
    当时没有原始文明名，「lancaster 被过滤成空」这类结果会一直粘着不放。
    """
    global _PLAYERS_CACHE
    if not _PLAYERS_CACHE:
        try:
            import json

            _PLAYERS_CACHE = json.loads(_players_cache_path(network_dir).read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            _PLAYERS_CACHE = {}
    try:
        st = p.stat()
    except OSError:
        return []
    key = f"{p.name}:{st.st_size}:{int(st.st_mtime)}"
    hit = _PLAYERS_CACHE.get(key)
    if hit is not None and not any("civ_raw" not in x for x in hit):
        return hit
    if data is None:
        try:
            raw = p.read_bytes()
            import gzip as _g

            data = _g.decompress(raw) if raw[:2] == b"\x1f\x8b" else raw
        except Exception:  # noqa: BLE001
            return []
    try:
        pl = parse_players(data)
    except Exception:  # noqa: BLE001
        pl = []
    _PLAYERS_CACHE[key] = pl
    return pl


def save_players_cache(network_dir: Path) -> None:
    try:
        import json

        _players_cache_path(network_dir).write_text(
            json.dumps(_PLAYERS_CACHE, ensure_ascii=False), encoding="utf-8"
        )
    except Exception:  # noqa: BLE001
        pass


def extract_mode_pack(data: bytes) -> dict:
    """从回放里提取「玩法包」标识。

    实测：自定义对局的设置区里有形如
        483db8a9959441839cb441ebb025b6f6:3439046599
        紧接着 "WinConditionExtensionPackPart"
    匹配对局（排位/快匹）没有这一段。

    返回 {"pack_hash": "...", "pack_id": 3439046599, "pack_kind": "WinConditionExtensionPackPart"}
    """
    out = {"pack_hash": "", "pack_id": None, "pack_kind": ""}
    m = re.search(rb"([0-9a-f]{32}):(\d{5,12})", data[:400_000])
    if not m:
        return out
    out["pack_hash"] = m.group(1).decode("ascii", "ignore")
    out["pack_id"] = int(m.group(2))
    tail = data[m.end() : m.end() + 200]
    km = re.search(rb"([A-Za-z][A-Za-z0-9_]{6,60})", tail)
    if km:
        out["pack_kind"] = km.group(1).decode("ascii", "ignore")
    return out


def infer_mode(players: list[dict], is_custom: bool, api_kind: str | None = None) -> str:
    """推断对局模式。

    回放文件里**没有**模式字段（实测：连已知 qm_4v4 的对局也搜不到任何模式串），
    所以只能按「玩家数」推：

        自定义 + 8 人 → 自定义 4v4
        自定义 + 6 人 → 自定义 3v3
        自定义 + 4 人 → 自定义 2v2
        自定义 + 2 人 → 自定义 1v1

    AoE4 只有这几种队伍规模，推出来的结果和实际一致率很高。
    """
    if api_kind and api_kind not in ("custom", "network"):
        return api_kind

    n = len([p for p in (players or []) if p.get("name")])
    if n >= 7:
        size = "4v4"
    elif n >= 5:
        size = "3v3"
    elif n >= 3:
        size = "2v2"
    elif n == 2:
        size = "1v1"
    else:
        size = ""

    if is_custom:
        return f"自定义 {size}".strip() if size else "自定义"
    return f"{size}" if size else ""


def _norm_civ(raw: str) -> str:
    """把回放里的文明原文归一化到官方全名（旗帜文件名用的就是全名）。

    实测回放里出现的写法有三类：
        * 裸名变体：lancaster / dragon / jeanne          → 必须查 CIV_ALIAS 原文
        * 带后缀变体：hre_ha_01 / sultanate_ha_tug       → 必须**先**查原文，别先剥
        * 基础简写：hre / abbasid / templar              → 查表
    顺序反了会掉级：sultanate_ha_tug 先剥会变成 sultanate（苏丹军），
    正确是 tughlaq_dynasty（图格鲁克王朝）。统一委托给 civs.resolve_civ。
    """
    if not raw:
        return ""
    return resolve_civ(raw)


def _civ_flag_dir() -> Path:
    return Path(__file__).resolve().parent.parent / "data" / "civflags"


def maybe_civ(raw: str) -> str:
    """这个 ascii 串看起来像不像文明名？像才返回归一化结果，否则返回 ""。

    为什么要这一步：`parse_players` 是在「名字 → steam id」之间盲搜 ascii 串，
    有些玩家槽根本没有文明字段，中间夹着的是 Steam ID 的文本片段，
    不加形状校验就会把垃圾当文明，还可能配错旗。
    真的未来文明查不到表时，`resolve_civ` 会原样返回，这里保留它 —— 别把
    未知文明和「没有文明」混成同一个状态。
    """
    s = (raw or "").strip().lower()
    if not s or s in _NON_CIV_STRINGS:
        return ""
    if not re.fullmatch(r"[a-z][a-z0-9_]{2,30}", s):
        return ""
    return _norm_civ(s)


_NON_CIV_STRINGS = {
    "default", "biome", "random_map", "map_size", "scenario", "hal", "lancast",
    "true", "false", "none", "null", "player", "human", "computer", "team",
}


def _civ_icon(civ: str, civ_raw: str = "") -> str:
    """文明 → 本地旗帜路径。查不到就不给路径（前端会画「?」兜底）。"""
    if not civ:
        return ""
    slug = civ_slug(civ)
    if not slug:
        return ""
    try:
        if not (_civ_flag_dir() / (slug + ".png")).exists():
            return ""
    except OSError:
        pass
    return f"/civflags/{slug}.png"


def parse_players(data: bytes) -> list[dict]:
    """从回放里解析玩家列表（steam id / 名字 / 文明 / 是否 AI）。

    实测布局（第二层 FOLDINFO 之后的 DATADATA 区）：
        ...ffffffffffffffff 00000000  [01 len 000000]  "名字" UTF-16
        [00000000] [03000000] [01 len 000000] "文明" ascii  "default"

    名字/文明都是「01 + uint32 长度 + 字符串 + 00000000」的带长前缀串，
    所以用 01 0d 000000 这种模式精确定位，而不是模糊匹配可打印字节。
    """
    from .civs import CIV_ZH

    players: list[dict] = []
    second = data.find(b"Relic Chunky", 1)
    start = second if second >= 0 else 0
    fi = data.find(b"FOLDINFO", start)
    if fi < 0:
        return players
    win = data[fi : fi + 20000]

    def _read_utf16(seg: bytes, pos: int) -> tuple[str, int]:
        """读 01 <len4> <utf16 字符串> 00000000，返回 (字符串, 新位置)。"""
        if pos + 6 > len(seg) or seg[pos] != 0x01:
            return "", pos
        n = int.from_bytes(seg[pos + 1 : pos + 5], "little")
        if n <= 0 or n > 60:
            return "", pos
        body = seg[pos + 5 : pos + 5 + n * 2]
        if len(body) < n * 2:
            return "", pos
        try:
            s = body.decode("utf-16-le", "ignore")
        except Exception:  # noqa: BLE001
            return "", pos
        if not s.isprintable():
            return "", pos
        return s, pos + 5 + n * 2

    def _read_ascii(seg: bytes, pos: int) -> tuple[str, int]:
        if pos + 6 > len(seg) or seg[pos] != 0x01:
            return "", pos
        n = int.from_bytes(seg[pos + 1 : pos + 5], "little")
        if n <= 0 or n > 40:
            return "", pos
        body = seg[pos + 5 : pos + 5 + n]
        try:
            s = body.decode("ascii", "ignore")
        except Exception:  # noqa: BLE001
            return "", pos
        if not re.fullmatch(r"[a-z][a-z0-9_]{1,30}", s):
            return "", pos
        return s, pos + 5 + n

    # 实测（用模板玩家 14579308 做锚点验证 8/8 全对）：
    #   名字在 steam id **之前**，配给它后面最近的那个 id
    #   布局：[01 len] "名字" UTF-16 ... [01 len] "文明" ascii "default" ... [11 000000] "steam_id"
    all_ids = [m.start() for m in re.finditer(rb"(?:[\x30-\x39]\x00){9,17}", win)]

    entries: list[tuple[int, str, str]] = []   # (id 偏移, 名字, 文明)
    for m in re.finditer(rb"\x01([\x02-\x28])\x00\x00\x00", win):
        n = m.group(1)[0]
        body = win[m.end() : m.end() + n * 2]
        if len(body) < n * 2:
            continue
        try:
            s = body.decode("utf-16-le", "ignore")
        except Exception:  # noqa: BLE001
            continue
        if not s.isprintable() or len(s) < 2 or s.isdigit():
            continue
        # 这个名字后面最近的一个 id
        after = [off for off in all_ids if off > m.end()]
        if not after:
            continue
        nid = after[0]
        # 名字到 id 之间找文明（ascii + default）
        seg = win[m.end() : nid]
        civ = ""
        civ_raw = ""
        cm = re.search(rb"([a-z][a-z0-9_]{2,24})\x00\x00\x22", seg) or re.search(
            rb"([a-z][a-z0-9_]{2,24})\x00default", seg
        )
        if cm:
            raw_civ = cm.group(1).decode("ascii", "ignore")
            # 尾巴要确认是「文明名 + 0000 2200 7f000000 07000000 default」这一套，
            # 否则会误抓段内的别的 ascii 串
            tail_ok = b"default" in seg[cm.end() : cm.end() + 24]
            if tail_ok:
                civ_raw = raw_civ
                civ = maybe_civ(raw_civ)
        entries.append((nid, s, civ, civ_raw))

    for nid, name, civ, civ_raw in entries:
        sid_m = re.compile(rb"(?:[\x30-\x39]\x00){9,17}").match(win, nid)
        if not sid_m:
            continue
        sid = sid_m.group(0).decode("utf-16-le", "ignore")
        if not sid.isdigit():
            continue
        players.append(
            {
                "steam_id": sid,
                "name": name,
                "civ": civ,
                "civ_raw": civ_raw,
                "civ_zh": civ_name(civ),
                "civ_icon": _civ_icon(civ, civ_raw),
                "is_ai": name.startswith("!AI"),
            }
        )

    seen: set[str] = set()
    uniq = []
    for pl in players:
        if pl["steam_id"] in seen:
            continue
        seen.add(pl["steam_id"])
        uniq.append(pl)
    return uniq


def detect_mode(path: Path) -> dict:
    """从回放文件本身判断模式，不依赖 AoE4World。

    依据（实测）：
      * 自定义对局的 DATASDSC 里有 `section_cac_conditions` 块
        （CAC = Custom Advanced Configuration：enable_king_mode、lord_slot、
         mole_qty、nomad_villagers …… 这些都是自定义游戏设置项）
      * 排位 / 快匹没有这个块（匹配对局的设置由服务器决定，不写进回放）
    """
    out = {"custom": False, "has_cac": False, "ai_count": 0, "players": 0, "map": ""}
    try:
        raw = path.read_bytes()
    except OSError:
        return out
    try:
        if raw[:2] == b"\x1f\x8b":
            import gzip as _gzip

            data = _gzip.decompress(raw)
        else:
            data = raw
    except Exception as e:  # noqa: BLE001
        out["error"] = f"{type(e).__name__}: {e}"
        return out

    # 设置块可能在文件任何位置，需要全文件找；gzip 解压后才有明文
    has_cac = b"section_cac_conditions" in data
    out["has_cac"] = has_cac
    out["custom"] = has_cac

    # 人机：设置区里出现 "!AI"
    out["ai_count"] = data.count(b"!AI")

    # 玩家条目：文件是两层 Relic Chunky，玩家列表在「第二层的」DATADATA 里。
    # 格式是带长度前缀的字符串： 01 xx/xxxxxx "french" 00 ... "default" 00
    # 所以不能直接匹配 "french\x00default"，要放宽：文明名后面若干字节内出现 default
    out["civs"] = []
    out["ai_count"] = data.count(b"!AI")
    second = data.find(b"Relic Chunky", 1)
    search_from = second if second >= 0 else 0
    di = data.find(b"DATADATA", search_from)
    if di >= 0:
        seg = data[di : di + 6000]
        seen: list[str] = []
        for m in re.finditer(rb"([a-z][a-z0-9_]{2,20})\x00", seg):
            name = m.group(1).decode("ascii", "ignore")
            if name not in KNOWN_CIVS:
                continue
            tail = seg[m.end() : m.end() + 40]
            if b"default" in tail and name not in seen:
                seen.append(name)
        out["civs"] = seen
        out["players"] = len(seen)

    # 地图：map_size_NNN 后面那个场景名
    ms = data.find(b"map_size_")
    if ms >= 0:
        seg = data[ms : ms + 300]
        strs = re.findall(rb"[\x20-\x7e]{4,40}", seg)
        for s in strs:
            name = s.decode("ascii", "ignore")
            if name.startswith("map_size_"):
                continue
            if re.fullmatch(r"[a-z][a-z0-9_]+", name):
                out["map"] = name.replace("_", " ").title()
                break
    return out


def classify(kind: str | None, source: str | None = None, has_api: bool = False) -> str:
    """把一场对局归到四类之一。"""
    k = (kind or "").lower()
    if k.startswith(RANKED_PREFIX):
        return "ranked"        # 排位
    if k.startswith(QUICK_PREFIX):
        return "quick"         # 快匹
    if k.startswith(("skirmish", "ai_", "ai")):
        return "ai"            # 人机
    if source in ("rogue", "temp"):
        return "custom"        # 本地 .rec，基本是人机/战役/自定义
    if not has_api:
        return "custom"        # AoE4World 查不到 → 不是官方匹配
    return "custom"            # 已经是 rm_/qm_ 之外的 kind，一律算自定义


def is_custom(kind: str | None, source: str | None = None, has_api: bool = False) -> bool:
    return classify(kind, source, has_api) == "custom"


def read_meta(path: Path) -> dict:
    """读一场回放的地图/文明等元信息。

    只解 gzip 的前若干字节 —— 回放的元数据都在开头的 DATASDSC 块里
    （场景描述是纯文本：`hidden_valley`、`forest_eastern` 这种），
    不需要解整个文件。
    """
    out = {"map": "", "biome": "", "civs": [], "date": ""}
    try:
        raw = path.read_bytes()
    except OSError:
        return out
    try:
        if raw[:2] == b"\x1f\x8b":
            import gzip

            data = gzip.decompress(raw)
        else:
            data = raw
    except Exception:  # noqa: BLE001
        return out

    head = data[:8192]
    text = head.decode("latin-1", "ignore")

    # 日期：AOE4_RE 后面那段 UTF-16LE
    m = re.search(r"AOE4_RE\x00", text)
    if m:
        try:
            tail = data[m.end() : m.end() + 60]
            s = tail.decode("utf-16-le", "ignore").split("\x00")[0]
            if re.match(r"^\d{4}/\d{1,2}/\d{1,2}", s):
                out["date"] = s.replace("/", "-")
        except Exception:  # noqa: BLE001
            pass

    # 地图：DATASDSC 块里是带长度前缀的字符串
    #   01 0c 000000 "map_size_896" 00
    #   01 33 00 01 0f 000000 "land_megarandom" 00   ← 这个才是场景名
    #   01 26 00 01 09 000000 "temperate" 00        ← 生物群系
    sdsc = data.find(b"DATASDSC")
    if sdsc >= 0:
        blob = data[sdsc : sdsc + 600]
        strs = re.findall(rb"\x01[\x00-\xff]?\x00?\x01?([\x05-\x28])\x00\x00\x00([\x20-\x7e]{3,40})\x00", blob)
        names = []
        for _lenbyte, raw in strs:
            try:
                names.append(raw.decode("ascii", "ignore"))
            except Exception:  # noqa: BLE001
                pass
        for n in names:
            if n.startswith("map_size_"):
                continue
            if n in ("default", "biome"):
                continue
            if re.fullmatch(r"[a-z][a-z0-9_]+", n):
                if not out["map"]:
                    out["map"] = n.replace("_", " ").title()
                elif n in ("temperate", "forest_eastern", "mediterranean", "japanese",
                           "asian", "desert", "tundra", "rainforest", "snow"):
                    out["biome"] = n
        # 场景路径形式（自定义对局常见）
        if not out["map"]:
            mm = re.search(rb"scenarios[\\/]([a-z0-9_\\/]{3,40})", blob)
            if mm:
                parts = [p for p in re.split(rb"[\\/]", mm.group(1)) if p]
                if parts:
                    out["map"] = parts[-1].decode("ascii", "ignore").replace("_", " ").title()

    # 文明：DATADATA 块里出现的文明 id
    for civ in CIV_IDS:
        if (b"\x00" + civ.encode() + b"\x00") in data[:20000] or (civ.encode() + b"\x00") in data[:20000]:
            out["civs"].append(civ)
    return out


CIV_IDS = [
    "english", "french", "rus", "mongols", "chinese", "holy_roman_empire",
    "abbasid_dynasty", "delhi_sultanate", "khmer", "malians", "ottomans",
    "byzantines", "japanese", "ayyubids", "jeanne_darc", "zhu_xis_legacy",
    "order_of_the_dragon", "house_of_lancaster", "tughlaq_dynasty", "sengoku_daimyo",
    "macedonian_dynasty", "knights_templar", "golden_horde", "aby",
]


_META_CACHE: dict[str, dict] = {}
_META_CACHE_FILE = None


def _meta_cache_path(network_dir: Path) -> Path:
    return network_dir.parent / "lcs_meta_cache.json"


def _load_meta_cache(network_dir: Path) -> dict:
    global _META_CACHE
    if _META_CACHE:
        return _META_CACHE
    p = _meta_cache_path(network_dir)
    try:
        import json

        _META_CACHE = json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        _META_CACHE = {}
    return _META_CACHE


def _save_meta_cache(network_dir: Path) -> None:
    try:
        import json

        _meta_cache_path(network_dir).write_text(
            json.dumps(_META_CACHE, ensure_ascii=False), encoding="utf-8"
        )
    except Exception:  # noqa: BLE001
        pass


def read_meta_cached(path: Path, network_dir: Path) -> dict:
    """带磁盘缓存：靠 (文件名, 大小, mtime) 判断是否复用旧结果。"""
    cache = _load_meta_cache(network_dir)
    try:
        st = path.stat()
    except OSError:
        return {}
    key = f"{path.name}:{st.st_size}:{int(st.st_mtime)}"
    if key in cache:
        return cache[key]
    try:
        meta = read_meta(path)
    except Exception:  # noqa: BLE001
        meta = {}
    cache[key] = meta
    return meta


_GAME_CACHE: dict[str, dict] = {}


def _game_cache_path(network_dir: Path) -> Path:
    return network_dir.parent / "lcs_games_cache.json"


def load_game_cache(network_dir: Path) -> dict:
    global _GAME_CACHE
    if _GAME_CACHE:
        return _GAME_CACHE
    try:
        import json

        _GAME_CACHE = json.loads(_game_cache_path(network_dir).read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        _GAME_CACHE = {}
    return _GAME_CACHE


def save_game_cache(network_dir: Path) -> None:
    try:
        import json

        _game_cache_path(network_dir).write_text(
            json.dumps(_GAME_CACHE, ensure_ascii=False), encoding="utf-8"
        )
    except Exception:  # noqa: BLE001
        pass


def enrich_from_api(game_ids: list[int], network_dir: Path, me_id: int, limit: int = 30) -> dict:
    """只查缓存里没有的 ID，带限速与 429 退避。返回 {game_id: 解析后的行数据}。"""
    import time

    from . import api
    from .civs import civ_name

    # 文明旗帜表：直接读文件，避免和 server 模块的加载顺序打架
    flags: dict = {}
    try:
        import json as _json
        from pathlib import Path as _Path

        p = _Path(__file__).resolve().parent.parent / "data" / "civflags_local.json"
        flags = _json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        flags = {}

    def _icon(civ: str | None) -> str:
        return flags.get(civ or "", "")

    cache = load_game_cache(network_dir)
    out: dict[int, dict] = {}
    todo = []
    for gid in game_ids:
        key = str(gid)
        hit = cache.get(key)
        # 旧缓存里 civ_icon 是 null 的，作废重查
        if hit and not hit.get("missing"):
            me = hit.get("me") or {}
            if me and me.get("civ_icon") is None:
                hit = None
        if hit is not None:
            out[gid] = hit
        elif limit > 0:
            todo.append(gid)
            limit -= 1

    for gid in todo:
        key = str(gid)
        try:
            d = api.game_detail(gid)
        except Exception as e:  # noqa: BLE001
            msg = str(e)
            # 只有 404 才是「AoE4World 真没有这场」。429/超时/DNS 抖动都是临时失败，
            # 一旦也记成 missing 并落盘，这局就永久显示「查不到」，再也没有重试机会。
            if "404" in msg:
                row = {"missing": True, "reason": "无公开数据"}
                cache[key] = row
                out[gid] = row
            else:
                out[gid] = {"missing": True, "reason": "查询失败（稍后重试）", "transient": True}
                if "429" in msg:
                    time.sleep(3)
            continue
        me, mates, opps = None, [], []
        for team in d.get("teams") or []:
            for entry in team:
                p = entry.get("player") or entry
                item = {
                    "name": p.get("name"),
                    "civ": p.get("civilization"),
                    "civ_zh": civ_name(p.get("civilization") or ""),
                    "civ_icon": _icon(p.get("civilization")),
                    "rating": p.get("rating"),
                    "rating_diff": p.get("rating_diff"),
                    "result": p.get("result"),
                }
                if p.get("profile_id") == me_id:
                    me = item
                    for e2 in team:
                        q = e2.get("player") or e2
                        if q.get("profile_id") == me_id:
                            continue
                        mates.append(
                            {
                                "name": q.get("name"),
                                "civ": q.get("civilization"),
                                "civ_zh": civ_name(q.get("civilization") or ""),
                                "civ_icon": _icon(q.get("civilization")),
                                "rating": q.get("rating"),
                                "result": q.get("result"),
                            }
                        )
                    opps = [
                        {
                            "name": (e2.get("player") or e2).get("name"),
                            "civ": (e2.get("player") or e2).get("civilization"),
                            "civ_zh": civ_name(((e2.get("player") or e2).get("civilization")) or ""),
                            "civ_icon": _icon((e2.get("player") or e2).get("civilization")),
                            "rating": (e2.get("player") or e2).get("rating"),
                            "result": (e2.get("player") or e2).get("result"),
                        }
                        for other in (d.get("teams") or [])
                        if other is not team
                        for e2 in other
                    ]
        row = {
            "missing": False,
            "kind": d.get("kind"),
            "map": d.get("map"),
            "duration": d.get("duration"),
            "started_at": d.get("started_at"),
            "average_rating": d.get("average_rating"),
            "patch": d.get("patch"),
            "me": me,
            "mates": mates,
            "opponents": opps,
        }
        cache[key] = row
        out[gid] = row
        time.sleep(0.5)

    save_game_cache(network_dir)
    return out


def _is_replay_fast(p: Path) -> bool:
    """只看头 64 字节判断是不是回放，不读整个文件。

    回放缓存有两类：
      * gzip 包着的（1f 8b）—— 真回放就是这种
      * 其他二进制资源 —— 直接排除
    实读整个文件解压会把 878 个文件拖到 10 秒，这里只看头。
    """
    try:
        with open(p, "rb") as fh:
            head = fh.read(64)
    except OSError:
        return False
    if head[:2] == b"\x1f\x8b":
        return True
    if head.startswith(b"\x00\x00,,\x41\x4f\x45\x34") or head[:12] == b"Relic Chunky":
        return True
    return False


_CLS_CACHE: dict[str, bool] = {}


def _cls_cache_path(network_dir: Path) -> Path:
    return network_dir.parent / "lcs_mode_cache.json"


def _load_cls_cache(network_dir: Path) -> dict:
    global _CLS_CACHE
    if _CLS_CACHE:
        return _CLS_CACHE
    try:
        import json

        _CLS_CACHE = json.loads(_cls_cache_path(network_dir).read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        _CLS_CACHE = {}
    return _CLS_CACHE


def save_cls_cache(network_dir: Path) -> None:
    try:
        import json

        _cls_cache_path(network_dir).write_text(
            json.dumps(_CLS_CACHE, ensure_ascii=False), encoding="utf-8"
        )
    except Exception:  # noqa: BLE001
        pass


def fast_custom(path: Path, network_dir: Path | None = None) -> bool:
    """带缓存的自定义判定，key = 文件名+大小+mtime。"""
    try:
        st = path.stat()
    except OSError:
        return False
    key = f"{path.name}:{st.st_size}:{int(st.st_mtime)}"
    cache = _load_cls_cache(network_dir) if network_dir else _CLS_CACHE
    if key in cache:
        return cache[key]
    val = _fast_custom(path)
    cache[key] = val
    return val


def _is_custom_cached(p: Path, network_dir: Path) -> bool:
    """内部用：判断是否自定义（带缓存）。"""
    return fast_custom(p, network_dir)


def _fast_custom(path: Path) -> bool:
    """快速判断一个回放文件是不是自定义对局（只解 gzip 找标志串）。"""
    try:
        raw = path.read_bytes()
    except OSError:
        return False
    try:
        if raw[:2] == b"\x1f\x8b":
            import gzip as _gzip

            data = _gzip.decompress(raw)
        else:
            data = raw
    except Exception:  # noqa: BLE001
        return False
    return b"section_cac_conditions" in data


def scan_network(network_dir: Path, library_ids: set[int] | None = None) -> list[dict]:
    """扫 network\\ 里的 M_<game_id>_<hash> 缓存 —— 在游戏里点开别人回放就落这里。

    同一个对局可能有多个副本（不同参与者各一份），取最大的那个
    （大的是完整对局，小的是剪辑/摘要）。
    """
    best: dict[int, Path] = {}
    try:
        for p in network_dir.iterdir():
            if not p.is_file():
                continue
            m = NET_RE.match(p.name)
            if not m:
                continue
            gid = int(m.group(1))
            cur = best.get(gid)
            try:
                if cur is None or p.stat().st_size > cur.stat().st_size:
                    best[gid] = p
            except OSError:
                continue
    except OSError:
        return []

    out: list[dict] = []
    for gid, p in best.items():
        try:
            st = p.stat()
        except OSError:
            continue
        valid = _is_replay_fast(p)
        if not valid:
            continue
        # 读元信息（走磁盘缓存，第二次起几乎零成本）
        meta = {"map": "", "civs": [], "date": "", "biome": ""}
        if st.st_size > 300 * 1024:
            try:
                meta = read_meta_cached(p, network_dir)
            except Exception:  # noqa: BLE001
                pass
        # 自定义对局：从文件里的设置块拿文明列表（AoE4World 没有这些数据）
        file_civs: list[str] = []
        players: list[dict] = []
        pack: dict = {"pack_hash": "", "pack_id": None, "pack_kind": ""}
        is_custom = _is_custom_cached(p, network_dir)
        if is_custom:
            try:
                raw2 = p.read_bytes()
                import gzip as _gz2

                d2 = _gz2.decompress(raw2) if raw2[:2] == b"\x1f\x8b" else raw2
                players = _players_cached(p, network_dir, d2)
                file_civs = [civ_name(c) for c in (detect_mode(p).get("civs") or [])]
                pack = extract_mode_pack(d2)
            except Exception:  # noqa: BLE001
                players = []
                file_civs = []
                pack = {"pack_hash": "", "pack_id": None, "pack_kind": ""}
        civs_zh = [civ_name(c) for c in (meta.get("civs") or [])]
        out.append(
            {
                "source": "network",
                "file": p.name,
                "path": str(p),
                "size": st.st_size,
                "started_at": "",
                "started_local": meta.get("date") or datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M"),
                "map": meta.get("map") or "网络缓存",
                "civ_zh": "、".join((file_civs or civs_zh)[:6]),
                "file_civs": file_civs,
                "players": players,
                "biome": meta.get("biome") or "",
                "kind": "custom" if is_custom else "network",
                "mode_label": infer_mode(players, is_custom),
                "pack_id": pack.get("pack_id"),
                "pack_hash": pack.get("pack_hash") or "",
                "pack_kind": pack.get("pack_kind") or "",
                "game_id": gid,
                "playable": True,
                "in_library": bool(library_ids and gid in library_ids),
                "note": "自定义对局" if is_custom else "缓存副本，可直接播",
            }
        )
    out.sort(key=lambda e: e["started_local"], reverse=True)
    _save_meta_cache(network_dir)
    return out


def _stat_or_none(p: Path):
    """stat 一下，文件正好在这时候被游戏删掉也不至于把整个列表打挂。"""
    try:
        return p.stat()
    except OSError:
        return None


def scan_local(playback_dir: Path) -> list[dict]:
    """列出本地自定义/临时对局。"""
    out: list[dict] = []
    rogue = playback_dir / "rogue"
    if rogue.exists():
        recs = []
        for p in rogue.glob("*.rec"):
            st = _stat_or_none(p)
            if st is not None:
                recs.append((st.st_mtime, p, st))
        for _mtime, p, st in sorted(recs, key=lambda t: t[0], reverse=True):
            meta = _parse_rogue_name(p.name)
            out.append(
                {
                    "source": "rogue",
                    "file": p.name,
                    "path": str(p),
                    "size": st.st_size,
                    "started_at": meta.get("started_at") or "",
                    "started_local": _fmt_mtime(p),
                    "map": meta.get("map") or "—",
                    "civ_zh": meta.get("civ_zh") or "",
                    "kind": "custom",
                    "game_id": None,
                    "playable": True,
                    "note": "本地 .rec，可直接播放",
                }
            )
    for name in ("temp.rec", "temp_campaign.rec"):
        p = playback_dir / name
        st = _stat_or_none(p)
        if st is not None and st.st_size > 4096:
            out.append(
                {
                    "source": "temp",
                    "file": p.name,
                    "path": str(p),
                    "size": st.st_size,
                    "started_at": "",
                    "started_local": _fmt_mtime(p),
                    "map": "—",
                    "civ_zh": "",
                    "kind": "temp",
                    "game_id": None,
                    "playable": True,
                    "note": "临时录制（最近一局），可直接播放",
                }
            )
    return out


def describe(entry: dict) -> str:
    if entry["source"] == "rogue":
        return f"{entry.get('map') or '?'} · {entry.get('civ_zh') or '?'}"
    if entry["source"] == "temp":
        return "临时录制"
    return "手动登记"

