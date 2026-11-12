# -*- coding: utf-8 -*-
"""
PicPac AI core: оценка качества, умный кроп, автоулучшение, шаблоны разворотов,
автораскладка фото по слотам и рендер разворотов в стиле picPac.

Зависимости: numpy, opencv-python, pillow, scipy  (опционально: ultralytics для запасного детектора людей)
"""
from __future__ import annotations

import io
import os
import threading
import math
import uuid
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageOps
from scipy.optimize import linear_sum_assignment

from themes import THEMES, get_theme, draw_back, draw_front

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
MODELS_DIR = os.path.join(BASE_DIR, "models")
YUNET_FILE = os.path.join(MODELS_DIR, "face_detection_yunet_2023mar.onnx")
YUNET_URL = ("https://github.com/opencv/opencv_zoo/raw/main/models/"
             "face_detection_yunet/face_detection_yunet_2023mar.onnx")

# Физический размер разворота (две страницы), см. Формат страницы 14x20.
SPREAD_CM = (28.0, 20.0)
MAX_SIDE = 3200            # внутренний предел размера хранимого фото
TARGET_DPI = 300
MIN_OK_DPI = 150

# ---------------------------------------------------------------------------
# ШРИФТЫ
# ---------------------------------------------------------------------------
_FONT_DIRS = ["/usr/share/fonts/truetype/dejavu", "C:/Windows/Fonts", "/Library/Fonts",
              "/System/Library/Fonts/Supplemental", os.path.join(BASE_DIR, "fonts")]


def _font(names, size):
    for d in _FONT_DIRS:
        for n in names:
            p = os.path.join(d, n)
            if os.path.exists(p):
                try:
                    return ImageFont.truetype(p, size)
                except Exception:
                    pass
    return ImageFont.load_default()


def serif(size, bold=False, italic=False):
    if bold:
        return _font(["DejaVuSerif-Bold.ttf", "timesbd.ttf", "Georgia Bold.ttf"], size)
    if italic:
        return _font(["DejaVuSerif-Italic.ttf", "timesi.ttf", "Georgia Italic.ttf"], size)
    return _font(["DejaVuSerif.ttf", "times.ttf", "Georgia.ttf"], size)


def sans(size, bold=False):
    return _font(["DejaVuSans-Bold.ttf", "arialbd.ttf", "Arial Bold.ttf"] if bold
                 else ["DejaVuSans.ttf", "arial.ttf", "Arial.ttf"], size)


# ---------------------------------------------------------------------------
# ДЕТЕКТОР ЛИЦ (цепочка: YuNet -> Haar -> YOLO person -> нет лиц)
# ---------------------------------------------------------------------------
def ensure_yunet(download: bool = True) -> Optional[str]:
    """Возвращает путь к валидной модели YuNet или None."""
    def valid(p):
        return os.path.exists(p) and os.path.getsize(p) > 50_000
    if valid(YUNET_FILE):
        return YUNET_FILE
    if not download:
        return None
    try:
        os.makedirs(MODELS_DIR, exist_ok=True)
        urllib.request.urlretrieve(YUNET_URL, YUNET_FILE)
        if valid(YUNET_FILE):
            return YUNET_FILE
        os.remove(YUNET_FILE)
    except Exception:
        try:
            if os.path.exists(YUNET_FILE):
                os.remove(YUNET_FILE)
        except Exception:
            pass
    return None


