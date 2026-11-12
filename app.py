# -*- coding: utf-8 -*-
"""
PicPac AI Prototype: фотокнига Premium с ИИ.
Пользователь загружает нужное количество фото -> ИИ оценивает качество, находит дубли,
умно кадрирует (лица, правило третей), улучшает и сам подставляет кадры в шаблон разворотов.

Запуск:  streamlit run app.py
"""
import copy
import json
import os
import time

import streamlit as st

import ai_core as A
import assistant as AS

st.set_page_config(page_title="PicPac AI", layout="centered", page_icon="📸")

# ---------------------------------------------------------------------------
# СТИЛЬ picPac
# ---------------------------------------------------------------------------
st.markdown("""
<style>
.block-container {max-width: 860px; padding-top: 0 !important;}
header[data-testid="stHeader"] {display: none;}
.pp-header {background:#de5558; color:#fff; text-align:center; font-size:1.35rem;
            padding:18px 10px; margin:0 -1rem 14px -1rem; letter-spacing:.2px;}
.pp-title-sub {color:#fff; font-size:.8rem; opacity:.85;}
.stButton>button {border-radius:10px;}
.pp-order button, div[data-testid="stButton"] button[kind="primary"] {background:#2d4b46 !important; color:#fff !important; border:0;}
.pp-badge {display:inline-block; padding:3px 9px; border-radius:12px; font-size:.78rem; color:#fff;}
.pp-good {background:#3ea060;} .pp-mid {background:#e8a828;} .pp-bad {background:#d64541;}
.pp-note {border:1px solid #f3e2b3; background:#fdf6e3; color:#8a6d1d; padding:14px; border-radius:8px; text-align:center;}
.pp-note.good {border-color:#bfe3cb; background:#eef9f1; color:#2c7a49;}
.pp-note.bad {border-color:#f1c2c0; background:#fdeeed; color:#a2322f;}
.pp-section {font-weight:700; font-size:1.05rem; margin:18px 0 6px 0;}
.pp-small {color:#777; font-size:.82rem;}
</style>
""", unsafe_allow_html=True)

# ---------------------------------------------------------------------------
# СОСТОЯНИЕ
# ---------------------------------------------------------------------------
def _default_api_key() -> str:
    """Ключ OpenRouter из переменной окружения или .streamlit/secrets.toml (чтобы не вводить вручную)."""
    k = os.environ.get("OPENROUTER_API_KEY", "")
    if not k:
        try:
            k = str(st.secrets.get("OPENROUTER_API_KEY", ""))
        except Exception:
            k = ""
    return k


S = st.session_state
if "photos" not in S:
    S.photos = {}              # id -> Photo
    S.seen = set()             # уже обработанные файлы
    S.book = A.empty_book("МОЯ КНИГА", "")
    S.history, S.future = [], []
    S.book_gen = 0             # меняется при программной замене книги (сброс ключей виджетов)
    S.render_cache = {}
    S.pdf = None
    S.msg = None
    S.chat = []                # история чата с ассистентом
    S.api_key = _default_api_key()
    S.models = list(AS.DEFAULT_MODELS)
    S.use_llm = True


@st.cache_resource(show_spinner="Загружаю детектор лиц...")
def get_detector():
    det = A.FaceDetector(download=True)
    if det._yunet is None and det._haar is None:
        try:
            from ultralytics import YOLO
            det.yolo = YOLO("yolov8n.pt")
            det.name = "YOLOv8 (голова по рамке человека)"
        except Exception:
            pass
    return det


@st.cache_resource(show_spinner="Загружаю модель Real-ESRGAN...")
def get_upscaler():
    return A.Upscaler(download=True)


def center_detail(img, frac=0.4):
    """Центральный фрагмент в 100%: на нём видно разницу между обычным и ИИ-увеличением."""
    w, h = img.size
    cw, ch = int(w * frac), int(h * frac)
    x, y = (w - cw) // 2, (h - ch) // 2
    return img.crop((x, y, x + cw, y + ch))


def snapshot():
    S.history.append(copy.deepcopy(S.book))
    S.history = S.history[-30:]
    S.future = []
    S.pdf = None


def undo():
    if S.history:
        S.future.append(copy.deepcopy(S.book))
        S.book = S.history.pop()
        S.book_gen += 1
        S.pdf = None


