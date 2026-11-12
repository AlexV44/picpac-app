/* PicPac — мобильный клиент (PWA). Вся логика ИИ на сервере, здесь только интерфейс.
   Экраны: home (Главная) · app (Фото / Альбом) · cart (Корзина) · checkout · orders (Заказы) · track. */
"use strict";

const $ = (s, r = document) => r.querySelector(s);
const view = $("#view"), appEl = $("#app");
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const LS = { get(k, d = "") { try { return localStorage.getItem(k) ?? d; } catch (e) { return d; } }, set(k, v) { try { localStorage.setItem(k, v); } catch (e) {} } };
const sleep = ms => new Promise(r => setTimeout(r, ms));

let S = null;                 // состояние с сервера (проекты, корзина, текущий проект)
let screen = "home";          // home | app | cart | checkout | orders | track
let tab = "photos";           // photos | spreads (внутри screen=app)
let flow = false;             // шапка «Фото–Альбом–Заказ» на экране корзины
let curSpread = 0, themeScope = "all";
let ctxPhoto = null, ctxExplicit = false;   // фото, о котором спрашиваем ИИ
let chatOpen = false, chatDraft = "", chatJob = null;
let orders = [], order = null, trackErr = "", trackFrom = "orders", pollT = null;
let pf = null;                // проверка корзины перед печатью
let ed = null;                // редактор кадра
let ev = null;                // редактор правки фото
let readyEdit = null;         // готовая правка, пока редактор был закрыт
const CLIENT = (() => { let c = LS.get("pp_client"); if (!c) { c = "c" + Math.random().toString(36).slice(2, 12); LS.set("pp_client", c); } return c; })();
const loadCk = () => { try { return JSON.parse(LS.get("pp_ck", "{}")); } catch (e) { return {}; } };
let ck = Object.assign({ delivery: "pickup", payment: "online", name: LS.get("pp_name"), phone: LS.get("pp_phone"), address: "", comment: "", promo: "", err: "", risk: false }, loadCk(), { err: "", risk: false });
let quoteLoy = null, quote = null, promoMsg = "", checkoutKey = LS.get("pp_key") || (() => { const k = "k" + Math.random().toString(36).slice(2, 14); LS.set("pp_key", k); return k; })();
let me = null;                // текущий пользователь (гость / клиент / администратор)
let supportOrder = "";        // заказ, к которому привязано обращение
let supT = null, adminTab = "orders", adminData = null, seenStage = {}, firstPoll = true;
const saveCk = () => LS.set("pp_ck", JSON.stringify({ delivery: ck.delivery, payment: ck.payment, name: ck.name, phone: ck.phone, address: ck.address, comment: ck.comment, promo: ck.promo }));

// Все запросы к /api/ получают токен сессии (или id гостя): права проверяет сервер.
const _fetch = window.fetch.bind(window);
window.fetch = (u, init = {}) => {
  if (typeof u === "string" && u.startsWith("/api/")) {
    const tok = LS.get("pp_token"), h = Object.assign({}, init.headers || {});
    if (tok) h.Authorization = "Bearer " + tok; else h["X-Client"] = CLIENT;
    init = Object.assign({}, init, { headers: h });
  }
  return _fetch(u, init);
};

if (new URLSearchParams(location.search).get("frame") === "0") document.body.classList.add("noframe");

// ---------------------------------------------------------------- сеть
let lastErr = "";
// Версия картинки фото: URL меняется только когда меняется само изображение, поэтому браузер держит картинки в кэше.
let _pvS = null, _pvM = {};
function pimg(id, w, extra = "") {
  if (_pvS !== S) { _pvS = S; _pvM = {}; (S.photos || []).forEach(p => { _pvM[p.id] = p.v; }); }
  return `/api/photo/${id}/img?w=${w}&v=${_pvM[id] || 0}${extra}`;
}
async function api(path, body, opts = {}) {
  // Ожидание всегда видно: явный busy показывается сразу, остальные запросы (кроме фоновых silent без wait) через 0,6 с
  let autoT = 0, autoOn = false;
  if (opts.busy) showPill(opts.busy);
  else if (!opts.silent || opts.wait) autoT = setTimeout(() => { autoOn = true; showPill(opts.wait || "Загружаю…"); }, 600);
  lastErr = "";
  try {
    const init = body instanceof FormData ? { method: "POST", body }
      : body !== undefined ? { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) } : {};
    const r = await fetch(path, init);
    const j = await r.json().catch(() => ({}));
    if (!r.ok) { lastErr = j.error || "Ошибка запроса"; if (!opts.silent) toast(lastErr); return null; }
    if (j.spreads && j.projects && !opts.noState) {
      await adopt(j);
      if (curSpread >= S.spreads.length) curSpread = Math.max(0, S.spreads.length - 1);
    }
    if (j.report && j.report.length && !opts.quiet) toast(j.report.join("\n"));
    if (j.failed && j.failed.length) toast("Не открылись: " + j.failed.join("; "));
    return j;
  } catch (e) {
    lastErr = "Нет связи с сервером";
    if (!opts.silent) toast(lastErr);
    return null;
  } finally {
    clearTimeout(autoT);
    if (opts.busy || autoOn) hidePill();
  }
}
// Шаги воронки (раз за сессию): сервер считает уникальных пользователей без личных данных
const _evSent = {};
function trackEv(ev) { if (_evSent[ev] || !S || !S.me || S.me.role === "anon") return; _evSent[ev] = 1; api("/api/event", { ev }, { noState: true, silent: true }); }
// Каталог и цены приходят из API (GET /api/catalog), а не из кода интерфейса: грузим при старте и заново, когда на сервере меняется версия.
let CAT = null;
async function loadCatalog(force) {
  try {
    const r = await fetch("/api/catalog", { cache: "no-cache" });
    if (r.ok) { CAT = await r.json(); LS.set("pp_cat", JSON.stringify(CAT)); }
  } catch (e) {}
  if (!CAT) { try { CAT = JSON.parse(LS.get("pp_cat", "null")); } catch (e) {} }   // нет сети: последний загруженный каталог
}
async function adopt(j) {
  if (!CAT || (j.catalog_v && CAT.version !== j.catalog_v)) await loadCatalog();
  j.catalog = CAT || { products: [], covers: [], delivery: [], payment: [], promos: [], limits: {}, currency: "руб." };
  S = j;
  setTimeout(checkIssues, 600);
}
async function refresh() { const j = await fetch("/api/state").then(r => r.json()).catch(() => null); if (j) await adopt(j); return j; }

// Небольшая плашка вместо блокирующего экрана: интерфейс остаётся живым.
let pillN = 0;
function showPill(t) { pillN++; $("#pill-t").textContent = t; $("#pill").hidden = false; }
function hidePill() { pillN = Math.max(0, pillN - 1); if (!pillN) $("#pill").hidden = true; }

let toastT;
function toast(t, onclick) {
  const el = $("#toast");
  el.textContent = t; el.style.whiteSpace = "pre-line"; el.hidden = false; el.onclick = onclick ? () => { el.hidden = true; onclick(); } : null;
  el.classList.toggle("tap", !!onclick);
  clearTimeout(toastT);
  toastT = setTimeout(() => { el.hidden = true; }, onclick ? 9000 : Math.min(7000, 2200 + t.length * 35));
}
function confetti() {
  const c = document.createElement("div"); c.className = "confetti";
  const cols = ["#e2575a", "#ffd23f", "#5ee6d0", "#a9bde0", "#ff7f6e", "#2f6b58"];
  for (let i = 0; i < 46; i++) {
    const p = document.createElement("i");
    p.style.left = Math.random() * 100 + "%"; p.style.background = cols[i % cols.length];
    p.style.animationDuration = 1.8 + Math.random() * 1.6 + "s"; p.style.animationDelay = Math.random() * .5 + "s";
    c.appendChild(p);
  }
  appEl.appendChild(c); setTimeout(() => c.remove(), 4200);
}
const fmt = v => Number(v).toFixed(2).replace(".", ",");
const money = v => fmt(v) + " руб.";
const plural = (n, a, b, c) => { n = Math.abs(n) % 100; const m = n % 10; return n > 10 && n < 20 ? c : m > 1 && m < 5 ? b : m === 1 ? a : c; };
const prod = id => S.catalog.products.find(p => p.id === id);
// Админ может отключать товары, обложки, доставку и оплату: новым покупателям предлагаем только включённые, старые проекты и заказы продолжают работать
const onlyOn = a => (a || []).filter(x => x.active !== false);
const defBook = () => (onlyOn(S.catalog.products).find(p => p.kind === "book") || onlyOn(S.catalog.products)[0] || S.catalog.products[0] || {}).id;
const defCover = () => (onlyOn(S.catalog.covers)[0] || S.catalog.covers[0] || {}).id;
const gradeLabel = g => ({ good: "хорошее", mid: "среднее", bad: "низкое" }[g]);
const curProj = () => S && S.proj;
const isPrint = () => curProj() && curProj().kind === "print";

// ---------------------------------------------------------------- листы
function openSheet(html) {
  $("#sheet").innerHTML = `<div class="grab"></div>` + html;
  $("#sheet").hidden = false; $("#sheet-bg").hidden = false; $("#sheet").scrollTop = 0;
}
function closeSheet() { $("#sheet").hidden = true; $("#sheet-bg").hidden = true; }
$("#sheet-bg").addEventListener("click", closeSheet);

// ---------------------------------------------------------------- навигация и шапка
function go(s, opts = {}) {
  if (s === "home" && me && me.role === "admin") s = "admin";
  screen = s; appEl.dataset.screen = s;
  if (opts.tab) tab = opts.tab;
  if (opts.flow !== undefined) flow = opts.flow;
  stopPoll();
  if (s === "home") { refreshOffers(); refreshLoy(); }
  if (s === "home" || s === "orders" || s === "track") startPoll();
  render(opts.anim !== false);
  view.scrollTop = 0;
}

function render(anim = false) {
  if (!S) return;
  view.classList.remove("scr");
  if (anim) { void view.offsetWidth; view.classList.add("scr"); }
  const keep = view.scrollTop;
  renderTop(); renderNav(); renderFab();
  ({ login: vLogin, admin: vAdmin, home: vHome, projects: vProjects, app: vApp, cart: vCart, checkout: vCheckout, orders: vOrders, track: vTrack, join: vJoin }[screen])();
  if (!anim) view.scrollTop = keep;
}

function stepsHtml(active) {
  const p = curProj(), hasPh = p && p.photos > 0 || (S.photos && S.photos.length > 0);
  const n2 = isPrint() ? "Печать" : "Альбом";
  const cls = i => active === i ? "on" : (i === 1 && hasPh && active > 1) || (i === 2 && active === 3) ? "done" : "";
  return `<div class="steps">
    <button data-step="1" class="${cls(1)}"><i>${cls(1) === "done" ? "✓" : "1"}</i>Фото</button>
    <button data-step="2" class="${cls(2)}"><i>${cls(2) === "done" ? "✓" : "2"}</i>${n2}</button>
    <button data-step="3" class="${cls(3)}"><i>3</i>Заказ</button></div>`;
}
const CLIENT_NAV = $("#nav").innerHTML;
const BACK = `<svg viewBox="0 0 24 24"><path d="m15 5-7 7 7 7"/></svg>`;

function renderTop() {
  const t = $("#top");
  if (screen === "login") t.innerHTML = "";
  else if (screen === "admin") t.innerHTML = `<span class="sp"></span><div id="ttl">Панель администратора</div><button class="ib" data-act="settings" aria-label="Профиль">${icon("user")}</button>`;
  else if (screen === "home") {
    const n = orders.filter(o => !o.finished).length;
    t.innerHTML = `<span class="sp"></span><span class="sp"></span><button class="ib prof" data-act="settings" aria-label="Профиль">${icon("user")}${n ? `<i class="pb">${n}</i>` : ""}</button>`;
  } else if (screen === "app" && curProj()) {
    const undo = tab === "spreads" && !isPrint();
    t.innerHTML = `${undo ? `
      <button class="ib" data-act="undo" ${S.can_undo ? "" : "disabled"} aria-label="Отменить"><svg viewBox="0 0 24 24"><path d="M9 7 4 12l5 5M4 12h10a6 6 0 0 1 0 12" transform="translate(0 -4)"/></svg></button>
      <button class="ib" data-act="redo" ${S.can_redo ? "" : "disabled"} aria-label="Повторить"><svg viewBox="0 0 24 24"><path d="m15 7 5 5-5 5M20 12H10a6 6 0 0 0 0 12" transform="translate(0 -4)"/></svg></button>` : `<span class="sp"></span>`}
      ${stepsHtml(tab === "photos" ? 1 : 2)}${undo ? "" : `<span class="sp"></span>`}`;
  } else if (screen === "app") {
    t.innerHTML = `<span class="sp"></span><div id="ttl">Фото и альбом</div><span class="sp"></span>`;
  } else if (screen === "projects") {
    t.innerHTML = `<span class="sp"></span><div id="ttl">Мои проекты</div><span class="sp"></span>`;
  } else if (screen === "cart") {
    t.innerHTML = flow ? `<span class="sp"></span>${stepsHtml(3)}<span class="sp"></span>` : `<span class="sp"></span><div id="ttl">Корзина</div><span class="sp"></span>`;
  } else if (screen === "checkout") {
    t.innerHTML = `<button class="ib" data-act="back-cart" aria-label="Назад">${BACK}</button>${stepsHtml(3)}<span class="sp"></span>`;
  } else if (screen === "orders") {
    t.innerHTML = `<button class="ib" data-act="back-home" aria-label="Назад">${BACK}</button><div id="ttl">История заказов</div><button class="ib" data-act="findcode" aria-label="Найти по коду"><svg viewBox="0 0 24 24"><circle cx="11" cy="11" r="6"/><path d="m20 20-4.2-4.2"/></svg></button>`;
  } else if (screen === "join") {
    t.innerHTML = `<button class="ib" data-act="join-home" aria-label="На главную">${BACK}</button><div id="ttl">Совместный альбом</div><span class="sp"></span>`;
  } else if (screen === "track") {
    t.innerHTML = `<button class="ib" data-act="back-orders" aria-label="Назад">${BACK}</button><div id="ttl">${order ? "Заказ " + esc(order.code) : "Заказ"}</div><span class="sp"></span>`;
  }
}

let navMode = "";
function renderNav() {
  const adm = me && me.role === "admin", nav = $("#nav");
  if ((adm ? "admin" : "client") !== navMode) {
    navMode = adm ? "admin" : "client";
    nav.innerHTML = adm ? ADMIN_TABS.map(t => `<button data-atab="${t[0]}">${icon(t[2])}<span>${t[1]}</span>${t[0] === "threads" ? `<i id="adm-badge" hidden></i>` : ""}</button>`).join("") : CLIENT_NAV;
  }
  if (adm) {
    nav.querySelectorAll("button").forEach(b => b.classList.toggle("on", screen === "admin" && b.dataset.atab === adminTab));
    const n = adminData ? adminData.stats.open_tickets : 0, bb = $("#adm-badge"); if (bb) { bb.hidden = !n; bb.textContent = n; }
    nav.hidden = screen === "login"; return;
  }
  const on = { home: "home", app: "photos", projects: "projects", cart: "cart", checkout: "cart", orders: "", track: "", join: "" }[screen];
  document.querySelectorAll("#nav button").forEach(b => b.classList.toggle("on", b.dataset.tab === on));
  const cb = $("#cart-badge"), n = S.cart.ids.length; cb.hidden = !n; cb.textContent = n;
  $("#nav").hidden = screen === "checkout" || screen === "login" || screen === "join";
}

function renderFab() {
  const fab = $("#fab"), show = !($("#full") && !$("#full").hidden) && screen === "app" && (tab === "photos" || tab === "spreads");
  fab.hidden = !show;
  if (show && !fab.firstChild) fab.innerHTML = robotSVG({ mood: "wave", face: true });
  $("#fab-hint").hidden = !(show && screen === "app" && tab === "spreads" && !LS.get("pp_hint") && !chatOpen);
  if (!show && chatOpen) closeChat();
  renderMenu(show);
}

function openTab(t) {
  closeSheet();
  if (t === "home") return go("home");
  if (t === "photos" || t === "spreads") return go("app", { tab: t === "photos" && screen === "app" ? tab : t });
  if (t === "projects") return go("projects");
  if (t === "cart") return openCart(false);
  if (t === "orders") return openOrders();
}

// ---------------------------------------------------------------- ГЛАВНАЯ
function svcArt(id) {
  const photo = (x, y, r, c1, c2) => `<g transform="rotate(${r} ${x + 17} ${y + 20})"><rect x="${x}" y="${y}" width="34" height="40" rx="3" fill="#fff"/><rect x="${x + 3}" y="${y + 3}" width="28" height="29" fill="${c1}"/><path d="M${x + 3} ${y + 32}l9-12 7 8 5-5 7 9z" fill="${c2}"/><circle cx="${x + 24}" cy="${y + 11}" r="3.5" fill="#fff6c4"/></g>`;
  const wave = `<path d="M0 62Q22 44 46 58T96 50V100H0Z" fill="rgba(255,255,255,.2)"/><path d="M0 78Q26 62 52 74T104 68V100H0Z" fill="rgba(255,255,255,.18)"/>`;
  const a = {
    prints: `${photo(14, 22, -14, "#bfe3f6", "#5a7fb5")}${photo(36, 16, 4, "#ffe0b0", "#e9893a")}${photo(58, 24, 18, "#d8efc9", "#4f8f5a")}`,
    classic: `<g><path d="M10 40Q34 30 54 40V82Q34 72 10 82Z" fill="#fff"/><path d="M98 40Q74 30 54 40V82Q74 72 98 82Z" fill="#f3f7fb"/><rect x="17" y="46" width="28" height="20" rx="2" fill="#bfe3f6"/><path d="M17 66l9-9 6 6 5-4 8 7z" fill="#5a7fb5"/><rect x="63" y="46" width="28" height="9" rx="2" fill="#ffe0b0"/><rect x="63" y="58" width="28" height="9" rx="2" fill="#d8efc9"/></g>`,
    premium: `<g transform="rotate(-6 54 56)"><rect x="24" y="24" width="56" height="64" rx="5" fill="#f4e6d8"/><rect x="20" y="22" width="56" height="64" rx="5" fill="#fff6ea"/><rect x="28" y="30" width="40" height="32" rx="3" fill="#bfe3f6"/><path d="M28 62l13-16 9 10 7-7 11 13z" fill="#5a7fb5"/><circle cx="58" cy="40" r="4" fill="#fff6c4"/><rect x="30" y="68" width="26" height="4" rx="2" fill="#c9885a"/><rect x="30" y="75" width="16" height="3" rx="1.5" fill="#d9a77d"/></g>`,
    digital: `<g><rect x="30" y="14" width="40" height="72" rx="8" fill="#2b2d42"/><rect x="34" y="22" width="32" height="52" rx="3" fill="#bfe3f6"/><path d="M34 74l10-14 8 9 6-6 8 11z" fill="#5a7fb5"/><circle cx="58" cy="34" r="4" fill="#fff6c4"/><circle cx="50" cy="80" r="2.4" fill="#8a8fa6"/></g><g transform="rotate(10 82 62)"><rect x="70" y="42" width="26" height="34" rx="2.5" fill="#fff"/><rect x="73" y="45" width="20" height="22" fill="#ffe0b0"/></g>`,
    lite: `<g transform="rotate(5 54 56)"><rect x="26" y="22" width="52" height="66" rx="3" fill="#e9b73f"/><rect x="26" y="22" width="7" height="66" rx="2" fill="#d19d2b"/><rect x="38" y="30" width="34" height="26" rx="2" fill="#fff"/><path d="M38 56l10-12 7 8 5-5 12 9z" fill="#5a7fb5"/><circle cx="62" cy="38" r="3.4" fill="#fbd277"/><rect x="38" y="64" width="26" height="4" rx="2" fill="#fff"/><rect x="38" y="72" width="16" height="3" rx="1.5" fill="#fff" opacity=".7"/></g>`,
  }[id] || "";
  return `<svg viewBox="0 0 108 100" class="art" aria-hidden="true">${wave}${a}</svg>`;
}
const DARK_TXT = { classic: 1, lite: 1 };

function svcCard(p) {
  return `<button class="svc ${DARK_TXT[p.id] ? "dk" : ""}" data-svc="${p.id}" style="--c:${p.color}">
    ${svcArt(p.id)}<b>${esc(p.name)}</b>
    <span class="pr">от <em>${fmt(p.from_price)}</em> руб. <s>${fmt(p.old_price)}</s></span></button>`;
}

function draftCard(p) {
  return `<div class="draft" data-open="${p.id}">
    <div class="th" style="background:${p.color}"><img loading="lazy" src="/api/project/${p.id}/thumb.jpg?w=240&v=${Math.round(p.updated)}" alt="" onerror="this.remove()"></div>
    <b>${esc(p.name)}</b>
    <small>${p.kind === "book" ? `${p.photos} фото · ${p.spreads} разв.` : `${p.photos} фото · ${p.line_qty} отп.`}</small>
    ${p.issue ? `<span class="inc warn">нужна замена</span>` : p.in_cart ? `<span class="inc">в корзине</span>` : ""}
    <button class="x" data-delproj="${p.id}" aria-label="Удалить">✕</button></div>`;
}

function orderBrief(o) {
  const items = o.items.map(i => i.kind === "book" ? `${i.qty} × ${esc(i.title)}` : `${i.qty} отп.`).join(", ");
  return `<button class="ocard ${o.finished ? "fin" : ""}" data-track="${esc(o.code)}">
    <div class="oi">${o.status_icon}</div>
    <div class="ob"><div class="r1"><b>${esc(o.status_title)}</b><span class="oc">${esc(o.code)}</span></div>
      <div class="r2">${items}</div>
      <div class="obar"><i style="width:${Math.max(6, Math.round(o.progress * 100))}%"></i></div>
      <div class="r3"><span>${money(o.price.total)}</span><span>${o.finished ? "Выдан" : "Срок: " + esc(o.eta)}</span></div></div></button>`;
}

