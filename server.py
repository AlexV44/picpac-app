# -*- coding: utf-8 -*-
"""
Бэкенд мобильной PWA PicPac: тонкая обёртка над ai_core.py, assistant.py, imgedit.py, orders.py, projects.py.
Одно устройство = один клиент (client id в браузере). Проекты (альбомы) и заказы сохраняются на диск (data/).

Запуск:  python server.py            -> http://0.0.0.0:8000
Ключ:    OPENROUTER_API_KEY (переменная окружения) или поле в приложении.
"""
from __future__ import annotations

import os as _os


def _load_env():
    """Читает .env (KEY=VALUE) рядом с server.py и в текущей папке; уже заданные переменные не перезаписывает."""
    for d in {_os.path.dirname(_os.path.abspath(__file__)), _os.getcwd()}:
        fp = _os.path.join(d, ".env")
        if not _os.path.isfile(fp):
            continue
        for ln in open(fp, encoding="utf-8-sig"):
            ln = ln.strip()
            if not ln or ln.startswith("#") or "=" not in ln:
                continue
            k, v = ln.split("=", 1)
            k, v = k.replace("export ", "").strip(), v.strip().strip('"').strip("'")
            if k and k not in _os.environ:
                _os.environ[k] = v


_load_env()

import atexit
import copy
import io
import itertools
import gzip
import hashlib
import json
import os
import secrets
import re
import threading
import time
import uuid
from datetime import datetime

from functools import wraps

from flask import Flask, g, jsonify, request, send_file, send_from_directory, abort, has_request_context
from PIL import Image

import ai_core as A
import assistant as AS
import imgedit as IE
import accounts as AC
import album_templates as ATPL
import audit as AU
import db
import crm as CRM
import offers as OF
import orders as O
import catalog_api as CAT
import analytics as AN
import loyalty as LY
import projects as P
import support as SP
from themes import THEMES

HERE = os.path.dirname(os.path.abspath(__file__))
WEB = os.path.join(HERE, "web")

app = Flask(__name__, static_folder=None)
CAT.ADMIN_CHECK = lambda: who().get("role") == "admin"
CAT.AUDIT = lambda action, obj, changes: AU.log(who()["uid"], "admin", action, obj, extra=dict(changes=changes[:60]))
app.register_blueprint(CAT.mock_api)
app.config["MAX_CONTENT_LENGTH"] = 200 * 1024 * 1024
class _UserLock:
    """Блокировка на каждого пользователя: тяжёлая операция одного человека (PDF, анализ фото) не задерживает остальных.
    Владелец берётся из запроса или из потока фоновой задачи (start_job)."""

    def __init__(self):
        self._locks, self._g, self._tl = {}, threading.Lock(), threading.local()

    def _for(self, uid):
        with self._g:
            return self._locks.setdefault(uid or "", threading.RLock())

    def of(self, uid):
        return self._for(uid)

    def _uid(self):
        uid = getattr(self._tl, "uid", None)
        if uid is None and has_request_context():
            uid = who()["uid"]
        return uid or ""

    def __enter__(self):
        l = self._for(self._uid())
        l.acquire()
        self._tl.__dict__.setdefault("stack", []).append(l)
        return self

    def __exit__(self, *a):
        self._tl.stack.pop().release()


LOCK = _UserLock()


# ---------------------------------------------------------------------------
# СОСТОЯНИЕ
# ---------------------------------------------------------------------------
class State:
    """Глобальные настройки + проекты. Свойства photos/book/... указывают на ТЕКУЩИЙ проект."""

    def __init__(self):
        self.projects: dict = P.load_all()
        self.curmap: dict = {}           # uid -> id текущего проекта (у каждого пользователя свой)
        self.rev = 0
        self.cache: dict = {}
        self.api_key = os.environ.get("OPENROUTER_API_KEY", "")
        self.models = [m.strip() for m in os.environ.get("PICPAC_MODELS", "").split(",") if m.strip()] or list(AS.DEFAULT_MODELS)
        self.use_llm = True
        self.upscale = False
        self.edits: dict = {}
        self.jobs: dict = {}
        self._pending: set = set()
        self._timer = None

    @property
    def cur(self):
        """Текущий проект ВЫЗЫВАЮЩЕГО пользователя; чужие проекты недоступны."""
        if not has_request_context():
            return None
        uid = who()["uid"]
        pid = self.curmap.get(uid)
        p = self.projects.get(pid)
        return pid if uid and p and p.owner == uid else None

    @cur.setter
    def cur(self, v):
        self.curmap[who()["uid"]] = v

    @property
    def proj(self):
        return self.projects.get(self.cur)

    def mine(self):
        uid = who()["uid"]
        return [p for p in list(self.projects.values()) if uid and p.owner == uid]

    def own(self, pid):
        p = self.projects.get(pid)
        return p if p and who()["uid"] and p.owner == who()["uid"] else None

    # --- доступ к текущему проекту (старые обработчики работают без изменений)
    photos = property(lambda s: s.proj.photos)
    originals = property(lambda s: s.proj.originals)
    chat = property(lambda s: s.proj.chat, lambda s, v: setattr(s.proj, "chat", v))
    history = property(lambda s: s.proj.history, lambda s, v: setattr(s.proj, "history", v))
    future = property(lambda s: s.proj.future, lambda s, v: setattr(s.proj, "future", v))
    book = property(lambda s: s.proj.book, lambda s, v: setattr(s.proj, "book", v))

    def bump(self):
        self.rev += 1
        _trim_cache()
        self.touch()

    def touch(self, p=None):
        p = p or (self.proj if has_request_context() else None)
        if not p:
            return
        p.updated = time.time()
        self._pending.add(p.id)
        if self._timer:
            self._timer.cancel()
        self._timer = threading.Timer(1.2, self.flush)
        self._timer.daemon = True
        self._timer.start()

    def flush(self):
        ids, self._pending = list(self._pending), set()
        for i in ids:
            p = self.projects.get(i)
            if p:
                with LOCK.of(p.owner):               # пока владелец не меняет проект
                    try:
                        P.save(p)
                    except Exception as e:           # диск и БД не должны ронять сервер
                        print("не удалось сохранить проект", i, e)


db.init()
AC.ensure_demo()
ST = State()
_mig = P.migrate_legacy(ST.projects)            # фото из старого хранилища-файлов: в БД (вошедшие) или во временный кэш (гости)
if _mig:
    print(f"[photos] перенесено файлов в новое хранилище: {_mig}")
P.purge_guests(ST.projects)


def _guest_gc():
    """Раз в 30 минут удаляем проекты гостей без активности дольше 24 часов (фото из временного кэша стираются)."""
    while True:
        time.sleep(1800)
        try:
            with LOCK:
                gone = P.purge_guests(ST.projects)
                if gone:
                    ST.bump()
        except Exception as e:
            print("очистка кэша гостей:", e)


threading.Thread(target=_guest_gc, daemon=True).start()
atexit.register(ST.flush)
_det = None
_sr = None


_init_lock = threading.Lock()


def detector():
    global _det
    with _init_lock:
        return _detector_locked()


def _detector_locked():
    global _det
    if _det is None:
        _det = A.FaceDetector(download=True)
        if _det._yunet is None and _det._haar is None:
            try:
                from ultralytics import YOLO
                _det.yolo = YOLO("yolov8n.pt")
                _det.name = "YOLOv8"
            except Exception:
                pass
    return _det


def upscaler():
    global _sr
    with _init_lock:
        if _sr is None:
            _sr = A.Upscaler(download=True)
        return _sr


def active():
    return [p for p in ST.photos.values() if not p.excluded and not p.dup_of]


def snapshot():
    ST.history.append(copy.deepcopy(ST.book))
    ST.history = ST.history[-30:]
    ST.future = []


def ensure(p):
    if not p.loaded:
        P.ensure_loaded(p, detector())


# ---------------------------------------------------------------------------
# СЕРИАЛИЗАЦИЯ
# ---------------------------------------------------------------------------
PAD = 0.02


def img_rect(rect):
    sx, sy, sw, sh = rect
    H = A.SPREAD_CM[1] / A.SPREAD_CM[0]
    tw, th = 1.0 + 2 * PAD, H + 2 * PAD
    return [(PAD + sx) / tw, (PAD + sy * H) / th, sw / tw, sh * H / th]


_VER = itertools.count(1)


def _pv(p):
    """Версия изображения фото: меняется, когда меняется картинка (поворот, правка) или включено улучшение."""
    v = getattr(p, "_ver", None)
    if v is None:
        v = p._ver = next(_VER)
    return "%d%d" % (v, int(bool(p.auto)))


def _spread_v(i):
    sp = ST.book["spreads"][i]
    ids = [s["photo"] for s in sp["slots"] if s["photo"] and s["photo"] in ST.photos]
    raw = json.dumps([ST.cur, sp, [_pv(ST.photos[x]) for x in ids]], sort_keys=True, default=str)
    return hashlib.md5(raw.encode()).hexdigest()[:10]


def _cover_v():
    raw = json.dumps([ST.cur] + [ST.book.get(k) for k in ("title", "subtitle", "theme", "decor_seed", "cover_bg")], default=str)
    return hashlib.md5(raw.encode()).hexdigest()[:10]


def _trim_cache():
    if len(ST.cache) > 300:
        for k in list(ST.cache)[:120]:
            ST.cache.pop(k, None)


def photo_json(p, used):
    pr = ST.proj
    qty = pr.prints.get(p.id, 0) if pr and pr.kind == "print" else None
    return dict(id=p.id, name=p.name, score=p.q["score"], grade=p.q["grade"], issues=p.q["issues"],
                message=p.q["message"], w=p.orig_size[0], h=p.orig_size[1], excluded=p.excluded,
                dup=bool(p.dup_of), used=(p.id in used) if qty is None else qty > 0, edited=p.id in ST.originals,
                faces=len(p.faces), ops=(p.ensure_enh().ops if p.auto else []), qty=qty, enhance=p.enhance, auto=p.auto, v=_pv(p),
                date=p.date.strftime("%d.%m.%Y") if p.date else None, by=p.by,
                day=p.date.strftime("%Y-%m-%d") if p.date else "", ts=int(p.date.timestamp()) if p.date else 0)


def spread_json(i, sp):
    slots = []
    for j, s in enumerate(sp["slots"]):
        d = dict(photo=s["photo"], crop=list(s["crop"]) if s["crop"] else None, manual=s.get("manual", False))
        if s["photo"] and s["photo"] in ST.photos:
            g, msg, dpi = A.slot_quality(ST.photos[s["photo"]], s["crop"], A.slot_width_cm(sp["template"], j))
            d.update(grade=g, msg=msg, dpi=round(dpi), aspect=A.slot_aspect(sp["template"], j))
        else:
            d.update(aspect=A.slot_aspect(sp["template"], j))
        slots.append(d)
    t = A.TEMPLATES[sp["template"]]
    return dict(index=i, v=_spread_v(i), bg=sp.get("bg"), template=sp["template"], theme=sp.get("theme", "classic"), texts=sp["texts"],
                slots=slots, rects=[img_rect(r) for r in t["slots"]], text_roles=[x["role"] for x in t["texts"]])