def redo():
    if S.future:
        S.history.append(copy.deepcopy(S.book))
        S.book = S.future.pop()
        S.book_gen += 1
        S.pdf = None


def render_cached(spread, width, grid, badges, enhanced):
    key = (json.dumps(spread, sort_keys=True, default=str),
           tuple(sorted((pid, id(p)) for pid, p in S.photos.items() if pid in
                        [s["photo"] for s in spread["slots"] if s["photo"]])),
           width, grid, badges, enhanced)
    c = S.render_cache
    if key not in c:
        if len(c) > 60:
            c.clear()
        c[key] = A.render_spread(spread, S.photos, width=width, grid=grid, badges=badges, use_enhanced=enhanced)
    return c[key]


def thumb(photo, size=420):
    t = photo.img.copy()
    t.thumbnail((size, size))
    return t


def badge(grade, text):
    return f'<span class="pp-badge pp-{grade}">{text}</span>'


def name_of(pid):
    return S.photos[pid].name if pid in S.photos else "— пусто —"


def refresh_photo_slots(pid):
    for sp in S.book["spreads"]:
        for j, s in enumerate(sp["slots"]):
            if s["photo"] == pid:
                sp["slots"][j] = A.make_slot(S.photos[pid], sp["template"], j)


def active_photos():
    return [p for p in S.photos.values() if not p.excluded and not p.dup_of]


# ---------------------------------------------------------------------------
# ШАПКА
# ---------------------------------------------------------------------------
st.markdown('<div class="pp-header">Фотокниги Premium</div>', unsafe_allow_html=True)
c_undo, c_redo, c_sp, c_prod, c_order = st.columns([1.1, 1.1, 1.2, 1.5, 1.2])
if c_undo.button("↶ Отменить", disabled=not S.history, use_container_width=True):
    undo()
    st.rerun()
if c_redo.button("↷ Повторить", disabled=not S.future, use_container_width=True):
    redo()
    st.rerun()
if c_prod.button("Сменить продукт", use_container_width=True):
    S.msg = "В демо доступен только продукт «Фотокнига Premium»."
if c_order.button("Заказать", type="primary", use_container_width=True):
    S.msg = "Демо: оформление заказа отключено. PDF для печати можно скачать во вкладке «Дизайн»."
if S.msg:
    st.info(S.msg)
    S.msg = None

tab_photo, tab_spreads, tab_chat, tab_edit, tab_design = st.tabs(
    ["🖼 Фото", "📖 Развороты", "💬 Ассистент", "✂️ Редактор кадра", "🎨 Дизайн"])

detector = get_detector()

