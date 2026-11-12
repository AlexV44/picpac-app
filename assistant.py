# -*- coding: utf-8 -*-
"""
Чат-ассистент редактора фотокниги.

Пользователь пишет обычной фразой, LLM (через OpenRouter) превращает её в список СТРОГИХ
действий (JSON), приложение проверяет их и выполняет над книгой. Модель не рисует и не
исполняет код, а в запрос уходит только описание структуры книги (без фото и имён файлов).
Если LLM недоступна, работает офлайн-разбор по ключевым словам.
"""
from __future__ import annotations

import json
import random
import re
import time
import urllib.error
import urllib.request
from datetime import datetime

import numpy as np
from scipy.optimize import linear_sum_assignment

import ai_core as A
from themes import THEMES

# ---------------------------------------------------------------------------
# МОДЕЛИ OPENROUTER
# ---------------------------------------------------------------------------
# Основная: дешёвая, с поддержкой JSON/инструментов и большим контекстом.
# Запасная идёт в тот же запрос (OpenRouter сам переключится при сбое первой).
DEFAULT_MODELS = ["z-ai/glm-5.3-flash", "openai/gpt-oss-20b"]
# Если ни одной из указанных нет в каталоге, берём первую существующую из этого списка.
CATALOG_FALLBACKS = ["google/gemini-2.5-flash", "meta-llama/llama-3.3-70b-instruct", "mistralai/mistral-nemo"]
API_URL = "https://openrouter.ai/api/v1/chat/completions"
MODELS_URL = "https://openrouter.ai/api/v1/models"
MAX_ACTIONS = 10

_catalog = {"t": 0.0, "data": None}


def fetch_catalog(timeout: int = 12):
    """Каталог моделей OpenRouter (публичный, без ключа). Кэш на 10 минут. None, если недоступен."""
    if _catalog["data"] is not None and time.time() - _catalog["t"] < 600:
        return _catalog["data"]
    try:
        with urllib.request.urlopen(urllib.request.Request(MODELS_URL, headers={"User-Agent": "picpac-ai"}),
                                    timeout=timeout) as r:
            data = json.loads(r.read().decode("utf-8"))["data"]
        _catalog["data"] = {m["id"]: m for m in data}
        _catalog["t"] = time.time()
        return _catalog["data"]
    except Exception:
        return None


def resolve_models(configured: list):
    """Оставляет только существующие модели. Возвращает (список, пояснение)."""
    cat = fetch_catalog()
    if not cat:
        return configured, "каталог OpenRouter недоступен, использую указанные модели как есть"
    ok = [m for m in configured if m in cat]
    if ok:
        skipped = [m for m in configured if m not in cat]
        return ok, ("не найдены в каталоге: " + ", ".join(skipped)) if skipped else ""
    for m in CATALOG_FALLBACKS:
        if m in cat:
            return [m], f"указанные модели не найдены, взята {m}"
    return configured, "ни одна модель не найдена в каталоге"


def model_info(model_id: str):
    cat = fetch_catalog()
    m = (cat or {}).get(model_id)
    if not m:
        return None
    p = m.get("pricing", {})
    try:
        return dict(id=model_id, in_per_m=float(p.get("prompt", 0)) * 1e6, out_per_m=float(p.get("completion", 0)) * 1e6,
                    ctx=m.get("context_length"), params=m.get("supported_parameters", []))
    except Exception:
        return None


# ---------------------------------------------------------------------------
# НУМЕРАЦИЯ ФОТО И КОНТЕКСТ ДЛЯ МОДЕЛИ
# ---------------------------------------------------------------------------
def numbering(photos: dict):
    """Стабильные номера фото 1..N (по имени файла). Возвращает (id->n, n->id)."""
    ids = sorted(photos.keys(), key=lambda i: (photos[i].name, i))
    return {pid: k + 1 for k, pid in enumerate(ids)}, {k + 1: pid for k, pid in enumerate(ids)}


def _shape(aspect: float) -> str:
    return "wide" if aspect > 1.15 else ("tall" if aspect < 0.87 else "square")


