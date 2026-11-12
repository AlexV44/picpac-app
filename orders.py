# -*- coding: utf-8 -*-
"""
Заказы и отслеживание по трек-коду. Хранилище: таблица orders (БД).
Статус заказа вычисляется по времени: каждый этап занимает STEP секунд (демо-время),
плюс кнопка «Следующий статус» для показа без ожидания.
"""
from __future__ import annotations

import json
import os
import random
import re
import threading
import time
from datetime import datetime, timezone

import catalog_api
import db

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.environ.get("PICPAC_DATA", os.path.join(HERE, "data"))
ORDERS_FILE = os.path.join(DATA, "orders.json")
PDF_DIR = os.path.join(DATA, "orders")
STEP_SECONDS = float(os.environ.get("TRACK_STEP_SECONDS", 25))
_LOCK = threading.RLock()
ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"      # без похожих символов 0/O, 1/I

# Каталог и цены грузятся из отдельного источника (catalog_api: внешний/mock API → data/catalog.json → seed/catalog.json),
# а не зашиты в код. Словарь обновляется на месте, поэтому все ссылки на O.CATALOG всегда видят актуальные цены.
CATALOG: dict = {}
catalog_api.sync(CATALOG, force=True)


def on(x: dict) -> bool:
    """Пункт каталога включён (по умолчанию да). Отключённые не предлагаются новым покупателям, но старые заказы и проекты работают."""
    return x.get("active", True) is not False


def first(key: str) -> dict:
    """Первый включённый пункт раздела каталога (умолчание вместо зашитых id)."""
    items = CATALOG[key]
    return next((x for x in items if on(x)), items[0])


def product(pid: str) -> dict:
    return next((p for p in CATALOG["products"] if p["id"] == pid), None) or first("products")


STATUSES = [
    dict(id="accepted", title="Заказ принят", text="Мы получили макет и данные для доставки.", icon="📥"),
    dict(id="paid", title="Оплата подтверждена", text="Оплата прошла, заказ запущен в работу.", icon="💳"),
    dict(id="ai_check", title="ИИ проверяет файлы", text="Проверяем резкость, разрешение и цвета каждого кадра.", icon="🤖"),
    dict(id="printing", title="Печать разворотов", text="Печатаем страницы на фотобумаге.", icon="🖨️"),
    dict(id="binding", title="Переплёт и обложка", text="Склеиваем блок и вставляем в обложку.", icon="📚"),
    dict(id="qc", title="Контроль качества", text="Мастер проверяет книгу вручную.", icon="🔍"),
    dict(id="handoff", title="Передан в доставку", text="Упаковали и передали службе доставки.", icon="📦"),
    dict(id="transit", title="В пути", text="Книга едет к вам.", icon="🚚"),
    dict(id="done", title="Готов к получению", text="Можно забирать. Приятного просмотра!", icon="🎉"),
]


GROUPS = {'accepted': 'Принят', 'paid': 'Принят', 'ai_check': 'Модерация', 'printing': 'Производство', 'binding': 'Производство', 'qc': 'Производство', 'handoff': 'Передан в доставку', 'transit': 'Передан в доставку', 'done': 'Завершён'}
GROUP_ORDER = ["Принят", "Модерация", "Производство", "Передан в доставку", "Завершён"]


def unit_price(prod_id: str, params: dict, spreads: int = 0) -> float:
    """Цена одной штуки: книга (за экземпляр) или одного отпечатка."""
    pr = product(prod_id)
    params = params or {}
    if pr["kind"] == "book":
        cov = next((x for x in CATALOG["covers"] if x["id"] == params.get("cover")), None) or first("covers")
        return round(pr["from_price"] + pr["per_spread"] * max(0, spreads - pr["min_spreads"]) + cov["add"], 2)
    fm = next((x for x in pr["formats"] if x["id"] == params.get("format")), pr["formats"][0])
    return round(pr["from_price"] * fm["mult"], 2)


def _prod_of(i: dict) -> dict:
    """Товар строки заказа. Если админ потом удалил его из каталога, берём то, что сохранено в самом заказе."""
    p = next((x for x in CATALOG["products"] if x["id"] == i.get("product")), None)
    return p or dict(name=i.get("title", "Товар"), kind=i.get("kind") or ("book" if i.get("spreads") else "print"))


def delivery_of(did: str):
    """Способ доставки для нового заказа (отключённые не принимаются)."""
    return next((x for x in CATALOG["delivery"] if x["id"] == did and on(x)), None)


