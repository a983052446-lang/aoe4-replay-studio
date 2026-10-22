# -*- coding: utf-8 -*-
"""Chrome native messaging host：把扩展录到的动作转交本地工作台。

协议：stdin/stdout 上每帧 = 4 字节小端长度 + UTF-8 JSON。
浏览器把最后一帧里的 "path" 当作 host 可执行文件；这里被 .bat 拉起，
所以**不能**往 stdout 写日志，调试信息全走 stderr 或文件。
"""
from __future__ import annotations

import json
import os
import re
import socket
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOG = ROOT / "data" / "native_host.log"
PORT_FILE = ROOT / "data" / "port.txt"
DEFAULT_OUT = ROOT / "data" / "scripts"
# 端口候选：服务启动时把实际端口写进 data/port.txt（默认 8787，被占用会见位到 8788+）。
# 写死 8787 的老写法在端口顺延后会让动作静默丢掉，所以这里必须动态找。
PORT_FALLBACK = range(8787, 8787 + 12)
# 单帧上限：一段动作脚本撑死几 MB，超过这个数只能是流错位了
MAX_FRAME = 32 * 1024 * 1024


def _log(msg: str) -> None:
    try:
        LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(LOG, "a", encoding="utf-8") as fh:
            fh.write(msg + "\n")
    except OSError:
        pass


def _candidates() -> list[int]:
    out: list[int] = []
    try:
        v = int(PORT_FILE.read_text(encoding="utf-8").strip())
        if 1 <= v <= 65535:
            out.append(v)
    except (OSError, ValueError):
        pass
    out.extend(p for p in PORT_FALLBACK if p not in out)
    return out


def _port_open(port: int, timeout: float = 0.15) -> bool:
    """端口上有没有人在听。

    直接用 socket 连一下，别用 urlopen 去试 —— 这台机器上连一个没人监听的
    127.0.0.1 端口是**超时**而不是立即 refused（安全软件/防火墙丢包），
    12 个候选 × 1.5s 就是 18 秒，Chrome 那边早就等不下去了。
    """
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=timeout):
            return True
    except OSError:
        return False


def find_endpoint() -> str:
    """探一遍 /api/ping，返回真正活着的那个端口的 ingest 地址；都没有就返回空串。"""
    for port in _candidates():
        if not _port_open(port):
            continue
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/ping", timeout=1.0) as r:
                if r.status == 200:
                    body = r.read(200)
                    if b"aoe4-replay-studio" in body:
                        return f"http://127.0.0.1:{port}/api/recorder/ingest"
        except Exception:  # noqa: BLE001
            continue
    return ""


def send(obj: dict) -> None:
    data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
    sys.stdout.buffer.write(len(data).to_bytes(4, "little"))
    sys.stdout.buffer.write(data)
    sys.stdout.buffer.flush()


def forward(payload: dict) -> bool:
    endpoint = find_endpoint()
    if not endpoint:
        _log("forward failed: 没找到在跑的本地工作台（8787~8798 都没响应）")
        return False
    try:
        req = urllib.request.Request(
            endpoint,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=5) as r:
            return 200 <= r.status < 300
    except Exception as e:  # noqa: BLE001
        _log(f"forward failed: {e}")
        return False


def save_local(payload: dict) -> str:
    """没连上工作台时先落盘，别让录到的动作凭空消失。"""
    DEFAULT_OUT.mkdir(parents=True, exist_ok=True)
    raw = str(payload.get("name") or "chrome-session")
    # 名字来自扩展，可能带 \ / : 或 ..；只留下安全字符，防止写出 data/scripts 之外
    name = re.sub(r"[^A-Za-z0-9_.-]+", "-", raw).strip("-.") or "chrome-session"
    path = DEFAULT_OUT / f"{name[:60]}-{os.getpid()}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    return str(path)


def read_exact(stream, n: int) -> bytes | None:
    """读满 n 字节；流关了返回 None。

    必须按「先 4 字节长度、再正好 size 字节」读。老的写法是一次 read(65536)，
    管道上这会一直等到攒够 64 KiB 或 EOF —— 一帧才几 KB，于是这一帧永远处理不到，
    Chrome 那边只会看到「native host has exited」。
    """
    buf = bytearray()
    while len(buf) < n:
        chunk = stream.read(n - len(buf))
        if not chunk:
            return None
        buf += chunk
    return bytes(buf)


def main() -> None:
    _log("native host started")
    stdin = sys.stdin.buffer
    while True:
        head = read_exact(stdin, 4)
        if head is None:
            break
        size = int.from_bytes(head, "little")
        if size <= 0 or size > MAX_FRAME:
            # 头是坏的（或超限）：流已经没法再对齐，直接收工
            _log(f"bad frame length {size}, aborting")
            return
        body = read_exact(stdin, size)
        if body is None:
            break
        try:
            payload = json.loads(body.decode("utf-8"))
        except Exception as e:  # noqa: BLE001
            _log(f"bad payload: {e}")
            continue
        if not isinstance(payload, dict):
            _log(f"payload 不是对象: {type(payload).__name__}")
            continue
        ok = forward(payload)
        saved = save_local(payload) if not ok else ""
        try:
            send({"ok": ok, "forwarded": ok, "saved": saved,
                  "actions": len(payload.get("actions") or [])})
        except Exception as e:  # noqa: BLE001
            _log(f"send failed: {e}")
            return
    _log("native host exit")


if __name__ == "__main__":
    main()
