/* Талисманы приложения: рыжий кот в синей бандане и белый робот с чёрным визором и книгой.
   Рисуются кодом (SVG), по референсу команды. */
"use strict";

let _uid = 0;

/* mood: point (как на референсе: смеётся и показывает вверх) | idle | sleep | happy (= point) */
function catSVG(o = {}) {
  const mood = o.mood === "happy" ? "point" : (o.mood || "point"), cls = o.cls || "", id = "c" + (++_uid);
  const sleep = mood === "sleep", point = mood === "point";
  const eyes = sleep
    ? `<path class="ln dk" d="M42 60q7 6 15 0M83 60q7 6 15 0" fill="none"/>`
    : point
      ? `<path class="ln dk" style="stroke-width:3.6" d="M43 60q7.5-12 15 0M82 60q7.5-12 15 0" fill="none"/>`
      : `<ellipse class="blink" cx="50" cy="58" rx="5.4" ry="7" fill="#3b1c0a"/><ellipse class="blink" cx="90" cy="58" rx="5.4" ry="7" fill="#3b1c0a"/>
         <circle cx="52" cy="55.5" r="2" fill="#fff"/><circle cx="92" cy="55.5" r="2" fill="#fff"/>`;
  const mouth = sleep
    ? `<path class="ln dk" d="M63 83q7 4 14 0" fill="none"/>`
    : `<path d="M58 79Q70 100 82 79Z" fill="#7a1f1f" class="ol"/><path d="M62.5 90.5Q70 98 77.5 90.5Q70 86 62.5 90.5Z" fill="#ff8da1" style="stroke:none"/>`;
  const arm = point
    ? `<g class="pt"><path d="M94 112Q112 98 118 70" fill="none" style="stroke:#8a3b12;stroke-width:18;stroke-linecap:round"/>
         <path d="M94 112Q112 98 118 70" fill="none" style="stroke:#ef7d22;stroke-width:14;stroke-linecap:round"/>
         <ellipse class="ol" cx="119" cy="62" rx="9" ry="9.5" fill="#fbb766"/>
         <path class="ol" d="M117 56Q116 40 120 38Q124 40 123 56Z" fill="#fbb766"/></g>`
    : `<path d="M96 114Q108 124 104 140" fill="none" style="stroke:#8a3b12;stroke-width:17;stroke-linecap:round"/>
       <path d="M96 114Q108 124 104 140" fill="none" style="stroke:#ef7d22;stroke-width:13;stroke-linecap:round"/>
       <ellipse class="ol" cx="104" cy="141" rx="8" ry="6" fill="#fbb766"/>`;
  const z = sleep ? `<text class="zz" x="112" y="30">z</text><text class="zz" x="122" y="14" style="animation-delay:.8s">Z</text>` : "";
  const vb = o.face ? "18 0 104 104" : "0 0 140 150";
  return `<svg class="mascot cat ${o.face ? "face " : ""}${cls}" viewBox="${vb}" role="img" aria-label="Кот">
    <defs><radialGradient id="${id}f" cx=".45" cy=".35" r=".8"><stop offset="0" stop-color="#ffab55"/><stop offset="1" stop-color="#ee7f26"/></radialGradient></defs>
    <path d="M44 134q-30 4-26-22" fill="none" style="stroke:#8a3b12;stroke-width:14;stroke-linecap:round" class="tail"/>
    <path d="M44 134q-30 4-26-22" fill="none" style="stroke:#f08a30;stroke-width:9.5;stroke-linecap:round" class="tail"/>
    <path class="ol" d="M34 150Q30 108 70 104Q110 108 106 150Z" fill="#ef7d22"/>
    <path d="M40 130Q70 138 100 130" fill="none" style="stroke:#d56a15;stroke-width:2.2;stroke-linecap:round"/>
    <path class="ol" d="M36 120Q26 134 34 148" fill="none"/>
    ${arm}
    <path class="ol" d="M44 96Q70 112 96 96L70 134Z" fill="#2d6be4"/>
    <path d="M52 100Q70 110 88 100L70 128Z" fill="#4c86f0" style="stroke:none"/>
    <circle cx="62" cy="106" r="1.8" fill="#fff"/><circle cx="78" cy="106" r="1.8" fill="#fff"/><circle cx="70" cy="116" r="1.8" fill="#fff"/><circle cx="66" cy="111" r="1.4" fill="#fff"/><circle cx="74" cy="111" r="1.4" fill="#fff"/>
    <path class="ol" d="M42 97Q70 110 98 97" fill="none" style="stroke-width:9"/><path d="M42 97Q70 110 98 97" fill="none" style="stroke:#2d6be4;stroke-width:6;stroke-linecap:round"/>
    <circle class="ol" cx="70" cy="104" r="7" fill="#4c86f0"/>
    <path class="ol" d="M28 52L24 8Q46 14 60 30Z" fill="#f7923a"/><path class="ol" d="M112 52L116 8Q94 14 80 30Z" fill="#f7923a"/>
    <path d="M32 44L30 20Q42 25 51 34Z" fill="#ffb3b3" style="stroke:none"/><path d="M108 44L110 20Q98 25 89 34Z" fill="#ffb3b3" style="stroke:none"/>
    <ellipse class="ol" cx="70" cy="62" rx="46" ry="40" fill="url(#${id}f)"/>
    <path class="ln" style="stroke:#c2651a" d="M70 24v11M58 26l2.5 9.5M82 26l-2.5 9.5" fill="none"/>
    <ellipse cx="47" cy="78" rx="17" ry="13" fill="#fff3e3"/><ellipse cx="93" cy="78" rx="17" ry="13" fill="#fff3e3"/><ellipse cx="70" cy="80" rx="20" ry="15" fill="#fff8ee"/>
    <circle cx="40" cy="70" r="5.5" fill="#ff8da1" opacity=".5"/><circle cx="100" cy="70" r="5.5" fill="#ff8da1" opacity=".5"/>
    ${eyes}
    <path class="ol" d="M64 69h12l-6 7z" fill="#ff7f95" style="stroke-width:2"/>
    ${mouth}
    <path class="ln" style="stroke-width:1.7" d="M36 76l-18-4M36 82l-18 3M104 76l18-4M104 82l18 3" fill="none"/>
    ${z}</svg>`;
}