def promo_of(code: str):
    c = str(code or "").strip().upper()
    return next((p for p in CATALOG["promos"] if p["code"] == c and on(p)), None)


def totals(items_sum: float, delivery: str, promo: str = "", perk: dict | None = None, points: int = 0) -> dict:
    """Формула: итого = товары − скидка + доставка. Скидка = наибольший из процентов (промокод или персональное
    предложение из CRM), проценты не суммируются; бесплатная доставка действует от промокода FREESHIP
    или персонального предложения. Округление до копеек. Валюта: руб. (BYN)."""
    dl = delivery_of(delivery) or first("delivery")
    pr = promo_of(promo)
    pp = pr["percent"] if pr else 0
    op = perk["percent"] if perk else 0
    pct = max(pp, op)
    disc = round(items_sum * pct / 100, 2)
    label = ("Скидка по промокоду " + pr["code"]) if pp and pp >= op else (perk["label"] if op else "")
    free = bool((pr and pr["free_delivery"]) or (perk and perk.get("free_delivery")))
    dprice = 0.0 if free else dl["price"]
    pts = max(0, min(int(points or 0), int(items_sum - disc)))       # фотобаллы: 1 балл = 1 руб., не больше стоимости товаров
    return dict(items=round(items_sum, 2), discount=disc, discount_label=label, delivery=dprice, points=pts,
                total=round(items_sum - disc - pts + dprice, 2), promo=pr["code"] if pr else "", perk=bool(perk and (op or perk.get("free_delivery"))),
                currency=CATALOG["currency"])


def _row(r):
    return db._j(r[0])


def _put(o: dict):
    """Сохраняет заказ целиком (смена статуса и т. п.)."""
    db.run("UPDATE orders SET data = %s::jsonb WHERE code = %s", (db.dumps(o), o["code"]))


def insert_raw(o: dict):
    """Прямая вставка готового заказа (генератор демо-данных)."""
    db.run("INSERT INTO orders (code, client, idem_key, data, created) VALUES (%s, %s, %s, %s::jsonb, %s) ON CONFLICT (code) DO NOTHING",
           (o["code"], o.get("client", ""), o.get("key", ""), db.dumps(o), o["created"]))


def new_code(existing=()) -> str:
    while True:
        raw = "".join(random.SystemRandom().choice(ALPHABET) for _ in range(8))
        code = f"PP-{raw[:4]}-{raw[4:]}"
        if code not in existing and not db.run("SELECT 1 FROM orders WHERE code = %s", (code,), one=True):
            return code


def norm_code(s: str) -> str:
    s = re.sub(r"[^A-Za-z0-9]", "", str(s or "")).upper()
    if s.startswith("PP"):
        s = s[2:]
    return f"PP-{s[:4]}-{s[4:8]}" if len(s) == 8 else ""


def validate_contact(j: dict):
    name = str(j.get("name", "")).strip()
    phone = re.sub(r"[^\d+]", "", str(j.get("phone", "")))
    if len(name) < 2:
        return "Укажите имя"
    if len(re.sub(r"\D", "", phone)) < 9:
        return "Укажите телефон (не меньше 9 цифр)"
    dl = delivery_of(j.get("delivery"))
    if not dl:
        return "Выберите способ получения"
    if j.get("payment") and not any(x["id"] == j["payment"] and on(x) for x in CATALOG["payment"]):
        return "Выберите способ оплаты"
    if dl["address"] and len(str(j.get("address", "")).strip()) < 6:
        return "Укажите адрес или отделение"
    return ""


def create_order(j: dict, items: list, client: str = "") -> dict:
    """items: [dict(id, product, title, spreads, photos, qty, unit, params)].
    Идемпотентность (МП-05): повторная отправка с тем же ключом key возвращает уже созданный заказ
    (гарантирует и БД: уникальный индекс по (client, idem_key))."""
    with _LOCK:
        key = str(j.get("key", ""))[:60]
        client = str(client)[:40]
        if key:
            r = db.run("SELECT data FROM orders WHERE client = %s AND idem_key = %s", (client, key), one=True)
            if r:
                o = _row(r)
                o["_duplicate"] = True
                return o
        code = new_code()
        dl = delivery_of(j["delivery"])
        s = sum(round(i["unit"] * i["qty"], 2) for i in items)
        o = dict(code=code, created=time.time(), client=client, name=str(j["name"]).strip()[:60], key=key,
                 phone=re.sub(r"[^\d+]", "", str(j["phone"]))[:20], delivery=j["delivery"], delivery_name=dl["name"],
                 address=str(j.get("address", "")).strip()[:160] if dl["address"] else "", comment=str(j.get("comment", "")).strip()[:300],
                 payment=j.get("payment") or first("payment")["id"], items=items, price=totals(s, j["delivery"], j.get("promo", ""), j.get("_perk"), j.get("_points", 0)), offset=0, eta=dl["eta"])
        if j.get("_loy"):
            o["loyalty"] = dict(j["_loy"])
        insert_raw(o)
        return o


