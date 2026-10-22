# -*- coding: utf-8 -*-
"""启动冒烟测试：真的把内置后端拉起来，用 HTTP 打一遍。

只依赖标准库（`python -m unittest discover -s tests`），CI 上不需要装任何东西。
覆盖的都是「坏了会静默出错」的地方：
  * 服务能不能起来、页面和图片能不能取到
  * 端口探测端点 /api/ping（浏览器扩展 / native host 靠它找端口）
  * 图片路径不能被 `..\\..\\data\\config.json` 这类请求穿出去
  * 跨站表单不能往本机端口发写操作
"""
from __future__ import annotations

import hashlib
import json
import shutil
import struct
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from aoereplay import config as cfgmod  # noqa: E402
from aoereplay import server  # noqa: E402


class GuardTest(unittest.TestCase):
    """不启服务也能测的纯函数。"""

    def test_safe_image_accepts_plain_name(self):
        self.assertIsNotNone(server._safe_image(server.THUMB_DIR, "altai.png"))

    def test_safe_image_blocks_backslash_traversal(self):
        # Windows 上反斜杠是分隔符，rsplit("/") 切不开它
        for evil in (r"..\..\data\config.json", r"..\config.json",
                     "..%5C..%5Cconfig.json", "../config.json",
                     "..", "altai.png.exe", "run.py", ""):
            self.assertIsNone(server._safe_image(server.THUMB_DIR, evil), evil)

    def test_reject_post_rules(self):
        self.assertIsNone(server._reject_post(None, "application/json"))
        self.assertIsNone(server._reject_post("http://127.0.0.1:8787", "application/json; charset=utf-8"))
        self.assertIsNone(server._reject_post("http://localhost:9000", "application/json"))
        self.assertIsNone(server._reject_post("chrome-extension://abcdef", "application/json"))
        # 表单类请求挡掉（浏览器 <form> 只能发这几种）
        self.assertIsNotNone(server._reject_post(None, "application/x-www-form-urlencoded"))
        self.assertIsNotNone(server._reject_post(None, "text/plain"))
        self.assertIsNotNone(server._reject_post(None, "multipart/form-data; boundary=x"))
        # 跨站 Origin 挡掉
        self.assertIsNotNone(server._reject_post("https://evil.example", "application/json"))


class RecorderTest(unittest.TestCase):
    """录制时间轴：外部灌进来的动作不可信，也不能让时间轴倒流。"""

    def setUp(self):
        from aoereplay.recorder import Recorder

        self.rec = Recorder(port_supplier=lambda: None, log=lambda *a: None)
        self.rec.recording = True
        self.rec.started_at = 1000.0

    def test_add_external_rejects_non_dict(self):
        self.assertIsNone(self.rec.add_external("nope"))
        self.assertIsNone(self.rec.add_external([1, 2]))
        self.assertEqual(self.rec.actions, [])

    def test_add_external_tolerates_string_ts(self):
        self.rec.add_external({"kind": "click", "ts": "not-a-number"})
        self.rec.add_external({"kind": "click", "ts": 1000123})
        self.assertEqual(len(self.rec.actions), 2)
        self.assertEqual(self.rec.actions[-1]["t"], 123)

    def test_normalize_order_fixes_backfilled_events(self):
        # 先有两条新的，再补抓一条更早的：追加顺序乱了，排序后 delay 必须非负且递增
        self.rec.add_external({"kind": "click", "ts": 1000500})
        self.rec.add_external({"kind": "click", "ts": 1000900})
        self.rec.add_external({"kind": "key", "ts": 1000200})
        self.rec._normalize_order()
        ts = [a["t"] for a in self.rec.actions]
        self.assertEqual(ts, sorted(ts))
        self.assertTrue(all(a["delay"] >= 0 for a in self.rec.actions))
        self.assertEqual(self.rec.actions[0]["delay"], 200)

    def test_ingest_script_shapes(self):
        from aoereplay.recorder import Recorder

        r = Recorder(port_supplier=lambda: None, log=lambda *a: None)
        r.recording = True
        r.started_at = 0.0
        # actions 写成对象、里面混入非 dict：都不能炸
        res = r.ingest_script({"name": "x", "actions": {"0": {"kind": "click"}, "1": "junk"}})
        self.assertTrue(res["ok"])
        self.assertEqual(res["merged"], 1)
        self.assertFalse(r.ingest_script("not a dict")["ok"])


