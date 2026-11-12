# -*- coding: utf-8 -*-
"""
Готовые шаблоны альбомов: стиль, раскладка разворотов и тексты уже подобраны, остаётся поставить свои фото и поправить подписи.
Ручной режим (manual_book) начинается с чистого альбома и полной свободы оформления.
"""
from __future__ import annotations

import random

import ai_core as A

# (раскладка, заголовок, подпись | цитата)
TL, HG, TP, FB, ST3, G4, M6, QR = "title_left", "hero_right_grid", "two_pages", "full_bleed", "stack_three", "grid4", "mosaic6", "quote_right"

TEMPLATES = [
    dict(id="birthday", name="День рождения", emoji="🎂", theme="birthday", title="С ДНЁМ РОЖДЕНИЯ!", subtitle="Лучший день в году",
         text="Конфетти, воздушные шары и яркие рамки: от торта до подарков.",
         spreads=[(TL, "Герой дня", "Имя и возраст"), (HG, "", ""), (QR, "Загадай желание", ""), (M6, "", ""), (ST3, "", ""), (G4, "", ""), (FB, "", ""), (QR, "Спасибо, что вы с нами!", "")]),
    dict(id="wedding", name="Свадьба", emoji="💍", theme="wedding", title="НАША СВАДЬБА", subtitle="Дата · Город",
         text="Золотые рамки и нежная зелень: сборы, церемония, праздник.",
         spreads=[(TL, "Мы вместе", "Имена"), (TP, "", ""), (QR, "Любовь — это мы", ""), (HG, "", ""), (G4, "", ""), (FB, "", ""), (M6, "", ""), (QR, "Спасибо, что разделили этот день", "")]),
    dict(id="newyear", name="Новый год", emoji="🎄", theme="newyear", title="С НОВЫМ ГОДОМ!", subtitle="Зимняя сказка",
         text="Тёмно-синий фон, золотые звёзды и снежинки. Для ёлки, подарков и семейного стола.",
         spreads=[(TL, "Волшебная ночь", "Год"), (HG, "", ""), (QR, "Загадай желание под ёлкой", ""), (G4, "", ""), (ST3, "", ""), (M6, "", ""), (FB, "", ""), (QR, "Пусть сбудется всё задуманное", "")]),
    dict(id="travel", name="Путешествие", emoji="✈️", theme="travel", title="ПУТЕШЕСТВИЕ", subtitle="Страна · Год",
         text="Маршрут на фоне, штамп и самолётик. Для отпуска, поездок и выходных.",
         spreads=[(TL, "Туда, где тепло", "Маршрут"), (HG, "", ""), (FB, "", ""), (M6, "", ""), (QR, "Лучшие моменты", ""), (ST3, "", ""), (TP, "", ""), (QR, "Скоро снова в путь", "")]),
    dict(id="halloween", name="Хэллоуин", emoji="🎃", theme="halloween", title="HAPPY HALLOWEEN", subtitle="Ночь костюмов",
         text="Фиолетовая ночь, тыквы и летучие мыши. Для костюмов и вечеринки.",
         spreads=[(TL, "Страшно весело", "Костюмы"), (G4, "", ""), (QR, "Сладость или гадость?", ""), (HG, "", ""), (M6, "", ""), (FB, "", "")]),
    dict(id="baby", name="Первый год малыша", emoji="🧸", theme="kids", title="НАШ МАЛЫШ", subtitle="Первый год",
         text="Мягкие пастельные тона. Месяц за месяцем: от первых дней до первого дня рождения.",
         spreads=[(TL, "Здравствуй, мир!", "Имя и дата рождения"), (TP, "", ""), (QR, "Первая улыбка", ""), (G4, "", ""), (ST3, "", ""), (QR, "Первые шаги", ""), (M6, "", ""), (FB, "", "")]),
    dict(id="love", name="История любви", emoji="🌸", theme="blush", title="С ЛЮБОВЬЮ", subtitle="Наша история",
         text="Пудровые тона и веточки. Для годовщины, свидания или подарка любимому человеку.",
         spreads=[(TL, "Мы", "С какого дня"), (TP, "", ""), (QR, "Ты — моё лучшее приключение", ""), (HG, "", ""), (G4, "", ""), (FB, "", ""), (QR, "Продолжение следует", "")]),
    dict(id="summer", name="Лето у моря", emoji="☀️", theme="summer", title="ЛЕТО", subtitle="Солнце, море, мы",
         text="Тёплые цвета и волны. Для пляжа, дачи и летних каникул.",
         spreads=[(TL, "Лето!", "Где и когда"), (HG, "", ""), (FB, "", ""), (M6, "", ""), (QR, "Солёные волосы и лёгкие мысли", ""), (ST3, "", ""), (G4, "", "")]),
    dict(id="family", name="Семейный архив", emoji="📜", theme="vintage", title="СЕМЕЙНЫЙ АРХИВ", subtitle="История нашей семьи",
         text="Состаренная бумага и сепия. Для старых и новых семейных фотографий.",
         spreads=[(TL, "Наши корни", "Годы"), (TP, "", ""), (QR, "Дом там, где семья", ""), (ST3, "", ""), (G4, "", ""), (HG, "", ""), (QR, "Для тех, кто будет после нас", "")]),
    dict(id="graduation", name="Выпускной", emoji="🎓", theme="emerald", title="ВЫПУСКНОЙ", subtitle="Класс · Год",
         text="Тёмно-зелёный и золото. Строго и красиво, для класса, курса и факультета.",
         spreads=[(TL, "Мы закончили!", "Школа · Год"), (G4, "", ""), (QR, "Спасибо нашим учителям", ""), (HG, "", ""), (M6, "", ""), (FB, "", ""), (QR, "До новых встреч", "")]),
]
BY_ID = {t["id"]: t for t in TEMPLATES}


def listing():
    return [dict(id=t["id"], name=t["name"], emoji=t["emoji"], theme=t["theme"], title=t["title"], text=t["text"], spreads=len(t["spreads"]))
            for t in TEMPLATES]


def _spread(layout, a, b, theme, rnd):
    texts = dict(title="", subtitle="", quote="")
    roles = [x["role"] for x in A.TEMPLATES[layout]["texts"]]
    if "title" in roles:
        texts["title"], texts["subtitle"] = a, b
    elif "quote" in roles:
        texts["quote"] = a
    return dict(template=layout, slots=[A.make_slot(None, layout, j) for j in range(len(A.TEMPLATES[layout]["slots"]))],
                texts=texts, theme=theme, decor_seed=int(rnd.randint(1, 10_000_000)))


def build(tid: str) -> dict:
    """Книга по шаблону: пустые слоты, готовая раскладка, стиль и тексты."""
    t = BY_ID[tid]
    rnd = random.Random(hash(tid) & 0xFFFF)
    spreads = [_spread(lay, a, b, t["theme"], rnd) for lay, a, b in t["spreads"]]
    spreads[0]["texts"]["title"] = t["title"]
    spreads[0]["texts"]["subtitle"] = t["subtitle"]
    return dict(title=t["title"], subtitle=t["subtitle"], theme=t["theme"], decor_seed=int(rnd.randint(1, 10_000_000)), spreads=spreads)


def manual_book() -> dict:
    """Чистый альбом для ручной работы: три пустых разворота, тема «Классика»; дальше всё настраивает пользователь."""
    bk = A.empty_book("МОЯ КНИГА", "")
    for sp in bk["spreads"]:
        sp.pop("auto", None)
    return bk
