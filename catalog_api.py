# -*- coding: utf-8 -*-
"""
Каталог и цены: отдельный источник данных, а не код интерфейса или бизнес-логики.

Источники по приоритету (первый рабочий):
  1. CATALOG_API_URL   — внешний (или mock) REST API каталога, например https://shop.example/api/v1/catalog
  2. data/catalog.json — каталог, отредактированный администратором через PUT /mock-api/v1/catalog
  3. seed/catalog.json — исходный демонстрационный каталог

Встроенный mock API (Flask Blueprint, тот же формат, что и у внешнего API):
  GET  /mock-api/v1/catalog             весь каталог + version
  GET  /mock-api/v1/products[/<id>]     товары
  GET  /mock-api/v1/delivery            способы доставки
  GET  /mock-api/v1/promos              промокоды
  GET  /mock-api/v1/prices              плоский прайс-лист (товар/формат/обложка → цена)
  PUT  /mock-api/v1/catalog             заменить каталог (только администратор), цены меняются без перезапуска

Сервер кэширует каталог на CATALOG_TTL секунд; при недоступном API держится последняя рабочая версия.
Клиент (PWA) получает каталог только через GET /api/catalog и перезагружает его, когда меняется version.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
import urllib.request

from flask import Blueprint, jsonify, request

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.environ.get("PICPAC_DATA", os.path.join(HERE, "data"))
EDITED = os.path.join(DATA, "catalog.json")
SEED = os.path.join(HERE, "seed", "catalog.json")
TTL = float(os.environ.get("CATALOG_TTL", 30))

_lock = threading.RLock()
_state = dict(cat=None, at=0.0, src="", version="")

REQUIRED = ("currency", "products", "covers", "delivery", "payment", "promos", "limits")


_ID = re.compile(r"^[a-z0-9_\-]{1,24}$")
_CODE = re.compile(r"^[A-Z0-9_\-]{2,20}$")


def _on(x) -> bool:
    return x.get("active", True) is not False


def _uniq(items, key, label):
    seen = set()
    for x in items:
        v = x.get(key)
        if v in seen:
            return f"{label}: повторяется «{v}»"
        seen.add(v)
    return ""


def validate(c) -> str:
    """Пустая строка, если каталог корректен, иначе описание ошибки (показывается администратору)."""
    if not isinstance(c, dict):
        return "каталог должен быть объектом"
    for k in REQUIRED:
        if k not in c:
            return f"нет раздела «{k}»"
    for key, label in (("products", "товары"), ("covers", "обложки"), ("delivery", "способы доставки"), ("payment", "способы оплаты")):
        if not isinstance(c[key], list) or not c[key]:
            return f"раздел «{label}» не может быть пустым"
        if not any(_on(x) for x in c[key]):
            return f"в разделе «{label}» должен быть хотя бы один включённый пункт"
        if _uniq(c[key], "id", label):
            return _uniq(c[key], "id", label)
        for x in c[key]:
            if not _ID.match(str(x.get("id", ""))):
                return f"{label}: неверный id «{x.get('id', '')}»"
            if not str(x.get("name", "")).strip():
                return f"{label}: у пункта {x.get('id')} нет названия"
    for p in c["products"]:
        if not all(k in p for k in ("kind", "from_price")):
            return f"у товара {p.get('id', '?')} не хватает полей kind/from_price"
        if p["kind"] not in ("book", "print"):
            return f"неизвестный тип товара {p['kind']}"
        if not isinstance(p["from_price"], (int, float)) or p["from_price"] < 0:
            return f"неверная цена у «{p['name']}»"
        if p["kind"] == "book":
            if not all(isinstance(p.get(k), (int, float)) for k in ("per_spread", "min_spreads")):
                return f"у книги «{p['name']}» нет per_spread/min_spreads"
            if p["per_spread"] < 0 or int(p["min_spreads"]) < 1:
                return f"неверная доплата за разворот у «{p['name']}»"
        else:
            if not p.get("formats") or not p.get("papers"):
                return f"у печати «{p['name']}» должен быть хотя бы один формат и один вид бумаги"
            for lst, lab in ((p["formats"], "форматы"), (p["papers"], "бумага")):
                if _uniq(lst, "id", f"{p['name']}, {lab}"):
                    return _uniq(lst, "id", f"{p['name']}, {lab}")
                for f in lst:
                    if not _ID.match(str(f.get("id", ""))) or not str(f.get("name", "")).strip():
                        return f"{p['name']}, {lab}: у пункта нет id или названия"
            for f in p["formats"]:
                if not isinstance(f.get("mult"), (int, float)) or f["mult"] <= 0:
                    return f"неверный множитель формата у «{p['name']}»"
            if not any(True for _ in p["formats"]):
                return f"у печати «{p['name']}» нет форматов"
    for d in c["delivery"]:
        if not isinstance(d.get("price"), (int, float)) or d["price"] < 0:
            return f"неверная цена доставки «{d['name']}»"
    for cv in c["covers"]:
        if not isinstance(cv.get("add"), (int, float)):
            return f"неверная доплата за обложку «{cv.get('name', '?')}»"
    if _uniq(c["promos"], "code", "промокоды"):
        return _uniq(c["promos"], "code", "промокоды")
    for pr in c["promos"]:
        if not _CODE.match(str(pr.get("code", ""))):
            return f"промокод «{pr.get('code', '')}»: только латиница, цифры, _ и -, от 2 до 20 символов"
        if not isinstance(pr.get("percent", 0), (int, float)) or not 0 <= pr.get("percent", 0) <= 100:
            return f"процент промокода {pr['code']} должен быть от 0 до 100"
    return ""


def _version(c: dict) -> str:
    return hashlib.sha1(json.dumps(c, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()[:10]


def _read_file(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _fetch():
    """(каталог, источник) из первого рабочего источника."""
    url = os.environ.get("CATALOG_API_URL", "").strip()
    if url:
        try:
            req = urllib.request.Request(url, headers={"Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=3) as r:
                c = json.loads(r.read().decode("utf-8"))
            c = c.get("catalog", c) if isinstance(c, dict) else c
            bad = validate(c)
            if not bad:
                return c, "api:" + url
            print("[catalog] внешний API вернул неверный каталог:", bad)
        except Exception as e:
            print("[catalog] внешний API недоступен:", e)
    for path, src in ((EDITED, "data/catalog.json"), (SEED, "seed/catalog.json")):
        try:
            c = _read_file(path)
            if not validate(c):
                return c, src
        except Exception:
            pass
    return None, ""


def sync(target: dict | None = None, force: bool = False) -> dict:
    """Обновляет каталог, если истёк TTL. Если передан target, обновляет его на месте (все ссылки на него остаются верными)."""
    with _lock:
        if force or _state["cat"] is None or time.time() - _state["at"] > TTL:
            c, src = _fetch()
            _state["at"] = time.time()
            if c is not None:
                v = _version(c)
                if v != _state["version"]:
                    _state.update(cat=c, src=src, version=v)
                else:
                    _state["src"] = src
            elif _state["cat"] is None:
                raise RuntimeError("Каталог не найден: нет seed/catalog.json и недоступен CATALOG_API_URL")
        c = _state["cat"]
        if target is not None and target.get("_version") != _state["version"]:
            target.clear()
            target.update(c)
            target["_version"] = _state["version"]
        return c


def info() -> dict:
    return dict(version=_state["version"], source=_state["src"], ttl=TTL)


def local_catalog() -> dict:
    """То, что отдаёт сам mock API: только локальные файлы (иначе при CATALOG_API_URL на себя получился бы цикл)."""
    for path in (EDITED, SEED):
        try:
            c = _read_file(path)
            if not validate(c):
                return dict(c, version=_version(c))
        except Exception:
            pass
    raise RuntimeError("seed/catalog.json не найден")


def public() -> dict:
    """Каталог для клиента: тот, что использует сервер для расчёта цен."""
    c = sync()
    return dict(c, version=_state["version"])


def prices(c: dict) -> list:
    """Плоский прайс-лист: для интеграций и проверки цен без разбора структуры."""
    out = []
    for p in c["products"]:
        if p["kind"] == "book":
            for cv in c["covers"]:
                out.append(dict(sku=f"{p['id']}:{cv['id']}", product=p["id"], title=f"{p['name']}, {cv['name'].lower()}",
                                price=round(p["from_price"] + cv["add"], 2), old_price=p.get("old_price"), per_spread=p["per_spread"], min_spreads=p["min_spreads"]))
        else:
            for f in p["formats"]:
                out.append(dict(sku=f"{p['id']}:{f['id']}", product=p["id"], title=f"{p['name']}, {f['name']}",
                                price=round(p["from_price"] * f["mult"], 2), old_price=p.get("old_price")))
    return out


def diff(old: dict, new: dict) -> list:
    """Человекочитаемый список изменений каталога (для журнала действий администратора)."""
    out = []

    def by(items, key):
        return {x[key]: x for x in items}

    def title(x):
        return x.get("name") or x.get("code") or x.get("id")

    def cmp(label, a, b, fields):
        for f, t in fields:
            if a.get(f, None if f != "active" else True) != b.get(f, None if f != "active" else True):
                out.append(f"{label}: {t} {a.get(f)} → {b.get(f)}")

    def lists(label, o_items, n_items, idk, fields):
        ob = by(o_items, idk)
        for x in n_items:
            if x[idk] not in ob:
                out.append(f"добавлено ({label}): {title(x)}")
            else:
                cmp(f"{label} «{title(x)}»", ob[x[idk]], x, fields)
        nb = by(n_items, idk)
        for x in o_items:
            if x[idk] not in nb:
                out.append(f"удалено ({label}): {title(x)}")

    pf = [("name", "название"), ("active", "включён"), ("from_price", "цена"), ("old_price", "старая цена"), ("per_spread", "за разворот"),
          ("min_spreads", "мин. разворотов"), ("lead", "срок"), ("text", "описание")]
    lists("товар", old["products"], new["products"], "id", pf)
    for p in new["products"]:
        o = by(old["products"], "id").get(p["id"])
        if o and p["kind"] == "print":
            lists(f"{p['name']}, формат", o.get("formats", []), p.get("formats", []), "id", [("name", "название"), ("mult", "множитель")])
            lists(f"{p['name']}, бумага", o.get("papers", []), p.get("papers", []), "id", [("name", "название")])
    lists("обложка", old["covers"], new["covers"], "id", [("name", "название"), ("add", "доплата"), ("active", "включена")])
    lists("доставка", old["delivery"], new["delivery"], "id", [("name", "название"), ("price", "цена"), ("eta", "срок"), ("address", "нужен адрес"), ("active", "включена")])
    lists("оплата", old["payment"], new["payment"], "id", [("name", "название"), ("active", "включена")])
    lists("промокод", old["promos"], new["promos"], "code", [("text", "описание"), ("percent", "процент"), ("free_delivery", "бесплатная доставка"), ("active", "включён")])
    return out


def normalize(c: dict) -> dict:
    """Служебные поля убираются, коды промокодов приводятся к верхнему регистру без пробелов."""
    c = {k: v for k, v in c.items() if k not in ("version", "_version", "statuses")}
    for pr in c.get("promos", []) if isinstance(c.get("promos"), list) else []:
        if isinstance(pr, dict):
            pr["code"] = str(pr.get("code", "")).strip().upper()
    return c


def edited() -> bool:
    return os.path.exists(EDITED)


def save(c: dict):
    """(версия, ошибка, список изменений). Сохраняет каталог администратора в data/catalog.json."""
    c = normalize(c)
    bad = validate(c)
    if bad:
        return "", "Каталог не принят: " + bad, []
    if GUARD:
        busy = GUARD(c)
        if busy:
            return "", busy, []
    changes = diff(local_catalog(), c)
    os.makedirs(DATA, exist_ok=True)
    tmp = EDITED + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(c, f, ensure_ascii=False, indent=1)
    os.replace(tmp, EDITED)
    sync(force=True)
    return _state["version"], "", changes


def reset():
    """Возвращает исходный каталог (seed/catalog.json): изменённый файл удаляется. Возвращает список отменённых изменений."""
    changes = diff(_read_file(SEED), local_catalog()) if edited() else []
    try:
        os.remove(EDITED)
    except OSError:
        pass
    sync(force=True)
    return changes


GUARD = None      # server.py подставляет проверку «пункт используется проектами или заказами»: она запрещает удаление
AUDIT = None      # server.py подставляет функцию журнала: AUDIT(action, obj, extra)

mock_api = Blueprint("mock_api", __name__, url_prefix="/mock-api/v1")


def _json(data, etag=None):
    if etag and request.headers.get("If-None-Match") == etag:
        return "", 304
    r = jsonify(data)
    if etag:
        r.headers["ETag"] = etag
        r.headers["Cache-Control"] = "no-cache"
    return r


@mock_api.get("/catalog")
def m_catalog():
    c = local_catalog()
    return _json(c, '"%s"' % c["version"])


@mock_api.get("/products")
def m_products():
    c = local_catalog()
    return _json(dict(items=c["products"], currency=c["currency"], version=c["version"]), '"p%s"' % c["version"])


@mock_api.get("/products/<pid>")
def m_product(pid):
    p = next((x for x in local_catalog()["products"] if x["id"] == pid), None)
    return _json(p) if p else (jsonify(error="Товар не найден"), 404)


@mock_api.get("/delivery")
def m_delivery():
    return _json(dict(items=local_catalog()["delivery"]))


@mock_api.get("/promos")
def m_promos():
    return _json(dict(items=local_catalog()["promos"]))


@mock_api.get("/prices")
def m_prices():
    c = local_catalog()
    return _json(dict(currency=c["currency"], version=c["version"], items=prices(c)))


ADMIN_CHECK = None      # server.py подставляет функцию «текущий пользователь — администратор?»


@mock_api.put("/catalog")
def m_put():
    if ADMIN_CHECK is None or not ADMIN_CHECK():
        return jsonify(error="Нужны права администратора"), 403
    ver, bad, changes = save(request.get_json(silent=True) or {})
    if bad:
        return jsonify(error=bad), 400
    if AUDIT:
        AUDIT("изменение цен (API)", f"{len(changes)} изм.", changes)
    return jsonify(ok=True, version=ver, changes=changes)