# ===========================================================================
# ВКЛАДКА 1: ФОТО
# ===========================================================================
with tab_photo:
    st.markdown('<div class="pp-section">1. Загрузите фото для книги</div>', unsafe_allow_html=True)
    st.caption("Загрузите ровно столько снимков, сколько нужно в книге. ИИ сам оценит качество, "
               "отсеет дубли, кадрирует и расставит их по шаблону.")
    files = st.file_uploader("Фото", type=["jpg", "jpeg", "png", "webp"], accept_multiple_files=True,
                             label_visibility="collapsed", key=f"uploader_{S.get('up_gen', 0)}")
    cols_demo = st.columns([1, 1, 2])
    use_demo = cols_demo[0].button("Демо-фото", help="Тестовый набор с дефектами: размытое, тёмное, дубль, малое разрешение")
    if cols_demo[1].button("Очистить всё"):
        S.photos, S.seen = {}, set()
        S.book = A.empty_book(S.book["title"], S.book.get("subtitle", ""))
        S.history, S.future, S.render_cache, S.pdf = [], [], {}, None
        S.book_gen += 1
        S.up_gen = S.get("up_gen", 0) + 1   # новый виджет загрузки, старые файлы не вернутся
        st.rerun()

    incoming = []
    for f in files or []:
        k = f"{f.name}:{f.size}"
        if k not in S.seen:
            incoming.append((k, f.name, f.getvalue()))
    if use_demo:
        for name, data in A.make_demo_photos():
            k = f"demo:{name}"
            if k not in S.seen:
                incoming.append((k, name, data))
    if incoming:
        bar = st.progress(0.0, text="ИИ анализирует фото...")
        for i, (k, name, data) in enumerate(incoming):
            try:
                p = A.load_photo(name, data, detector)
                S.photos[p.id] = p
            except Exception as e:
                st.warning(f"Не удалось открыть {name}: {e}")
            S.seen.add(k)
            bar.progress((i + 1) / len(incoming), text=f"Анализ: {name}")
        bar.empty()
        A.mark_duplicates(list(S.photos.values()))
        S.pdf = None
        st.rerun()

    photos = list(S.photos.values())
    if not photos:
        st.info("Пока нет фото. Во вкладке «Развороты» уже виден пустой шаблон альбома: "
                "загрузите снимки, и ИИ заполнит его.")
    else:
        act = active_photos()
        n_good = sum(p.q["grade"] == "good" for p in act)
        n_dup = sum(1 for p in photos if p.dup_of)
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Загружено", len(photos))
        m2.metric("В книгу пойдут", len(act))
        m3.metric("Хорошего качества", n_good)
        m4.metric("Дубликатов", n_dup)

        st.markdown('<div class="pp-section">2. Собрать альбом</div>', unsafe_allow_html=True)
        opt1, opt2 = st.columns(2)
        S.enhance = opt1.checkbox("Автоулучшение кадров (уровни, яркость, контраст, шум, резкость)", value=S.get("enhance", True))
        title = opt2.text_input("Название книги", value=S.book["title"], key=f"booktitle_{S.book_gen}")
        if title != S.book["title"]:
            S.book["title"] = title
            if S.book["spreads"] and S.book["spreads"][0]["template"] in ("title_left",):
                S.book["spreads"][0]["texts"]["title"] = title
        b1, b2 = st.columns(2)
        if b1.button("✨ ИИ: подставить фото в альбом", type="primary", use_container_width=True):
            snapshot()
            with st.spinner("Подбираю кадры, кадрирую, расставляю..."):
                A.ai_fill(S.book, S.photos, act)
            S.book_gen += 1
            st.success("Готово: фото подставлены в слоты. Смотрите вкладку «Развороты».")
        if b2.button("Пересобрать альбом с нуля", use_container_width=True):
            snapshot()
            with st.spinner("Планирую развороты..."):
                S.book = A.plan_book(act, S.book["title"], S.book.get("subtitle", ""), theme=S.book.get("theme"))
            S.book_gen += 1
            st.success("Альбом пересобран: шаблоны подобраны под ориентацию и качество кадров.")

        st.markdown('<div class="pp-section">Анализ качества</div>', unsafe_allow_html=True)
        st.caption(f"Детектор лиц: {detector.name}")
        cols = st.columns(3)
        for i, p in enumerate(sorted(photos, key=lambda x: x.name)):
            with cols[i % 3]:
                st.image(thumb(p), use_container_width=True)
                st.markdown(badge(p.q["grade"], f'{p.q["score"]:.0f}/100 · ' +
                                  {"good": "хорошее", "mid": "среднее", "bad": "низкое"}[p.q["grade"]]),
                            unsafe_allow_html=True)
                st.caption(p.name)
                if p.q["issues"]:
                    st.caption("⚠ " + ", ".join(p.q["issues"]))
                st.caption(f"Лиц: {len(p.faces)} · {p.orig_size[0]}×{p.orig_size[1]} px")
                if p.ops:
                    st.caption("ИИ улучшит: " + ", ".join(p.ops))
                if p.dup_of:
                    st.caption(f"♻ Дубликат: «{name_of(p.dup_of)}»")
                p.excluded = st.checkbox("Не использовать", value=p.excluded, key=f"ex_{p.id}")
                r1, r2 = st.columns(2)
                if r1.button("↺", key=f"rl_{p.id}", help="Повернуть на 90° влево"):
                    S.photos[p.id] = A.rotate_photo(p, -90, detector)
                    refresh_photo_slots(p.id)
                    A.mark_duplicates(list(S.photos.values()))
                    st.rerun()
                if r2.button("↻", key=f"rr_{p.id}", help="Повернуть на 90° вправо"):
                    S.photos[p.id] = A.rotate_photo(p, 90, detector)
                    refresh_photo_slots(p.id)
                    A.mark_duplicates(list(S.photos.values()))
                    st.rerun()