// ---------------------------------------------------------------- ПЕРСОНАЛЬНЫЕ ПРЕДЛОЖЕНИЯ (данные клиента из CRM PicPac)
let offersData = null, offersAt = 0;
const persOn = () => LS.get("pp_pers") !== "0";
const canPers = () => !!(me && !me.guest && me.role === "client");
async function loadOffers(force) {
  if (!canPers() || !persOn()) { offersData = null; return; }
  if (!force && offersData && Date.now() - offersAt < 120000) return;
  const j = await api("/api/offers", undefined, { noState: true, silent: true });
  offersData = j && j.offers ? j : null; offersAt = Date.now();
}
function refreshOffers() {
  const b = JSON.stringify(offersData);
  loadOffers().then(() => { if (screen === "home" && $("#sheet").hidden && JSON.stringify(offersData) !== b) render(); });
}
function offerCard(o) {
  return `<div class="offer k-${o.kind}"><div class="oh">${o.badge ? `<span class="ob2">${esc(o.badge)}</span>` : ""}<b>${esc(o.title)}</b></div>
    <p>${esc(o.text)}</p><small class="why">Почему вы это видите: ${esc(o.why)}</small>
    ${o.cta ? `<button class="btn sm p" data-offer="${esc(o.cta.product)}">${esc(o.cta.label)}</button>` : `<span class="auto">Применится при оформлении</span>`}
    ${o.valid_until && o.checkout ? `<small class="mut">Действует до ${esc(o.valid_until)}</small>` : ""}</div>`;
}
function offersHtml() {
  if (!offersData || !offersData.offers.length) return "";
  const c = offersData.customer;
  return `<div class="sec">Для вас${c && c.name ? ", " + esc(c.name) : ""}</div><div class="offers">${offersData.offers.map(offerCard).join("")}</div>`;
}
function persHtml() {
  if (!canPers()) return "";
  if (!persOn()) return `<div class="perk off">Персональные предложения выключены. <button class="chip sm" data-act="pers-on">Включить</button></div>`;
  const D = quote && quote.disc_options;
  if (!D || (!D.options.length && !D.free_offer)) return "";
  const row = (id, badge, t, sub) => `<button class="dr ${D.chosen === id ? "on" : ""}" role="radio" aria-checked="${D.chosen === id}" data-act="setdisc" data-id="${esc(id)}"><span class="rd"></span><span class="ob2">${esc(badge)}</span><span class="dt"><b>${esc(t)}</b>${sub ? `<small>${esc(sub)}</small>` : ""}</span></button>`;
  return `<div class="perk"><div class="pkh"><b>Скидка к заказу</b><span>действует одна</span></div><div role="radiogroup" aria-label="Скидка">
    ${D.options.map(o => row(o.id, "−" + o.pct + "%", o.title, "")).join("")}
    ${row("none", "0", "Без скидки", "")}</div>
    ${D.free_offer ? `<button class="dr fr ${D.free_on ? "on" : ""}" role="switch" aria-checked="${D.free_on}" data-act="togfree"><span class="rd sq"></span><span class="ob2">доставка 0</span><span class="dt"><b>Бесплатная доставка</b></span></button>` : ""}</div>`;
}

function vHome() {
  const drafts = S.projects.filter(p => !p.ordered && (p.photos > 0 || p.id === S.cur));
  const act = orders.filter(o => !o.finished);
  const nm = me && !me.guest && me.name ? me.name.split(" ")[0] : "";
  let h = `<div class="phero"><img src="/img/hero.jpg" alt="" fetchpriority="high"><div class="htx">
      <small>${nm ? "Здравствуйте, " + esc(nm) : "PicPac"}</small>
      <h1>Ваши фото — <em>в красивые книги</em></h1>
      <p>Загрузите снимки и соберите альбом сами или попросите помощи у ИИ.</p></div></div>`;
  h += loyCard();
  h += offersHtml();
  if (act.length) h += `<div class="sec row2"><span>Активные заказы</span><button class="lnk" data-act="orders-all">История заказов ›</button></div><div class="olist">${act.map(orderBrief).join("")}</div>`;
  h += `<div class="sec">Услуги</div><div class="svcs">${onlyOn(S.catalog.products).map(svcCard).join("")}</div>
    <div class="pad"><button class="btn full" data-act="demostart">🐱 Попробовать на демо-фото</button></div>
    <div class="how"><div><span>📲</span>Загрузите фото</div><div><span>🤖</span>ИИ соберёт и улучшит</div><div><span>🛒</span>Оформите заказ</div><div><span>📦</span>Следите за статусом</div></div>
    <div class="pad small mut" style="margin:6px 0 24px;text-align:center">PicPac · прототип. Цены «от» как на сайте picpac.by, остальное — демонстрационный расчёт.</div>`;
  view.innerHTML = h;
}

function svcSheet(id) {
  trackEv("catalog");
  const p = prod(id);
  if (!p || p.active === false) return toast("Эта услуга сейчас недоступна");
  let body;
  if (p.kind === "book") {
    body = `<div class="sec" style="padding-top:4px">Обложка</div><div class="pad">${onlyOn(S.catalog.covers).map((c, i) => `
      <button class="opt ${i === 0 ? "on" : ""}" data-opt="cover:${c.id}"><span class="rd"></span><span class="g">${esc(c.name)}</span><span class="pr">${c.add ? (c.add > 0 ? "+" : "−") + money(Math.abs(c.add)) : "в цене"}</span></button>`).join("")}</div>
      <div class="pad small mut" style="margin-top:8px">Цена зависит от числа разворотов: от ${p.min_spreads} разв. — ${money(p.from_price)}, каждый следующий +${money(p.per_spread)}.</div>`;
  } else {
    body = `<div class="sec" style="padding-top:4px">Формат</div><div class="pad">${p.formats.map((f, i) => `
      <button class="opt ${i === 0 ? "on" : ""}" data-opt="format:${f.id}"><span class="rd"></span><span class="g">${esc(f.name)}</span><span class="pr">${money(p.from_price * f.mult)}</span></button>`).join("")}</div>
      <div class="sec">Бумага</div><div class="pad">${p.papers.map((f, i) => `
      <button class="opt ${i === 0 ? "on" : ""}" data-opt="paper:${f.id}"><span class="rd"></span><span class="g">${esc(f.name)}</span></button>`).join("")}</div>`;
  }
  svcPick = {};
  openSheet(`<h3>${esc(p.name)}</h3><div class="pad mut" style="margin:-4px 0 4px">${esc(p.text)}</div>
    <div class="facts"><span>${icon("calendar")} Изготовление: ${esc(p.lead)}</span><span>${icon("image")} ${esc(p.req)}</span></div>${body}
    <div class="pad" style="margin-top:14px">${p.kind === "book"
      ? `<div class="sec" style="padding:0 0 6px">Как создать альбом</div>
         <button class="mode" data-act="svctpl" data-id="${p.id}"><span class="mi">🎨</span><span class="mt"><b>По шаблону</b><small>Готовый стиль, раскладка и тексты: остаётся добавить фото</small></span><span class="qc">›</span></button>
         <button class="mode" data-act="svcgo" data-id="${p.id}"><span class="mi">✍️</span><span class="mt"><b>Вручную</b><small>Чистый альбом: вы сами выбираете тему, фон, раскладку и тексты</small></span><span class="qc">›</span></button>`
      : `<button class="btn p big full" data-act="svcgo" data-id="${p.id}">Выбрать фото →</button>`}</div>`);
}
let svcPick = {};

async function startProject(id, tpl) {
  const p = prod(id), params = {};
  if (p.kind === "book") params.cover = svcPick.cover || defCover(); else { params.format = svcPick.format || p.formats[0].id; params.paper = svcPick.paper || p.papers[0].id; }
  closeSheet();
  const j = await api("/api/project/new", { product: id, params, template: tpl || "" }, { busy: "Создаю проект…" });
  if (j) { curSpread = 0; go("app", { tab: "photos" }); toast(tpl ? "Шаблон готов. Добавьте фото и разложите их по разворотам." : "Чистый альбом создан. Добавьте фото и оформите страницы."); }
}

// ---------------------------------------------------------------- ШАБЛОНЫ АЛЬБОМОВ
let tplProd = null;
function tplGallery(pid) {
  tplProd = pid || tplProd || defBook();
  openSheet(`<h3>${icon("image")} Шаблоны альбомов</h3>
    <div class="pad small mut" style="margin:-4px 0 8px">Стиль, раскладка и подписи уже готовы. Выберите шаблон, добавьте свои фото и поправьте текст.</div>
    <div class="tgrid">${S.album_templates.map(t => `
      <button class="tcard" data-tpl="${t.id}"><img loading="lazy" decoding="async" src="/img/tpl/${t.id}-b.jpg" alt=""><b>${t.emoji} ${esc(t.name)}</b><small>${t.spreads} разворотов</small></button>`).join("")}</div>`);
}
function tplDetail(id) {
  const t = S.album_templates.find(x => x.id === id); if (!t) return;
  openSheet(`<h3>${t.emoji} ${esc(t.name)}</h3>
    <div class="pad small mut" style="margin:-4px 0 8px">${esc(t.text)}</div>
    <div class="tprev"><img src="/img/tpl/${t.id}-a.jpg" alt="Обложка"><img src="/img/tpl/${t.id}-c.jpg" alt="Первый разворот"><img src="/img/tpl/${t.id}-b.jpg" alt="Разворот"></div>
    <div class="pad small mut" style="margin-top:8px">Разворотов: ${t.spreads}. Все тексты, тему и раскладку потом можно изменить.</div>
    <div class="pad" style="margin-top:8px"><button class="btn p big full" data-act="tpluse" data-id="${t.id}">Использовать этот шаблон</button>
      <button class="btn full" style="margin-top:8px" data-act="tplback">Назад к шаблонам</button></div>`);
}

async function openProject(id) {
  const j = await api("/api/project/open", { id }, { busy: "Открываю…" });
  if (!j) return;
  curSpread = 0;
  go("app", { tab: S.photos.length ? "spreads" : "photos" });
}

// ---------------------------------------------------------------- ФОТО
function noProject(txt) {
  view.innerHTML = `<div class="empty">${catSVG({ mood: "sleep" }).replace('class="mascot', 'style="width:120px;height:120px;margin:0 auto" class="mascot')}
    <h3>Нет открытого проекта</h3>${txt}<div class="cta"><button class="btn p full" data-tab="home">Выбрать услугу</button></div></div>`;
}

function vApp() {
  if (!curProj()) return noProject("Выберите услугу на главной или продолжите черновик.");
  (tab === "photos" ? vPhotos : isPrint() ? vPrints : vSpreads)();
}

function viewSwitch() {
  const pr = curProj(); if (!pr) return "";
  return `<div class="seg vsw" role="tablist"><button class="${tab === "photos" ? "on" : ""}" data-step="1">Фото${S.photos.length ? ` · ${S.photos.length}` : ""}</button><button class="${tab === "spreads" ? "on" : ""}" data-step="2">${isPrint() ? "Печать" : "Альбом"}</button></div>`;
}
const MONTHS = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября", "ноября", "декабря"];
function dayLabel(day) {
  if (!day) return "Без даты";
  const [y, m, d] = day.split("-").map(Number), t = new Date();
  const today = new Date(t.getFullYear(), t.getMonth(), t.getDate()), dd = new Date(y, m - 1, d), diff = Math.round((today - dd) / 864e5);
  const base = `${d} ${MONTHS[m - 1]}${y !== t.getFullYear() ? " " + y : ""}`;
  return diff === 0 ? "Сегодня · " + base : diff === 1 ? "Вчера · " + base : base;
}
function photoCell(p) {
  return `<button class="ph ${p.excluded || p.dup ? "off" : ""}" data-photo="${p.id}" aria-label="Фото: ${esc(p.name)}">
        <img loading="lazy" decoding="async" src="${pimg(p.id, 260)}" alt="">
        <span class="dot ${p.grade}"></span>
        ${p.used ? `<span class="used">${isPrint() ? "×" + p.qty : "в книге"}</span>` : ""}${p.edited ? `<span class="mg">✨</span>` : ""}
        ${p.by ? `<span class="by">от ${esc(p.by)}</span>` : ""}
        ${p.dup ? `<span class="tag">похожий кадр</span>` : p.excluded ? `<span class="tag">не используется</span>` : ""}
      </button>`;
}
function photosByDate() {
  const newFirst = LS.get("pp_sort") !== "old";
  const groups = {};
  S.photos.forEach(p => { (groups[p.day || ""] = groups[p.day || ""] || []).push(p); });
  const days = Object.keys(groups).sort(); if (newFirst) days.reverse();
  return `<div class="sortbar"><span>Сортировка</span><button class="chip" data-act="sortdate">${newFirst ? "Сначала новые" : "Сначала старые"} ⇅</button></div>` +
    days.map(day => {
      const ps = groups[day].slice().sort((a, b) => newFirst ? b.ts - a.ts : a.ts - b.ts);
      return `<div class="dgrp"><div class="dh"><b>${dayLabel(day)}</b><span>${ps.length}</span></div><div class="grid">${ps.map(photoCell).join("")}</div></div>`;
    }).join("");
}
function projRow(p) {
  const mode = p.params && p.params.mode === "template" ? "По шаблону" : p.params && p.params.mode === "manual" ? "Вручную" : "";
  const st = p.issue && !p.ordered ? `<span class="pst warn">нужна замена</span>` : p.ordered ? `<span class="pst ok">заказан</span>` : p.in_cart ? `<span class="pst cart">в корзине</span>` : p.id === S.cur ? `<span class="pst on">открыт</span>` : `<span class="pst">черновик</span>`;
  const d = new Date(p.updated * 1000), when = isNaN(d) ? "" : `${d.getDate()} ${MONTHS[d.getMonth()]}`;
  return `<div class="prj" data-open="${p.id}">
    <div class="th" style="background:${p.color}"><img loading="lazy" decoding="async" src="/api/project/${p.id}/thumb.jpg?w=240&v=${Math.round(p.updated)}" alt="" onerror="this.remove()"></div>
    <div class="pi"><b>${esc(p.name)}</b><small>${esc(p.product_name)}${mode ? " · " + mode : ""}</small>
      <small>${p.kind === "book" ? `${p.photos} фото · ${p.spreads} разв.` : `${p.photos} фото · ${p.line_qty} отп.`}${when ? " · " + when : ""}</small>${st}</div>
    ${p.ordered ? "" : `<button class="x" data-delproj="${p.id}" aria-label="Удалить проект">${icon("trash")}</button>`}</div>`;
}
function vProjects() {
  const all = S.projects, work = all.filter(p => !p.ordered && (p.photos > 0 || p.id === S.cur || p.kind === "book")), done = all.filter(p => p.ordered);
  let h = `<div class="pad" style="margin-top:12px"><button class="btn p big full" data-act="newproj">＋ Новый проект</button></div>`;
  if (!work.length && !done.length) h += `<div class="empty">${catSVG({ mood: "sleep" }).replace('class="mascot', 'style="width:110px;height:110px;margin:0 auto" class="mascot')}<h3>Проектов пока нет</h3>Начните новый: выберите услугу, шаблон или соберите альбом сами. Незаконченные проекты сохраняются здесь.</div>`;
  if (work.length) h += `<div class="sec">В работе · ${work.length}</div><div class="prjs">${work.map(projRow).join("")}</div>`;
  if (done.length) h += `<div class="sec">Заказанные · ${done.length}</div><div class="prjs">${done.map(projRow).join("")}</div>`;
  view.innerHTML = h;
}

function vPhotos() {
  const pr = curProj(), n = S.photos.length, act = S.photos.filter(p => !p.excluded && !p.dup).length;
  let h = viewSwitch() + `<div class="pj"><span class="dotc" style="background:${pr.color}"></span><b>${esc(pr.product_name)}</b><span class="mut">${isPrint() ? "· " + esc(pr.params.format || "") : ""}</span></div>`;
  if (!n) {
    h += `<div class="dropzone">${catSVG({ mood: "sleep" }).replace('class="mascot', 'style="width:110px;height:110px;margin:0 auto" class="mascot')}
      <h3 style="margin:8px 0 4px;font-size:19px">Пока пусто</h3>
      <div class="mut" style="margin-bottom:14px">Выберите снимки из галереи. ${isPrint() ? "Каждое будет напечатано." : "Они появятся здесь по датам. Ничего не меняется само: в альбом вы разложите их, когда захотите."}</div>
      <button class="btn p big full" data-act="pick">＋ Выбрать фото</button>
      <button class="btn full" style="margin-top:10px" data-act="demo">🐱 Демо-фото</button></div>`;
  } else {
    h += `<div class="pad row"><button class="btn p grow" data-act="pick">＋ Добавить фото</button><button class="btn" data-act="demo">Демо</button></div>`;
    const bad = S.photos.filter(p => p.grade === "bad").length, dup = S.photos.filter(p => p.dup).length;
    if (bad || dup) h += `<div class="note">${bad ? `Низкое качество: ${bad}. ` : ""}${dup ? `Похожих кадров: ${dup}. ` : ""}Нажмите на фото: можно спросить ИИ, как его улучшить.</div>`;
    h += photosByDate();
    h += `<div class="pad" style="margin-top:14px"><button class="btn g big full" data-step="2">${isPrint() ? "Дальше: количество отпечатков →" : "Дальше: альбом →"}</button></div>`;
  }
  view.innerHTML = h;
}

function photoSheet(id) {
  const p = S.photos.find(x => x.id === id); if (!p) return;
  openSheet(`
    ${p.auto ? `<div class="ba" id="ba"><img src="${pimg(p.id, 700)}" alt=""><div class="aft"><img src="${pimg(p.id, 700, "&enh=1")}" alt=""></div><div class="bar"></div><span class="lb l">До</span><span class="lb r">После</span><input type="range" min="0" max="100" value="50" aria-label="Сравнить до и после"></div>`
      : `<div class="pad"><img loading="lazy" decoding="async" src="${pimg(p.id, 700)}" style="width:100%;border-radius:14px;max-height:250px;object-fit:contain;background:#eee" alt=""></div>`}
    <h3>${esc(p.name)}</h3>
    <div class="note ${p.grade === "good" ? "good" : p.grade === "bad" ? "bad" : ""}">
      <b>Оценка ИИ: ${Math.round(p.score)}/100 (${gradeLabel(p.grade)})</b><br>${esc(p.message)}<br>
      <span class="small">${p.w}×${p.h} px${p.faces ? `, лиц: ${p.faces}` : ""}${p.issues.length ? " · " + esc(p.issues.join(", ")) : ""}</span>
      ${p.ops && p.ops.length ? `<br><span class="small">${p.auto ? "Автоулучшение применено: " : "Можно улучшить автоматически: "}${esc(p.ops.join(", "))}</span>` : ""}
      ${p.dup ? `<br><span class="small">Почти дубль другого кадра — не используется.</span>` : ""}
    </div>
    <button class="act ai" data-sh="ask" data-id="${p.id}"><span class="em">🤖</span>Спросить у ИИ про это фото</button>
    ${isPrint() ? "" : `<button class="act" data-sh="acrop" data-id="${p.id}"><span class="em">🎯</span><span style="flex:1">Автокадрирование этого фото<small class="mut" style="display:block">Подобрать кадр по лицам и композиции${p.used ? "" : " (после того, как фото попадёт в альбом)"}</small></span></button>`}
    <button class="act" data-sh="enh" data-id="${p.id}" data-on="${p.auto ? 0 : 1}"><span class="em">⚖️</span><span style="flex:1">${p.auto ? "Не улучшать это фото" : "Улучшить это фото автоматически"}<small class="mut" style="display:block">${p.auto ? "Сейчас применяются тон, контраст и резкость" : "Фото печатается как есть, пока вы не включите улучшение"}</small></span></button>
    ${isPrint() ? "" : `<button class="act" data-sh="place" data-id="${p.id}"><span class="em">📖</span>Поставить в слот…</button>`}
    <div class="act2"><button class="act" data-sh="mv" data-id="${p.id}" data-d="-1"><span class="em">⬆️</span>Раньше</button><button class="act" data-sh="mv" data-id="${p.id}" data-d="1"><span class="em">⬇️</span>Позже</button></div>
    <button class="act" data-sh="rot" data-id="${p.id}" data-d="90"><span class="em">↻</span>Повернуть вправо</button>
    <button class="act" data-sh="rot" data-id="${p.id}" data-d="-90"><span class="em">↺</span>Повернуть влево</button>
    <button class="act" data-sh="excl" data-id="${p.id}"><span class="em">${p.excluded ? "✅" : "🚫"}</span>${p.excluded ? "Использовать снова" : "Не использовать"}</button>
    <button class="act dng" data-sh="del" data-id="${p.id}"><span class="em">🗑</span>Удалить</button>`);
  const ba = $("#ba");
  if (ba) { const r = ba.querySelector("input"), aft = ba.querySelector(".aft"), bar = ba.querySelector(".bar");
    const set = () => { aft.style.clipPath = `inset(0 0 0 ${r.value}%)`; bar.style.left = r.value + "%"; }; r.oninput = set; }
}

// ---------------------------------------------------------------- АЛЬБОМ (книги)
function vSpreads() {
  const pr = curProj();
  const empty = S.spreads.reduce((n, sp) => n + sp.slots.filter(s => !s.photo).length, 0);
  const free = S.photos.filter(p => !p.used && !p.excluded && !p.dup).length;
  let h = viewSwitch();
  h += `<div class="spread"><div class="hd"><b>Обложка <span class="mut" style="font-weight:600">· ${esc(S.title)}</span></b>
      <button class="chip sm" data-act="coversheet">✏️ Название и тема</button></div>
    <div class="canvas cover" data-act="coversheet"><img src="/api/cover.jpg?w=760&v=${S.cover_v}" alt=""></div></div>`;
  if (!S.photos.length) h += `<div class="note">Сначала добавьте фото на вкладке «Фото»: ИИ сам заполнит серые слоты.</div>`;
  else if (empty && free) h += `<div class="note good">Свободных фото: ${free}. <button class="chip" style="margin-left:6px" data-act="fill">✨ Заполнить слоты</button></div>`;
  S.spreads.forEach((sp, i) => {
    const tn = S.templates.find(t => t.id === sp.template).name;
    h += `<div class="spread"><div class="hd"><b>Разворот ${i + 1} <span class="mut" style="font-weight:600">· ${esc(tn)}</span></b>
      <button class="more" data-sp="${i}" aria-label="Меню разворота">•••</button></div>
      <div class="canvas ${i === curSpread ? "sel" : ""}">
        <img loading="lazy" decoding="async" src="/api/spread/${i}.jpg?w=760&v=${sp.v}" alt="">
        ${sp.rects.map((r, j) => `<button class="hit" data-slot="${i}:${j}" style="left:${r[0] * 100}%;top:${r[1] * 100}%;width:${r[2] * 100}%;height:${r[3] * 100}%" aria-label="Слот ${j + 1}"></button>`).join("")}
      </div></div>`;
  });
  h += `<button class="btn full addsp" data-act="addspread">＋ Добавить разворот</button>
    <div class="cta"><div class="sumline"><span>${S.spreads.length} ${plural(S.spreads.length, "разворот", "разворота", "разворотов")}</span><b>${money(pr.unit)} <small>за книгу</small></b></div>
    <button class="btn g big full" data-step="3">Дальше: в корзину →</button></div>`;
  view.innerHTML = h;
}

