import sys, struct
from pathlib import Path

base = Path.home() / "Documents/My Games/Age of Empires IV/mods/extension/subscriptions"
f = list((base / "72188").glob("*.sga"))[0]
data = f.read_bytes()
print("文件:", f.name[:40], len(data))
print("前 128 字节:", data[:128])
print("hex:", data[:128].hex())
# SGA 魔数通常是 _ARCHIVE 或版本号
print("\n前 8 字节 ascii:", data[:8])