# ===========================================================================
# ВКЛАДКА 2: РАЗВОРОТЫ
# ===========================================================================
with tab_spreads:
    enhanced = S.get("enhance", True)
    top1, top2 = st.columns([1, 1])
    show_grid = top1.toggle("Сетка кадрирования", value=S.get("grid", False)) if hasattr(st, "toggle") \
        else top1.checkbox("Сетка кадрирования", value=S.get("grid", False))
    S.grid = show_grid
    show_badges = top2.checkbox("Индикаторы качества на слотах", value=True)

    st.image(A.render_cover(S.book, 860), use_container_width=True)
    book = S.book
    filled = sum(1 for sp in book["spreads"] for s in sp["slots"] if s["photo"])
    total = sum(len(sp["slots"]) for sp in book["spreads"])
    st.caption(f"Слотов заполнено: {filled} из {total} · кадров не использовано: "
               f"{len([p for p in active_photos() if p.id not in A.used_ids(book)])}")

    for si, sp in enumerate(book["spreads"]):
        st.markdown(f'<div class="pp-section">Разворот {si + 1}</div>', unsafe_allow_html=True)
        st.image(render_cached(sp, 860, show_grid, show_badges, enhanced), use_container_width=True)
        with st.expander(f"⚙ Настроить разворот {si + 1}"):
            tid = sp["template"]
            tids = list(A.TEMPLATES.keys())
            new_tid = st.selectbox("Шаблон", tids, index=tids.index(tid),
                                   format_func=lambda t: f'{A.TEMPLATES[t]["name"]} ({len(A.TEMPLATES[t]["slots"])} фото)',
                                   key=f"tpl_{si}_{tid}_{S.book_gen}")
            if new_tid != tid:
                snapshot()
                A.change_template(book, si, new_tid, S.photos)
                st.rerun()

            ids = [None] + sorted(S.photos.keys(), key=lambda x: S.photos[x].name)
            for j, s in enumerate(sp["slots"]):
                cur = s["photo"]
                sel = st.selectbox(f"Слот {j + 1}", ids, index=ids.index(cur) if cur in ids else 0,
                                   format_func=name_of, key=f"sl_{si}_{j}_{tid}_{cur}_{S.book_gen}")
                if sel != cur:
                    snapshot()
                    A.set_slot_photo(book, si, j, sel, S.photos)
                    st.rerun()

            roles = {t["role"] for t in A.TEMPLATES[tid]["texts"]}
            if "title" in roles:
                sp["texts"]["title"] = st.text_input("Заголовок", value=sp["texts"].get("title", ""), key=f"ttl_{si}_{S.book_gen}")
                sp["texts"]["subtitle"] = st.text_input("Подпись", value=sp["texts"].get("subtitle", ""), key=f"sub_{si}_{S.book_gen}")
            if "quote" in roles:
                sp["texts"]["quote"] = st.text_input("Текст", value=sp["texts"].get("quote", ""), key=f"quo_{si}_{S.book_gen}")

            a1, a2 = st.columns(2)
            if a1.button("🎯 ИИ-кадрирование заново", key=f"rc_{si}_{S.book_gen}", use_container_width=True):
                snapshot()
                for j in range(len(sp["slots"])):
                    A.recrop_slot(book, si, j, S.photos)
                st.rerun()
            if a2.button("🗑 Удалить разворот", key=f"del_{si}_{S.book_gen}", use_container_width=True):
                snapshot()
                book["spreads"].pop(si)
                S.book_gen += 1
                st.rerun()

    st.markdown("---")
    add_t = st.selectbox("Добавить разворот", list(A.TEMPLATES.keys()), key="add_tpl",
                         format_func=lambda t: f'{A.TEMPLATES[t]["name"]} ({len(A.TEMPLATES[t]["slots"])} фото)')
    if st.button("＋ Добавить пустой разворот"):
        snapshot()
        book["spreads"].append(dict(template=add_t,
                                    slots=[A.make_slot(None, add_t, j) for j in range(len(A.TEMPLATES[add_t]["slots"]))],
                                    texts=dict(title="", subtitle="", quote="")))
        A.inherit_theme(book)
        st.rerun()
    if S.photos and st.button("✨ ИИ: заполнить пустые слоты", type="primary"):
        snapshot()
        S.msg = " ".join(AS.execute([{"type": "fill_empty"}], book, S.photos, active_photos()))
        S.book_gen += 1
        st.rerun()