def build_context(book: dict, photos: dict) -> str:
    num, _ = numbering(photos)
    used = A.used_ids(book)
    lines = [f'Book title: "{book.get("title", "")}", subtitle: "{book.get("subtitle", "")}", '
             f'book theme: {book.get("theme", "classic")}, spreads: {len(book["spreads"])}']
    for i, sp in enumerate(book["spreads"], 1):
        t = A.TEMPLATES[sp["template"]]
        parts = []
        for j, s in enumerate(sp["slots"], 1):
            shape = _shape(A.slot_aspect(sp["template"], j - 1))
            who = f'photo {num[s["photo"]]}' if s["photo"] in num else "empty"
            parts.append(f"slot {j} [{shape}] = {who}")
        roles = sorted({x["role"] for x in t["texts"]})
        txt = ""
        if roles:
            tx = sp["texts"]
            txt = f' text areas: {roles}; title="{tx.get("title", "")}", subtitle="{tx.get("subtitle", "")}", quote="{tx.get("quote", "")}";'
        lines.append(f'Spread {i}: template={sp["template"]} ("{t["name"]}"), theme={sp.get("theme", "classic")};{txt} ' + "; ".join(parts))
    lines.append("Photos (number: orientation, quality, faces, date):")
    for pid, n in sorted(num.items(), key=lambda kv: kv[1]):
        p = photos[pid]
        flags = []
        if p.dup_of:
            flags.append("duplicate")
        if p.excluded:
            flags.append("excluded")
        if pid not in used:
            flags.append("not in book")
        d = p.date.strftime("%Y-%m-%d") if p.date else "no date"
        lines.append(f"  {n}: {_shape(p.aspect)}, quality {p.q['score']:.0f}/100 ({p.q['grade']}), faces {len(p.faces)}, {d}"
                     + (f" [{', '.join(flags)}]" if flags else ""))
    lines.append("Templates available: " + "; ".join(
        f'{tid} ({t["name"]}): ' + ",".join(_shape(A.slot_aspect(tid, j)) for j in range(len(t["slots"])))
        for tid, t in A.TEMPLATES.items()))
    lines.append("Themes available: " + "; ".join(f'{tid} ({t["name"]})' for tid, t in THEMES.items()))
    return "\n".join(lines)


SYSTEM_PROMPT = """You are the editing assistant inside a photo-book editor (picPac style).
Turn the user's request into editing actions for the book. Answer with ONE JSON object and nothing else:
{"thought": "<1-3 short sentences in the user's language: how you understood the request and your plan, shown to the user>", "reply": "<short answer in the user's language: what you will do, or ONE clarifying question>", "actions": [ ... ]}

Action types (spreads and slots are numbered from 1, photos by their number from the list):
 {"type":"apply_theme","theme":"<theme id>","target":"all" | <spread number>}
 {"type":"set_text","field":"cover_title"|"cover_subtitle"|"title"|"subtitle"|"quote","spread":<n, for title/subtitle/quote>,"text":"..."}
 {"type":"change_template","spread":<n>,"template":"<template id>"}
 {"type":"swap_photos","a":{"spread":<n>,"slot":<m>},"b":{"spread":<n>,"slot":<m>}}
 {"type":"set_photo","spread":<n>,"slot":<m>,"photo":<photo number>}
 {"type":"remove_photo","spread":<n>,"slot":<m>}
 {"type":"rearrange","target":"all" | <spread number>,"mode":"shuffle"|"chronological"|"best_first"|"relayout"|"replan"|"balance"}
    shuffle = move photos between slots randomly (keeping orientation sensible); chronological = by shooting date;
    best_first = best-quality photos into the biggest slots; relayout = switch to different templates with the same photo count;
    replan = rebuild the whole book from scratch (target must be "all").
    balance = spread the photos already in the book evenly over ALL spreads (use it to fill a new empty spread when there are no free photos; target "all").
 {"type":"recrop","target":"all" | <spread number>}
 {"type":"add_spread","template":"<template id>" | null,"position":<n> | null}
 {"type":"remove_spread","spread":<n>}
 {"type":"fill_empty"}

Rules:
- Use ONLY ids/numbers that exist in CURRENT STATE. Never invent photos, templates or themes.
- Pick the theme by meaning: birthday party -> birthday, wedding -> wedding, trip/vacation/sea -> travel, New Year/Christmas/winter holidays -> newyear, Halloween -> halloween, clean/simple -> minimal, reset -> classic.
- "whole book / all pages / everywhere" -> target "all". If the user names a spread (first, second, last, "page 3"), use its number.
- If it is unclear which spread/photo is meant and a wrong guess would be annoying, ask ONE question and return "actions": [].
- If the request cannot be done with these actions, say so honestly in "reply" and return "actions": [].
- "fill / populate the new or empty spread with photos" -> {"type":"fill_empty"} (the app falls back to balancing if no free photos remain).
- change_template already re-places the spread's photos into the new layout and tops up empty slots with free photos, so do NOT add set_photo after it unless the user asked for specific photos. Slot numbers in later actions refer to the layout AFTER the change.
- "layout / раскладка / количество фото на развороте" means the template; if the user gave a number of photos, choose a template with exactly that many slots from "Templates available".
- At most 10 actions. Do not repeat the same action twice. Keep "reply" under 200 characters.

Examples:
User: оформи как день рождения -> {"reply":"Оформляю всю книгу в стиле дня рождения.","actions":[{"type":"apply_theme","theme":"birthday","target":"all"}]}
User: на втором развороте сделай новогодний стиль -> {"reply":"Второй разворот в новогоднем стиле.","actions":[{"type":"apply_theme","theme":"newyear","target":2}]}
User: перемешай все фото -> {"reply":"Перемешала фото по всей книге.","actions":[{"type":"rearrange","target":"all","mode":"shuffle"}]}
User: назови книгу «Лето 2026» -> {"reply":"Переименовала книгу.","actions":[{"type":"set_text","field":"cover_title","text":"Лето 2026"}]}
"""


