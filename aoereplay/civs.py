# -*- coding: utf-8 -*-
"""文明 id -> 中文名（AoE4 全 DLC）。未收录的 id 原样显示。"""
import re

CIV_ZH = {
    "abbasid_dynasty": "阿拔斯王朝",
    "aachen": "阿亨",
    "english": "英格兰",
    "french": "法兰西",
    "holy_roman_empire": "神圣罗马帝国",
    "rus": "罗斯",
    "chinese": "中国",
    "mongols": "蒙古",
    "delhi_sultanate": "德里苏丹国",
    "khmer": "高棉",
    "malians": "马里",
    "ottomans": "奥斯曼",
    "byzantines": "拜占庭",
    "japanese": "日本",
    "ayyubids": "阿尤布王朝",
    "jeanne_darc": "圣女贞德",
    "zhu_xis_legacy": "朱希传承",
    "order_of_the_dragon": "龙骑士团",
    "house_of_lancaster": "兰开斯特王朝",
    "tughlaq_dynasty": "图格鲁克王朝",
    "sengoku_daimyo": "战国大名",
    "macedonian_dynasty": "马其顿王朝",
    "knights_templar": "圣殿骑士团",
    "golden_horde": "金帐汗国",
    "sultans_army": "苏丹军",
    "army_of_the_sultan": "苏丹军",
    "hindu_rajas": "印度诸王",
    "jin_dynasty": "金朝",
    "vijayanagara": "毗奢耶那伽罗",
    "dan": "丹麦",
    "danish": "丹麦",
    "tarkhan": "塔尔汗",
    # 回放里出现的裸名变体（lancaster / dragon / jeanne …）
    "lancaster": "兰开斯特王朝",
    "dragon": "龙骑士团",
    "jeanne": "圣女贞德",
    "zhu_xi": "朱希传承",
    "macedonian": "马其顿王朝",
    "tughlaq": "图格鲁克王朝",
    "sengoku": "战国大名",
    "jin": "金朝",
    "tartar": "塔尔汗",
    "rand": "随机",
    # 回放文件里的简写形式
    "abbasid": "阿拔斯王朝",
    "byzantine": "拜占庭",
    "templar": "圣殿骑士团",
    "ottoman": "奥斯曼",
    "mongol": "蒙古",
    "hre": "神圣罗马帝国",
    "delhi": "德里苏丹国",
    "malian": "马里",
    "ayyubid": "阿尤布王朝",
    "sultanate": "苏丹军",
    "french": "法兰西",
    "english": "英格兰",
    "chinese": "中国",
    "rus": "罗斯",
    "japanese": "日本",
    "khmer": "高棉",
}


def civ_name(civ_id: str) -> str:
    if not civ_id:
        return ""
    return CIV_ZH.get(civ_id, civ_id)


# 回放文件里的简写 → 官方全名（旗帜文件用的就是全名）
#
# 这里的键是**回放原文**，不是剥完后缀的 base 名。
# 带 _ha_* 后缀的变体必须写全，否则会被 _norm_civ 先剥成别的 base 名。
CIV_ALIAS = {
    # 基础简写
    "hre": "holy_roman_empire",
    "abbasid": "abbasid_dynasty",
    "byzantine": "byzantines",
    "templar": "knights_templar",
    "templars": "knights_templar",
    "ottoman": "ottomans",
    "mongol": "mongols",
    "delhi": "delhi_sultanate",
    "malian": "malians",
    "ayyubid": "ayyubids",
    "sultanate": "sultans_army",
    "sultans_army": "sultans_army",
    "army_of_the_sultan": "sultans_army",
    "chinese": "chinese",
    "english": "english",
    "french": "french",
    "rus": "rus",
    "japanese": "japanese",
    "khmer": "khmer",
    # 帝国变体（AOE4 的帝国 dlc 在回放里就是裸名字）
    "lancaster": "house_of_lancaster",
    "house_of_lancaster": "house_of_lancaster",
    "dragon": "order_of_the_dragon",
    "order_of_the_dragon": "order_of_the_dragon",
    "jeanne": "jeanne_darc",
    "jeanne_darc": "jeanne_darc",
    "zhu_xi": "zhu_xis_legacy",
    "zhu_xis_legacy": "zhu_xis_legacy",
    "macedonian": "macedonian_dynasty",
    "macedonian_dynasty": "macedonian_dynasty",
    "tughlaq": "tughlaq_dynasty",
    "tughlaq_dynasty": "tughlaq_dynasty",
    "sengoku": "sengoku_daimyo",
    "sengoku_daimyo": "sengoku_daimyo",
    "jin": "jin_dynasty",
    "jin_dynasty": "jin_dynasty",
    "golden_horde": "golden_horde",
    "vijayanagara": "vijayanagara",
    "hindu_rajas": "hindu_rajas",
    "dan": "danish",
    "danish": "danish",
    "tartar": "tarkhan",
    "tarkhan": "tarkhan",
    # 文明变体（带 _ha_* 后缀）—— 必须写全，防止被剥成别的 base 名
    "english_ha_01": "english",
    "french_ha_01": "french",
    "chinese_ha_01": "chinese",
    "hre_ha_01": "holy_roman_empire",
    "mongol_ha_gol": "mongols",
    "sultanate_ha_tug": "tughlaq_dynasty",
    "abbasid_ha_01": "abbasid_dynasty",
    "byzantine_ha_01": "byzantines",
    "ottoman_ha_01": "ottomans",
    "japanese_ha_01": "japanese",
    "malian_ha_01": "malians",
    "ayyubid_ha_01": "ayyubids",
    "delhi_ha_01": "delhi_sultanate",
    "rus_ha_01": "rus",
    "khmer_ha_01": "khmer",
}

# 回放里表示「随机文明」的写法
CIV_RANDOM = {"random", "random_civ", "rand"}


def resolve_civ(raw: str) -> str:
    """回放原文 → 官方全名（能查表就查表，不认识就剥后缀再试，最后原样返回）。

    三段式，顺序不能反：
      1. 原文直接命中 CIV_ALIAS      —— sultanate_ha_tug 在这里就落到 tughlaq_dynasty
      2. 剥 _ha_* 后缀后再命中       —— hre_ha_07 这种没登记的变体
      3. 仍然查不到就原样返回，交给 civ_name() 兜底显示
    """
    if not raw:
        return ""
    s = raw.strip().lower()
    if s in CIV_ALIAS:
        return CIV_ALIAS[s]
    base = re.sub(r"_ha(_\d+)?(\w+)?$", "", s).strip("_") if s else ""
    if base and base in CIV_ALIAS:
        return CIV_ALIAS[base]
    if base and base in CIV_ZH:
        return base
    if s in CIV_ZH:
        return s
    return s


def civ_slug(civ_id: str) -> str:
    """回放的 civ id → 旗帜文件名（不含 .png）。"""
    if not civ_id:
        return ""
    s = civ_id.strip().lower()
    if s in CIV_ALIAS:
        return CIV_ALIAS[s]
    if s in CIV_ZH:
        return s
    return CIV_ALIAS.get(re.sub(r"_ha(_\d+)?(\w+)?$", "", s).strip("_"), s)