# ===========================================================================
# ВКЛАДКА 3: РЕДАКТОР КАДРА (как экран редактирования фото в picPac)
# ===========================================================================
with tab_edit:
    book = S.book
    filled_slots = [(si, j) for si, sp in enumerate(book["spreads"]) for j, s in enumerate(sp["slots"]) if s["photo"]]
    if not filled_slots:
        st.info("Нет заполненных слотов. Загрузите фото и нажмите «ИИ: подставить фото в альбом».")
    else:
        def slot_label(x):
            si_, j_ = x
            return f"Разворот {si_ + 1}, слот {j_ + 1}: {name_of(book['spreads'][si_]['slots'][j_]['photo'])}"
        si, sj = st.selectbox("Выберите кадр", filled_slots, format_func=slot_label, key=f"edsel_{S.book_gen}")
        sp = book["spreads"][si]
        slot = sp["slots"][sj]
        photo = S.photos[slot["photo"]]
        tid = sp["template"]
        aspect = A.slot_aspect(tid, sj)
        W, H = photo.img.size
        crop = slot["crop"]

        grade, msg, dpi = A.slot_quality(photo, crop, A.slot_width_cm(tid, sj))
        cls = {"good": "good", "mid": "", "bad": "bad"}[grade]
        st.markdown(f'<div class="pp-note {cls}">{msg}<br><span class="pp-small">≈ {dpi:.0f} dpi в этом слоте '
                    f'(для печати нужно {A.TARGET_DPI}, минимум {A.MIN_OK_DPI})</span></div>', unsafe_allow_html=True)

        wmax_n = min(1.0, (H * aspect) / W)             # ширина максимального окна (доли ширины фото)
        z0 = float(min(4.0, max(1.0, wmax_n / max(crop[2], 1e-6))))
        cx0 = (crop[0] + crop[2] / 2) * 100
        cy0 = (crop[1] + crop[3] / 2) * 100
        ck = f"{si}_{sj}_{photo.id}_{hash(tuple(round(c, 4) for c in crop))}_{S.book_gen}"

        zoom = st.slider("Масштаб", 1.0, 4.0, round(z0, 2), 0.05, key=f"z_{ck}")
        cx = st.slider("Положение по горизонтали, %", 0.0, 100.0, float(round(cx0, 1)), 0.5, key=f"x_{ck}")
        cy = st.slider("Положение по вертикали, %", 0.0, 100.0, float(round(cy0, 1)), 0.5, key=f"y_{ck}")

        cw_n = wmax_n / zoom
        ch_n = cw_n * W / aspect / H
        x0 = min(max(cx / 100 - cw_n / 2, 0), 1 - cw_n)
        y0 = min(max(cy / 100 - ch_n / 2, 0), 1 - ch_n)
        new_crop = (x0, y0, cw_n, ch_n)

        st.image(A.draw_crop_editor(photo.img, new_crop, 720), use_container_width=True,
                 caption="Сетка третей: ИИ ставит лица на линии, оставляет воздух над головой")

        b1, b2, b3, b4 = st.columns(4)
        if b1.button("Применить", type="primary", use_container_width=True, key=f"ap_{ck}"):
            snapshot()
            slot["crop"], slot["manual"] = new_crop, True
            st.rerun()
        if b2.button("🎯 ИИ-кроп", use_container_width=True, key=f"ai_{ck}"):
            snapshot()
            A.recrop_slot(book, si, sj, S.photos)
            st.rerun()
        if b3.button("Удалить", use_container_width=True, key=f"rm_{ck}"):
            snapshot()
            A.set_slot_photo(book, si, sj, None, S.photos)
            S.book_gen += 1
            st.rerun()
        ids = [pid for pid in sorted(S.photos, key=lambda x: S.photos[x].name) if pid != photo.id]
        with b4.popover("Заменить") if hasattr(st, "popover") else st.expander("Заменить"):
            rep = st.selectbox("Другое фото", ids, format_func=name_of, key=f"rep_{ck}") if ids else None
            if rep and st.button("Заменить фото", key=f"repbtn_{ck}"):
                snapshot()
                A.set_slot_photo(book, si, sj, rep, S.photos)
                S.book_gen += 1
                st.rerun()

        st.markdown('<div class="pp-section">До и после улучшения</div>', unsafe_allow_html=True)
        ow = 460
        oh = int(ow / aspect)
        c_before, c_after = st.columns(2)
        c_before.image(A.render_slot_image(photo, new_crop, ow, oh, use_enhanced=False),
                       caption="Как есть", use_container_width=True)
        c_after.image(A.render_slot_image(photo, new_crop, ow, oh, use_enhanced=True),
                      caption="После ИИ", use_container_width=True)
        st.caption("Применено: " + ", ".join(photo.ops + ["адаптивная резкость"]))
        st.caption("Улучшение умеренное: оно подтягивает свет, контраст и резкость, но не восстанавливает "
                   "сильно смазанные кадры. Для них ИИ предупреждает и предлагает замену.")

        st.markdown('<div class="pp-section">ИИ-апскейл (Real-ESRGAN)</div>', unsafe_allow_html=True)
        pw = min(1800, int(A.slot_width_cm(tid, sj) / 2.54 * A.TARGET_DPI))
        ph_px = int(pw / aspect)
        region_px = (new_crop[2] * W) * (new_crop[3] * H)
        st.caption(f"Печатный размер слота: {pw}×{ph_px} px при {A.TARGET_DPI} dpi. "
                   f"Лупа показывает центр кадра в масштабе 100%.")
        if st.button("🔍 Показать апскейл (лупа 100%)", key=f"sr_{ck}"):
            sr = get_upscaler()
            if not sr.available:
                st.warning(f"ИИ-апскейл недоступен: {sr.reason}")
            elif region_px > A.SR_MAX_PIXELS:
                st.info("В этом кадре и так достаточно пикселей, апскейл не нужен.")
            else:
                with st.spinner("Повышаю разрешение..."):
                    plain = A.render_slot_image(photo, new_crop, pw, ph_px, True)
                    smart = A.render_slot_image(photo, new_crop, pw, ph_px, True, sr=sr, force_sr=True)
                S.sr_cmp = (ck, center_detail(plain), center_detail(smart), sr.name)
        cmp_ = S.get("sr_cmp")
        if cmp_ and cmp_[0] == ck:
            l1, l2 = st.columns(2)
            l1.image(cmp_[1], caption="Обычное увеличение", use_container_width=True)
            l2.image(cmp_[2], caption=f"{cmp_[3]}", use_container_width=True)

