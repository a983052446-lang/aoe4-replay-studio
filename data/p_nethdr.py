# -*- coding: utf-8 -*-
"""扫 network\\ 所有文件头，找非 gzip 的容器格式（datatype=1/2 -> zip 之类）。"""
import collections
import sys
from pathlib import Path

sys.path.insert(0, ".")
from aoereplay.config import Config  # noqa: E402

cfg = Config()
c = collections.Counter()
samples = {}
for p in cfg.network_dir.iterdir():
    if not p.is_file():
        continue
    try:
        with open(p, "rb") as fh:
            h = fh.read(8)
    except OSError:
        continue
    key = h[:4].hex()
    c[key] += 1
    samples.setdefault(key, p.name)
for k, n in c.most_common(15):
    print(k, n, samples[k][:60])
