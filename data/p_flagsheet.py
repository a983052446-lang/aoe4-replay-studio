# -*- coding: utf-8 -*-
"""把本地 24 张旗帜拼成一张对照图，一次看清每张是不是「有内容」。"""
import os
import sys

from PIL import Image, ImageDraw

sys.path.insert(0, os.path.abspath("."))
FLAG = os.path.abspath("data/civflags")
names = sorted(f[:-4] for f in os.listdir(FLAG) if f.endswith(".png"))
cell_w, cell_h = 104, 56
pad = 16
cols = 4
rows = (len(names) + cols - 1) // cols
W = cols * (cell_w + pad) + pad
H = rows * (cell_h + pad + 14) + pad
sheet = Image.new("RGB", (W, H), (12, 18, 26))
dr = ImageDraw.Draw(sheet)

for i, n in enumerate(names):
    x = pad + (i % cols) * (cell_w + pad)
    y = pad + (i // cols) * (cell_h + pad + 14)
    im = Image.open(os.path.join(FLAG, n + ".png")).convert("RGB").resize((cell_w, cell_h))
    sheet.paste(im, (x, y))
    # 统计非黑像素占比，判断这张图是不是「空」
    px = list(im.getdata())
    bright = sum(1 for r, g, b in px if (r + g + b) > 90) / len(px)
    dr.text((x, y + cell_h + 2), f"{n[:20]} {bright:.0%}", fill=(200, 215, 230))

out = os.path.abspath("data/flagsheet.png")
sheet.save(out)
print("saved", out, W, "x", H)
for n in names:
    im = Image.open(os.path.join(FLAG, n + ".png")).convert("RGB")
    px = list(im.getdata())
    bright = sum(1 for r, g, b in px if (r + g + b) > 90) / len(px)
    print("%-24s %5.1f%%  size=%s" % (n, bright * 100, im.size))