# ===========================================================================
# ВКЛАДКА 4: ДИЗАЙН / ЭКСПОРТ
# ===========================================================================
with tab_design:
    st.markdown('<div class="pp-section">Тема оформления</div>', unsafe_allow_html=True)
    tids = list(A.THEMES.keys())
    cur_t = S.book.get("theme", "classic")
    pick = st.selectbox("Тема", tids, index=tids.index(cur_t) if cur_t in tids else 0,
                        format_func=lambda t: f'{A.THEMES[t]["emoji"]} {A.THEMES[t]["name"]}',
                        key=f"theme_pick_{S.book_gen}")
    if st.button("Применить ко всей книге") and pick != cur_t:
        snapshot()
        A.apply_theme(S.book, pick)
        st.rerun()
    st.caption("Тему можно также менять фразой во вкладке «Ассистент», в том числе для одного разворота.")

    st.markdown('<div class="pp-section">Обложка</div>', unsafe_allow_html=True)
    st.caption("Название книги меняется во вкладке «Фото».")
    S.book["subtitle"] = st.text_input("Подпись на обложке", value=S.book.get("subtitle", ""),
                                       key=f"dsub_{S.book_gen}")
    st.image(A.render_cover(S.book, 860), use_container_width=True)

    st.markdown('<div class="pp-section">Экспорт для печати</div>', unsafe_allow_html=True)
    dpi = st.radio("Качество PDF", [150, 200, 300], index=1, horizontal=True,
                   format_func=lambda d: f"{d} dpi")

    # слоты, которым не хватает пикселей для выбранного dpi
    weak = 0
    for sp_ in S.book["spreads"]:
        for j_, s_ in enumerate(sp_["slots"]):
            if s_["photo"] in S.photos:
                if A.slot_dpi(S.photos[s_["photo"]], s_["crop"], A.slot_width_cm(sp_["template"], j_)) < dpi:
                    weak += 1
    S.sr_on = st.checkbox("ИИ-апскейл (Real-ESRGAN) для слотов, где не хватает пикселей",
                          value=S.get("sr_on", False),
                          help="Нужен PyTorch. На CPU медленно, на GPU быстро. Применяется только при экспорте.")
    sr_engine = None
    if S.sr_on:
        sr_engine = get_upscaler()
        if sr_engine.available:
            st.caption(f"Движок: {sr_engine.name}. Слотов с нехваткой пикселей при {dpi} dpi: {weak}.")
        else:
            st.warning(f"ИИ-апскейл недоступен: {sr_engine.reason}. Экспорт пойдёт без него.")
            sr_engine = None

    if st.button("Подготовить PDF"):
        with st.spinner("Рендер разворотов..." + (" (с ИИ-апскейлом это может занять время)" if sr_engine else "")):
            S.pdf = A.export_pdf(S.book, S.photos, dpi=dpi, include_cover=True, sr=sr_engine)
    if S.pdf:
        st.download_button("⬇ Скачать PDF", S.pdf, file_name="photobook.pdf", mime="application/pdf")

    st.markdown('<div class="pp-section">Как это работает</div>', unsafe_allow_html=True)
    st.markdown(f"""
- **Оценка качества:** резкость (по лицу или по самому резкому участку), экспозиция, контраст, разрешение, эффективный dpi для каждого слота, поиск дублей.
- **Умный кроп:** рамка под форму слота, не режет лица, оставляет воздух над головой, ставит глаза и лица на линии третей. Детектор лиц: **{detector.name}**.
- **Автоулучшение:** автоуровни, яркость, локальный контраст, шумоподавление, адаптивная резкость.
- **ИИ-апскейл:** Real-ESRGAN (realesr-general-x4v3) достраивает детали там, где слоту не хватает пикселей для печати.
- **Автораскладка:** шаблоны выбираются под ориентацию и качество кадров, кадры идут по времени съёмки.
- **Приватность:** все фото обрабатываются локально в памяти и нигде не сохраняются, внешние ИИ-сервисы не вызываются.
""")