# ---------------------------------------------------------------------------
# КЛИЕНТ OPENROUTER
# ---------------------------------------------------------------------------
def _extract_json(txt: str):
    if not txt:
        return None
    txt = re.sub(r"<think>.*?</think>", "", txt, flags=re.S).strip()
    txt = re.sub(r"^```(?:json)?|```$", "", txt.strip(), flags=re.M).strip()
    try:
        return json.loads(txt)
    except Exception:
        pass
    a, b = txt.find("{"), txt.rfind("}")
    if a >= 0 and b > a:
        try:
            return json.loads(txt[a:b + 1])
        except Exception:
            return None
    return None


def ask_llm(user_text: str, ctx: str, history: list, api_key: str, models: list, timeout: int = 40, lang: str = "ru"):
    """Возвращает (dict | None, ошибка, мета). meta: model, cost, tokens, seconds."""
    note = "\n\nWrite \"thought\" and \"reply\" in Belarusian (беларуская мова)." if lang == "be" else ""
    msgs = [{"role": "system", "content": SYSTEM_PROMPT + note + "\n\nCURRENT STATE:\n" + ctx}]
    for h in (history or [])[-6:]:
        if h.get("content"):
            msgs.append({"role": h["role"], "content": h["content"][:600]})
    msgs.append({"role": "user", "content": user_text})
    body = {"messages": msgs, "temperature": 0.2, "max_tokens": 1500,
            "response_format": {"type": "json_object"}, "reasoning": {"effort": "low"},
            "usage": {"include": True}}
    if len(models) == 1:
        body["model"] = models[0]
    else:
        body["models"] = models
    req = urllib.request.Request(
        API_URL, data=json.dumps(body).encode("utf-8"),
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json",
                 "HTTP-Referer": "http://localhost:8501", "X-Title": "PicPac"})
    t0 = time.time()
    meta = {}
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            msg = json.loads(e.read().decode("utf-8")).get("error", {}).get("message", "")
        except Exception:
            msg = ""
        hint = {401: "неверный ключ API", 402: "на счёте OpenRouter нет средств", 429: "лимит запросов"}.get(e.code, "")
        return None, f"OpenRouter HTTP {e.code}: {hint or msg or e.reason}", meta
    except Exception as e:
        return None, f"нет связи с OpenRouter: {type(e).__name__}", meta
    meta["seconds"] = round(time.time() - t0, 1)
    if "error" in data and "choices" not in data:
        return None, f"OpenRouter: {data['error'].get('message', 'ошибка')}", meta
    try:
        msg0 = data["choices"][0]["message"]
        content = msg0.get("content") or ""
        rs = msg0.get("reasoning")
        if isinstance(rs, str) and rs.strip():
            meta["reasoning"] = rs.strip()[:900]
    except Exception:
        return None, "OpenRouter вернул пустой ответ", meta
    meta["model"] = data.get("model", "")
    u = data.get("usage") or {}
    meta["tokens"] = (u.get("prompt_tokens", 0), u.get("completion_tokens", 0))
    meta["cost"] = u.get("cost")
    parsed = _extract_json(content)
    if not isinstance(parsed, dict):
        return None, "модель вернула ответ не в формате JSON", meta
    return parsed, "", meta


# ---------------------------------------------------------------------------
# ПРОВЕРКА ДЕЙСТВИЙ
# ---------------------------------------------------------------------------
def _int(v):
    try:
        if isinstance(v, bool):
            return None
        return int(float(str(v).strip()))
    except Exception:
        return None


def _target(v, n_spreads):
    """'all' | индекс разворота (1-based) | None."""
    if isinstance(v, str) and v.strip().lower() in ("all", "всё", "все", "book", "*"):
        return "all"
    i = _int(v)
    return i if i is not None and 1 <= i <= n_spreads else None


def _slot_ok(sl_n, sp, sl):
    n = sl_n[sp - 1]
    return sl >= 1 and (n is None or sl <= n)