// Панель быстрых команд для альбома: действия, которые раньше были чипами над разворотами.
function quickSheet() {
  const empty = S.spreads.reduce((n, sp) => n + sp.slots.filter(s => !s.photo).length, 0);
  const free = S.photos.filter(p => !p.used && !p.excluded && !p.dup).length;
  const row = (act, em, t, d, extra = "") => `<button class="qrow" data-act="${act}"><span class="em">${em}</span><span class="qt"><b>${t}</b><small>${d}</small></span>${extra}</button>`;
  openSheet(`<h3>${icon("bolt")} Быстрые команды</h3>
    <button class="act" data-act="toggle-autoenh"><span class="em">⚖️</span><span style="flex:1">Автоулучшение всех фото<small class="mut" style="display:block">${S.auto_enhance ? "Включено: тон, контраст и резкость применяются ко всем фото, кроме отключённых" : "Выключено: ИИ ничего не ретуширует, пока вы не выберете сами"}</small></span><span class="switch ${S.auto_enhance ? "on" : ""}"></span></button>
    <div class="sec">Заполнение и кадрирование</div>
    ${row("fill", "🖼", "Заполнить слоты", empty ? `Пустых слотов: ${empty}, свободных фото: ${free}` : "Пустых слотов нет", free && empty ? `<span class="qn">${free}</span>` : "")}
    ${row("recrop", "🎯", "Автокадрирование", "Подобрать кадр по лицам и композиции во всех фото (по умолчанию кадр по центру)")}
    <div class="sec">Раскладка</div>
    ${row("balance", "⚖️", "Фото поровну", "Распределить фото равномерно по всем разворотам")}
    ${row("autoplan", "✨", "Разложить фото по альбому", "ИИ подберёт кадры и разложит их по разворотам с нуля")}
    ${row("addspread", "＋", "Добавить разворот", "Новая страница в конце альбома")}
    <div class="sec">Оформление</div>
    ${row("coversheet", "✏️", "Название и тема", "Заголовок, подпись, тема и обложка")}
    <div class="pad small mut" style="margin:10px 0">Любую из этих команд можно попросить и у робота-помощника своими словами.</div>`);
}

function coverSheet() {
  const pr = curProj(), p = prod(pr.product);
  const scopeSel = themeScope === "all" ? "all" : String(Math.min(+themeScope, S.spreads.length - 1));
  const cur = scopeSel === "all" ? S.theme : (S.spreads[+scopeSel] || {}).theme;
  openSheet(`<h3>Обложка и оформление</h3>
    <label class="field"><span>Заголовок обложки</span><input id="t-title" maxlength="40" value="${esc(S.title)}"></label>
    <label class="field"><span>Подпись</span><input id="t-sub" maxlength="50" value="${esc(S.subtitle)}"></label>
    <div class="pad"><button class="btn full" data-sh="savetitle">Сохранить название</button></div>
    <div class="sec">Тема</div>
    <div class="seg"><button class="${scopeSel === "all" ? "on" : ""}" data-scope="all">Вся книга</button>
      <button class="${scopeSel !== "all" ? "on" : ""}" data-scope="${curSpread}">Разворот ${curSpread + 1}</button></div>
    <div class="themes">${S.themes.map(t => `
      <button class="theme ${t.id === cur ? "on" : ""}" data-theme="${t.id}">
        <div class="sw" style="background:${t.bg}">${t.emoji}</div><b>${esc(t.name)}</b></button>`).join("")}</div>
    <div class="pad small mut" style="margin-top:6px">Тему можно попросить и у робота: «оформи как день рождения».</div>
    <div class="sec">Цвет обложки</div>
    <div class="swatches"><button class="swt auto ${S.cover_bg ? "" : "on"}" data-sh="covbg" data-c="" aria-label="По теме">тема</button>${BGS.map(c => `<button class="swt ${S.cover_bg && S.cover_bg.join() === c.join() ? "on" : ""}" style="background:rgb(${c})" data-sh="covbg" data-c="${c}" aria-label="Цвет обложки"></button>`).join("")}</div>
    <div class="sec">Обложка книги</div><div class="pad">${S.catalog.covers.filter(c => c.active !== false || c.id === pr.params.cover).map(c => `
      <button class="opt ${pr.params.cover === c.id ? "on" : ""}" data-sh="cover" data-id="${c.id}"><span class="rd"></span><span class="g">${esc(c.name)}</span><span class="pr">${c.add ? (c.add > 0 ? "+" : "−") + money(Math.abs(c.add)) : "в цене"}</span></button>`).join("")}</div>
    <button class="act" data-act="pdf"><span class="em">📄</span>Скачать PDF для печати</button>
    <button class="act" data-act="toggle-upscale"><span class="em">🔍</span><span style="flex:1">Улучшать качество фото в PDF</span><span class="switch ${S.settings.upscale ? "on" : ""}"></span></button>`);
}

function slotSheet(si, sj) {
  const sp = S.spreads[si], s = sp.slots[sj];
  if (!s.photo) {
    const free = S.photos.filter(p => !p.used && !p.excluded && !p.dup);
    openSheet(`<h3>Разворот ${si + 1}, слот ${sj + 1}</h3>
      <button class="act" data-sh="fill1"><span class="em">✨</span>ИИ подберёт фото сам</button>
      <button class="act" data-sh="pickphoto" data-si="${si}" data-sj="${sj}"><span class="em">🖼</span>Выбрать из моих фото…</button>
      ${free.length ? "" : `<div class="note">Все загруженные фото уже в книге или не подходят. Добавьте ещё на вкладке «Фото».</div>`}`);
    return;
  }
  const name = (S.photos.find(p => p.id === s.photo) || {}).name || "";
  openSheet(`<h3>${esc(name)}</h3>
    <div class="note ${s.grade === "good" ? "good" : s.grade === "bad" ? "bad" : ""}">${esc(s.msg || "")}<br><span class="small">≈ ${s.dpi} dpi в этом слоте (нужно ${S.thresholds.target_dpi}, минимум ${S.thresholds.min_dpi})</span></div>
    <button class="act ai" data-sh="ask" data-id="${s.photo}"><span class="em">🤖</span>Спросить у ИИ про это фото</button>
    <button class="act" data-sh="crop" data-si="${si}" data-sj="${sj}"><span class="em">✂️</span>Редактор кадра</button>
    <button class="act" data-sh="aicrop" data-si="${si}" data-sj="${sj}"><span class="em">🎯</span>Автокадрирование</button>
    <button class="act" data-sh="pickphoto" data-si="${si}" data-sj="${sj}"><span class="em">🔄</span>Заменить фото…</button>
    <button class="act dng" data-sh="clear" data-si="${si}" data-sj="${sj}"><span class="em">✖️</span>Убрать из слота</button>`);
}

function pickPhotoSheet(si, sj) {
  const list = S.photos.filter(p => !p.excluded && !p.dup);
  openSheet(`<h3>Выберите фото</h3><div class="pick">` + (list.length ? list.map(p => `
    <button class="ph" data-sh="setphoto" data-si="${si}" data-sj="${sj}" data-id="${p.id}">
      <img loading="lazy" decoding="async" src="${pimg(p.id,240)}" alt=""><span class="dot ${p.grade}"></span>${p.used ? `<span class="used">в книге</span>` : ""}</button>`).join("") : `<div class="empty">Нет фото</div>`) + `</div>`);
}

const BGS = [[255,255,255],[251,246,241],[246,240,230],[238,238,236],[253,232,232],[255,243,214],[226,240,230],[222,234,248],[236,228,246],[43,45,66],[30,52,45],[28,28,32],[96,36,48]];
function spreadMenu(i) {
  curSpread = i;
  const sp = S.spreads[i];
  openSheet(`<h3>Разворот ${i + 1}</h3>
    <div class="small mut" style="margin:0 18px 8px">Шаблон</div>
    <div class="tpls">${S.templates.map(t => `
      <button class="tpl ${t.id === sp.template ? "on" : ""}" data-sh="tpl" data-si="${i}" data-id="${t.id}">
        <div class="mini">${t.rects.map(r => `<i style="left:${r[0] * 100}%;top:${r[1] * 100}%;width:${r[2] * 100}%;height:${r[3] * 100}%"></i>`).join("")}</div>${t.n} фото</button>`).join("")}</div>
    <div class="small mut" style="margin:10px 18px 6px">Фон разворота</div>
    <div class="swatches"><button class="swt auto ${sp.bg ? "" : "on"}" data-sh="spbg" data-si="${i}" data-c="" aria-label="По теме">тема</button>${BGS.map(c => `<button class="swt ${sp.bg && sp.bg.join() === c.join() ? "on" : ""}" style="background:rgb(${c})" data-sh="spbg" data-si="${i}" data-c="${c}" aria-label="Цвет фона"></button>`).join("")}</div>
    <button class="act" data-sh="sptheme" data-si="${i}"><span class="em">🎨</span><span style="flex:1">Тема этого разворота<small class="mut" style="display:block">Сейчас: ${esc((S.themes.find(t => t.id === sp.theme) || {}).name || "Классика")}</small></span><span class="qc">›</span></button>
    ${sp.text_roles.length ? `<button class="act" data-sh="text" data-si="${i}"><span class="em">🔤</span>Изменить текст</button>` : ""}
    <button class="act" data-sh="up" data-si="${i}" ${i === 0 ? "hidden" : ""}><span class="em">⬆️</span>Переместить выше</button>
    <button class="act" data-sh="down" data-si="${i}" ${i === S.spreads.length - 1 ? "hidden" : ""}><span class="em">⬇️</span>Переместить ниже</button>
    <button class="act dng" data-sh="rmspread" data-si="${i}"><span class="em">🗑</span>Удалить разворот</button>`);
}

function textSheet(i) {
  const sp = S.spreads[i], field = sp.text_roles[0] === "quote" ? "quote" : "title";
  openSheet(`<h3>Текст на развороте ${i + 1}</h3>
    <label class="field"><span>${field === "quote" ? "Цитата" : "Заголовок"}</span><input id="txt" maxlength="60" value="${esc(sp.texts[field] || "")}"></label>
    ${field === "title" ? `<label class="field"><span>Подпись</span><input id="txt2" maxlength="60" value="${esc(sp.texts.subtitle || "")}"></label>` : ""}
    <div class="pad"><button class="btn p full" data-sh="savetext" data-si="${i}" data-f="${field}">Сохранить</button></div>`);
}


// ---------------------------------------------------------------- ПРОСМОТР АЛЬБОМА С ПЕРЕЛИСТЫВАНИЕМ
let fl = null;
function openFlip() {
  if (!curProj() || !S.spreads.length) return;
  closeSheet(); closeChat();
  const pages = [{ src: `/api/cover.jpg?w=1000&plain=1&v=${S.cover_v}`, label: "Обложка" }]
    .concat(S.spreads.map((sp, i) => ({ src: `/api/spread/${i}.jpg?w=1000&plain=1&v=${sp.v}`, label: `Разворот ${i + 1} из ${S.spreads.length}` })));
  fl = { pages, i: 0, busy: false };
  pages.forEach(p => { const im = new Image(); im.src = p.src; });
  const f = $("#full"); f.hidden = false;
  f.className = "flipv";
  f.innerHTML = `<div class="fh"><button data-flip="close">✕ Закрыть</button><b>Просмотр альбома</b><span style="width:84px"></span></div>
    <div class="fstage"><div class="book" id="book"></div></div>
    <div class="fnav"><button data-flip="prev" aria-label="Назад">‹</button><span id="flab"></span><button data-flip="next" aria-label="Вперёд">›</button></div>
    <div class="small mut" style="text-align:center;padding:0 16px 14px">Листайте пальцем или кнопками. Пустые слоты в просмотре не показываются. Поверните телефон для большего размера.</div>`;
  renderFab(); flipSize(); flipDraw();
  const bk = $("#book"); let x0 = null;
  bk.addEventListener("touchstart", e => { x0 = e.touches[0].clientX; }, { passive: true });
  bk.addEventListener("touchend", e => { if (x0 === null) return; const dx = e.changedTouches[0].clientX - x0; x0 = null; if (Math.abs(dx) > 36) flipGo(dx < 0 ? 1 : -1); }, { passive: true });
  bk.addEventListener("click", e => { const r = bk.getBoundingClientRect(); flipGo(e.clientX - r.left > r.width / 2 ? 1 : -1); });
  fl.onResize = () => flipSize(); window.addEventListener("resize", fl.onResize);
}
function flipSize() {
  const st = $(".fstage"); if (!st) return;
  const w = Math.min(st.clientWidth - 24, (st.clientHeight - 24) * 1.4, 760);
  const bk = $("#book"); bk.style.width = w + "px"; bk.style.height = w / 1.4 + "px";
}
function half(src, side, extra = "") { return `<div class="hf ${side}" style="background-image:url('${src}')${extra}"></div>`; }
function flipDraw() {
  const bk = $("#book"), P = fl.pages, i = fl.i;
  bk.innerHTML = half(P[i].src, "l") + half(P[i].src, "r") + `<i class="spine"></i>`;
  $("#flab").textContent = P[i].label;
  document.querySelector('[data-flip="prev"]').disabled = i === 0;
  document.querySelector('[data-flip="next"]').disabled = i === P.length - 1;
}
function flipGo(dir) {
  if (!fl || fl.busy) return;
  const n = fl.i + dir, P = fl.pages; if (n < 0 || n >= P.length) return;
  fl.busy = true;
  const bk = $("#book"), cur = P[fl.i], nx = P[n];
  if (dir > 0) {                                  // правая половина текущей страницы переворачивается и открывает левую половину следующей
    bk.innerHTML = half(cur.src, "l") + half(nx.src, "r") + `<div class="leaf r go-next"><div class="face f" style="background-image:url('${cur.src}');background-position:100% 0"></div><div class="face b" style="background-image:url('${nx.src}');background-position:0 0"></div></div><i class="spine"></i>`;
  } else {
    bk.innerHTML = half(nx.src, "l") + half(cur.src, "r") + `<div class="leaf l go-prev"><div class="face f" style="background-image:url('${cur.src}');background-position:0 0"></div><div class="face b" style="background-image:url('${nx.src}');background-position:100% 0"></div></div><i class="spine"></i>`;
  }
  const leaf = bk.querySelector(".leaf");
  leaf.addEventListener("animationend", () => { fl.i = n; fl.busy = false; flipDraw(); }, { once: true });
}
function flipClose() { if (fl && fl.onResize) window.removeEventListener("resize", fl.onResize); fl = null; $("#full").className = ""; closeFull(); }

// ---------------------------------------------------------------- ПЕЧАТЬ ФОТО (вместо альбома)
function vPrints() {
  const pr = curProj(), p = prod(pr.product), list = S.photos.filter(x => !x.excluded);
  if (!p) { view.innerHTML = `<div class="pad">${issueCard(pr)}</div>`; return; }
  const fm = p.formats.find(f => f.id === pr.params.format) || p.formats[0];
  let h = `<div class="sec">Формат и бумага</div>
    <div class="chips" style="padding-top:0">${p.formats.map(f => `<button class="chip ${f.id === pr.params.format ? "on" : ""}" data-pp="format:${f.id}">${esc(f.name)}</button>`).join("")}</div>
    <div class="chips" style="padding-top:0">${p.papers.map(f => `<button class="chip ${f.id === pr.params.paper ? "on" : ""}" data-pp="paper:${f.id}">${esc(f.name)}</button>`).join("")}</div>
    <div class="sec">Сколько отпечатков · ${money(pr.unit)} за штуку</div>`;
  if (!list.length) h += `<div class="note">Сначала добавьте фото на вкладке «Фото».</div>`;
  else {
    h += `<div class="pad row" style="margin-bottom:6px"><span class="mut small grow">Всем сразу:</span>
      ${[1, 2, 3].map(n => `<button class="chip" data-pqa="${n}">${n}</button>`).join("")}</div>`;
    h += `<div class="plist">` + list.map(x => `
      <div class="prow ${x.qty ? "" : "off"}"><button class="th" data-photo="${x.id}"><img loading="lazy" src="${pimg(x.id,160)}" alt=""><span class="dot ${x.grade}"></span></button>
        <div class="nm"><b>${esc(x.name)}</b><small>${esc(fm.name)} · ${x.grade === "bad" ? "низкое качество" : "качество " + gradeLabel(x.grade)}</small></div>
        <div class="step"><button data-pq="${x.id}:-1" aria-label="Меньше">−</button><i>${x.qty}</i><button data-pq="${x.id}:1" aria-label="Больше">+</button></div></div>`).join("") + `</div>`;
  }
  h += `<div class="cta"><div class="sumline"><span>${pr.line_qty} ${plural(pr.line_qty, "отпечаток", "отпечатка", "отпечатков")}</span><b>${money(pr.sum)}</b></div>
    <button class="btn g big full" data-step="3" ${pr.line_qty ? "" : "disabled"}>Дальше: в корзину →</button></div>`;
  view.innerHTML = h;
}

// ---------------------------------------------------------------- РЕДАКТОР КАДРА
function openCrop(si, sj) {
  const s = S.spreads[si].slots[sj];
  const p = S.photos.find(x => x.id === s.photo); if (!p) return;
  closeSheet();
  const aspect = s.aspect, W = p.w, H = p.h, wmax = Math.min(1, (H * aspect) / W);
  ed = { si, sj, p, aspect, wmax, W, H, crop: s.crop.slice() };
  ed.zoom = Math.min(4, Math.max(1, wmax / Math.max(ed.crop[2], 1e-6)));
  ed.cx = ed.crop[0] + ed.crop[2] / 2; ed.cy = ed.crop[1] + ed.crop[3] / 2;
  const f = $("#full"); f.hidden = false; renderFab();
  f.innerHTML = `<div class="fh"><button data-ed="close">Отмена</button><b>Редактор кадра</b><button data-ed="apply">Готово</button></div>
    <div class="fb">
      <div class="note ${s.grade === "good" ? "good" : s.grade === "bad" ? "bad" : ""}">${esc(s.msg || "")} <span class="small">≈ ${s.dpi} dpi</span></div>
      <div class="cropbox" id="cb"><img loading="lazy" decoding="async" src="${pimg(p.id,900)}" alt=""><div class="cropwin" id="cw"></div></div>
      <label class="field"><span>Масштаб</span><input type="range" id="zm" min="1" max="4" step="0.02" value="${ed.zoom}"></label>
      <div class="pad small mut" style="margin-bottom:8px">Тяните рамку пальцем. Сетка третей: ИИ ставит лица на линии и оставляет воздух над головой.</div>
      <div class="pad row"><button class="btn grow" data-ed="ai">🎯 Автокадрирование</button></div>
      <div id="lp" style="margin-top:12px"></div>
    </div>`;
  drawCrop();
  const cb = $("#cb"); let drag = null;
  cb.addEventListener("pointerdown", e => { cb.setPointerCapture(e.pointerId); drag = { x: e.clientX, y: e.clientY, cx: ed.cx, cy: ed.cy }; });
  cb.addEventListener("pointermove", e => {
    if (!drag) return;
    const r = cb.getBoundingClientRect();
    ed.cx = drag.cx + (e.clientX - drag.x) / r.width; ed.cy = drag.cy + (e.clientY - drag.y) / r.height; drawCrop();
  });
  const end = () => { drag = null; };
  cb.addEventListener("pointerup", end); cb.addEventListener("pointercancel", end);
  cb.addEventListener("wheel", e => { e.preventDefault(); ed.zoom = Math.min(4, Math.max(1, ed.zoom * (e.deltaY < 0 ? 1.06 : 0.94))); $("#zm").value = ed.zoom; drawCrop(); }, { passive: false });
  $("#zm").addEventListener("input", e => { ed.zoom = parseFloat(e.target.value); drawCrop(); });
}
function curCrop() {
  const cw = ed.wmax / ed.zoom, ch = cw * ed.W / ed.aspect / ed.H;
  const x = Math.min(Math.max(ed.cx - cw / 2, 0), 1 - cw), y = Math.min(Math.max(ed.cy - ch / 2, 0), 1 - ch);
  ed.cx = x + cw / 2; ed.cy = y + ch / 2;
  return [x, y, cw, ch];
}
function drawCrop() {
  const c = curCrop(), w = $("#cw");
  w.style.left = c[0] * 100 + "%"; w.style.top = c[1] * 100 + "%"; w.style.width = c[2] * 100 + "%"; w.style.height = c[3] * 100 + "%";
}
function closeFull() { clearInterval(supT); $("#full").hidden = true; $("#full").innerHTML = ""; ed = null; ev = null; renderFab(); }

async function loupe() {
  const lp = $("#lp");
  const sr = await fetch("/api/sr").then(r => r.json()).catch(() => ({ available: false }));
  await api("/api/crop", { si: ed.si, sj: ed.sj, crop: curCrop() }, { quiet: true });
  const q = `si=${ed.si}&sj=${ed.sj}&t=${Date.now()}`;
  lp.innerHTML = `<div class="loupe"><figure><img src="/api/loupe?${q}&mode=plain" alt=""><figcaption>Обычное увеличение</figcaption></figure>
    <figure>${sr.available ? `<img src="/api/loupe?${q}&mode=sr" alt=""><figcaption>С улучшением ИИ</figcaption>`
      : `<div class="note" style="margin:0">Улучшение сейчас недоступно</div>`}</figure></div>`;
}

// ---------------------------------------------------------------- ФОНОВЫЕ ЗАДАЧИ ИИ
async function pollJob(id, onUpdate) {
  let fails = 0;
  for (;;) {
    await sleep(550);
    let j = null;
    try { j = await fetch("/api/job/" + id).then(r => r.ok ? r.json() : null); } catch (e) { j = null; }
    if (!j) { if (++fails > 8) return { done: true, error: "Потеряна связь с сервером", steps: [] }; continue; }
    fails = 0; onUpdate && onUpdate(j);
    if (j.done) return j;
  }
}

function traceHtml(steps, live) {
  const ic = { step: "•", thought: "💭", ok: "✓", warn: "⚠" };
  return `<div class="trace">${steps.map((s, i) => `<div class="tr ${s.kind || "step"}"><i>${ic[s.kind || "step"]}</i><span>${esc(s.text)}</span>${live && i === steps.length - 1 && (s.kind === "step" || s.kind === "thought") ? `<u class="dots"></u>` : ""}</div>`).join("")}</div>`;
}

// ---------------------------------------------------------------- ПРАВКА ФОТО (результат «Было / Стало»)
const EDCHIPS = [
  ["👀 Открой глаза", "Открой закрытые глаза у людей на фото, чтобы выглядело естественно"],
  ["🧹 Убери лишнее", "Убери отмеченный объект с фото и аккуратно дорисуй фон", true],
  ["🎃 Хэллоуин", "Оформи это фото в стиле Хэллоуин"],
  ["🎄 Новый год", "Сделай новогоднее настроение: снег и гирлянды"],
  ["🎂 День рождения", "Добавь праздничное оформление дня рождения: шарики и конфетти"],
  ["🎩 Добавь шляпу", "Добавь человеку на фото стильную шляпу"],
  ["🖤 Чёрно-белое", "Сделай чёрно-белым"],
  ["🎞 Винтаж", "Сделай в стиле старой плёнки, винтаж"],
  ["🎨 Мультяшный", "Сделай в мультяшном стиле"],
  ["☀️ Ярче", "Сделай ярче и исправь цвета"],
];