# ===========================================================================
# ВКЛАДКА: АССИСТЕНТ (чат, который меняет альбом по фразам)
# ===========================================================================
QUICK = [("🎂 День рождения", "оформи всю книгу как день рождения"),
         ("💍 Свадьба", "оформи всю книгу в стиле свадьбы"),
         ("✈️ Путешествие", "оформи всю книгу как путешествие"),
         ("🎄 Новый год", "оформи всю книгу в новогоднем стиле"),
         ("🔀 Перемешать фото", "перемешай все фото"),
         ("📅 По дате съёмки", "расставь все фото по дате съёмки"),
         ("🧩 Другая раскладка", "измени раскладку на всех разворотах"),
         ("⭐ Лучшие в крупные", "поставь лучшие по качеству фото в самые крупные слоты")]


def run_command(text: str):
    """Планирует (LLM или офлайн) и выполняет команду; одна команда = один шаг в «Отменить»."""
    hist = [{"role": m["role"], "content": m["content"]} for m in S.chat if m["role"] in ("user", "assistant")]
    S.chat.append({"role": "user", "content": text})
    with st.spinner("Думаю..."):
        plan = AS.handle(text, S.book, S.photos, history=hist, api_key=S.api_key,
                         models=S.models, use_llm=S.use_llm)
    report = []
    if plan["actions"]:
        snapshot()
        report = AS.execute(plan["actions"], S.book, S.photos, active_photos())
        S.book_gen += 1
    S.chat.append({"role": "assistant", "content": plan["reply"] or ("Готово." if report else "Не поняла запрос."),
                   "report": report, "source": plan["source"], "llm_error": plan["llm_error"],
                   "errors": plan["errors"], "meta": plan["meta"]})
    st.rerun()


