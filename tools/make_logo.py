# -*- coding: utf-8 -*-
"""Из маленького логотипа (белая надпись на коралловом фоне) делает чёткие версии: SVG-вектор, PNG с прозрачностью, иконки.
python tools/make_logo.py <исходник.jpg>"""
import os, sys
import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFilter

SRC = sys.argv[1]
ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "web")
os.makedirs(os.path.join(ROOT, "img"), exist_ok=True)
im = np.asarray(Image.open(SRC).convert("RGB")).astype(np.float32)
bg = np.array([204, 95, 92], np.float32)
wh = np.array([252, 236, 236], np.float32)
a = np.clip(((im - bg) @ (wh - bg)) / ((wh - bg) @ (wh - bg)), 0, 1)       # прозрачность надписи 0..1
a[:6, :] = 0; a[-6:, :] = 0; a[:, :6] = 0; a[:, -6:] = 0                    # края (угол с тёмным пятном)
a[a < 0.12] = 0
ys, xs = np.where(a > 0.3)
x0, x1, y0, y1 = xs.min() - 4, xs.max() + 5, ys.min() - 4, ys.max() + 5
crop = a[y0:y1, x0:x1]
K = 12
big = cv2.resize(crop, None, fx=K, fy=K, interpolation=cv2.INTER_CUBIC)
big = cv2.GaussianBlur(big, (0, 0), K * 0.30)
mask = np.clip((big - 0.47) / 0.08, 0, 1)                                     # резкий гладкий край
mask = cv2.GaussianBlur(mask, (0, 0), 1.2)
H, W = mask.shape
print("маска", W, H)

# PNG с прозрачностью (белая надпись)
rgba = np.zeros((H, W, 4), np.uint8); rgba[..., :3] = 255; rgba[..., 3] = (mask * 255).astype(np.uint8)
Image.fromarray(rgba, "RGBA").save(os.path.join(ROOT, "img", "logo-white.png"), optimize=True)

# SVG: контуры маски
m8 = (mask > 0.5).astype(np.uint8) * 255
cnts, hier = cv2.findContours(m8, cv2.RETR_CCOMP, cv2.CHAIN_APPROX_NONE)
d = []
def smooth(pts):
    n = len(pts)
    if n < 4: return "M" + " L".join(f"{x:.2f} {y:.2f}" for x, y in pts) + "Z"
    out = [f"M{pts[0][0]:.2f} {pts[0][1]:.2f}"]
    for i in range(n):                                   # Catmull-Rom -> кубические кривые Безье
        p0, p1, p2, p3 = pts[(i - 1) % n], pts[i], pts[(i + 1) % n], pts[(i + 2) % n]
        c1 = (p1[0] + (p2[0] - p0[0]) / 6, p1[1] + (p2[1] - p0[1]) / 6)
        c2 = (p2[0] - (p3[0] - p1[0]) / 6, p2[1] - (p3[1] - p1[1]) / 6)
        out.append(f"C{c1[0]:.2f} {c1[1]:.2f} {c2[0]:.2f} {c2[1]:.2f} {p2[0]:.2f} {p2[1]:.2f}")
    return "".join(out) + "Z"
for c in cnts:
    if cv2.contourArea(c) < 40 * K: continue
    c = cv2.approxPolyDP(c, 2.2, True)
    d.append(smooth([(x / K, y / K) for x, y in c[:, 0, :]]))
vw, vh = W / K, H / K
svg = f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {vw:.1f} {vh:.1f}"><path fill="#fff" fill-rule="evenodd" d="{" ".join(d)}"/></svg>'
open(os.path.join(ROOT, "img", "logo-white.svg"), "w").write(svg)
svg2 = svg.replace('#fff', '#e2575a')
open(os.path.join(ROOT, "img", "logo-coral.svg"), "w").write(svg2)

def tile(size, radius=0.22, pad=0.2, maskable=False):
    S = size * 2
    bgim = Image.new("RGB", (S, S))
    px = bgim.load()
    top, bot = np.array([240, 106, 96]), np.array([206, 58, 70])
    g = np.linspace(0, 1, S)[:, None, None]
    arr = (top * (1 - g) + bot * g).repeat(S, 1).astype(np.uint8)
    bgim = Image.fromarray(arr, "RGB")
    wmax = S * (0.56 if maskable else 1 - 2 * pad)
    sc = wmax / W
    lw, lh = int(W * sc), int(H * sc)
    logo = Image.fromarray(rgba, "RGBA").resize((lw, lh), Image.LANCZOS)
    bgim.paste(logo, ((S - lw) // 2, (S - lh) // 2), logo)
    if not maskable:
        m = Image.new("L", (S, S), 0); ImageDraw.Draw(m).rounded_rectangle([0, 0, S - 1, S - 1], int(S * radius), fill=255)
        out = Image.new("RGBA", (S, S), (0, 0, 0, 0)); out.paste(bgim, (0, 0), m)
    else:
        out = bgim.convert("RGBA")
    return out.resize((size, size), Image.LANCZOS)

tile(192).save(os.path.join(ROOT, "icons", "icon-192.png"), optimize=True)
tile(512).save(os.path.join(ROOT, "icons", "icon-512.png"), optimize=True)
tile(512, maskable=True).save(os.path.join(ROOT, "icons", "maskable-512.png"), optimize=True)
tile(180, radius=0).convert("RGB").save(os.path.join(ROOT, "icons", "apple-touch-180.png"), optimize=True)
tile(1024, radius=0).convert("RGB").save(os.path.join(ROOT, "icons", "icon-1024.png"), optimize=True)
print("ok")
