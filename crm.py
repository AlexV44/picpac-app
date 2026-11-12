# -*- coding: utf-8 -*-
"""
Интеграция с CRM picPac: получить «портрет» клиента по API.

Режимы (переменная CRM_MODE):
  mock  (по умолчанию) — встроенная демо-CRM: тестовые клиенты и /mock-crm/v1/* внутри сервера;
  http  — настоящая CRM по REST: CRM_URL (база, например https://crm.example/api/v1) и CRM_TOKEN (Bearer);
  off   — интеграция выключена, приложение работает как раньше.

Контракт (его должна отдавать CRM, см. docs/CRM.md):
  GET {CRM_URL}/customers/lookup?email=<e-mail>   ->  200 JSON-портрет | 404, если клиента нет
  GET {CRM_URL}/customers/<id>                    ->  то же по id

Принципы:
  - CRM недоступна или тормозит: приложение не ломается (таймаут, кэш, пауза после сбоя), предложений просто нет;
  - берём только нужные поля (белый список), портрет живёт в памяти и кэшируется на несколько минут, на диск не пишется;
  - клиент без согласия на маркетинг (marketing_consent=false) персональные предложения не получает.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

MODE = os.environ.get("CRM_MODE", "mock").strip().lower()
URL = os.environ.get("CRM_URL", "").rstrip("/")
TOKEN = os.environ.get("CRM_TOKEN", "demo-crm-token")
TIMEOUT = float(os.environ.get("CRM_TIMEOUT", 2.5))
TTL = float(os.environ.get("CRM_CACHE_SECONDS", 300))
PAUSE = 30.0                       # после сбоя CRM не дёргаем 30 секунд

_LOCK = threading.Lock()
_cache: dict = {}                  # ключ -> (время, портрет | None)
_state = dict(ok=None, error="", last=0.0, fails=0, until=0.0, calls=0, hits=0)

# Белый список полей: всё остальное из ответа CRM отбрасывается.
FIELDS = ("id", "name", "segment", "since", "orders_count", "total_spent", "avg_check", "last_order_date", "last_product",
          "favorite_product", "kinds", "discount_percent", "loyalty_level", "birthday", "events", "summary", "interests",
          "marketing_consent")


def normalize(raw: dict) -> dict:
    """Приводит ответ CRM к единому виду; пропущенные поля получают безопасные значения."""
    r = {k: raw.get(k) for k in FIELDS}
    out = dict(
        id=str(r["id"] or ""), name=str(r["name"] or "")[:40], segment=str(r["segment"] or "regular")[:20],
        since=str(r["since"] or "")[:10], orders_count=int(r["orders_count"] or 0),
        total_spent=round(float(r["total_spent"] or 0), 2), avg_check=round(float(r["avg_check"] or 0), 2),
        last_order_date=str(r["last_order_date"] or "")[:10], last_product=str(r["last_product"] or "")[:20],
        favorite_product=str(r["favorite_product"] or "")[:20],
        kinds={str(k)[:10]: int(v) for k, v in (r["kinds"] or {}).items()} if isinstance(r["kinds"], dict) else {},
        discount_percent=max(0, min(30, int(r["discount_percent"] or 0))), loyalty_level=str(r["loyalty_level"] or "")[:20],
        birthday=str(r["birthday"] or "")[:5],
        events=[dict(title=str(e.get("title", ""))[:60], date=str(e.get("date", ""))[:10]) for e in (r["events"] or []) if isinstance(e, dict)][:5],
        summary=str(r["summary"] or "")[:300], interests=[str(x)[:30] for x in (r["interests"] or [])][:6],
        marketing_consent=bool(r["marketing_consent"]) if r["marketing_consent"] is not None else False)
    return out


# ---------------------------------------------------------------- демо-CRM
def _d(days: int) -> str:
    return (dt.date.today() + dt.timedelta(days=days)).isoformat()


def _demo() -> dict:
    """Три постоянных демо-клиента; даты считаются от сегодняшнего дня, поэтому предложения срабатывают всегда."""
    bd = (dt.date.today() + dt.timedelta(days=2)).strftime("%m-%d")
    return {
        "client@picpac.test": dict(
            id="C-DEMO-1", name="Анна", segment="loyal", since="2023-04-12", orders_count=7, total_spent=486.4, avg_check=69.5,
            last_order_date=_d(-26), last_product="premium", favorite_product="premium", kinds=dict(book=5, print=2),
            discount_percent=5, loyalty_level="Серебро", birthday="", marketing_consent=True,
            events=[dict(title="День рождения сына", date=_d(19))], interests=["семья", "путешествия"],
            summary="Постоянный клиент: чаще заказывает фотокниги Premium после семейных поездок и праздников."),
        "vip@picpac.test": dict(
            id="C-DEMO-2", name="Игорь", segment="vip", since="2021-09-03", orders_count=23, total_spent=2150.0, avg_check=93.5,
            last_order_date=_d(-9), last_product="classic", favorite_product="classic", kinds=dict(book=21, print=2),
            discount_percent=10, loyalty_level="Золото", birthday=bd, marketing_consent=True, events=[],
            interests=["свадьбы", "путешествия"], summary="VIP: заказывает подарочные фотокниги, ценит скорость и бесплатную доставку."),
        "sleep@picpac.test": dict(
            id="C-DEMO-3", name="Ольга", segment="sleeping", since="2022-02-18", orders_count=3, total_spent=61.8, avg_check=20.6,
            last_order_date=_d(-140), last_product="prints", favorite_product="prints", kinds=dict(print=3),
            discount_percent=0, loyalty_level="Базовый", birthday="", marketing_consent=True, events=[],
            interests=["дети"], summary="Заказывала только фотопечать, последний раз почти пять месяцев назад."),
    }


def demo_by_email(email: str):
    return _demo().get(str(email).strip().lower())


def demo_by_id(cid: str):
    for p in _demo().values():
        if p["id"] == cid:
            return p
    try:
        with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "seed", "crm_portraits.json"), encoding="utf-8") as f:
            return json.load(f).get(cid)
    except Exception:
        return None


# ---------------------------------------------------------------- запросы
def _http(path: str):
    if not URL:
        raise RuntimeError("CRM_URL не задан")
    req = urllib.request.Request(URL + path, headers={"Authorization": "Bearer " + TOKEN, "Accept": "application/json",
                                                       "X-Client": "picpac-mobile"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return json.loads(r.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise


def _fetch(email: str):
    if MODE == "http":
        raw = _http("/customers/lookup?email=" + urllib.parse.quote(email))
    else:
        raw = demo_by_email(email)
    return normalize(raw) if raw else None


def lookup(email: str):
    """Портрет клиента по e-mail или None (нет в CRM, CRM выключена или недоступна). Никогда не бросает исключений."""
    email = str(email or "").strip().lower()
    if MODE == "off" or not email:
        return None
    now = time.time()
    with _LOCK:
        c = _cache.get(email)
        if c and now - c[0] < TTL:
            _state["hits"] += 1
            return c[1]
        if now < _state["until"]:
            return c[1] if c else None
    try:
        p = _fetch(email)
        with _LOCK:
            _cache[email] = (time.time(), p)
            _state.update(ok=True, error="", last=time.time(), fails=0, calls=_state["calls"] + 1)
        return p
    except Exception as e:                      # сеть, таймаут, 5xx, битый JSON
        with _LOCK:
            _state.update(ok=False, error=type(e).__name__ + ": " + str(e)[:120], last=time.time(),
                          fails=_state["fails"] + 1, until=time.time() + PAUSE)
            return _cache[email][1] if email in _cache else None


def status() -> dict:
    with _LOCK:
        return dict(mode=MODE, url=URL if MODE == "http" else "", ok=_state["ok"], error=_state["error"],
                    calls=_state["calls"], cache_hits=_state["hits"], cached=len(_cache), fails=_state["fails"],
                    last=_state["last"])


def forget(email: str):
    """Сбросить кэш клиента (например, после выхода или удаления данных)."""
    with _LOCK:
        _cache.pop(str(email or "").strip().lower(), None)


def reset():
    with _LOCK:
        _cache.clear()
        _state.update(ok=None, error="", last=0.0, fails=0, until=0.0, calls=0, hits=0)
