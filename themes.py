# -*- coding: utf-8 -*-
"""
Темы оформления разворотов: фон, палитра, шрифты, рамки фото и декор.
Всё рисуется кодом (PIL), без внешних картинок. Модуль не зависит от ai_core.
"""
from __future__ import annotations

import math
import random

from PIL import Image, ImageDraw

# title_font: "serif" | "serif_italic" | "sans_bold"
THEMES = {
    "classic": dict(
        name="Классика", emoji="📖", keywords="классика обычный стандартный по умолчанию",
        bg=(255, 255, 255), cover_bg=(246, 245, 232), accent=(169, 189, 224), sub=(169, 189, 224),
        frame=None, frame_w=0.0, title_font="serif", decor=None,
        palette=[(169, 189, 224)], default_title="МОЯ КНИГА"),
    "birthday": dict(
        name="День рождения", emoji="🎂", keywords="день рождения др юбилей именины birthday party праздник",
        bg=(255, 248, 235), cover_bg=(255, 240, 214), accent=(233, 76, 128), sub=(171, 71, 188),
        frame=(255, 255, 255), frame_w=0.007, title_font="sans_bold", decor="birthday",
        palette=[(240, 98, 146), (255, 193, 7), (66, 165, 245), (102, 187, 106), (171, 71, 188), (255, 112, 67)],
        default_title="С ДНЁМ РОЖДЕНИЯ!"),
    "wedding": dict(
        name="Свадьба", emoji="💍", keywords="свадьба свадебный wedding венчание помолвка",
        bg=(253, 250, 244), cover_bg=(250, 244, 232), accent=(176, 141, 79), sub=(150, 122, 78),
        frame=(176, 141, 79), frame_w=0.0022, title_font="serif_italic", decor="wedding",
        palette=[(176, 141, 79), (196, 170, 120), (122, 150, 112)], default_title="НАША СВАДЬБА"),
    "travel": dict(
        name="Путешествие", emoji="✈️", keywords="путешествие поездка отпуск travel море каникулы тур",
        bg=(245, 238, 224), cover_bg=(236, 226, 205), accent=(24, 111, 125), sub=(196, 98, 62),
        frame=(255, 255, 255), frame_w=0.009, title_font="sans_bold", decor="travel",
        palette=[(24, 111, 125), (196, 98, 62), (214, 170, 80)], default_title="ПУТЕШЕСТВИЕ"),
    "newyear": dict(
        name="Новый год", emoji="🎄", keywords="новый год новогодний рождество зима праздники christmas",
        bg=(19, 32, 68), cover_bg=(14, 24, 54), accent=(240, 200, 100), sub=(210, 225, 250),
        frame=(240, 200, 100), frame_w=0.0035, title_font="serif_italic", decor="newyear",
        palette=[(255, 255, 255), (240, 200, 100), (170, 200, 245)], default_title="С НОВЫМ ГОДОМ!"),
    "halloween": dict(
        name="Хэллоуин", emoji="🎃", keywords="хэллоуин хеллоуин halloween тыква ужасы страшн",
        bg=(30, 18, 44), cover_bg=(22, 12, 34), accent=(255, 140, 30), sub=(200, 170, 240),
        frame=(255, 140, 30), frame_w=0.004, title_font="serif_italic", decor="halloween",
        palette=[(255, 140, 30), (170, 110, 230), (255, 255, 255)], default_title="HAPPY HALLOWEEN"),
    "minimal": dict(
        name="Минимализм", emoji="◻️", keywords="минимализм минимал строгий простой чистый лаконичный",
        bg=(250, 250, 249), cover_bg=(238, 238, 236), accent=(60, 60, 60), sub=(120, 120, 120),
        frame=None, frame_w=0.0, title_font="sans_bold", decor=None,
        palette=[(60, 60, 60)], default_title="ФОТОКНИГА"),
    # ---- дополнительные стили оформления ----
    "noir": dict(
        name="Полночь", emoji="🌙", keywords="полночь чёрный чёрное золото элегантный вечер роскошный дорого noir",
        bg=(24, 24, 29), cover_bg=(16, 16, 20), accent=(222, 194, 140), sub=(190, 180, 160),
        frame=(222, 194, 140), frame_w=0.0028, title_font="serif_italic", decor="goldinset",
        palette=[(222, 194, 140), (245, 232, 200), (160, 140, 100)], default_title="НАША ИСТОРИЯ"),
    "emerald": dict(
        name="Изумруд", emoji="💎", keywords="изумруд зелёный зелень золото премиум благородный дорогой emerald",
        bg=(22, 56, 47), cover_bg=(14, 42, 35), accent=(224, 186, 104), sub=(196, 214, 200),
        frame=(224, 186, 104), frame_w=0.0030, title_font="serif_italic", decor="goldinset",
        palette=[(224, 186, 104), (250, 240, 210), (120, 160, 138)], default_title="ИЗУМРУДНЫЙ ГОД"),
    "blush": dict(
        cover_panel=True,
        name="Нежность", emoji="🌸", keywords="нежность нежный розовый пудровый пудра романтика девушка blush",
        bg=(254, 245, 242), cover_bg=(250, 232, 228), accent=(190, 112, 118), sub=(150, 110, 110),
        frame=(255, 255, 255), frame_w=0.008, title_font="serif_italic", decor="botanic",
        palette=[(190, 112, 118), (222, 164, 160), (132, 160, 138)], default_title="С ЛЮБОВЬЮ"),
    "nordic": dict(
        cover_panel=True,
        name="Скандинавия", emoji="🌿", keywords="скандинавия скандинавский светлый чистый природа шалфей nordic уют",
        bg=(245, 247, 245), cover_bg=(232, 238, 233), accent=(86, 116, 102), sub=(130, 146, 138),
        frame=(255, 255, 255), frame_w=0.010, title_font="sans_bold", decor="dots",
        palette=[(86, 116, 102), (170, 190, 178), (214, 224, 216)], default_title="ДОМ И УЮТ"),
    "vintage": dict(
        cover_panel=True,
        name="Винтаж", emoji="📜", keywords="винтаж ретро старинный сепия архив ностальгия vintage",
        bg=(240, 228, 206), cover_bg=(226, 208, 176), accent=(120, 80, 48), sub=(150, 112, 78),
        frame=(250, 244, 230), frame_w=0.011, title_font="serif_italic", decor="paper",
        palette=[(120, 80, 48), (170, 126, 82), (96, 110, 80)], default_title="СЕМЕЙНЫЙ АРХИВ"),
    "summer": dict(
        cover_panel=True,
        name="Лето", emoji="☀️", keywords="лето летний солнце море пляж отпуск яркий summer",
        bg=(255, 251, 238), cover_bg=(255, 241, 205), accent=(22, 142, 156), sub=(236, 150, 60),
        frame=(255, 255, 255), frame_w=0.009, title_font="sans_bold", decor="waves",
        palette=[(22, 142, 156), (244, 184, 64), (236, 120, 84)], default_title="ЛЕТО"),
    "sunset": dict(
        cover_panel=True,
        name="Закат", emoji="🌇", keywords="закат градиент тёплый персиковый вечерний sunset",
        bg=(255, 236, 222), cover_bg=(255, 214, 190), accent=(176, 64, 96), sub=(130, 80, 110),
        frame=(255, 255, 255), frame_w=0.008, title_font="serif_italic", decor="gradient",
        bg2=(236, 188, 200), cover_bg2=(214, 142, 170),
        palette=[(176, 64, 96), (240, 150, 110), (130, 80, 110)], default_title="ТЁПЛЫЙ ВЕЧЕР"),
    "ocean": dict(
        cover_panel=True,
        name="Океан", emoji="🌊", keywords="океан море синий морской волны глубина ocean яхта",
        bg=(232, 244, 250), cover_bg=(30, 74, 110), accent=(255, 255, 255), sub=(190, 224, 236),
        frame=(255, 255, 255), frame_w=0.008, title_font="sans_bold", decor="gradient",
        bg2=(190, 224, 238), cover_bg2=(14, 44, 78),
        palette=[(30, 90, 130), (120, 190, 214), (240, 224, 190)], default_title="У МОРЯ"),
    "linen": dict(
        cover_panel=True,
        name="Лён", emoji="🧵", keywords="лён льняной бежевый натуральный эко спокойный linen",
        bg=(246, 240, 230), cover_bg=(232, 222, 204), accent=(128, 98, 70), sub=(160, 134, 106),
        frame=(255, 252, 246), frame_w=0.008, title_font="serif", decor="stripes",
        palette=[(128, 98, 70), (176, 148, 114), (110, 124, 100)], default_title="МОИ ДНИ"),
    "kids": dict(
        cover_panel=True,
        name="Малыш", emoji="🧸", keywords="малыш ребёнок детский дети младенец первый год kids baby пастель",
        bg=(255, 250, 240), cover_bg=(255, 238, 214), accent=(86, 156, 196), sub=(240, 132, 132),
        frame=(255, 255, 255), frame_w=0.010, title_font="sans_bold", decor="dots",
        palette=[(86, 156, 196), (244, 170, 150), (248, 210, 110), (150, 202, 160)], default_title="НАШ МАЛЫШ"),
}


