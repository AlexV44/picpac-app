# -*- coding: utf-8 -*-
"""Библиотека для рисования иллюстраций picPac (векторные сцены -> SVG -> JPEG). Всё нарисовано кодом, права чистые."""
import math
import random

CREAM, CORAL, GREEN, GOLD = "#fbf3ea", "#e2575a", "#2f4a3e", "#c9a35a"


def scenes_defs():
    """Мини-«фотографии» (viewBox 200x150), сцены без лиц и узнаваемых людей."""
    d = []

    def sym(i, body):
        d.append(f'<symbol id="sc{i}" viewBox="0 0 200 150" preserveAspectRatio="xMidYMid slice">{body}</symbol>')

    # 1 закат над холмами
    sym(1, '''<defs><linearGradient id="g1" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#c65b8a"/><stop offset=".55" stop-color="#ff9a8b"/><stop offset="1" stop-color="#ffd9a8"/></linearGradient>
      <radialGradient id="g1s"><stop offset="0" stop-color="#fff6d8"/><stop offset="1" stop-color="#fff6d8" stop-opacity="0"/></radialGradient></defs>
      <rect width="200" height="150" fill="url(#g1)"/><circle cx="130" cy="82" r="46" fill="url(#g1s)"/><circle cx="130" cy="84" r="15" fill="#fff3d0"/>
      <path d="M0 108c30-18 60-14 90-4s70 6 110-10v56H0Z" fill="#8a4a78"/><path d="M0 124c40-14 70-8 110 0s60 4 90-6v32H0Z" fill="#5b2c5a"/><path d="M0 140c50-10 100-6 200-4v14H0Z" fill="#3a1f48"/>
      <path d="M40 40q4-5 8 0q4-5 8 0M70 28q3-4 6 0q3-4 6 0" stroke="#4a2350" stroke-width="1.4" fill="none"/>''')
    # 2 пляж и парусник
    sym(2, '''<defs><linearGradient id="g2" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#a8dcf2"/><stop offset="1" stop-color="#f6efe0"/></linearGradient>
      <linearGradient id="g2s" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#2f8fb0"/><stop offset="1" stop-color="#8fd0dc"/></linearGradient></defs>
      <rect width="200" height="150" fill="url(#g2)"/><circle cx="36" cy="30" r="14" fill="#fff7d6"/><rect y="70" width="200" height="46" fill="url(#g2s)"/>
      <path d="M0 116c40-8 80 6 120-2s60-4 80 0v36H0Z" fill="#f0d9a8"/><path d="M0 132c50-6 100 6 200-2v20H0Z" fill="#e6c88e"/>
      <path d="M0 96h40M60 104h60M130 92h50" stroke="#fff" stroke-opacity=".55" stroke-width="1.6" stroke-linecap="round"/>
      <path d="M118 90V58l24 32Z" fill="#fff"/><path d="M114 90V64L96 90Z" fill="#fde7e0"/><path d="M92 92h54l-8 8h-38Z" fill="#c9503f"/>''')
    # 3 горы и озеро
    sym(3, '''<defs><linearGradient id="g3" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#274a78"/><stop offset="1" stop-color="#a6c6e6"/></linearGradient>
      <linearGradient id="g3l" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#7fa6cc"/><stop offset="1" stop-color="#3b6791"/></linearGradient></defs>
      <rect width="200" height="150" fill="url(#g3)"/><path d="M-10 100 50 36l30 38 24-26 64 60v42H-10Z" fill="#5b7ea3"/><path d="M50 36l-14 22 10-4 6 10 8-8 6 6-8-18Z" fill="#f2f6fb"/>
      <path d="M104 48l-12 18 8-3 6 8 8-9 6 6Z" fill="#f2f6fb"/><rect y="100" width="200" height="50" fill="url(#g3l)"/>
      <path d="M0 150V118l8-18 8 18 8-22 8 24 8-20 8 18v32Z" fill="#1f4234"/><path d="M150 150V120l8-20 8 20 8-24 8 24 10-18 8 18v30Z" fill="#1a382c"/>''')
    # 4 портрет-силуэт (без лица)
    sym(4, '''<defs><linearGradient id="g4" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#f8d4b8"/><stop offset="1" stop-color="#e59a7b"/></linearGradient></defs>
      <rect width="200" height="150" fill="url(#g4)"/><circle cx="60" cy="40" r="26" fill="#fff" opacity=".16"/>
      <path d="M40 150c0-34 26-46 60-46s60 12 60 46Z" fill="#2f4a3e"/><rect x="88" y="84" width="24" height="26" rx="10" fill="#dca083"/>
      <ellipse cx="100" cy="64" rx="24" ry="28" fill="#e8b394"/><path d="M74 62c-2-26 16-38 34-34 20 4 22 26 18 40-4-12-10-18-18-20-12 4-26 4-34 14Z" fill="#4a2f26"/>''')
    # 5 воздушные шары
    sym(5, '''<defs><linearGradient id="g5" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#bfe0ff"/><stop offset="1" stop-color="#f7fbff"/></linearGradient></defs>
      <rect width="200" height="150" fill="url(#g5)"/><ellipse cx="40" cy="120" rx="46" ry="12" fill="#fff"/><ellipse cx="150" cy="130" rx="56" ry="14" fill="#fff"/>
      <g stroke="#8a8a92" stroke-width=".9" fill="none"><path d="M70 62C74 90 94 110 100 140M110 50C106 80 100 110 100 140M146 70C130 100 106 118 100 140"/></g>
      <ellipse cx="70" cy="46" rx="20" ry="24" fill="#e2575a"/><ellipse cx="110" cy="34" rx="20" ry="24" fill="#f3b83f"/><ellipse cx="146" cy="54" rx="19" ry="23" fill="#3fa7a3"/>
      <ellipse cx="63" cy="38" rx="5" ry="8" fill="#fff" opacity=".45"/><ellipse cx="103" cy="26" rx="5" ry="8" fill="#fff" opacity=".45"/><ellipse cx="140" cy="46" rx="5" ry="8" fill="#fff" opacity=".45"/>''')
    # 6 осенний парк
    sym(6, '''<defs><linearGradient id="g6" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#f6cf7a"/><stop offset="1" stop-color="#e9954a"/></linearGradient></defs>
      <rect width="200" height="150" fill="url(#g6)"/><path d="M0 112c40-10 90-6 200-12v50H0Z" fill="#b5602d"/><path d="M70 150c10-26 24-38 36-48 10 12 22 24 40 48Z" fill="#f0c98f"/>
      <g fill="#8a4a2a"><rect x="34" y="70" width="6" height="44"/><rect x="150" y="60" width="7" height="52"/></g>
      <circle cx="38" cy="62" r="28" fill="#d9532e"/><circle cx="22" cy="74" r="18" fill="#e8762f"/><circle cx="54" cy="76" r="18" fill="#f0a030"/>
      <circle cx="154" cy="52" r="32" fill="#e8762f"/><circle cx="172" cy="70" r="20" fill="#d9532e"/><circle cx="136" cy="70" r="18" fill="#f0b43a"/>''')
    # 7 цветы
    sym(7, '''<rect width="200" height="150" fill="#fde5e8"/><circle cx="170" cy="20" r="30" fill="#fff" opacity=".5"/>
      <g stroke="#5f9b6f" stroke-width="3" fill="none" stroke-linecap="round"><path d="M50 150C50 120 56 100 60 80M110 150C108 124 104 100 100 60M160 150C158 130 150 110 146 92"/></g>
      <g fill="#6aa97b"><ellipse cx="72" cy="124" rx="14" ry="6" transform="rotate(-30 72 124)"/><ellipse cx="128" cy="130" rx="14" ry="6" transform="rotate(30 128 130)"/></g>
      <g fill="#f08aa8"><circle cx="60" cy="66" r="11"/><circle cx="46" cy="76" r="11"/><circle cx="74" cy="76" r="11"/><circle cx="52" cy="90" r="11"/><circle cx="68" cy="90" r="11"/></g><circle cx="60" cy="79" r="8" fill="#f6c94a"/>
      <g fill="#fff"><circle cx="100" cy="42" r="11"/><circle cx="86" cy="52" r="11"/><circle cx="114" cy="52" r="11"/><circle cx="92" cy="66" r="11"/><circle cx="108" cy="66" r="11"/></g><circle cx="100" cy="55" r="8" fill="#f6b73a"/>
      <g fill="#c88ae8"><circle cx="146" cy="80" r="10"/><circle cx="133" cy="90" r="10"/><circle cx="159" cy="90" r="10"/><circle cx="139" cy="103" r="10"/><circle cx="153" cy="103" r="10"/></g><circle cx="146" cy="93" r="7" fill="#f6d34a"/>''')
    # 8 вечерний город
    sym(8, '''<defs><linearGradient id="g8" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#1a1f4a"/><stop offset="1" stop-color="#7a4f9a"/></linearGradient></defs>
      <rect width="200" height="150" fill="url(#g8)"/><circle cx="150" cy="32" r="14" fill="#fff6d8"/><circle cx="144" cy="28" r="12" fill="#2c2a62" opacity=".35"/>
      <g fill="#14163a"><rect x="6" y="70" width="34" height="80"/><rect x="44" y="52" width="30" height="98"/><rect x="78" y="82" width="36" height="68"/><rect x="118" y="60" width="30" height="90"/><rect x="152" y="86" width="42" height="64"/></g>
      <g fill="#ffd773"><rect x="12" y="80" width="6" height="8"/><rect x="26" y="96" width="6" height="8"/><rect x="50" y="62" width="6" height="8"/><rect x="62" y="82" width="6" height="8"/><rect x="50" y="110" width="6" height="8"/>
      <rect x="86" y="94" width="6" height="8"/><rect x="100" y="112" width="6" height="8"/><rect x="124" y="72" width="6" height="8"/><rect x="136" y="96" width="6" height="8"/><rect x="160" y="98" width="6" height="8"/><rect x="176" y="116" width="6" height="8"/></g>''')
    return "".join(d)


