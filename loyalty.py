# -*- coding: utf-8 -*-
"""
Программа лояльности «Фотобаллы».

1 фотобалл = 1 руб. (BYN) скидки. Баланс не хранится отдельным числом, а считается из заказов клиента,
поэтому его нельзя «накрутить» и он всегда сходится с историей:

    баланс = приветственные + перенесённые из CRM + начисленные за выданные заказы − потраченные

Начисление (шкала подогнана под печать: фотоотпечаток стоит копейки, фотокнига десятки рублей).
Сумма = товары − скидка − оплаченное баллами, без доставки:

    10–29,99 руб.  →  1 балл        100–199,99 руб. →  12 баллов
    30–59,99 руб.  →  3 балла       200–399,99 руб. →  28 баллов
    60–99,99 руб.  →  6 баллов      от 400 руб.     →  70 баллов

Праздничные дни (8 марта, 14 и 23 февраля, Новый год, день рождения клиента из CRM): начисление ×2.
Баллы приходят, когда заказ выдан; до этого они «в ожидании».

Списание: после покупок на 30 руб.; до 10% (товары до 50 руб.), 15% (до 150 руб.) или 20% (дороже) стоимости.
Баллы не суммируются со скидкой по промокоду и персональным предложением: это правило сервер проверяет сам.
"""
from __future__ import annotations

import datetime as dt

import orders as O

WELCOME = 5
LEGACY_RATE = 0.04            # перенос истории из CRM: 4% от суммы прошлых покупок
SPEND_MIN_TOTAL = 30.0
TIERS = [(10, 30, 1), (30, 60, 3), (60, 100, 6), (100, 200, 12), (200, 400, 28), (400, None, 70)]
CAPS = [(0, 50, 10), (50, 150, 15), (150, None, 20)]
HOLIDAYS = [("02-14", "День влюблённых"), ("02-23", "23 февраля"), ("03-08", "8 марта"),
            ("12-31", "Новый год"), ("01-01", "Новый год"), ("01-02", "Новый год")]


def earn_base(amount: float) -> int:
    for lo, hi, pts in TIERS:
        if amount >= lo and (hi is None or amount < hi):
            return pts
    return 0


def holiday(today: dt.date | None = None, birthday_delta=None) -> str:
    today = today or dt.date.today()
    key = today.strftime("%m-%d")
    for k, name in HOLIDAYS:
        if k == key:
            return name
    if birthday_delta is not None and abs(birthday_delta) <= 3:
        return "День рождения"
    return ""


def earn(amount: float, today: dt.date | None = None, birthday_delta=None):
    """(баллы, название праздничного бонуса или '')."""
    base = earn_base(amount)
    h = holiday(today, birthday_delta) if base else ""
    return (base * 2 if h else base), h


def cap_pct(amount: float) -> int:
    for lo, hi, pct in CAPS:
        if amount >= lo and (hi is None or amount < hi):
            return pct
    return 10


def _finished(o: dict) -> bool:
    return O.stage_of(o) >= len(O.STATUSES) - 1


def account(orders: list, portrait: dict | None = None) -> dict:
    earned = pending = spent = 0
    paid = 0.0
    hist = []
    for o in sorted(orders, key=lambda x: x["created"]):
        pr = o.get("price") or {}
        sp = int(pr.get("points", 0) or 0)
        lo = o.get("loyalty") or {}
        e = int(lo.get("earned", 0) or 0)
        spent += sp
        done = _finished(o)
        if done:
            earned += e
            paid += max(0.0, pr.get("items", 0) - pr.get("discount", 0) - sp)
        else:
            pending += e
        if e or sp:
            hist.append(dict(code=o["code"], ts=o["created"], earned=e, spent=sp, done=done, bonus=lo.get("bonus", "")))
    legacy = int(round((portrait or {}).get("total_spent", 0) * LEGACY_RATE)) if portrait else 0
    lifetime = paid + float((portrait or {}).get("total_spent", 0) or 0)
    balance = max(0, WELCOME + legacy + earned - spent)
    return dict(balance=balance, pending=pending, earned=earned, spent=spent, welcome=WELCOME, legacy=legacy,
                lifetime=round(lifetime, 2), can_spend=lifetime >= SPEND_MIN_TOTAL, spend_min=SPEND_MIN_TOTAL,
                history=hist[::-1][:20])


def max_spend(items_sum: float, discount: float, acct: dict) -> int:
    """Сколько баллов можно списать в этом заказе (0, если нельзя). Баллы не суммируются со скидками."""
    if discount > 0 or not acct["can_spend"] or items_sum <= 0:
        return 0
    cap = int(items_sum * cap_pct(items_sum) / 100)
    return max(0, min(acct["balance"], cap))


def rules() -> dict:
    """Правила для окна «Как это работает» (клиент рисует таблицы из этих данных, а не из зашитого текста)."""
    return dict(welcome=WELCOME, rate=1, spend_min=SPEND_MIN_TOTAL,
                tiers=[dict(lo=lo, hi=hi, pts=p) for lo, hi, p in TIERS],
                caps=[dict(lo=lo, hi=hi, pct=c) for lo, hi, c in CAPS],
                holidays=["8 марта", "14 февраля", "23 февраля", "Новый год", "День рождения"])
