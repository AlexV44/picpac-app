# -*- coding: utf-8 -*-
"""
Персональные предложения по портрету клиента из CRM.

Это прозрачные правила с баллами (а не «чёрный ящик»): у каждого предложения есть причина («почему вы это видите»),
срок и расчёт скидки. Скидку всегда пересчитывает и проверяет сервер, клиентское приложение процент не присылает.

Предложения:
  loyalty   личная скидка из CRM (уровень лояльности)
  birthday  день рождения клиента: окно от −3 до +10 дней
  event     событие из CRM (например, день рождения ребёнка в ближайшие 30 дней)
  winback   «скучаем»: последний заказ больше 90 дней назад
  vipship   бесплатная доставка для VIP
  crosssell докупить то, чего клиент ещё не заказывал (книга -> печать, печать -> книга)
  reorder   повторить любимый продукт
Правило скидок: проценты не суммируются, действует наибольший; бесплатная доставка добавляется отдельно.
Промокод с процентом не суммируется с персональной скидкой: берётся лучшая (см. orders.totals).
"""
from __future__ import annotations

import datetime as dt

import orders as O

WINBACK_DAYS = 90
BIRTHDAY_BEFORE, BIRTHDAY_AFTER = 3, 10
EVENT_WINDOW = 30
VALID_DAYS = 14


def _date(s: str):
    try:
        return dt.date.fromisoformat(s[:10])
    except Exception:
        return None


def _pname(pid: str) -> str:
    p = O.product(pid)
    return p["name"] if p else ""


def _fmt(d: dt.date) -> str:
    return d.strftime("%d.%m.%Y")


def _birthday_delta(bd: str, today: dt.date):
    """Дней до ближайшего дня рождения (отрицательно: уже прошёл недавно)."""
    try:
        m, d = int(bd[:2]), int(bd[3:5])
    except Exception:
        return None
    best = None
    for y in (today.year - 1, today.year, today.year + 1):
        try:
            delta = (dt.date(y, m, d) - today).days
        except ValueError:
            continue
        if best is None or abs(delta) < abs(best):
            best = delta
    return best


