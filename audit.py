# -*- coding: utf-8 -*-
"""Журнал основных операций (ОТ-04): пользователь, время (ISO с часовым поясом), действие, объект, результат.
Чувствительные поля (телефон, ключи, пароли) в журнал не попадают. Хранится в таблице audit (БД)."""
from __future__ import annotations

from datetime import datetime, timezone

import db


def log(user: str, role: str, action: str, obj: str = "", result: str = "ok", extra: dict | None = None):
    ts = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    try:
        db.run("INSERT INTO audit (ts, actor, role, action, object, result, extra) VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb)",
               (ts, user or "аноним", role or "-", action, obj, result, db.dumps(extra) if extra else None))
    except Exception as e:                      # журнал не должен ронять запрос
        print("журнал недоступен:", e)


def read(limit: int = 200) -> list:
    try:
        rows = db.run("SELECT ts, actor, role, action, object, result, extra FROM audit ORDER BY id DESC LIMIT %s", (int(limit),), many=True)
    except Exception:
        return []
    return [dict(ts=r[0], user=r[1], role=r[2], action=r[3], object=r[4], result=r[5], **({"extra": db._j(r[6])} if r[6] else {})) for r in rows]