function openEditor(pid, prompt = "", autorun = false, res = null) {
  const p = S.photos.find(x => x.id === pid); if (!p) return;
  closeSheet(); closeChat();
  ev = { pid, prompt, marks: [], marking: false, token: res ? res.token : null, res: res ? { ...res, t: Date.now() } : null, view: "after", err: "", running: false, steps: [] };
  drawEditor();
  if (autorun && prompt && !res) runEdit();
}

function drawEditor() {
  const f = $("#full"); f.hidden = false; renderFab();
  const p = S.photos.find(x => x.id === ev.pid); if (!p) return closeFull();
  const hasRes = !!ev.res, after = hasRes && ev.view === "after";
  const src = after ? `/api/edit/${ev.token}.jpg?t=${ev.res.t}` : `${pimg(ev.pid,1000)}`;
  f.innerHTML = `
    <div class="fh"><button data-em="close">Закрыть</button><b>✨ Правка фото</b><span style="width:70px"></span></div>
    <div class="fb">
      <div class="edstage ${ev.marking && !hasRes && !ev.running ? "marking" : ""} ${ev.running ? "busy" : ""}" id="stage2"><img src="${src}" alt="">
        ${hasRes ? `<span class="badge">${after ? "Стало" : "Было"}</span>` : ""}
        ${ev.running ? `<div class="scan"></div>` : ""}
        ${!hasRes ? ev.marks.map(m => `<i class="mk" style="left:${m.x * 100}%;top:${m.y * 100}%;width:${m.r * 200}%;aspect-ratio:1"></i>`).join("") : ""}</div>
      ${ev.running ? `<div class="card thinkcard"><div class="pf">${robotSVG({ mood: "think" })}<div class="bub"><b>Робот работает над фото…</b><div id="edtrace">${traceHtml(ev.steps, true)}</div></div></div>
          <div class="small mut" style="margin-top:8px">Можно закрыть окно и пользоваться приложением: результат появится в уведомлении.</div></div>`
      : hasRes ? `<div class="seg"><button class="${!after ? "on" : ""}" data-em="before">Было</button><button class="${after ? "on" : ""}" data-em="after">Стало</button></div>
        <div class="note good"><b>${ev.res.source === "model" ? "Готово" : "Готово: " + esc(ev.res.label)}</b>
          ${ev.res.note ? `<br><span class="small">${esc(ev.res.note)}</span>` : ""}</div>
        <div class="pad row"><button class="btn g grow" data-em="apply-replace">✅ Заменить фото</button><button class="btn grow" data-em="apply-copy">➕ Как новое</button></div>
        <div class="pad" style="margin-top:8px"><button class="btn full" data-em="again">↩️ Попробовать ещё раз</button></div>`
      : `<div class="chips">${EDCHIPS.map((c, i) => `<button class="chip" data-chip="${i}">${c[0]}</button>`).join("")}</div>
        <div class="edtext"><textarea id="edp" placeholder="Что сделать с фото? Например: «открой глаза», «убери человека справа», «сделай в стиле Хэллоуин»">${esc(ev.prompt)}</textarea></div>
        <div class="pad row"><button class="btn grow ${ev.marking ? "p" : ""}" data-em="mark">👆 ${ev.marking ? "Отмечаю…" : "Отметить на фото"}</button>
          ${ev.marks.length ? `<button class="btn" data-em="clearmarks">Сбросить (${ev.marks.length})</button>` : ""}</div>
        ${ev.marking ? `<div class="note">Коснитесь объекта на фото. Можно отметить несколько мест: точки нужны для «убери…» и «измени вот это».</div>` : ""}
        ${ev.err ? `<div class="note bad">${esc(ev.err)}</div>` : ""}
        <div class="pad" style="margin-top:10px"><button class="btn p big full" data-em="run">✨ Применить</button></div>
        <div class="pad small mut" style="margin-top:10px">
          ${p.edited ? `<br><button class="chip" style="margin-top:8px" data-em="revert">↩️ Вернуть оригинал фото</button>` : ""}</div>`}
    </div>`;
  const st = $("#stage2");
  if (st && ev.marking && !hasRes && !ev.running) st.addEventListener("click", e => {
    const r = st.getBoundingClientRect();
    ev.marks.push({ x: (e.clientX - r.left) / r.width, y: (e.clientY - r.top) / r.height, r: 0.07 });
    const i = document.createElement("i"); i.className = "mk"; const m = ev.marks[ev.marks.length - 1];
    i.style.cssText = `left:${m.x * 100}%;top:${m.y * 100}%;width:${m.r * 200}%;aspect-ratio:1`; st.appendChild(i);
  });
  const ta = $("#edp"); if (ta) ta.addEventListener("input", e => { ev.prompt = e.target.value; });
}

// Запуск правки в фоне. fromChat: ход мыслей показывается в чате, а не в редакторе.
async function runEdit(pid, prompt, marks, fromChat) {
  if (!pid) { const ta = $("#edp"); if (ta) ev.prompt = ta.value; pid = ev.pid; prompt = ev.prompt.trim(); marks = ev.marks; }
  if (!prompt) { ev.err = "Напишите, что сделать с фото, или выберите подсказку."; return drawEditor(); }
  const r = await fetch("/api/edit/start", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ photo: pid, prompt, marks: marks || [] }) })
    .then(async x => ({ ok: x.ok, j: await x.json().catch(() => ({})) })).catch(() => ({ ok: false, j: { error: "Нет связи с сервером" } }));
  if (!r.ok) {
    if (ev && !fromChat) { ev.err = r.j.error || "Не получилось"; return drawEditor(); }
    return toast(r.j.error || "Не получилось");
  }
  const mine = ev && ev.pid === pid && !fromChat;
  if (mine) { ev.running = true; ev.steps = []; ev.err = ""; drawEditor(); }
  const j = await pollJob(r.j.job, jb => {
    if (fromChat && chatJob) { chatJob.steps = chatJob.base.concat(jb.steps); updateChatTrace(); }
    else if (ev && ev.pid === pid && ev.running) { ev.steps = jb.steps; const t = $("#edtrace"); if (t) t.innerHTML = traceHtml(jb.steps, true); }
  });
  if (fromChat) endChatJob();
  if (j.error || !j.result) {
    const msg = j.error || "Не получилось";
    if (/Отметьте|коснитесь/i.test(msg)) { openEditor(pid, prompt); ev.marking = true; ev.err = msg; return drawEditor(); }
    if (ev && ev.pid === pid && ev.running) { ev.running = false; ev.err = msg; return drawEditor(); }
    return toast(msg);
  }
  const res = { ...j.result, t: Date.now() };
  if (ev && ev.pid === pid && !fromChat) { ev.running = false; ev.token = res.token; ev.res = res; ev.view = "after"; ev.marking = false; drawEditor(); }
  else if (fromChat || !ev) {
    readyEdit = res;
    if (fromChat) { openEditor(pid, prompt, false, res); }
    else toast("✨ Правка фото готова. Нажмите, чтобы посмотреть", () => { openEditor(pid, prompt, false, res); });
  }
}

// ---------------------------------------------------------------- ЧАТ (плавающая панель, работает в фоне)
const QUICK = [
  ["🎃 Хэллоуин", "оформи альбом в стиле Хэллоуин"], ["🎂 День рождения", "оформи как день рождения"],
  ["🔀 Перемешай", "перемешай фото"], ["📅 По дате", "расставь фото по дате съёмки"],
  ["🧩 Другая раскладка", "измени раскладку на всех разворотах"], ["➕ Разворот", "добавь новый разворот"],
];
const PHOTO_QUICK = EDCHIPS.filter(c => !c[2]).slice(0, 8);

function openChat() {
  chatOpen = true; LS.set("pp_hint", "1");
  $("#chat").hidden = false; $("#fab-hint").hidden = true; $("#ch-ava").innerHTML = robotSVG({ mood: "idle", face: true });
  renderChat(); setTimeout(() => { const i = $("#cin"); i && i.focus({ preventScroll: true }); }, 60);
}
function closeChat() { chatOpen = false; $("#chat").hidden = true; }

function askAI(pid) {
  closeSheet(); ctxPhoto = pid; ctxExplicit = true; LS.set("pp_ctx", pid);
  openChat();
  const i = $("#cin"); if (i) i.placeholder = "Что сделать с этим фото? Например: открой глаза";
}

function renderCtx() {
  const bar = $("#ctxbar"), p = ctxExplicit && ctxPhoto && S.photos.find(x => x.id === ctxPhoto);
  bar.hidden = !p;
  if (p) bar.innerHTML = `<img loading="lazy" decoding="async" src="${pimg(p.id,80)}" alt=""><span>Фото: <b>${esc(p.name)}</b></span><button data-act="ctxclear" aria-label="Убрать">✕</button>`;
  const i = $("#cin"); if (i && !p) i.placeholder = "Что изменить? Например: оформи в стиле Хэллоуин";
}

const STEP_ICON = { step: "•", thought: "💭", ok: "✓", warn: "⚠" };
const stepHtml = st => `<div class="tr ${st.kind || "step"}"><i>${STEP_ICON[st.kind || "step"]}</i><span>${esc(st.text)}</span></div>`;

// Как в Claude: сначала сворачиваемый блок «Ход мыслей», потом отдельным сообщением итоговый ответ.
function msgHtml(m) {
  if (m.role === "user") return `<div class="msg u">${m.photo ? `<img class="att" src="${pimg(m.photo,90)}" alt="">` : ""}${esc(m.content)}</div>`;
  const steps = (m.steps || []).filter(s => s.text);
  return (steps.length ? `<details class="think"><summary><span class="cv"></span>Ход мыслей · ${steps.length} ${plural(steps.length, "шаг", "шага", "шагов")}</summary><div class="tbody">${steps.map(stepHtml).join("")}</div></details>` : "") +
    `<div class="msg a">${esc(m.content)}
    ${m.report && m.report.length ? `<div class="rep">${m.report.map(esc).join("<br>")}</div>` : ""}
    ${m.errors && m.errors.length ? `<div class="rep" style="color:#a33">${m.errors.map(esc).join("<br>")}</div>` : ""}
    ${m.edit ? `<div class="rep"><button class="chip" data-edit="${m.edit.photo}" data-ep="${esc(m.edit.prompt)}">✨ Открыть редактор фото</button></div>` : ""}
  </div>`;
}

function renderChat() {
  const list = chatJob && chatJob.inChat ? (S.chat || []).slice(0, -2) : (S.chat || []);
  let ms = list.map(msgHtml).join("");
  if (!ms && !chatJob) ms = `<div class="msg a">Привет! Я робот-помощник. Скажите, что изменить: «оформи в стиле Хэллоуин», «перемешай фото», «открой глаза на фото 2». Я покажу, как думаю, и не буду мешать вам работать.</div>`;
  if (chatJob) {
    ms += `<div class="msg u">${chatJob.photo ? `<img class="att" src="${pimg(chatJob.photo,90)}" alt="">` : ""}${esc(chatJob.text)}</div>
      <details class="think live" open><summary><span class="spin sm"></span>Думаю…</summary><div class="tbody" id="livetrace">${chatJob.steps.map(stepHtml).join("")}</div></details>`;
    chatJob.shown = chatJob.steps.length;
  }
  $("#msgs").innerHTML = ms;
  const quick = ctxExplicit && ctxPhoto ? PHOTO_QUICK : QUICK;
  $("#chips").innerHTML = quick.map((q, i) => `<button class="chip" data-q="${i}">${q[0]}</button>`).join("");
  renderCtx();
  const m = $("#msgs"); m.scrollTop = m.scrollHeight;
  $("#csend").disabled = !!chatJob;
}
// Добавляем только новые шаги: уже показанные не перерисовываются, поэтому ничего не мигает.
function updateChatTrace() {
  const t = $("#livetrace"); if (!t || !chatJob) return;
  const from = chatJob.shown || 0;
  if (chatJob.steps.length <= from) return;
  t.insertAdjacentHTML("beforeend", chatJob.steps.slice(from).map(stepHtml).join(""));
  chatJob.shown = chatJob.steps.length;
  const m = $("#msgs"); m.scrollTop = m.scrollHeight;
}
function endChatJob() { chatJob = null; if (chatOpen) renderChat(); }

$("#cin").addEventListener("input", e => { chatDraft = e.target.value; });
$("#cbar").addEventListener("submit", e => { e.preventDefault(); sendChat($("#cin").value); });

async function sendChat(text) {
  text = (text || "").trim();
  if (!text || chatJob || !curProj()) { if (!curProj()) toast("Сначала откройте проект на главной"); return; }
  chatDraft = ""; $("#cin").value = "";
  const pid = S.cur, photo = ctxPhoto, explicit = ctxExplicit;
  // как в Claude: вложение уходит с одним сообщением и после отправки открепляется
  ctxPhoto = null; ctxExplicit = false; LS.set("pp_ctx", "");
  chatJob = { text, photo: explicit ? photo : null, steps: [{ text: "Отправляю запрос…", kind: "step" }], base: [], shown: 0 };
  renderChat();
  const r = await fetch("/api/chat/start", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ text, photo, explicit, lang: (window.I18N && I18N.lang) || "ru" }) })
    .then(async x => ({ ok: x.ok, j: await x.json().catch(() => ({})) })).catch(() => ({ ok: false, j: { error: "Нет связи с сервером" } }));
  if (!r.ok) { endChatJob(); return toast(r.j.error || "Не получилось отправить"); }
  const j = await pollJob(r.j.job, jb => { if (chatJob) { if (jb.steps.length) chatJob.steps = jb.steps; chatJob.base = jb.steps; updateChatTrace(); } });
  if (S.cur !== pid) { endChatJob(); return; }
  const before = S.rev;
  await refresh();
  const msg = j.result && j.result.message;
  if (j.error || !msg) { endChatJob(); return toast(j.error || "Не получилось"); }
  if (msg.edit) {
    // правка фото идёт дальше в фоне, ход мыслей остаётся в этом же чате
    chatJob.steps = j.steps.slice(); chatJob.base = j.steps.slice(); chatJob.inChat = true; updateChatTrace();
    const keepJob = chatJob;
    await runEdit(msg.edit.photo, msg.edit.prompt, [], true);
    return;
  }
  endChatJob();
  if (S.rev !== before) render();
  if (!chatOpen) toast("🤖 " + (msg.content || "Готово").slice(0, 120));
}

// ---------------------------------------------------------------- КОРЗИНА
function paramText(p) {
  if (p.issue && p.issue.field === "product") return `${p.photos} фото`;
  if (p.kind === "book") { const c = S.catalog.covers.find(x => x.id === p.params.cover); return `${p.spreads} разв. · ${p.photos} фото · ${c ? c.name.toLowerCase() : ""}`; }
  const pp = prod(p.product) || { formats: [], papers: [] }, f = pp.formats.find(x => x.id === p.params.format), pa = pp.papers.find(x => x.id === p.params.paper);
  return `${f ? f.name : ""} · ${pa ? pa.name.toLowerCase() : ""} бумага · ${p.photos} фото`;
}
const cartItems = () => S.projects.filter(p => p.in_cart && !p.ordered);

async function openCart(isFlow) {
  closeSheet();
  if (isFlow) {
    const pr = curProj();
    if (pr && !pr.in_cart || (pr && pr.in_cart && pr.kind === "print")) {
      if (!S.photos.length) { toast("Сначала добавьте фото"); return go("app", { tab: "photos" }); }
      const j = await api("/api/cart/add", { id: pr.id }, { busy: "Кладу в корзину…" });
      if (!j) return;
    }
  }
  go("cart", { flow: !!isFlow });
}

function vCart() {
  const items = cartItems();
  if (!items.length) {
    view.innerHTML = `<div class="empty">${catSVG({ mood: "sleep" }).replace('class="mascot', 'style="width:120px;height:120px;margin:0 auto" class="mascot')}
      <h3>Корзина пуста</h3>Добавьте альбом или фотопечать: можно заказать сразу несколько позиций и в нескольких экземплярах.
      <div class="cta"><button class="btn p full" data-tab="home">Выбрать услугу</button></div></div>`;
    return;
  }
  const total = items.reduce((s, p) => s + p.sum, 0), cnt = items.reduce((s, p) => s + p.line_qty, 0);
  view.innerHTML = `<div class="sec">Ваш заказ · ${items.length} ${plural(items.length, "позиция", "позиции", "позиций")}</div>
    <div class="citems">${items.map(p => `
      <div class="citem"><div class="cth" style="background:${p.color}"><img src="/api/project/${p.id}/thumb.jpg?w=200&v=${Math.round(p.updated)}" alt="" onerror="this.remove()"></div>
        <div class="cb"><b>${esc(p.name)}</b><small>${esc(p.product_name)}</small><small>${esc(paramText(p))}</small>${p.issue ? `<button class="issue-b" data-issue="${p.id}">⚠ Нужна замена: ${esc(p.issue.what)}</button>` : ""}
          <div class="cr">${p.kind === "book"
            ? `<div class="step"><button data-cq="${p.id}:${p.qty - 1}" aria-label="Меньше">${p.qty <= 1 ? "🗑" : "−"}</button><i>${p.qty}</i><button data-cq="${p.id}:${p.qty + 1}" aria-label="Больше">+</button></div>`
            : `<span class="mut small">${p.line_qty} отп. × ${money(p.unit)}</span>`}
            <b class="sm">${money(p.sum)}</b></div>
          <div class="ca"><button data-cedit="${p.id}">✏️ Редактировать</button><button data-cdel="${p.id}">Убрать</button></div></div></div>`).join("")}</div>
    <button class="btn full addsp" data-tab="home">＋ Добавить ещё услугу</button>
    <div class="card"><div class="sum"><span>Товары (${cnt} шт.)</span><span>${money(total)}</span></div>
      <div class="sum"><span>Доставка</span><span class="mut">на следующем шаге</span></div>
      <div class="sum tot"><span>Итого</span><span>${money(total)}</span></div></div>
    <div class="cta"><button class="btn g big full" data-act="tocheckout">Оформить заказ →</button></div>`;
}

// ---------------------------------------------------------------- ОФОРМЛЕНИЕ ЗАКАЗА
async function openCheckout() {
  trackEv("checkout");
  if (!onlyOn(S.catalog.delivery).some(x => x.id === ck.delivery)) {      // выбранный способ мог быть отключён или удалён
    const was = ck.delivery; ck.delivery = (onlyOn(S.catalog.delivery)[0] || {}).id;
    if (was) toast("Выбранный способ получения больше недоступен. Мы выбрали «" + ((onlyOn(S.catalog.delivery)[0] || {}).name || "другой") + "»: при желании измените его ниже.");
  }
  if (!onlyOn(S.catalog.payment).some(x => x.id === ck.payment)) {
    const was = ck.payment; ck.payment = (onlyOn(S.catalog.payment)[0] || {}).id;
    if (was) toast("Выбранный способ оплаты больше недоступен. Мы выбрали «" + ((onlyOn(S.catalog.payment)[0] || {}).name || "другой") + "».");
  }
  const items = cartItems();
  if (!items.length) { toast("Корзина пуста"); return; }
  ck.err = ""; ck.risk = false;
  pf = await api("/api/preflight", undefined, { noState: true, silent: true });
  await refreshQuote();
  go("checkout");
}

async function refreshQuote() {
  const j = await api("/api/quote", { delivery: ck.delivery, promo: ck.promo, pers: persOn(), use_points: !!ck.usePts, disc: ck.disc || "auto", nofree: !!ck.nofree }, { noState: true, silent: true, wait: "Пересчитываю цену…" });
  if (j) {
    quote = j.totals; quoteLoy = j.loy || null; if (quoteLoy && !quoteLoy.used && !quoteLoy.max) ck.usePts = false;
    promoMsg = j.error ? { bad: true, t: j.error } : (ck.promo && j.totals.promo ? { t: "Промокод применён: " + j.promo_text } : "");
  }
}

function calcTotal() {
  const items = cartItems(), sum = items.reduce((s, p) => s + p.sum, 0), dl = S.catalog.delivery.find(x => x.id === ck.delivery) || onlyOn(S.catalog.delivery)[0] || S.catalog.delivery[0];
  const q = quote || { items: sum, discount: 0, delivery: dl.price, total: sum + dl.price };
  return { items, sum, dl: q.delivery, disc: q.discount || 0, pts: q.points || 0, lbl: q.discount_label || "", total: q.total, dlo: dl };
}
const riskyItems = () => (pf ? pf.items.filter(i => i.issues.length) : []);

function vCheckout() {
  const c = S.catalog, pr = calcTotal(), bad = riskyItems(), ok = !bad.length;
  const pfHtml = `<div class="card"><div class="pf">${robotSVG({ mood: ok ? "happy" : "think" })}
    <div class="bub ${ok ? "ok" : "warn"}">${ok
      ? `<b>Всё в порядке!</b><br>Я проверил резкость, разрешение и заполнение: можно печатать.`
      : `<b>Я проверил заказ и нашёл, что поправить:</b><ul>${bad.map(i => `<li><b>${esc(i.name)}</b>: ${i.issues.map(x => esc(x.text)).join("; ")}</li>`).join("")}</ul>
         <div class="row">${bad.map(i => `<button class="chip" data-cedit="${i.id}">Исправить «${esc(i.name.slice(0, 14))}»</button>`).join("")}</div>
         <label class="chk"><input type="checkbox" data-risk ${ck.risk ? "checked" : ""}> Понимаю, что качество ниже среднего, и заказываю как есть</label></div>`}</div></div></div>`;
  view.innerHTML = `
    ${pfHtml}
    ${persHtml()}
    ${loyCheckout()}
    <div class="sec">Как получить</div><div class="pad" role="radiogroup" aria-label="Способ получения">${onlyOn(c.delivery).map(x => `<button class="opt ${ck.delivery === x.id ? "on" : ""}" role="radio" aria-checked="${ck.delivery === x.id}" data-opt="delivery:${x.id}"><span class="rd"></span><span class="g">${esc(x.name)}<br><small>${esc(x.eta)}</small></span><span class="pr">${x.price ? money(x.price) : "бесплатно"}</span></button>`).join("")}</div>
    <div class="sec">Контакты</div>
    <label class="field"><span>Имя</span><input data-ck="name" value="${esc(ck.name)}" autocomplete="name" placeholder="Как к вам обращаться"></label>
    <label class="field"><span>Телефон</span><input data-ck="phone" type="tel" value="${esc(ck.phone)}" autocomplete="tel" placeholder="+375 29 123-45-67"></label>
    ${pr.dlo.address ? `<label class="field"><span>Адрес или отделение</span><input data-ck="address" value="${esc(ck.address)}" autocomplete="street-address" placeholder="Город, улица, дом, квартира / № отделения"></label>` : ""}
    <label class="field"><span>Комментарий (необязательно)</span><input data-ck="comment" value="${esc(ck.comment)}" placeholder="Например: позвоните перед доставкой"></label>
    <div class="sec">Промокод</div>
    <div class="track-in"><input data-ck="promo" value="${esc(ck.promo)}" placeholder="PICPAC10" aria-label="Промокод" autocapitalize="characters"><button class="btn" data-act="applypromo">Применить</button></div>
    ${promoMsg ? `<div class="${promoMsg.bad ? "err" : "okmsg"}">${esc(promoMsg.t)}</div>` : `<div class="pad small mut" style="margin-top:6px">Тестовые коды: PICPAC10 (−10%), FREESHIP (бесплатная доставка).</div>`}
    <div class="sec">Оплата</div><div class="pad" role="radiogroup" aria-label="Оплата">${onlyOn(c.payment).map(x => `<button class="opt ${ck.payment === x.id ? "on" : ""}" role="radio" aria-checked="${ck.payment === x.id}" data-opt="payment:${x.id}"><span class="rd"></span><span class="g">${esc(x.name)}</span></button>`).join("")}</div>
    <div class="pad small mut" style="margin-top:6px">Это демо: деньги не списываются, карточные данные не запрашиваются.</div>
    <div class="card" style="margin-top:14px">
      ${pr.items.map(p => `<div class="sum"><span>${p.kind === "book" ? `${p.qty} × ${esc(p.name)}` : `${esc(p.product_name)} · ${p.line_qty} отп.`}</span><span>${money(p.sum)}</span></div>`).join("")}
      ${pr.disc ? `<div class="sum disc"><span>${esc(pr.lbl || "Скидка")}</span><span>−${money(pr.disc)}</span></div>` : ""}
      ${pr.pts ? `<div class="sum disc"><span>Оплачено фотобаллами</span><span>−${money(pr.pts)}</span></div>` : ""}
      <div class="sum"><span>Получение</span><span>${pr.dl ? money(pr.dl) : "бесплатно"}</span></div>
      <div class="sum tot"><span>Итого</span><span>${money(pr.total)}</span></div>
      <div class="small mut" style="margin-top:6px">Итого = товары − скидка − фотобаллы + доставка. Пересчитывается при смене доставки, количества и промокода.</div></div>
    ${ck.err ? `<div class="err" role="alert">${esc(ck.err)}</div>` : ""}
    <div class="buy"><button class="btn g big full" data-act="submit">Оформить заказ · ${money(pr.total)}</button></div>`;
}