def all_orders():
    return [_row(r) for r in db.run("SELECT data FROM orders ORDER BY created DESC", many=True)]


def delete_client(client: str) -> int:
    if not client:
        return 0
    return db.run("DELETE FROM orders WHERE client = %s", (client,))


def set_stage(code: str, stage: int):
    with _LOCK:
        o = get_order(code)
        if not o:
            return None
        cur = stage_of(o)
        o["offset"] = o.get("offset", 0) + (max(0, min(len(STATUSES) - 1, stage)) - cur)
        _put(o)
        return o


def orders_of(client: str):
    if not client:
        return []
    return [_row(r) for r in db.run("SELECT data FROM orders WHERE client = %s ORDER BY created DESC", (client,), many=True)]


def get_order(code: str):
    r = db.run("SELECT data FROM orders WHERE code = %s", (norm_code(code),), one=True)
    return _row(r) if r else None


def move_client(old: str, new: str) -> int:
    """Гостевые заказы переходят к аккаунту после входа или регистрации."""
    with _LOCK:
        n = 0
        for r in db.run("SELECT data FROM orders WHERE client = %s", (old,), many=True):
            o = _row(r)
            o["client"] = new
            db.run("UPDATE orders SET client = %s, data = %s::jsonb WHERE code = %s", (new, db.dumps(o), o["code"]))
            n += 1
        return n


def stage_of(o: dict, now=None) -> int:
    now = now or time.time()
    return int(min(len(STATUSES) - 1, o.get("offset", 0) + max(0, (now - o["created"]) // STEP_SECONDS)))


def advance(code: str):
    with _LOCK:
        o = get_order(code)
        if not o:
            return None
        if stage_of(o) < len(STATUSES) - 1:
            o["offset"] = o.get("offset", 0) + 1
            _put(o)
        return o


def public_view(o: dict) -> dict:
    """Что видит клиент на странице отслеживания (без телефона, имя обрезано)."""
    st = stage_of(o)
    steps = []
    for i, s in enumerate(STATUSES):
        reached = i <= st
        t = None
        if reached:
            t = min(time.time(), o["created"] + max(0, i - o.get("offset", 0)) * STEP_SECONDS)
        steps.append(dict(id=s["id"], title=s["title"], text=s["text"], icon=s["icon"],
                          state="done" if i < st else "current" if i == st else "todo", at=t))
    nm = o["name"]
    items = [dict(id=i.get("id"), product=i["product"], product_name=i.get("product_name") or _prod_of(i)["name"], title=i["title"],
                  spreads=i.get("spreads", 0), photos=i.get("photos", 0), qty=i["qty"], unit=i["unit"],
                  sum=round(i["unit"] * i["qty"], 2), kind=i.get("kind") or _prod_of(i)["kind"], thumb=i.get("id")) for i in o["items"]]
    n = sum(i["qty"] for i in items)
    return dict(code=o["code"], created=o["created"], created_iso=datetime.fromtimestamp(o["created"], timezone.utc).astimezone().isoformat(timespec="seconds"), groups=GROUP_ORDER, group=GROUPS[STATUSES[st]["id"]], customer=nm.split()[0] if nm else "", loyalty=o.get("loyalty"), items=items, count=n,
                price=o["price"], delivery_name=o["delivery_name"], eta=o["eta"],
                address=o["address"],
                stage=st, steps=steps, finished=st == len(STATUSES) - 1, status_title=STATUSES[st]["title"],
                status_icon=STATUSES[st]["icon"], progress=round(st / (len(STATUSES) - 1), 3),
                step_seconds=STEP_SECONDS,
                pdfs=[dict(id=i.get("id"), title=i["title"]) for i in o["items"]
                      if os.path.exists(os.path.join(PDF_DIR, o["code"], f"{i.get('id')}.pdf"))])
