# -*- coding: utf-8 -*-
"""
Проекты клиента: каждый альбом (или набор отпечатков) хранит своё состояние и переживает перезапуск сервера.
Хранение: раскладка и метаданные в таблице projects (БД, у проекта есть владелец owner), файлы фото в data/projects/<id>/photos/<photo id>.jpg (+ .orig.jpg для изменённых ИИ).
При запуске проекты читаются «лениво»: метаданные сразу, фото (анализ лиц и качества) при первом открытии.
"""
from __future__ import annotations

import json
import os
import shutil
import threading
import time
import uuid
from datetime import datetime

from PIL import Image

import ai_core as A
import db
import orders as O

PROJ_DIR = os.path.join(O.DATA, "projects")             # устаревшее хранилище файлов: при запуске фото из него переезжают в БД
GUEST_DIR = os.path.join(O.DATA, "guest_tmp")           # временное хранилище фото гостей (кэш, живёт GUEST_TTL секунд без активности)
GUEST_TTL = float(os.environ.get("GUEST_TTL_HOURS", 24)) * 3600


class Project:
    def __init__(self, product: str, params: dict | None = None, pid: str | None = None):
        pr = O.product(product)
        self.id = pid or uuid.uuid4().hex[:8]
        self.owner = ""               # uid пользователя; чужие проекты недоступны
        self.product, self.params = pr["id"], dict(params or {})
        self.kind = pr["kind"]
        self.photos: dict = {}
        self.book = A.empty_book("МОЯ КНИГА", "") if self.kind == "book" else None
        self.history: list = []
        self.future: list = []
        self.originals: dict = {}
        self.chat: list = []
        self.qty = 1                  # экземпляров книги
        self.prints: dict = {}        # photo id -> число отпечатков
        self.manual_order = False     # пользователь сам менял порядок фото
        self.auto_enhance = False     # автоулучшение фото: по умолчанию ВЫКЛЮЧЕНО, включает пользователь
        self.share: dict = {}         # совместный альбом: token, on, members {uid: {name, joined}}
        self.in_cart = False
        self.ordered = False
        self.created = self.updated = time.time()
        self.loaded = True
        self.meta_photos: list = []   # метаданные для ленивой загрузки
        self.dirty_files: set = set()
        self.names: dict = {"product": pr["name"]}      # названия выбранных пунктов каталога: нужны, если админ потом удалит их

    @property
    def name(self) -> str:
        return (self.book or {}).get("title") or self.names.get("product") or O.product(self.product)["name"]

    def catalog_product(self):
        """Товар проекта в текущем каталоге или None, если админ его удалил."""
        return next((x for x in O.CATALOG["products"] if x["id"] == self.product), None)

    def count_photos(self) -> int:
        if not self.loaded:
            return len(self.meta_photos)
        return len(A.used_ids(self.book)) if self.book else sum(1 for p in self.photos.values() if self.prints.get(p.id, 0) > 0)

    def n_spreads(self) -> int:
        return len(self.book["spreads"]) if self.book else 0

    def unit(self) -> float:
        if self.catalog_product() is None:
            return 0.0                                  # товар удалён из каталога: цену не придумываем, пока пользователь не заменит его
        if self.kind == "book":
            return O.unit_price(self.product, self.params, self.n_spreads())
        return O.unit_price(self.product, self.params)

    def line_qty(self) -> int:
        return self.qty if self.kind == "book" else sum(self.prints.get(i, 0) for i in self.prints if i in self.photos or not self.loaded)

    def line_sum(self) -> float:
        return round(self.unit() * self.line_qty(), 2)