async function submitOrder() {
  ck.err = "";
  const dl = calcTotal().dlo;
  if (ck.name.trim().length < 2) ck.err = "Укажите имя";
  else if (ck.phone.replace(/\D/g, "").length < 9) ck.err = "Укажите телефон (не меньше 9 цифр)";
  else if (dl.address && ck.address.trim().length < 6) ck.err = "Укажите адрес или номер отделения";
  else if (riskyItems().length && !ck.risk) ck.err = "Исправьте отмеченные фото или подтвердите, что заказываете как есть";
  if (ck.err) { render(); view.scrollTop = view.scrollHeight; return; }
  LS.set("pp_name", ck.name); LS.set("pp_phone", ck.phone); saveCk();
  if (submitOrder.busy) return;
  submitOrder.busy = true;
  const j = await api("/api/order", { ...ck, key: checkoutKey, client: CLIENT, pers: persOn(), use_points: !!ck.usePts, disc: ck.disc || "auto", nofree: !!ck.nofree }, { busy: "Готовлю макеты и оформляю заказ…", silent: true });
  submitOrder.busy = false;
  if (!j) {
    const msg = lastErr; await refresh();
    const gone = (S.projects || []).find(p => p.issue && p.in_cart && !p.ordered);
    if (gone) { ck.err = msg; ck.pending = false; render(); view.scrollTop = view.scrollHeight; issueSheet(gone.id); return; }
    lastErr = msg;
    ck.pending = lastErr === "Нет связи с сервером";
    ck.err = ck.pending ? "Нет связи с сервером. Данные сохранены: заказ отправится, когда сеть вернётся. Второй заказ не создастся." : lastErr;
    render(); view.scrollTop = view.scrollHeight; return;
  }
  ck.pending = false;
  checkoutKey = "k" + Math.random().toString(36).slice(2, 14); LS.set("pp_key", checkoutKey); offersAt = 0;
  ck.promo = ""; ck.usePts = false; ck.disc = "auto"; ck.nofree = false; quote = null; quoteLoy = null; promoMsg = ""; saveCk(); loy = null;
  if (j.duplicate) toast("Этот заказ уже оформлен, второй не создаём");
  await refresh(); await loadOrders();
  seenStage[j.order.code] = j.order.stage;
  order = j.order; trackErr = ""; trackFrom = "orders";
  history.pushState({}, "", "/track/" + order.code);
  go("track"); confetti();
}

// ---------------------------------------------------------------- ЗАКАЗЫ и ОТСЛЕЖИВАНИЕ
async function loadOrders() {
  const j = await api("/api/orders?client=" + encodeURIComponent(CLIENT), undefined, { noState: true, silent: true });
  if (j) orders = j.orders;
  return orders;
}
async function openOrders() { await loadOrders(); go("orders"); }
function vOrders() {
  const act = orders.filter(o => !o.finished), fin = orders.filter(o => o.finished);
  if (!orders.length) {
    view.innerHTML = `<div class="empty">${robotSVG({ mood: "hold" }).replace('class="mascot', 'style="width:120px;height:120px;margin:0 auto" class="mascot')}
      <h3>Заказов пока нет</h3>Здесь появятся ваши заказы с трекингом: нажмите на заказ, чтобы увидеть, на каком он этапе.
      <div class="cta"><button class="btn p full" data-tab="home">Создать первый заказ</button>
      <button class="btn full" data-act="findcode">Найти заказ по коду</button></div></div>`;
    return;
  }
  view.innerHTML = (act.length ? `<div class="sec">Активные · ${act.length}</div><div class="olist">${act.map(orderBrief).join("")}</div>` : "")
    + (fin.length ? `<div class="sec">Завершённые</div><div class="olist">${fin.map(orderBrief).join("")}</div>` : "")
    + `<div class="pad" style="margin-top:12px"><button class="btn full" data-act="findcode">🔎 Найти заказ по коду</button></div>`;
}

async function openTrack(code, from, push) {
  const j = await api("/api/track/" + encodeURIComponent(code), undefined, { noState: true, silent: true });
  if (!j) { trackErr = lastErr; order = null; trackFrom = from || "home"; go("track"); return false; }
  order = j.order; trackErr = ""; trackFrom = from || "orders";
  if (push) history.pushState({}, "", "/track/" + order.code);
  go("track"); startPoll(); return true;
}
// Фоновый опрос заказов (каждые 5 с): обновляет экраны и имитирует push при смене статуса.
function startPoll() { if (!pollT) pollT = setInterval(bgPoll, 5000); }
function stopPoll() {}
async function bgPoll() {
  if (!S || screen === "login" || !navigator.onLine) return;
  const prev = seenStage;
  await loadOrders();
  const cur = {}; orders.forEach(o => { cur[o.code] = o.stage; });
  if (!firstPoll) orders.forEach(o => {
    if (prev[o.code] !== undefined && prev[o.code] !== o.stage) pushNote(o);
  });
  firstPoll = false; seenStage = cur;
  if (screen === "track" && order) { const o = orders.find(x => x.code === order.code); if (o) { const ch = o.stage !== order.stage; order = o; if (ch) render(); } }
  else if (screen === "orders" || screen === "home") { if (JSON.stringify(prev) !== JSON.stringify(cur)) render(); }
  if (me && me.role === "admin" && screen === "admin" && !adminThread) { const before = JSON.stringify(adminData && [adminData.orders.length, adminData.stats, adminData.threads]); await loadAdmin(); if (JSON.stringify([adminData.orders.length, adminData.stats, adminData.threads]) !== before && !document.activeElement.matches("input,select")) render(); }
  renderNav();
}
function pushNote(o) {
  const t = `Заказ ${o.code}: ${o.status_title}`;
  const el = $("#push"); el.innerHTML = `<b>${icon("bell")} PicPac</b><span>${esc(t)}</span>`; el.hidden = false;
  el.onclick = () => { el.hidden = true; openTrack(o.code, "orders", true); };
  clearTimeout(pushNote.t); pushNote.t = setTimeout(() => { el.hidden = true; }, 7000);
  try { if (window.Notification && Notification.permission === "granted") new Notification("PicPac", { body: t, icon: "/icons/icon-192.png" }); } catch (e) {}
}

function fmtTime(ts) {
  if (!ts) return "";
  const d = new Date(ts * 1000), now = new Date();
  const t = d.toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" });
  return d.toDateString() === now.toDateString() ? t : d.toLocaleDateString("ru-RU", { day: "numeric", month: "short" }) + ", " + t;
}

function vTrack() {
  if (!order) {
    view.innerHTML = `<div class="empty">${catSVG({ mood: "sleep" }).replace('class="mascot', 'style="width:120px;height:120px;margin:0 auto" class="mascot')}<h3>Заказ не найден</h3>${esc(trackErr || "Проверьте код")}<div class="cta"><button class="btn p full" data-act="back-orders">К заказам</button></div></div>`;
    return;
  }
  const o = order, cur = o.steps[o.stage], pct = Math.round(o.stage / (o.steps.length - 1) * 100);
  const pet = o.finished ? catSVG({ mood: "happy" }) : o.stage === 2 ? robotSVG({ mood: "think" }) : o.stage < 2 ? catSVG({ mood: "sleep" }) : robotSVG({ mood: "hold" });
  view.innerHTML = `
    <div class="thero ${o.finished ? "done" : ""}">
      <div class="code">ТРЕК-КОД · ${esc(o.code)}</div><div class="code2">Оформлен ${esc(fmtTime(o.created))}</div>
      <h2>${cur.icon} ${esc(cur.title)}</h2><p>${esc(cur.text)}</p>
      <div class="pbar"><i style="width:${pct}%"></i></div>
      <div class="pet">${pet}</div>
    </div>
    <div class="sec">Где ваш заказ</div>
    <div class="tl">${o.steps.map(s => `
      <div class="st ${s.state}"><div class="ic">${s.icon}</div><div class="tx"><b>${esc(s.title)}</b><small>${esc(s.text)}</small></div><div class="at">${s.state === "todo" ? "" : fmtTime(s.at)}</div></div>`).join("")}</div>
    <div class="sec">Состав заказа</div>
    <div class="card" style="margin-top:4px">
      ${o.items.map(i => `<div class="oit"><div class="cth" style="background:#eee"><img src="/api/project/${i.thumb}/thumb.jpg?w=120" alt="" onerror="this.remove()"></div>
        <div class="ob"><b>${esc(i.title)}</b><small>${esc(i.product_name)} · ${i.kind === "book" ? `${i.spreads} разв.` : `${i.photos} фото`} · ${i.qty} шт.</small></div><span>${money(i.sum)}</span></div>`).join("")}
      ${o.price.discount ? `<div class="sum disc"><span>Скидка ${esc(o.price.promo)}</span><span>−${money(o.price.discount)}</span></div>` : ""}
      <div class="sum" style="margin-top:6px"><span>Получение</span><span>${esc(o.delivery_name)}</span></div>
      ${o.address ? `<div class="sum"><span>Адрес</span><span style="text-align:right;max-width:60%">${esc(o.address)}</span></div>` : ""}
      <div class="sum"><span>Срок после оплаты</span><span>${esc(o.eta)}</span></div>
      ${o.price.points ? `<div class="sum disc"><span>Оплачено фотобаллами</span><span>−${money(o.price.points)}</span></div>` : ""}
      <div class="sum tot"><span>Итого</span><span>${money(o.price.total)}</span></div>
      ${o.loyalty && o.loyalty.earned ? `<div class="small mut" style="margin-top:6px">${o.finished ? "Начислено" : "Начислим после выдачи"}: <b style="color:var(--ink)">+${o.loyalty.earned} ${ptsWord(o.loyalty.earned)}</b></div>` : ""}</div>
    <div class="pad row" style="margin-top:6px">
      <button class="btn grow" data-act="copycode">📋 Код</button><button class="btn grow" data-act="copylink">🔗 Ссылка</button></div>
    <div class="sec">Поддержка по заказу</div>
    <div class="card" id="trk-sup" style="margin-top:4px">${supportBlock(o.code)}</div>
    <div class="pad" style="margin-top:10px">
      ${(o.pdfs || []).map(p => `<a class="btn full" style="text-decoration:none;margin-bottom:8px" href="/api/track/${o.code}/pdf/${p.id}">📄 Макет: ${esc(p.title)} (PDF)</a>`).join("")}
      ${o.finished ? "" : `<button class="btn full" data-act="advance">⏩ Демо: следующий статус</button>
        <div class="small mut" style="margin:6px 4px 10px">В демо статусы идут сами раз в ${Math.round(o.step_seconds)} с. Кнопка перематывает сразу.</div>`}
      <button class="btn p full" data-tab="home">Создать новый заказ</button></div>
    <div style="height:30px"></div>`;
  fillSupportBlock(o.code);
}

// ---------------------------------------------------------------- НАСТРОЙКИ ИИ
function settingsSheet() {
  const st = S.settings, adm = me && me.role === "admin", notif = window.Notification ? Notification.permission : "denied";
  const act = orders.filter(o => !o.finished).length;
  openSheet(`<h3>${icon("user")} Профиль</h3>
    <div class="acc"><span class="av">${icon("user")}</span><div><b>${esc(me ? me.name : "Гость")}</b><small>${me && me.guest ? "Гостевой режим" : (me && me.email ? esc(me.email) : "Аккаунт")}${adm ? " · администратор" : ""}</small></div>
      ${me && me.guest ? `<button class="chip" data-act="relogin">Войти</button>` : `<button class="chip" data-act="logout">Выйти</button>`}</div>
    ${adm ? "" : `<button class="act" data-act="orders-all"><span class="em">📦</span><span style="flex:1">История заказов<small class="mut" style="display:block">${orders.length ? `Всего заказов: ${orders.length}${act ? `, активных: ${act}` : ""}` : "Здесь появятся ваши заказы"}</small></span><span class="qc">›</span></button>
    <button class="act ai" data-act="support"><span class="em">💬</span><span style="flex:1">Чат поддержки<small class="mut" style="display:block">Ответим здесь же, можно привязать заказ</small></span></button>`}
    <div class="sec">Язык</div>
    <div class="pad"><div class="seg" role="tablist" aria-label="Язык"><button class="${I18N.lang === "ru" ? "on" : ""}" data-act="lang" data-l="ru" data-nt>Русский</button><button class="${I18N.lang === "be" ? "on" : ""}" data-act="lang" data-l="be" data-nt>Беларуская</button></div></div>
    <div class="sec">Настройки</div>
    <button class="act" data-act="toggle-push"><span class="em">🔔</span><span style="flex:1">Уведомления о статусе заказа</span><span class="switch ${notif === "granted" ? "on" : ""}"></span></button>
    ${canPers() ? `<button class="act" data-act="toggle-pers"><span class="em">🎁</span><span style="flex:1">Персональные предложения<small class="mut" style="display:block">Скидки и подборки по вашим заказам в PicPac</small></span><span class="switch ${persOn() ? "on" : ""}"></span></button>` : ""}
    <button class="act" data-act="toggle-upscale"><span class="em">🔍</span><span style="flex:1">Улучшать качество фото в PDF<small class="mut" style="display:block">По умолчанию выключено</small></span><span class="switch ${S.settings.upscale ? "on" : ""}"></span></button>
    ${curProj() ? `<button class="act" data-act="clearchat"><span class="em">🧹</span>Очистить чат ИИ этого проекта</button>` : ""}
    ${adm ? "" : `<div class="sec">Данные</div>
    <button class="act dng" data-act="deldata"><span class="em">🗑</span>Удалить мои данные (фото, проекты, заказы, обращения)</button>`}
    <div class="pad small mut" style="margin:10px 0">Ваши проекты и фото хранятся, пока вы не удалите данные. ИИ ничего не делает сам: команды выполняются только по вашему запросу.</div>`);
}

function findCodeSheet() {
  openSheet(`<h3>Найти заказ по коду</h3>
    <div class="pad small mut" style="margin:-2px 0 8px">Если заказ оформлен на другом устройстве, введите его трек-код.</div>
    <div class="track-in"><input id="code" placeholder="PP-AB12-CD34" autocomplete="off" autocapitalize="characters"><button class="btn p" data-act="gocode">Найти</button></div>`);
}


// ---------------------------------------------------------------- ВХОД
let authMode = "login";
function vLogin() {
  const reg = authMode === "register";
  view.innerHTML = `<div class="login"><div class="lhero"><img src="/img/login.jpg" alt="" fetchpriority="high"><div class="lbrand"><img src="/img/logo-coral.svg" alt="PicPac" width="200" height="106"><p>Фотокниги и печать фото с телефона</p></div></div>
    <div class="lform">
    <div class="langsw"><button class="${I18N.lang === "ru" ? "on" : ""}" data-act="lang" data-l="ru" data-nt>RU</button><button class="${I18N.lang === "be" ? "on" : ""}" data-act="lang" data-l="be" data-nt>БЕЛ</button></div>
    <div class="seg" role="tablist"><button class="${!reg ? "on" : ""}" data-act="authmode" data-mode="login">Вход</button><button class="${reg ? "on" : ""}" data-act="authmode" data-mode="register">Регистрация</button></div>
    ${reg ? `<label class="field"><span>Имя</span><input id="r-name" autocomplete="name" maxlength="60" placeholder="Как к вам обращаться"></label>
      <label class="field"><span>Почта</span><input id="r-mail" type="email" autocomplete="email" placeholder="name@example.com"></label>
      <label class="field"><span>Пароль (не короче 8 символов)</span><input id="r-pw" type="password" autocomplete="new-password"></label>
      <label class="field"><span>Повторите пароль</span><input id="r-pw2" type="password" autocomplete="new-password"></label>
      <label class="chk"><input type="checkbox" id="r-ok"> Согласен на обработку моих данных для выполнения заказов</label>
      <div class="err" id="l-err" role="alert" hidden></div>
      <div class="pad"><button class="btn p big full" data-act="doregister">Создать аккаунт</button></div>`
    : `<label class="field"><span>Почта</span><input id="l-mail" type="email" autocomplete="username" placeholder="name@example.com"></label>
      <label class="field"><span>Пароль</span><input id="l-pw" type="password" autocomplete="current-password"></label>
      <div class="err" id="l-err" role="alert" hidden></div>
      <div class="pad"><button class="btn p big full" data-act="dologin">Войти</button></div>`}
    <div class="or"><span>или</span></div>
    <button class="btn full" data-act="guest">Продолжить как гость</button>
    ${reg ? "" : `<div class="demoacc">Тестовые записи: <button class="chip" data-fill="client@picpac.test:client123">${icon("user")} Клиент</button><button class="chip" data-fill="vip@picpac.test:vip123">${icon("user")} VIP</button><button class="chip" data-fill="sleep@picpac.test:sleep123">${icon("user")} Давно не заказывал</button><button class="chip" data-fill="admin@picpac.test:admin123">${icon("shield")} Администратор</button></div>`}
    <small class="mut">У каждого пользователя свои фото, проекты и заказы. Гость может зарегистрироваться позже: его заказы и черновики перенесутся в аккаунт.</small></div></div>`;
  const pw = $("#l-pw"); if (pw) pw.addEventListener("keydown", e => { if (e.key === "Enter") doLogin(); });
  const pw2 = $("#r-pw2"); if (pw2) pw2.addEventListener("keydown", e => { if (e.key === "Enter") doRegister(); });
}
async function startSession(tok) {
  LS.set("pp_token", tok); LS.set("pp_seen", "1");
  await refresh(); me = S && S.me; firstPoll = true; adminData = null; adminThread = null; adminTab = "sum"; await loadOrders(); await loadOffers(true);
  seenStage = {}; orders.forEach(o => { seenStage[o.code] = o.stage; }); loy = null;
}
async function doGuest() {
  const j = await api("/api/guest", { client: CLIENT }, { busy: "Открываю гостевой режим…", noState: true });
  if (!j) return;
  await startSession(j.token); afterAuth();
}
const afterAuth = () => (LS.get("pp_join") ? openJoin(LS.get("pp_join")) : LS.get("pp_dl") ? route().then(ok => { if (!ok) go("home"); }) : go("home"));
async function doLogin() {
  const email = $("#l-mail").value.trim(), pw = $("#l-pw").value, er = $("#l-err");
  const j = await api("/api/login", { email, password: pw }, { noState: true, silent: true, busy: "Вхожу в аккаунт…" });
  if (!j) { er.textContent = lastErr; er.hidden = false; return; }
  const wasGuest = me && me.guest;
  await startSession(j.token);
  if (wasGuest && j.user.role === "client") await api("/api/me/claim", { guest: CLIENT }, { noState: true, silent: true });
  await loadOrders();
  toast("Вы вошли как " + j.user.name); afterAuth();
}
async function doRegister() {
  const name = $("#r-name").value.trim(), email = $("#r-mail").value.trim(), pw = $("#r-pw").value, pw2 = $("#r-pw2").value, er = $("#l-err");
  const msg = name.length < 2 ? "Укажите имя" : !/^[^@\s]+@[^@\s]+\.[^@\s]{2,}$/.test(email) ? "Проверьте адрес почты"
    : pw.length < 8 ? "Пароль должен быть не короче 8 символов" : pw !== pw2 ? "Пароли не совпадают" : !$("#r-ok").checked ? "Нужно согласие на обработку данных" : "";
  if (msg) { er.textContent = msg; er.hidden = false; return; }
  const j = await api("/api/register", { name, email, password: pw }, { noState: true, silent: true, busy: "Создаю аккаунт…" });
  if (!j) { er.textContent = lastErr; er.hidden = false; return; }
  const wasGuest = me && me.guest;
  await startSession(j.token);
  if (wasGuest) await api("/api/me/claim", { guest: CLIENT }, { noState: true, silent: true });
  await loadOrders();
  toast("Добро пожаловать, " + j.user.name); afterAuth();
}
async function doLogout() {
  await api("/api/logout", {}, { noState: true, silent: true, busy: "Выхожу…" });
  LS.set("pp_token", ""); closeSheet(); closeFull(); me = null; offersData = null; loy = null; go("login");
}

// ---------------------------------------------------------------- ЧАТ ПОДДЕРЖКИ
function supportBlock(code) {
  return `<div class="small mut" id="sup-ln">Загружаю обращения…</div><button class="btn full" style="margin-top:10px" data-act="support" data-order="${esc(code)}">${icon("chat")} Написать в поддержку по заказу</button>`;
}
async function fillSupportBlock(code) {
  const j = await api("/api/support", undefined, { noState: true, silent: true }); const el = $("#sup-ln");
  if (!el || !j) return;
  const ms = (j.messages || []).filter(m => m.order === code);
  el.innerHTML = ms.length ? `Обращений по заказу: ${ms.filter(m => m.from_ === "user").length}. Последний ответ: <b>${esc(((ms.filter(m => m.from_ !== "user").slice(-1)[0] || {}).text || "").slice(0, 90))}</b>` : "Обращений по этому заказу пока нет.";
}