def photo(i, x, y, w, h, r=4, extra=""):
    """Фото-сцена в прямоугольнике с мягкими углами и тонкой внутренней рамкой."""
    cid = f"c{random.randint(0, 10**9)}"
    return (f'<g {extra}><clipPath id="{cid}"><rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{r}"/></clipPath>'
            f'<use href="#sc{i}" x="{x}" y="{y}" width="{w}" height="{h}" clip-path="url(#{cid})"/>'
            f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{r}" fill="none" stroke="#000" stroke-opacity=".08"/></g>')


def polaroid(i, x, y, w, rot, tape=True):
    h = w * 1.2
    pad = w * 0.07
    t = (f'<rect x="{x + w * .32}" y="{y - w * .06}" width="{w * .36}" height="{w * .14}" fill="#efe2c6" opacity=".78" transform="rotate(-3 {x + w / 2} {y})"/>' if tape else "")
    return (f'<g transform="rotate({rot} {x + w / 2} {y + h / 2})" filter="url(#sh)"><rect x="{x}" y="{y}" width="{w}" height="{h}" rx="3" fill="#fffdf9"/>'
            f'{photo(i, x + pad, y + pad, w - 2 * pad, w - 2 * pad, 2)}{t}</g>')


def leaf_branch(x, y, length, angle, color, n=9, size=34, seed=1, blur=0):
    rnd = random.Random(seed)
    parts = []
    ex, ey = math.cos(math.radians(angle)) * length, math.sin(math.radians(angle)) * length
    parts.append(f'<path d="M{x} {y}q{ex * .5} {ey * .1} {ex} {ey}" stroke="#5c6f55" stroke-width="3" fill="none" stroke-linecap="round"/>')
    for k in range(n):
        t = (k + 1) / (n + 1)
        px, py = x + ex * t, y + ey * t
        for side in (-1, 1):
            a = angle + side * (55 + rnd.uniform(-12, 12))
            s = size * (1.1 - t * .55) * rnd.uniform(.85, 1.1)
            shade = rnd.choice([color, color, "#9db8a2", "#6f8f7c"])
            parts.append(f'<ellipse cx="{px + math.cos(math.radians(a)) * s * .55:.1f}" cy="{py + math.sin(math.radians(a)) * s * .55:.1f}" rx="{s * .55:.1f}" ry="{s * .26:.1f}" '
                         f'transform="rotate({a:.0f} {px + math.cos(math.radians(a)) * s * .55:.1f} {py + math.sin(math.radians(a)) * s * .55:.1f})" fill="{shade}" opacity=".95"/>')
    f = f' filter="url(#bl{blur})"' if blur else ""
    return f"<g{f}>" + "".join(parts) + "</g>"


def common_defs():
    return ('<filter id="sh" x="-20%" y="-20%" width="150%" height="150%"><feGaussianBlur in="SourceAlpha" stdDeviation="9"/><feOffset dy="10"/><feComponentTransfer><feFuncA type="linear" slope=".30"/></feComponentTransfer>'
            '<feMerge><feMergeNode/><feMergeNode in="SourceGraphic"/></feMerge></filter>'
            '<filter id="sh2" x="-30%" y="-30%" width="170%" height="170%"><feGaussianBlur in="SourceAlpha" stdDeviation="22"/><feOffset dy="26"/><feComponentTransfer><feFuncA type="linear" slope=".38"/></feComponentTransfer>'
            '<feMerge><feMergeNode/><feMergeNode in="SourceGraphic"/></feMerge></filter>'
            '<filter id="bl3"><feGaussianBlur stdDeviation="3"/></filter><filter id="bl6"><feGaussianBlur stdDeviation="6"/></filter><filter id="bl14"><feGaussianBlur stdDeviation="14"/></filter>'
            '<pattern id="linen" width="6" height="6" patternUnits="userSpaceOnUse"><rect width="6" height="6" fill="none"/><path d="M0 1h6M0 4h6" stroke="#fff" stroke-opacity=".06" stroke-width="1"/><path d="M1 0v6M4 0v6" stroke="#000" stroke-opacity=".06" stroke-width="1"/></pattern>'
            f'{scenes_defs()}')


def hardcover(x, y, w, h, color, rot, label="camera", spine="#00000030"):
    """Закрытый альбом с тканевой обложкой, золотым тиснением и закладкой."""
    cx, cy = x + w / 2, y + h / 2
    icon = ""
    if label == "camera":
        icon = (f'<g transform="translate({cx + 10} {cy - 10})" fill="none" stroke="{GOLD}" stroke-width="5" stroke-linecap="round" stroke-linejoin="round">'
                '<rect x="-46" y="-30" width="92" height="64" rx="12"/><circle cx="0" cy="2" r="19"/><path d="M-22-30l6-12h32l6 12"/><circle cx="31" cy="-16" r="2.5" fill="#c9a35a"/></g>')
    return (f'<g transform="rotate({rot} {cx} {cy})" filter="url(#sh2)"><rect x="{x}" y="{y}" width="{w}" height="{h}" rx="14" fill="{color}"/>'
            f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="14" fill="url(#linen)"/><rect x="{x}" y="{y}" width="{w * .09}" height="{h}" rx="14" fill="{spine}"/>'
            f'<rect x="{x + w * .13}" y="{y + 22}" width="{w * .82}" height="{h - 44}" rx="8" fill="none" stroke="{GOLD}" stroke-width="2.4" opacity=".9"/>'
            f'<rect x="{x + w * .17}" y="{y + 30}" width="{w * .74}" height="{h - 60}" rx="5" fill="none" stroke="{GOLD}" stroke-width="1" opacity=".7"/>{icon}'
            f'<path d="M{x + w * .72} {y + h - 2}v46l9-8 9 8v-46Z" fill="{CORAL}"/></g>')


def open_book(x, y, pw, ph, rot, pages):
    """Открытая фотокнига: две страницы с фото. pages = (left_items, right_items), элементы (scene, rx, ry, rw, rh) в долях страницы."""
    cx, cy = x + pw, y + ph / 2
    s = [f'<g transform="rotate({rot} {cx} {cy})" filter="url(#sh2)">']
    s.append(f'<rect x="{x - 14}" y="{y - 8}" width="{pw * 2 + 28}" height="{ph + 22}" rx="16" fill="#d84a4f"/><rect x="{x - 14}" y="{y - 8}" width="{pw * 2 + 28}" height="{ph + 22}" rx="16" fill="url(#linen)"/>')
    s.append(f'<path d="M{x} {y + ph}h{pw * 2}" stroke="#e8dccd" stroke-width="7"/><path d="M{x + 4} {y + ph + 4}h{pw * 2 - 8}" stroke="#d9c9b6" stroke-width="3"/>')
    s.append('<defs><linearGradient id="pgl" x1="0" y1="0" x2="1" y2="0"><stop offset="0" stop-color="#fffaf3"/><stop offset=".86" stop-color="#fffaf3"/><stop offset="1" stop-color="#e7d9c6"/></linearGradient>'
             '<linearGradient id="pgr" x1="0" y1="0" x2="1" y2="0"><stop offset="0" stop-color="#e7d9c6"/><stop offset=".14" stop-color="#fffaf3"/><stop offset="1" stop-color="#fffaf3"/></linearGradient></defs>')
    s.append(f'<rect x="{x}" y="{y}" width="{pw}" height="{ph}" rx="4" fill="url(#pgl)"/><rect x="{x + pw}" y="{y}" width="{pw}" height="{ph}" rx="4" fill="url(#pgr)"/>')
    for side, items in enumerate(pages):
        ox = x + side * pw
        for it in items:
            if it[0] == "line":
                _, rx, ry, rw = it
                s.append(f'<rect x="{ox + rx * pw}" y="{y + ry * ph}" width="{rw * pw}" height="9" rx="4.5" fill="#d8ccbd"/>')
            else:
                sc, rx, ry, rw, rh = it
                s.append(photo(sc, ox + rx * pw, y + ry * ph, rw * pw, rh * ph, 3))
    s.append('</g>')
    return "".join(s)
