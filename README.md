# AoE4 回放工作台

[![Python](https://img.shields.io/badge/Python-3.10%2B-blue)](https://www.python.org/)
[![Platform](https://img.shields.io/badge/Platform-Windows-lightgrey)]()
[![License](https://img.shields.io/badge/License-MIT-green)](LICENSE)

> **维护状态：按现状发布（as-is）。** 作者不提供后续更新，不回应 issue / PR，也不做功能请求。
> 代码能跑、测试能过就到此为止；哪里不合用，请自己 fork 改。
> 踩过什么坑、每条结论怎么验证的，全在 [开发记录.md](开发记录.md) 里，比 issue 区有用。
>
> **来源与声明**：二次修改，基于 spartain-aoe/aoe4world-replay-extension 与 AoE4 Replay Launcher，作者原话见 [声明.md](声明.md)。
> 那句「不要传播」是**请求，不是许可限制** —— 仓库公开、代码 MIT，谁都能用能改能分发；作者只是不背书任何转手版本。

把 `aoe4world.com/players/<profile_id>/games` 那张又长又暗的网页，压成一个本地小界面：

* **实时更新**：后台每 60 秒抓一次 AoE4World 公开 API（页面静默时自动刷；点「刷新」立刻拉）
* **分页**：每页 50 场，可一直往下翻（你的历史总场数）
* **选择下载 + 下载反馈**：勾选 / 按「最近 N 天缺的 M 场」批量入队 → 队列里能看到 排队 → 解析 → 下载 % → 已入库
* **选择回放**：每行一个「回放」按钮，投送文件到 `playback\` 并用 `-replay` 拉起 Steam，游戏运行中会拦住并提示先退出
* **自定义对局**：AoE4World 不收录的对局，直接读回放文件判断（不依赖服务器）
* **号主判定**：靠回放里的 Steam ID 区分「这局是谁的」，不看名字（重名常见）
* **收藏**：任意对局点 ☆ 收藏，可在「已收藏对局」页写自己的备注
* **库存对账**：`playback\` 目录 + 「回放库」两边一起扫，认出的 game_id 就是「已下载」，不会重复下

界面是深色单页，本地端口起服务，无构建、无前端框架。

```
┌ 概览 100 场 / 已有 4 / 缺 96 ──┬ 时间 地图 模式 时长 文明 队友对手 均分 状态 操作 ──┬ 浏览器面板 ─┐
│ 模式筛选（快速4v4 98 场…）    │ ▎10-21 Hidden Valley 31:35 朱希传承 1731 ⚠缺签名地址 │ 队列进度    │
│ 只看胜局 / 只有缺的            │ ▎10-20 Fangs 38:58 圣殿骑士 1798 ✓已下载  [回放]    │ 手动登记地址 │
└───────────────────────────────┴──────────────────────────────────────────────────┴─────────────┘
```

---

## 一、快速开始

### 从零开始（第一次用，照这个顺序走）

**第 1 步 · 装 Python（没装过就做这步）**

需要 **Python 3.10 或更新**。打开命令行输 `python --version`，看到 `3.10` 以上就跳过本步。

| 方式 | 地址 |
|---|---|
| 官网（推荐） | https://www.python.org/downloads/ |
| 直链（Windows 64 位） | https://www.python.org/ftp/python/3.12.10/python-3.12.10-amd64.exe |
| 微软商店 | 搜「Python 3.12」 |

⚠️ **安装时一定要勾上 `Add python.exe to PATH`**（安装向导第一页最底下那个勾），否则 `.bat` 找不到 Python。

装完重开一个命令行窗口，验证：

```
python --version     → Python 3.12.x
py --version         → 也行
```

**第 2 步 · 拿到代码**

```
git clone https://github.com/<你的用户名>/aoe4-replay-studio.git
```

或者网页上 **Code → Download ZIP** 解压。**别放在中文路径太深的目录**（虽然支持，但省事）。

**第 3 步 · 装可选依赖**

双击 **`安装依赖.bat`**。它会自动：

1. 找到 Python（PATH → `%LOCALAPPDATA%\Programs\Python` → `C:\Python3*`）
2. 检查版本是否 ≥ 3.10，太旧会提示下载地址
3. pip 缺失就先 `ensurepip` 自举
4. 装 `requests` 和 `websockets`
5. 哪步失败会打印原因，**不装也能跑**（见下表）

也可以手动：

```
pip install -r requirements.txt
```

**第 4 步 · 启动**

双击 **`启动.bat`**（或 `python run.py`）。会自动打开浏览器到 `http://127.0.0.1:8787/`。`8787` 被占用时自动顺延（8788、8789…），控制台会打印实际端口，同时写进 `data/port.txt` 供录制扩展使用。

**第 5 步 · 填两个值**

界面右上角「设置」：

| 项 | 去哪找 |
|---|---|
| **玩家 ID**（`profile_id`） | AoE4World 玩家页 URL 里那串数字：`aoe4world.com/players/`**`12345678`**`/games` |
| **Steam ID** | 17 位数字。Steam 客户端 → 个人资料 → 编辑 → 或 steamid.io |

其余路径（`playback` / `network` / Steam / 浏览器）**全部自动探测**，不用填。

### 依赖缺失会损失什么

程序**只用标准库也能跑**，可选包缺了只是对应功能降级：

| 包 | 缺了会怎样 | 影响核心吗 |
|---|---|---|
| `requests` | 自动退回标准库 `urllib`，API 拉取、下载**都还能用**（只是并发/重试略弱） | ❌ 不影响 |
| `websockets` | 「浏览器面板」「录制操作」不可用，其余全部正常 | ❌ 不影响 |
| `fastapi` + `uvicorn` | 用内置 `http.server` 代替，`/docs` 自动文档没有 | ❌ 不影响 |

启动时会打印体检结果：

```
[!] 缺少可选的 Python 包（不影响启动，但对应功能不可用）：
    - requests     → AoE4World 数据接口 / 回放下载
    - websockets   → 浏览器面板与操作录制（CDP）

    一键安装：双击 安装依赖.bat，或手动执行：
      "C:\...\python.exe" -m pip install -r requirements.txt
```

### 环境要求一览

| 项 | 要求 | 必需吗 |
|---|---|---|
| Python | **3.10+** | ✅ 必需 |
| 系统 | Windows（依赖注册表 / `tasklist` / Steam 命令行） | ✅ |
| 游戏 | 已安装 AoE4 + Steam | ✅ 播放必需 |
| 浏览器 | 任意（看界面） | ✅ |
| Chrome / Edge / Brave | 只有「录制」功能需要 | ⭕ 可选 |
| git | 只有 clone 方式需要 | ⭕ 可选 |

### 首次运行会生成

```
data/config.json          本机配置（玩家 ID、各路径、端口），可手改，也可在界面里改
data/replay.db            库存索引 + 收藏（SQLite）
data/lcs_*.json           各类缓存
data/replays/             新下载的回放
data/browser-profile/     独立浏览器配置，不和日常 Chrome 混用
```

这些都在 `.gitignore` 里，不会被提交。

---

## 二、录像怎么拿到（重要修正）

早期版本判断「录像只能由游戏客户端换取」，这个结论是错的。实际存在一个公开接口：

```
GET https://aoe-api.worldsedgelink.com/community/leaderboard/getReplayFiles
        ?matchIDs=[250514479,249396481]&title=age4
```

**不需要登录、不限对局新旧**，直接返回带签名的下载地址：

```json
{"result":0,
 "expiryUnix": 1792610360,
 "replayFiles": [
   {"profile_id": 9201707, "matchhistory_id": 256755022,
    "url": "https://rl0aoelivemk2blob.blob.core.windows.net/cloudfiles/…/M_256755022_<hash>.gz?sig=…"}
 ]}
```

要点（都写在 `aoereplay/relay.py`）：

| 项 | 值 |
|---|---|
| 一批最多 | 10 个 matchID |
| 签名有效期 | 约 3 分钟（`expiryUnix`） |
| 同一场 | 返回多份副本，按 `profile_id` 区分；优先取自己那份 |
| User-Agent | **必须**声明 `AoE4ReplayLauncher-ChromeExt/1.0 …`；伪装浏览器 UA 会被 409/403 |
| 429 | 指数退避 5s→60s |

实测（2026-10-22）：

```
250514479  两个月前 → 353 KB → 解压 1.19 MB  Relic Chunky ✓
249396481  两个月前 → 338 KB → 解压 1.18 MB  Relic Chunky ✓
245621493  更早     → 554 KB → 解压 2.06 MB  Relic Chunky ✓
256752131  近期     → 843 KB → 解压 3.14 MB  Relic Chunky ✓
```

**下载回来的文件是 gzip 包着的 `,,AOE4_RE` 包裹**（`1f 8b` 开头）。解压**一层**就是游戏 `playback\` 认的格式：

```
云端原始   1f 8b ...                        gzip
   ↓ 解一层
 00 00 ,,AOE4_RE + 日期(UTF-16) + 块流       ← 存这个
   ↓ 再解一层（不要！）
 "Relic Chunky" 裸流                         ← 剥了它就丢版本头
```

**踩过的坑**：一开始我解到 `Relic Chunky` 才存盘，结果游戏每次都报

```
Ignoring replay [playback:AgeIV_Replay_256662570]
  as it is version [26988] and current build is [11308]
```

界面停在 `DevOnlySkipIntroFlow` 红字页。原因不是版本旧，是**外层头（带着版本号）被我剥掉了**。
改成只解一层后，同一场立刻正常进加载界面。代码在 `gz.py` 的 `to_playback()` / `is_playback_format()`。

> 参考实现：Chrome 扩展 [AoE4 Replay Launcher](https://chromewebstore.google.com/detail/aoe4-replay-launcher/ckkbdeejodfnpehhllhmhhannpgojfec)
> （`github.com/spartain-aoe/aoe4world-replay-extension`），本工具的接口用法即来自对它的分析。

所以下载这件事是**纯脚本可完成**的：界面上点哪场下哪场，不用登录、不用开游戏、不看你何时下载。

---

## 三、一键播放

点每行的 ▶，三种走向：

```
▶
├─ 本地没有 → 自动下载 → 下完自动启动回放（不用再点第二次）
├─ 本地已有 → 直接投送 playback 并拉起 Steam
└─ 游戏正在运行 → 先退出游戏（引擎只在启动时读 -replay）
```

界面上会跟着报：`⏳ 正在下载` → `✅ 下载完成，正在启动回放…` → 「最近启动」显示完整命令行。

`⭳` 按钮仍然只下载、不启动，批量囤货时用它。

---

## 四、为什么还需要「浏览器面板」

现在只有两个用途，跟下载无关：

1. 查看某场在 AoE4World 上的完整页面（对局详情、Timeline 图表）
2. 录制操作（见第九节）

原先那套「抓签名地址」的逻辑已经不用了，`actor` 仅保留为可选的浏览窗口。

---

## 五、回放是怎么起起来的

沿用《调查报告》里已验证的路径，没有改游戏本体：

```
steam.exe -applaunch 1466860 -dev -replay "playback:AgeIV_Replay_<game_id>"
```

细节：

* 投送前会用 `tasklist` 查 `RelicCardinal.exe`，在跑就直接返回「请先退出游戏」——引擎只在启动时读 `-replay`，这一点没变
* 投送时统一解包成裸 `Relic Chunky` 写进 `playback\AgeIV_Replay_<id>`（游戏自己的命名规则），
  与 `playback\` 里现有 44 个文件的构造完全一致（实测 44/44 都能正常解出 `Relic Chunky`）
* 「重下」会把新文件覆盖回 `playback\`，旧文件保留在 `回放库\`

---

## 六、常用操作

| 操作 | 方法 |
|---|---|
| 只要最近没下过的 | 左侧「只有缺的」+ 勾「全选」+「下载选中」 |
| 一次补全部 | 「下载全部缺失」 |
| 看某把的对局详情 | 行尾「网页」→ 在独立窗口打开该把的 AoE4World 页面 |
| 直接播 | 行尾「回放」（Ctrl+Enter 播第一个勾选的） |
| 键盘 | `Space` 勾选当前行，`D` 下载勾选 |
| 导出清单 | 顶部「导出 CSV」（带 UTF-8 BOM，Excel 直接开不花屏） |
| 换玩家 | 「设置」里改 profile_id（自动拉名字） |

---

## 七、接口（想自己写脚本/接自动化时用）

后端默认用标准库 `http.server`（不依赖 Flask/FastAPI）；装了 FastAPI+uvicorn 会自动切换，并多一个 `/docs`。

```
GET  /api/ping             几乎没有开销的心跳，返回 {"app":"aoe4-replay-studio"}；扩展/本机桥靠它找端口
GET  /api/state            配置 + 对局表 + 库存 + 队列 + 游戏进程状态（一个请求拿全）
POST /api/refresh          {"pages":2} 由服务端决定，强制同步
POST /api/download         {"game_ids":[256755022]}
POST /api/retry            {"game_ids":[...]}
POST /api/cancel           {}
POST /api/play             {"game_id":256672896}
POST /api/actor/start      启动独立浏览器（CDP:9333 起）
POST /api/actor/open       {"game_id":...} 在新窗口打开该把的网页
POST /api/manual_url       {"url":"…M_<id>_<hash>.gz?…","authorization":"XBL3.0 x=…"}
POST /api/settings         {"profile_id":…, "playback_dir":"…", …}
GET  /api/game/{id}        单场完整数据（官方 detail 接口）
GET  /api/export           已下载清单 CSV
```

写操作（所有 `POST`）只接受 `Content-Type: application/json`，且带 `Origin` 时必须是本机同源
或浏览器扩展 —— 否则返回 403。这样浏览器里随便哪个网页都没法用 `<form>` 往 127.0.0.1 的端口
发写操作。自己写脚本用 `curl`/`requests` 不受影响（不带 Origin 就放行）。

`/api/state` 每行的关键字段：

```json
{"game_id":256672896,"started_local":"2026-10-20 22:58","map":"Fangs","kind":"qm_4v4",
 "duration_text":"26:23","me":{"civ_zh":"圣殿骑士团","rating":1806,"result":"win"},
 "mates":["..."],"opponents":["..."],"owned":true,"status":"downloaded"}
```

`status` 取值：`downloaded | missing | queued | resolving | downloading | needs_login | error`。

---

## 八、坑与对策

| 现象 | 原因 | 对策 |
|---|---|---|
| 顶部显示「离线 · 用缓存」、提示 HTTP 429 | AoE4World 反爬，且**明确拒绝伪造浏览器 UA**（原文：`Blocked for webscraping … and spoofing user agent`） | 已改成真实 UA `AoE4ReplayStudio/1.0` + 429 后冷却 30s，重试即可恢复；界面同时显示缓存数据不会空 |
| 下载一直 `needs_login` | 还没记账（没登录或没在游戏里点开过那场回放） | 右上「浏览器面板」登录 → 游戏里点开一次 → 回来重试 |
| 下载 `HTTP 409` | 拿到的是裸地址（没签名）或签名已过期 | 重新在游戏里点开那场回放，让工具记账新的签名地址 |
| 点「回放」提示先退游戏 | 引擎只在启动时读 `-replay` | 退出游戏再点（工具不会偷偷杀进程） |
| 下载目录在 D 盘、playback 在 C 盘 | 正常，工具按卷复制，不跨盘软链 | 无需处理 |
| 想把已有回放也给旧启动器用 | 两边共用同一个「回放库」 | 已默认共用，无需处理 |
| 点「开始录制」提示浏览器面板没开 | 录制走的是内置浏览器那条 CDP 通道 | 先点右上「浏览器面板」，或用 `安装录制扩展.bat` 走日常 Chrome |
| 录制里没有游戏动作 | 游戏是在 `-replay` 参数下启动的，只在启动瞬间出现 | 每 0.4s 轮询一次，极少漏；漏了重开一次回放即可 |
| 回放时点歪了 | 录制时的窗口尺寸和现在不同 | 工具按归一化坐标缩放；仍有偏差就先用「视觉级」看一眼落点 |

---

## 九、文件结构

```
aoe4-replay-studio\
  启动.bat / 自检.bat / run.py      入口
  安装录制扩展.bat                  给日常 Chrome 装录制扩展 + 本机桥
  aoereplay\
    config.py      路径探测（Documents/OneDrive、Steam 注册表、Chrome）
    api.py         AoE4World API（真实 UA、429 冷却、page 分页）
    library.py     库存扫描 + game_id 对账（指纹兜底）+ SQLite 索引
    gz.py          Relic 包裹层解包/还原
    launcher.py    投送 playback + -replay 启动 + 进程检测
    downloads.py   下载队列（3 并发、进度、状态机）
    actor.py       独立浏览器 + CDP 抓签名地址 + 地址台账
    browser_setup.py 独立配置初始化
    civs.py        文明中文名
    recorder.py    操作监视：注入捕获脚本 + CDP 事件 → 动作脚本
    mimic.py       拟我回放：脚本级 / 视觉级 / 双轨
    replay_watch.py 游戏进程 -replay 参数 + playback 目录监视
    server.py      Hub + 两套后端（stdlib / FastAPI）
  web\app.html     单页界面（无构建、无外部依赖）
  extension\       MV3 扩展（capture.js / background.js / manifest.json）
  native\
    pack_extension.py  纯标准库 RSA-2048 签名，打出可安装的 crx
    rsa_crx.py         密钥生成 / DER 编解码 / PKCS#1 v1.5 签名
    install_host.py    注册本机桥 + Chrome 外部扩展
    native_host.py     stdio 桥：把扩展录到的动作投给本地工作台
  tests\           启动冒烟测试（纯标准库，`python -m unittest discover -s tests`）
  data\            运行期数据（除 mapthumbs/ civflags/ 外都不进仓库）
```

### 开发与测试

```bash
python -m unittest discover -s tests -v      # 冒烟测试，不用装任何第三方包
python run.py --selftest                     # 自检：配置 / 库存 / API / 启动命令
```

测试覆盖的是「坏了会静默出错」的那几处：服务能不能起来、`/api/ping` 端口探测、
`/mapthumbs/..\..\data\config.json` 这类路径穿越、以及跨站表单往本机端口发写操作。
CI 在 `windows-latest` 上跑 Python 3.10 / 3.12，并额外验证「一个第三方包都不装」也能通过。

`config.json` 关键项：

```json
{
  "profile_id": 14579308,
  "steam_id": "7656119xxxxxxxxxx",
  "poll_seconds": 60,
  "playback_dir": "C:\\Users\\<you>\\Documents\\My Games\\Age of Empires IV\\playback",
  "network_dir": "C:\\Users\\<you>\\Documents\\My Games\\Age of Empires IV\\network",
  "port": 8787
}
```

`profile_id` 就是 AoE4World 玩家页 URL 里那串数字；`steam_id` 是 17 位数字，用于判定「哪些对局是我的」。
两项都可以在界面「设置」里改，不用手编文件。

---

## 十、录制与拟人回放（故障排查备用）

点右上角 **录制我的操作**。它做三件事：**记录你怎么操作 → 存成动作脚本 → 在同一个浏览器里照着演一遍**。

### 10.1 监视什么

| 类别 | 抓到的字段 | 来源 |
|---|---|---|
| 鼠标点击 | 坐标、归一化坐标 0~1、目标元素描述、CSS 选择器 | 注入页面的捕获脚本（`recorder.py` 的 `CAPTURE_JS`） |
| 键盘 | 键名、code、Ctrl/Shift/Alt/Meta | 同上 |
| 输入框 | 选择器 + 输入值 | 同上 |
| 滚动 / 滚轮 | 滚动位置、delta | 同上 |
| 网络 | `.gz` / `blob.core.windows.net` 请求的 URL、方法、`Authorization`/`Referer` 头、响应状态 | CDP `Network.*` 事件 |
| **游戏动作** | 启动回放时进程命令行里的 `-replay playback:<名字>`、playback 目录里新出现的回放文件 | `replay_watch.py` 每 0.4s 查 `RelicCardinal.exe` 的 CommandLine |

游戏那一路是关键：**你在游戏菜单里点「Watch Replay」这个动作没有鼠标坐标可抓**，但它的结果（命令行参数 `-replay`）能被读到，所以这一条被单独记成 `kind:"game"` 的动作。这样一条完整链路（网页选录像 → 启动游戏 → 进入回放）都能被同一条时间轴串起来。

### 10.2 回放三种模式

| 模式 | 行为 | 用途 |
|---|---|---|
| 脚本级 `script` | 真的派发 `Input.dispatchMouseEvent / dispatchKeyEvent`，页面会真的点下去 | 批量重放你的操作、跑回归 |
| 视觉级 `visual` | 只移动金色指针 + 点击涟漪 + 气泡文字，**不触发真实点击** | 演示「我刚才怎么做的」、预演危险动作 |
| 双轨 `both` | 两者同时 | 一边演示一边真跑 |

视觉级已实测：同样的点击动作，`visual` 模式下页面状态不变，`script` 模式下页面进入 `CLICKED`。

### 10.3 「我平时用 Chrome，不想换浏览器」

用 `安装录制扩展.bat`。它做完整链路（已在本机跑通）：

```
1. 生成 RSA-2048 密钥（纯标准库，约 1~3 秒）→ data/lcs-recorder.pem
2. 打包签名 crx（Cr24 v3 头 + SPKI + PKCS#1 v1.5 SHA-256 签名）→ data/lcs-recorder.crx
   扩展 ID = sha256(SPKI) 前 16 字节映射到 a-p（**ID 由你本机生成的密钥决定**，所以每台机器不同）
3. 注册本机桥：
   HKCU\Software\Google\Chrome\NativeMessagingHosts\com.lcs.aoe4recorder
4. 登记 Chrome 外部扩展（免开发者模式）：
   HKCU\Software\Google\Chrome\Extensions\<扩展ID>  path/version
```

之后退出 Chrome 再打开，工具栏就有「LCS 操作录制器」：点一下开始录，再点一下结束——
动作会 **HTTP 投给本地工作台**（开着就进同一个动作流），没开就落到 `data/scripts/`。

```bash
# 手动拆分执行也行
python native/pack_extension.py --ext extension --out data/lcs-recorder.crx --key data/lcs-recorder.pem --print-id
python native/install_host.py --ext-id <打印出来的ID>
python native/install_host.py --ext-id <ID> --uninstall     # 卸载
```

Chrome 对「外部扩展」的官方行为：首次装好后会把注册表键降级为只读，再次强制需要删键重登记，这是 Chrome 侧的规矩，不是本工具的坑。

### 10.4 动作文件格式

```json
{
  "version": 1,
  "kind": "lcs-action-script",
  "name": "session-1022-0305",
  "viewport": {"url": "https://aoe4world.com/...", "w": 1424, "h": 805, "dpr": 1},
  "actions": [
    {"kind": "click", "x": 120, "y": 90, "button": 0, "selector": "#b1",
     "target": "button#b1 · 按钮A", "t": 1200, "delay": 1200},
    {"kind": "key", "key": "Enter", "code": "Enter", "ctrl": false, "t": 1900, "delay": 700},
    {"kind": "game", "action": "launch_replay", "arg": "playback:AgeIV_Replay_256672896", "t": 5200, "delay": 3300}
  ]
}
```

`delay` 是相对上一条的毫秒间隔，回放时按它节流（再除以倍速）。
坐标同时保留绝对值和归一化值：换分辨率时按 `w/h` 等比缩放，落点仍然对。

### 10.5 接口

```
POST /api/recorder/start       开始录制（同时启动游戏进程监视 + playback 目录监视）
POST /api/recorder/stop        停止
POST /api/recorder/save        {"name":"..."} 存脚本到 data/scripts/
GET  /api/recorder/state       录制状态、动作统计、已存脚本、游戏事件
POST /api/recorder/actions     {"n":40} 最近动作
POST /api/recorder/poll        兜底补抓（注入脚本掉线时从页面事件队列里捞）
POST /api/recorder/ingest      外部整段导入（扩展 / 手工 JSON）
POST /api/mimic/play           {"name":"...","mode":"script|visual|both","speed":1}
POST /api/mimic/stop
GET  /api/mimic/state          进度百分比 / 当前动作说明
```

---

## 已知限制（有意留着的，不是待办）

按现状发布，下面这些**不会**再改。写在这里是为了让你别花时间去查：

| 项 | 现状与影响 |
|---|---|
| 服务没有鉴权 | 只为监听 `127.0.0.1` 设计。用 `--host` 绑到别的地址等于把「改配置 / 启动回放 / 读对局」对局域网敞开，启动时会打印警告 |
| 扩展申请全站 host 权限 | 「录制任意网页的操作」需要它。要上 Chrome 商店必须收窄成 `optional_host_permissions` + 录制时申请 |
| `data/url-ledger.json` 落盘凭据 | 里面存带签名的下载地址和 `Authorization` / `XBL3.0` 头（下载功能需要）。已在 `.gitignore` 里，仍请当成密码对待 |
| `gz.wrap()` 的「还原云同步命名」路径不可用 | 它去文件里找 zlib 头，真实包裹层没有。目前没有任何调用方传 `as_cloud_name=True`，所以是死路径，不影响下载与播放 |
| `gz.is_playback_format()` 接受裸 Relic Chunky 流 | 引擎其实会忽略裸流。收紧会让边缘样本变成硬失败，权衡后保留宽松判定 |
| 死代码 | `aoereplay/apiversion.py` 没有被 import；`mapthumbs.cache_all()`、`library.link_into()` 没有调用方 |
| 默认 `profile_id` 是第三方公开档案 | 目的是首次打开界面就有内容可看。换成自己的 `profile_id` 只需在「设置」里改一下 |
| 地图缩略图 / 文明旗是游戏美术资源 | 版权归微软 / Relic。随仓库分发只是为了离线可用，被投诉就得自己删 `data/mapthumbs`、`data/civflags`（界面会自动回退到线上地址） |
| 没有自动更新、没有数据迁移 | 只支持 Windows。回放库、收藏、脚本都在 `data/` 里，升级靠你自己备份 |
