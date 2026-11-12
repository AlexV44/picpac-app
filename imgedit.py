# -*- coding: utf-8 -*-
"""
Редактирование фото по текстовому запросу.

Только локальные приёмы без генерации изображений нейросетью (генеративные модели не используются):
стили «Хэллоуин», «Новый год», «День рождения», ч/б, винтаж, мультяшный, «ярче»
и удаление объекта по отметке пальцем (inpaint OpenCV).
Что локально невозможно (открыть глаза, дорисовать шляпу), честно сообщается пользователю.
"""
from __future__ import annotations

import io
import math
import os
import random
import re
import time
import logging
_lg = logging.getLogger("picpac")

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFilter



class EditError(Exception):
    pass


def status() -> dict:
    """Для админ-панели. Генерация и дорисовка фото нейросетью не используются, только локальные приёмы."""
    return dict(mode="local", ok=True, url="", error="")


# ---------------------------------------------------------------------------
# ЛОКАЛЬНЫЕ ПРИЁМЫ
# ---------------------------------------------------------------------------
def _np(im):
    return np.asarray(im.convert("RGB")).copy()


def _vignette(arr, strength=0.55, power=2.2):
    h, w = arr.shape[:2]
    y, x = np.ogrid[:h, :w]
    d = np.sqrt(((x - w / 2) / (w / 2)) ** 2 + ((y - h / 2) / (h / 2)) ** 2) / math.sqrt(2)
    m = 1 - strength * np.clip(d, 0, 1) ** power
    return (arr.astype(np.float32) * m[..., None])


def _tint(arr, rgb, k):
    a = arr.astype(np.float32)
    return a * (1 - k) + np.array(rgb, np.float32) * k


def _bat(d, cx, cy, s, fill=(15, 10, 25)):
    pts = [(cx, cy - s * .15), (cx + s * .25, cy - s * .35), (cx + s * .5, cy - s * .3), (cx + s * 1.0, cy - s * .05),
           (cx + s * .75, cy + s * .02), (cx + s * .6, cy + s * .22), (cx + s * .38, cy + s * .05), (cx + s * .18, cy + s * .25),
           (cx, cy + s * .12)]
    mir = [(2 * cx - x, y) for x, y in reversed(pts)]
    d.polygon(pts + mir, fill=fill)
    d.polygon([(cx - s * .1, cy - s * .15), (cx - s * .06, cy - s * .32), (cx, cy - s * .18),
               (cx + s * .06, cy - s * .32), (cx + s * .1, cy - s * .15)], fill=fill)


