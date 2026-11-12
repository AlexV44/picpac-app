/* Единый набор линейных иконок вместо эмодзи (интерфейс выглядит цельно, а не «как у нейросети»).
   Любой эмодзи из словаря EMO в тексте страницы автоматически заменяется на SVG; сторонние эмодзи удаляются из сервера тоже. */
"use strict";
const ICONS = {
  bot: '<rect x="4" y="8" width="16" height="11" rx="4"/><path d="M12 8V5.5M9 13v1.5M15 13v1.5"/><circle cx="12" cy="4.4" r="1"/>',
  search: '<circle cx="11" cy="11" r="6"/><path d="m20 20-4.2-4.2"/>',
  trash: '<path d="M5 7h14M10 7V5h4v2M7 7l1 12h8l1-12M10 11v5M14 11v5"/>',
  target: '<circle cx="12" cy="12" r="7.5"/><circle cx="12" cy="12" r="2.5"/><path d="M12 2.5v3M12 18.5v3M2.5 12h3M18.5 12h3"/>',
  pumpkin: '<path d="M12 8c-5-2.5-9 1.5-8 6s5 6 8 5c3 1 7-.5 8-5s-3-8.5-8-6z"/><path d="M12 8c0-2 .8-3.2 2.2-3.8M9.5 12.5v3M14.5 12.5v3"/>',
  cake: '<path d="M4 20h16v-6a2 2 0 0 0-2-2H6a2 2 0 0 0-2 2zM4 16.2c2 1.4 4-1.4 8 0s6 1.4 8 0M12 12V9M12 5.5c.9.8.9 1.8 0 2.6-.9-.8-.9-1.8 0-2.6z"/>',
  image: '<rect x="3" y="5" width="18" height="14" rx="3"/><circle cx="9" cy="10.5" r="1.8"/><path d="m21 16-5-5-8 8"/>',
  box: '<path d="M4 8l8-4 8 4v8l-8 4-8-4zM4 8l8 4 8-4M12 12v8"/>',
  book: '<path d="M12 6c-2-1.5-5-2-8-1v13c3-1 6-.5 8 1 2-1.5 5-2 8-1V5c-3-1-6-.5-8 1zM12 6v13"/>',
  okc: '<circle cx="12" cy="12" r="8.5"/><path d="m8.5 12.3 2.5 2.5 4.5-5"/>',
  pencil: '<path d="M4 20l1-4L16 5l3 3L8 19zM14 7l3 3"/>',
  file: '<path d="M7 3h7l4 4v14H7zM14 3v4h4M9.5 13h5M9.5 17h5"/>',
  thought: '<path d="M7 17a4 4 0 0 1-.5-8A5 5 0 0 1 16 8a4 4 0 0 1 1.5 8z"/><circle cx="8" cy="20.2" r="1"/>',
  eraser: '<path d="M4 15l8-8 6 6-5 5H8zM9 12l6 6M13 20h7"/>',
  tree: '<path d="M12 3l5 7h-3l4 6H6l4-6H7zM12 16v5"/>',
  plus: '<path d="M12 5v14M5 12h14"/>',
  undo: '<path d="M9 8 4 12.5 9 17"/><path d="M4.5 12.5h9a4.5 4.5 0 0 1 0 9H10"/>',
  gear: '<circle cx="12" cy="12" r="3.2"/><path d="M12 3v2.5M12 18.5V21M3 12h2.5M18.5 12H21M5.6 5.6l1.8 1.8M16.6 16.6l1.8 1.8M18.4 5.6l-1.8 1.8M7.4 16.6l-1.8 1.8"/>',
  phone: '<rect x="7" y="3" width="10" height="18" rx="2.5"/><path d="M11 18h2"/>',
  cart: '<path d="M3 4h2.5l2 11h10l2-8H7"/><circle cx="9.5" cy="19" r="1.4"/><circle cx="16.5" cy="19" r="1.4"/>',
  rotr: '<path d="M19 12a7 7 0 1 1-2.1-5M19 4v4h-4"/>',
  rotl: '<path d="M5 12a7 7 0 1 0 2.1-5M5 4v4h4"/>',
  ban: '<circle cx="12" cy="12" r="8.5"/><path d="m6 6 12 12"/>',
  sliders: '<path d="M4 8h9M17 8h3M4 16h3M11 16h9"/><circle cx="15" cy="8" r="2"/><circle cx="9" cy="16" r="2"/>',
  repeat: '<path d="M5 11V9a3 3 0 0 1 3-3h10l-3-3M19 13v2a3 3 0 0 1-3 3H6l3 3"/>',
  crop: '<path d="M7 3v14h14M3 7h14v14"/>',
  xc: '<circle cx="12" cy="12" r="8.5"/><path d="m9 9 6 6M15 9l-6 6"/>',
  text: '<path d="M5 6h14M12 6v13M9 19h6"/>',
  up: '<path d="M12 19V5M6 11l6-6 6 6"/>',
  down: '<path d="M12 5v14M6 13l6 6 6-6"/>',
  warn: '<path d="M12 4 3 19h18zM12 10v4M12 16.4v.4"/>',
  eye: '<path d="M2.5 12S6 6 12 6s9.5 6 9.5 6-3.5 6-9.5 6-9.5-6-9.5-6z"/><circle cx="12" cy="12" r="2.6"/>',
  hat: '<path d="M3 17h18M6 17c0-6 1-10 6-10s6 4 6 10M6.5 13.5h11"/>',
  contrast: '<circle cx="12" cy="12" r="8.5"/><path d="M12 3.5v17a8.5 8.5 0 0 0 0-17z"/>',
  film: '<rect x="4" y="4" width="16" height="16" rx="2"/><path d="M8 4v16M16 4v16M4 9h4M4 15h4M16 9h4M16 15h4"/>',
  palette: '<path d="M12 4a8 8 0 1 0 0 16c1.5 0 2-1 1.5-2s0-2.5 1.5-2.5H17a3 3 0 0 0 3-3A8 8 0 0 0 12 4z"/><circle cx="8" cy="11" r=".7"/><circle cx="11" cy="8" r=".7"/><circle cx="15" cy="9" r=".7"/>',
  sun: '<circle cx="12" cy="12" r="4"/><path d="M12 3v2M12 19v2M3 12h2M19 12h2M5.6 5.6 7 7M17 17l1.4 1.4M18.4 5.6 17 7M7 17l-1.4 1.4"/>',
  pointer: '<path d="M9 11V5.5a1.5 1.5 0 0 1 3 0V11M12 10.5a1.5 1.5 0 0 1 3 0M15 11.5a1.5 1.5 0 0 1 3 0V16a5 5 0 0 1-5 5h-1a5 5 0 0 1-4-2l-3-4 1-1 3 2"/>',
  shuffle: '<path d="M4 7h3c5 0 5 10 10 10h3M4 17h3c1.5 0 2.7-1.2 3.6-2.6M14 9.2C14.9 8 15.8 7 17 7h3M17 4l3 3-3 3M17 14l3 3-3 3"/>',
  calendar: '<rect x="4" y="5" width="16" height="15" rx="2.5"/><path d="M4 10h16M9 3v4M15 3v4"/>',
  layout: '<rect x="4" y="4" width="7" height="7" rx="1.5"/><rect x="13" y="4" width="7" height="7" rx="1.5"/><rect x="4" y="13" width="16" height="7" rx="1.5"/>',
  clip: '<rect x="6" y="5" width="12" height="16" rx="2"/><path d="M9 5V4h6v1M9 11h6M9 15h6"/>',
  link: '<path d="M10 14a4 4 0 0 0 5.7 0l3-3a4 4 0 0 0-5.7-5.7l-1 1M14 10a4 4 0 0 0-5.7 0l-3 3a4 4 0 0 0 5.7 5.7l1-1"/>',
  chip: '<rect x="6" y="6" width="12" height="12" rx="2"/><rect x="9.5" y="9.5" width="5" height="5" rx="1"/><path d="M9 3v3M15 3v3M9 18v3M15 18v3M3 9h3M3 15h3M18 9h3M18 15h3"/>',
  inbox: '<path d="M4 13l2.5-7h11L20 13v6H4zM4 13h5l1 2h4l1-2h5"/>',
  card: '<rect x="3" y="6" width="18" height="12" rx="2.5"/><path d="M3 10.5h18M7 15h3"/>',
  printer: '<path d="M7 9V4h10v5M7 17H5V9h14v8h-2M7 14h10v6H7z"/>',
  truck: '<path d="M3 6h11v10H3zM14 9h4l3 3v4h-7"/><circle cx="7" cy="17.5" r="1.8"/><circle cx="17" cy="17.5" r="1.8"/>',
  flag: '<path d="M6 21V4M6 5h11l-2 4 2 4H6"/>',
  ring: '<circle cx="12" cy="15" r="5"/><path d="M9.5 6.5 12 3l2.5 3.5L12 8z"/>',
  plane: '<path d="M21 4 3 11l7 2.5L12.5 21l3-6L21 4zM10 13.5 21 4"/>',
  square: '<rect x="5" y="5" width="14" height="14" rx="2"/>',
  user: '<circle cx="12" cy="8.5" r="3.5"/><path d="M5 20c.8-4 3.5-6 7-6s6.2 2 7 6"/>',
  lock: '<rect x="5" y="11" width="14" height="9" rx="2.5"/><path d="M8 11V8a4 4 0 0 1 8 0v3"/>',
  bell: '<path d="M6 17V11a6 6 0 0 1 12 0v6l1.5 2h-15zM10 21h4"/>',
  chat: '<path d="M4 5h16v11H11l-5 4v-4H4z"/><path d="M8 9.5h8M8 12.5h5"/>',
  shield: '<path d="M12 3l7 3v5c0 5-3 8.5-7 10-4-1.5-7-5-7-10V6z"/>',
  list: '<path d="M8 6h12M8 12h12M8 18h12"/><circle cx="4.5" cy="6" r="1"/><circle cx="4.5" cy="12" r="1"/><circle cx="4.5" cy="18" r="1"/>',
  tag: '<path d="M3 12V4h8l10 10-8 8z"/><circle cx="7.5" cy="8.5" r="1.3"/>',
  gift: '<rect x="3.5" y="9" width="17" height="11" rx="2"/><path d="M3.5 13h17M12 9v11M12 9c-1-3.5-4.5-4-4.5-1.8S10 9 12 9zm0 0c1-3.5 4.5-4 4.5-1.8S14 9 12 9z"/>',
  bolt: '<path d="M13 3 5 13.5h6L10 21l8-10.5h-6z"/>',
  chart: '<path d="M4 20V9M10 20V4M16 20v-8M22 20H2"/>',
  send: '<path d="M4 12 20 4l-4 16-4-6z"/><path d="m12 14 8-10"/>',
};
const EMO = {
  "🤖": "bot", "⚡": "bolt", "🎁": "gift", "🔍": "search", "🔎": "search", "🗑": "trash", "🎯": "target", "🎃": "pumpkin", "🎂": "cake", "🐱": "image", "🖼": "image",
  "📦": "box", "📥": "inbox", "📖": "book", "📚": "book", "✅": "okc", "✏": "pencil", "📄": "file", "💭": "thought", "🧹": "eraser",
  "🎄": "tree", "➕": "plus", "↩": "undo", "⚙": "gear", "📲": "phone", "🛒": "cart", "↻": "rotr", "🔄": "rotr", "↺": "rotl",
  "🚫": "ban", "⚖": "sliders", "🔁": "repeat", "✂": "crop", "✖": "xc", "🔤": "text", "⬆": "up", "⬇": "down", "⚠": "warn",
  "👀": "eye", "🎩": "hat", "🖤": "contrast", "🎞": "film", "🎨": "palette", "☀": "sun", "👆": "pointer", "🔀": "shuffle",
  "📅": "calendar", "🧩": "layout", "📋": "clip", "🔗": "link", "🧠": "chip", "💳": "card", "🖨": "printer", "🚚": "truck",
  "🎉": "flag", "💬": "chat", "🛡": "shield", "🔔": "bell", "💍": "ring", "✈": "plane", "◻": "square", "✨": "",
};
const EMO_RX = new RegExp("(" + Object.keys(EMO).join("|") + ")️?", "gu");

