import sys, gzip, re
from pathlib import Path
sys.path.insert(0, ".")
from aoereplay.config import Config

cfg = Config()
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _diag import pick
p = pick()
data = __import__('gzip').decompress(p.read_bytes()) if p.read_bytes()[:2] == b'\x1f\x8b' else p.read_bytes()

# DATASDSC 块：找出它的 size，然后把整个块的所有字符串都列出来
i = data.find(b"DATASDSC")
import struct
ver, size, flags = struct.unpack("<III", data[i+8:i+20])
body = data[i+20:i+20+size]
print(f"DATASDSC 块 size={size}")
strs = re.findall(rb"[\x20-\x7e]{3,60}", body)
print("块内字符串:")
for s in strs:
    print("   ", s.decode("ascii","ignore"))

# 再看看块后面紧跟着的几百字节
print("\n块后面 300 字节:", data[i+20+size:i+20+size+300])
