"""AoE4 回放工作台启动入口。

用法：
    python run.py                 # 起本地服务并打开界面
    python run.py --port 9000
    python run.py --no-browser
    python run.py --selftest      # 只跑一遍自检（API、库存、路径、启动命令）
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
import webbrowser
from pathlib import Path

# 不写 __pycache__：.pyc 里会嵌进源文件的绝对路径（本机目录结构），
# 分发出去等于泄露目录结构，而且每次跑都会重新生成。字节码缓存对这小项目毫无收益。
sys.dont_write_bytecode = True

sys.path.insert(0, str(Path(__file__).resolve().parent))

from aoereplay import deps, launcher  # noqa: E402
from aoereplay.config import Config  # noqa: E402
from aoereplay.server import Hub, make_server  # noqa: E402


def _banner(cfg: Config, backend: str) -> None:
    url = f"http://{cfg.host}:{cfg.port}/"
    print("=" * 68)
    print("  AoE4 回放工作台")
    print("=" * 68)
    print(f"  界面        {url}")
    print(f"  后端        {backend}")
    print(f"  玩家        {cfg.profile_name} ({cfg.profile_id})")
    print(f"  playback    {cfg.playback_dir}")
    print(f"  回放库      {cfg.library_dir}")
    print(f"  下载目录    {cfg.download_dir}")
    print(f"  Steam       {cfg.steam_exe or '未找到'}")
    print(f"  界面浏览器    {cfg.ui_browser or '未找到'}")
    print(f"  面板浏览器    {cfg.actor_browser or '未找到（需 Chromium 系）'}")
    print("=" * 68)
    print("  Ctrl+C 退出")
    print()


def selftest() -> int:
    cfg = Config()
    hub = Hub(cfg)
    print("[1] 配置")
    print(json.dumps(cfg.snapshot(), ensure_ascii=False, indent=2))
    print("[2] 库存扫描")
    hub.rescan()
    owned = hub.store.owned()
    print(f"    已入库 {len(owned)} 场：{sorted(owned)[-6:]}")
    print("[3] API（最近 2 页）")
    print("   ", hub.refresh(pages=2))
    print("[4] 录像直链接口探测")
    st = hub.state()
    print("   ", st["capabilities"])
    print("[5] 游戏进程 / Steam")
    print("    游戏运行中:", launcher.is_game_running(), "| Steam:", cfg.steam_exe)
    print("[6] 首场对局行")
    if st["games"]:
        g = st["games"][0]
        print("   ", json.dumps({k: g[k] for k in ("game_id", "started_local", "map", "status", "me")}, ensure_ascii=False))
    print("[7] 启动命令样例（不执行）")
    print(f"    \"{cfg.steam_exe}\" -applaunch 1466860 -dev -replay playback:AgeIV_Replay_256672896")
    return 0


def find_free_port(host: str, start: int, tries: int = 12) -> int:
    """端口被占就顺延，免得双击后直接崩。

    注意：不能设 SO_REUSEADDR —— Windows 上它允许绑定到已被监听的端口，
    探测会永远认为“可用”，结果两个实例抢同一个端口，第二个静默失败。
    """
    import socket

    for p in range(start, start + tries):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind((host, p))
                return p
            except OSError:
                continue
    raise SystemExit(f"{start}~{start + tries - 1} 全被占用，换个 --port 再试")


def main() -> int:
    ap = argparse.ArgumentParser(description="AoE4 回放工作台")
    ap.add_argument("--port", type=int, default=0)
    ap.add_argument("--host", default="")
    ap.add_argument("--no-browser", action="store_true")
    ap.add_argument("--backend", choices=["auto", "stdlib", "fastapi"], default="auto")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--install-deps", action="store_true", help="装可选依赖后退出")
    args = ap.parse_args()

    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8")
            sys.stderr.reconfigure(encoding="utf-8")
        except Exception:
            pass

    # 依赖体检：缺包不拦路，但要说清楚缺什么、怎么装
    if args.install_deps:
        print(f"[deps] Python {sys.version.split()[0]}")
        res = deps.install_optional()
        for p in res["installed"]:
            print(f"   OK  {p}")
        for p, why in res["failed"].items():
            print(f"   [x] {p}: {why}")
        print(f"\n还缺：{deps.missing() or '无'}")
        return 0

    deps.report()

    if args.selftest:
        return selftest()

    cfg = Config()
    if args.host:
        cfg.host = args.host
    want = args.port or cfg.port
    cfg.port = find_free_port(cfg.host, want)
    if cfg.port != want:
        print(f"[!] 端口 {want} 被占用，改用 {cfg.port}")

    # 实际端口落盘：浏览器扩展和 native host 都靠它找到服务。
    # 少了这一步，8787 一旦被占用（或者用户在设置里改了端口），录制那条链路
    # 会连到一个没人监听的端口上，动作就这么静默丢掉。
    try:
        (cfg.data_dir / "port.txt").write_text(str(cfg.port), encoding="utf-8")
    except OSError:
        pass

    # 首次运行提示：分发版没有预置配置，得让使用者自己填这两个
    if not cfg.steam_id:
        print("[!] 还没设置 Steam ID —— 号主判定会全部显示「无法判断」")
        print("    界面右上角「设置」里填 17 位 Steam ID 即可")
    if not cfg.profile_id:
        print("[!] 还没设置玩家 profile_id —— 对局列表会是空的")
    # 服务没有任何鉴权（设计如此：只听本机）。绑到别的地址等于把「启动游戏、改配置、
    # 下载文件」这些写操作对局域网公开，这里必须把话说死。
    if cfg.host not in ("127.0.0.1", "localhost", "::1", ""):
        print("=" * 68)
        print(f"[!!] 正在监听 {cfg.host}:{cfg.port} —— 不是本机回环地址。")
        print("     这个服务没有登录/鉴权，局域网内任何人都能改配置、启动回放、读你的对局。")
        print("     除非你清楚在做什么，请用 --host 127.0.0.1（默认值）。")
        print("=" * 68)

    hub = Hub(cfg)
    hub.refresh(pages=2)
    hub.start_background()

    server, backend = make_server(hub, prefer=args.backend)
    url = f"http://{cfg.host}:{cfg.port}/"
    _banner(cfg, backend)

    if not args.no_browser:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()

    try:
        if backend == "fastapi":
            import uvicorn

            uvicorn.run(server, host=cfg.host, port=cfg.port, log_level="warning")
        else:
            server.serve_forever()
    except KeyboardInterrupt:
        print("\n已退出")
    except OSError as e:
        print(f"\n[x] 服务起不来：{e}")
        return 1
    finally:
        try:
            hub.downloads.cancel_all()
            hub.actor.stop()
            hub.store.close()
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