function icon(name, cls = "") {
  return `<svg class="ei ${cls}" viewBox="0 0 24 24" aria-hidden="true">${ICONS[name] || ""}</svg>`;
}

function emojify(root) {
  if (!root || !root.nodeType) return;
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, {
    acceptNode(n) {
      const p = n.parentNode;
      if (!p || /^(SCRIPT|STYLE|TEXTAREA|TITLE)$/.test(p.nodeName) || p.closest && p.closest("svg")) return NodeFilter.FILTER_REJECT;
      return EMO_RX.test(n.nodeValue) ? (EMO_RX.lastIndex = 0, NodeFilter.FILTER_ACCEPT) : NodeFilter.FILTER_REJECT;
    },
  });
  const nodes = [];
  while (walker.nextNode()) nodes.push(walker.currentNode);
  for (const n of nodes) {
    const frag = document.createDocumentFragment();
    let last = 0, m;
    EMO_RX.lastIndex = 0;
    const t = n.nodeValue;
    while ((m = EMO_RX.exec(t))) {
      frag.appendChild(document.createTextNode(t.slice(last, m.index)));
      const nm = EMO[m[1]];
      if (nm) { const w = document.createElement("span"); w.innerHTML = icon(nm); frag.appendChild(w.firstChild); }
      last = m.index + m[0].length;
    }
    frag.appendChild(document.createTextNode(t.slice(last).replace(/^ (?=$)/, "")));
    n.parentNode.replaceChild(frag, n);
  }
}

let _emoQueued = false;
new MutationObserver(() => {
  if (_emoQueued) return;
  _emoQueued = true;
  queueMicrotask(() => { _emoQueued = false; emojify(document.body); });
}).observe(document.body, { childList: true, subtree: true, characterData: true });
document.addEventListener("DOMContentLoaded", () => emojify(document.body));