def get_theme(tid) -> dict:
    return THEMES.get(tid) or THEMES["classic"]


# ---------------------------------------------------------------------------
# ПРИМИТИВЫ
# ---------------------------------------------------------------------------
def _lighten(c, k=0.55):
    return tuple(min(255, int(v + (255 - v) * k)) for v in c)


def _confetti(d, W, H, rng, pal, n):
    for _ in range(n):
        x, y = rng.random() * W, rng.random() * H
        s = rng.uniform(0.006, 0.014) * W
        c = rng.choice(pal)
        k = rng.random()
        if k < 0.4:
            d.ellipse([x, y, x + s, y + s], fill=c)
        elif k < 0.75:
            a = rng.random() * math.pi
            dx, dy = math.cos(a) * s * 1.2, math.sin(a) * s * 1.2
            ox, oy = -dy * 0.35, dx * 0.35
            d.polygon([(x - dx + ox, y - dy + oy), (x + dx + ox, y + dy + oy),
                       (x + dx - ox, y + dy - oy), (x - dx - ox, y - dy - oy)], fill=c)
        else:
            d.polygon([(x, y), (x + s, y + s * 0.3), (x + s * 0.3, y + s)], fill=c)


def _balloon(d, cx, cy, r, color, string=3.2):
    rx, ry = r, r * 1.22
    d.ellipse([cx - rx, cy - ry, cx + rx, cy + ry], fill=color)
    d.ellipse([cx - rx * 0.55, cy - ry * 0.62, cx - rx * 0.15, cy - ry * 0.22], fill=_lighten(color))
    by = cy + ry
    d.polygon([(cx, by - 1), (cx - r * 0.18, by + r * 0.2), (cx + r * 0.18, by + r * 0.2)], fill=color)
    pts = [(cx + math.sin(i / 2.2) * r * 0.25, by + r * 0.2 + i * r * string / 12) for i in range(13)]
    d.line(pts, fill=(130, 130, 130), width=max(1, int(r * 0.05)))


