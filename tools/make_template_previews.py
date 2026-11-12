# -*- coding: utf-8 -*-
"""Рисует превью шаблонов альбомов (обложка и разворот с примерами фото): python tools/make_template_previews.py"""
import os, sys
ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, ROOT)
import ai_core as A
import album_templates as T
from PIL import Image

OUT = os.path.join(ROOT, "web", "img", "tpl")
os.makedirs(OUT, exist_ok=True)
det = A.FaceDetector()
photos = {}
for i in range(1, 9):
    with open(os.path.join(ROOT, "seed", "scenes", f"scene{i}.jpg"), "rb") as f:
        ph = A.load_photo(f"scene{i}.jpg", f.read(), det)
        photos[ph.id] = ph
pool = list(photos.values())
for t in T.TEMPLATES:
    bk = T.build(t["id"])
    k = 0
    for sp in bk["spreads"]:                                   # примеры фото в слотах
        for j in range(len(sp["slots"])):
            sp["slots"][j] = A.make_slot(pool[k % len(pool)], sp["template"], j); k += 1
    cov = A.render_cover(bk, 900)
    cov.convert("RGB").save(os.path.join(OUT, t["id"] + "-a.jpg"), quality=86, optimize=True)
    pick = max(bk["spreads"][1:5], key=lambda x: len(x["slots"]))
    A.render_spread(pick, photos, width=900, badges=False, plain=True).convert("RGB").save(os.path.join(OUT, t["id"] + "-b.jpg"), quality=86, optimize=True)
    A.render_spread(bk["spreads"][0], photos, width=900, badges=False, plain=True).convert("RGB").save(os.path.join(OUT, t["id"] + "-c.jpg"), quality=86, optimize=True)
    print("готово:", t["id"])