def project_json(p):
    pr_ = p.catalog_product()
    return dict(id=p.id, product=p.product, product_name=p.names.get("product") or (pr_ or {}).get("name", ""), kind=p.kind, name=p.name,
                issue=P.availability(p),
                params=p.params, photos=p.count_photos(), spreads=p.n_spreads(), qty=p.qty, line_qty=p.line_qty(),
                unit=p.unit(), sum=p.line_sum(), in_cart=p.in_cart, ordered=p.ordered, updated=p.updated,
                color=(pr_ or {}).get("color", "#b7a8a0"), share=share_json(p))


def share_json(p):
    sh = p.share or {}
    if not sh.get("token"):
        return dict(on=False, token="", members=[])
    return dict(on=bool(sh.get("on")), token=sh["token"],
                members=[dict(name=m["name"], n=len(m.get("ids", []))) for m in sh.get("members", {}).values()])


def state_json():
    pr = ST.proj
    mine = ST.mine()
    cart = [p for p in mine if p.in_cart and not p.ordered]
    d = dict(
        me=who(), rev=ST.rev, cover_v=(_cover_v() if (pr and pr.loaded and pr.book) else ""), cur=ST.cur, proj=project_json(pr) if pr else None, auto_enhance=bool(pr.auto_enhance) if pr else False,
        title="", subtitle="", theme="classic", spreads=[], photos=[], can_undo=False, can_redo=False, chat=[],
        projects=[project_json(p) for p in sorted(mine, key=lambda x: -x.updated)],
        cart=dict(ids=[p.id for p in cart], count=sum(p.line_qty() for p in cart), items=round(sum(p.line_sum() for p in cart), 2)),
        templates=[dict(id=t["id"], name=t["name"], n=len(t["slots"]), rects=t["slots"]) for t in A.TEMPLATES.values()],
        themes=[dict(id=k, name=v["name"], emoji=v["emoji"], accent="#%02x%02x%02x" % tuple(v["accent"]),
                     bg="#%02x%02x%02x" % tuple(v["bg"])) for k, v in THEMES.items()],
        settings=dict(upscale=ST.upscale), album_templates=ATPL.listing(),
        catalog_v=O.CATALOG.get("_version", ""), statuses=[dict(id=x["id"], title=x["title"]) for x in O.STATUSES],
        thresholds=dict(target_dpi=A.TARGET_DPI, min_dpi=A.MIN_OK_DPI),
    )
    if pr and pr.loaded:
        used = A.used_ids(pr.book) if pr.book else set()
        ps = list(pr.photos.values())          # порядок задаёт пользователь (загрузка или перемещение)
        d["photos"] = [photo_json(p, used) for p in ps]
        d["chat"] = pr.chat[-40:]
        d["can_undo"], d["can_redo"] = bool(pr.history), bool(pr.future)
        if pr.book:
            d.update(cover_bg=pr.book.get("cover_bg"), title=pr.book["title"], subtitle=pr.book.get("subtitle", ""), theme=pr.book.get("theme", "classic"),
                     spreads=[spread_json(i, sp) for i, sp in enumerate(pr.book["spreads"])])
    return d


def ok(**extra):
    d = state_json()
    d.update(extra)
    return jsonify(d)


def err(msg, code=400):
    return jsonify(error=msg), code


# Пути, которым нужен текущий проект (и книга для части из них)
NEEDS_PROJECT = ("/api/upload", "/api/demo", "/api/photo/", "/api/spread", "/api/cover", "/api/loupe", "/api/pdf", "/api/slot",
                 "/api/crop", "/api/template", "/api/theme", "/api/text", "/api/ai", "/api/undo", "/api/redo", "/api/chat",
                 "/api/edit", "/api/print", "/api/reset", "/api/sr", "/api/photos")
NEEDS_BOOK = ("/api/spread", "/api/cover", "/api/loupe", "/api/pdf", "/api/slot", "/api/crop", "/api/template", "/api/theme",
              "/api/text", "/api/ai", "/api/undo", "/api/redo")


@app.before_request
def _catalog_sync():
    CAT.sync(O.CATALOG)          # цены берутся из каталога-источника; по истечении TTL подтягиваются изменения без перезапуска


@app.before_request
def _guard():
    path = request.path
    if not path.startswith("/api/") or path == "/api/sr":
        return None
    if any(path.startswith(x) for x in NEEDS_PROJECT):
        if not who()["uid"]:
            return err("Войдите или продолжите как гость", 401)
        if not ST.proj:
            return err("Сначала выберите услугу на главной", 409)
        if any(path.startswith(x) for x in NEEDS_BOOK) and not ST.proj.book:
            return err("Это действие доступно только для фотокниг", 409)
        if not ST.proj.loaded:
            with LOCK:
                ensure(ST.proj)
    return None


# ---------------------------------------------------------------------------
# ЛИЧНОСТЬ, РОЛИ, ЖУРНАЛ (ОТ-02, ОТ-04)
# ---------------------------------------------------------------------------
def who() -> dict:
    """Текущий пользователь: по токену сессии; иначе гость по client id из запроса; иначе аноним."""
    u = getattr(g, "_who", None)
    if u is not None:
        return u
    h = request.headers.get("Authorization", "")
    tok = h[7:] if h.startswith("Bearer ") else ""
    if not tok and request.method in ("GET", "HEAD"):
        tok = request.cookies.get("pp_session", "")          # картинки <img> не умеют слать заголовки; для изменяющих запросов cookie не принимается (CSRF)
    s = AC.by_token(tok)
    if s:
        u = dict(uid=s["uid"], role=s["role"], name=s["name"], guest=s.get("guest", False), email=s.get("email", ""))
    else:
        cid = request.headers.get("X-Client") or request.args.get("client", "")
        u = dict(uid=cid, role="client", name="Гость", guest=True) if cid else dict(uid="", role="anon", name="", guest=True)
    g._who = u
    return u


def need(role):
    """Серверная проверка роли: закрытые функции недоступны, даже если кнопку «подсмотреть» в коде страницы."""
    def deco(fn):
        @wraps(fn)
        def wrap(*a, **k):
            u = who()
            if u["role"] != role:
                AU.log(u["uid"], u["role"], "доступ запрещён: " + request.path, result="403")
                return err("Недостаточно прав для этого действия", 403)
            return fn(*a, **k)
        return wrap
    return deco


@app.after_request
def _gzip(resp):
    try:
        if resp.status_code != 200 or "Content-Encoding" in resp.headers or resp.mimetype == "image/jpeg":
            return resp
        ct = resp.mimetype or ""
        if not (ct.startswith("text/") or ct in ("application/json", "application/javascript", "image/svg+xml")):
            return resp
        if "gzip" not in request.headers.get("Accept-Encoding", ""):
            return resp
        resp.direct_passthrough = False
        data = resp.get_data()
        if len(data) < 800:
            return resp
        resp.set_data(gzip.compress(data, 5))
        resp.headers["Content-Encoding"] = "gzip"
        resp.headers["Vary"] = "Accept-Encoding"
        resp.headers["Content-Length"] = str(len(resp.get_data()))
    except Exception:
        pass
    return resp


SENSITIVE = {"phone", "api_key", "password", "address", "name", "comment", "text"}


@app.after_request
def _audit(resp):
    if request.method == "POST" and request.path.startswith("/api/") and not request.path.startswith("/api/job"):
        u = who()
        obj = ""
        try:
            j = request.get_json(silent=True) or {}
            if isinstance(j, dict):
                obj = str(j.get("id") or j.get("photo") or j.get("code") or j.get("product") or j.get("order") or j.get("email") or "")[:60]
        except Exception:
            pass
        AU.log(u["uid"], u["role"], request.path, obj, str(resp.status_code))
    return resp


# ---------------------------------------------------------------------------
# СТАТИКА / PWA
# ---------------------------------------------------------------------------
@app.get("/")
def index():
    return send_from_directory(WEB, "index.html", max_age=0)


@app.get("/track/<code>")
@app.get("/join/<tok>")
@app.get("/p/<pid>")
@app.get("/catalog")
@app.get("/catalog/<sid>")
@app.get("/orders")
@app.get("/cart")
def track_page(code="", tok="", pid="", sid=""):
    return send_from_directory(WEB, "index.html", max_age=0)


@app.get("/<path:path>")
def static_files(path):
    if path.startswith("api/"):
        abort(404)
    r = send_from_directory(WEB, path, max_age=0)
    if path == "sw.js":
        r.headers["Service-Worker-Allowed"] = "/"
    return r


# ---------------------------------------------------------------------------
# API: СОСТОЯНИЕ, ПРОЕКТЫ, КОРЗИНА
# ---------------------------------------------------------------------------
@app.get("/api/state")
def api_state():
    with LOCK:
        return ok()


@app.post("/api/project/new")
def api_project_new():
    j = request.get_json(force=True)
    prod = O.product(j.get("product") or next((p["id"] for p in O.CATALOG["products"] if O.on(p) and p["kind"] == "book"), O.first("products")["id"]))
    if j.get("product") and (prod["id"] != j.get("product") or not O.on(prod)):
        return err("Эта услуга сейчас недоступна. Выберите другую.")
    params = {}
    if prod["kind"] == "book":
        params["cover"] = (j.get("params") or {}).get("cover")
        if params["cover"] not in [c["id"] for c in O.CATALOG["covers"] if O.on(c)]:
            params["cover"] = O.first("covers")["id"]
    else:
        pp = j.get("params", {})
        params["format"] = pp.get("format") if pp.get("format") in [f["id"] for f in prod["formats"]] else prod["formats"][0]["id"]
        params["paper"] = pp.get("paper") if pp.get("paper") in [x["id"] for x in prod["papers"]] else prod["papers"][0]["id"]
    with LOCK:
        if not who()["uid"]:
            return err("Войдите или продолжите как гость", 401)
        p = P.Project(prod["id"], params)
        AN.track(who()["uid"], "project")
        if prod["kind"] == "book":
            tid = str(j.get("template") or "")
            if tid in ATPL.BY_ID:                                   # по шаблону: готовые стиль, раскладка и тексты
                p.book = ATPL.build(tid)
                p.params["mode"], p.params["tpl"] = "template", tid
            else:                                                    # вручную: чистый альбом, всё настраивает пользователь
                p.book = ATPL.manual_book()
                p.params["mode"] = "manual"
        p.owner = who()["uid"]
        ST.projects[p.id] = p
        ST.cur = p.id
        ST.bump()
        ST.flush()
        return ok()


@app.post("/api/project/open")
def api_project_open():
    j = request.get_json(force=True)
    with LOCK:
        p = ST.own(j.get("id"))
        if not p:
            return err("Проект не найден", 404)
        ensure(p)
        ST.cur = p.id
        ST.cache.clear()
        ST.rev += 1
        return ok()


@app.post("/api/project/close")
def api_project_close():
    with LOCK:
        ST.flush()
        ST.cur = None
        return ok()