def _snow(d, cx, cy, r, color, w):
    for k in range(6):
        a = k * math.pi / 3
        d.line([(cx, cy), (cx + math.cos(a) * r, cy + math.sin(a) * r)], fill=color, width=w)
        mx, my = cx + math.cos(a) * r * 0.6, cy + math.sin(a) * r * 0.6
        for s in (-1, 1):
            b = a + s * 0.6
            d.line([(mx, my), (mx + math.cos(b) * r * 0.3, my + math.sin(b) * r * 0.3)], fill=color, width=w)


def _bat_t(d, cx, cy, s, fill=(8, 4, 16)):
    pts = [(cx, cy - s * .15), (cx + s * .25, cy - s * .35), (cx + s * .5, cy - s * .3), (cx + s * 1.0, cy - s * .05),
           (cx + s * .75, cy + s * .02), (cx + s * .6, cy + s * .22), (cx + s * .38, cy + s * .05), (cx + s * .18, cy + s * .25),
           (cx, cy + s * .12)]
    d.polygon(pts + [(2 * cx - x, y) for x, y in reversed(pts)], fill=fill)


def _star(d, cx, cy, r, color):
    pts = []
    for i in range(10):
        rr = r if i % 2 == 0 else r * 0.45
        a = -math.pi / 2 + i * math.pi / 5
        pts.append((cx + math.cos(a) * rr, cy + math.sin(a) * rr))
    d.polygon(pts, fill=color)