def halloween(im: Image.Image, seed=7) -> Image.Image:
    rng = random.Random(seed)
    a = _np(im)
    a = _tint(a, (255, 120, 20), 0.12) * np.array([1.0, 0.86, 0.78]) * 0.82
    a = _vignette(np.clip(a, 0, 255), 0.75, 2.0)
    out = Image.fromarray(np.clip(a, 0, 255).astype(np.uint8)).convert("RGBA")
    W, H = out.size
    ov = Image.new("RGBA", out.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(ov)
    # луна
    mr = W * 0.07
    mx, my = W * 0.83, H * 0.14
    glow = Image.new("RGBA", out.size, (0, 0, 0, 0))
    ImageDraw.Draw(glow).ellipse([mx - mr * 1.8, my - mr * 1.8, mx + mr * 1.8, my + mr * 1.8], fill=(255, 220, 130, 70))
    ov = Image.alpha_composite(ov, glow.filter(ImageFilter.GaussianBlur(mr * 0.8)))
    d = ImageDraw.Draw(ov)
    d.ellipse([mx - mr, my - mr, mx + mr, my + mr], fill=(255, 232, 160, 235))
    for cx, cy, r in ((-0.3, -0.2, .22), (0.25, 0.25, .16), (0.2, -0.35, .12)):
        d.ellipse([mx + cx * mr - r * mr, my + cy * mr - r * mr, mx + cx * mr + r * mr, my + cy * mr + r * mr], fill=(235, 205, 130, 200))
    # летучие мыши
    for _ in range(6):
        bx, by = rng.uniform(.05, .8) * W, rng.uniform(.03, .3) * H
        _bat(d, bx, by, rng.uniform(.025, .05) * W)
    # паутина в левом верхнем углу
    cw = W * 0.2
    for k in range(6):
        ang = k * (math.pi / 2) / 5
        d.line([(0, 0), (math.cos(ang) * cw, math.sin(ang) * cw)], fill=(235, 235, 235, 170), width=max(1, int(W * .002)))
    for r in (0.3, 0.55, 0.8, 1.0):
        pts = [(math.cos(k * math.pi / 2 / 5) * cw * r * (0.93 if k % 2 else 1), math.sin(k * math.pi / 2 / 5) * cw * r * (0.93 if k % 2 else 1)) for k in range(6)]
        d.line(pts, fill=(235, 235, 235, 150), width=max(1, int(W * .0016)))
    # тыква внизу справа
    pr = W * 0.07
    px, py = W * 0.9, H * 0.9
    for dx in (-0.55, 0.55, 0):
        d.ellipse([px + dx * pr - pr * .7, py - pr * .7, px + dx * pr + pr * .7, py + pr * .7], fill=(238, 120, 20, 245), outline=(150, 60, 0, 255), width=max(1, int(W * .002)))
    d.rectangle([px - pr * .08, py - pr * .95, px + pr * .12, py - pr * .6], fill=(60, 100, 30, 255))
    for ex in (-0.3, 0.3):
        d.polygon([(px + ex * pr, py - pr * .1), (px + ex * pr - pr * .13, py + pr * .1), (px + ex * pr + pr * .13, py + pr * .1)], fill=(40, 20, 0, 255))
    d.polygon([(px - pr * .3, py + pr * .25), (px - pr * .1, py + pr * .38), (px, py + pr * .28), (px + pr * .1, py + pr * .38), (px + pr * .3, py + pr * .25), (px + pr * .15, py + pr * .5), (px - pr * .15, py + pr * .5)], fill=(40, 20, 0, 255))
    return Image.alpha_composite(out, ov).convert("RGB")


def newyear(im: Image.Image, seed=3) -> Image.Image:
    rng = random.Random(seed)
    a = _tint(_np(im), (120, 160, 230), 0.14) * np.array([0.95, 1.0, 1.08])
    a = _vignette(np.clip(a, 0, 255), 0.35)
    out = Image.fromarray(np.clip(a, 0, 255).astype(np.uint8)).convert("RGBA")
    W, H = out.size
    ov = Image.new("RGBA", out.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(ov)
    for _ in range(int(W * H / 9000)):
        x, y, r = rng.uniform(0, W), rng.uniform(0, H), rng.uniform(.0018, .005) * W
        d.ellipse([x - r, y - r, x + r, y + r], fill=(255, 255, 255, rng.randint(150, 240)))
    # гирлянда
    pts = [(W * t / 40, H * 0.035 + math.sin(t / 40 * math.pi * 5) * H * 0.02) for t in range(41)]
    d.line(pts, fill=(40, 60, 40, 220), width=max(1, int(W * .0025)))
    cols = [(255, 80, 80), (255, 210, 80), (90, 190, 255), (120, 230, 130)]
    for i, (x, y) in enumerate(pts[1::2]):
        r = W * .008
        c = cols[i % 4]
        d.ellipse([x - r * 2.2, y - r * 2.2, x + r * 2.2, y + r * 2.2], fill=c + (60,))
        d.ellipse([x - r, y, x + r, y + r * 2.4], fill=c + (255,))
    return Image.alpha_composite(out, ov.filter(ImageFilter.GaussianBlur(.4))).convert("RGB")


def birthday(im: Image.Image, seed=5) -> Image.Image:
    import themes as T
    rng = random.Random(seed)
    out = im.convert("RGBA")
    ov = Image.new("RGBA", out.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(ov)
    T._confetti(d, out.width, out.height, rng, T.THEMES["birthday"]["palette"], int(out.width * out.height / 7000))
    out = Image.alpha_composite(out, ov).convert("RGB")
    T.draw_front(out, T.THEMES["birthday"], seed)
    return out


def bw(im):
    g = cv2.cvtColor(_np(im), cv2.COLOR_RGB2GRAY)
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    return Image.fromarray(cv2.cvtColor(clahe.apply(g), cv2.COLOR_GRAY2RGB))


def vintage(im):
    a = _np(im).astype(np.float32)
    sep = np.stack([a @ [.393, .769, .189], a @ [.349, .686, .168], a @ [.272, .534, .131]], -1)
    a = 0.7 * sep + 0.3 * a
    a = _vignette(np.clip(a, 0, 255), 0.45)
    rng = np.random.default_rng(1)
    a += rng.normal(0, 6, a.shape[:2])[..., None]
    return Image.fromarray(np.clip(a, 0, 255).astype(np.uint8))


def cartoon(im):
    a = _np(im)
    s = max(a.shape[:2])
    if s > 1400:
        k = 1400 / s
        a = cv2.resize(a, None, fx=k, fy=k, interpolation=cv2.INTER_AREA)
    return Image.fromarray(cv2.stylization(a, sigma_s=60, sigma_r=0.45))


def enhance(im):
    from ai_core import tone_enhance, pil_to_bgr, bgr_to_pil
    bgr, _ = tone_enhance(pil_to_bgr(im))
    return bgr_to_pil(bgr)


def remove_objects(im: Image.Image, marks) -> Image.Image:
    """marks: [{x,y,r}] в долях изображения (r — доля меньшей стороны)."""
    if not marks:
        raise EditError("Отметьте пальцем на фото, что нужно убрать.")
    a = _np(im)
    h, w = a.shape[:2]
    mask = np.zeros((h, w), np.uint8)
    for m in marks:
        r = max(6, int(float(m.get("r", .06)) * min(w, h)))
        cv2.circle(mask, (int(float(m["x"]) * w), int(float(m["y"]) * h)), r, 255, -1)
    mask = cv2.dilate(mask, np.ones((9, 9), np.uint8))
    s = 1.0
    if max(h, w) > 1600:
        s = 1600 / max(h, w)
        small = cv2.resize(a, None, fx=s, fy=s, interpolation=cv2.INTER_AREA)
        msmall = cv2.resize(mask, (small.shape[1], small.shape[0]), interpolation=cv2.INTER_NEAREST)
        res = cv2.inpaint(small, msmall, 5, cv2.INPAINT_TELEA)
        res = cv2.resize(res, (w, h), interpolation=cv2.INTER_CUBIC)
        soft = cv2.GaussianBlur(mask, (0, 0), 3).astype(np.float32)[..., None] / 255
        res = (res * soft + a * (1 - soft)).astype(np.uint8)
    else:
        res = cv2.inpaint(a, mask, 5, cv2.INPAINT_TELEA)
    return Image.fromarray(res)


# (ключевые слова, функция, название)
LOCAL_STYLES = [
    (r"хэллоуин|хеллоуин|halloween|хэлоуин", halloween, "Хэллоуин"),
    (r"нов(ый|ого|огодн\w*) год|новогодн|рождеств|зимн|снег", newyear, "Новый год"),
    (r"день рождения|днюх|праздничн|конфетти|шарик", birthday, "День рождения"),
    (r"ч/б|чёрно|черно|монохром|\bbw\b", bw, "Чёрно-белое"),
    (r"винтаж|ретро|сепи|старин|плёнк|пленк", vintage, "Винтаж"),
    (r"мульт|комикс|рисован|cartoon", cartoon, "Мультяшный стиль"),
    (r"ярче|улучш|исправь цвет|цвет|контраст|светлее|авто", enhance, "Автоулучшение"),
]
REMOVE_RX = r"убер|удал|сотри|убрать|удалить|стереть|избав|без (человек|мусор|лишн)"
NEEDS_MODEL_NOTE = ("Такую правку (открыть глаза, дорисовать предмет, поменять одежду) сейчас сделать не получится. "
                    "Доступны стили (Хэллоуин, Новый год, День рождения, ч/б, винтаж, мультяшный, ярче) "
                    "и удаление объекта по отметке.")


def local_edit(im: Image.Image, prompt: str, marks=None):
    """Возвращает (Image, название приёма). Бросает EditError, если локально сделать нельзя."""
    low = prompt.lower()
    if re.search(REMOVE_RX, low):
        if not marks:
            raise EditError("Чтобы убрать объект, коснитесь его на фото (можно несколько раз), затем нажмите «Применить».")
        return remove_objects(im, marks), "Удаление объекта"
    for rx, fn, name in LOCAL_STYLES:
        if re.search(rx, low):
            return fn(im), name
    raise EditError(NEEDS_MODEL_NOTE)


def run_edit(im: Image.Image, prompt: str, marks=None, log=None):
    """Главная точка входа. Возвращает dict(image, source='local', label, note, meta)."""
    prompt = (prompt or "").strip()
    if not prompt:
        raise EditError("Напишите, что сделать с фото.")
    log = log or (lambda *a, **k: None)
    log("Подбираю подходящий приём правки.")
    out, label = local_edit(im, prompt, marks)
    log(f"Применила локальный приём: {label}.", "ok")
    return dict(image=out, source="local", label=label, note="", meta={})


EDIT_RX = re.compile(
    r"глаз|моргн|улыб|убер[иуеь]|удали|сотри|сотр|убрать|дорис|добав\w+ (на фото|на фотографи|шляп|очки|цветы|снег|фон)|"
    r"замени фон|фон на|хэллоуин|хеллоуин|в стиле|сделай (ч/б|чёрно|черно|мульт|винтаж|ярче|светлее)|ретушь|отретуш|"
    r"волос|одежд|шляп|очки|помен\w+ (одежд|фон|цвет)|"
    r"измени\w* (это |эту )?(фото|снимок|кадр|фотографи)|(фото|снимок|кадр)\w* ?\d* ?(добав|дорис|убер|удали)|добав\w+ .{1,25} (на|в) (это |эту )?(фото|снимок|кадр)", re.I)