async function openSupport(code = "") {
  closeSheet(); closeChat(); supportOrder = code || "";
  const f = $("#full"); f.hidden = false; renderFab();
  f.innerHTML = `<div class="fh"><button data-sup="close">Закрыть</button><b>${icon("chat")} Поддержка</b><span style="width:70px"></span></div>
    <div class="supwrap"><div id="supmsgs" aria-live="polite"></div>
      <div class="supctx" id="supctx"></div>
      <div class="chips" id="supchips">${["Где мой заказ?", "Сроки и доставка", "Промокод", "Хочу оператора"].map(t => `<button class="chip" data-supq="${esc(t)}">${t}</button>`).join("")}</div>
      <form id="supform"><input id="supin" placeholder="Напишите вопрос" autocomplete="off" aria-label="Сообщение в поддержку"><button aria-label="Отправить">${icon("send")}</button></form></div>`;
  $("#supform").addEventListener("submit", e => { e.preventDefault(); sendSupport(); });
  await loadSupport();
  clearInterval(supT); supT = setInterval(() => { if ($("#supmsgs")) loadSupport(true); else clearInterval(supT); }, 5000);
}
async function loadSupport(quiet) {
  const j = await api("/api/support", undefined, { noState: true, silent: true });
  const box = $("#supmsgs"); if (!box) return;
  if (!j) { if (!quiet) box.innerHTML = `<div class="err">${esc(lastErr)}</div>`; return; }
  const ms = j.messages || [];
  const who = { user: "u", bot: "a", operator: "o" };
  box.innerHTML = (ms.length ? "" : `<div class="msg a">Здравствуйте! Спросите про доставку, сроки, качество фото или статус заказа. Если не смогу ответить, передам оператору.</div>`) +
    ms.map(m => `<div class="msg ${who[m.from_] || "a"}">${m.from_ === "operator" ? `<small class="lab">Оператор</small>` : m.from_ === "bot" ? `<small class="lab">Помощник</small>` : ""}${esc(m.text)}${m.order ? `<small class="lab">заказ ${esc(m.order)}</small>` : ""}<small class="tm">${esc(fmtTime(m.ts))}</small></div>`).join("");
  $("#supctx").innerHTML = supportOrder ? `${icon("box")} Обращение по заказу <b>${esc(supportOrder)}</b> <button data-sup="unlink" aria-label="Отвязать">✕</button>`
    : (orders.length ? `<span>Привязать к заказу:</span>${orders.slice(0, 3).map(o => `<button class="chip sm" data-sup="link" data-code="${esc(o.code)}">${esc(o.code)}</button>`).join("")}` : "");
  box.scrollTop = box.scrollHeight;
}
async function sendSupport(text) {
  const inp = $("#supin"); text = (text || (inp && inp.value) || "").trim(); if (!text) return;
  if (inp) inp.value = "";
  const j = await api("/api/support", { text, order: supportOrder }, { noState: true, silent: true });
  if (!j) { if (inp) inp.value = text; toast(lastErr + ". Текст сохранён в поле: отправьте ещё раз."); return; }
  await loadSupport();
}

// ---------------------------------------------------------------- ПАНЕЛЬ АДМИНИСТРАТОРА (главный экран роли admin; права проверяет сервер)
const ADMIN_TABS = [["sum", "Сводка", "chart"], ["orders", "Заказы", "box"], ["threads", "Обращения", "chat"], ["prices", "Каталог", "tag"], ["audit", "Журнал", "list"]];
let adminThread = null, adminQ = "", adminF = "all";
async function loadAdmin() {
  const j = await api("/api/admin/overview", undefined, { noState: true, silent: true });
  if (j) { adminData = j; } else if (!adminData) toast(lastErr || "Нет доступа");
  return j;
}
function vAdmin() {
  if (!adminData) { view.innerHTML = `<div class="empty"><div class="spin lg"></div><br>Загружаю данные…</div>`; loadAdmin().then(() => { if (screen === "admin") render(); }); return; }
  const d = adminData, st = d.stats, cur = d.currency;
  if (adminThread) return vAdminThread();
  let h = "";
  if (adminTab === "sum") {
    const mx = Math.max(1, ...st.by_stage.map(x => x.n));
    h = `<div class="kpis">
        <div class="kpi"><small>Заказов всего</small><b>${st.orders}</b></div><div class="kpi"><small>За 24 часа</small><b>${st.today}</b></div>
        <div class="kpi"><small>В работе</small><b>${st.active}</b></div><div class="kpi"><small>Выдано</small><b>${st.finished}</b></div>
        <div class="kpi wide"><small>Выручка (демо)</small><b>${fmt(st.revenue)} ${esc(cur)}</b></div><div class="kpi wide"><small>Средний чек</small><b>${fmt(st.avg)} ${esc(cur)}</b></div>
        <button class="kpi wide ${st.open_tickets ? "alert" : ""}" data-adtab="threads"><small>Обращения, ждут ответа</small><b>${st.open_tickets} из ${st.tickets}</b></button></div>
      <div class="sec">Воронка за 30 дней</div><div class="card stg">${(d.funnel || []).map(x => `<div class="br"><span>${esc(x.title)}</span><div class="bar"><i style="width:${x.share}%"></i></div><b>${x.users}${x.conv !== null && x.conv !== undefined ? ` <small class="mut">${x.conv}%</small>` : ""}</b></div>`).join("")}</div>
      <div class="sec">Интеграция с CRM</div><div class="card crmst"><div class="sum"><span>Режим</span><span>${esc(d.crm.mode === "http" ? "CRM по API" : d.crm.mode === "mock" ? "демо-CRM" : "выключена")}</span></div>
        <div class="sum"><span>Связь</span><span class="${d.crm.ok === false ? "bad" : ""}">${d.crm.ok === null ? "запросов ещё не было" : d.crm.ok ? "работает" : "недоступна, приложение работает без персонализации"}</span></div>
        <div class="sum"><span>Запросов / из кэша</span><span>${d.crm.calls} / ${d.crm.cache_hits}</span></div></div>
      <div class="sec">Система</div><div class="card crmst"><div class="sum"><span>База данных</span><span>${esc(d.db.kind)} · пользователей ${d.db.users}</span></div>
        <div class="sum"><span>Редактор фото</span><span>локальные приёмы (без генерации нейросетью)</span></div></div>
      <div class="sec">Заказы по этапам</div><div class="card stg">${st.by_stage.map(x => `<div class="br"><span>${esc(x.title)}</span><div class="bar"><i style="width:${Math.round(x.n / mx * 100)}%"></i></div><b>${x.n}</b></div>`).join("")}</div>
      <div class="sec">Последние заказы</div><div class="adbody" style="padding-top:0">${d.orders.slice(0, 3).map(adminOrderRow).join("") || `<div class="empty">Заказов пока нет</div>`}</div>`;
  } else if (adminTab === "orders") {
    const q = adminQ.toLowerCase();
    const list = d.orders.filter(o => (adminF === "all" || (adminF === "active" ? !o.finished : o.finished)) && (!q || (o.code + o.customer_name + o.phone).toLowerCase().includes(q)));
    h = `<div class="adtools"><input id="adq" type="search" placeholder="Поиск: код, имя, телефон" value="${esc(adminQ)}" aria-label="Поиск заказов">
        <div class="seg">${[["all", "Все"], ["active", "В работе"], ["done", "Выданные"]].map(f => `<button class="${adminF === f[0] ? "on" : ""}" data-adf="${f[0]}">${f[1]}</button>`).join("")}</div></div>
      <div class="adbody">${list.map(adminOrderRow).join("") || `<div class="empty">Ничего не найдено</div>`}</div>`;
  } else if (adminTab === "threads") {
    h = `<div class="adbody">${d.threads.map(t => `<button class="arow th" data-adthread="${esc(t.uid)}"><div class="ah"><b>${esc(t.name || t.uid)}</b><span class="st ${t.status}">${t.status === "open" ? "нужен ответ" : t.status === "answered" ? "отвечено" : "новое"}</span></div><small>${esc(t.last)}</small></button>`).join("") || `<div class="empty">Обращений нет</div>`}</div>`;
  } else if (adminTab === "prices") {
    if (!admCat) { view.innerHTML = `<div class="empty"><div class="spin lg"></div><br>Загружаю цены…</div>`; loadAdminCat().then(() => { if (screen === "admin") render(); }); return; }
    h = pricesHtml();
  } else {
    h = `<div class="adbody audit">${d.audit.map(a => `<div class="ar"><b>${esc(a.action)}</b><span>${esc(a.user)} (${esc(a.role)}) · ${esc(a.object || "—")} · ${esc(a.result)}</span>${a.extra && a.extra.changes ? `<ul class="chg">${a.extra.changes.slice(0, 12).map(x => `<li>${esc(x)}</li>`).join("")}${a.extra.changes.length > 12 ? `<li>и ещё ${a.extra.changes.length - 12}</li>` : ""}</ul>` : ""}<small>${esc(a.ts)}</small></div>`).join("")}</div>`;
  }
  view.innerHTML = h;
}
// ---- Если админ убрал из каталога то, что пользователь уже выбрал (услугу, обложку, формат, бумагу), мягко объясняем и предлагаем замену.
// Фото и макет при этом сохраняются.
const issueCard = pr => `<div class="issue"><b>${esc(pr.name)}</b><p>${esc(pr.issue ? pr.issue.text : "")}</p>${pr.issue ? `<button class="btn p" data-issue="${pr.id}">Выбрать замену</button>` : ""}</div>`;
const _issueShown = {};
function issueSheet(pid) {
  const pr = (S.projects || []).find(p => p.id === pid); if (!pr || !pr.issue) return;
  const is = pr.issue, ttl = { product: "Этой услуги больше нет", cover: "Этой обложки больше нет", format: "Этого формата больше нет", paper: "Этой бумаги больше нет" }[is.field];
  openSheet(`<h3>${ttl}</h3>
    <div class="pad mut" style="margin:-4px 0 10px">Проект «${esc(pr.name)}»: ${esc(is.text)}</div>
    ${is.options.length ? `<div class="pad" role="radiogroup">${is.options.map(o => `<button class="opt" data-repl="${pr.id}:${is.field}:${o.id}"><span class="rd"></span><span class="g">${esc(o.name)}</span><span class="pr">${esc(o.note || "")}</span></button>`).join("")}</div>`
      : `<div class="pad mut">Подходящей замены пока нет. Проект сохранён, загляните позже.</div>`}
    <div class="pad"><button class="btn full" data-act="closesheet">Решу позже</button></div>`);
}
async function doReplace(pid, field, val) {
  const j = await api("/api/project/replace", { id: pid, field, value: val }, { busy: "Заменяю…" });
  if (!j) return;
  closeSheet(); toast("Готово: заменили на «" + (j.name || "выбранный вариант") + "». Фото и макет сохранены.");
  const next = (S.projects || []).find(p => p.issue); if (next) setTimeout(() => issueSheet(next.id), 500);
  render();
}
function checkIssues() {
  if (!S || !S.projects || !S.me || S.me.role === "anon" || !["home", "projects", "cart", "app"].includes(screen) || !$("#sheet").hidden) return;
  const p = S.projects.find(x => x.issue && !x.ordered && !_issueShown[x.id + ":" + x.issue.field + ":" + x.issue.what]);
  if (!p) return;
  _issueShown[p.id + ":" + p.issue.field + ":" + p.issue.what] = 1;
  setTimeout(() => issueSheet(p.id), 400);
}
// ---- вкладка «Цены»: редактирование каталога (товары, форматы, обложки, доставка, промокоды), запись в журнал на сервере
let admCat = null;
async function loadAdminCat() {
  const j = await api("/api/admin/catalog", undefined, { noState: true, silent: true, wait: "Загружаю цены…" });
  if (j) admCat = j; else toast(lastErr || "Нет доступа");
  return j;
}
const pin = (path, v, step = "0.01", w = "") => `<input class="pin ${w}" type="number" inputmode="decimal" step="${step}" min="0" value="${v === undefined || v === null ? "" : v}" data-cp="${path}" aria-label="${path}">`;
const ptx = (path, v) => `<input class="pin tx" type="text" value="${esc(v || "")}" data-cp="${path}" aria-label="${path}">`;
const rid = () => Math.random().toString(36).slice(2, 8);
const chk = (path, v, label) => `<label class="chk"><input type="checkbox" data-cp="${path}" ${v !== false ? "checked" : ""}> ${label}</label>`;
const delB = (path, what) => `<button class="pdel" data-act="catdel" data-path="${path}" aria-label="Удалить ${esc(what)}">${icon("trash")}</button>`;
function pricesHtml() {
  const c = admCat.catalog;
  const prod = c.products.map((p, i) => `<div class="pc2${p.active === false ? " off" : ""}"><div class="pch"><b>${p.kind === "book" ? "Фотокнига" : "Печать"}</b>${delB(`products.${i}`, p.name)}</div>
    <label>Название ${ptx(`products.${i}.name`, p.name)}</label>
    <label>Описание ${ptx(`products.${i}.text`, p.text)}</label>
    ${chk(`products.${i}.active`, p.active, "включён (виден покупателям)")}
    <label>Цена «от», руб. ${pin(`products.${i}.from_price`, p.from_price)}</label>
    <label>Старая цена ${pin(`products.${i}.old_price`, p.old_price)}</label>
    ${p.kind === "book" ? `<label>За каждый разворот сверх мин. ${pin(`products.${i}.per_spread`, p.per_spread)}</label><label>Мин. разворотов ${pin(`products.${i}.min_spreads`, p.min_spreads, "1")}</label>` : ""}
    <label>Срок ${ptx(`products.${i}.lead`, p.lead)}</label>
    ${p.kind === "print" ? `<div class="pgroup">Форматы</div>${(p.formats || []).map((f, k) => `<div class="prow">${ptx(`products.${i}.formats.${k}.name`, f.name)}<span class="mx">×</span>${pin(`products.${i}.formats.${k}.mult`, f.mult, "0.01", "s")}${delB(`products.${i}.formats.${k}`, f.name)}</div>`).join("")}
      <button class="padd" data-act="catadd" data-kind="format" data-path="${i}">+ формат</button>
      <div class="pgroup">Бумага</div>${(p.papers || []).map((f, k) => `<div class="prow">${ptx(`products.${i}.papers.${k}.name`, f.name)}${delB(`products.${i}.papers.${k}`, f.name)}</div>`).join("")}
      <button class="padd" data-act="catadd" data-kind="paper" data-path="${i}">+ бумага</button>` : ""}</div>`).join("");
  const cov = c.covers.map((x, i) => `<div class="pc2${x.active === false ? " off" : ""}"><div class="prow">${ptx(`covers.${i}.name`, x.name)}${delB(`covers.${i}`, x.name)}</div>
    <label>Доплата, руб. (может быть минусом) <input class="pin" type="number" step="0.01" value="${x.add}" data-cp="covers.${i}.add"></label>${chk(`covers.${i}.active`, x.active, "включена")}</div>`).join("");
  const dl = c.delivery.map((x, i) => `<div class="pc2${x.active === false ? " off" : ""}"><div class="prow">${ptx(`delivery.${i}.name`, x.name)}${delB(`delivery.${i}`, x.name)}</div>
    <label>Цена ${pin(`delivery.${i}.price`, x.price)}</label><label>Срок ${ptx(`delivery.${i}.eta`, x.eta)}</label>
    ${chk(`delivery.${i}.address`, x.address === true ? true : false, "нужен адрес или отделение")}${chk(`delivery.${i}.active`, x.active, "включена")}</div>`).join("");
  const pay = c.payment.map((x, i) => `<div class="pc2${x.active === false ? " off" : ""}"><div class="prow">${ptx(`payment.${i}.name`, x.name)}${delB(`payment.${i}`, x.name)}</div>${chk(`payment.${i}.active`, x.active, "включена")}</div>`).join("");
  const pr = c.promos.map((x, i) => `<div class="pc2${x.active === false ? " off" : ""}"><div class="prow"><input class="pin tx up" type="text" value="${esc(x.code)}" data-cp="promos.${i}.code" aria-label="Код промокода">${delB(`promos.${i}`, x.code)}</div>
    <label>Описание ${ptx(`promos.${i}.text`, x.text)}</label><label>Скидка, % ${pin(`promos.${i}.percent`, x.percent, "1")}</label>
    ${chk(`promos.${i}.free_delivery`, x.free_delivery === true, "бесплатная доставка")}${chk(`promos.${i}.active`, x.active, "включён")}</div>`).join("");
  return `<div class="adbody prices">
    ${admCat.external ? `<div class="pwarn">Включён внешний каталог (CATALOG_API_URL): изменения здесь не будут применяться, пока он активен.</div>` : ""}
    <div class="psrc2 small mut">Версия каталога <b>${esc(admCat.version)}</b> · ${admCat.edited ? "изменён администратором" : "исходный"}. Изменения применяются после «Сохранить», каждое попадает в журнал. То, что уже есть в проектах и заказах, удалить нельзя: отключите галочкой.</div>
    <div class="sec">Товары</div>${prod}
    <div class="padds"><button class="padd" data-act="catadd" data-kind="book">+ фотокнига</button><button class="padd" data-act="catadd" data-kind="print">+ печать фото</button></div>
    <div class="sec">Обложки</div>${cov}<button class="padd" data-act="catadd" data-kind="cover">+ обложка</button>
    <div class="sec">Доставка</div>${dl}<button class="padd" data-act="catadd" data-kind="delivery">+ способ доставки</button>
    <div class="sec">Оплата</div>${pay}<button class="padd" data-act="catadd" data-kind="payment">+ способ оплаты</button>
    <div class="sec">Промокоды</div>${pr}<button class="padd" data-act="catadd" data-kind="promo">+ промокод</button>
    <div class="pbar"><button class="btn p" data-act="pricesave">Сохранить</button><button class="btn" data-act="pricecancel">Отменить правки</button>${admCat.edited ? `<button class="btn" data-act="pricereset">Вернуть исходный каталог</button>` : ""}</div></div>`;
}
const NEW_ITEM = {
  book: () => ({ id: "b" + rid(), kind: "book", name: "Новая фотокнига", from_price: 50, color: "#a9c7dd", text: "Описание", lead: "5–7 дней", req: "JPEG/PNG, от 1200 px по длинной стороне", per_spread: 3, min_spreads: 5, active: true }),
  print: () => ({ id: "p" + rid(), kind: "print", name: "Новая печать", from_price: 0.5, color: "#d9542e", text: "Описание", lead: "1–2 дня", req: "JPEG/PNG, от 800 px по длинной стороне", formats: [{ id: "f" + rid(), name: "10×15 см", mult: 1 }], papers: [{ id: "m" + rid(), name: "Матовая" }], active: true }),
  format: () => ({ id: "f" + rid(), name: "Новый формат", mult: 1 }),
  paper: () => ({ id: "m" + rid(), name: "Новая бумага" }),
  cover: () => ({ id: "c" + rid(), name: "Новая обложка", add: 0, active: true }),
  delivery: () => ({ id: "d" + rid(), name: "Новый способ доставки", price: 5, eta: "3–5 дней", address: true, active: true }),
  payment: () => ({ id: "y" + rid(), name: "Новый способ оплаты", active: true }),
  promo: () => ({ code: "NEW" + Math.floor(Math.random() * 90 + 10), text: "−10% на товары", percent: 10, free_delivery: false, active: true }),
};
function catEdit(fn) {
  admCat.catalog = collectCat();                      // сначала забираем всё, что уже введено в поля
  fn(admCat.catalog);
  const sc = view.scrollTop; render(); view.scrollTop = sc;
}
function catAdd(kind, path) {
  catEdit(c => {
    if (kind === "format" || kind === "paper") c.products[+path][kind === "format" ? "formats" : "papers"].push(NEW_ITEM[kind]());
    else c[{ book: "products", print: "products", cover: "covers", delivery: "delivery", payment: "payment", promo: "promos" }[kind]].push(NEW_ITEM[kind]());
  });
}
function catDel(path) {
  const p = path.split("."), idx = +p.pop();
  catEdit(c => { let o = c; p.forEach(k => { o = o[isNaN(k) ? k : +k]; }); o.splice(idx, 1); });
}
function collectCat() {
  const c = JSON.parse(JSON.stringify(admCat.catalog));
  document.querySelectorAll("[data-cp]").forEach(el => {
    const path = el.dataset.cp.split("."); let o = c;
    for (let i = 0; i < path.length - 1; i++) o = o[isNaN(path[i]) ? path[i] : +path[i]];
    const k = path[path.length - 1];
    if (el.type === "checkbox") o[k] = el.checked;
    else if (el.type === "number") { if (el.value === "" && k === "old_price") delete o[k]; else o[k] = parseFloat(String(el.value).replace(",", ".")); }
    else o[k] = el.value;
  });
  return c;
}
async function priceSave() {
  const j = await api("/api/admin/catalog", { catalog: collectCat() }, { noState: true, busy: "Сохраняю цены…" });
  if (!j) return;
  admCat = j; adminData = null; CAT = null;
  toast(j.changes.length ? "Цены сохранены. Изменений: " + j.changes.length : "Изменений нет");
  render();
}
async function priceReset() {
  if (!confirm("Вернуть исходный каталог? Все правки цен будут отменены.")) return;
  const j = await api("/api/admin/catalog/reset", {}, { noState: true, busy: "Возвращаю каталог…" });
  if (!j) return;
  admCat = j; adminData = null; CAT = null; toast("Исходный каталог возвращён"); render();
}
function adminOrderRow(o) {
  return `<div class="arow"><div class="ah"><b>${esc(o.code)}</b><span>${money(o.price.total)}</span></div>
    <small>${esc(o.customer_name)} · ${esc(o.phone)} · ${o.items.map(i => esc(i.title) + " ×" + i.qty).join(", ")}</small>
    <label class="stg">Статус <select data-adstage="${esc(o.code)}">${adminData.stages.map((t, i) => `<option value="${i}" ${i === o.stage ? "selected" : ""}>${i + 1}. ${esc(t)}</option>`).join("")}</select></label></div>`;
}
async function openAdminThread(uid) {
  const j = await api("/api/admin/thread/" + encodeURIComponent(uid), undefined, { noState: true, silent: true });
  if (!j) return toast(lastErr);
  adminThread = { uid, msgs: j.messages }; render();
  const m = $("#supmsgs"); if (m) m.scrollTop = m.scrollHeight;
}
function vAdminThread() {
  const t = adminThread, nm = { user: "Клиент", bot: "Помощник", operator: "Вы" }, cl = { user: "a", bot: "a", operator: "u" };
  view.innerHTML = `<div class="adback"><button data-adm="back">${BACK} Все обращения</button></div>
    <div id="supmsgs" class="flowmsgs">${t.msgs.map(m => `<div class="msg ${cl[m.from_]}"><small class="lab">${nm[m.from_]}${m.order ? " · " + esc(m.order) : ""}</small>${esc(m.text)}</div>`).join("")}</div>
    <form id="supform" class="stickyform"><input id="supin" placeholder="Ответ клиенту" autocomplete="off" aria-label="Ответ клиенту"><button aria-label="Отправить">${icon("send")}</button></form>`;
  $("#supform").addEventListener("submit", async e => {
    e.preventDefault(); const x = $("#supin").value.trim(); if (!x) return;
    await api("/api/admin/thread/" + encodeURIComponent(t.uid), { text: x }, { noState: true }); await loadAdmin(); openAdminThread(t.uid);
  });
}