def validate_actions(actions, book: dict, photos: dict):
    """Возвращает (чистые действия, список ошибок)."""
    clean, errs = [], []
    # число слотов по разворотам МЕНЯЕТСЯ по ходу цепочки (change_template/add/remove) —
    # проверяем каждое действие против состояния после предыдущих. None = неизвестно.
    sl_n = [len(sp["slots"]) for sp in book["spreads"]]
    ns = len(sl_n)
    _, by_num = numbering(photos)
    if not isinstance(actions, list):
        return [], ["ответ модели: поле actions не список"]
    seen = set()
    for a in actions[:MAX_ACTIONS]:
        if not isinstance(a, dict):
            continue
        ty = str(a.get("type", "")).strip()
        key = json.dumps(a, sort_keys=True, ensure_ascii=False)
        if key in seen:
            continue
        seen.add(key)
        if ty == "apply_theme":
            th, tg = str(a.get("theme", "")).strip().lower(), _target(a.get("target", "all"), ns)
            if th not in THEMES:
                errs.append(f"неизвестная тема «{a.get('theme')}»")
            elif tg is None:
                errs.append(f"нет разворота {a.get('target')}")
            else:
                clean.append(dict(type=ty, theme=th, target=tg))
        elif ty == "set_text":
            f, txt = str(a.get("field", "")), str(a.get("text", "")).strip()
            if f not in ("cover_title", "cover_subtitle", "title", "subtitle", "quote") or not txt and f in ("cover_title",):
                errs.append("set_text: неверное поле или пустой текст")
                continue
            sp = _int(a.get("spread"))
            if f in ("title", "subtitle", "quote"):
                if sp is None or not 1 <= sp <= ns:
                    errs.append(f"set_text: нужен номер разворота (1-{ns})")
                    continue
            clean.append(dict(type=ty, field=f, spread=sp, text=txt[:80]))
        elif ty == "change_template":
            sp, tid = _int(a.get("spread")), str(a.get("template", "")).strip()
            if sp is None or not 1 <= sp <= ns or tid not in A.TEMPLATES:
                errs.append(f"change_template: разворот {a.get('spread')} или шаблон «{a.get('template')}» не найден")
            else:
                sl_n[sp - 1] = len(A.TEMPLATES[tid]["slots"])
                clean.append(dict(type=ty, spread=sp, template=tid))
        elif ty == "swap_photos":
            ok = True
            ends = []
            for k in ("a", "b"):
                e = a.get(k) or {}
                sp, sl = _int(e.get("spread")), _int(e.get("slot"))
                if sp is None or not 1 <= sp <= ns or sl is None or not _slot_ok(sl_n, sp, sl):
                    ok = False
                ends.append((sp, sl))
            if ok and ends[0] != ends[1]:
                clean.append(dict(type=ty, a=dict(spread=ends[0][0], slot=ends[0][1]), b=dict(spread=ends[1][0], slot=ends[1][1])))
            else:
                errs.append("swap_photos: неверные разворот или слот")
        elif ty in ("set_photo", "remove_photo"):
            sp, sl = _int(a.get("spread")), _int(a.get("slot"))
            if sp is None or not 1 <= sp <= ns or sl is None or not _slot_ok(sl_n, sp, sl):
                errs.append(f"{ty}: неверные разворот или слот")
                continue
            if ty == "set_photo":
                pn = _int(a.get("photo"))
                if pn not in by_num:
                    errs.append(f"set_photo: нет фото №{a.get('photo')}")
                    continue
                clean.append(dict(type=ty, spread=sp, slot=sl, photo=pn))
            else:
                clean.append(dict(type=ty, spread=sp, slot=sl))
        elif ty == "rearrange":
            tg, mode = _target(a.get("target", "all"), ns), str(a.get("mode", "")).strip()
            if mode not in ("shuffle", "chronological", "best_first", "relayout", "replan", "balance") or tg is None:
                errs.append("rearrange: неверный режим или разворот")
            else:
                tg = "all" if mode == "replan" else tg
                if mode in ("relayout", "replan", "balance"):   # раскладка могла смениться — слоты неизвестны
                    for k in (range(len(sl_n)) if tg == "all" else [tg - 1]):
                        sl_n[k] = None
                clean.append(dict(type=ty, target=tg, mode=mode))
        elif ty == "recrop":
            tg = _target(a.get("target", "all"), ns)
            if tg is None:
                errs.append("recrop: нет такого разворота")
            else:
                clean.append(dict(type=ty, target=tg))
        elif ty == "add_spread":
            tid = a.get("template")
            tid = str(tid).strip() if tid else None
            pos = _int(a.get("position"))
            if tid and tid not in A.TEMPLATES:
                errs.append(f"add_spread: нет шаблона «{tid}»")
            else:
                p0 = pos if pos and 1 <= pos <= ns + 1 else None
                sl_n.insert((p0 or ns + 1) - 1, len(A.TEMPLATES[tid or "grid4"]["slots"]))
                ns = len(sl_n)
                clean.append(dict(type=ty, template=tid, position=p0))
        elif ty == "remove_spread":
            sp = _int(a.get("spread"))
            if sp is None or not 1 <= sp <= ns:
                errs.append(f"remove_spread: нет разворота {a.get('spread')}")
            else:
                sl_n.pop(sp - 1)
                ns = len(sl_n)
                clean.append(dict(type=ty, spread=sp))
        elif ty == "fill_empty":
            sl_n[:] = [None] * len(sl_n)
            clean.append(dict(type=ty))
        else:
            errs.append(f"неизвестное действие «{ty}»")
    return clean, errs