@app.post("/api/project/delete")
def api_project_delete():
    j = request.get_json(force=True)
    with LOCK:
        p = ST.own(j.get("id"))
        if p:
            ST.projects.pop(p.id, None)
            P.delete_files(p.id)
            if ST.cur == p.id:
                ST.cur = None
        ST.rev += 1
        return ok()


@app.post("/api/project/auto_enhance")
def api_project_auto_enhance():
    """Переключатель «автоматически улучшать фото» для проекта (по умолчанию выключен)."""
    j = request.get_json(force=True)
    with LOCK:
        pr = ST.proj
        pr.auto_enhance = bool(j.get("on"))
        P.sync_enhance(pr)
        ST.bump()
        return ok()


@app.post("/api/photo/<pid>/enhance")
def api_photo_enhance(pid):
    """Личный выбор для одного фото: on=true улучшить, on=false не трогать, on=null как в проекте."""
    j = request.get_json(force=True)
    with LOCK:
        pr = ST.proj
        ph = pr.photos.get(pid)
        if not ph:
            return err("Фото не найдено", 404)
        v = j.get("on")
        ph.enhance = None if v is None else bool(v)
        pr.dirty_files.add(pid)
        P.sync_enhance(pr)
        ST.bump()
        return ok()


@app.post("/api/project/params")
def api_project_params():
    j = request.get_json(force=True)
    with LOCK:
        pr = ST.proj
        if not pr:
            return err("Нет проекта", 409)
        for k, v in (j.get("params") or {}).items():
            if k in ("cover", "format", "paper"):
                bad = P.replace_choice(pr, k, str(v))
                if bad:
                    return err(bad)
        ST.touch()
        return ok()


@app.post("/api/project/replace")
def api_project_replace():
    """Пользователь заменяет то, что админ убрал из каталога: услугу, обложку, формат или бумагу. Фото и макет не теряются."""
    j = request.get_json(force=True)
    with LOCK:
        p = ST.own(j.get("id"))
        if not p:
            return err("Проект не найден", 404)
        field = str(j.get("field", ""))
        if field not in ("product", "cover", "format", "paper"):
            return err("Нечего заменять")
        bad = P.replace_choice(p, field, str(j.get("value", "")))
        if bad:
            return err(bad)
        P.availability(p)
        ST.touch(p)
        ST.flush()
        return ok(name=p.names.get(field) or p.names.get("product") or "")


@app.get("/api/project/<pid>/thumb.jpg")
def api_project_thumb(pid):
    w = min(int(request.args.get("w", 360)), 900)
    with LOCK:
        p = ST.own(pid)
        if not p:
            abort(404)
        key = ("thumb", pid, w, json.dumps({k: (p.book or {}).get(k) for k in ("title", "subtitle", "theme", "decor_seed")}), p.updated if p.kind == "print" else 0)
        if key not in ST.cache:
            if p.kind == "book":
                ST.cache[key] = _jpeg(A.render_cover(p.book, w))
            else:
                first = next(iter(p.photos.values()), None)
                if first:
                    im = first.img.copy()
                else:
                    metas = p.meta_photos
                    raw = P.blob_get(p, (metas[0]["id"] if metas else "x") + ".jpg")
                    if raw is None:
                        abort(404)
                    im = Image.open(io.BytesIO(raw)).convert("RGB")
                im.thumbnail((w, w))
                ST.cache[key] = _jpeg(im, 82)
        data = ST.cache[key]
    r = send_file(io.BytesIO(data), mimetype="image/jpeg")
    r.headers["Cache-Control"] = "no-store"
    return r


@app.post("/api/cart/add")
def api_cart_add():
    j = request.get_json(force=True)
    with LOCK:
        p = ST.own(j.get("id"))
        if not p:
            return err("Проект не найден", 404)
        if P.availability(p):
            return err("Сначала замените то, что больше недоступно в каталоге: фото и макет сохранятся.")
        if p.line_qty() <= 0 and p.kind == "print":
            return err("Выберите хотя бы одно фото для печати")
        p.in_cart, p.ordered = True, False
        AN.track(who()["uid"], "cart")
        ST.touch(p)
        ST.flush()
        return ok()


@app.post("/api/cart/qty")
def api_cart_qty():
    j = request.get_json(force=True)
    with LOCK:
        p = ST.own(j.get("id"))
        if not p:
            return err("Проект не найден", 404)
        q = max(0, min(99, int(j.get("qty", 1))))
        if q == 0:
            p.in_cart = False
        else:
            p.qty = q
        ST.touch(p)
        return ok()


@app.post("/api/print/qty")
def api_print_qty():
    j = request.get_json(force=True)
    with LOCK:
        pr = ST.proj
        if pr.kind != "print":
            return err("Только для печати фото", 409)
        q = max(0, min(99, int(j.get("qty", 1))))
        ids = list(pr.photos) if j.get("all") else [j.get("photo")]
        for i in ids:
            if i in pr.photos:
                pr.prints[i] = q
        ST.bump()
        return ok()


# ---------------------------------------------------------------------------
# API: ФОТО
# ---------------------------------------------------------------------------
def _after_photos_change(pr):
    P.sync_enhance(pr)
    A.mark_duplicates(list(pr.photos.values()))
    if pr.kind == "print":
        for ph in pr.photos.values():
            ph.dup_of = None


def check_upload(f, data: bytes):
    """МП-02: только JPEG/PNG, размер и число файлов ограничены, сообщения понятны человеку."""
    lim = O.CATALOG["limits"]
    if len(data) > lim["max_file_mb"] * 1024 * 1024:
        return f"«{f.filename}»: файл больше {lim['max_file_mb']} МБ. Уменьшите его или выберите другой."
    try:
        im = Image.open(io.BytesIO(data))
        fmt = (im.format or "").upper()
    except Exception:
        return f"«{f.filename}»: не удалось открыть. Поддерживаются JPEG и PNG."
    if fmt not in ("JPEG", "PNG"):
        return f"«{f.filename}»: формат {fmt or 'неизвестный'} не поддерживается. Сохраните снимок как JPEG или PNG."
    if max(im.size) > 12000:
        return f"«{f.filename}»: слишком большое изображение ({im.size[0]}×{im.size[1]}). Максимум 12000 px по стороне."
    if min(im.size) < 200:
        return f"«{f.filename}»: слишком маленькое ({im.size[0]}×{im.size[1]} px) для печати. Нужно хотя бы 200 px по короткой стороне."
    return ""


@app.post("/api/upload")
def api_upload():
    files = request.files.getlist("files")
    if not files:
        return err("Файлы не выбраны. Выберите снимки в галерее.")
    added, failed, bad = 0, [], []
    with LOCK:
        pr = ST.proj
        mts = request.form.getlist("mt")
        for fi, f in enumerate(files):
            if len(pr.photos) >= O.CATALOG["limits"]["max_photos"]:
                failed.append(f"«{f.filename}»: достигнут лимит {O.CATALOG['limits']['max_photos']} фото в проекте.")
                continue
            data = f.read()
            msg = check_upload(f, data)
            if msg:
                failed.append(msg)
                continue
            try:
                ph = A.load_photo(f.filename or "photo.jpg", data, detector())
                if ph.date is None:                       # нет даты съёмки в файле: берём дату файла с телефона, иначе время загрузки
                    try:
                        ms = float(mts[fi]) if fi < len(mts) else 0
                        ph.date = datetime.fromtimestamp(ms / 1000) if 946684800000 < ms < time.time() * 1000 + 86400000 else datetime.now()
                    except Exception:
                        ph.date = datetime.now()
                pr.photos[ph.id] = ph
                if pr.kind == "print":
                    pr.prints[ph.id] = 1
                added += 1
                if ph.q["grade"] == "bad":
                    bad.append(dict(id=ph.id, name=ph.name, message=ph.q["message"]))
            except Exception as e:
                failed.append(f"«{f.filename}»: не удалось обработать ({e}).")
        _after_photos_change(pr)
        ST.bump()
        if added:
            AN.track(who()["uid"], "photos")
        return ok(added=added, failed=failed, bad=[b for b in bad if b["id"] in pr.photos])


@app.post("/api/demo")
def api_demo():
    with LOCK:
        pr = ST.proj
        AN.track(who()["uid"], "photos")
        for name, data in A.make_demo_photos():
            ph = A.load_photo(name, data, detector())
            if ph.date is None:
                ph.date = datetime.now()
            pr.photos[ph.id] = ph
            if pr.kind == "print":
                pr.prints[ph.id] = 1
        _after_photos_change(pr)
        ST.bump()
        return ok()


@app.post("/api/reset")
def api_reset():
    with LOCK:
        pr = ST.proj
        pr.photos.clear()
        pr.originals.clear()
        pr.prints.clear()
        if pr.kind == "book":
            pr.book = A.empty_book("МОЯ КНИГА", "")
        pr.history, pr.future, pr.chat = [], [], []
        for k in [k for k, e in ST.edits.items() if e.get("project") == pr.id]:
            ST.edits.pop(k, None)
        ST.bump()
        return ok()


def _jpeg(im, q=84):
    b = io.BytesIO()
    im.save(b, "JPEG", quality=q)
    return b.getvalue()


@app.get("/api/photo/<pid>/img")
def api_photo_img(pid):
    w = min(int(request.args.get("w", 600)), 2400)
    with LOCK:
        p = ST.photos.get(pid)
        if not p:
            abort(404)
        enh = request.args.get("enh") == "1" and p.auto
        key = ("img", pid, _pv(p), w, enh)
        if key not in ST.cache:
            im = (p.ensure_enh().enh if enh else p.img).copy()
            im.thumbnail((w, w))
            ST.cache[key] = _jpeg(im, 82)
        data = ST.cache[key]
    r = send_file(io.BytesIO(data), mimetype="image/jpeg")
    r.headers["Cache-Control"] = "private, max-age=3600"
    return r


@app.post("/api/photos/move")
def api_photo_move():
    """Изменение порядка фото: {id, dir: -1|1} или {id, to: индекс}. Порядок сохраняется вместе с проектом (МП-03)."""
    j = request.get_json(force=True)
    with LOCK:
        pr = ST.proj
        ids = list(pr.photos)
        pid = j.get("id")
        if pid not in ids:
            return err("Фото не найдено", 404)
        i = ids.index(pid)
        k = int(j["to"]) if "to" in j else i + int(j.get("dir", 0))
        k = max(0, min(len(ids) - 1, k))
        ids.insert(k, ids.pop(i))
        pr.photos = {x: pr.photos[x] for x in ids}
        pr.manual_order = True
        ST.bump()
        return ok()


@app.post("/api/photo/<pid>")
def api_photo_edit(pid):
    j = request.get_json(force=True)
    with LOCK:
        pr = ST.proj
        p = pr.photos.get(pid)
        if not p:
            return err("Фото не найдено", 404)
        if "excluded" in j:
            p.excluded = bool(j["excluded"])
        if j.get("rotate") in (90, -90, 180):
            pr.photos[pid] = A.rotate_photo(p, j["rotate"], detector())
            pr.dirty_files.add(pid)
            if pr.book:
                for si, sp in enumerate(pr.book["spreads"]):
                    for sj, s in enumerate(sp["slots"]):
                        if s["photo"] == pid:
                            A.recrop_slot(pr.book, si, sj, pr.photos, smart=False)
        if j.get("delete"):
            if pr.book:
                for si, sp in enumerate(pr.book["spreads"]):
                    for sj, s in enumerate(sp["slots"]):
                        if s["photo"] == pid:
                            A.set_slot_photo(pr.book, si, sj, None, pr.photos)
            pr.photos.pop(pid, None)
            pr.prints.pop(pid, None)
            pr.originals.pop(pid, None)
            for ext in (".jpg", ".orig.jpg"):
                P.blob_del(pr, pid + ext)
        _after_photos_change(pr)
        ST.bump()
        return ok()