// ---------------------------------------------------------------- события
document.addEventListener("click", async e => {
  if (e.target.closest("#toast")) return;
  const t = e.target.closest("button, [data-act], [data-tab], [data-svc], [data-open], [data-track]"); if (!t) return;
  const d = t.dataset;

  if (t.id === "fab") return chatOpen ? closeChat() : openChat();
  if (d.act === "chat-close" || t.closest(".ch-x")) return closeChat();
  if (d.tab) return openTab(d.tab);
  if (d.step) {
    if (!curProj()) return toast("Сначала выберите услугу на главной");
    if (d.step === "1") return go("app", { tab: "photos" });
    if (d.step === "2") return go("app", { tab: "spreads" });
    return openCart(true);
  }
  if (d.fill) { const [m, p] = d.fill.split(":"); $("#l-mail").value = m; $("#l-pw").value = p; return; }
  if (d.sup) {
    if (d.sup === "close") return closeFull();
    if (d.sup === "unlink") { supportOrder = ""; return loadSupport(); }
    if (d.sup === "link") { supportOrder = d.code; return loadSupport(); }
  }
  if (d.supq) return sendSupport(d.supq);
  if (d.atab) { adminTab = d.atab; adminThread = null; return go("admin", { anim: false }); }
  if (d.adm === "back") { adminThread = null; return loadAdmin().then(() => render()); }
  if (d.adf) { adminF = d.adf; return render(); }
  if (d.adtab) { adminTab = d.adtab; adminThread = null; return go("admin", { anim: false }); }
  if (d.adthread) return openAdminThread(d.adthread);
  if (d.offer) return svcSheet(d.offer);
  if (d.svc) return svcSheet(d.svc);
  if (d.issue) { e.stopPropagation(); return issueSheet(d.issue); }
  if (d.repl) { const [pid, field, val] = d.repl.split(":"); return doReplace(pid, field, val); }
  if (d.delproj) { e.stopPropagation(); if (confirm("Удалить этот проект?")) { await api("/api/project/delete", { id: d.delproj }); render(); } return; }
  if (d.open) return openProject(d.open);
  if (d.q !== undefined) return sendChat((ctxExplicit && ctxPhoto ? PHOTO_QUICK : QUICK)[+d.q][1]);
  if (d.edit) return openEditor(d.edit, d.ep || "", true);
  if (d.track) return openTrack(d.track, screen === "home" ? "home" : "orders", true);
  if (d.scope) { themeScope = d.scope; return coverSheet(); }
  if (d.theme) { await api("/api/theme", { theme: d.theme, si: themeScope === "all" ? null : curSpread }); render(); return coverSheet(); }
  if (d.flip) { if (d.flip === "close") return flipClose(); return flipGo(d.flip === "next" ? 1 : -1); }
  if (d.tpl) return tplDetail(d.tpl);
  if (d.photo) return photoSheet(d.photo);
  if (d.slot) { const [i, j] = d.slot.split(":").map(Number); curSpread = i; return slotSheet(i, j); }
  if (d.sp !== undefined) return spreadMenu(+d.sp);
  if (d.opt) {
    const [k, v] = d.opt.split(":");
    if (screen === "checkout") { ck[k] = v; saveCk(); if (k === "delivery") await refreshQuote(); return render(); }
    svcPick[k] = v; t.parentElement.querySelectorAll(".opt").forEach(b => b.classList.toggle("on", b === t)); return;
  }
  if (d.pp) { const [k, v] = d.pp.split(":"); await api("/api/project/params", { params: { [k]: v } }); return render(); }
  if (d.pq) { const [id, dl] = d.pq.split(":"), p = S.photos.find(x => x.id === id); await api("/api/print/qty", { photo: id, qty: Math.max(0, (p.qty || 0) + +dl) }); return render(); }
  if (d.pqa) { await api("/api/print/qty", { all: true, qty: +d.pqa }); return render(); }
  if (d.cq) { const [id, q] = d.cq.split(":"); await api("/api/cart/qty", { id, qty: +q }); return render(); }
  if (d.cdel) { await api("/api/cart/qty", { id: d.cdel, qty: 0 }); return render(); }
  if (d.cedit) { return openProject(d.cedit); }

  if (d.chip !== undefined) {
    const c = EDCHIPS[+d.chip], ta = $("#edp");
    ev.prompt = c[1]; if (ta) ta.value = c[1];
    if (c[2]) { ev.marking = true; drawEditor(); }
    return;
  }
  if (d.em) {
    switch (d.em) {
      case "close": return closeFull();
      case "mark": ev.marking = !ev.marking; { const ta = $("#edp"); if (ta) ev.prompt = ta.value; } return drawEditor();
      case "clearmarks": ev.marks = []; return drawEditor();
      case "run": return runEdit();
      case "before": ev.view = "before"; return drawEditor();
      case "after": ev.view = "after"; return drawEditor();
      case "again": ev.res = null; ev.token = null; ev.err = ""; return drawEditor();
      case "apply-replace": case "apply-copy": {
        const j = await api("/api/edit/apply", { token: ev.token, mode: d.em === "apply-copy" ? "copy" : "replace" }, { busy: "Сохраняю…" });
        if (j) { toast(d.em === "apply-copy" ? "Добавлено как новое фото" : "Фото обновлено, кадры пересчитаны"); closeFull(); render(); }
        return;
      }
      case "revert": { const j = await api("/api/photo/" + ev.pid + "/revert", {}, { busy: "Возвращаю оригинал…" }); if (j) { toast("Оригинал возвращён"); closeFull(); render(); } return; }
    }
  }
  if (d.ed) {
    if (d.ed === "close") return closeFull();
    if (d.ed === "apply") { const c = curCrop(), si = ed.si, sj = ed.sj; await api("/api/crop", { si, sj, crop: c }, { quiet: true }); closeFull(); return render(); }
    if (d.ed === "ai") {
      const si = ed.si, sj = ed.sj, j = await api("/api/crop", { si, sj, auto: true }, { quiet: true });
      if (j) { const c = S.spreads[si].slots[sj].crop; ed.zoom = Math.min(4, Math.max(1, ed.wmax / c[2])); ed.cx = c[0] + c[2] / 2; ed.cy = c[1] + c[3] / 2; $("#zm").value = ed.zoom; drawCrop(); toast("ИИ подобрал кадрирование"); }
      return;
    }
  }

  if (d.sh) {
    const si = +d.si, sj = +d.sj;
    switch (d.sh) {
      case "ask": return askAI(d.id);
      case "enh": { await api(`/api/photo/${d.id}/enhance`, { on: d.on === "1" }, { busy: "Применяю…" }); render(); return photoSheet(d.id); }
      case "mv": { await api("/api/photos/move", { id: d.id, dir: +d.d }); render(); return photoSheet(d.id); }
      case "keepbad": return closeSheet();
      case "replacebad": { closeSheet(); await api("/api/photo/" + d.id, { delete: true }); render(); return $("#file").click(); }
      case "delbad": { await api("/api/photo/" + d.id, { delete: true }); render(); const left = badList.filter(b => b.id !== d.id && S.photos.some(p => p.id === b.id)); badList = left; return left.length ? badSheet() : closeSheet(); }
      case "place": {
        closeSheet();
        const free = S.spreads.flatMap((sp, i) => sp.slots.map((s, j) => ({ i, j, s }))).filter(x => !x.s.photo);
        openSheet(`<h3>Куда поставить?</h3>` + (free.length ? free.map(x => `<button class="act" data-sh="setphoto" data-si="${x.i}" data-sj="${x.j}" data-id="${d.id}"><span class="em">▫️</span>Разворот ${x.i + 1}, слот ${x.j + 1}</button>`).join("")
          : `<div class="note">Пустых слотов нет. Добавьте разворот на вкладке «Альбом».</div>`));
        return;
      }
      case "acrop": { closeSheet(); await api("/api/ai", { op: "recrop_photo", id: d.id }, { busy: "Кадрирую…" }); return render(); }
      case "rot": closeSheet(); await api("/api/photo/" + d.id, { rotate: +d.d }, { busy: "Пересчитываю…" }); return render();
      case "excl": { closeSheet(); const p = S.photos.find(x => x.id === d.id); await api("/api/photo/" + d.id, { excluded: !p.excluded }); return render(); }
      case "del": closeSheet(); await api("/api/photo/" + d.id, { delete: true }); return render();
      case "fill1": closeSheet(); await api("/api/ai", { op: "fill" }, { busy: "ИИ подбирает фото…" }); return render();
      case "pickphoto": closeSheet(); return pickPhotoSheet(si, sj);
      case "setphoto": closeSheet(); await api("/api/slot", { si, sj, photo: d.id }, { busy: "Кадрирую…" }); if (tab === "photos") tab = "spreads"; return render();
      case "crop": return openCrop(si, sj);
      case "aicrop": closeSheet(); await api("/api/crop", { si, sj, auto: true }); return render();
      case "clear": closeSheet(); await api("/api/slot", { si, sj, photo: null }); return render();
      case "spbg": { await api("/api/spread", { op: "bg", si, bg: d.c ? d.c.split(",").map(Number) : null }); render(); return spreadMenu(si); }
      case "covbg": { await api("/api/cover/style", { bg: d.c ? d.c.split(",").map(Number) : null }); render(); return coverSheet(); }
      case "sptheme": { themeScope = String(si); curSpread = si; return coverSheet(); }
      case "tpl": closeSheet(); await api("/api/template", { si, template: d.id }, { busy: "Перестраиваю разворот…" }); return render();
      case "text": closeSheet(); return textSheet(si);
      case "cover": await api("/api/project/params", { params: { cover: d.id } }); render(); return coverSheet();
      case "savetitle":
        await api("/api/text", { field: "cover_title", text: $("#t-title").value }, { quiet: true });
        await api("/api/text", { field: "cover_subtitle", text: $("#t-sub").value }, { quiet: true });
        toast("Название сохранено"); render(); return coverSheet();
      case "savetext": {
        const v = $("#txt").value, v2 = $("#txt2");
        closeSheet();
        await api("/api/text", { field: d.f, si, text: v }, { quiet: true });
        if (v2) await api("/api/text", { field: "subtitle", si, text: v2.value }, { quiet: true });
        return render();
      }
      case "up": case "down": closeSheet(); await api("/api/spread", { op: "move", si, dir: d.sh === "up" ? -1 : 1 }); curSpread = si + (d.sh === "up" ? -1 : 1); return render();
      case "rmspread": closeSheet(); await api("/api/spread", { op: "remove", si }); return render();
    }
  }

  switch (d.act) {
    case "undo": await api("/api/undo", {}); return render();
    case "redo": await api("/api/redo", {}); return render();
    case "settings": return settingsSheet();
    case "orders-all": closeSheet(); return openOrders();
    case "guest": return doGuest();
    case "dologin": return doLogin();
    case "doregister": return doRegister();
    case "authmode": authMode = d.mode; return vLogin();
    case "relogin": closeSheet(); return go("login");
    case "logout": return doLogout();
    case "support": return openSupport(d.order || "");
    case "applypromo": { await refreshQuote(); return render(); }
    case "toggle-push": {
      if (!window.Notification) return toast("Ваш браузер не поддерживает системные уведомления: уведомления будут внутри приложения");
      const r = await Notification.requestPermission(); toast(r === "granted" ? "Уведомления включены" : "Системные уведомления отключены: статусы будут показываться внутри приложения"); return settingsSheet();
    }
    case "deldata":
      if (confirm("Удалить все ваши фото, проекты, заказы и обращения? Это нельзя отменить.")) {
        const j = await api("/api/me/delete", {}, { noState: true });
        if (j) { await refresh(); await loadOrders(); closeSheet(); toast(`Удалено: проектов ${j.projects}, заказов ${j.orders}`); go("home"); }
      }
      return;
    case "findcode": return findCodeSheet();
    case "gocode": { const c = $("#code").value.trim(); if (c) { closeSheet(); return openTrack(c, screen === "home" ? "home" : "orders", true); } return; }
    case "svcgo": return startProject(d.id);
    case "svctpl": return tplGallery(d.id);
    case "tplback": return tplGallery();
    case "tpluse": return startProject(tplProd || defBook(), d.id);
    case "newproj": go("home"); return requestAnimationFrame(() => { const e = document.querySelector(".svcs"); if (e) view.scrollTop = e.offsetTop - 70; });
    case "flip": return openFlip();
    case "loyhelp": return loySheet();
    case "closesheet": return closeSheet();
    case "amenu": menuOpen = !menuOpen; return renderMenu(true);
    case "setdisc": ck.disc = d.id; saveCk(); await refreshQuote(); return render();
    case "togfree": ck.nofree = !ck.nofree; saveCk(); await refreshQuote(); return render();
    case "usepts": ck.usePts = !ck.usePts; await refreshQuote(); return render();
    case "share": return shareSheet();
    case "shareon": await api("/api/share", { on: !(curProj().share || {}).on }, { busy: "Обновляю ссылку…" }); return shareSheet();
    case "sharenew": await api("/api/share", { on: true, renew: true }, { busy: "Создаю новую ссылку…" }); toast("Новая ссылка создана, старая больше не работает"); return shareSheet();
    case "sharecopy": { const u = shareUrl(); try { await navigator.clipboard.writeText(u); toast("Ссылка скопирована"); } catch (e) { prompt("Скопируйте ссылку", u); } return; }
    case "sharesend": { const u = shareUrl(); if (navigator.share) { try { await navigator.share({ title: "Совместный альбом PicPac", text: "Добавляй свои фото в наш альбом", url: u }); } catch (e) {} } else { try { await navigator.clipboard.writeText(u); toast("Ссылка скопирована"); } catch (e) {} } return; }
    case "join-home": history.pushState({}, "", "/"); LS.set("pp_join", ""); return go("home");
    case "join-go": return joinNow();
    case "join-pick": return $("#jfile").click();
    case "join-open": history.pushState({}, "", "/"); LS.set("pp_join", ""); { const pid = joinData && joinData.pid; if (pid) { await api("/api/project/open", { id: pid }); } } return go("app", { tab: "spreads" });
    case "join-guest": { const j = await api("/api/guest", { client: CLIENT }, { busy: "Открываю гостевой режим…", noState: true }); if (!j) return; await startSession(j.token); return openJoin(joinTok); }
    case "join-login": authMode = "login"; return go("login");
    case "catadd": return catAdd(d.kind, d.path);
    case "catdel": return catDel(d.path);
    case "pricecancel": admCat = null; return loadAdminCat().then(() => render());
    case "pricesave": return priceSave();
    case "pricereset": return priceReset();
    case "lang": showPill("Переключаю язык…"); setTimeout(() => I18N.set(d.l), 120); return;
    case "coversheet": return coverSheet();
    case "ctxclear": ctxPhoto = null; ctxExplicit = false; LS.set("pp_ctx", ""); return renderChat();
    case "back-cart": return go("cart", { flow: true });
    case "back-orders": history.pushState({}, "", "/"); order = null; return trackFrom === "home" ? go("home") : openOrders();
    case "back-home": return go("home");
    case "tocheckout": return openCheckout();
    case "submit": return submitOrder();
    case "demostart": {
      const id = defBook(), p = prod(id); svcPick = {};
      let j = await api("/api/project/new", { product: id, params: { cover: defCover() } }, { busy: "Создаю проект…" });
      if (!j) return;
      j = await api("/api/demo", {}, { busy: "Добавляю демо-фото…" });
      if (j) { go("app", { tab: "photos" }); toast("Демо-фото добавлены. Разложить их по альбому можно в «Быстрых командах»."); }
      return;
    }
    case "sortdate": LS.set("pp_sort", LS.get("pp_sort") === "old" ? "new" : "old"); return render();
    case "pick": return $("#file").click();
    case "demo": await api("/api/demo", {}, { busy: "ИИ оценивает демо-кадры…" }); return render();
    case "autoplan": closeSheet(); await api("/api/ai", { op: "autoplan" }, { busy: "ИИ собирает альбом…" }); return go("app", { tab: "spreads" });
    case "fill": closeSheet(); await api("/api/ai", { op: "fill" }, { busy: "ИИ заполняет слоты…" }); return render();
    case "recrop": closeSheet(); await api("/api/ai", { op: "recrop_all" }, { busy: "Кадрирую…" }); return render();
    case "balance": closeSheet(); await api("/api/ai", { op: "balance" }, { busy: "Распределяю…" }); return render();
    case "addspread": closeSheet(); await api("/api/spread", { op: "add" }); curSpread = S.spreads.length - 1; render(); return requestAnimationFrame(() => { view.scrollTop = view.scrollHeight; });
    case "pdf": location.href = `/api/pdf?dpi=150&sr=${S.settings.upscale ? 1 : 0}`; return toast("Готовлю PDF…");
    case "toggle-upscale": await api("/api/settings", { upscale: !S.settings.upscale }); return $("#sheet").hidden ? render() : (curProj() && tab === "spreads" && screen === "app" ? coverSheet() : settingsSheet());
    case "quick": return quickSheet();
    case "toggle-autoenh": await api("/api/project/auto_enhance", { on: !S.auto_enhance }, { busy: "Пересчитываю…" }); render(); return quickSheet();
    case "toggle-pers": LS.set("pp_pers", persOn() ? "0" : "1"); await loadOffers(true); return settingsSheet();
    case "pers-off": LS.set("pp_pers", "0"); offersData = null; await refreshQuote(); return render();
    case "pers-on": LS.set("pp_pers", "1"); await loadOffers(true); await refreshQuote(); return render();
    case "clearchat": await api("/api/chat/clear", {}); closeSheet(); return chatOpen && renderChat();
    case "advance": { const j = await api("/api/track/" + order.code + "/advance", {}, { noState: true }); if (j) { order = j.order; render(); } return; }
    case "copycode": case "copylink": {
      const txt = d.act === "copycode" ? order.code : location.origin + "/track/" + order.code;
      try { await navigator.clipboard.writeText(txt); toast("Скопировано: " + txt); } catch (err) { toast(txt); }
      return;
    }
  }
});

document.addEventListener("input", e => {
  if (e.target.id === "adq") { adminQ = e.target.value; const pos = e.target.selectionStart; render(); const q = $("#adq"); if (q) { q.focus(); q.setSelectionRange(pos, pos); } return; }
  const k = e.target.dataset && e.target.dataset.ck;
  if (k) { ck[k] = k === "promo" ? e.target.value.toUpperCase() : e.target.value; saveCk(); }
});
document.addEventListener("change", async e => {
  const t = e.target;
  if (t.dataset && "risk" in t.dataset) ck.risk = t.checked;
  if (t.dataset && t.dataset.adstage) {
    const j = await api("/api/admin/order/" + t.dataset.adstage + "/status", { stage: +t.value }, { noState: true });
    if (j) { toast("Статус изменён: " + j.order.status_title); const o = adminData.orders.find(x => x.code === t.dataset.adstage); if (o) o.stage = j.order.stage; }
  }
});
document.addEventListener("keydown", e => {
  if (e.key !== "Escape") return;
  if (!$("#sheet").hidden) return closeSheet();
  if (!$("#full").hidden) return closeFull();
  if (chatOpen) return closeChat();
});
window.addEventListener("offline", () => { $("#offline").hidden = false; });
document.addEventListener("visibilitychange", () => { if (document.visibilityState === "visible" && S && S.me && S.me.role !== "anon" && screen !== "login") refresh(); });
window.addEventListener("online", () => {
  $("#offline").hidden = true; toast("Связь восстановлена"); drainUploads();
  if (screen === "checkout" && ck.pending && !submitOrder.busy) submitOrder();
});
if (!navigator.onLine) $("#offline").hidden = false;
window.addEventListener("offline", () => UQ.count().then(offlineText));

