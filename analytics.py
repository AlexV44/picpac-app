# -*- coding: utf-8 -*-
"""
Аналитика воронки: каталог → проект → фото → корзина → оформление → заказ.
Хранится только обезличенный id пользователя и название шага (никаких фото и текстов).
Воронка считается по уникальным пользователям за период: сколько дошло до каждого шага и конверсия от предыдущего.
"""
from __future__ import annotations

import time

import db

STEPS = [("catalog", "Открыли каталог"), ("project", "Создали проект"), ("photos", "Загрузили фото"),
         ("cart", "Добавили в корзину"), ("checkout", "Открыли оформление"), ("order", "Оформили заказ")]
KNOWN = {k for k, _ in STEPS}


def track(uid: str, ev: str):
    if not uid or ev not in KNOWN:
        return
    try:
        db.run("INSERT INTO funnel_events (ts, uid, ev) VALUES (%s, %s, %s)", (time.time(), str(uid)[:40], ev))
    except Exception as e:                       # аналитика не должна ронять запрос
        print("аналитика недоступна:", e)


def funnel(days: int = 30) -> list:
    since = time.time() - days * 86400
    try:
        rows = db.run("SELECT ev, COUNT(DISTINCT uid) FROM funnel_events WHERE ts >= %s GROUP BY ev", (since,), many=True)
    except Exception:
        rows = []
    n = {r[0]: int(r[1]) for r in rows}
    out, prev = [], None
    top = max([n.get(k, 0) for k, _ in STEPS] + [1])
    for k, title in STEPS:
        v = n.get(k, 0)
        out.append(dict(id=k, title=title, users=v, share=round(v / top * 100), conv=(round(v / prev * 100) if prev else None)))
        prev = v
    return out


def forget(uid: str):
    try:
        db.run("DELETE FROM funnel_events WHERE uid = %s", (uid,))
    except Exception:
        pass