# ---------------------------------------------------------------------------
# ВЫПОЛНЕНИЕ
# ---------------------------------------------------------------------------
def _positions(book, target):
    """Список (si, sj) слотов с фото в области действия (0-based), в порядке чтения."""
    idx = range(len(book["spreads"])) if target == "all" else [target - 1]
    out = []
    for si in idx:
        sp = book["spreads"][si]
        t = A.TEMPLATES[sp["template"]]
        for sj, s in enumerate(sp["slots"]):
            if s["photo"]:
                x, y, w, h = t["slots"][sj]
                out.append((si, sj, x, y, w * h))
    return out


def _place(book, photos, assign):
    """assign: [(si, sj, photo_id)]; пересчитывает кропы."""
    for si, sj, pid in assign:
        sp = book["spreads"][si]
        sp["slots"][sj] = A.make_slot(photos[pid], sp["template"], sj)


def _rearrange(book, photos, target, mode, pool):
    pos = _positions(book, target)
    if mode == "replan":
        ids = [s["photo"] for sp in book["spreads"] for s in sp["slots"] if s["photo"]]
        if not ids:
            return "Нет фото для пересборки."
        new = A.plan_book([photos[i] for i in ids], book["title"], book.get("subtitle", ""), theme=book.get("theme"))
        keep = {k: book[k] for k in ("theme", "decor_seed") if k in book}
        book["spreads"] = new["spreads"]
        book.update(keep)
        A.inherit_theme(book)
        return f"Пересобрала книгу: {len(book['spreads'])} разв., шаблоны подобраны заново."
    if mode == "balance":
        return A.balance_book(book, photos)
    if mode == "relayout":
        n_ch = 0
        idx = range(len(book["spreads"])) if target == "all" else [target - 1]
        for si in idx:
            sp = book["spreads"][si]
            ps = [photos[s["photo"]] for s in sp["slots"] if s["photo"]]
            if not ps:
                continue
            cands = [t for t, d in A.TEMPLATES.items() if len(d["slots"]) == len(ps) and t != sp["template"]
                     and not (si == 0 and sp["template"] == "title_left")]
            if cands:
                rnd = random.choice(sorted(cands, key=lambda t: A._template_cost(ps, t))[:3])
                A.change_template(book, si, rnd, photos)
                n_ch += 1
            elif len(ps) > 1:
                sub = _rearrange(book, photos, si + 1, "shuffle", pool)
                n_ch += 1
        return f"Изменила раскладку на {n_ch} разв." if n_ch else "Для этого числа фото нет других шаблонов."
    if not pos:
        return "В выбранной области нет фото."
    ids = [book["spreads"][si]["slots"][sj]["photo"] for si, sj, *_ in pos]
    if mode == "shuffle":
        rng = np.random.default_rng()
        C = np.array([[A._aspect_cost(photos[pid], book["spreads"][si]["template"], sj) + rng.random() * 0.9
                       for pid in ids] for si, sj, *_ in pos])
        r, c = linear_sum_assignment(C)
        assign = [(pos[i][0], pos[i][1], ids[j]) for i, j in zip(r, c)]
        msg = "Перемешала фото" + (" по всей книге." if target == "all" else f" на развороте {target}.")
    elif mode == "chronological":
        order = sorted(ids, key=lambda i: (photos[i].date or datetime.max, photos[i].name))
        slots = sorted(pos, key=lambda p: (p[0], round(p[2], 2), p[3]))
        assign = [(s[0], s[1], pid) for s, pid in zip(slots, order)]
        msg = "Расставила фото по времени съёмки."
    else:  # best_first
        order = sorted(ids, key=lambda i: -photos[i].q["score"])
        slots = sorted(pos, key=lambda p: -p[4])
        assign = [(s[0], s[1], pid) for s, pid in zip(slots, order)]
        msg = "Лучшие по качеству кадры поставила в самые крупные слоты."
    _place(book, photos, assign)
    return msg


def _find_photo(book, pid):
    for si, sp in enumerate(book["spreads"]):
        for sj, s in enumerate(sp["slots"]):
            if s["photo"] == pid:
                return si, sj
    return None


