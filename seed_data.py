# -*- coding: utf-8 -*-
"""
Синтетические данные для демонстрации (ТЗ, раздел 8). Никаких реальных людей и телефонов.
  python seed_data.py            -> seed/catalog.json, seed/orders.csv (600 строк), seed/support.csv (320 обращений), seed/customers.csv (240)
  python seed_data.py --load     -> дополнительно загружает 40 последних заказов в data/orders.json (для админ-панели)
Данные детерминированы (random.seed), поэтому набор можно воссоздать без секретов.
"""
import csv
import json
import os
import random
import sys
import time

import orders as O

random.seed(2026)
HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "seed")
os.makedirs(OUT, exist_ok=True)

CHANNELS = ["сайт", "мобильное приложение", "пункт приёма", "соцсети"]
STATUS_IDS = [s["id"] for s in O.STATUSES]
FIRST = ["Анна", "Иван", "Мария", "Пётр", "Ольга", "Дмитрий", "Елена", "Артём", "Светлана", "Николай"]


def main():
    with open(os.path.join(OUT, "catalog.json"), "w", encoding="utf-8") as f:
        json.dump({k: v for k, v in O.CATALOG.items() if k != "_version"}, f, ensure_ascii=False, indent=1)
    customers = [dict(id=f"C{i:04d}", name=random.choice(FIRST), channel=random.choice(CHANNELS), city=random.choice(["Минск", "Брест", "Гомель", "Гродно", "Витебск", "Могилёв"])) for i in range(1, 241)]
    with open(os.path.join(OUT, "customers.csv"), "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(customers[0])); w.writeheader(); w.writerows(customers)
    rows, now = [], time.time()
    for i in range(1, 601):
        p = random.choice(O.CATALOG["products"])
        qty = random.choice([1, 1, 1, 2, 3]) if p["kind"] == "book" else random.choice([10, 20, 30, 50])
        unit = O.unit_price(p["id"], dict(cover=random.choice(["hard", "soft"]), format="10x15"), random.choice([5, 8, 12, 20]))
        created = now - random.randint(0, 120 * 86400)
        dl = random.choice(O.CATALOG["delivery"])
        rows.append(dict(order_id=f"PP-{i:06d}", customer=random.choice(customers)["id"], product=p["id"], qty=qty, unit_price=unit,
                         sum=round(unit * qty, 2), currency="BYN", delivery=dl["id"], channel=random.choice(CHANNELS),
                         status=random.choice(STATUS_IDS), created_at=time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(created)),
                         on_time=random.random() > 0.08, defect=random.random() < 0.03))
    with open(os.path.join(OUT, "orders.csv"), "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0])); w.writeheader(); w.writerows(rows)

    # портреты клиентов для демо-CRM (контракт docs/CRM.md): считаются из тех же заказов
    portraits, by = {}, {}
    for r in rows:
        by.setdefault(r["customer"], []).append(r)
    for c in customers:
        rs = sorted(by.get(c["id"], []), key=lambda r: r["created_at"])
        kinds = {}
        for r in rs:
            k = O.product(r["product"])["kind"]
            kinds[k] = kinds.get(k, 0) + 1
        spent = round(sum(r["sum"] for r in rs), 2)
        favs = {}
        for r in rs:
            favs[r["product"]] = favs.get(r["product"], 0) + 1
        fav = max(favs, key=favs.get) if favs else ""
        last = rs[-1]["created_at"][:10] if rs else ""
        lvl, disc = ("Золото", 10) if spent > 400 else ("Серебро", 5) if spent > 150 else ("Базовый", 0)
        portraits[c["id"]] = dict(id=c["id"], name=c["name"], segment="vip" if spent > 400 else ("new" if len(rs) <= 1 else "regular"),
                                  since=rs[0]["created_at"][:10] if rs else "", orders_count=len(rs), total_spent=spent,
                                  avg_check=round(spent / len(rs), 2) if rs else 0, last_order_date=last,
                                  last_product=rs[-1]["product"] if rs else "", favorite_product=fav, kinds=kinds,
                                  discount_percent=disc, loyalty_level=lvl, birthday="", events=[], interests=[],
                                  marketing_consent=random.random() > 0.1,
                                  summary=f"Заказов: {len(rs)}, на сумму {spent} BYN." + (f" Чаще всего: {O.product(fav)['name']}." if fav else ""))
    with open(os.path.join(OUT, "crm_portraits.json"), "w", encoding="utf-8") as f:
        json.dump(portraits, f, ensure_ascii=False, indent=1)
    topics = ["доставка", "качество печати", "срок", "оплата", "изменение заказа", "возврат"]
    with open(os.path.join(OUT, "support.csv"), "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f); w.writerow(["ticket_id", "customer", "order_id", "topic", "channel", "sla_hours", "resolved"])
        for i in range(1, 321):
            w.writerow([f"T{i:05d}", random.choice(customers)["id"], random.choice(rows)["order_id"], random.choice(topics), random.choice(CHANNELS), random.choice([4, 8, 24]), random.random() > 0.1])
    print("готово:", OUT, "(каталог, 240 клиентов с портретами CRM, 600 заказов, 320 обращений)")
    if "--load" in sys.argv:
        for r in sorted(rows, key=lambda r: r["created_at"])[-40:]:
            code = O.new_code()
            p = O.product(r["product"])
            item = dict(id=None, product=p["id"], title=p["name"], spreads=8 if p["kind"] == "book" else 0, photos=r["qty"], qty=r["qty"], unit=r["unit_price"], params={})
            O.insert_raw(dict(code=code, created=time.time() - random.randint(0, 20 * 86400), client="seed", name="Тестовый Клиент", key="",
                           phone="+375000000000", delivery=r["delivery"], delivery_name=O.delivery_of(r["delivery"])["name"], address="", comment="",
                           payment="online", items=[item], price=O.totals(r["sum"], r["delivery"]), offset=0, eta=O.delivery_of(r["delivery"])["eta"]))
        print("загружено 40 заказов в базу данных")


if __name__ == "__main__":
    main()
