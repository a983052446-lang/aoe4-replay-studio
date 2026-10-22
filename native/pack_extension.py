# -*- coding: utf-8 -*-
"""把扩展目录打包成 Chrome 可安装的 .crx（含稳定 ID），纯标准库实现。

用法：
    python native/pack_extension.py --ext extension --out data/lcs-recorder.crx \
        --key data/lcs-recorder.pem --print-id

CRX3 结构（照 Chromium 的 crx_file/crx3.proto 来，不能自己发明）：

    "Cr24" | uint32 version=3 | uint32 header_len |
        CrxFileHeader {
            sha256_with_rsa: { public_key: <SPKI DER>, signature: <PKCS#1 v1.5 SHA-256> }   // 字段 2
            signed_header_data: { crx_id: sha256(SPKI)[:16] }                                // 字段 10000
        } | zip

    签名覆盖的字节 = "CRX3 SignedData\\x00" + uint32(len(signed_header_data))
                     + signed_header_data + zip

老版本这里写的是 `len(spki) | spki | sig`（像是半途从 CRX2 改过来的），Chrome 会把
那段当作 protobuf 解析、直接判为损坏 —— 装不上的。现在按字段逐个拼，和 Chrome 自己
`--pack-extension` 出来的结构逐字节对得上（已用 Chrome 产物反解验证过）。
"""
from __future__ import annotations

import argparse
import hashlib
import io
import struct
import zipfile
from pathlib import Path

import rsa_crx

CRX3_MAGIC = b"Cr24"
CRX3_VERSION = 3
SIGNATURE_CONTEXT = b"CRX3 SignedData\x00"   # 注意结尾那个 NUL


def _varint(n: int) -> bytes:
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        if n:
            out.append(b | 0x80)
        else:
            out.append(b)
            return bytes(out)


def _ld_field(field_no: int, payload: bytes) -> bytes:
    """写一个 length-delimited 字段：tag(varint) + len(varint) + 内容。"""
    return _varint((field_no << 3) | 2) + _varint(len(payload)) + payload


def zip_dir(src: Path) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in sorted(src.rglob("*")):
            if p.is_file() and not p.name.endswith((".pem", ".crx")):
                zf.write(p, p.relative_to(src).as_posix())
    return buf.getvalue()


def build_crx3(spki: bytes, key: dict, payload: bytes) -> bytes:
    crx_id = hashlib.sha256(spki).digest()[:16]
    signed_header_data = _ld_field(1, crx_id)          # SignedData { crx_id }
    signed_blob = (
        SIGNATURE_CONTEXT
        + struct.pack("<I", len(signed_header_data))
        + signed_header_data
        + payload
    )
    signature = rsa_crx.sign_sha256(signed_blob, key)
    proof = _ld_field(1, spki) + _ld_field(2, signature)   # AsymmetricKeyProof
    header = _ld_field(2, proof) + _ld_field(10000, signed_header_data)
    return CRX3_MAGIC + struct.pack("<I", CRX3_VERSION) + struct.pack("<I", len(header)) + header + payload


def build(ext: Path, out: Path, key_path: Path) -> str:
    if key_path.exists():
        k = rsa_crx.parse_private(rsa_crx.pem_decode(key_path.read_bytes()))
    else:
        k = rsa_crx.gen_keypair(2048)
        key_path.parent.mkdir(parents=True, exist_ok=True)
        # 存 PKCS#8：Chrome 自己的 --pack-extension 也能读同一把钥匙
        key_path.write_bytes(rsa_crx.pem(rsa_crx.pkcs8_private_der(k), "PRIVATE KEY"))
    spki = rsa_crx.spki_public_der(k)
    payload = zip_dir(ext)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(build_crx3(spki, k, payload))
    return rsa_crx.extension_id(spki)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ext", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--key", required=True)
    ap.add_argument("--print-id", action="store_true")
    a = ap.parse_args()
    ext_id = build(Path(a.ext), Path(a.out), Path(a.key))
    if a.print_id:
        print(ext_id)


if __name__ == "__main__":
    main()