class FaceDetector:
    """Возвращает лица в нормализованных координатах (x, y, w, h, score) от 0 до 1."""

    def __init__(self, yolo=None, download: bool = True):
        self.yolo = yolo
        self.name = "нет детектора"
        self._yunet = None
        self._haar = None
        path = ensure_yunet(download) if hasattr(cv2, "FaceDetectorYN") else None
        if path:
            try:
                self._yunet = cv2.FaceDetectorYN.create(path, "", (320, 320), 0.6, 0.3, 200)
                self.name = "YuNet (OpenCV)"
            except Exception:
                self._yunet = None
        if self._yunet is None:
            try:
                hp = os.path.join(cv2.data.haarcascades, "haarcascade_frontalface_default.xml")
                if os.path.exists(hp):
                    c = cv2.CascadeClassifier(hp)
                    if not c.empty():
                        self._haar = c
                        self.name = "Haar cascade"
            except Exception:
                pass
        if self._yunet is None and self._haar is None and yolo is not None:
            self.name = "YOLOv8 (голова по рамке человека)"

    _lock = threading.Lock()      # cv2.FaceDetectorYN не потокобезопасен: детектор общий для всех пользователей

    def detect(self, bgr: np.ndarray):
        with FaceDetector._lock:
            return self._detect(bgr)

    def _detect(self, bgr: np.ndarray):
        h, w = bgr.shape[:2]
        scale = 1280 / max(h, w) if max(h, w) > 1280 else 1.0
        img = cv2.resize(bgr, (int(w * scale), int(h * scale))) if scale != 1.0 else bgr
        ih, iw = img.shape[:2]
        out = []
        try:
            if self._yunet is not None:
                self._yunet.setInputSize((iw, ih))
                _, faces = self._yunet.detect(img)
                if faces is not None:
                    for f in faces:
                        x, y, fw, fh = f[:4]
                        out.append((x / iw, y / ih, fw / iw, fh / ih, float(f[-1])))
            elif self._haar is not None:
                gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
                rects = self._haar.detectMultiScale(gray, 1.1, 5, minSize=(max(24, iw // 25),) * 2)
                for (x, y, fw, fh) in rects:
                    out.append((x / iw, y / ih, fw / iw, fh / ih, 0.8))
            elif self.yolo is not None:
                res = self.yolo(img, verbose=False)
                for r in res:
                    for b in r.boxes:
                        if int(b.cls) != 0:
                            continue
                        x1, y1, x2, y2 = [float(v) for v in b.xyxy[0]]
                        bw, bh = x2 - x1, y2 - y1
                        if bh < ih * 0.08:
                            continue
                        hw = bw * 0.42
                        cx = (x1 + x2) / 2
                        out.append(((cx - hw / 2) / iw, y1 / ih, hw / iw, (bh * 0.17) / ih, float(b.conf)))
        except Exception:
            return []
        return [(max(0, x), max(0, y), min(fw, 1 - max(0, x)), min(fh, 1 - max(0, y)), s)
                for (x, y, fw, fh, s) in out if fw > 0.01 and fh > 0.01]


# ---------------------------------------------------------------------------
# КАРТА ЗАМЕТНОСТИ (spectral residual, без доп. зависимостей)
# ---------------------------------------------------------------------------
def saliency_map(bgr: np.ndarray, width: int = 96) -> np.ndarray:
    h, w = bgr.shape[:2]
    sw = width
    sh = max(8, int(round(width * h / w)))
    small = cv2.resize(bgr, (sw, sh), interpolation=cv2.INTER_AREA)
    gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY).astype(np.float32)
    f = np.fft.fft2(gray)
    amp, phase = np.abs(f), np.angle(f)
    log_amp = np.log(amp + 1e-8)
    resid = log_amp - cv2.blur(log_amp, (3, 3))
    sal = np.abs(np.fft.ifft2(np.exp(resid + 1j * phase))) ** 2
    sal = cv2.GaussianBlur(sal.astype(np.float32), (0, 0), 2.0)
    # добавляем контраст насыщенности (яркие объекты привлекают взгляд)
    hsv = cv2.cvtColor(small, cv2.COLOR_BGR2HSV)
    sat = cv2.GaussianBlur(hsv[:, :, 1].astype(np.float32), (0, 0), 2.0)
    sal = sal / (sal.max() + 1e-8) * 0.75 + (sat / 255.0) * 0.25
    yy, xx = np.mgrid[0:sh, 0:sw]
    g = np.exp(-(((xx - sw / 2) / (sw * 0.45)) ** 2 + ((yy - sh / 2) / (sh * 0.45)) ** 2))
    sal = sal * (0.7 + 0.3 * g)
    sal -= sal.min()
    return (sal / (sal.max() + 1e-8)).astype(np.float32)


# ---------------------------------------------------------------------------
# ОЦЕНКА КАЧЕСТВА
# ---------------------------------------------------------------------------
# Калибровка порогов резкости (см. calibrate в tests). Метрика: 75-й процентиль
# дисперсии лапласиана по блокам при длинной стороне 800 px (или по лицу).
SHARP_LO = 8.0     # ниже: явно размыто
SHARP_HI = 50.0    # выше: резко


def _lap_var(gray: np.ndarray) -> float:
    return float(cv2.Laplacian(gray, cv2.CV_64F).var())


def sharpness_metric(bgr: np.ndarray, faces) -> float:
    h, w = bgr.shape[:2]
    s = 800 / max(h, w)
    img = cv2.resize(bgr, (int(w * s), int(h * s)), interpolation=cv2.INTER_AREA) if s < 1 else bgr
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    gh, gw = gray.shape
    # если есть лица, главным считаем лицо: фон может быть в боке
    if faces:
        vals = []
        for (x, y, fw, fh, sc) in faces:
            x0, y0 = int(x * gw), int(y * gh)
            x1, y1 = int((x + fw) * gw), int((y + fh) * gh)
            crop = gray[max(0, y0):y1, max(0, x0):x1]
            if crop.shape[0] < 8 or crop.shape[1] < 8:
                continue
            crop = cv2.resize(crop, (96, 96), interpolation=cv2.INTER_AREA)
            vals.append((fw * fh, _lap_var(crop)))
        if vals:
            total = sum(a for a, _ in vals)
            return float(sum(a * v for a, v in vals) / total) * 0.55  # лицо менее текстурное
    vs = []
    for by in range(4):
        for bx in range(4):
            blk = gray[by * gh // 4:(by + 1) * gh // 4, bx * gw // 4:(bx + 1) * gw // 4]
            if blk.size:
                vs.append(_lap_var(blk))
    return float(np.percentile(vs, 75))


def analyze_quality(bgr: np.ndarray, faces, orig_size) -> dict:
    h, w = bgr.shape[:2]
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    sm = cv2.resize(gray, (min(w, 400), int(h * min(w, 400) / w)), interpolation=cv2.INTER_AREA)
    mean = float(sm.mean()) / 255.0
    dark_frac = float((sm < 12).mean())
    bright_frac = float((sm > 245).mean())
    p5, p95 = np.percentile(sm, (5, 95))
    contrast = float(p95 - p5)

    sharp = sharpness_metric(bgr, faces)
    sharp_s = float(np.clip((sharp - SHARP_LO) / (SHARP_HI - SHARP_LO), 0, 1))

    exp_s = 1.0
    issues = []
    if mean < 0.25 or dark_frac > 0.35:
        exp_s -= 0.45
        issues.append("тёмный кадр")
    elif mean < 0.33:
        exp_s -= 0.2
        issues.append("слегка тёмный")
    if mean > 0.80 or bright_frac > 0.25:
        exp_s -= 0.45
        issues.append("пересвет")
    if contrast < 70:
        exp_s -= 0.2
        issues.append("низкий контраст")
    exp_s = float(np.clip(exp_s, 0, 1))

    ow, oh = orig_size
    # для одного разворота даже самый крупный слот на 14 см: оцениваем DPI на странице 14x20
    dpi_page = min(ow, oh) / (14.0 / 2.54)
    res_s = float(np.clip((dpi_page - 100) / 200, 0, 1))
    if sharp_s < 0.25:
        issues.insert(0, "размытый кадр")
    elif sharp_s < 0.55:
        issues.insert(0, "мягкая резкость")
    if dpi_page < MIN_OK_DPI:
        issues.append("низкое разрешение")

    score = 100 * (0.5 * sharp_s + 0.25 * exp_s + 0.25 * res_s)
    if sharp_s < 0.25:
        score = min(score, 38)
    if score >= 75:
        grade = "good"
    elif score >= 50:
        grade = "mid"
    else:
        grade = "bad"
    msg = {"good": "Хорошее качество.",
           "mid": "Среднее качество; отпечаток может быть размытым.",
           "bad": "Низкое качество; на отпечатке будет заметна размытость."}[grade]
    return dict(score=round(score, 1), grade=grade, message=msg, sharp=round(sharp, 1),
                sharp_s=sharp_s, mean=mean, contrast=contrast, issues=issues,
                orig_w=ow, orig_h=oh, dpi_page=round(dpi_page))


def slot_dpi(photo: "Photo", crop, slot_w_cm: float) -> float:
    cw_px = crop[2] * photo.orig_size[0]
    return cw_px / (slot_w_cm / 2.54)


def slot_quality(photo: "Photo", crop, slot_w_cm: float):
    """(grade, message, dpi) для конкретного слота с учётом кропа."""
    dpi = slot_dpi(photo, crop, slot_w_cm)
    q = photo.q
    if photo.q["sharp_s"] < 0.25:
        return "bad", "Низкое качество; отпечаток будет размытым.", dpi
    if dpi < MIN_OK_DPI * 0.8:
        return "bad", f"Низкое разрешение ({dpi:.0f} dpi) для этого слота.", dpi
    if dpi < MIN_OK_DPI or q["grade"] == "mid" or dpi < 220:
        return "mid", "Среднее качество; отпечаток может быть размытым.", dpi
    return "good", "Хорошее качество.", dpi


# --- дубликаты -------------------------------------------------------------
def dhash(bgr: np.ndarray, size: int = 12) -> np.ndarray:
    g = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    g = cv2.resize(g, (size + 1, size), interpolation=cv2.INTER_AREA)
    return (g[:, 1:] > g[:, :-1]).flatten()


def hamming(a: np.ndarray, b: np.ndarray) -> int:
    return int(np.count_nonzero(a != b))


# ---------------------------------------------------------------------------
# УМНЫЙ КРОП
# ---------------------------------------------------------------------------
def smart_crop(img_w: int, img_h: int, aspect: float, faces, sal: np.ndarray,
               headroom: float = 0.07):
    """
    Подбор рамки заданного соотношения сторон (ширина/высота).
    Возвращает (x, y, w, h) в НОРМАЛИЗОВАННЫХ координатах и dict с пояснением.
    Критерии: не резать лица, оставить воздух над головой, линия глаз на верхней
    трети, лица на вертикалях третей, максимум заметных областей, больше пикселей.
    """
    wmax = min(img_w, img_h * aspect)
    fpx = [(x * img_w, y * img_h, w * img_w, h * img_h, s) for (x, y, w, h, s) in faces]
    sh, sw = sal.shape
    sal_tot = float(sal.sum()) + 1e-8
    sal_int = cv2.integral(sal.astype(np.float64))

    def sal_in(x0, y0, x1, y1):
        a, b = int(x0 / img_w * sw), int(y0 / img_h * sh)
        c, d = max(a + 1, int(math.ceil(x1 / img_w * sw))), max(b + 1, int(math.ceil(y1 / img_h * sh)))
        c, d = min(c, sw), min(d, sh)
        return float(sal_int[d, c] - sal_int[b, c] - sal_int[d, a] + sal_int[b, a])

    areas = [fw * fh for (_, _, fw, fh, _) in fpx]
    atot = sum(areas) or 1.0
    best, best_s, best_info = None, -1e9, {}
    scales = [1.0, 0.88, 0.76, 0.64, 0.52] if fpx else [1.0, 0.85, 0.7]
    for sc in scales:
        cw = wmax * sc
        ch = cw / aspect
        if ch > img_h + 1e-6:
            continue
        nx = 1 if img_w - cw < 1 else 18
        ny = 1 if img_h - ch < 1 else 18
        for ix in range(nx):
            x0 = 0 if nx == 1 else (img_w - cw) * ix / (nx - 1)
            for iy in range(ny):
                y0 = 0 if ny == 1 else (img_h - ch) * iy / (ny - 1)
                x1, y1 = x0 + cw, y0 + ch
                s = 0.0
                cut = 0
                if fpx:
                    for (fx, fy, fw, fh, fs), fa in zip(fpx, areas):
                        wgt = fa / atot
                        ix0, iy0 = max(fx, x0), max(fy, y0)
                        ix1, iy1 = min(fx + fw, x1), min(fy + fh, y1)
                        inter = max(0, ix1 - ix0) * max(0, iy1 - iy0)
                        cont = inter / (fa + 1e-8)
                        if cont < 0.97:
                            cut += 1
                        s -= wgt * (1 - cont) * 14
                        # воздух над головой
                        if cont > 0.5 and (fy - y0) < headroom * ch:
                            s -= wgt * (headroom * ch - (fy - y0)) / ch * 6
                        # лицо не должно быть слишком маленьким/огромным в кадре
                        rel = fh / ch
                        if rel < 0.10:
                            s -= wgt * (0.10 - rel) * 4
                        if rel > 0.55:
                            s -= wgt * (rel - 0.55) * 4
                    # правило третей по главной точке (центр масс лиц)
                    cx = sum((fx + fw / 2) * fa for (fx, fy, fw, fh, _), fa in zip(fpx, areas)) / atot
                    cy = sum((fy + fh * 0.4) * fa for (fx, fy, fw, fh, _), fa in zip(fpx, areas)) / atot
                    rx, ry = (cx - x0) / cw, (cy - y0) / ch
                    dx = min(abs(rx - 1 / 3), abs(rx - 2 / 3), abs(rx - 0.5) * 0.8)
                    dy = min(abs(ry - 1 / 3), abs(ry - 0.45) * 0.9)
                    s -= 1.4 * dx + 1.0 * dy
                    s += 0.7 * sal_in(x0, y0, x1, y1) / sal_tot
                else:
                    s += 1.6 * sal_in(x0, y0, x1, y1) / sal_tot
                s += 0.5 * sc  # больше пикселей = выше резкость и dpi
                if s > best_s:
                    best_s = s
                    best = (x0, y0, cw, ch)
                    best_info = dict(faces=len(fpx), cut=cut, scale=sc)
    if best is None:
        cw = min(img_w, img_h * aspect)
        ch = cw / aspect
        best = ((img_w - cw) / 2, (img_h - ch) / 2, cw, ch)
        best_info = dict(faces=len(fpx), cut=0, scale=1.0)
    x, y, w, h = best
    return (x / img_w, y / img_h, w / img_w, h / img_h), best_info


def draw_crop_editor(img: Image.Image, crop, out_w: int = 720) -> Image.Image:
    """Как в picPac: вне рамки кадр приглушён, внутри сетка 3x3."""
    s = out_w / img.width
    base = img.resize((out_w, int(img.height * s)), Image.LANCZOS).convert("RGBA")
    veil = Image.new("RGBA", base.size, (255, 255, 255, 190))
    comp = Image.alpha_composite(base, veil)
    x, y, w, h = crop
    box = (int(x * base.width), int(y * base.height),
           int((x + w) * base.width), int((y + h) * base.height))
    comp.paste(base.crop(box), box[:2])
    d = ImageDraw.Draw(comp)
    bw, bh = box[2] - box[0], box[3] - box[1]
    for i in (1, 2):
        d.line([(box[0] + bw * i // 3, box[1]), (box[0] + bw * i // 3, box[3])], fill=(255, 255, 255, 235), width=2)
        d.line([(box[0], box[1] + bh * i // 3), (box[2], box[1] + bh * i // 3)], fill=(255, 255, 255, 235), width=2)
    d.rectangle(box, outline=(255, 255, 255, 255), width=3)
    return comp.convert("RGB")


# ---------------------------------------------------------------------------
# АВТОУЛУЧШЕНИЕ
# ---------------------------------------------------------------------------
def tone_enhance(bgr: np.ndarray):
    """Тоновая коррекция всего кадра. Возвращает (bgr, список_операций)."""
    ops = []
    out = bgr.copy()
    lab = cv2.cvtColor(out, cv2.COLOR_BGR2LAB)
    L = lab[:, :, 0].astype(np.float32)
    lo, hi = np.percentile(L, (0.5, 99.5))
    if hi - lo < 215 and hi - lo > 20:
        L = np.clip((L - lo) / (hi - lo) * 255, 0, 255)
        ops.append("автоуровни")
    mean = float(L.mean()) / 255
    target = 0.46
    if abs(mean - target) > 0.07:
        gamma = float(np.clip(math.log(target) / math.log(max(mean, 0.02)), 0.7, 1.6))
        L = 255 * np.power(np.clip(L, 0, 255) / 255, gamma)
        ops.append("яркость")
    L8 = np.clip(L, 0, 255).astype(np.uint8)
    if float(np.std(L8)) < 48:
        L8 = cv2.createCLAHE(clipLimit=1.6, tileGridSize=(8, 8)).apply(L8)
        ops.append("локальный контраст")
    lab[:, :, 0] = L8
    out = cv2.cvtColor(lab, cv2.COLOR_LAB2BGR)
    hsv = cv2.cvtColor(out, cv2.COLOR_BGR2HSV).astype(np.float32)
    if hsv[:, :, 1].mean() < 60:
        hsv[:, :, 1] = np.clip(hsv[:, :, 1] * 1.12, 0, 255)
        out = cv2.cvtColor(hsv.astype(np.uint8), cv2.COLOR_HSV2BGR)
        ops.append("насыщенность")
    gray = cv2.cvtColor(out, cv2.COLOR_BGR2GRAY)
    small = cv2.resize(gray, (0, 0), fx=0.5, fy=0.5) if max(gray.shape) > 1200 else gray
    noise = float(np.median(np.abs(small.astype(np.int16) - cv2.medianBlur(small, 3).astype(np.int16))))
    if noise >= 3.0 and max(out.shape[:2]) <= 2600:
        out = cv2.fastNlMeansDenoisingColored(out, None, 3, 3, 5, 15)
        ops.append("шумоподавление")
    return out, ops


def adaptive_sharpen(arr: np.ndarray, amount: float) -> np.ndarray:
    if amount <= 0.01:
        return arr
    blur = cv2.GaussianBlur(arr, (0, 0), 1.1)
    out = cv2.addWeighted(arr, 1 + amount, blur, -amount, 0)
    return out


# ---------------------------------------------------------------------------
# МОДЕЛЬ ДАННЫХ
# ---------------------------------------------------------------------------
@dataclass
class Photo:
    id: str
    name: str
    img: Image.Image                 # исходное (RGB, не более MAX_SIDE)
    enh: Optional[Image.Image]       # с тоновой коррекцией; считается только когда нужна (ensure_enh)
    orig_size: tuple
    date: Optional[datetime]
    faces: list
    sal: np.ndarray
    hash: np.ndarray
    q: dict
    ops: list = field(default_factory=list)
    dup_of: Optional[str] = None
    excluded: bool = False
    enhance: Optional[bool] = None   # выбор пользователя для этого фото: True/False, None = как в проекте
    auto: bool = False               # итог: применять ли автоулучшение при показе и печати (по умолчанию НЕТ)
    by: str = ""                     # имя участника совместного альбома, который добавил фото ("" = владелец)

    def ensure_enh(self):
        """Тоновая коррекция считается лениво: пока пользователь не включил автоулучшение, это лишняя работа (~70% анализа фото)."""
        if self.enh is None:
            enh_bgr, ops = tone_enhance(pil_to_bgr(self.img))
            self.enh, self.ops = bgr_to_pil(enh_bgr), ops
        return self

    @property
    def aspect(self):
        return self.img.width / self.img.height


def _exif_date(im: Image.Image):
    try:
        ex = im.getexif()
        v = None
        try:
            v = ex.get_ifd(0x8769).get(36867)
        except Exception:
            pass
        v = v or ex.get(306)
        if v:
            return datetime.strptime(str(v)[:19], "%Y:%m:%d %H:%M:%S")
    except Exception:
        pass
    return None


def pil_to_bgr(im: Image.Image) -> np.ndarray:
    return cv2.cvtColor(np.asarray(im.convert("RGB")), cv2.COLOR_RGB2BGR)


def bgr_to_pil(a: np.ndarray) -> Image.Image:
    return Image.fromarray(cv2.cvtColor(a, cv2.COLOR_BGR2RGB))


def build_photo(name: str, im: Image.Image, detector: FaceDetector, date=None, orig_size=None) -> Photo:
    """Анализ готового PIL-изображения (уже повернутого по EXIF)."""
    orig_size = orig_size or im.size
    im = im.convert("RGB")
    if max(im.size) > MAX_SIDE:
        s = MAX_SIDE / max(im.size)
        im = im.resize((int(im.width * s), int(im.height * s)), Image.LANCZOS)
    bgr = pil_to_bgr(im)
    faces = detector.detect(bgr)
    q = analyze_quality(bgr, faces, orig_size)
    return Photo(id=uuid.uuid4().hex[:8], name=name, img=im, enh=None,
                 orig_size=orig_size, date=date, faces=faces, sal=saliency_map(bgr),
                 hash=dhash(bgr), q=q, ops=[])


def load_photo(name: str, data: bytes, detector: FaceDetector) -> Photo:
    im = Image.open(io.BytesIO(data))
    date = _exif_date(im)
    im = ImageOps.exif_transpose(im)
    return build_photo(name, im, detector, date=date, orig_size=im.size)


def rotate_photo(photo: Photo, degrees: int, detector: FaceDetector) -> Photo:
    """Поворот на 90/-90/180, пересчитывает анализ, сохраняет id."""
    im = photo.img.rotate(-degrees, expand=True) if degrees else photo.img
    osz = photo.orig_size if degrees % 180 == 0 else photo.orig_size[::-1]
    new = build_photo(photo.name, im, detector, date=photo.date, orig_size=osz)
    new.id, new.excluded, new.dup_of = photo.id, photo.excluded, photo.dup_of
    return new


def mark_duplicates(photos: list, thresh: int = 9):
    """Помечает почти одинаковые кадры; оставляет лучший по качеству в группе."""
    for p in photos:
        p.dup_of = None
    order = sorted(photos, key=lambda p: -p.q["score"])
    kept = []
    for p in order:
        for k in kept:
            if hamming(p.hash, k.hash) <= thresh:
                p.dup_of = k.id
                break
        else:
            kept.append(p)


# ---------------------------------------------------------------------------
# ШАБЛОНЫ РАЗВОРОТОВ (координаты нормализованы к развороту 28x20 см)
# ---------------------------------------------------------------------------
def _t(tid, name, slots, texts=()):
    return dict(id=tid, name=name, slots=[tuple(s) for s in slots],
                texts=[dict(rect=tuple(r), role=role) for r, role in texts])


TEMPLATES = {t["id"]: t for t in [
    # точная копия шаблона со скриншота picPac: слева текст + фото, справа фото + 3 узких
    _t("title_left", "Заголовок + 5 фото",
       [(0, .5, .5, .5), (.5, 0, .5, .5),
        (.530, .545, .136, .40), (.670, .545, .136, .40), (.810, .545, .136, .40)],
       [((0, 0, .5, .5), "title")]),
    _t("hero_right_grid", "Крупное + 3 справа",
       [(0, 0, .5, 1), (.54, .06, .42, .46), (.54, .56, .205, .38), (.755, .56, .205, .38)]),
    _t("two_pages", "По фото на странице",
       [(.04, .06, .42, .88), (.54, .06, .42, .88)]),
    _t("full_bleed", "Фото на разворот", [(0, 0, 1, 1)]),
    _t("stack_three", "Два + одно", [(.04, .05, .42, .43), (.04, .52, .42, .43), (.54, .05, .42, .90)]),
    _t("grid4", "Сетка 2x2", [(.04, .05, .42, .43), (.04, .52, .42, .43),
                              (.54, .05, .42, .43), (.54, .52, .42, .43)]),
    _t("mosaic6", "Мозаика 6",
       [(.04, .05, .42, .43), (.04, .52, .205, .43), (.255, .52, .205, .43),
        (.54, .05, .42, .43), (.54, .52, .205, .43), (.755, .52, .205, .43)]),
    _t("quote_right", "Текст + фото справа", [(.5, 0, .5, 1)],
       [((.04, .3, .42, .4), "quote")]),
]}


def slot_aspect(tid: str, j: int) -> float:
    x, y, w, h = TEMPLATES[tid]["slots"][j]
    return (w * SPREAD_CM[0]) / (h * SPREAD_CM[1])


def slot_width_cm(tid: str, j: int) -> float:
    return TEMPLATES[tid]["slots"][j][2] * SPREAD_CM[0]


# ---------------------------------------------------------------------------
# ПЛАНИРОВАНИЕ КНИГИ
# ---------------------------------------------------------------------------
def _aspect_cost(photo: Photo, tid: str, j: int) -> float:
    cost = abs(math.log(photo.aspect / slot_aspect(tid, j)))
    cost -= 0.25 * photo.q["score"] / 100
    # малоразрешённые кадры лучше ставить в маленькие слоты (там dpi выше)
    est_dpi = 0.8 * photo.orig_size[0] / (slot_width_cm(tid, j) / 2.54)
    cost += 0.8 * max(0.0, (200 - est_dpi) / 200)
    return cost


def assign_photos(photos: list, tid: str):
    """Венгерский алгоритм: фото -> слоты по ориентации и качеству."""
    n_slots = len(TEMPLATES[tid]["slots"])
    if not photos:
        return [None] * n_slots
    C = np.array([[_aspect_cost(p, tid, j) for p in photos] for j in range(n_slots)])
    rows, cols = linear_sum_assignment(C)
    res = [None] * n_slots
    for r, c in zip(rows, cols):
        res[r] = photos[c]
    return res


def _template_cost(photos: list, tid: str) -> float:
    n = len(TEMPLATES[tid]["slots"])
    sub = photos[:n]
    C = np.array([[_aspect_cost(p, tid, j) for p in sub] for j in range(n)])
    r, c = linear_sum_assignment(C)
    return float(C[r, c].mean())


def center_crop(img_w: int, img_h: int, aspect: float):
    """Простое кадрирование по центру (без ИИ): максимальная рамка нужного формата."""
    cw = min(img_w, img_h * aspect)
    ch = cw / aspect
    return [round((img_w - cw) / 2 / img_w, 5), round((img_h - ch) / 2 / img_h, 5), round(cw / img_w, 5), round(ch / img_h, 5)]


def make_slot(photo: Optional[Photo], tid: str, j: int, smart: bool = False):
    """По умолчанию кадр по центру; умное кадрирование (лица, композиция) — только smart=True, по просьбе пользователя."""
    if photo is None:
        return dict(photo=None, crop=None, manual=False)
    asp = slot_aspect(tid, j)
    if smart:
        crop, _ = smart_crop(photo.img.width, photo.img.height, asp, photo.faces, photo.sal)
    else:
        crop = center_crop(photo.img.width, photo.img.height, asp)
    return dict(photo=photo.id, crop=crop, manual=False)


def make_spread(tid: str, photos_for_spread: list, title="", subtitle=""):
    assigned = assign_photos(photos_for_spread, tid)
    return dict(template=tid,
                slots=[make_slot(p, tid, j) for j, p in enumerate(assigned)],
                texts=dict(title=title, subtitle=subtitle, quote=""))


def apply_theme(book: dict, theme_id: str, spreads: Optional[list] = None, cover: bool = True) -> None:
    """
    Применяет тему оформления. spreads=None -> вся книга (тема становится темой по умолчанию
    для новых разворотов), иначе список индексов разворотов (с 0).
    """
    if theme_id not in THEMES:
        raise ValueError(f"неизвестная тема: {theme_id}")
    rng = np.random.default_rng()
    idx = range(len(book["spreads"])) if spreads is None else spreads
    for i in idx:
        sp = book["spreads"][i]
        sp["theme"] = theme_id
        sp["decor_seed"] = int(rng.integers(1, 10_000_000))
    if spreads is None:
        book["theme"] = theme_id
    if cover and spreads is None:
        book["decor_seed"] = int(rng.integers(1, 10_000_000))


def inherit_theme(book: dict) -> None:
    """Новым разворотам (без темы) выдаётся тема книги."""
    tid = book.get("theme", "classic")
    rng = np.random.default_rng()
    for sp in book["spreads"]:
        if not sp.get("theme"):
            sp["theme"] = tid
            sp["decor_seed"] = int(rng.integers(1, 10_000_000))


def plan_book(photos: list, title: str = "МОЯ КНИГА", subtitle: str = "", theme: Optional[str] = None, manual_order: bool = False) -> dict:
    """Автоматическая раскладка выбранных фото по разворотам."""
    pool = list(photos) if manual_order else sorted(photos, key=lambda p: (p.date or datetime.max, p.name))
    spreads, i, prev = [], 0, []
    while i < len(pool):
        remaining = len(pool) - i
        if not spreads and remaining >= 5:
            tid = "title_left"
        else:
            best, best_c = None, 1e9
            for tid_c, t in TEMPLATES.items():
                n = len(t["slots"])
                if n > remaining:
                    continue
                c = _template_cost(pool[i:i + n], tid_c)
                if tid_c in prev[-2:]:
                    c += 0.45
                if n == 1 and remaining > 1:
                    c += 0.35
                c += 0.04 * abs(n - 4)
                if c < best_c:
                    best, best_c = tid_c, c
            tid = best
        n = len(TEMPLATES[tid]["slots"])
        sp = make_spread(tid, pool[i:i + n], title if (not spreads and tid == "title_left") else "",
                         subtitle if (not spreads and tid == "title_left") else "")
        spreads.append(sp)
        prev.append(tid)
        i += n
    book = dict(title=title, subtitle=subtitle, spreads=spreads)
    if theme:
        apply_theme(book, theme)
    return book


def empty_book(title="МОЯ КНИГА", subtitle="") -> dict:
    """Пустой шаблон альбома (как в picPac: серые слоты с плюсом)."""
    seq = ["title_left", "hero_right_grid", "grid4"]
    spreads = [dict(template=t, slots=[make_slot(None, t, j) for j in range(len(TEMPLATES[t]["slots"]))],
                    texts=dict(title=title if k == 0 else "", subtitle=subtitle if k == 0 else "", quote=""),
                    auto=True)
               for k, t in enumerate(seq)]
    return dict(title=title, subtitle=subtitle, spreads=spreads)


def used_ids(book: dict) -> set:
    return {s["photo"] for sp in book["spreads"] for s in sp["slots"] if s["photo"]}


def ai_fill(book: dict, photos: dict, pool: Optional[list] = None) -> dict:
    """
    ИИ подставляет фото в пустые слоты существующего шаблона:
    подбирает по ориентации/качеству, считает кроп. Остаток раскладывает
    в новые развороты.
    """
    used_before = set(used_ids(book))
    free = [p for p in (pool if pool is not None else photos.values())
            if not p.excluded and not p.dup_of and p.id not in used_ids(book)]
    free.sort(key=lambda p: (p.date or datetime.max, p.name))
    for sp in book["spreads"]:
        tid = sp["template"]
        empty = [j for j, s in enumerate(sp["slots"]) if s["photo"] is None]
        if not empty or not free:
            continue
        k = len(empty)
        take = free[:k]
        C = np.array([[_aspect_cost(p, tid, j) for p in take] for j in empty])
        r, c = linear_sum_assignment(C)
        for ri, ci in zip(r, c):
            sp["slots"][empty[ri]] = make_slot(take[ci], tid, empty[ri])
        for ci in sorted(set(c), reverse=True):
            free.remove(take[ci])
    if free:
        extra = plan_book(free, book["title"])
        for sp in extra["spreads"]:
            sp["texts"]["title"] = ""
        book["spreads"].extend(extra["spreads"])
    elif set(used_ids(book)) != used_before:
        _tidy_tail(book, photos)      # только если что-то реально подставили в этом вызове
    for sp in book["spreads"]:
        sp.pop("auto", None)          # стартовый шаблон отработал; дальше развороты принадлежат пользователю
    inherit_theme(book)
    return book


def _tidy_tail(book: dict, photos: dict):
    """После ПЕРВОЙ сборки по стартовому шаблону: убрать лишние пустые развороты шаблона в конце и
    подогнать последний под число кадров. Развороты, добавленные пользователем (без флага auto), не трогаем."""
    while (len(book["spreads"]) > 1 and book["spreads"][-1].get("auto")
           and not any(s["photo"] for s in book["spreads"][-1]["slots"])):
        book["spreads"].pop()
    sp = book["spreads"][-1]
    ps = [photos[s["photo"]] for s in sp["slots"] if s["photo"]]
    if sp.get("auto") and ps and len(ps) < len(sp["slots"]):
        cands = [t for t, d in TEMPLATES.items() if len(d["slots"]) == len(ps)]
        if cands:
            best = min(cands, key=lambda t: _template_cost(ps, t))
            change_template(book, len(book["spreads"]) - 1, best, photos)


def balance_book(book: dict, photos: dict) -> str:
    """
    Распределяет уже вставленные фото поровну по ВСЕМ имеющимся разворотам (в порядке съёмки),
    подбирая для каждого шаблон с нужным числом слотов. Так пустой разворот наполняется,
    когда свободных фото нет. Тексты и темы разворотов сохраняются.
    """
    ids = [s["photo"] for sp in book["spreads"] for s in sp["slots"] if s["photo"]]
    ps = sorted((photos[i] for i in ids), key=lambda p: (p.date or datetime.max, p.name))
    M, N = len(book["spreads"]), len(ps)
    if not ps:
        return "В книге нет фото для распределения."
    max_slots = max(len(t["slots"]) for t in TEMPLATES.values())
    if N > M * max_slots:
        return "Фото слишком много для такого числа разворотов."
    m = min(M, N)                           # фото меньше разворотов: лишние остаются пустыми
    base, extra = divmod(N, m)
    pos = 0
    for i, sp in enumerate(book["spreads"]):
        k = (base + (1 if i < extra else 0)) if i < m else 0
        chunk = ps[pos:pos + k]
        pos += k
        if not chunk:
            sp["slots"] = [make_slot(None, sp["template"], j) for j in range(len(TEMPLATES[sp["template"]]["slots"]))]
            continue
        cands = [t for t, d in TEMPLATES.items() if len(d["slots"]) == len(chunk)]
        if not cands:
            cands = [min(TEMPLATES, key=lambda t: abs(len(TEMPLATES[t]["slots"]) - len(chunk)))]
        tid = min(cands, key=lambda t: _template_cost(chunk, t))
        assigned = assign_photos(chunk, tid)
        sp["template"] = tid
        sp["slots"] = [make_slot(p, tid, j) for j, p in enumerate(assigned)]
    return f"Распределила {N} фото поровну по {M} разв."


def change_template(book: dict, si: int, new_tid: str, photos: dict):
    sp = book["spreads"][si]
    ps = [photos[s["photo"]] for s in sp["slots"] if s["photo"]]
    assigned = assign_photos(ps, new_tid)
    sp["template"] = new_tid
    sp["slots"] = [make_slot(p, new_tid, j) for j, p in enumerate(assigned)]


def fill_spread_empty(book: dict, si: int, free: list, photos: dict = None) -> int:
    """Заполняет пустые слоты одного разворота свободными фото (по ориентации). Возвращает число поставленных."""
    sp = book["spreads"][si]
    tid = sp["template"]
    empty = [j for j, s in enumerate(sp["slots"]) if s["photo"] is None]
    free = [p for p in free if p.id not in used_ids(book)]
    free.sort(key=lambda p: (p.date or datetime.max, p.name))
    take = free[:max(len(empty), 0) * 3] if len(free) > len(empty) else free
    if not empty or not take:
        return 0
    C = np.array([[_aspect_cost(p, tid, j) for p in take] for j in empty])
    r, c = linear_sum_assignment(C)
    for ri, ci in zip(r, c):
        sp["slots"][empty[ri]] = make_slot(take[ci], tid, empty[ri])
    return len(r)


def set_slot_photo(book: dict, si: int, sj: int, pid: Optional[str], photos: dict):
    sp = book["spreads"][si]
    sp["slots"][sj] = make_slot(photos[pid], sp["template"], sj) if pid else make_slot(None, sp["template"], sj)


def recrop_slot(book: dict, si: int, sj: int, photos: dict, smart: bool = True):
    sp = book["spreads"][si]
    pid = sp["slots"][sj]["photo"]
    if pid:
        sp["slots"][sj] = make_slot(photos[pid], sp["template"], sj, smart=smart)


# ---------------------------------------------------------------------------
# РЕНДЕР
# ---------------------------------------------------------------------------
BLUE = (169, 189, 224)
CREAM = (246, 245, 232)
GRAY = (217, 217, 217)
GOOD, MID, BAD = (62, 160, 96), (232, 168, 40), (214, 69, 65)


# ---------------------------------------------------------------------------
# ИИ-АПСКЕЙЛ (Real-ESRGAN, компактная модель realesr-general-x4v3, чистый PyTorch)
# ---------------------------------------------------------------------------
SR_URL = "https://github.com/xinntao/Real-ESRGAN/releases/download/v0.2.5.0/realesr-general-x4v3.pth"
SR_FILE = os.path.join(MODELS_DIR, "realesr-general-x4v3.pth")
SR_MAX_PIXELS = 1_500_000      # крупнее не апскейлим: там и так хватает пикселей, а CPU будет долго


def ensure_sr_weights(download: bool = True) -> Optional[str]:
    def valid(p):
        return os.path.exists(p) and os.path.getsize(p) > 1_000_000
    if valid(SR_FILE):
        return SR_FILE
    if not download:
        return None
    try:
        os.makedirs(MODELS_DIR, exist_ok=True)
        urllib.request.urlretrieve(SR_URL, SR_FILE)
        if valid(SR_FILE):
            return SR_FILE
        os.remove(SR_FILE)
    except Exception:
        try:
            if os.path.exists(SR_FILE):
                os.remove(SR_FILE)
        except Exception:
            pass
    return None


def tile_apply(img: np.ndarray, fn, scale: int, tile: int = 256, pad: int = 16) -> np.ndarray:
    """
    Обработка большого изображения плитками с перекрытием (чтобы не кончилась память).
    img: float32 HxWx3; fn(плитка HxWx3) -> (H*scale)x(W*scale)x3.
    """
    h, w = img.shape[:2]
    out = np.zeros((h * scale, w * scale, img.shape[2]), np.float32)
    for y in range(0, h, tile):
        for x in range(0, w, tile):
            y0, x0 = max(y - pad, 0), max(x - pad, 0)
            y1, x1 = min(y + tile + pad, h), min(x + tile + pad, w)
            res = fn(img[y0:y1, x0:x1])
            th, tw = min(tile, h - y) * scale, min(tile, w - x) * scale
            ty0, tx0 = (y - y0) * scale, (x - x0) * scale
            out[y * scale:y * scale + th, x * scale:x * scale + tw] = res[ty0:ty0 + th, tx0:tx0 + tw]
    return out


def _build_srvgg(torch, sd: dict):
    """SRVGGNetCompact; размеры (каналы, число слоёв, масштаб) берутся из самих весов."""
    nn = torch.nn
    F = torch.nn.functional
    idxs = sorted({int(k.split(".")[1]) for k in sd if k.startswith("body.")})
    last = idxs[-1]
    num_in = sd["body.0.weight"].shape[1]
    num_feat = sd["body.0.weight"].shape[0]
    out_ch = sd[f"body.{last}.weight"].shape[0]
    scale = int(round(math.sqrt(out_ch / num_in)))
    num_conv = (last - 2) // 2

    class Net(nn.Module):
        def __init__(self):
            super().__init__()
            self.scale = scale
            self.body = nn.ModuleList()
            self.body.append(nn.Conv2d(num_in, num_feat, 3, 1, 1))
            self.body.append(nn.PReLU(num_parameters=num_feat))
            for _ in range(num_conv):
                self.body.append(nn.Conv2d(num_feat, num_feat, 3, 1, 1))
                self.body.append(nn.PReLU(num_parameters=num_feat))
            self.body.append(nn.Conv2d(num_feat, num_in * scale * scale, 3, 1, 1))
            self.upsampler = nn.PixelShuffle(scale)

        def forward(self, x):
            out = x
            for layer in self.body:
                out = layer(out)
            out = self.upsampler(out)
            return out + F.interpolate(x, scale_factor=self.scale, mode="nearest")

    return Net(), scale


class Upscaler:
    """
    ИИ-повышение разрешения. Если PyTorch или веса недоступны, available=False и
    в reason лежит причина: приложение продолжает работать без апскейла.
    """

    def __init__(self, download: bool = True, tile: int = 256, _fn=None, _scale: int = 4):
        self.net, self.reason, self.name = None, "", "нет"
        self.scale, self.tile, self._cache = _scale, tile, {}
        self._fn = _fn                       # подставная функция (для тестов)
        self.torch, self.device, self.half = None, "cpu", False
        if _fn is not None:
            self.net, self.name = object(), "тестовый апскейлер"
            return
        try:
            import torch
        except Exception:
            self.reason = "не установлен PyTorch (pip install torch)"
            return
        path = ensure_sr_weights(download)
        if not path:
            self.reason = ("не удалось скачать веса: положите realesr-general-x4v3.pth в папку models/ "
                           "(" + SR_URL + ")")
            return
        try:
            self.torch = torch
            try:
                ckpt = torch.load(path, map_location="cpu", weights_only=True)
            except TypeError:
                ckpt = torch.load(path, map_location="cpu")
            sd = ckpt.get("params_ema") or ckpt.get("params") or ckpt
            net, self.scale = _build_srvgg(torch, sd)
            net.load_state_dict(sd, strict=True)
            self.device = "cuda" if torch.cuda.is_available() else "cpu"
            net.eval().to(self.device)
            if self.device == "cuda":
                net.half()
                self.half = True
            self.net = net
            self.name = f"Real-ESRGAN x{self.scale} ({'GPU' if self.device == 'cuda' else 'CPU'})"
        except Exception as e:
            self.net, self.reason = None, f"ошибка загрузки модели: {e}"

    @property
    def available(self) -> bool:
        return self.net is not None

    def _infer(self, tile_rgb: np.ndarray) -> np.ndarray:
        if self._fn is not None:
            return self._fn(tile_rgb)
        torch = self.torch
        t = torch.from_numpy(np.ascontiguousarray(tile_rgb.transpose(2, 0, 1))).unsqueeze(0).to(self.device)
        if self.half:
            t = t.half()
        with torch.no_grad():
            o = self.net(t)
        return o.squeeze(0).float().clamp_(0, 1).cpu().numpy().transpose(1, 2, 0)

    def upscale(self, rgb: np.ndarray, key=None) -> Optional[np.ndarray]:
        """RGB uint8 -> RGB uint8, размер x scale. key нужен для кэша."""
        if not self.available:
            return None
        if key is not None and key in self._cache:
            return self._cache[key]
        with Upscaler._lock:
            return self._upscale_locked(rgb, key)

    _lock = threading.Lock()

    def _upscale_locked(self, rgb, key):
        img = rgb.astype(np.float32) / 255.0
        out = tile_apply(img, self._infer, self.scale, self.tile, 16)
        res = np.clip(out * 255.0 + 0.5, 0, 255).astype(np.uint8)
        if key is not None:
            if len(self._cache) >= 24:
                self._cache.pop(next(iter(self._cache)))
            self._cache[key] = res
        return res


def render_slot_image(photo: Photo, crop, out_w: int, out_h: int, use_enhanced: bool = True,
                      sr: Optional[Upscaler] = None, force_sr: bool = False) -> Image.Image:
    use_enhanced = use_enhanced and photo.auto      # ретушь только там, где её выбрал пользователь
    src = photo.ensure_enh().enh if use_enhanced else photo.img
    W, H = src.size
    x, y, w, h = crop
    box = (int(round(x * W)), int(round(y * H)), int(round((x + w) * W)), int(round((y + h) * H)))
    reg = np.asarray(src.crop(box).convert("RGB"))
    rh, rw = reg.shape[:2]
    up = out_w / max(rw, 1)
    # ИИ-апскейл, когда слоту не хватает пикселей (или принудительно для сравнения)
    if (use_enhanced and sr is not None and sr.available and (force_sr or up > 1.15)
            and rw * rh <= SR_MAX_PIXELS):
        big = sr.upscale(reg, key=(photo.id, id(photo), box))
        if big is not None:
            interp = cv2.INTER_AREA if big.shape[1] >= out_w else cv2.INTER_LANCZOS4
            reg = cv2.resize(big, (out_w, out_h), interpolation=interp)
            return Image.fromarray(adaptive_sharpen(reg, 0.12))
    interp = cv2.INTER_AREA if up < 1 else cv2.INTER_LANCZOS4
    reg = cv2.resize(reg, (out_w, out_h), interpolation=interp)
    if use_enhanced:
        deficit = 1 - photo.q["sharp_s"]
        amount = 0.30 + 0.85 * deficit + (0.25 if up > 1.15 else 0)
        reg = adaptive_sharpen(reg, min(amount, 1.4))
    return Image.fromarray(reg)


def _plus(d: ImageDraw.ImageDraw, cx, cy, r):
    d.ellipse([cx - r, cy - r, cx + r, cy + r], outline=(140, 140, 140), width=max(2, r // 9))
    k = r * 0.55
    d.line([(cx - k, cy), (cx + k, cy)], fill=(140, 140, 140), width=max(2, r // 9))
    d.line([(cx, cy - k), (cx, cy + k)], fill=(140, 140, 140), width=max(2, r // 9))


def _fit_text(d, text, font_fn, max_w, start, min_size=10, **kw):
    size = start
    while size > min_size:
        f = font_fn(size, **kw)
        if d.textlength(text, font=f) <= max_w:
            return f
        size -= 2
    return font_fn(min_size, **kw)


def _theme_font_fn(theme: dict):
    """(функция шрифта, доп. аргументы) для заголовков темы."""
    k = theme.get("title_font", "serif")
    if k == "sans_bold":
        return sans, {"bold": True}
    if k == "serif_italic":
        return serif, {"italic": True}
    return serif, {}


def render_spread(spread: dict, photos: dict, width: int = 1100, grid: bool = False,
                  badges: bool = True, use_enhanced: bool = True, plain: bool = False,
                  sr: Optional[Upscaler] = None) -> Image.Image:
    """plain=True: без бейджей/плюсов (для печати/экспорта). sr: ИИ-апскейл слотов с малым dpi."""
    H = int(width * SPREAD_CM[1] / SPREAD_CM[0])
    pad = 0 if plain else int(width * 0.02)
    canvas = Image.new("RGB", (width + 2 * pad, H + 2 * pad), (255, 255, 255) if plain else (236, 236, 236))
    theme = _contrast_theme(get_theme(spread.get("theme")), spread.get("bg"))
    seed = int(spread.get("decor_seed", 0))
    spr = Image.new("RGB", (width, H), tuple(spread["bg"]) if spread.get("bg") else theme["bg"])
    draw_back(spr, theme if not spread.get("bg") else dict(theme, decor=theme.get("decor") if theme.get("decor") in ("birthday", "wedding", "travel", "newyear", "halloween", "botanic", "goldinset") else None), seed)
    d = ImageDraw.Draw(spr)
    t = TEMPLATES[spread["template"]]
    fw = int(round(width * theme["frame_w"])) if theme.get("frame") else 0
    if fw:
        fw = max(2, fw)
    for j, (sx, sy, sw, sh) in enumerate(t["slots"]):
        x0, y0 = int(round(sx * width)), int(round(sy * H))
        x1, y1 = int(round((sx + sw) * width)), int(round((sy + sh) * H))
        s = spread["slots"][j]
        if s["photo"] and s["photo"] in photos:
            ph = photos[s["photo"]]
            spr.paste(render_slot_image(ph, s["crop"], x1 - x0, y1 - y0, use_enhanced, sr=sr), (x0, y0))
            if fw and not ((x1 - x0) >= width * 0.98 and (y1 - y0) >= H * 0.98):
                d.rectangle([x0, y0, x1 - 1, y1 - 1], outline=theme["frame"], width=fw)
            if grid and not plain:
                ov = Image.new("RGBA", spr.size, (0, 0, 0, 0))
                od = ImageDraw.Draw(ov)
                for i in (1, 2):
                    od.line([(x0 + (x1 - x0) * i // 3, y0), (x0 + (x1 - x0) * i // 3, y1)], fill=(255, 255, 255, 190), width=1)
                    od.line([(x0, y0 + (y1 - y0) * i // 3), (x1, y0 + (y1 - y0) * i // 3)], fill=(255, 255, 255, 190), width=1)
                spr.paste(Image.alpha_composite(spr.convert("RGBA"), ov).convert("RGB"))
                d = ImageDraw.Draw(spr)
            if badges and not plain:
                grade, _, dpi = slot_quality(ph, s["crop"], slot_width_cm(spread["template"], j))
                col = {"good": GOOD, "mid": MID, "bad": BAD}[grade]
                r = max(7, width // 90)
                d.ellipse([x1 - 3 * r, y0 + r, x1 - r, y0 + 3 * r], fill=col, outline=(255, 255, 255), width=2)
        elif not plain:
            d.rectangle([x0, y0, x1 - 1, y1 - 1], fill=GRAY)
            _plus(d, (x0 + x1) // 2, (y0 + y1) // 2, max(14, min(x1 - x0, y1 - y0) // 9))
    for tx in t["texts"]:
        rx, ry, rw, rh = tx["rect"]
        bx0, by0 = int(rx * width), int(ry * H)
        bw, bh = int(rw * width), int(rh * H)
        ffn, fkw = _theme_font_fn(theme)
        if tx["role"] == "title":
            title = spread["texts"].get("title", "")
            sub = spread["texts"].get("subtitle", "")
            if title:
                f = _fit_text(d, title, ffn, bw - 24, int(bh * 0.2), **fkw)
                d.text((bx0 + 14, by0 + 8), title, font=f, fill=theme["accent"])
            if sub:
                f2 = serif(max(10, int(bh * 0.045)), italic=True)
                tw = d.textlength(sub, font=f2)
                d.text((bx0 + (bw - tw) / 2, by0 + bh * 0.5), sub, font=f2, fill=theme["sub"])
        elif tx["role"] == "quote":
            q = spread["texts"].get("quote", "") or spread["texts"].get("title", "")
            if q:
                f = _fit_text(d, q, ffn, bw - 20, int(bh * 0.18), **fkw)
                tw = d.textlength(q, font=f)
                d.text((bx0 + (bw - tw) / 2, by0 + bh * 0.4), q, font=f, fill=theme["accent"])
    draw_front(spr, theme, seed)
    # корешок
    sp_ov = Image.new("RGBA", spr.size, (0, 0, 0, 0))
    od = ImageDraw.Draw(sp_ov)
    for k in range(10):
        a = int(34 * (1 - k / 10))
        od.line([(width // 2 - k, 0), (width // 2 - k, H)], fill=(0, 0, 0, a))
        od.line([(width // 2 + k, 0), (width // 2 + k, H)], fill=(0, 0, 0, a))
    spr = Image.alpha_composite(spr.convert("RGBA"), sp_ov).convert("RGB")
    canvas.paste(spr, (pad, pad))
    return canvas


def _contrast_theme(theme: dict, bg) -> dict:
    """Если пользователь выбрал свой цвет фона, подбираем читаемый цвет текста и рамок."""
    if not bg:
        return theme
    lum = 0.299 * bg[0] + 0.587 * bg[1] + 0.114 * bg[2]
    if lum < 125:
        return dict(theme, accent=(246, 240, 228), sub=(214, 208, 196), frame=(246, 240, 228))
    if sum(theme["accent"]) > 560:
        return dict(theme, accent=(60, 56, 64), sub=(110, 104, 116))
    return theme


def _cover_panel(d, W, H, theme):
    """Лицевая сторона обложки: тонкая двойная рамка и орнамент-разделитель над заголовком."""
    col = tuple(theme["accent"])
    lw = max(1, int(W * 0.0016))
    x0, x1 = W * 0.535, W * 0.965
    y0, y1 = H * 0.06, H * 0.94
    d.rectangle([x0, y0, x1, y1], outline=col, width=lw)
    m = W * 0.008
    d.rectangle([x0 + m, y0 + m, x1 - m, y1 - m], outline=tuple(theme["sub"]), width=lw)
    cx, cy, r = (x0 + x1) / 2 + W * 0.0, H * 0.66, W * 0.007
    d.line([(x0 + W * 0.05, cy), (cx - r * 3, cy)], fill=col, width=lw)
    d.line([(cx + r * 3, cy), (x1 - W * 0.05, cy)], fill=col, width=lw)
    d.polygon([(cx, cy - r * 1.6), (cx + r * 1.6, cy), (cx, cy + r * 1.6), (cx - r * 1.6, cy)], fill=col)


def render_cover(book: dict, width: int = 1100, plain: bool = False) -> Image.Image:
    H = int(width * SPREAD_CM[1] / SPREAD_CM[0])
    pad = int(width * 0.02)
    canvas = Image.new("RGB", (width + 2 * pad, H + 2 * pad), (236, 236, 236))
    theme = _contrast_theme(get_theme(book.get("theme")), book.get("cover_bg"))
    seed = int(book.get("decor_seed", 0))
    cbg = tuple(book["cover_bg"]) if book.get("cover_bg") else theme["cover_bg"]
    spr = Image.new("RGB", (width, H), cbg)
    draw_back(spr, theme if not book.get("cover_bg") else dict(theme, decor=theme.get("decor") if theme.get("decor") in ("birthday", "wedding", "travel", "newyear", "halloween", "botanic", "goldinset") else None), seed)
    draw_front(spr, theme, seed)
    d = ImageDraw.Draw(spr)
    if theme.get("cover_panel"):
        _cover_panel(d, width, H, theme)
    title = book["title"]
    ffn, fkw = _theme_font_fn(theme)
    f = _fit_text(d, title, ffn, int(width * 0.42), int(H * 0.085), **fkw)
    tw = d.textlength(title, font=f)
    d.text((width * 0.55, H * 0.74), title, font=f, fill=theme["accent"])
    sub = book.get("subtitle", "")
    if sub:
        f2 = serif(max(10, int(H * 0.03)), italic=True)
        d.text((width * 0.55, H * 0.74 + f.size * 1.4), sub, font=f2, fill=theme["sub"])
    # корешок с вертикальным текстом
    sf = serif(max(9, int(H * 0.028)))
    tmp = Image.new("RGBA", (int(d.textlength(title, font=sf)) + 8, sf.size + 8), (0, 0, 0, 0))
    ImageDraw.Draw(tmp).text((4, 2), title, font=sf, fill=tuple(theme["accent"]) + (255,))
    tmp = tmp.rotate(-90, expand=True)
    spr.paste(tmp, (width // 2 - tmp.width // 2 + 2, int(H * 0.06)), tmp)
    ov = Image.new("RGBA", spr.size, (0, 0, 0, 0))
    od = ImageDraw.Draw(ov)
    for k in range(10):
        a = int(30 * (1 - k / 10))
        od.line([(width // 2 - k, 0), (width // 2 - k, H)], fill=(0, 0, 0, a))
        od.line([(width // 2 + k, 0), (width // 2 + k, H)], fill=(0, 0, 0, a))
    spr = Image.alpha_composite(spr.convert("RGBA"), ov).convert("RGB")
    if plain:
        return spr
    canvas.paste(spr, (pad, pad))
    return canvas


def export_pdf(book: dict, photos: dict, dpi: int = 200, include_cover: bool = True,
               sr: Optional[Upscaler] = None) -> bytes:
    width = int(SPREAD_CM[0] / 2.54 * dpi)
    pages = []
    if include_cover:
        c = render_cover(book, width)
        pages.append(c.crop((int(width * 0.02), int(width * 0.02), int(width * 1.02), int(width * 0.02) + int(width * SPREAD_CM[1] / SPREAD_CM[0]))))
    for sp in book["spreads"]:
        pages.append(render_spread(sp, photos, width=width, plain=True, sr=sr))
    buf = io.BytesIO()
    pages[0].save(buf, format="PDF", save_all=True, append_images=pages[1:], resolution=float(dpi))
    return buf.getvalue()


# ---------------------------------------------------------------------------
# ДЕМО-ДАННЫЕ (на случай если нет под рукой фото)
# ---------------------------------------------------------------------------
def make_demo_photos(max_n: int = 10):
    """Набор демо-кадров из skimage.data с намеренными дефектами."""
    from skimage import data
    items = []
    def add(name, arr):
        im = Image.fromarray(arr)
        b = io.BytesIO(); im.save(b, "JPEG", quality=92)
        items.append((name, b.getvalue()))
    def up(a, w, h):
        return cv2.resize(a, (w, h), interpolation=cv2.INTER_CUBIC)
    try:
        ast = data.astronaut()
        big = up(ast, 1600, 1600)
        add("01_portrait.jpg", big)
        # почти копия: проверка поиска дублей
        add("02_portrait_copy.jpg", np.clip(big.astype(np.float32) * 1.03, 0, 255).astype(np.uint8))
        add("03_portrait_wide.jpg", up(ast[40:380, :, :], 1800, 1020))
    except Exception:
        pass
    try:
        ch = data.chelsea()
        add("04_cat_wide.jpg", up(ch, 1804, 1200))
        add("05_cat_portrait.jpg", up(ch[:, 100:380], 900, 1200))
        add("06_coffee.jpg", up(data.coffee(), 1800, 1200))
        # тёмный кадр
        rk = up(data.rocket(), 1800, 1200)
        add("07_rocket_dark.jpg", np.clip(rk.astype(np.float32) * 0.38, 0, 255).astype(np.uint8))
        # размытый кадр
        add("08_retina_blurry.jpg", cv2.GaussianBlur(up(data.retina(), 1400, 1400), (0, 0), 8))
        # маленькое разрешение: мало пикселей для печати
        add("09_space_lowres.jpg", cv2.resize(data.hubble_deep_field(), (420, 366), interpolation=cv2.INTER_AREA))
    except Exception:
        pass
    return items[:max_n]