# ---------------------------------------------------------------------------
# API: РЕНДЕР
# ---------------------------------------------------------------------------
@app.get("/api/spread/<int:i>.jpg")
def api_spread(i):
    w = min(int(request.args.get("w", 900)), 2000)
    with LOCK:
        if not (0 <= i < len(ST.book["spreads"])):
            abort(404)
        plain = request.args.get("plain") == "1"
        key = ("sp", _spread_v(i), w, plain)
        if key not in ST.cache:
            ST.cache[key] = _jpeg(A.render_spread(ST.book["spreads"][i], ST.photos, width=w, badges=not plain, plain=plain))
        data = ST.cache[key]
    r = send_file(io.BytesIO(data), mimetype="image/jpeg")
    r.headers["Cache-Control"] = "private, max-age=86400"
    return r


@app.post("/api/cover/style")
def api_cover_style():
    j = request.get_json(force=True)
    c = j.get("bg")
    if c is not None and not (isinstance(c, list) and len(c) == 3 and all(isinstance(x, int) and 0 <= x <= 255 for x in c)):
        return err("Неверный цвет")
    with LOCK:
        snapshot()
        if c is None:
            ST.book.pop("cover_bg", None)
        else:
            ST.book["cover_bg"] = c
        ST.bump()
        return ok()


@app.get("/api/cover.jpg")
def api_cover():
    w = min(int(request.args.get("w", 900)), 2000)
    with LOCK:
        plain = request.args.get("plain") == "1"
        key = ("cv", _cover_v(), w, plain)
        if key not in ST.cache:
            ST.cache[key] = _jpeg(A.render_cover(ST.book, w, plain=plain))
        data = ST.cache[key]
    r = send_file(io.BytesIO(data), mimetype="image/jpeg")
    r.headers["Cache-Control"] = "private, max-age=86400"
    return r


@app.get("/api/loupe")
def api_loupe():
    si, sj = int(request.args["si"]), int(request.args["sj"])
    mode = request.args.get("mode", "plain")
    with LOCK:
        s = ST.book["spreads"][si]["slots"][sj]
        if not s["photo"]:
            abort(404)
        p = ST.photos[s["photo"]]
        asp = A.slot_aspect(ST.book["spreads"][si]["template"], sj)
        ow = 800
        oh = int(ow / asp)
        sr = upscaler() if mode == "sr" else None
        im = A.render_slot_image(p, s["crop"], ow, oh, True, sr=sr, force_sr=(mode == "sr"))
        cw, ch = int(ow * 0.4), int(oh * 0.4)
        x, y = (ow - cw) // 2, (oh - ch) // 2
        data = _jpeg(im.crop((x, y, x + cw, y + ch)).resize((cw * 2, ch * 2), Image.NEAREST), 88)
    return send_file(io.BytesIO(data), mimetype="image/jpeg")


@app.get("/api/sr")
def api_sr():
    with LOCK:
        sr = upscaler()
        return jsonify(available=bool(sr.available), reason=getattr(sr, "reason", "") or "")


@app.get("/api/pdf")
def api_pdf():
    dpi = min(max(int(request.args.get("dpi", 150)), 72), 300)
    with LOCK:
        sr = upscaler() if request.args.get("sr") == "1" else None
        data = A.export_pdf(ST.book, ST.photos, dpi=dpi, include_cover=True, sr=sr)
    return send_file(io.BytesIO(data), mimetype="application/pdf", as_attachment=True, download_name="photobook.pdf")


# ---------------------------------------------------------------------------
# API: РЕДАКТИРОВАНИЕ КНИГИ
# ---------------------------------------------------------------------------
def _act(actions):
    snapshot()
    rep = AS.execute(actions, ST.book, ST.photos, active())
    ST.bump()
    return rep


@app.post("/api/slot")
def api_slot():
    j = request.get_json(force=True)
    with LOCK:
        try:
            si, sj, pid = int(j["si"]), int(j["sj"]), j.get("photo")
            sp = ST.book["spreads"][si]
            if pid and pid not in ST.photos:
                return err("Фото не найдено", 404)
            snapshot()
            if pid:
                loc = AS._find_photo(ST.book, pid)
                old = sp["slots"][sj]["photo"]
                A.set_slot_photo(ST.book, si, sj, pid, ST.photos)
                if loc and loc != (si, sj):
                    A.set_slot_photo(ST.book, loc[0], loc[1], old, ST.photos)
            else:
                A.set_slot_photo(ST.book, si, sj, None, ST.photos)
            ST.bump()
        except (KeyError, IndexError, ValueError):
            return err("Неверный слот")
        return ok()


@app.post("/api/crop")
def api_crop():
    j = request.get_json(force=True)
    with LOCK:
        try:
            si, sj = int(j["si"]), int(j["sj"])
            slot = ST.book["spreads"][si]["slots"][sj]
            if not slot["photo"]:
                return err("Слот пуст")
            if j.get("auto"):
                snapshot()
                A.recrop_slot(ST.book, si, sj, ST.photos)
            else:
                x, y, w, h = [float(v) for v in j["crop"]]
                w, h = min(max(w, 0.05), 1.0), min(max(h, 0.05), 1.0)
                x, y = min(max(x, 0), 1 - w), min(max(y, 0), 1 - h)
                snapshot()
                slot["crop"], slot["manual"] = (x, y, w, h), True
            ST.bump()
        except (KeyError, IndexError, ValueError, TypeError):
            return err("Неверные параметры кропа")
        return ok()


@app.post("/api/template")
def api_template():
    j = request.get_json(force=True)
    if j.get("template") not in A.TEMPLATES:
        return err("Нет такого шаблона")
    with LOCK:
        return ok(report=_act([dict(type="change_template", spread=int(j["si"]) + 1, template=j["template"])]))


@app.post("/api/theme")
def api_theme():
    j = request.get_json(force=True)
    if j.get("theme") not in THEMES:
        return err("Нет такой темы")
    target = "all" if j.get("si") is None else int(j["si"]) + 1
    with LOCK:
        return ok(report=_act([dict(type="apply_theme", theme=j["theme"], target=target)]))


@app.post("/api/text")
def api_text():
    j = request.get_json(force=True)
    with LOCK:
        snapshot()
        field = j.get("field")
        text = str(j.get("text", ""))[:80]
        if field == "cover_title":
            old = ST.book["title"]
            ST.book["title"] = text
            for sp in ST.book["spreads"]:
                if sp["texts"].get("title") == old:
                    sp["texts"]["title"] = text
        elif field == "cover_subtitle":
            ST.book["subtitle"] = text
        elif field in ("title", "subtitle", "quote"):
            ST.book["spreads"][int(j["si"])]["texts"][field] = text
        else:
            return err("Неверное поле")
        ST.bump()
        return ok()


@app.post("/api/spread")
def api_spread_edit():
    j = request.get_json(force=True)
    with LOCK:
        op = j.get("op")
        if op == "add":
            return ok(report=_act([dict(type="add_spread", template=j.get("template") or "grid4", position=None)]))
        if op == "remove":
            return ok(report=_act([dict(type="remove_spread", spread=int(j["si"]) + 1)]))
        if op == "bg":                                   # свой цвет фона разворота (ручной дизайн)
            si = int(j["si"])
            c = j.get("bg")
            if not (0 <= si < len(ST.book["spreads"])):
                return err("Нет такого разворота", 404)
            if c is not None and not (isinstance(c, list) and len(c) == 3 and all(isinstance(x, int) and 0 <= x <= 255 for x in c)):
                return err("Неверный цвет")
            snapshot()
            if c is None:
                ST.book["spreads"][si].pop("bg", None)
            else:
                ST.book["spreads"][si]["bg"] = c
            ST.bump()
            return ok()
        if op == "move":
            si, d = int(j["si"]), int(j["dir"])
            sps = ST.book["spreads"]
            if 0 <= si + d < len(sps):
                snapshot()
                sps[si], sps[si + d] = sps[si + d], sps[si]
                ST.bump()
            return ok()
        return err("Неизвестная операция")


@app.post("/api/ai")
def api_ai():
    j = request.get_json(force=True)
    op = j.get("op")
    with LOCK:
        if op == "fill":
            return ok(report=_act([dict(type="fill_empty")]))
        if op == "recrop_all":
            return ok(report=_act([dict(type="recrop", target="all")]))
        if op == "recrop_photo":
            # автокадрирование одного фото везде, где оно стоит в альбоме (по желанию пользователя)
            pid, n = str(j.get("id", "")), 0
            if not ST.book or pid not in ST.photos:
                return err("Фото не найдено", 404)
            snapshot()
            for si, sp in enumerate(ST.book["spreads"]):
                for sj, sl in enumerate(sp["slots"]):
                    if sl["photo"] == pid:
                        A.recrop_slot(ST.book, si, sj, ST.photos)
                        n += 1
            if not n:
                ST.history.pop()
                return ok(report=["Это фото пока не стоит в альбоме: поставьте его в слот и повторите."])
            ST.bump()
            return ok(report=[f"Автокадрирование: кадров обновлено {n}."])
        if op == "autoplan":
            act = active()
            if not act:
                return err("Сначала добавьте фото")
            snapshot()
            ST.proj.book = A.plan_book(act, ST.book["title"], ST.book.get("subtitle", ""), theme=ST.book.get("theme"), manual_order=ST.proj.manual_order)
            ST.bump()
            return ok(report=[f"Собрала альбом: {len(ST.book['spreads'])} разв., {len(act)} фото."])
        if op == "balance":
            snapshot()
            msg = A.balance_book(ST.book, ST.photos)
            ST.bump()
            return ok(report=[msg])
        return err("Неизвестная операция")


@app.post("/api/undo")
def api_undo():
    with LOCK:
        if ST.history:
            ST.future.append(copy.deepcopy(ST.book))
            ST.proj.book = ST.history.pop()
            ST.bump()
        return ok()


@app.post("/api/redo")
def api_redo():
    with LOCK:
        if ST.future:
            ST.history.append(copy.deepcopy(ST.book))
            ST.proj.book = ST.future.pop()
            ST.bump()
        return ok()


# ---------------------------------------------------------------------------
# ФОНОВЫЕ ЗАДАЧИ ИИ (чат и правка фото): клиент опрашивает /api/job/<id> и видит ход рассуждений
# ---------------------------------------------------------------------------
class Job:
    def __init__(self, kind):
        self.id = uuid.uuid4().hex[:10]
        self.kind = kind
        self.steps: list = []
        self.done = False
        self.error = ""
        self.result: dict = {}
        self.t0 = time.time()
        self.owner = who()["uid"] if has_request_context() else ""

    def step(self, text, kind="step"):
        self.steps.append(dict(text=str(text)[:600], kind=kind, t=round(time.time() - self.t0, 1)))

    def view(self):
        return dict(id=self.id, kind=self.kind, steps=self.steps, done=self.done, error=self.error, result=self.result)