# ---------------------------------------------------------------------------
# ДОСТУПНОСТЬ ВЫБРАННОГО В КАТАЛОГЕ
# Если админ удалил товар, обложку, формат или бумагу, которые пользователь уже выбрал, проект не ломается: он получает
# «issue» с мягким объяснением и вариантами замены (фото и макет сохраняются).
# ---------------------------------------------------------------------------
def availability(p: "Project"):
    """None, если всё выбранное есть в каталоге; иначе dict(field, what, text, options)."""
    cat, nm = O.CATALOG, p.names
    prod = p.catalog_product()
    if prod is None:
        what = nm.get("product") or "Выбранная услуга"
        opts = [dict(id=x["id"], name=x["name"], note=f"от {x['from_price']:.2f} {cat['currency']}") for x in cat["products"] if O.on(x) and x["kind"] == p.kind]
        text = (f"«{what}» больше нет в каталоге. Ваши фото и макет сохранены: выберите, на что её заменить, и можно продолжать."
                if opts else f"«{what}» больше нет в каталоге, а подходящей замены пока нет. Проект сохранён: загляните позже.")
        return dict(field="product", what=what, text=text, options=opts)
    nm["product"] = prod["name"]
    if prod["kind"] == "book":
        cv = next((x for x in cat["covers"] if x["id"] == p.params.get("cover")), None)
        if cv:
            nm["cover"] = cv["name"]
            return None
        what = nm.get("cover") or "Выбранная обложка"
        opts = [dict(id=x["id"], name=x["name"], note=(f"{x['add']:+.2f} {cat['currency']}" if x["add"] else "без доплаты")) for x in cat["covers"] if O.on(x)]
        return dict(field="cover", what=what, text=f"Обложка «{what}» больше недоступна. Выберите другую: остальное в проекте не изменится.", options=opts)
    for field, key, lab in (("format", "formats", "Формат"), ("paper", "papers", "Бумага")):
        cur = next((x for x in prod[key] if x["id"] == p.params.get(field)), None)
        if cur:
            nm[field] = cur["name"]
            continue
        what = nm.get(field) or ("Выбранный формат" if field == "format" else "Выбранная бумага")
        opts = [dict(id=x["id"], name=x["name"], note=(f"×{x['mult']:g} к цене" if "mult" in x else "")) for x in prod[key]]
        text = (f"Формат «{what}» больше недоступен. Выберите другой: фото останутся как есть." if field == "format"
                else f"Бумага «{what}» больше недоступна. Выберите другую: фото останутся как есть.")
        return dict(field=field, what=what, text=text, options=opts)
    return None


def replace_choice(p: "Project", field: str, value: str):
    """Замена товара / обложки / формата / бумаги по выбору пользователя. Возвращает текст ошибки или ''."""
    cat = O.CATALOG
    if field == "product":
        new = next((x for x in cat["products"] if x["id"] == value and O.on(x)), None)
        if not new or new["kind"] != p.kind:
            return "Эту замену выбрать нельзя: выберите вариант из списка."
        p.product = new["id"]
        p.names["product"] = new["name"]
        if new["kind"] == "book":
            if not any(x["id"] == p.params.get("cover") for x in cat["covers"]):
                p.params["cover"] = O.first("covers")["id"]
        else:
            if not any(x["id"] == p.params.get("format") for x in new["formats"]):
                p.params["format"] = new["formats"][0]["id"]
            if not any(x["id"] == p.params.get("paper") for x in new["papers"]):
                p.params["paper"] = new["papers"][0]["id"]
        return ""
    prod = p.catalog_product()
    if not prod:
        return "Сначала замените услугу."
    if field == "cover" and prod["kind"] == "book" and any(x["id"] == value and O.on(x) for x in cat["covers"]):
        p.params["cover"] = value
        return ""
    if field in ("format", "paper") and prod["kind"] == "print" and any(x["id"] == value for x in prod["formats" if field == "format" else "papers"]):
        p.params[field] = value
        return ""
    return "Эту замену выбрать нельзя: выберите вариант из списка."


# ---------------------------------------------------------------------------
# ХРАНИЛИЩЕ ФОТО
#   вошедший пользователь  -> таблица photo_blobs в БД
#   гость (uid начинается с «c») -> временная папка GUEST_DIR/<проект>/, чистится по сроку GUEST_TTL
#   при входе гостя в аккаунт фото переносятся из кэша в БД (move_to_account)
# ---------------------------------------------------------------------------
def is_guest(owner: str) -> bool:
    return str(owner or "").startswith("c")


def _dir(pid):
    return os.path.join(PROJ_DIR, pid)


def _gdir(pid):
    return os.path.join(GUEST_DIR, pid)


def blob_put(p: Project, name: str, data: bytes):
    if is_guest(p.owner):
        os.makedirs(_gdir(p.id), exist_ok=True)
        with open(os.path.join(_gdir(p.id), name), "wb") as f:
            f.write(data)
    else:
        db.run("INSERT INTO photo_blobs (project_id, name, data, updated) VALUES (%s, %s, %s, %s) "
               "ON CONFLICT (project_id, name) DO UPDATE SET data = EXCLUDED.data, updated = EXCLUDED.updated",
               (p.id, name, data, time.time()))


