# -*- coding: utf-8 -*-
"""注册本机桥清单 + 把扩展登记为 Chrome 外部扩展（强制安装，不需要开发者模式）。

Chrome 外部扩展（External Extensions）在 Windows 上的规则：
  * 必须给 .crx（带签名）的绝对路径，扩展 ID 由公钥派生，签一次就固定
  * 注册表位置 HKCU\\Software\\Google\\Chrome\\Extensions\\<扩展ID>
  * 缺省情况下 Chrome 首次装外部扩展后会把该键降级为只读，再次启动就不再强制，
    这是官方行为；需要重新装时删掉键再跑一次本脚本即可

Native Messaging 清单同理，放在 HKCU\\...\\NativeMessagingHosts\\<名字>，
清单里点名 allowed_origins 扩展 ID，别的扩展连不上这个桥。
"""
from __future__ import annotations

import argparse
import json
import winreg
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HOST_NAME = "com.lcs.aoe4recorder"
MANIFEST = ROOT / "native" / f"{HOST_NAME}.json"
LAUNCHER = ROOT / "native" / "native_host.bat"
CRX = ROOT / "data" / "lcs-recorder.crx"


def write_manifest(ext_id: str | None) -> Path:
    data = {
        "name": HOST_NAME,
        "description": "AoE4 回放工作台 · 操作录制桥",
        "path": str(LAUNCHER),
        "type": "stdio",
        "allowed_origins": [f"chrome-extension://{ext_id}/"] if ext_id else [],
    }
    MANIFEST.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return MANIFEST


def _manifest_version() -> str:
    """扩展版本号直接从 manifest.json 读。

    以前这里写死 "1.0.0"：Chrome 只在「注册表版本 == manifest 版本」时才认这条外部
    扩展记录，所以哪天把扩展版本改到 1.0.1，装扩展就会静默失效，谁也不知道为什么。
    """
    try:
        m = json.loads((ROOT / "extension" / "manifest.json").read_text(encoding="utf-8"))
        return str(m.get("version") or "1.0.0")
    except Exception:  # noqa: BLE001
        return "1.0.0"


def register_host() -> None:
    for sub in (
        rf"Software\Google\Chrome\NativeMessagingHosts\{HOST_NAME}",
        rf"Software\Microsoft\Edge\NativeMessagingHosts\{HOST_NAME}",
    ):
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, sub) as k:
            winreg.SetValueEx(k, "", 0, winreg.REG_SZ, str(MANIFEST))


def unregister_host() -> None:
    """删掉本机桥登记。只删键里的默认值不够，Chrome 会把空键也算作已登记。"""
    for sub in (
        rf"Software\Google\Chrome\NativeMessagingHosts\{HOST_NAME}",
        rf"Software\Microsoft\Edge\NativeMessagingHosts\{HOST_NAME}",
    ):
        try:
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, sub)
        except FileNotFoundError:
            pass
        except OSError:
            # 键已经不在了 / 权限不够：不阻塞卸载流程
            pass


def register_extension(ext_id: str) -> None:
    if not CRX.exists():
        raise FileNotFoundError(f"先打包扩展：{CRX}")
    sub = rf"Software\Google\Chrome\Extensions\{ext_id}"
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, sub) as k:
        winreg.SetValueEx(k, "path", 0, winreg.REG_SZ, str(CRX))
        winreg.SetValueEx(k, "version", 0, winreg.REG_SZ, _manifest_version())


def unregister_extension(ext_id: str) -> None:
    try:
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, rf"Software\Google\Chrome\Extensions\{ext_id}")
    except FileNotFoundError:
        pass


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ext-id", default="")
    ap.add_argument("--no-extension", action="store_true", help="只注册本机桥，不动 Chrome 扩展")
    ap.add_argument("--uninstall", action="store_true")
    a = ap.parse_args()

    if a.uninstall:
        # 卸载只删，不做任何写入 —— 老版本会先 write_manifest() 再删，
        # 等于把桥又装回去一遍，还顺手把 allowed_origins 清空。
        unregister_host()
        print(f"[bridge] 已移除本机桥登记 {HOST_NAME}")
        if a.ext_id:
            unregister_extension(a.ext_id)
            print(f"[chrome] 已移除外部扩展登记 {a.ext_id}")
        else:
            print("[chrome] 没给 --ext-id，扩展登记保留（可在 chrome://extensions 里手动删）")
        return

    if not a.ext_id and MANIFEST.exists():
        # 不带 ID 的重复运行不该把已经能用的清单冲成 allowed_origins: []
        print(f"[bridge] 沿用已有清单（未指定 --ext-id）：{MANIFEST}")
    else:
        write_manifest(a.ext_id)
        print(f"[bridge] {MANIFEST}")
    register_host()
    print(f"[bridge] 启动器 {LAUNCHER}")
    if a.ext_id and not a.no_extension:
        register_extension(a.ext_id)
        print(f"[chrome] 已登记外部扩展 {a.ext_id} → {CRX}")
    print(f"[id] {a.ext_id or '(未指定)'}")


if __name__ == "__main__":
    main()