def start_job(kind, fn, *args):
    job = Job(kind)
    ST.jobs[job.id] = job
    if len(ST.jobs) > 40:
        for k in list(ST.jobs)[:10]:
            ST.jobs.pop(k, None)

    def run():
        LOCK._tl.uid = job.owner                  # фоновая задача работает под блокировкой своего владельца
        try:
            fn(job, *args)
        except Exception as e:
            job.error = f"Что-то пошло не так: {e}"
            job.step(job.error, "warn")
        finally:
            job.done = True

    threading.Thread(target=run, daemon=True).start()
    return job


@app.get("/api/job/<jid>")
def api_job(jid):
    j = ST.jobs.get(jid)
    if not j or j.owner != who()["uid"]:
        return err("Задача не найдена", 404)
    return jsonify(j.view())


BOOK_WORDS = re.compile(r"книг|альбом|разворот|обложк|страниц|слот|шаблон|раскладк", re.I)


def _find_target_photo(pr, text, cur_photo):
    _, by_num = AS.numbering(pr.photos)
    m = re.search(r"(?:фото(?:графи\w*)?|снимк\w*|кадр\w*)\s*(?:№|#)?\s*(\d+)|(\d+)\s*-?(?:е|я|ое|ая)?\s*(?:фото|снимок)", text, re.I)
    if m:
        pid = by_num.get(int(m.group(1) or m.group(2)))
        if pid:
            return pid
    return cur_photo if cur_photo in pr.photos else None


def _describe(a):
    t = a.get("type")
    names = {"apply_theme": lambda: f"оформить темой «{THEMES[a['theme']]['name']}»" + ("" if a.get("target") == "all" else f" (разворот {a['target']})"),
             "set_text": lambda: f"поменять текст: «{a.get('text', '')}»", "change_template": lambda: f"сменить шаблон разворота {a['spread']}",
             "swap_photos": lambda: "поменять фото местами", "set_photo": lambda: f"поставить фото {a['photo']} в слот",
             "remove_photo": lambda: "убрать фото из слота", "rearrange": lambda: f"переставить фото ({a.get('mode')})",
             "recrop": lambda: "заново подобрать кадрирование", "add_spread": lambda: "добавить разворот",
             "remove_spread": lambda: f"удалить разворот {a['spread']}", "fill_empty": lambda: "заполнить пустые слоты"}
    try:
        return names.get(t, lambda: str(t))()
    except Exception:
        return str(t)


def _chat_job(job, pid, text, cur_photo, explicit, lang="ru"):
    pr = ST.projects.get(pid)
    job.photo = cur_photo if explicit and pr and cur_photo in pr.photos else None
    job.step(f"Читаю запрос: «{text}»")
    msg = dict(role="assistant", content="", report=[], source="offline", errors=[])
    # 1) правка самого снимка?
    with LOCK:
        book_words = bool(BOOK_WORDS.search(text))
        is_edit = (bool(IE.EDIT_RX.search(text)) and not book_words) or (explicit and cur_photo in pr.photos and not book_words)
        target = _find_target_photo(pr, text, cur_photo) if is_edit else None
        pname = pr.photos[target].name if target else ""
    if is_edit:
        if not target:
            job.step("Это похоже на правку снимка, но я не поняла, какого. Нужен номер фото или открытое фото.", "warn")
            msg["content"] = "Какое фото править? Нажмите на фото в альбоме и выберите «Спросить у ИИ», либо напишите номер: «фото 3»."
        else:
            job.step("Понимаю так: это правка самого снимка, а не раскладки альбома.")
            job.step(f"Нашла фото «{pname}». Открою для него редактор и сразу запущу запрос.", "ok")
            msg["content"] = "Берусь за это фото. Результат можно посмотреть до применения."
            msg["edit"] = dict(photo=target, prompt=text)
        _finish_chat(job, pr, text, msg)
        return
    # 2) команда для альбома
    if pr.book is None:
        job.step("В проекте с печатью нет разворотов, поэтому могу только править отдельные фото.", "warn")
        msg["content"] = "Для фотопечати я умею править отдельные фото: нажмите на фото и спросите, что с ним сделать."
        _finish_chat(job, pr, text, msg)
        return
    with LOCK:
        book_copy = copy.deepcopy(pr.book)
        photos = pr.photos
        hist = [dict(role=m["role"], content=m["content"]) for m in pr.chat[-8:] if m["role"] in ("user", "assistant")]
    n_ph, n_sp = len(photos), len(book_copy["spreads"])
    job.step(f"Смотрю альбом: {n_sp} разв., {n_ph} фото, тема «{THEMES.get(book_copy.get('theme', 'classic'), THEMES['classic'])['name']}».")
    data, acts, errs, source = None, [], [], "offline"
    if ST.use_llm and ST.api_key:
        ms, _ = AS.resolve_models(ST.models or AS.DEFAULT_MODELS)
        job.step("Советуюсь с ИИ: передаю структуру альбома и оценки качества (сами фото не отправляются).")
        data, e, meta = AS.ask_llm(text, AS.build_context(book_copy, photos), hist, ST.api_key, ms, lang=lang)
        if e:
            app.logger.warning("chat LLM: %s", e)
        if data is not None:
            if meta.get("reasoning"):
                job.step(meta["reasoning"], "thought")
            if data.get("thought"):
                job.step(str(data["thought"])[:500], "thought")
            acts, errs = AS.validate_actions(data.get("actions") or [], book_copy, photos)
            if acts or not (data.get("actions") or []):
                source = "llm"
                msg["content"] = str(data.get("reply", ""))[:400]
            else:
                app.logger.warning("chat LLM: неприменимые действия: %s", "; ".join(errs[:3]))
                job.step("Предложенные действия не подошли к этому альбому, разбираю запрос проще.", "warn")
                data = None
        else:
            job.step("Умный разбор сейчас недоступен, разбираю запрос по ключевым словам.", "warn")
    else:
        job.step("Разбираю запрос по ключевым словам.")
    if data is None:
        off = AS.parse_offline(text, book_copy, photos)
        acts, errs = AS.validate_actions(off["actions"], book_copy, photos)
        msg["content"] = off["reply"]
        source = "offline"
    msg["source"], msg["errors"] = source, errs
    if acts:
        job.step("План: " + "; ".join(_describe(a) for a in acts) + ".")
        job.step("Проверяю, что каждое действие применимо к альбому…")
    elif errs:
        job.step("Не получилось подобрать действия: " + "; ".join(errs[:2]), "warn")
    else:
        job.step("Менять в альбоме ничего не нужно, отвечаю текстом.")
    if acts:
        with LOCK:
            snapshot_p(pr)
            msg["report"] = AS.execute(acts, pr.book, pr.photos, [p for p in pr.photos.values() if not p.excluded and not p.dup_of])
            ST.rev += 1
            ST.cache.clear()
            ST.touch(pr)
        job.step("Готово: изменения применены. Их можно отменить кнопкой «Отменить» на вкладке «Альбом».", "ok")
    if not msg["content"]:
        msg["content"] = "Готово." if msg["report"] else "Не поняла запрос."
    _finish_chat(job, pr, text, msg)


def snapshot_p(pr):
    pr.history.append(copy.deepcopy(pr.book))
    pr.history = pr.history[-30:]
    pr.future = []


def _finish_chat(job, pr, text, msg):
    msg["steps"] = list(job.steps)
    with LOCK:
        pr.chat.append(dict(role="user", content=text, photo=getattr(job, "photo", None)))
        pr.chat.append(msg)
        ST.touch(pr)
    job.result = dict(message=msg)


@app.post("/api/chat/start")
def api_chat_start():
    body = request.get_json(force=True) or {}
    text = str(body.get("text", "")).strip()[:500]
    if not text:
        return err("Пустое сообщение")
    job = start_job("chat", _chat_job, ST.cur, text, body.get("photo"), bool(body.get("explicit")), "be" if body.get("lang") == "be" else "ru")
    return jsonify(job=job.id)


@app.post("/api/chat/clear")
def api_chat_clear():
    with LOCK:
        ST.proj.chat = []
        ST.touch()
        return ok()


# --- правка фото ---
def _edit_job(job, pid, photo_id, prompt, marks):
    pr = ST.projects.get(pid)
    with LOCK:
        p = pr.photos.get(photo_id)
        if not p:
            raise RuntimeError("фото не найдено")
        src, name = p.img, p.name
    job.step(f"Беру фото «{name}», запрос: «{prompt}».")
    res = IE.run_edit(src, prompt, marks, log=job.step)
    with LOCK:
        tok = uuid.uuid4().hex[:10]
        if len(ST.edits) > 12:
            ST.edits.pop(next(iter(ST.edits)))
        ST.edits[tok] = dict(owner=pr.owner, project=pid, photo=photo_id, image=res["image"], label=res["label"], source=res["source"], prompt=prompt)
    job.result = dict(token=tok, source=res["source"], label=res["label"], note=res["note"],
                      size=list(res["image"].size), photo=photo_id)
    job.step("Результат готов: сравните «Было / Стало» и решите, применять ли.", "ok")


def _edit_job_guard(job, pid, photo_id, prompt, marks):
    try:
        _edit_job(job, pid, photo_id, prompt, marks)
    except IE.EditError as e:
        job.error = str(e)
        job.step(str(e), "warn")


@app.post("/api/edit/start")
def api_edit_start():
    j = request.get_json(force=True)
    pid, prompt = j.get("photo"), str(j.get("prompt", "")).strip()[:300]
    marks = [m for m in (j.get("marks") or [])[:12] if isinstance(m, dict) and "x" in m and "y" in m]
    if pid not in ST.photos:
        return err("Фото не найдено", 404)
    if not prompt:
        return err("Напишите, что сделать с фото")
    job = start_job("edit", _edit_job_guard, ST.cur, pid, prompt, marks)
    return jsonify(job=job.id)


@app.post("/api/edit")
def api_edit_sync():
    """Синхронный вариант (для тестов и простых клиентов)."""
    j = request.get_json(force=True)
    pid, prompt = j.get("photo"), str(j.get("prompt", "")).strip()[:300]
    marks = [m for m in (j.get("marks") or [])[:12] if isinstance(m, dict) and "x" in m and "y" in m]
    if pid not in ST.photos:
        return err("Фото не найдено", 404)
    if not prompt:
        return err("Напишите, что сделать с фото")
    job = Job("edit")
    _edit_job_guard(job, ST.cur, pid, prompt, marks)
    if job.error:
        return err(job.error, 422)
    return jsonify(job.result)


