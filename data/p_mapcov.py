# -*- coding: utf-8 -*-
import json
import re
from pathlib import Path

maps = json.loads(Path("data/maps.json").read_text(encoding="utf-8"))
locs = json.loads(Path("data/mapthumbs_local.json").read_text(encoding="utf-8"))
thumb_dir = Path("data/mapthumbs")


def slug(n):
    return re.sub(r"[^a-z0-9]+", "_", (n or "").lower()).strip("_")


names = [
    "Land Megarandom", "Lipany", "Hidden Valley", "Dry Arabia", "Hill and Dale",
    "MegaRandom", "Four Lakes", "Golden Pit", "Golden Heights", "Mongolian Heights",
    "Nagari", "Prairie", "Canal", "Fortitude", "Ancient Spires", "Fissure", "Glade",
    "Archipelago", "Team Islands", "Boulder Bay", "Kawasan", "Haywire", "Oasis",
    "Ravine", "Sinkholes", "Zealot Fort", "Gorge", "Fields of Ruin", "Crown Bay",
    "Wetlands", "Northern Fields", "River Crossing", "Golden Valley", "Marshland",
    "Scandinavia", "Dunes", "Twin Villages", "Kings and Castles", "Black Forest",
]
for name in names:
    s = slug(name)
    print("%-22s slug=%-24s local=%-32s remote=%-5s file=%s" % (
        name, s, locs.get(s), bool(maps.get(s)), (thumb_dir / (s + ".png")).exists()))
print("---")
print("local keys:", sorted(locs))
print("remote keys:", sorted(maps))