class NativeHostTest(unittest.TestCase):
    """native host 的名字净化和端口探测。"""

    @classmethod
    def setUpClass(cls):
        sys.path.insert(0, str(ROOT / "native"))
        import native_host

        cls.nh = native_host

    def test_save_local_sanitizes_name(self):
        tmp = Path(tempfile.mkdtemp(prefix="aoe4rs-nh-"))
        old = self.nh.DEFAULT_OUT
        self.nh.DEFAULT_OUT = tmp
        try:
            p = Path(self.nh.save_local({"name": r"..\..\evil/name", "actions": []}))
            self.assertEqual(p.parent, tmp)
            self.assertNotIn("..", p.name)
        finally:
            self.nh.DEFAULT_OUT = old
            shutil.rmtree(tmp, ignore_errors=True)

    def test_read_exact(self):
        import io

        self.assertEqual(self.nh.read_exact(io.BytesIO(b"abcdef"), 6), b"abcdef")
        self.assertIsNone(self.nh.read_exact(io.BytesIO(b"ab"), 6))


class ConfigTest(unittest.TestCase):
    def test_as_int_falls_back(self):
        from aoereplay.config import _as_int

        self.assertEqual(_as_int("8787", 1), 8787)
        self.assertEqual(_as_int(None, 1), 1)
        self.assertEqual(_as_int("8 7", 1), 1)
        self.assertEqual(_as_int(0, 8787, lo=1), 8787)
        self.assertEqual(_as_int(99999, 8787, hi=65535), 8787)


class CrxTest(unittest.TestCase):
    """CRX3 头必须是 Chrome 认的那个结构（以前写成了 CRX2 的样子，装不上）。"""

    def test_crx3_header_layout(self):
        sys.path.insert(0, str(ROOT / "native"))
        import pack_extension as pe
        import rsa_crx

        k = rsa_crx.gen_keypair(1024)          # 测试用，够快
        spki = rsa_crx.spki_public_der(k)
        blob = pe.build_crx3(spki, k, b"PK\x03\x04payload")
        self.assertEqual(blob[:4], b"Cr24")
        ver, hlen = struct.unpack("<II", blob[4:12])
        self.assertEqual(ver, 3)
        header = blob[12 : 12 + hlen]

        def varint(b, i):
            v = sh = 0
            while True:
                c = b[i]
                i += 1
                v |= (c & 0x7F) << sh
                if not c & 0x80:
                    return v, i
                sh += 7

        i, fields = 0, {}
        while i < len(header):
            tag, i = varint(header, i)
            ln, i = varint(header, i)
            fields.setdefault(tag >> 3, []).append(header[i : i + ln])
            i += ln
        self.assertEqual(sorted(fields), [2, 10000])
        shd = fields[10000][0]
        self.assertEqual(shd[:2], b"\x0a\x10")                       # SignedData { crx_id: 16B }
        self.assertEqual(shd[2:], hashlib.sha256(spki).digest()[:16])
        self.assertEqual(blob[12 + hlen :], b"PK\x03\x04payload")

        # 字段 2 里必须是 public_key + signature，顺序不能错
        proof = fields[2][0]
        self.assertTrue(proof.startswith(b"\x0a" + bytes([len(spki)])))


class ServerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="aoe4rs-test-"))
        data = cls.tmp / "data"
        for sub in ("mapthumbs", "civflags", "library", "replays", "scripts", "playback", "network"):
            (data / sub).mkdir(parents=True)
        shutil.copy2(ROOT / "data" / "mapthumbs" / "altai.png", data / "mapthumbs" / "altai.png")
        shutil.copy2(ROOT / "data" / "civflags" / "english.png", data / "civflags" / "english.png")

        # 把所有路径钉在临时目录里：不读也不写仓库里那份 data/
        cfgmod.DATA_DIR = data
        cfgmod.CONFIG_PATH = data / "config.json"
        cfgmod.CONFIG_PATH.write_text(
            json.dumps({
                "profile_id": 14579308,
                "steam_id": "76561190000000000",
                "download_dir": str(data / "replays"),
                "library_dir": str(data / "library"),
                "playback_dir": str(data / "playback"),
                "network_dir": str(data / "network"),
                "poll_seconds": 0,
            }, ensure_ascii=False),
            encoding="utf-8",
        )
        # 模块级常量在 import 时就固定了，手动指过去
        server.WEB_DIR = ROOT / "web"
        server.THUMB_DIR = data / "mapthumbs"
        server.CIV_DIR = data / "civflags"

        cls.hub = server.Hub(cfgmod.Config())
        cls.hub.downloads.on_done = lambda *a, **k: None
        cls.hub.cfg.port = 0                       # 让系统给个空闲端口
        cls.srv = server.build_stdlib(cls.hub)
        cls.port = cls.srv.server_address[1]
        cls.thread = threading.Thread(target=cls.srv.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()
        try:
            cls.hub.store.close()
        except Exception:  # noqa: BLE001
            pass
        shutil.rmtree(cls.tmp, ignore_errors=True)

    # ---------- 请求工具 ----------
    def url(self, path: str) -> str:
        return f"http://127.0.0.1:{self.port}{path}"

    def get(self, path: str):
        try:
            with urllib.request.urlopen(self.url(path), timeout=10) as r:
                return r.status, r.read()
        except urllib.error.HTTPError as e:
            return e.code, e.read()

    def post(self, path: str, body: dict | None = None, *, ctype: str = "application/json",
             origin: str | None = None):
        data = json.dumps(body or {}).encode("utf-8") if ctype == "application/json" else b"a=1"
        headers = {"Content-Type": ctype}
        if origin:
            headers["Origin"] = origin
        req = urllib.request.Request(self.url(path), data=data, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, r.read()
        except urllib.error.HTTPError as e:
            return e.code, e.read()

    # ---------- 用例 ----------
    def test_index_serves_page(self):
        code, body = self.get("/")
        self.assertEqual(code, 200)
        self.assertIn(b"<html", body.lower())

    def test_ping(self):
        code, body = self.get("/api/ping")
        self.assertEqual(code, 200)
        payload = json.loads(body)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["app"], "aoe4-replay-studio")

    def test_queue(self):
        code, body = self.get("/api/queue")
        self.assertEqual(code, 200)
        self.assertIn("jobs", json.loads(body))

    def test_thumb_ok(self):
        code, body = self.get("/mapthumbs/altai.png")
        self.assertEqual(code, 200)
        self.assertEqual(body[:8], b"\x89PNG\r\n\x1a\n")

    def test_civflag_ok(self):
        code, _ = self.get("/civflags/english.png")
        self.assertEqual(code, 200)

    def test_traversal_blocked(self):
        # 反斜杠（URL 里 %5C）和普通的 .. 都试一遍
        for evil in ("/mapthumbs/..%5C..%5Cdata%5Cconfig.json",
                     "/mapthumbs/../../data/config.json",
                     "/civflags/..%5Cconfig.json",
                     "/mapthumbs/run.py"):
            code, body = self.get(evil)
            self.assertEqual(code, 404, evil)
            self.assertNotIn(b"profile_id", body)

    def test_post_json_allowed(self):
        code, _ = self.post("/api/cancel", {}, origin=f"http://127.0.0.1:{self.port}")
        self.assertEqual(code, 200)

    def test_post_form_csrf_blocked(self):
        code, body = self.post("/api/cancel", None, ctype="application/x-www-form-urlencoded")
        self.assertEqual(code, 403)
        self.assertIn("application/json", body.decode("utf-8"))

    def test_post_cross_origin_blocked(self):
        code, _ = self.post("/api/cancel", {}, origin="https://evil.example")
        self.assertEqual(code, 403)

    def test_unknown_route_404(self):
        code, _ = self.get("/api/nope")
        self.assertEqual(code, 404)


if __name__ == "__main__":
    unittest.main()
