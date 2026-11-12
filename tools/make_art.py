# -*- coding: utf-8 -*-
"""Рисует иллюстрации для главной и входа: python tools/make_art.py  (нужен rsvg-convert и Pillow)."""
import os
import subprocess
import sys
import random
from art_lib import *

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "web", "img")
os.makedirs(OUT, exist_ok=True)


def render(svg, name, w, h, q=86):
    p = os.path.join(OUT, name + ".svg")
    open(p, "w", encoding="utf-8").write(svg)
    png = os.path.join(OUT, name + ".png")
    subprocess.run(["rsvg-convert", "-w", str(w), "-h", str(h), p, "-o", png], check=True)
    from PIL import Image
    Image.open(png).convert("RGB").save(os.path.join(OUT, name + ".jpg"), quality=q, optimize=True, progressive=True)
    os.remove(png); os.remove(p)


def bokeh(seed, n, w, h, rmin=14, rmax=60, color="#fff", op=(.12, .3)):
    r = random.Random(seed)
    return "".join(f'<circle cx="{r.uniform(0, w):.0f}" cy="{r.uniform(0, h):.0f}" r="{r.uniform(rmin, rmax):.0f}" fill="{color}" opacity="{r.uniform(*op):.2f}" filter="url(#bl6)"/>' for _ in range(n))


def hero():
    W, H = 1170, 820
    s = [f'<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" width="{W}" height="{H}" viewBox="0 0 {W} {H}"><defs>{common_defs()}',
         '<linearGradient id="bg" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#fbe9dc"/><stop offset=".55" stop-color="#f6d9c9"/><stop offset="1" stop-color="#efc7b7"/></linearGradient>',
         '<linearGradient id="fade" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#fbf6f1" stop-opacity="0"/><stop offset="1" stop-color="#fbf6f1"/></linearGradient>',
         '<linearGradient id="top" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#2b1a18" stop-opacity=".35"/><stop offset="1" stop-color="#2b1a18" stop-opacity="0"/></linearGradient></defs>',
         f'<rect width="{W}" height="{H}" fill="url(#bg)"/>',
         f'<g opacity=".5"><path d="M-60 120 700 -80 900 -80 40 260Z" fill="#fff" opacity=".35" filter="url(#bl14)"/><path d="M300 880 1230 360 1230 520 480 880Z" fill="#fff" opacity=".22" filter="url(#bl14)"/></g>',
         bokeh(3, 9, W, 420)]
    # ветки эвкалипта (дальние размыты)
    s.append(leaf_branch(-20, 700, 360, -35, "#7d9a86", n=9, size=52, seed=4, blur=3))
    s.append(leaf_branch(1190, 90, 330, 150, "#8aa795", n=8, size=48, seed=7, blur=3))
    # закрытый альбом (слева сзади)
    s.append(hardcover(70, 170, 330, 440, GREEN, -9))
    # стопка отпечатков справа
    for k, (sc, rot, dx) in enumerate([(2, 9, 0), (6, 3, 14), (3, -4, 6)]):
        x, y = 850 + dx, 330 + k * 8
        s.append(f'<g transform="rotate({rot} {x + 110} {y + 90})" filter="url(#sh)"><rect x="{x}" y="{y}" width="230" height="170" rx="3" fill="#fffdf9"/>{photo(sc, x + 10, y + 10, 210, 150, 2)}</g>')
    # открытая фотокнига
    pages = ([(1, .08, .08, .84, .46), (4, .08, .58, .38, .3), (5, .54, .58, .38, .3), ("line", .08, .92, .5)],
             [(3, .08, .08, .38, .3), (7, .54, .08, .38, .3), (8, .08, .44, .38, .3), (2, .54, .44, .38, .3), ("line", .08, .86, .6), ("line", .08, .91, .35)])
    s.append(open_book(330, 150, 285, 400, -4, pages))
    # закладка-лента
    s.append(f'<path d="M612 536 v112 l14-13 14 13 v-112Z" fill="{CORAL}" transform="rotate(-4 615 400)" filter="url(#sh)"/>')
    # полароиды
    s.append(polaroid(1, 760, 30, 210, 8))
    s.append(polaroid(5, 80, 560, 190, 6))
    s.append(polaroid(7, 870, 560, 180, -8))
    s.append(leaf_branch(640, 800, 300, -120, "#6f8f7c", n=7, size=44, seed=9))
    s.append(bokeh(11, 7, W, H, 20, 70, "#fff", (.08, .2)))
    s.append(f'<rect width="{W}" height="170" fill="url(#top)"/>')
    s.append(f'<rect y="{H - 210}" width="{W}" height="210" fill="url(#fade)"/></svg>')
    render("".join(s), "hero", W, H, 85)


def login():
    W, H = 1170, 1500
    s = [f'<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" width="{W}" height="{H}" viewBox="0 0 {W} {H}"><defs>{common_defs()}',
         '<linearGradient id="bg" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#f3dccb"/><stop offset=".5" stop-color="#f1cdbb"/><stop offset="1" stop-color="#e8b8a6"/></linearGradient>',
         '<linearGradient id="fade" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#fff" stop-opacity="0"/><stop offset="1" stop-color="#fff"/></linearGradient></defs>',
         f'<rect width="{W}" height="{H}" fill="url(#bg)"/>',
         f'<path d="M0 0H760L300 1000H0Z" fill="#fff" opacity=".18" filter="url(#bl14)"/>', bokeh(21, 10, W, 800, 18, 70)]
    s.append(leaf_branch(1230, 260, 420, 165, "#8aa795", n=9, size=56, seed=12, blur=3))
    s.append(leaf_branch(-40, 900, 380, -30, "#7d9a86", n=8, size=50, seed=14, blur=3))
    # большая открытая фотокнига по диагонали
    pages = ([(1, .07, .07, .86, .5), (6, .07, .62, .4, .3), (4, .53, .62, .4, .3)],
             [(3, .07, .07, .4, .32), (5, .53, .07, .4, .32), (2, .07, .45, .86, .3), ("line", .07, .82, .6), ("line", .07, .87, .4)])
    s.append(open_book(250, 330, 330, 470, 7, pages))
    s.append(hardcover(60, 760, 300, 400, GREEN, -12))
    s.append(hardcover(780, 840, 280, 380, "#b9454a", 9, label="camera"))
    s.append(polaroid(7, 800, 160, 230, -9))
    s.append(polaroid(8, 60, 190, 210, -7))
    s.append(polaroid(3, 600, 960, 220, 5))
    s.append(leaf_branch(560, 1200, 300, -100, "#6f8f7c", n=7, size=44, seed=17))
    s.append(f'<rect y="{H - 520}" width="{W}" height="520" fill="url(#fade)"/></svg>')
    render("".join(s), "login", W, H, 84)


if __name__ == "__main__":
    which = sys.argv[1:] or ["hero", "login"]
    for w in which:
        globals()[w]()
        print("готово:", w)
