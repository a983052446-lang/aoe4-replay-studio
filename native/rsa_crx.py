# -*- coding: utf-8 -*-
"""纯 Python 的 RSA-2048 密钥生成 + PKCS#1 v1.5 / SHA-256 签名 + 手工 DER 编码。

用途：给 Chrome 扩展打 .crx（外部扩展只能装带签名的包）。
不依赖 openssl / cryptography / M2Crypto —— 只用标准库，密钥生成约 1~3 秒。
"""
from __future__ import annotations

import base64
import hashlib
import random
import secrets

SMALL_PRIMES = [p for p in range(3, 2000) if all(p % d for d in range(2, int(p**0.5) + 1))]


def _is_probable_prime(n: int, rounds: int = 40) -> bool:
    if n < 2:
        return False
    for p in SMALL_PRIMES:
        if n % p == 0:
            return n == p
    d, s = n - 1, 0
    while d % 2 == 0:
        d //= 2
        s += 1
    for _ in range(rounds):
        a = secrets.randbelow(n - 3) + 2
        x = pow(a, d, n)
        if x in (1, n - 1):
            continue
        for _ in range(s - 1):
            x = pow(x, 2, n)
            if x == n - 1:
                break
        else:
            return False
    return True


def _gen_prime(bits: int, e: int) -> int:
    while True:
        cand = secrets.randbits(bits) | (1 << (bits - 1)) | 1
        if (cand - 1) % e == 0:
            continue
        if _is_probable_prime(cand):
            return cand


def gen_keypair(bits: int = 2048, e: int = 65537) -> dict:
    half = bits // 2
    while True:
        p = _gen_prime(half, e)
        q = _gen_prime(half, e)
        if p == q:
            continue
        n = p * q
        if n.bit_length() != bits:
            continue
        phi = (p - 1) * (q - 1)
        d = pow(e, -1, phi)
        return {
            "n": n,
            "e": e,
            "d": d,
            "p": p,
            "q": q,
            "dp": d % (p - 1),
            "dq": d % (q - 1),
            "qinv": pow(q, -1, p),
        }


# ---------------- DER ----------------
def _len(n: int) -> bytes:
    if n < 0x80:
        return bytes([n])
    raw = n.to_bytes((n.bit_length() + 7) // 8, "big")
    return bytes([0x80 | len(raw)]) + raw


def _int(i: int) -> bytes:
    raw = i.to_bytes(max(1, (i.bit_length() + 7) // 8), "big")
    if raw[0] & 0x80:
        raw = b"\x00" + raw
    return b"\x02" + _len(len(raw)) + raw


def _seq(*items: bytes) -> bytes:
    body = b"".join(items)
    return b"\x30" + _len(len(body)) + body


def pkcs1_private_der(k: dict) -> bytes:
    return _seq(
        b"\x02\x01\x00",
        _int(k["n"]), _int(k["e"]), _int(k["d"]), _int(k["p"]), _int(k["q"]), _int(k["dp"]), _int(k["dq"]), _int(k["qinv"]),
    )


def spki_public_der(k: dict) -> bytes:
    rsa_pub = _seq(_int(k["n"]), _int(k["e"]))
    algid = bytes.fromhex("300d06092a864886f70d0101010500")
    bitstr = b"\x03" + _len(len(rsa_pub) + 1) + b"\x00" + rsa_pub
    body = algid + bitstr
    return b"\x30" + _len(len(body)) + body


def pem(der: bytes, label: str = "RSA PRIVATE KEY") -> bytes:
    b64 = base64.b64encode(der).decode()
    lines = [b64[i : i + 64] for i in range(0, len(b64), 64)]
    return f"-----BEGIN {label}-----\n".encode() + "\n".join(lines).encode() + f"\n-----END {label}-----\n".encode()


def pkcs8_private_der(k: dict) -> bytes:
    """PKCS#8 包裹。Chrome 自己的 --pack-extension 只认 PKCS#8，存这个互通性更好。"""
    pkcs1 = pkcs1_private_der(k)
    algid = bytes.fromhex("300d06092a864886f70d0101010500")      # rsaEncryption
    return _seq(b"\x02\x01\x00", algid, b"\x04" + _len(len(pkcs1)) + pkcs1)


def _der_parts(buf: bytes, pos: int = 0):
    """读一个 TLV，返回 (tag, 内容, 下一位)。"""
    tag = buf[pos]
    ln = buf[pos + 1]
    p = pos + 2
    if ln & 0x80:
        nb = ln & 0x7F
        ln = int.from_bytes(buf[p : p + nb], "big")
        p += nb
    return tag, buf[p : p + ln], p + ln


def parse_private(der: bytes) -> dict:
    """私钥 DER → 整数。PKCS#1 和 PKCS#8 都认。

    别人用 openssl / Chrome 生成的密钥是 PKCS#8（外面套一层 AlgorithmIdentifier），
    直接按 PKCS#1 解析会拿到错位的一堆整数，签名结果就成了乱码。
    """
    try:
        return parse_pkcs1_private(der)
    except Exception:  # noqa: BLE001
        pass
    _t, seq, _ = _der_parts(der, 0)                  # PrivateKeyInfo 的 SEQUENCE
    # 版本是 INTEGER、后面跟 AlgorithmIdentifier，都跳过；按顺序找到 OCTET STRING 就行
    pos = 0
    while pos < len(seq):
        tag, body, nxt = _der_parts(seq, pos)
        if tag == 0x04:                              # OCTET STRING → 里面是 PKCS#1
            return parse_pkcs1_private(body)
        pos = nxt
    raise ValueError("看不懂的私钥格式（既不是 PKCS#1 也不是 PKCS#8）")


def pem_decode(text: bytes) -> bytes:
    import re

    return base64.b64decode(re.sub(rb"-----[^-]+-----|\s", b"", text))


def parse_pkcs1_private(der: bytes) -> dict:
    """把 PKCS#1 私钥 DER 解回整数（读自己写的 .pem）。"""
    idx = 2 if der[1] < 0x80 else 2 + (der[1] & 0x7F)
    out = []
    while idx < len(der):
        if der[idx] != 0x02:
            break
        ln = der[idx + 1]
        if ln & 0x80:
            nb = ln & 0x7F
            ln = int.from_bytes(der[idx + 2 : idx + 2 + nb], "big")
            start = idx + 2 + nb
        else:
            start = idx + 2
        out.append(int.from_bytes(der[start : start + ln], "big"))
        idx = start + ln
    _ver, n, e, d, p, q, dp, dq, qinv = out[:9]
    return {"n": n, "e": e, "d": d, "p": p, "q": q, "dp": dp, "dq": dq, "qinv": qinv}


# ---------------- 签名 ----------------
SHA256_DIGEST_INFO = bytes.fromhex("3031300d060960864801650304020105000420")


def sign_sha256(data: bytes, k: dict) -> bytes:
    size = (k["n"].bit_length() + 7) // 8
    t = SHA256_DIGEST_INFO + hashlib.sha256(data).digest()
    if size < len(t) + 11:
        raise ValueError("key too small")
    em = b"\x00\x01" + b"\xff" * (size - len(t) - 3) + b"\x00" + t
    m = int.from_bytes(em, "big")
    s = pow(m, k["d"], k["n"])
    return s.to_bytes(size, "big")


def extension_id(spki: bytes) -> str:
    digest = hashlib.sha256(spki).hexdigest()[:32]
    return "".join(chr(ord("a") + int(c, 16)) for c in digest)