def _leaf(d, px, py, a, l, color):
    w = l * 0.32
    ca, sa = math.cos(a), math.sin(a)
    up, dn = [], []
    for i in range(9):
        u = i / 8
        off = math.sin(math.pi * u) * w
        bx, by = px + ca * l * u, py + sa * l * u
        up.append((bx - sa * off, by + ca * off))
        dn.append((bx + sa * off, by - ca * off))
    d.polygon(up + dn[::-1], fill=color)


def _sprig(d, x, y, a, L, color, n=5):
    ex, ey = x + math.cos(a) * L, y + math.sin(a) * L
    d.line([(x, y), (ex, ey)], fill=color, width=max(1, int(L * 0.02)))
    for i in range(1, n + 1):
        u = i / (n + 1)
        px, py = x + math.cos(a) * L * u, y + math.sin(a) * L * u
        l = L * 0.28 * (1.1 - 0.5 * u)
        _leaf(d, px, py, a + 0.9, l, color)
        _leaf(d, px, py, a - 0.9, l, color)
    _leaf(d, ex, ey, a, L * 0.2, color)


# ---------------------------------------------------------------------------
# СЛОИ ДЕКОРА
# ---------------------------------------------------------------------------
def draw_back(img: Image.Image, theme: dict, seed: int = 0, tint: bool = True):
    """Фон и мелкий декор ПОД фотографиями."""
    W, H = img.size
    d = ImageDraw.Draw(img)
    rng = random.Random(seed)
    pal = theme["palette"]
    kind = theme.get("decor")
    if kind == "birthday":
        _confetti(d, W, H, rng, pal, 150)
    elif kind == "wedding":
        m = W * 0.018
        lw = max(1, int(W * 0.0016))
        for x0, x1 in ((m, W / 2 - m), (W / 2 + m, W - m)):
            d.rectangle([x0, m, x1, H - m], outline=pal[0], width=lw)
            d.rectangle([x0 + m * 0.55, m * 1.55, x1 - m * 0.55, H - m * 1.55], outline=pal[1], width=lw)
    elif kind == "travel":
        pts = []
        for i in range(0, 61):
            t = i / 60
            x = W * (0.05 + 0.9 * t)
            y = H * (0.88 - 0.76 * t) + math.sin(t * math.pi * 3) * H * 0.045
            pts.append((x, y))
        for i, (x, y) in enumerate(pts):
            if i % 2 == 0:
                r = W * 0.0035
                d.ellipse([x - r, y - r, x + r, y + r], fill=pal[1])
        cx, cy, R = W * 0.5, H * 0.5, H * 0.34
        for k, a in ((1.0, 2), (0.82, 1), (0.34, 1)):
            r = R * k
            d.ellipse([cx - r, cy - r, cx + r, cy + r], outline=_lighten(pal[0], 0.8), width=max(1, int(W * 0.0018 * a)))
    elif kind == "halloween":
        for _ in range(110):
            x, y = rng.random() * W, rng.random() * H
            r = rng.uniform(0.0012, 0.0036) * W
            d.ellipse([x - r, y - r, x + r, y + r], fill=rng.choice([(255, 255, 255), (255, 190, 90), (190, 150, 250)]))
        mr = W * 0.035
        d.ellipse([W * 0.5 - mr, H * 0.5 - mr, W * 0.5 + mr, H * 0.5 + mr], outline=(255, 190, 90), width=max(1, int(W * 0.002)))
    elif kind == "newyear":
        for _ in range(170):
            x, y = rng.random() * W, rng.random() * H
            r = rng.uniform(0.0016, 0.0042) * W
            d.ellipse([x - r, y - r, x + r, y + r], fill=rng.choice([(255, 255, 255), (170, 200, 245), (240, 200, 100)]))
    elif kind == "gradient":
        top = img.getpixel((0, 0))
        bot = theme.get("cover_bg2") if tuple(top) == tuple(theme["cover_bg"]) else theme.get("bg2")
        if bot:
            import numpy as _np
            g = _np.linspace(0, 1, H)[:, None, None]
            arr = (_np.array(top) * (1 - g) + _np.array(bot) * g).repeat(W, 1).astype(_np.uint8)
            img.paste(Image.fromarray(arr, "RGB"))
            d = ImageDraw.Draw(img)
    elif kind in ("dots", "stripes"):
        c = _lighten(pal[0], 0.86)
        if kind == "dots":
            step = W * 0.028
            y = step / 2
            row = 0
            while y < H:
                x = step / 2 + (step / 2 if row % 2 else 0)
                while x < W:
                    r = W * 0.0017
                    d.ellipse([x - r, y - r, x + r, y + r], fill=c)
                    x += step
                y += step
                row += 1
        else:
            step = W * 0.012
            x = -H
            while x < W:
                d.line([(x, H), (x + H, 0)], fill=_lighten(pal[0], 0.9), width=max(1, int(W * 0.0012)))
                x += step
    elif kind == "waves":
        lw = max(2, int(W * 0.0028))
        for k in range(4):
            y0 = H * (0.80 + k * 0.045)
            pts = [(x, y0 + math.sin(x / W * math.pi * 6 + k) * H * 0.012) for x in range(0, int(W) + 1, 8)]
            d.line(pts, fill=_lighten(pal[0], 0.55 + k * 0.1), width=lw)
    elif kind == "paper":
        ov = Image.new("RGBA", img.size, (0, 0, 0, 0))
        od = ImageDraw.Draw(ov)
        for k in range(36):
            a = int(46 * (1 - k / 36) ** 2)
            od.rectangle([k * W * 0.004, k * H * 0.004, W - k * W * 0.004, H - k * H * 0.004], outline=(110, 80, 40, a), width=max(2, int(W * 0.004)))
        for _ in range(900):
            x, y = rng.random() * W, rng.random() * H
            od.point((x, y), fill=(120, 90, 50, rng.randint(14, 34)))
        img.paste(Image.alpha_composite(img.convert("RGBA"), ov).convert("RGB"))
        d = ImageDraw.Draw(img)
        m = W * 0.02
        d.rectangle([m, m, W - m, H - m], outline=pal[1], width=max(1, int(W * 0.0014)))
    elif kind == "goldinset":
        for _ in range(140):
            x, y = rng.random() * W, rng.random() * H
            r = rng.uniform(0.0008, 0.0028) * W
            d.ellipse([x - r, y - r, x + r, y + r], fill=rng.choice([pal[0], pal[1], pal[2]]))
        m = W * 0.016
        lw = max(1, int(W * 0.0016))
        for x0, x1 in ((m, W / 2 - m), (W / 2 + m, W - m)):
            d.rectangle([x0, m, x1, H - m], outline=pal[0], width=lw)
            d.rectangle([x0 + m * 0.5, m * 1.5, x1 - m * 0.5, H - m * 1.5], outline=pal[2], width=lw)