def blob_get(p: Project, name: str):
    if is_guest(p.owner):
        try:
            with open(os.path.join(_gdir(p.id), name), "rb") as f:
                return f.read()
        except OSError:
            return None
    r = db.run("SELECT data FROM photo_blobs WHERE project_id = %s AND name = %s", (p.id, name), one=True)
    return bytes(r[0]) if r else None


def blob_exists(p: Project, name: str) -> bool:
    if is_guest(p.owner):
        return os.path.exists(os.path.join(_gdir(p.id), name))
    return db.run("SELECT 1 FROM photo_blobs WHERE project_id = %s AND name = %s", (p.id, name), one=True) is not None


def blob_del(p: Project, name: str):
    if is_guest(p.owner):
        try:
            os.remove(os.path.join(_gdir(p.id), name))
        except OSError:
            pass
    else:
        db.run("DELETE FROM photo_blobs WHERE project_id = %s AND name = %s", (p.id, name))


def blob_names(p: Project) -> list:
    if is_guest(p.owner):
        try:
            return os.listdir(_gdir(p.id))
        except OSError:
            return []
    return [r[0] for r in db.run("SELECT name FROM photo_blobs WHERE project_id = %s", (p.id,), many=True)]


def _jpeg_bytes(im) -> bytes:
    import io
    b = io.BytesIO()
    im.convert("RGB").save(b, "JPEG", quality=93)
    return b.getvalue()


def move_to_account(p: Project):
    """Гость вошёл в аккаунт: фото из временного хранилища (кэша) переезжают в БД. Вызывать после смены p.owner на uid аккаунта."""
    gd = _gdir(p.id)
    if not os.path.isdir(gd):
        return 0
    n = 0
    for name in os.listdir(gd):
        try:
            with open(os.path.join(gd, name), "rb") as f:
                blob_put(p, name, f.read())
            n += 1
        except OSError:
            pass
    shutil.rmtree(gd, ignore_errors=True)
    return n


def migrate_legacy(projects: dict) -> int:
    """Фото из старого хранилища data/projects/<id>/photos переносятся: вошедшие пользователи -> БД, гости -> временный кэш."""
    n = 0
    for p in projects.values():
        d = os.path.join(_dir(p.id), "photos")
        if not os.path.isdir(d):
            continue
        for name in os.listdir(d):
            try:
                with open(os.path.join(d, name), "rb") as f:
                    blob_put(p, name, f.read())
                n += 1
            except OSError:
                pass
        shutil.rmtree(_dir(p.id), ignore_errors=True)
    return n


def purge_guests(projects: dict, now: float | None = None) -> list:
    """Удаляет проекты гостей без активности дольше GUEST_TTL вместе с фото из кэша. Возвращает id удалённых проектов."""
    now = now or time.time()
    gone = []
    for p in list(projects.values()):
        if is_guest(p.owner) and not p.ordered and now - p.updated > GUEST_TTL:
            gone.append(p.id)
    for pid in gone:
        shutil.rmtree(_gdir(pid), ignore_errors=True)
        db.run("DELETE FROM projects WHERE id = %s", (pid,))
        projects.pop(pid, None)
    if os.path.isdir(GUEST_DIR):                 # осиротевшие папки
        for name in os.listdir(GUEST_DIR):
            f = os.path.join(GUEST_DIR, name)
            if name not in projects and now - os.path.getmtime(f) > GUEST_TTL:
                shutil.rmtree(f, ignore_errors=True)
    return gone


