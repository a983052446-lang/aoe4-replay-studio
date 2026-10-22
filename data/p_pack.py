import sys, gzip
from pathlib import Path
sys.path.insert(0, ".")
from aoereplay import custom
from aoereplay.config import Config

cfg = Config()
print("=== 各对局的玩法包 ID ===")
for p in sorted(cfg.network_dir.glob("M_*"), key=lambda x: -x.stat().st_size)[:12]:
    try:
        data = gzip.decompress(p.read_bytes())
        pack = custom.extract_mode_pack(data)
        is_c = custom.fast_custom(p, cfg.network_dir)
        gid = p.name.split("_")[1]
        tag = "自定义" if is_c else "匹配"
        print(f"  {gid} [{tag}] pack_id={pack['pack_id']} kind={pack['pack_kind']}")
    except Exception as e:
        pass