with tab_chat:
    st.markdown('<div class="pp-section">Ассистент: меняйте альбом обычными фразами</div>', unsafe_allow_html=True)
    st.caption("Например: «оформи как день рождения», «на втором развороте сделай новогодний стиль», "
               "«перемешай фото», «поставь лучшее фото на первый слот», «назови книгу «Лето»». "
               "Любое изменение можно отменить кнопкой «Отменить» вверху.")

    with st.expander("⚙ Модель и ключ OpenRouter", expanded=not S.api_key):
        S.api_key = st.text_input("API-ключ OpenRouter", value=S.api_key, type="password",
                                  help="Можно задать переменной окружения OPENROUTER_API_KEY или в .streamlit/secrets.toml")
        models_txt = st.text_input("Модели (через запятую: первая основная, остальные запасные)",
                                   value=", ".join(S.models))
        S.models = [m.strip() for m in models_txt.split(",") if m.strip()] or list(AS.DEFAULT_MODELS)
        S.use_llm = st.checkbox("Использовать ИИ-модель (без неё работает офлайн-разбор по ключевым словам)",
                                value=S.use_llm)
        if st.button("Проверить модели"):
            ms, note = AS.resolve_models(S.models)
            for m in S.models:
                info = AS.model_info(m)
                if info:
                    st.success(f"{m}: ${info['in_per_m']:.3f} / ${info['out_per_m']:.3f} за 1M токенов (вход/выход), "
                               f"контекст {info['ctx']}")
                else:
                    st.warning(f"{m}: не найдена в каталоге OpenRouter или каталог недоступен")
            if note:
                st.caption(note)
        st.caption("Приватность: в модель уходит только описание структуры книги (шаблоны, номера слотов и фото, "
                   "оценки качества). Сами фотографии и имена файлов не отправляются.")

    if S.photos:
        with st.expander("🔢 Номера фото (для команд вроде «поменяй местами фото 2 и 5»)"):
            num, _ = AS.numbering(S.photos)
            used = A.used_ids(S.book)
            tcols = st.columns(5)
            for k, (pid, n) in enumerate(sorted(num.items(), key=lambda kv: kv[1])):
                with tcols[k % 5]:
                    st.image(thumb(S.photos[pid], 160), use_container_width=True)
                    st.caption(f"№{n}" + ("" if pid in used else " · не в книге"))

    qc = st.columns(4)
    for i, (label, text) in enumerate(QUICK):
        if qc[i % 4].button(label, key=f"quick_{i}", use_container_width=True):
            run_command(text)

    with st.expander("👁 Предпросмотр альбома", expanded=True):
        st.image(A.render_cover(S.book, 700), use_container_width=True, caption="Обложка")
        for si, sp in enumerate(S.book["spreads"][:4]):
            st.image(render_cached(sp, 700, False, False, S.get("enhance", True)), use_container_width=True,
                     caption=f"Разворот {si + 1} · тема: {A.THEMES.get(sp.get('theme', 'classic'), A.THEMES['classic'])['name']}")
        if len(S.book["spreads"]) > 4:
            st.caption(f"Показаны первые 4 из {len(S.book['spreads'])} разворотов, остальные во вкладке «Развороты».")

    for m in S.chat:
        with st.chat_message(m["role"]):
            st.markdown(m["content"])
            if m["role"] == "assistant":
                for line in m.get("report", []):
                    st.markdown(f"- {line}")
                for e in m.get("errors", []):
                    st.caption(f"⚠ пропущено: {e}")
                if m.get("llm_error"):
                    st.caption(f"⚠ ИИ-модель недоступна ({m['llm_error']}). Использован офлайн-разбор.")
                meta = m.get("meta") or {}
                if m.get("source") == "llm":
                    cost = f" · ≈${meta['cost']:.5f}" if meta.get("cost") else ""
                    tk = meta.get("tokens") or (0, 0)
                    st.caption(f"Модель: {meta.get('model', '?')} · {meta.get('seconds', '?')} с · "
                               f"токенов {tk[0]}+{tk[1]}{cost}")
                else:
                    st.caption("Офлайн-разбор по ключевым словам")

    if hasattr(st, "chat_input"):
        prompt = st.chat_input("Что изменить в альбоме?")
    else:
        prompt = st.text_input("Что изменить в альбоме?", key="chat_fallback")
    if prompt:
        run_command(prompt)