@app.get("/api/edit/<token>.jpg")
def api_edit_img(token):
    with LOCK:
        e = ST.edits.get(token)
        if not e or e.get("owner") != who()["uid"]:
            abort(404)
        im = e["image"].copy()
    im.thumbnail((1100, 1100))
    r = send_file(io.BytesIO(_jpeg(im, 86)), mimetype="image/jpeg")
    r.headers["Cache-Control"] = "no-store"
    return r


@app.post("/api/edit/apply")
def api_edit_apply():
    j = request.get_json(force=True)
    with LOCK:
        pr = ST.proj
        e = ST.edits.get(j.get("token"))
        if not e or e["project"] != pr.id or e["photo"] not in pr.photos:
            return err("Правка устарела, повторите", 404)
        old = pr.photos[e["photo"]]
        img = e["image"]
        osz = old.orig_size if img.size == old.img.size else img.size
        if j.get("mode") == "copy":
            newp = A.build_photo(f"{old.name} ({e['label']})", img, detector(), date=old.date, orig_size=osz)
            pr.photos[newp.id] = newp
            pr.originals.setdefault(newp.id, old)
            if pr.kind == "print":
                pr.prints[newp.id] = 1
            pid = newp.id
        else:
            pr.originals.setdefault(old.id, old)
            newp = A.build_photo(old.name, img, detector(), date=old.date, orig_size=osz)
            newp.id, newp.excluded, newp.enhance = old.id, old.excluded, old.enhance
            pr.photos[old.id] = newp
            pr.dirty_files.add(old.id)
            if pr.book:
                for si, sp in enumerate(pr.book["spreads"]):
                    for sj, s in enumerate(sp["slots"]):
                        if s["photo"] == old.id and not s.get("manual"):
                            A.recrop_slot(pr.book, si, sj, pr.photos, smart=False)
            pid = old.id
        _after_photos_change(pr)
        ST.edits.pop(j.get("token"), None)
        ST.bump()
        return ok(photo=pid)


@app.post("/api/photo/<pid>/revert")
def api_photo_revert(pid):
    with LOCK:
        pr = ST.proj
        o = pr.originals.pop(pid, None)
        if not o:
            return err("Это фото не менялось", 404)
        pr.photos[pid] = o
        pr.dirty_files.add(pid)
        if pr.book:
            for si, sp in enumerate(pr.book["spreads"]):
                for sj, s in enumerate(sp["slots"]):
                    if s["photo"] == pid and not s.get("manual"):
                        A.recrop_slot(pr.book, si, sj, pr.photos, smart=False)
        _after_photos_change(pr)
        P.blob_del(pr, pid + ".orig.jpg")
        ST.bump()
        return ok()


# ---------------------------------------------------------------------------
# API: ПРОВЕРКА ПЕРЕД ПЕЧАТЬЮ, ЗАКАЗ, ОТСЛЕЖИВАНИЕ
# ---------------------------------------------------------------------------
def preflight_of(p):
    issues, empty, bad = [], 0, 0
    if p.kind == "book":
        for si, sp in enumerate(p.book["spreads"]):
            for sj, s in enumerate(sp["slots"]):
                if not s["photo"]:
                    empty += 1
                    continue
                g, msg, dpi = A.slot_quality(p.photos[s["photo"]], s["crop"], A.slot_width_cm(sp["template"], sj))
                if g == "bad":
                    bad += 1
                    issues.append(dict(kind="bad", si=si, sj=sj, text=f"Разворот {si + 1}, слот {sj + 1}: {msg}"))
        if empty:
            issues.insert(0, dict(kind="empty", text=f"Пустых слотов: {empty}. На печати будут серые места."))
    else:
        for ph in p.photos.values():
            if p.prints.get(ph.id, 0) > 0 and ph.q["grade"] == "bad":
                bad += 1
                issues.append(dict(kind="bad", text=f"«{ph.name}»: {ph.q['message']}"))
    return dict(id=p.id, name=p.name, kind=p.kind, empty=empty, bad=bad, issues=issues[:6], n=p.line_qty(), issue=P.availability(p))


@app.get("/api/preflight")
def api_preflight():
    with LOCK:
        items = []
        for p in ST.mine():
            if p.in_cart and not p.ordered:
                ensure(p)
                items.append(preflight_of(p))
        return jsonify(items=items, can_order=bool(items) and all(i["n"] > 0 and not i.get("issue") for i in items))


def cart_projects():
    return [p for p in ST.mine() if p.in_cart and not p.ordered and p.line_qty() > 0]


@app.post("/api/event")
def api_event():
    """Шаги воронки, которые видит только интерфейс (открыт каталог, открыто оформление)."""
    ev = str((request.get_json(silent=True) or {}).get("ev", ""))
    if ev in ("catalog", "checkout"):
        AN.track(who()["uid"], ev)
    return jsonify(ok=True)


@app.get("/api/catalog")
def api_catalog():
    """МП-01: каталог и цены отдаются API, интерфейс ничего не зашивает. Источник: catalog_api (внешний/mock API или файл)."""
    c = CAT.public()
    c.pop("_version", None)
    et = '"%s"' % c["version"]
    if request.headers.get("If-None-Match") == et:
        return "", 304
    r = jsonify(dict(c, statuses=[dict(id=x["id"], title=x["title"]) for x in O.STATUSES]))
    r.headers["ETag"] = et
    r.headers["Cache-Control"] = "no-cache"
    return r


def _portrait():
    """Портрет текущего клиента из CRM: только для вошедшего клиента (по e-mail аккаунта); гостям и админам None."""
    u = who()
    if u.get("guest") or u.get("role") != "client" or not u.get("email"):
        return None
    return CRM.lookup(u["email"])


def _perk(j):
    """Персональная скидка/доставка для корзины. Проценты считает только сервер; клиент лишь сообщает, включены ли предложения."""
    if j.get("pers") is False:
        return None
    return OF.perk(_portrait())


def _disc_inputs(j):
    """Какая скидка применяется. Клиент выбирает ОДИН источник процента: disc = auto (самая выгодная) | none | promo | id предложения.
    Бесплатная доставка по предложению включается отдельно (nofree). Проценты по-прежнему не суммируются."""
    promo = str(j.get("promo", "")).strip()
    pk = _perk(j)
    ch = str(j.get("disc") or "auto")
    po = O.promo_of(promo)
    if ch != "auto":
        if ch != "promo" and po and po["percent"]:
            promo = ""                                      # процент промокода не нужен: выбрано другое
        if pk:
            offs = {o["id"]: o for o in OF.build(_portrait()) if o["checkout"]}
            pk = dict(pk)
            if ch in offs:
                pk["percent"], pk["label"] = offs[ch]["percent"], offs[ch]["short"]
            elif ch in ("none", "promo"):
                pk["percent"], pk["label"] = 0, ""
    if pk and j.get("nofree"):
        pk = dict(pk, free_delivery=False)
    return promo, pk


def _disc_options(j):
    """Список вариантов скидки для оформления и то, что применено сейчас."""
    pk = _perk(j)
    opts = [dict(id=o["id"], pct=o["percent"], title=o["short"] or o["title"], why=o["why"]) for o in (OF.build(_portrait()) if pk is not None else []) if o["checkout"] and o["percent"]]
    free = any(o["free_delivery"] for o in (OF.build(_portrait()) if pk is not None else []) if o["checkout"])
    po = O.promo_of(str(j.get("promo", "")).strip())
    if po and po["percent"]:
        opts.append(dict(id="promo", pct=po["percent"], title="Промокод " + po["code"], why=""))
    return opts, free


@app.get("/api/offers")
def api_offers():
    """Персональные предложения для главной или оформления (where=checkout). Нет в CRM или нет согласия: пустой список."""
    p = _portrait()
    offs = OF.build(p)
    if request.args.get("where") == "checkout":
        offs = [o for o in offs if o["checkout"]]
    return jsonify(source="crm" if p else "none", customer=OF.summary(p), offers=offs[:6])


# --- демо-CRM по HTTP: тот же контракт, что должна отдавать настоящая CRM (docs/CRM.md) ---
def _crm_auth():
    return request.headers.get("Authorization", "") == "Bearer " + CRM.TOKEN


@app.get("/mock-crm/v1/customers/lookup")
def mock_crm_lookup():
    if not _crm_auth():
        return jsonify(error="unauthorized"), 401
    raw = CRM.demo_by_email(request.args.get("email", ""))
    return (jsonify(raw), 200) if raw else (jsonify(error="not found"), 404)


@app.get("/mock-crm/v1/customers/<cid>")
def mock_crm_by_id(cid):
    if not _crm_auth():
        return jsonify(error="unauthorized"), 401
    raw = CRM.demo_by_id(cid)
    return (jsonify(raw), 200) if raw else (jsonify(error="not found"), 404)


def _loy_acct():
    """Фотобаллы текущего клиента (None: гость, админ, аноним — программа только для вошедших клиентов)."""
    u = who()
    if u.get("guest") or u.get("role") != "client" or not u.get("uid"):
        return None
    return LY.account(O.orders_of(u["uid"]), _portrait())


def _quote(j, s):
    """Итоговая цена с учётом промокода, персональной скидки и (по желанию клиента) фотобаллов. Всё считает сервер."""
    promo, pk = _disc_inputs(j)
    t = O.totals(s, j.get("delivery") or O.first("delivery")["id"], promo, pk)
    acct = _loy_acct()
    loy = None
    if acct:
        mx = LY.max_spend(t["items"], t["discount"], acct)
        use = bool(j.get("use_points")) and mx > 0
        if use:
            t = O.totals(s, j.get("delivery") or O.first("delivery")["id"], promo, pk, mx)
        amt = t["items"] - t["discount"] - t["points"]
        bd = OF._birthday_delta((_portrait() or {}).get("birthday", ""), datetime.now().date())
        pts, bonus = LY.earn(amt, birthday_delta=bd)
        why = "" if (mx > 0 or t["points"]) else ("discount" if t["discount"] > 0 else "min" if not acct["can_spend"] else "empty" if not acct["balance"] else "")
        loy = dict(balance=acct["balance"], max=mx, used=t["points"], earn=pts, bonus=bonus, why=why, spend_min=LY.SPEND_MIN_TOTAL)
    opts, free = _disc_options(j)
    chosen = "none" if not t["discount"] else (str(j.get("disc") or "auto") if str(j.get("disc") or "auto") != "auto" else
             ("promo" if t["promo"] and "промокод" in t["discount_label"].lower() else next((o["id"] for o in opts if o["id"] != "promo" and o["title"] == t["discount_label"]), "none")))
    t["disc_options"] = dict(options=opts, chosen=chosen, free_offer=free, free_on=bool(pk and pk.get("free_delivery")))
    return t, loy


@app.post("/api/quote")
def api_quote():
    """Пересчёт итоговой цены на сервере (МП-04): товары корзины, доставка, промокод, фотобаллы."""
    j = request.get_json(force=True)
    with LOCK:
        cart = cart_projects()
        s = sum(p.line_sum() for p in cart)
        promo = str(j.get("promo", "")).strip()
        t, loy = _quote(j, s)
        if promo and not O.promo_of(promo):
            return jsonify(error="Промокод не найден. Проверьте написание: тестовые коды PICPAC10 и FREESHIP.", totals=t, loy=loy), 200
        return jsonify(totals=t, loy=loy, promo_text=(O.promo_of(promo) or {}).get("text", ""))