/* mood: hold (с книгой, как на референсе) | wave | think | happy */
function robotSVG(o = {}) {
  const mood = o.mood === "idle" ? "hold" : (o.mood || "hold"), cls = o.cls || "", id = "r" + (++_uid);
  const think = mood === "think", wave = mood === "wave";
  const eyes = think
    ? `<circle class="glow" cx="56" cy="50" r="4.2" fill="#46c2ff"/><circle class="glow" cx="70" cy="50" r="4.2" fill="#46c2ff" style="animation-delay:.2s"/><circle class="glow" cx="84" cy="50" r="4.2" fill="#46c2ff" style="animation-delay:.4s"/>`
    : `<g class="glow"><path d="M49 52Q56 41 63 52M77 52Q84 41 91 52" fill="none" style="stroke:#46c2ff;stroke-width:9;stroke-linecap:round;opacity:.28"/>
         <path d="M49 52Q56 41 63 52M77 52Q84 41 91 52" fill="none" style="stroke:#8be0ff;stroke-width:4.2;stroke-linecap:round"/>
         <path d="M63 61Q70 67 77 61" fill="none" style="stroke:#8be0ff;stroke-width:3;stroke-linecap:round"/></g>`;
  const book = `<g transform="rotate(-6 80 126)"><rect class="ol" x="52" y="106" width="52" height="40" rx="4" fill="#f4b53a"/>
      <rect x="57" y="111" width="42" height="26" rx="2" fill="#bfe3f6" style="stroke:none"/>
      <path d="M57 137l12-15 8 9 7-8 15 14Z" fill="#5a7fb5" style="stroke:none"/><path d="M57 137l9-9 6 7z" fill="#e9893a" style="stroke:none"/>
      <circle cx="90" cy="118" r="3" fill="#fff6c4" style="stroke:none"/></g>`;
  const armL = `<path d="M48 104Q42 118 56 126" fill="none" style="stroke:#9aa6ba;stroke-width:15;stroke-linecap:round"/><path d="M48 104Q42 118 56 126" fill="none" style="stroke:#f4f7fb;stroke-width:11.5;stroke-linecap:round"/>
       <circle cx="57" cy="127" r="8.5" fill="#1a1d26"/>`;
  const armR = wave
    ? `<g class="wave2"><path d="M94 104Q112 100 114 78" fill="none" style="stroke:#9aa6ba;stroke-width:15;stroke-linecap:round"/><path d="M94 104Q112 100 114 78" fill="none" style="stroke:#f4f7fb;stroke-width:11.5;stroke-linecap:round"/>
         <circle cx="114" cy="73" r="8.5" fill="#1a1d26"/></g>`
    : `<path d="M94 104Q100 118 88 126" fill="none" style="stroke:#9aa6ba;stroke-width:15;stroke-linecap:round"/><path d="M94 104Q100 118 88 126" fill="none" style="stroke:#f4f7fb;stroke-width:11.5;stroke-linecap:round"/>
       <circle cx="87" cy="127" r="8.5" fill="#1a1d26"/>`;
  const vb = o.face ? "22 4 96 92" : "0 0 140 150";
  return `<svg class="mascot robot ${o.face ? "face " : ""}${cls}" viewBox="${vb}" role="img" aria-label="Робот-помощник">
    <defs><radialGradient id="${id}h" cx=".38" cy=".3" r=".85"><stop offset="0" stop-color="#ffffff"/><stop offset=".7" stop-color="#e9eef6"/><stop offset="1" stop-color="#c9d3e3"/></radialGradient>
      <linearGradient id="${id}b" x1="0" x2="1"><stop offset="0" stop-color="#fff"/><stop offset="1" stop-color="#d7deea"/></linearGradient></defs>
    <g class="${think ? "thinking" : ""}">
    <path class="ol" style="stroke:#9aa6ba;stroke-width:2.4" d="M42 150Q40 100 70 94Q100 100 98 150Z" fill="url(#${id}b)"/>
    <rect x="60" y="86" width="20" height="12" rx="5" fill="#1a1d26"/>
    <circle cx="70" cy="121" r="5" fill="#1a1d26"/><circle cx="70" cy="121" r="2.4" fill="#46c2ff" class="glow"/>
    ${armL}
    ${wave ? "" : book}
    ${armR}
    <ellipse cx="26" cy="52" rx="8" ry="14" fill="#dfe6f1" style="stroke:#9aa6ba;stroke-width:2.4"/><ellipse cx="26" cy="52" rx="4" ry="8.5" fill="#1a1d26"/>
    <ellipse cx="114" cy="52" rx="8" ry="14" fill="#dfe6f1" style="stroke:#9aa6ba;stroke-width:2.4"/><ellipse cx="114" cy="52" rx="4" ry="8.5" fill="#1a1d26"/>
    <circle cx="70" cy="50" r="42" fill="url(#${id}h)" style="stroke:#9aa6ba;stroke-width:2.6"/>
    <ellipse cx="70" cy="52" rx="33" ry="27" fill="#161922"/>
    <path d="M44 40Q52 28 70 27" fill="none" style="stroke:#fff;stroke-width:3;stroke-linecap:round;opacity:.22"/>
    ${eyes}
    </g></svg>`;
}