def execute(actions: list, book: dict, photos: dict, pool: list | None = None) -> list:
    """Применяет проверенные действия. Возвращает список строк-отчётов."""
    rep = []
    _, by_num = numbering(photos)
    pool = pool if pool is not None else [p for p in photos.values() if not p.excluded and not p.dup_of]
    for a in actions:
        ty = a["type"]
        try:
            if ty == "apply_theme":
                name = THEMES[a["theme"]]["name"]
                if a["target"] == "all":
                    A.apply_theme(book, a["theme"])
                    rep.append(f"Тема «{name}» применена ко всей книге, включая обложку.")
                else:
                    A.apply_theme(book, a["theme"], spreads=[a["target"] - 1])
                    rep.append(f"Тема «{name}» применена к развороту {a['target']}.")
            elif ty == "set_text":
                f = a["field"]
                if f == "cover_title":
                    old = book["title"]
                    book["title"] = a["text"]
                    for sp in book["spreads"]:
                        if sp["texts"].get("title") == old:
                            sp["texts"]["title"] = a["text"]
                    rep.append(f"Название книги: «{a['text']}».")
                elif f == "cover_subtitle":
                    book["subtitle"] = a["text"]
                    rep.append(f"Подпись обложки: «{a['text']}».")
                else:
                    sp = book["spreads"][a["spread"] - 1]
                    roles = {t["role"] for t in A.TEMPLATES[sp["template"]]["texts"]}
                    need = "quote" if f == "quote" else "title"
                    if need not in roles:
                        rep.append(f"На развороте {a['spread']} нет места под текст: шаблон «{A.TEMPLATES[sp['template']]['name']}». "
                                   f"Могу сменить шаблон на «Заголовок + 5 фото» или «Текст + фото справа».")
                    else:
                        sp["texts"][f] = a["text"]
                        rep.append(f"Текст на развороте {a['spread']} обновлён.")
            elif ty == "change_template":
                si = a["spread"] - 1
                before = {s["photo"] for s in book["spreads"][si]["slots"] if s["photo"]}
                A.change_template(book, si, a["template"], photos)
                added = A.fill_spread_empty(book, si, [p for p in pool if p.id not in A.used_ids(book)], photos)
                after = {s["photo"] for s in book["spreads"][si]["slots"] if s["photo"]}
                lost = len(before - after)
                rep.append(f"Разворот {a['spread']}: шаблон «{A.TEMPLATES[a['template']]['name']}»."
                           + (f" Добавлено свободных фото: {added}." if added else "")
                           + (f" {lost} фото не поместились и вернулись в неиспользуемые." if lost else ""))
            elif ty == "swap_photos":
                (sa, ja), (sb, jb) = (a["a"]["spread"] - 1, a["a"]["slot"] - 1), (a["b"]["spread"] - 1, a["b"]["slot"] - 1)
                pa = book["spreads"][sa]["slots"][ja]["photo"]
                pb = book["spreads"][sb]["slots"][jb]["photo"]
                A.set_slot_photo(book, sa, ja, pb, photos)
                A.set_slot_photo(book, sb, jb, pa, photos)
                rep.append("Поменяла фото местами.")
            elif ty == "set_photo":
                pid = by_num[a["photo"]]
                si, sj = a["spread"] - 1, a["slot"] - 1
                old = book["spreads"][si]["slots"][sj]["photo"]
                loc = _find_photo(book, pid)
                A.set_slot_photo(book, si, sj, pid, photos)
                if loc and loc != (si, sj):
                    A.set_slot_photo(book, loc[0], loc[1], old, photos)   # фото уже было в книге: меняем местами
                rep.append(f"Фото {a['photo']} поставлено на разворот {a['spread']}, слот {a['slot']}.")
            elif ty == "remove_photo":
                A.set_slot_photo(book, a["spread"] - 1, a["slot"] - 1, None, photos)
                rep.append(f"Слот {a['slot']} на развороте {a['spread']} очищен.")
            elif ty == "rearrange":
                rep.append(_rearrange(book, photos, a["target"], a["mode"], pool))
            elif ty == "recrop":
                idx = range(len(book["spreads"])) if a["target"] == "all" else [a["target"] - 1]
                for si in idx:
                    for sj in range(len(book["spreads"][si]["slots"])):
                        A.recrop_slot(book, si, sj, photos)
                rep.append("Автокадрирование выполнено" + (" во всей книге." if a["target"] == "all" else f" на развороте {a['target']}."))
            elif ty == "add_spread":
                tid = a.get("template") or "grid4"
                sp = dict(template=tid, slots=[A.make_slot(None, tid, j) for j in range(len(A.TEMPLATES[tid]["slots"]))],
                          texts=dict(title="", subtitle="", quote=""))
                pos = (a.get("position") or len(book["spreads"]) + 1) - 1
                book["spreads"].insert(pos, sp)
                A.inherit_theme(book)
                rep.append(f"Добавлен пустой разворот {pos + 1} («{A.TEMPLATES[tid]['name']}»).")
            elif ty == "remove_spread":
                if len(book["spreads"]) <= 1:
                    rep.append("Нельзя удалить единственный разворот.")
                else:
                    book["spreads"].pop(a["spread"] - 1)
                    rep.append(f"Разворот {a['spread']} удалён, его фото вернулись в неиспользуемые.")
            elif ty == "fill_empty":
                used = A.used_ids(book)
                free = [p for p in pool if p.id not in used]
                empty_spreads = [i for i, sp in enumerate(book["spreads"]) if not any(s["photo"] for s in sp["slots"])]
                has_empty_slots = any(s["photo"] is None for sp in book["spreads"] for s in sp["slots"])
                if not has_empty_slots:
                    rep.append("Пустых слотов нет.")
                elif free:
                    n0 = len(used)
                    A.ai_fill(book, photos, pool)
                    rep.append(f"ИИ заполнил пустые слоты: добавлено фото {len(A.used_ids(book)) - n0}.")
                elif empty_spreads:
                    msg = A.balance_book(book, photos)
                    rep.append("Свободных фото нет: все загруженные уже в книге. " + msg)
                else:
                    rep.append("Свободных фото нет: все загруженные уже стоят в книге. Загрузите ещё фото "
                               "или скажите «распредели фото поровну».")
        except Exception as e:   # одно сломанное действие не должно ронять остальные
            rep.append(f"Не удалось выполнить «{ty}»: {e}")
    return rep