def save(p: Project):
    if p.loaded:
        have = set(blob_names(p))
        for pid, ph in p.photos.items():
            if pid in p.dirty_files or pid + ".jpg" not in have:
                blob_put(p, pid + ".jpg", _jpeg_bytes(ph.img))
        for pid, ph in p.originals.items():
            if pid + ".orig.jpg" not in have:
                blob_put(p, pid + ".orig.jpg", _jpeg_bytes(ph.img))
        p.dirty_files.clear()
        metas = [dict(id=i, name=ph.name, date=ph.date.isoformat() if ph.date else None, excluded=ph.excluded,
                      dup_of=ph.dup_of, orig_size=list(ph.orig_size), edited=i in p.originals, by=ph.by, enhance=ph.enhance,
                      orig_orig_size=list(p.originals[i].orig_size) if i in p.originals else None,
                      orig_date=(p.originals[i].date.isoformat() if i in p.originals and p.originals[i].date else None))
                 for i, ph in p.photos.items()]
    else:
        metas = p.meta_photos
    availability(p)
    data = dict(id=p.id, owner=p.owner, product=p.product, kind=p.kind, names=p.names, params=p.params, book=p.book, qty=p.qty, prints=p.prints, in_cart=p.in_cart,
                ordered=p.ordered, manual_order=p.manual_order, auto_enhance=p.auto_enhance, created=p.created, updated=p.updated, photos=metas, chat=p.chat[-40:], share=p.share)
    db.run("INSERT INTO projects (id, owner, data, updated) VALUES (%s, %s, %s::jsonb, %s) "
           "ON CONFLICT (id) DO UPDATE SET owner = EXCLUDED.owner, data = EXCLUDED.data, updated = EXCLUDED.updated",
           (p.id, p.owner, db.dumps(data), p.updated))


def delete_files(pid: str):
    shutil.rmtree(_dir(pid), ignore_errors=True)
    shutil.rmtree(_gdir(pid), ignore_errors=True)
    db.run("DELETE FROM photo_blobs WHERE project_id = %s", (pid,))
    db.run("DELETE FROM projects WHERE id = %s", (pid,))


def load_all() -> dict:
    out = {}
    for (raw,) in db.run("SELECT data FROM projects", many=True):
        try:
            d = db._j(raw)
            p = Project(d["product"], d.get("params"), pid=d["id"])
            p.product = d["product"]                      # не подменяем товаром по умолчанию, даже если админ его удалил
            p.kind = d.get("kind") or p.kind
            p.names = d.get("names") or p.names
            p.owner = d.get("owner", "")
            p.book = d.get("book")
            p.qty, p.prints = d.get("qty", 1), d.get("prints", {})
            p.in_cart, p.ordered = d.get("in_cart", False), d.get("ordered", False)
            p.manual_order = d.get("manual_order", False)
            p.auto_enhance = d.get("auto_enhance", False)
            p.created, p.updated = d.get("created", time.time()), d.get("updated", time.time())
            p.chat = d.get("chat", [])
            p.share = d.get("share") or {}
            p.meta_photos, p.loaded = d.get("photos", []), False
            out[p.id] = p
        except Exception:
            continue
    return out


def sync_enhance(p: Project):
    """Вычисляет для каждого фото, применять ли автоулучшение: личный выбор пользователя важнее настройки проекта."""
    for ph in p.photos.values():
        ph.auto = bool(ph.enhance) if ph.enhance is not None else bool(p.auto_enhance)


def ensure_loaded(p: Project, detector):
    """Строит Photo (анализ качества и лиц) из сохранённых файлов при первом открытии проекта."""
    if p.loaded:
        return
    import io

    def mk(pid, name, date, osz, suffix=""):
        raw = blob_get(p, pid + suffix + ".jpg")
        if raw is None:
            raise FileNotFoundError(pid)
        im = Image.open(io.BytesIO(raw)).convert("RGB")
        ph = A.build_photo(name, im, detector, date=datetime.fromisoformat(date) if date else None,
                           orig_size=tuple(osz) if osz else im.size)
        ph.id = pid
        return ph

    photos, origs = {}, {}
    for m in p.meta_photos:
        try:
            ph = mk(m["id"], m["name"], m.get("date"), m.get("orig_size"))
            ph.excluded, ph.dup_of, ph.enhance = m.get("excluded", False), m.get("dup_of"), m.get("enhance")
            ph.by = m.get("by", "")
            photos[ph.id] = ph
            if m.get("edited") and blob_exists(p, m["id"] + ".orig.jpg"):
                origs[ph.id] = mk(m["id"], m["name"], m.get("orig_date") or m.get("date"), m.get("orig_orig_size"), ".orig")
        except Exception:
            continue
    p.photos, p.originals, p.loaded = photos, origs, True
    sync_enhance(p)
    # книга могла ссылаться на фото, файлов которых нет: чистим такие слоты
    if p.book:
        for sp in p.book["spreads"]:
            for j, s in enumerate(sp["slots"]):
                if s["photo"] and s["photo"] not in photos:
                    sp["slots"][j] = dict(photo=None, crop=None, manual=False)
    A.mark_duplicates(list(photos.values()))