def build(portrait, today: dt.date | None = None):
    """Список предложений для клиента, отсортированный по важности. Пустой, если клиента нет или нет согласия."""
    if not portrait or not portrait.get("marketing_consent"):
        return []
    today = today or dt.date.today()
    out, kinds = [], portrait.get("kinds") or {}
    name = portrait.get("name") or ""
    last = _date(portrait.get("last_order_date", ""))
    days_since = (today - last).days if last else None
    until = _fmt(today + dt.timedelta(days=VALID_DAYS))

    def add(id_, kind, title, text, why, score, percent=0, free=False, badge="", cta=None, valid="", short=""):
        out.append(dict(id=id_, kind=kind, title=title, text=text, why=why, score=score, percent=percent, free_delivery=free,
                        badge=badge, cta=cta, valid_until=valid, short=short, checkout=bool(percent or free)))

    # день рождения
    bd = _birthday_delta(portrait.get("birthday", ""), today)
    if bd is not None and -BIRTHDAY_AFTER <= bd <= BIRTHDAY_BEFORE:
        pct = 20 if portrait.get("segment") == "vip" else 15
        add("birthday", "birthday", "Подарок ко дню рождения" + (f", {name}" if name else ""),
            f"−{pct}% на любой заказ до {_fmt(today + dt.timedelta(days=VALID_DAYS))}. Загляните за фотокнигой о лучшем в этом году.",
            "У вас скоро день рождения" if bd >= 0 else "С прошедшим днём рождения", 90, percent=pct, badge=f"−{pct}%", short="Скидка ко дню рождения",
            cta=dict(label="Собрать фотокнигу", product=portrait.get("favorite_product") if (O.product(portrait.get("favorite_product", "")) or {}).get("kind") == "book" else "classic"),
            valid=until)

    # событие
    for ev in portrait.get("events", []):
        d = _date(ev.get("date", ""))
        if d and 0 <= (d - today).days <= EVENT_WINDOW:
            add("event", "event", f"К празднику: {ev['title'].lower()}", f"−10% на фотокнигу, чтобы собрать её к {_fmt(d)}. Классический разворот займёт пару минут.",
                f"В вашем календаре: {ev['title'].lower()} через {(d - today).days} дн.", 80, percent=10, badge="−10%", short="Скидка к празднику",
                cta=dict(label="Начать фотокнигу", product="premium"), valid=_fmt(d))
            break

    # возвращение
    if days_since is not None and days_since > WINBACK_DAYS:
        add("winback", "winback", "Мы скучаем по вашим фото", f"−12% на следующий заказ до {until}. Ваши снимки ждут своего альбома.",
            f"Последний заказ был {days_since} дн. назад", 85, percent=12, badge="−12%", short="Скидка «Мы скучаем»",
            cta=dict(label="Выбрать услугу", product=portrait.get("favorite_product") or "classic"), valid=until)

    # личная скидка и перки
    pct = portrait.get("discount_percent", 0)
    if pct > 0:
        lvl = portrait.get("loyalty_level") or "постоянный клиент"
        add("loyalty", "loyalty", f"Ваша скидка −{pct}%", f"Уровень «{lvl}»: скидка применится к заказу автоматически.",
            f"Вы с нами с {portrait.get('since', '')[:4] or 'давних пор'} года, заказов: {portrait.get('orders_count', 0)}" if portrait.get("since") else f"Заказов: {portrait.get('orders_count', 0)}",
            60 + pct, percent=pct, badge=f"−{pct}%", short="Ваша скидка")
    if portrait.get("segment") == "vip":
        add("vipship", "vipship", "Бесплатная доставка для вас", "Для VIP-клиентов доставка бесплатна на любой заказ.",
            "Статус VIP", 70, free=True, badge="доставка 0", short="бесплатная доставка")

    # кросс-продажи
    pf = portrait.get("favorite_product", "")
    if kinds.get("book", 0) and not kinds.get("print", 0):
        add("crosssell_print", "crosssell", "Лучшие кадры на бумаге", "Распечатайте любимые снимки из ваших альбомов. Формат 10×15 уже от 0,65 руб. за снимок.",
            "Вы заказывали фотокниги, но ещё не печатали отдельные фото", 50, cta=dict(label="Заказать печать", product="digital"))
    elif kinds.get("print", 0) and not kinds.get("book", 0):
        add("crosssell_book", "crosssell", "Соберите фото в книгу", "Из тех снимков, что вы печатали, получится красивая фотокнига: ИИ сам подберёт кадры и разложит по разворотам.",
            "Вы заказывали фотопечать, а фотокнигу ещё нет", 55, cta=dict(label="Попробовать фотокнигу", product="lite"))
    if pf and O.product(pf) and O.product(pf)["kind"] == "book" and days_since is not None and days_since <= WINBACK_DAYS and portrait.get("orders_count", 0) >= 3:
        add("reorder", "reorder", f"Ещё одна книга: {_pname(pf)}", "Новый сюжет, тот же формат, который вам понравился. Цена считается так же, как в прошлый раз.",
            f"Вы чаще всего заказываете «{_pname(pf)}»", 45, cta=dict(label="Начать проект", product=pf))
    out.sort(key=lambda o: -o["score"])
    return out


def perk(portrait, today: dt.date | None = None):
    """Что применяется в корзине автоматически: наибольший процент + бесплатная доставка, с подписью для чека."""
    offs = [o for o in build(portrait, today) if o["checkout"]]
    if not offs:
        return None
    best = max(offs, key=lambda o: o["percent"])
    pct = best["percent"] if best["percent"] else 0
    free = any(o["free_delivery"] for o in offs)
    label = best["short"] if pct else ""
    return dict(percent=pct, free_delivery=free, label=label, ids=[o["id"] for o in offs if (o is best or o["free_delivery"])])


def summary(portrait):
    """Что можно показать самому клиенту: без финансовых деталей и внутренних тегов."""
    if not portrait:
        return None
    return dict(name=portrait["name"], level=portrait["loyalty_level"], orders=portrait["orders_count"])