# ---------------------------------------------------------------------------
# ЗАПАСНОЙ РАЗБОР БЕЗ LLM
# ---------------------------------------------------------------------------
_THEME_RX = [
    ("birthday", r"день рожд|\bдр\b|юбиле|именин|birthday|днюх|деньрожд"),
    ("wedding", r"свадьб|свадеб|wedding|венчан|помолвк"),
    ("halloween", r"хэллоуин|хеллоуин|хэлоуин|halloween|тыкв"),
    ("newyear", r"нов(ый|ого|ым|огодн\w*) год|новогодн|рождеств|christmas|ёлк|елк|зимн"),
    ("travel", r"путешеств|поездк|отпуск|travel|каникул|\bтур\b|море|морск"),
    ("minimal", r"минимал|строг|лаконич|простой стиль"),
    ("classic", r"классич|убери оформлен|без оформлен|сбрось оформлен|по умолчанию"),
]
_ORD = {"перв": 1, "втор": 2, "трет": 3, "четв": 4, "пят": 5, "шест": 6, "седьм": 7, "восьм": 8}


def _spread_ref(low: str, n: int):
    m = re.search(r"(?:разворот\w*|страниц\w*|стр\.?)\s*(?:№|#)?\s*(\d+)", low)
    if m and 1 <= int(m.group(1)) <= n:
        return int(m.group(1))
    m = re.search(r"(\d+)\s*-?\s*(?:м|й|ом|ой|ем)?\s*(?:разворот|страниц)", low)
    if m and 1 <= int(m.group(1)) <= n:
        return int(m.group(1))
    for stem, k in _ORD.items():
        if re.search(stem + r"\w*\s+(?:разворот|страниц)", low) and k <= n:
            return k
    if re.search(r"последн\w*\s+(?:разворот|страниц)", low):
        return n
    return None