@app.get("/api/loyalty")
def api_loyalty():
    """Карточка фотобаллов для главной: только для вошедшего клиента."""
    acct = _loy_acct()
    return jsonify(account=acct, rules=LY.rules(), cur=O.CATALOG["currency"])


@app.post("/api/order")
def api_order():
    j = request.get_json(force=True)
    msg = O.validate_contact(j)
    if msg:
        return err(msg)
    if j.get("promo") and not O.promo_of(j["promo"]):
        return err("Промокод не найден. Уберите его или проверьте написание.")
    u = who()
    client = u["uid"] or str(j.get("client", ""))
    with LOCK:
        # повторная отправка того же запроса не создаёт второй заказ (МП-05)
        if j.get("key"):
            for o in O.all_orders():
                if o.get("key") == str(j["key"])[:60] and o.get("client") == client[:40]:
                    return jsonify(order=O.public_view(o), duplicate=True)
        j["promo"], j["_perk"] = _disc_inputs(j)
        cart = cart_projects()
        if not cart:
            return err("Корзина пуста")
        gone = [p for p in cart if P.availability(p)]
        if gone:                                         # админ убрал выбранное: заказ не оформляем, предлагаем замену
            r = jsonify(error="Пока каталог обновлялся, кое-что из вашей корзины стало недоступно: «" + "», «".join(p.name for p in gone) + "». Замените это на похожее, фото и макет сохранятся.",
                        unavailable=[p.id for p in gone])
            return r, 409
        qt, loy = _quote(j, sum(p.line_sum() for p in cart))
        j["_points"] = qt["points"]
        if loy:
            j["_loy"] = dict(earned=loy["earn"], bonus=loy["bonus"])
        items = []
        for p in cart:
            ensure(p)
            items.append(dict(id=p.id, product=p.product, product_name=p.names.get("product", ""), kind=p.kind, title=p.name,
                              spreads=p.n_spreads(), photos=p.count_photos(), qty=p.line_qty(), unit=p.unit(), params=p.params))
        o = O.create_order(j, items, client)
        AN.track(client, "order")
        try:
            os.makedirs(os.path.join(O.PDF_DIR, o["code"]), exist_ok=True)
            for p in cart:
                if p.kind == "book":
                    data = A.export_pdf(p.book, p.photos, dpi=150, include_cover=True, sr=upscaler() if ST.upscale else None)
                    with open(os.path.join(O.PDF_DIR, o["code"], f"{p.id}.pdf"), "wb") as f:
                        f.write(data)
        except Exception as e:
            return err(f"Заказ создан ({o['code']}), но макет не сохранился: {e}", 500)
        for p in cart:
            p.in_cart, p.ordered = False, True
            ST.touch(p)
        ST.flush()
        return jsonify(order=O.public_view(o))


@app.get("/api/orders")
def api_orders():
    """История заказов только текущего пользователя (по сессии или гостевому id)."""
    u = who()
    return jsonify(orders=[O.public_view(o) for o in O.orders_of(u["uid"])])


@app.get("/api/track/<code>")
def api_track(code):
    o = O.get_order(code)
    if not o:
        return err("Заказ с таким кодом не найден. Проверьте код: он выглядит как PP-AB12-CD34.", 404)
    return jsonify(order=O.public_view(o))


@app.post("/api/track/<code>/advance")
def api_track_advance(code):
    if os.environ.get("PICPAC_DEMO", "1") != "1" and who()["role"] != "admin":
        return err("Перемотка статусов доступна только оператору", 403)
    o = O.advance(code)
    if not o:
        return err("Заказ не найден", 404)
    return jsonify(order=O.public_view(o))


@app.get("/api/track/<code>/pdf/<pid>")
def api_track_pdf(code, pid):
    o = O.get_order(code)
    path = os.path.join(O.PDF_DIR, o["code"], f"{pid}.pdf") if o and re.fullmatch(r"[0-9a-f]{8}", pid) else ""
    if not o or not os.path.exists(path):
        abort(404)
    return send_file(path, mimetype="application/pdf", as_attachment=True, download_name=f"{o['code']}-{pid}.pdf")


# ---------------------------------------------------------------------------
# ВХОД, ПОДДЕРЖКА, АДМИНИСТРИРОВАНИЕ, УДАЛЕНИЕ ДАННЫХ
# ---------------------------------------------------------------------------
def _session_response(tok, user, extra=None):
    resp = jsonify(token=tok, user=dict(uid=user["uid"], role=user["role"], name=user["name"], guest=user["guest"], email=user.get("email", "")), **(extra or {}))
    resp.set_cookie("pp_session", tok, httponly=True, samesite="Lax", secure=request.is_secure, max_age=AC.SESSION_DAYS * 86400, path="/")
    return resp


def _ip():
    return (request.headers.get("X-Forwarded-For", "").split(",")[0].strip() or request.remote_addr or "?")


@app.post("/api/register")
def api_register():
    """Регистрация клиента: имя, почта, пароль. После неё пользователь сразу вошёл."""
    j = request.get_json(force=True) or {}
    key = "reg|" + _ip()
    if AC.throttled(key, limit=10, window=3600):
        return err("Слишком много регистраций с этого адреса. Попробуйте позже.", 429)
    tok, u = AC.register(j.get("name", ""), j.get("email", ""), j.get("password", ""))
    if not tok:
        AC.fail(key)
        return err(u, 400)
    AU.log(u["uid"], "client", "регистрация", result="ok")
    return _session_response(tok, u)


@app.post("/api/login")
def api_login():
    j = request.get_json(force=True) or {}
    email = str(j.get("email", "")).strip().lower()
    key = "login|" + _ip() + "|" + email
    if AC.throttled(key):
        AU.log(email[:40], "-", "/api/login", result="429")
        return err("Слишком много неудачных попыток. Подождите несколько минут.", 429)
    r = AC.login(email, j.get("password", ""))
    if not r:
        AC.fail(key)
        AU.log(email[:40], "-", "/api/login", result="401")
        return err("Неверная почта или пароль.", 401)
    AC.clear_fails(key)
    tok, u = r
    AU.log(u["uid"], u["role"], "вход", result="ok")
    return _session_response(tok, u)


# ---------------------------------------------------------------------------
# СОВМЕСТНЫЙ АЛЬБОМ ПО ССЫЛКЕ
# Владелец включает ссылку; кто её открыл, становится участником и может добавлять свои фото в общий пул
# (владелец сам решает, какие поставить в альбом). Участник видит общий пул, но не чужие заказы и не правит макет.
# ---------------------------------------------------------------------------
SHARE_PER_MEMBER = 40


def _shared(tok):
    tok = str(tok or "")
    for p in list(ST.projects.values()):
        sh = p.share or {}
        if sh.get("on") and sh.get("token") == tok and p.kind == "book" and not p.ordered:
            return p
    return None


def _member_of(p):
    u = who()
    if not u["uid"]:
        return None
    return (p.share.get("members") or {}).get(u["uid"])


@app.post("/api/share")
def api_share():
    """Включить/выключить ссылку на текущий альбом. Выключение отзывает доступ; новая ссылка при повторном включении."""
    j = request.get_json(force=True)
    with LOCK:
        pr = ST.proj
        if not pr or pr.kind != "book":
            return err("Совместный доступ есть только у альбомов", 409)
        sh = pr.share or {}
        if j.get("on"):
            if not sh.get("token") or j.get("renew"):
                sh = dict(token=secrets.token_urlsafe(9), members={}, created=time.time())
            sh["on"] = True
        else:
            sh["on"] = False
        pr.share = sh
        ST.touch(pr)
        return ok()


def _join_json(p):
    sh = p.share
    u = who()
    mem = _member_of(p)
    owner = u["uid"] == p.owner
    ensure(p)
    own_n = len(mem.get("ids", [])) if mem else 0
    return dict(title=p.name, owner=(AC.name_of(p.owner) or "").split(" ")[0],
                photos=[dict(id=i, by=ph.by, mine=bool(mem and i in mem.get("ids", []))) for i, ph in p.photos.items() if not ph.excluded][:120],
                members=len(sh.get("members", {})), joined=bool(mem) or owner, is_owner=owner, mine=own_n,
                limit=SHARE_PER_MEMBER, product=p.names.get("product") or O.product(p.product)["name"], pid=p.id)


@app.get("/api/join/<tok>")
def api_join_info(tok):
    p = _shared(tok)
    if not p:
        return err("Ссылка не работает: владелец закрыл доступ или альбом уже заказан.", 404)
    if not who()["uid"]:
        return err("Продолжите как гость или войдите", 401)
    with LOCK.of(p.owner):
        return jsonify(_join_json(p))


@app.post("/api/join/<tok>")
def api_join(tok):
    p = _shared(tok)
    if not p:
        return err("Ссылка не работает: владелец закрыл доступ или альбом уже заказан.", 404)
    u = who()
    if not u["uid"]:
        return err("Продолжите как гость или войдите", 401)
    j = request.get_json(silent=True) or {}
    name = (str(j.get("name", "")).strip() or u["name"] or "Гость")[:30]
    with LOCK.of(p.owner):
        mem = p.share.setdefault("members", {})
        if u["uid"] != p.owner:
            m = mem.setdefault(u["uid"], dict(name=name, joined=time.time(), ids=[]))
            if j.get("name"):
                m["name"] = name
            ST.touch(p)
        return jsonify(_join_json(p))


@app.post("/api/join/<tok>/upload")
def api_join_upload(tok):
    p = _shared(tok)
    if not p:
        return err("Ссылка не работает: владелец закрыл доступ или альбом уже заказан.", 404)
    u = who()
    files = request.files.getlist("files")
    if not u["uid"] or not files:
        return err("Файлы не выбраны. Выберите снимки в галерее.")
    mts = request.form.getlist("mt")
    added, failed = 0, []
    with LOCK.of(p.owner):
        ensure(p)
        mem = p.share.setdefault("members", {})
        m = mem.setdefault(u["uid"], dict(name=(u["name"] or "Гость")[:30], joined=time.time(), ids=[])) if u["uid"] != p.owner else None
        for fi, f in enumerate(files):
            if len(p.photos) >= O.CATALOG["limits"]["max_photos"]:
                failed.append("В альбоме уже максимум фото.")
                break
            if m is not None and len(m["ids"]) >= SHARE_PER_MEMBER:
                failed.append(f"Можно добавить не больше {SHARE_PER_MEMBER} фото.")
                break
            data = f.read()
            msg = check_upload(f, data)
            if msg:
                failed.append(msg)
                continue
            try:
                ph = A.load_photo(f.filename or "photo.jpg", data, detector())
                if ph.date is None:
                    try:
                        ms = float(mts[fi]) if fi < len(mts) else 0
                        ph.date = datetime.fromtimestamp(ms / 1000) if 946684800000 < ms < time.time() * 1000 + 86400000 else datetime.now()
                    except Exception:
                        ph.date = datetime.now()
                ph.by = m["name"] if m else ""
                p.photos[ph.id] = ph
                if m is not None:
                    m["ids"].append(ph.id)
                added += 1
            except Exception as e:
                failed.append(f"«{f.filename}»: не удалось обработать ({e}).")
        _after_photos_change(p)
        ST.rev += 1
        _trim_cache()
        ST.touch(p)
        r = _join_json(p)
    r.update(added=added, failed=failed)
    return jsonify(r)


