import sys, re, zlib
from pathlib import Path

base = Path.home() / "Documents/My Games/Age of Empires IV/mods/extension/subscriptions"
for mid in ("166901","167634","207587","207675","314963","72188"):
    d = base / mid
    if not d.exists(): continue
    f = list(d.glob("*.sga"))[0]
    data = f.read_bytes()
    # SGA 里有 UTF-16 字符串，找可读的
    strs = []
    for m in re.finditer(rb"(?:[\x00-\xff][\x00-\xff]){4,80}", data[:200000]):
        try:
            s = m.group(0).decode("utf-16-le", "ignore").strip()
        except Exception:
            continue
        if s and s.isprintable() and any('\u4e00' <= c <= '\u9fff' for c in s):
            strs.append(s)
        elif s and s.isprintable() and re.fullmatch(r"[A-Za-z0-9 _\-\.']{4,60}", s):
            strs.append(s)
    uniq = []
    for s in strs:
        if s not in uniq: uniq.append(s)
    print(f"=== {mid} ({f.stat().st_size//1024}KB) 前 8 个串:")
    for s in uniq[:8]:
        print("   ", s[:60])
    print()