// ---- Очередь загрузки (офлайн): выбранные фото сначала кладутся в IndexedDB на устройстве и уходят на сервер порциями.
// При обрыве связи они ждут и догружаются сами (после появления сети или при следующем запуске), дублей не будет: файл
// удаляется из очереди только после ответа сервера.
const UQ = {
  db: null,
  open() {
    if (this.db) return Promise.resolve(this.db);
    return new Promise(res => {
      try {
        const r = indexedDB.open("picpac_uq", 1);
        r.onupgradeneeded = () => r.result.createObjectStore("q", { keyPath: "id", autoIncrement: true });
        r.onsuccess = () => { this.db = r.result; res(this.db); };
        r.onerror = () => res(null);
      } catch (e) { res(null); }
    });
  },
  async tx(mode, fn) {
    const db = await this.open(); if (!db) return null;
    return new Promise(res => { try { const t = db.transaction("q", mode), s = t.objectStore("q"), r = fn(s); t.oncomplete = () => res(r && "result" in r ? r.result : true); t.onerror = () => res(null); } catch (e) { res(null); } });
  },
  add(it) { return this.tx("readwrite", s => s.add(it)); },
  all() { return this.tx("readonly", s => s.getAll()).then(x => x || []); },
  del(id) { return this.tx("readwrite", s => s.delete(id)); },
  async count() { return (await this.all()).length; },
};
let uqBusy = false, uqAdded = 0;
function offlineText(n) {
  const el = $("#offline"); if (!el) return;
  el.textContent = n ? `Нет сети. Фото в очереди: ${n}. Они загрузятся сами, когда связь вернётся. Черновик сохранён, заказ не задвоится.`
    : "Нет сети. Ваши данные не потеряны: черновик сохранён, заказ не задвоится при повторной отправке.";
}
async function drainUploads() {
  if (uqBusy || !S || !navigator.onLine) return;
  uqBusy = true;
  try {
    let items = await UQ.all();
    offlineText(items.length);
    while (items.length) {
      const pid = items[0].pid, batch = items.filter(x => x.pid === pid).slice(0, 6);
      if (!(S.projects || []).some(p => p.id === pid)) { for (const b of batch) await UQ.del(b.id); items = await UQ.all(); continue; }   // проект удалён
      if (S.cur !== pid) { const o = await api("/api/project/open", { id: pid }, { silent: true }); if (!o) { if (lastErr === "Нет связи с сервером") break; for (const b of batch) await UQ.del(b.id); items = await UQ.all(); continue; } }
      const fd = new FormData();
      batch.forEach(b => { fd.append("files", b.blob, b.name); fd.append("mt", String(b.mt || 0)); });
      const left = items.length;
      const j = await api("/api/upload", fd, { busy: `Загружаю фото (в очереди ${left})…`, quiet: true });
      if (!j && lastErr === "Нет связи с сервером") break;       // связи нет: оставляем в очереди
      for (const b of batch) await UQ.del(b.id);                 // ответ получен (успех или понятный отказ): файл из очереди убираем
      if (j) { uqAdded += j.added || 0; (j.bad || []).forEach(b => badList.push(b)); }
      items = await UQ.all();
    }
    offlineText(items.length);
  } finally { uqBusy = false; }
  if (uqAdded) {
    const added = uqAdded; uqAdded = 0;
    if (S && S.photos) {
      if (tab !== "photos" || screen !== "app") go("app", { tab: "photos" }); else render();
      toast(isPrint() ? `Добавлено фото: ${added}` : `Добавлено фото: ${added}. В альбом они попадут, когда вы запустите «Быстрые команды».`);
      badList = badList.filter(b => S.photos.some(p => p.id === b.id));
      if (badList.length) badSheet();
    }
  }
}
$("#file").addEventListener("change", async e => {
  const files = [...e.target.files]; e.target.value = "";
  if (!files.length || !curProj()) return;
  const pid = S.cur;
  const saved = await Promise.all(files.map(f => UQ.add({ pid, name: f.name, mt: f.lastModified || 0, blob: f })));
  if (saved.some(x => x === null)) {                              // IndexedDB недоступна: загружаем напрямую, как раньше
    let added = 0;
    for (let i = 0; i < files.length; i += 6) {
      const fd = new FormData();
      files.slice(i, i + 6).forEach(f => { fd.append("files", f, f.name); fd.append("mt", String(f.lastModified || 0)); });
      const j = await api("/api/upload", fd, { busy: `Загружаю фото ${Math.min(i + 6, files.length)}/${files.length}…`, quiet: true });
      if (j) { added += j.added || 0; (j.bad || []).forEach(b => badList.push(b)); }
    }
    uqAdded += added;
    if (!added) { if (S.cur === pid) render(); return; }
    return drainUploads();
  }
  if (!navigator.onLine) { offlineText(await UQ.count()); $("#offline").hidden = false; toast(`Нет сети: ${files.length} фото ждут в очереди и загрузятся сами`); return; }
  await drainUploads();
});
let badList = [];
function badSheet() {
  openSheet(`<h3>Есть некачественные фото</h3><div class="pad mut" style="margin:-4px 0 6px">На печати они будут выглядеть хуже остальных. Замените снимок или продолжайте осознанно.</div>
    ${badList.map(b => `<div class="badrow"><img loading="lazy" decoding="async" src="${pimg(b.id,120)}" alt=""><div><b>${esc(b.name)}</b><small>${esc(b.message)}</small>
      <div class="row" style="margin-top:6px"><button class="chip" data-sh="replacebad" data-id="${b.id}">Заменить</button><button class="chip" data-sh="delbad" data-id="${b.id}">Удалить</button></div></div></div>`).join("")}
    <div class="pad" style="margin-top:8px"><button class="btn p full" data-sh="keepbad">Оставить как есть и продолжить</button></div>`);
}


// ---------------------------------------------------------------- ПЛАВАЮЩЕЕ МЕНЮ АЛЬБОМА
// Кнопка-иконка закреплена на экране и не двигается при прокрутке; по нажатию раскрывает короткий список функций.
let menuOpen = false;
function renderMenu(allowed) {
  const el = $("#amenu");
  const show = allowed && tab === "spreads" && curProj() && curProj().kind === "book";
  el.hidden = !show;
  if (!show) { menuOpen = false; el.classList.remove("open"); return; }
  const sh = curProj().share || {}, free = (S.photos || []).filter(p => !p.used && !p.excluded).length;
  el.classList.toggle("open", menuOpen);
  el.innerHTML = `<div class="am-list" role="menu">
      <button role="menuitem" data-act="flip"><svg viewBox="0 0 24 24"><path d="M2.5 12S6 5.5 12 5.5 21.5 12 21.5 12 18 18.5 12 18.5 2.5 12 2.5 12Z"/><circle cx="12" cy="12" r="3"/></svg>Просмотр</button>
      <button role="menuitem" data-act="quick"><svg viewBox="0 0 24 24"><path d="M13 3 5 13.5h6L10 21l8-10.5h-6Z"/></svg>Быстрые команды</button>
      <button role="menuitem" data-act="share"><svg viewBox="0 0 24 24"><circle cx="9" cy="8.5" r="3.2"/><path d="M3 19c.6-3.2 3-5 6-5s5.4 1.8 6 5"/><path d="M16.5 5.5a3 3 0 0 1 0 6M18 14.4c1.7.6 2.7 2.1 3 4.6"/></svg>Поделиться${sh.on ? `<b class="dotp"></b>` : ""}</button></div>
    <button class="am-btn" data-act="amenu" aria-label="Меню альбома" aria-expanded="${menuOpen}"><svg viewBox="0 0 24 24"><path d="M4 7h10M18 7h2M4 17h2M10 17h10"/><circle cx="16" cy="7" r="2"/><circle cx="8" cy="17" r="2"/></svg>${free ? `<i>${free}</i>` : ""}</button>`;
}

document.addEventListener("click", e => {
  if (!menuOpen || e.target.closest(".am-btn")) return;
  menuOpen = false; $("#amenu").classList.remove("open");
}, true);

// ---------------------------------------------------------------- ФОТОБАЛЛЫ (программа лояльности)
let loy = null, loyAt = 0;
const COIN = `<svg viewBox="0 0 32 32" aria-hidden="true"><circle cx="16" cy="16" r="14" fill="#ffd23f"/><circle cx="16" cy="16" r="14" fill="none" stroke="#e8a317" stroke-width="2"/><circle cx="16" cy="16" r="9.5" fill="none" stroke="#e8a317" stroke-width="1.6" stroke-dasharray="2.2 2.2"/><rect x="10.6" y="12.2" width="10.8" height="8" rx="2" fill="#e8a317"/><circle cx="16" cy="16.2" r="2.3" fill="#ffd23f"/><path d="M13.4 12.2l.8-1.5h3.6l.8 1.5z" fill="#e8a317"/></svg>`;
async function loadLoy(force, wait) {
  if (!me || me.guest || me.role !== "client") { loy = null; return; }
  if (!force && loy && Date.now() - loyAt < 20000) return;
  const j = await api("/api/loyalty", undefined, { noState: true, silent: true, wait });
  loy = j && j.account ? j : null; loyAt = Date.now();
}
function refreshLoy() {
  const b = JSON.stringify(loy && loy.account);
  loadLoy().then(() => { if (screen === "home" && $("#sheet").hidden && JSON.stringify(loy && loy.account) !== b) render(); });
}
const ptsWord = n => plural(n, "фотобалл", "фотобалла", "фотобаллов");
function loyCard() {
  if (!loy || !loy.account) return "";
  const a = loy.account;
  return `<div class="loy"><span class="lo-ic">${COIN}</span>
    <div class="lo-t"><small>Мои фотобаллы</small><b>${a.balance}</b>${a.pending ? `<em>+${a.pending} ждут выдачи заказа</em>` : `<em>1 балл = 1 руб. скидки</em>`}</div>
    <button class="lo-how" data-act="loyhelp" aria-label="Как это работает"><i>?</i>Как это работает</button></div>`;
}
const rng = (lo, hi) => hi == null ? `от ${fmt(lo).replace(",00", "")} руб.` : `${fmt(lo).replace(",00", "")}–${fmt(hi - 0.01)} руб.`;
async function loySheet() {
  await loadLoy(true, "Загружаю фотобаллы…");
  const R = (loy && loy.rules) || { tiers: [], caps: [], welcome: 5, spend_min: 30, holidays: [] }, a = loy && loy.account;
  const maxp = Math.max(...R.tiers.map(t => t.pts), 1);
  openSheet(`<div class="lh"><div class="lh-c">${COIN}</div><h3>Фотобаллы PicPac</h3><p>Копите баллы за заказы и оплачивайте ими часть следующих. <b>1 фотобалл = 1 руб.</b></p></div>
    ${a ? `<div class="lh-me"><div><small>Сейчас на счёте</small><b>${a.balance}</b></div><div><small>Ждут выдачи</small><b>${a.pending}</b></div><div><small>Потрачено</small><b>${a.spent}</b></div></div>` : ""}
    <div class="sec">Как это работает</div>
    <div class="lsteps"><div><i>1</i><b>Заказывайте</b><small>фотокниги и печать фото</small></div><span>›</span><div><i>2</i><b>Получайте</b><small>баллы приходят, когда заказ выдан</small></div><span>›</span><div><i>3</i><b>Платите</b><small>баллами за часть заказа</small></div></div>
    <div class="sec">Сколько баллов вы получите</div>
    <div class="lbars">${R.tiers.map(t => `<div class="lb"><span class="lr">${rng(t.lo, t.hi)}</span><span class="lt"><i style="width:${Math.max(8, Math.round(t.pts / maxp * 100))}%"></i></span><b>+${t.pts}</b></div>`).join("")}</div>
    <div class="lnote">Считаем от суммы заказа без доставки, после скидок. Например, фотокнига за 72,90 руб. даст <b>+${(R.tiers.find(t => 72.9 >= t.lo && (t.hi == null || 72.9 < t.hi)) || { pts: 0 }).pts}</b>, набор фото 10×15 за 13 руб. даст <b>+1</b>.</div>
    <div class="lhol"><span class="lx2">×2</span><div><b>В праздники баллов вдвое больше</b><div class="chips">${R.holidays.map(h => `<em>${esc(h)}</em>`).join("")}</div></div></div>
    <div class="lgift"><span>🎁</span><div><b>+${R.welcome} фотобаллов в подарок</b><small>каждому, кто зарегистрировался</small></div></div>
    <div class="sec">Как тратить баллы</div>
    <div class="lcap">${R.caps.map(c => `<div class="lb"><span class="lr">заказ ${c.hi == null ? "от " + c.lo : "до " + c.hi} руб.</span><span class="lt"><i class="c" style="width:${c.pct * 4}%"></i></span><b>до ${c.pct}%</b></div>`).join("")}</div>
    <div class="lnote">Баллами можно оплатить часть стоимости заказа, в таблице видно, какую. Тратить можно после покупок на ${fmt(R.spend_min).replace(",00", "")} руб. Включите «Оплатить фотобаллами» при оформлении заказа.</div>
    <div class="limp"><b>Важно</b>Баллы не суммируются со скидкой по промокоду и персональным предложением: действует что-то одно. Доставка баллами не оплачивается.</div>
    ${a && a.history.length ? `<div class="sec">Последние операции</div>${a.history.slice(0, 5).map(h => `<div class="lhis"><span>${esc(h.code)}</span><span>${h.spent ? `<b class="neg">−${h.spent}</b>` : ""}${h.earned ? `<b class="${h.done ? "pos" : "wait"}">+${h.earned}${h.done ? "" : " · ждёт"}</b>` : ""}</span></div>`).join("")}` : ""}
    <div class="pad"><button class="btn p big full" data-act="closesheet">Понятно</button></div>`);
}
function loyCheckout() {
  const L = quoteLoy;
  if (!L) return "";
  const why = { discount: "Баллы не суммируются со скидкой: сейчас применена скидка по промокоду или предложению.", min: `Тратить баллы можно после покупок на ${fmt(L.spend_min).replace(",00", "")} руб.`, empty: "Пока нечем платить: баллы появятся после первого заказа." }[L.why] || "";
  const sw = L.max > 0 || L.used > 0
    ? `<button class="act" data-act="usepts"><span class="em">${COIN}</span><span style="flex:1">Оплатить фотобаллами<small class="mut" style="display:block">${ck.usePts ? `Спишется ${L.used} из ${L.balance}` : `Можно списать до ${L.max} из ${L.balance}`}</small></span><span class="switch ${ck.usePts ? "on" : ""}"></span></button>`
    : `<div class="lcheck"><span>${COIN}</span><div><b>На счёте ${L.balance} ${ptsWord(L.balance)}</b><small>${why}</small></div></div>`;
  return `<div class="sec">Фотобаллы</div><div class="pad">${sw}<div class="small mut" style="margin:6px 4px">За этот заказ вы получите <b style="color:var(--ink)">+${L.earn} ${ptsWord(L.earn)}</b>${L.bonus ? ` (праздничный бонус ×2: ${esc(L.bonus)})` : ""} после выдачи.</div></div>`;
}

// ---------------------------------------------------------------- СОВМЕСТНЫЙ АЛЬБОМ
const shareUrl = () => location.origin + "/join/" + ((curProj().share || {}).token || "");
function shareSheet() {
  const sh = (curProj() || {}).share || {}, mem = sh.members || [];
  openSheet(`<h3>👥 Совместный альбом</h3>
    <div class="pad mut" style="margin:-4px 0 8px">Отправьте ссылку друзьям и родным: каждый добавит свои фото в общий альбом, а расставите их вы. Участники не видят ваши заказы и не меняют макет.</div>
    <button class="act" data-act="shareon"><span class="em">🔗</span><span style="flex:1">Доступ по ссылке<small class="mut" style="display:block">${sh.on ? "Включён: кто открыл ссылку, может добавлять фото" : "Выключен: ссылка не работает"}</small></span><span class="switch ${sh.on ? "on" : ""}"></span></button>
    ${sh.on ? `<div class="lnk2"><input readonly value="${esc(shareUrl())}" aria-label="Ссылка на альбом" onclick="this.select()"></div>
      <div class="act2"><button class="btn p" data-act="sharesend">Отправить</button><button class="btn" data-act="sharecopy">Скопировать</button></div>
      <div class="sec">Участники · ${mem.length}</div>
      ${mem.length ? mem.map(m => `<div class="mrow"><span class="av2">${esc((m.name || "?")[0].toUpperCase())}</span><b>${esc(m.name)}</b><small>${m.n} ${plural(m.n, "фото", "фото", "фото")}</small></div>`).join("") : `<div class="pad small mut">Пока никто не присоединился.</div>`}
      <div class="pad small mut" style="margin-top:8px">Чужие фото появятся на вкладке «Фото» с пометкой «от имени». В альбом они попадут, когда вы поставите их сами.</div>
      <button class="act" data-act="sharenew"><span class="em">♻️</span>Создать новую ссылку (старая перестанет работать)</button>` : ""}`);
}
let joinTok = "", joinData = null, joinErr = "", joinBusy = "";
async function openJoin(tok) {
  joinTok = tok; joinErr = ""; joinData = null; LS.set("pp_join", tok);
  if (LS.get("pp_token") || LS.get("pp_seen")) {
    const j = await api("/api/join/" + encodeURIComponent(tok), undefined, { noState: true, silent: true, wait: "Открываю совместный альбом…" });
    if (j) joinData = j; else joinErr = lastErr;
  }
  go("join");
}
async function joinNow() {
  const nm = $("#jname"), j = await api("/api/join/" + encodeURIComponent(joinTok), { name: nm ? nm.value.trim() : "" }, { noState: true });
  if (j) { joinData = j; render(); toast("Вы в альбоме. Добавьте свои фото"); }
}
function vJoin() {
  const authed = !!LS.get("pp_token") || !!LS.get("pp_seen");
  let h = `<div class="jhero"><span class="jic">👥</span><h2>Совместный альбом</h2>`;
  if (!authed) {
    view.innerHTML = h + `<p>Вас пригласили добавить фото в общий альбом PicPac.</p></div>
      <div class="pad"><button class="btn p big full" data-act="join-guest">Продолжить как гость</button>
      <button class="btn full" style="margin-top:10px" data-act="join-login">Войти или зарегистрироваться</button></div>`;
    return;
  }
  if (joinErr || !joinData) {
    view.innerHTML = h + `<p>${esc(joinErr || "Загружаю…")}</p></div><div class="pad"><button class="btn full" data-act="join-home">На главную</button></div>`;
    return;
  }
  const d = joinData, mine = d.photos.filter(p => p.mine);
  h += `<p>${d.is_owner ? "Это ваш альбом" : (d.owner ? esc(d.owner) + " приглашает вас в альбом" : "Вас пригласили в альбом")} <b>«${esc(d.title)}»</b></p><small>${d.photos.length} ${plural(d.photos.length, "фото", "фото", "фото")} · участников: ${d.members + 1}</small></div>`;
  if (d.is_owner) {
    h += `<div class="pad"><button class="btn p big full" data-act="join-open">Открыть альбом</button></div>`;
  } else if (!d.joined) {
    h += `<div class="pad">${me && me.guest ? `<label class="field"><span>Ваше имя (увидит владелец)</span><input id="jname" maxlength="30" placeholder="Например, Оля" value="${esc(LS.get("pp_name"))}"></label>` : ""}
      <button class="btn p big full" data-act="join-go">Присоединиться</button></div>`;
  } else {
    h += `<div class="pad"><button class="btn p big full" data-act="join-pick">${icon("plus")} Добавить фото</button>
      <div class="small mut" style="margin-top:6px;text-align:center">Вы добавили ${d.mine} из ${d.limit}. Фото попадают в общий пул, расставляет их владелец.</div></div>
      <input type="file" id="jfile" accept="image/*" multiple hidden>`;
  }
  if (d.photos.length) h += `<div class="sec">Фото в альбоме</div><div class="grid">${d.photos.map(p => `<div class="ph jp"><img loading="lazy" decoding="async" src="/api/join/${encodeURIComponent(joinTok)}/img/${p.id}?w=260" alt="">
    ${p.by ? `<span class="by">от ${esc(p.by)}</span>` : ""}${p.mine ? `<button class="x" data-jdel="${p.id}" aria-label="Удалить моё фото">✕</button>` : ""}</div>`).join("")}</div>`;
  view.innerHTML = h;
  const f = $("#jfile");
  if (f) f.onchange = async () => {
    const files = [...f.files]; f.value = ""; if (!files.length) return;
    let added = 0, failed = [];
    for (let i = 0; i < files.length; i += 4) {
      const fd = new FormData();
      files.slice(i, i + 4).forEach(x => { fd.append("files", x, x.name); fd.append("mt", String(x.lastModified || 0)); });
      const j = await api("/api/join/" + encodeURIComponent(joinTok) + "/upload", fd, { busy: `Загружаю ${Math.min(i + 4, files.length)}/${files.length}…`, noState: true, quiet: true });
      if (j) { joinData = j; added += j.added || 0; failed = failed.concat(j.failed || []); }
    }
    render(); toast(added ? `Добавлено фото: ${added}` : (failed[0] || "Не удалось добавить фото"));
  };
}
document.addEventListener("click", async e => {
  const x = e.target.closest("[data-jdel]"); if (!x) return;
  e.preventDefault(); e.stopPropagation();
  const j = await api("/api/join/" + encodeURIComponent(joinTok) + "/delete", { id: x.dataset.jdel }, { noState: true });
  if (j) { joinData = j; render(); }
}, true);

// ---------------------------------------------------------------- старт + PWA
// Deep links: /p/<проект>, /catalog, /catalog/<услуга>, /orders, /cart, /track/<заказ>, /join/<токен>.
// Если пользователь ещё не вошёл, ссылка запоминается и открывается сразу после входа или гостевого режима.
const DEEP = /^\/(p\/[^/]+|catalog(\/[^/]+)?|orders|cart)\/?$/;
async function routeDeep(path) {
  history.replaceState({}, "", "/");
  let m;
  if ((m = path.match(/^\/p\/([^/]+)/))) {
    if (!(S.projects || []).some(p => p.id === decodeURIComponent(m[1]))) { toast("Проект по ссылке не найден: он принадлежит другому аккаунту или удалён"); go("home"); return true; }
    await openProject(decodeURIComponent(m[1])); return true;
  }
  if ((m = path.match(/^\/catalog\/([^/]+)/))) {
    go("home"); const id = decodeURIComponent(m[1]);
    if (prod(id)) setTimeout(() => svcSheet(id), 300); else toast("Такой услуги нет в каталоге");
    return true;
  }
  if (/^\/catalog/.test(path)) { go("home"); setTimeout(() => { const el = document.querySelector(".svcs"); if (el) el.scrollIntoView({ behavior: "smooth", block: "start" }); }, 300); return true; }
  if (/^\/orders/.test(path)) { await openOrders(); return true; }
  if (/^\/cart/.test(path)) { await openCart(false); return true; }
  return false;
}
async function route() {
  const dl = LS.get("pp_dl");
  if (dl && S && S.me && S.me.role !== "anon" && LS.get("pp_token")) { LS.set("pp_dl", ""); if (await routeDeep(dl)) return true; }
  if (DEEP.test(location.pathname)) { if (LS.get("pp_token")) return routeDeep(location.pathname); }
  const m = location.pathname.match(/^\/track\/([^/]+)/);
  if (m) { await openTrack(decodeURIComponent(m[1]), "home", false); return true; }
  const jm = location.pathname.match(/^\/join\/([^/]+)/);
  if (jm) { await openJoin(decodeURIComponent(jm[1])); return true; }
  return false;
}
window.addEventListener("popstate", async () => { if (!(await route())) { order = null; await refresh(); go("home"); } });

const T0 = performance.now();
function hideSplash() {
  const sp = $("#splash"); if (!sp) return;
  setTimeout(() => { sp.classList.add("out"); setTimeout(() => sp.remove(), 600); }, Math.max(0, 1100 - (performance.now() - T0)));
}
(async function init() {
  const r = await refresh();
  if (!r) { view.innerHTML = `<div class="empty">${catSVG({ mood: "sleep" })}<h3>Нет связи с сервером</h3>Запустите <code>python server.py</code> и обновите страницу.</div>`; return; }
  me = S.me;
  if (LS.get("pp_token") && me.role === "anon") { LS.set("pp_token", ""); await refresh(); me = S.me; }
  if (!LS.get("pp_token")) {
    if (LS.get("pp_seen")) { const j = await api("/api/guest", { client: CLIENT }, { busy: "Открываю гостевой режим…", noState: true, silent: true }); if (j) await startSession(j.token); }
    else if (!/^\/(track|join)\//.test(location.pathname)) { if (DEEP.test(location.pathname)) LS.set("pp_dl", location.pathname); go("login"); startPoll(); return; }
  }
  me = S.me;
  if (LS.get("pp_ctx") && S.photos.some(p => p.id === LS.get("pp_ctx"))) { ctxPhoto = LS.get("pp_ctx"); ctxExplicit = true; }
  await loadOrders(); orders.forEach(o => { seenStage[o.code] = o.stage; });
  startPoll();
  setTimeout(drainUploads, 1500);                              // догружаем фото, оставшиеся в очереди после обрыва или перезапуска
  if (await route()) return;
  go("home");
})().finally(hideSplash);

if ("serviceWorker" in navigator && (location.protocol === "https:" || location.hostname === "localhost" || location.hostname === "127.0.0.1")) {
  navigator.serviceWorker.register("/sw.js").catch(() => {});
}