@app.post("/api/join/<tok>/delete")
def api_join_delete(tok):
    p = _shared(tok)
    j = request.get_json(force=True)
    if not p:
        return err("Ссылка не работает", 404)
    with LOCK.of(p.owner):
        m = _member_of(p)
        pid = j.get("id")
        if not m or pid not in m.get("ids", []):
            return err("Можно удалять только свои фото", 403)
        if p.book and any(s["photo"] == pid for sp in p.book["spreads"] for s in sp["slots"]):
            return err("Это фото уже стоит в альбоме, его убрать может только владелец", 409)
        p.photos.pop(pid, None)
        m["ids"].remove(pid)
        ST.rev += 1
        ST.touch(p)
        return jsonify(_join_json(p))


@app.get("/api/join/<tok>/img/<pid>")
def api_join_img(tok, pid):
    p = _shared(tok)
    if not p or (not _member_of(p) and who()["uid"] != p.owner):
        abort(404)
    w = min(int(request.args.get("w", 400)), 1200)
    with LOCK.of(p.owner):
        ph = p.photos.get(pid)
        if not ph:
            abort(404)
        key = ("img", pid, _pv(ph), w, False)
        if key not in ST.cache:
            im = ph.img.copy()
            im.thumbnail((w, w))
            ST.cache[key] = _jpeg(im, 80)
        data = ST.cache[key]
    r = send_file(io.BytesIO(data), mimetype="image/jpeg")
    r.headers["Cache-Control"] = "private, max-age=86400"
    return r


@app.post("/api/guest")
def api_guest():
    j = request.get_json(silent=True) or {}
    tok, u = AC.guest(str(j.get("client", "")))
    return _session_response(tok, u)


@app.post("/api/logout")
def api_logout():
    h = request.headers.get("Authorization", "")
    CRM.forget(who().get("email", ""))
    AC.logout(h[7:] if h.startswith("Bearer ") else "")
    resp = jsonify(ok=True)
    resp.delete_cookie("pp_session", path="/")
    return resp


@app.post("/api/me/claim")
def api_claim():
    """Идентификация гостя: заказы и обращения гостевого id переносятся на аккаунт после входа."""
    u = who()
    guest = str((request.get_json(silent=True) or {}).get("guest", ""))
    if u["guest"] or not guest or not guest.startswith("c"):
        return jsonify(moved=0)
    n = O.move_client(guest, u["uid"])
    with LOCK:
        for p in list(ST.projects.values()):             # черновики гостя переходят к аккаунту
            if p.owner == guest:
                p.owner = u["uid"]
                P.move_to_account(p)                      # фото гостя: из временного кэша в БД
                ST.touch(p)
    t = SP.thread(guest)
    if t["messages"]:
        for m in t["messages"]:
            SP.add_message(u["uid"], m["text"], m.get("order", ""), "user" if m.get("from_") == "user" else m.get("from_"))
        SP.delete_user(guest)
    return jsonify(moved=n)


@app.post("/api/me/delete")
def api_me_delete():
    """МП-06: пользователь удаляет свои тестовые данные: заказы, обращения, проекты и фото этого устройства."""
    u = who()
    if not u["uid"]:
        return err("Нет данных для удаления")
    with LOCK:
        n_orders = O.delete_client(u["uid"])
        SP.delete_user(u["uid"])
        mine = ST.mine()
        n_proj = len(mine)
        for p in mine:
            ST.projects.pop(p.id, None)
            P.delete_files(p.id)
        ST.curmap.pop(u["uid"], None)
        ST.bump()
    CRM.forget(u.get("email", ""))
    AN.forget(u["uid"])
    AU.log(u["uid"], u["role"], "удаление данных", result=f"заказов {n_orders}, проектов {n_proj}")
    return jsonify(ok=True, orders=n_orders, projects=n_proj)


@app.get("/api/support")
def api_support_get():
    u = who()
    if not u["uid"]:
        return jsonify(messages=[], status="new")
    return jsonify(**SP.thread(u["uid"]))


@app.post("/api/support")
def api_support_post():
    u = who()
    j = request.get_json(force=True)
    text = str(j.get("text", "")).strip()
    if not u["uid"]:
        return err("Сначала войдите или продолжите как гость")
    if not text:
        return err("Напишите сообщение")
    order = O.norm_code(j.get("order", ""))
    if j.get("order") and not O.get_order(order):
        return err("Заказ с таким кодом не найден")
    SP.add_message(u["uid"], text, order, "user", name=u["name"])
    reply, esc = SP.bot_reply(text, u["uid"], order)
    SP.add_message(u["uid"], reply, order, "bot")
    SP.set_status(u["uid"], "open" if esc else "answered")
    return jsonify(**SP.thread(u["uid"]))


@app.get("/api/admin/overview")
@need("admin")
def api_admin_overview():
    orders = O.all_orders()
    views = [O.public_view(o) for o in orders]
    stages = [x["title"] for x in O.STATUSES]
    by_stage = [sum(1 for v in views if v["stage"] == i) for i in range(len(stages))]
    day0 = time.time() - 86400
    threads = SP.all_threads()
    stats = dict(orders=len(views), active=sum(1 for v in views if not v["finished"]), finished=sum(1 for v in views if v["finished"]),
                 revenue=round(sum(v["price"]["total"] for v in views), 2), today=sum(1 for o in orders if o["created"] > day0),
                 avg=round(sum(v["price"]["total"] for v in views) / len(views), 2) if views else 0,
                 open_tickets=sum(1 for t in threads if t["status"] == "open"), tickets=len(threads),
                 by_stage=[dict(title=t, n=n) for t, n in zip(stages, by_stage)], currency=O.CATALOG["currency"])
    return jsonify(
        orders=[dict(v, client=o.get("client", ""), customer_name=o["name"], phone=o["phone"]) for v, o in zip(views[:150], orders[:150])],
        threads=threads, audit=AU.read(150), stages=stages, stats=stats, crm=CRM.status(), flux=IE.status(), funnel=AN.funnel(30), db=dict(kind=db.kind(), users=db.run("SELECT count(*) FROM users", one=True)[0]))


def _catalog_impact(new: dict) -> list:
    """Что затронет сохранение: сколько проектов пользователей использовали то, что админ убирает. Удалять можно: пользователям
    покажут мягкое сообщение и предложат замену (фото и макет сохраняются)."""
    old = CAT.local_catalog()
    ids = lambda items, k="id": {x[k] for x in items}
    with LOCK:
        projs = [x for x in ST.projects.values() if not x.ordered]
    out = []
    for p in old["products"]:
        if p["id"] not in ids(new["products"]):
            n = sum(1 for x in projs if x.product == p["id"])
            if n:
                out.append(f"«{p['name']}» удалён: проектов, где его надо заменить, {n}")
    for p in new["products"]:
        o = next((x for x in old["products"] if x["id"] == p["id"]), None)
        if o and p["kind"] == "print":
            for key, field, lab in (("formats", "format", "формат"), ("papers", "paper", "бумага")):
                gone = ids(o.get(key, [])) - ids(p.get(key, []))
                n = sum(1 for x in projs if x.product == p["id"] and x.params.get(field) in gone)
                if n:
                    out.append(f"В «{p['name']}» убрана {lab}: проектов для замены {n}")
    gone = ids(old["covers"]) - ids(new["covers"])
    n = sum(1 for x in projs if (x.params or {}).get("cover") in gone)
    if n:
        out.append(f"Убрана обложка: проектов для замены {n}")
    return out


def _adm_catalog():
    c = CAT.local_catalog()
    c.pop("version", None)
    return dict(catalog=c, edited=CAT.edited(), version=CAT.info()["version"], external=bool(os.environ.get("CATALOG_API_URL", "").strip()))


@app.get("/api/admin/catalog")
@need("admin")
def api_admin_catalog():
    """Цены и условия для редактирования во вкладке «Цены»."""
    return jsonify(**_adm_catalog())


@app.post("/api/admin/catalog")
@need("admin")
def api_admin_catalog_save():
    new = (request.get_json(force=True) or {}).get("catalog") or {}
    new = CAT.normalize(new) if isinstance(new, dict) else {}
    notes = _catalog_impact(new) if isinstance(new, dict) and not CAT.validate(new) else []
    ver, bad, changes = CAT.save(new)
    if bad:
        return err(bad)
    AU.log(who()["uid"], "admin", "изменение каталога", f"{len(changes)} изм.", extra=dict(changes=(changes + notes)[:60]))
    return jsonify(**_adm_catalog(), changes=changes, notes=notes)


@app.post("/api/admin/catalog/reset")
@need("admin")
def api_admin_catalog_reset():
    changes = CAT.reset()
    AU.log(who()["uid"], "admin", "возврат исходного каталога", f"{len(changes)} изм. отменено", extra=dict(changes=changes[:60]))
    return jsonify(**_adm_catalog(), changes=changes)


@app.post("/api/admin/order/<code>/status")
@need("admin")
def api_admin_status(code):
    o = O.set_stage(code, int((request.get_json(force=True) or {}).get("stage", 0)))
    if not o:
        return err("Заказ не найден", 404)
    return jsonify(order=O.public_view(o))


@app.get("/api/admin/thread/<uid>")
@need("admin")
def api_admin_thread(uid):
    return jsonify(**SP.thread(uid))


@app.post("/api/admin/thread/<uid>")
@need("admin")
def api_admin_reply(uid):
    text = str((request.get_json(force=True) or {}).get("text", "")).strip()
    if not text:
        return err("Напишите ответ")
    SP.add_message(uid, text, "", "operator")
    return jsonify(**SP.thread(uid))


# ---------------------------------------------------------------------------
# НАСТРОЙКИ
# ---------------------------------------------------------------------------
@app.post("/api/settings")
def api_settings():
    j = request.get_json(force=True)
    tech = {"api_key", "models", "use_llm"} & set(j)
    if tech and who().get("role") != "admin":
        return err("Недостаточно прав", 403)
    with LOCK:
        if "api_key" in j:
            ST.api_key = str(j["api_key"]).strip()
        if "models" in j:
            ms = [m.strip() for m in str(j["models"]).split(",") if m.strip()]
            ST.models = ms or list(AS.DEFAULT_MODELS)
        if "use_llm" in j:
            ST.use_llm = bool(j["use_llm"])
        if "upscale" in j:
            ST.upscale = bool(j["upscale"])
        return ok()


if __name__ == "__main__":
    import socket
    port = int(os.environ.get("PORT", 8000))
    ip = "localhost"
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
    except Exception:
        pass
    print(f"\n  Ноутбук:  http://localhost:{port}/   (рамка телефона)")
    print(f"  Телефон:  http://{ip}:{port}/               (та же Wi-Fi сеть)\n")
    app.run(host="0.0.0.0", port=port, threaded=True)