def parse_offline(text: str, book: dict, photos: dict) -> dict:
    low = text.lower().strip()
    n = len(book["spreads"])
    sp = _spread_ref(low, n)
    tgt = sp if sp else "all"
    whole = bool(re.search(r"\bвсе(?:х|м|й)?\b|всю книг|везде|целиком|всей книг", low))
    if whole:
        tgt = "all"
    acts, reply = [], ""

    q = re.search(r"[«\"“']([^»\"”']{1,80})[»\"”']", text)
    if q and re.search(r"назови|название|заголов|подпис|надпис|текст|напиши", low):
        val = q.group(1).strip()
        if re.search(r"обложк", low) and "подпис" in low:
            acts.append(dict(type="set_text", field="cover_subtitle", text=val))
        elif re.search(r"назови|назван|обложк", low) and not re.search(r"разворот|страниц", low):
            acts.append(dict(type="set_text", field="cover_title", text=val))
        else:
            fld = "subtitle" if "подпис" in low else ("quote" if re.search(r"цитат|надпис|текст", low) else "title")
            s = sp
            if s is None:
                s = next((i + 1 for i, d in enumerate(book["spreads"]) if d["template"] == "title_left"), 1)
            acts.append(dict(type="set_text", field=fld, spread=s, text=val))
        return dict(reply="Меняю текст.", actions=acts)

    for tid, rx in _THEME_RX:
        if re.search(rx, low):
            acts.append(dict(type="apply_theme", theme=tid, target=tgt))
            return dict(reply=f"Применяю тему «{THEMES[tid]['name']}».", actions=acts)

    m = re.search(r"фото\s*(?:№|#)?\s*(\d+)\s*(?:и|с|на)\s*(?:фото\s*)?(?:№|#)?\s*(\d+)", low)
    if re.search(r"поменя\w*\s+местами|обмен", low) and m:
        _, by_num = numbering(photos)
        l1, l2 = (_find_photo(book, by_num.get(int(m.group(i)))) for i in (1, 2))
        if l1 and l2:
            return dict(reply="Меняю фото местами.", actions=[dict(type="swap_photos",
                        a=dict(spread=l1[0] + 1, slot=l1[1] + 1), b=dict(spread=l2[0] + 1, slot=l2[1] + 1))])
        return dict(reply="Не нашла оба этих фото в книге. Проверьте номера на вкладке «Ассистент».", actions=[])

    if re.search(r"перемеш|случайн|рандом|перетасу", low):
        return dict(reply="Перемешиваю фото.", actions=[dict(type="rearrange", target=tgt, mode="shuffle")])
    if re.search(r"по дате|хронолог|по порядку|по времени", low):
        return dict(reply="Расставляю по времени съёмки.", actions=[dict(type="rearrange", target=tgt, mode="chronological")])
    if re.search(r"лучш\w+.*(?:крупн|больш|перв|обложк)|самые удачн|best", low):
        return dict(reply="Ставлю лучшие кадры в крупные слоты.", actions=[dict(type="rearrange", target=tgt, mode="best_first")])
    if re.search(r"с нуля|пересобер|перепланир|заново собер", low):
        return dict(reply="Пересобираю книгу.", actions=[dict(type="rearrange", target="all", mode="replan")])
    if re.search(r"раскладк|расположени|перекомпон|переставь|друго[йе] шаблон|измени шаблон|поменяй шаблон", low):
        return dict(reply="Меняю раскладку.", actions=[dict(type="rearrange", target=tgt, mode="relayout")])
    if re.search(r"добав\w+\s+(?:\w+\s+)?разворот|нов\w+\s+разворот", low):
        return dict(reply="Добавляю разворот.", actions=[dict(type="add_spread", template=None, position=None)])
    if re.search(r"удал\w+\s+разворот|убер\w+\s+разворот", low):
        if sp:
            return dict(reply=f"Удаляю разворот {sp}.", actions=[dict(type="remove_spread", spread=sp)])
        return dict(reply="Какой разворот удалить? Напишите номер.", actions=[])
    if re.search(r"поровну|равномерн|распредел|выровня", low):
        return dict(reply="Распределяю фото поровну.", actions=[dict(type="rearrange", target="all", mode="balance")])
    if re.search(r"наполн|заполн|запол|подстав|добавь фото|размест|раскидай", low):
        return dict(reply="Заполняю пустые слоты.", actions=[dict(type="fill_empty")])
    if re.search(r"перекадр|кадрирован|кроп", low):
        return dict(reply="Заново кадрирую.", actions=[dict(type="recrop", target=tgt)])
    return dict(reply="Не совсем поняла запрос. Попробуйте, например: «оформи как день рождения», «перемешай все фото», "
                      "«расставь по дате», «измени раскладку на втором развороте», «назови книгу «Лето»». "
                      "Все основные команды есть и в панели «Быстрые команды».", actions=[])


# ---------------------------------------------------------------------------
# ГЛАВНАЯ ТОЧКА ВХОДА
# ---------------------------------------------------------------------------
def handle(text: str, book: dict, photos: dict, history: list | None = None, api_key: str = "",
           models: list | None = None, use_llm: bool = True) -> dict:
    """
    Не меняет книгу, а только планирует. Возвращает dict:
    reply, actions (проверенные), errors, source ('llm'|'offline'), llm_error, meta.
    """
    out = dict(reply="", actions=[], errors=[], source="offline", llm_error="", meta={})
    parsed = None
    if use_llm and api_key:
        ms, note = resolve_models(models or DEFAULT_MODELS)
        data, err, meta = ask_llm(text, build_context(book, photos), history or [], api_key, ms)
        out["meta"] = meta
        if data is not None:
            acts, errs = validate_actions(data.get("actions") or [], book, photos)
            if acts or not (data.get("actions") or []):
                parsed = dict(reply=str(data.get("reply", ""))[:400], actions=acts)
                out["thought"] = str(data.get("thought", ""))[:500]
                out.update(source="llm", errors=errs)
            else:
                out["llm_error"] = "модель предложила неприменимые действия: " + "; ".join(errs[:3])
        else:
            out["llm_error"] = err
    if parsed is None:
        off = parse_offline(text, book, photos)
        acts, errs = validate_actions(off["actions"], book, photos)
        parsed = dict(reply=off["reply"], actions=acts)
        out["errors"] = errs
    out["reply"], out["actions"] = parsed["reply"], parsed["actions"]
    return out
