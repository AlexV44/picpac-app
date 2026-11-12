# -*- coding: utf-8 -*-
"""
Чат поддержки: сообщения клиента, автоответ помощника (FAQ + статус заказа по коду) и ответы оператора.
Обращение можно связать с конкретным заказом (поле order). Хранилище: таблица support_threads (БД).
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
import uuid

import db
import orders as O

_LOCK = threading.RLock()


def _get(uid: str):
    r = db.run("SELECT data FROM support_threads WHERE uid = %s", (uid,), one=True)
    return db._j(r[0]) if r else None


def _put(uid: str, t: dict):
    db.run("INSERT INTO support_threads (uid, data, updated) VALUES (%s, %s::jsonb, %s) "
           "ON CONFLICT (uid) DO UPDATE SET data = EXCLUDED.data, updated = EXCLUDED.updated",
           (uid, db.dumps(t), t.get("updated", time.time())))


FAQ = [
    (r"достав|курьер|европочт|самовывоз|получ", "Доставка: самовывоз из пункта picPac бесплатно (3–5 дней), Европочта 5,50 руб. (4–7 дней), курьер 9,00 руб. (3–5 дней). Способ выбирается при оформлении."),
    (r"оплат|карт|деньг|списан", "В этом прототипе деньги не списываются: оплата картой онлайн имитируется, либо выберите «При получении»."),
    (r"промокод|скидк|купон", "Тестовые промокоды: PICPAC10 (−10% на товары) и FREESHIP (бесплатная доставка). Введите промокод на экране оформления."),
    (r"разрешен|качеств|размыт|dpi|резк", "Для печати нужно хорошее разрешение: для книги не менее ~150 dpi в своём слоте. Если фото помечено красным, замените его или нажмите «Спросить у ИИ про это фото»."),
    (r"срок|долго|когда|сколько дн", "Сроки: фотопечать 1–2 дня, фотокниги 5–7 дней плюс доставка. Точный этап виден в разделе «Заказы»."),
    (r"формат|jpeg|jpg|png|heic|загруз", "Принимаются JPEG и PNG до 25 МБ. HEIC, WebP и GIF пока не поддерживаются: сохраните снимок как JPEG."),
    (r"удал|данн", "Удалить фото можно в карточке фото, а все ваши данные — в настройках: «Удалить мои данные»."),
    (r"отмен|вернуть|возврат", "Отмена и возврат в прототипе оформляются через обращение: напишите номер заказа, оператор ответит здесь же."),
]
HUMAN = re.compile(r"оператор|человек|менеджер|жалоб|претенз|не работает|ошибк", re.I)


def thread(uid: str) -> dict:
    with _LOCK:
        return _get(uid) or dict(messages=[], status="new")


def _status_text(code: str) -> str:
    o = O.get_order(code)
    if not o:
        return ""
    v = O.public_view(o)
    return f"Заказ {v['code']}: сейчас «{v['status_title']}», срок после оплаты {v['eta']}, сумма {v['price']['total']:.2f} {O.CATALOG['currency']}."


def bot_reply(text: str, uid: str, order: str = "") -> tuple[str, bool]:
    """Возвращает (ответ, нужен_оператор). Не выдумывает факты: без совпадений передаёт оператору."""
    m = re.search(r"PP-?[A-Z0-9]{4}-?[A-Z0-9]{4}", text.upper())
    code = O.norm_code(m.group(0)) if m else O.norm_code(order)
    parts = []
    if code:
        st = _status_text(code)
        if st:
            parts.append(st)
    for rx, ans in FAQ:
        if re.search(rx, text, re.I):
            parts.append(ans)
            break
    esc = bool(HUMAN.search(text)) or not parts
    if parts and not esc:
        return " ".join(parts), False
    if parts:
        return " ".join(parts) + " Передала обращение оператору: ответ появится здесь.", True
    return "Не нашла готового ответа, поэтому передала обращение оператору. Он ответит здесь же. Если вопрос про заказ, укажите его код.", True


def add_message(uid: str, text: str, order: str = "", who: str = "user", name: str = "") -> dict:
    with _LOCK:
        t = _get(uid) or dict(messages=[], status="new", name=name or "Клиент")
        if name:
            t["name"] = name
        msg = dict(id=uuid.uuid4().hex[:8], from_=who, text=text[:1000], ts=time.time(), order=O.norm_code(order) or "")
        t["messages"].append(msg)
        t["messages"] = t["messages"][-200:]
        t["status"] = "answered" if who == "operator" else t["status"]
        t["updated"] = msg["ts"]
        _put(uid, t)
        return msg


def set_status(uid: str, status: str):
    with _LOCK:
        t = _get(uid)
        if t:
            t["status"] = status
            _put(uid, t)


def all_threads() -> list:
    rows = db.run("SELECT uid, data FROM support_threads", many=True)
    out = [dict(uid=k, name=db._j(v).get("name", ""), status=db._j(v).get("status", "new"), updated=db._j(v).get("updated", 0),
                last=(db._j(v)["messages"][-1]["text"] if db._j(v)["messages"] else ""), count=len(db._j(v)["messages"])) for k, v in rows]
    return sorted(out, key=lambda x: -x["updated"])


def for_order(uid: str, code: str) -> list:
    code = O.norm_code(code)
    return [m for m in thread(uid)["messages"] if m.get("order") == code]


def delete_user(uid: str):
    db.run("DELETE FROM support_threads WHERE uid = %s", (uid,))