def draw_front(img: Image.Image, theme: dict, seed: int = 0):
    """Небольшие угловые «наклейки» ПОВЕРХ фотографий (занимают только углы)."""
    W, H = img.size
    d = ImageDraw.Draw(img)
    rng = random.Random(seed + 7)
    pal = theme["palette"]
    kind = theme.get("decor")
    if kind == "birthday":
        r = W * 0.024
        cols = pal[:]
        rng.shuffle(cols)
        # слева-сверху у шаблонов заголовок, справа-снизу подпись обложки: декор в двух других углах
        for (cx, cy, c) in ((W * 0.974, H * 0.075, cols[0]), (W * 0.934, H * 0.05, cols[1]), (W * 0.955, H * 0.125, cols[2])):
            _balloon(d, cx, cy, r, c, 2.4)
        for (cx, cy, c) in ((W * 0.026, H * 0.925, cols[3]), (W * 0.066, H * 0.95, cols[4])):
            _balloon(d, cx, cy, r * 0.9, c, 1.2)
    elif kind == "wedding":
        L = W * 0.06
        col = pal[2]
        _sprig(d, W * 0.955, H * 0.07, math.pi * 0.82, L, col)
        _sprig(d, W * 0.045, H * 0.93, -math.pi * 0.18, L, col)
    elif kind == "travel":
        x, y = W * 0.95, H * 0.12 + math.sin(math.pi * 3) * H * 0.045
        r = W * 0.012
        d.polygon([(x - r * 0.8, y + r * 0.4), (x + r * 0.8, y + r * 0.4), (x, y + r * 2.6)], fill=pal[1])
        d.ellipse([x - r, y - r, x + r, y + r], fill=pal[1])
        d.ellipse([x - r * 0.4, y - r * 0.4, x + r * 0.4, y + r * 0.4], fill=(255, 255, 255))
        sx, sy, sr = W * 0.06, H * 0.9, W * 0.032
        d.ellipse([sx - sr, sy - sr, sx + sr, sy + sr], outline=pal[0], width=max(2, int(W * 0.0028)))
        d.ellipse([sx - sr * 0.78, sy - sr * 0.78, sx + sr * 0.78, sy + sr * 0.78], outline=pal[0], width=max(1, int(W * 0.0014)))
        _star(d, sx, sy, sr * 0.42, pal[0])
    elif kind == "halloween":
        for (cx, cy, s_) in ((W * 0.95, H * 0.07, W * 0.03), (W * 0.9, H * 0.13, W * 0.02), (W * 0.045, H * 0.93, W * 0.026)):
            _bat_t(d, cx, cy, s_)
        pr = W * 0.032
        px, py = W * 0.96, H * 0.93
        for dx in (-0.55, 0.55, 0):
            d.ellipse([px + dx * pr - pr * .7, py - pr * .7, px + dx * pr + pr * .7, py + pr * .7],
                      fill=(238, 120, 20), outline=(150, 60, 0), width=max(1, int(W * .0016)))
        d.rectangle([px - pr * .08, py - pr * .95, px + pr * .12, py - pr * .6], fill=(60, 100, 30))
    elif kind == "newyear":
        for (cx, cy, r) in ((W * 0.96, H * 0.07, W * 0.02), (W * 0.915, H * 0.045, W * 0.011),
                            (W * 0.04, H * 0.93, W * 0.02), (W * 0.095, H * 0.96, W * 0.011)):
            _star(d, cx, cy, r, pal[1])
        for (cx, cy, r) in ((W * 0.965, H * 0.95, W * 0.018), (W * 0.52, H * 0.045, W * 0.014)):
            _snow(d, cx, cy, r, (255, 255, 255), max(1, int(W * 0.0022)))

    elif kind == "botanic":
        L = W * 0.07
        _sprig(d, W * 0.96, H * 0.08, math.pi * 0.80, L, pal[2], 6)
        _sprig(d, W * 0.04, H * 0.92, -math.pi * 0.20, L, pal[2], 6)
        _sprig(d, W * 0.94, H * 0.10, math.pi * 0.62, L * 0.7, _lighten(pal[1], .2), 4)
    elif kind == "goldinset":
        for (cx, cy) in ((W * 0.04, H * 0.07), (W * 0.96, H * 0.93)):
            _star(d, cx, cy, W * 0.011, pal[0])
