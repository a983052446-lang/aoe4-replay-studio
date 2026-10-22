# -*- coding: utf-8 -*-
"""AoE4 Replay Studio 引擎层。

模块分工：
    config   —— 路径探测（AoE4 playback 目录 / 回放库 / Steam exe / 浏览器）
    civs     —— 文明中文名
    api      —— AoE4World REST API（玩家、对局、录像链接）
    library  —— 本地回放库存（playback 目录 + 回放库 + relink 软链接），负责 game_id 对账
    gz       —— Relic gzip 包裹层解包 / 重新加头
    actor    —— 用独立浏览器 profile 抓取带签名的实时录像链接
    launcher —— 把回放投送进 playback 目录并用 -replay 启动 Steam
    downloads—— 下载队列（状态机 + 进度回传）
"""

__version__ = "1.0.0"
