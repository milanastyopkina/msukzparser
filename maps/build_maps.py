#!/usr/bin/env python3
"""Рисует схемы этажей (SVG -> PNG) и картинки «где кабинет» для бота.

Запуск:  python maps/build_maps.py            — перерисовать все этажи
Результат: maps/png/<этаж>/<кабинет>.png (кабинет подсвечен метками) и maps/png/<этаж>/_plan.png (весь этаж),
           maps/rooms.json (кабинет -> этаж и подсказка, как найти) — для бота.
Нужен rsvg-convert (apt install librsvg2-bin) и шрифт Inter.
"""
from __future__ import annotations

import json
import math
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parent
R2 = math.sqrt(2)

# ---------------------------------------------------------------- геометрия 7 этажа
# Здание — «галочка»: два крыла под прямым углом. Удобно описывать в осях a (вдоль верхнего крыла, на северо-восток)
# и b (вдоль нижнего крыла, на юго-восток); S() переводит их в координаты чертежа.


def S(a: float, b: float) -> tuple[float, float]:
    return ((a + b) / R2, (b - a) / R2)


def rect(a0, a1, b0, b1):
    return [S(a0, b0), S(a1, b0), S(a1, b1), S(a0, b1)]


def poly_ab(pts):
    return [S(a, b) for a, b in pts]


B0, B1 = 186, 250          # ширина верхнего крыла (по b)
A0, A1 = -130, -68         # ширина нижнего крыла (по a)

def clip_half(poly, keep):
    """Отсекает выпуклый многоугольник полуплоскостью: keep(p) -> число, >=0 — оставляем (Сазерленд — Ходжман)."""
    out = []
    for i, p in enumerate(poly):
        q = poly[(i + 1) % len(poly)]
        fp, fq = keep(p), keep(q)
        if fp >= 0:
            out.append(p)
        if (fp >= 0) != (fq >= 0):
            t = fp / (fp - fq)
            out.append((p[0] + (q[0] - p[0]) * t, p[1] + (q[1] - p[1]) * t))
    return out


HEX = [(52, 160), (88, 168), (113, 203), (119, 234), (78, 272), (22, 253), (9, 199)]
CORE_AB = [(-68, 250), (30, 250), (30, 348), (-68, 348)]


def new_floor(title: str) -> dict:
    """Общий контур здания (одинаков на всех этажах): холл, большой блок слева, туалеты, выходы."""
    return {"title": title, "rooms": {}, "core": poly_ab(CORE_AB), "hex": HEX, "narrow": set(),
            "lifts": [], "stairs": S(-19, 299),
            "wc": [((100, 186), "Ж"), ((88, 238), "М")], "exits": [S(182, 218), S(-99, 497)]}


def add(fl, rid, label, zone, poly, ab=None, kind=None):
    """Добавляет кабинет. ab=(ширина по a, ширина по b) — чтобы узнать, узкий ли он."""
    fl["rooms"][rid] = {"poly": poly, "label": label, "zone": zone, **({"kind": kind} if kind else {})}
    if ab and min(ab) < 27.5:
        fl["narrow"].add(rid)


def arm_a(fl, dividers: dict):
    """Верхнее крыло: {номер: (a0, a1)}."""
    for rid, (a0, a1) in dividers.items():
        add(fl, rid, rid, "верхнее крыло", rect(a0, a1, B0, B1), (a1 - a0, B1 - B0))


def arm_b(fl, dividers: dict):
    """Нижнее крыло: {номер: (b0, b1)}."""
    for rid, (b0, b1) in dividers.items():
        add(fl, rid, rid, "нижнее крыло", rect(A0, A1, b0, b1), (A1 - A0, b1 - b0))


EAST_CHAMFER = [(30, 0), (77, 0), (77, 350), (41, 392), (40, 348), (30, 348)]   # «угловая» комната восточной части


def east_top(fl, rid, b0, b1, label=None):
    add(fl, rid, label or rid, "восточная часть", rect(30, 77, b0, b1), (47, b1 - b0))


def east_corner(fl, rid, b0, label=None):
    pts_ = [(30, b0), (77, b0), (77, 350), (41, 392), (40, 348), (30, 348)]
    add(fl, rid, label or rid, "восточная часть", poly_ab(pts_), (47, 350 - b0))


def east_bottom(fl, rid, a0, a1, label=None):
    add(fl, rid, label or rid, "восточная часть", rect(a0, a1, 348, 391), (a1 - a0, 43))


def hex_whole(fl, rid, label):
    add(fl, rid, label, "большой блок слева", fl["hex"], kind="hex")


def hex_split(fl, upper, lower):
    cy = sum(p[1] for p in fl["hex"]) / len(fl["hex"])
    up = clip_half(fl["hex"], lambda p: cy - p[1])
    lo = clip_half(fl["hex"], lambda p: p[1] - cy)
    add(fl, upper, upper, "большой блок слева", up, kind="hex")
    add(fl, lower, lower, "большой блок слева", lo, kind="hex")


# ------------------------------------------------------------ 7 этаж
FLOOR7 = new_floor("7 этаж")
arm_a(FLOOR7, {"714": (-68, -12.7), "715": (-12.7, 21), "716": (21, 65.5), "717": (65.5, 93.5), "718": (93.5, 135), "719": (135, 168)})
arm_b(FLOOR7, {"708": (250, 267.5), "707": (267.5, 287), "706": (287, 341), "705": (341, 362), "704": (362, 379.5),
               "703": (379.5, 398.5), "702": (398.5, 454), "701": (454, 480)})
east_top(FLOOR7, "720", 250, 272)
east_top(FLOOR7, "721", 272, 299)
east_corner(FLOOR7, "722-727", 299, "722–727")
east_bottom(FLOOR7, "728", -24, 40)
east_bottom(FLOOR7, "729", -68, -24)
hex_whole(FLOOR7, "710-712", "710–712")
FLOOR7["lifts"] = [S(9, 264), S(-47, 316)]

# ------------------------------------------------------------ 6 этаж
FLOOR6 = new_floor("6 этаж")
arm_a(FLOOR6, {"606": (-68, -2.6), "607": (-2.6, 43.1), "608": (43.1, 96), "609": (96, 168)})
arm_b(FLOOR6, {"604": (250, 318.3), "603": (318.3, 363.4), "602": (363.4, 409.8), "601": (409.8, 480)})
east_top(FLOOR6, "610", 250, 295)
east_top(FLOOR6, "611", 295, 325)
east_corner(FLOOR6, "612", 325)
east_bottom(FLOOR6, "613", -24, 40)
east_bottom(FLOOR6, "614", -68, -24)
hex_whole(FLOOR6, "605", "605")
FLOOR6["lifts"] = [S(9, 264), S(-47, 316)]

# ------------------------------------------------------------ 5 этаж
FLOOR5 = new_floor("5 этаж")
arm_a(FLOOR5, {"509": (-68, -18.9), "510": (-18.9, 12.9), "511": (12.9, 44.1), "512": (44.1, 78.9), "513": (78.9, 116.1), "514": (116.1, 168)})
arm_b(FLOOR5, {"506": (250, 296.5), "505": (296.5, 331), "504": (331, 364.4), "503": (364.4, 396.2), "502": (396.2, 430.2), "501": (430.2, 480)})
add(FLOOR5, "515", "515", "восточная часть", rect(29, 66, 250, 270), (37, 20))
add(FLOOR5, "516", "516", "восточная часть", rect(29, 66, 270, 306), (37, 36))
add(FLOOR5, "517", "517", "восточная часть", poly_ab([(29, 306), (66, 306), (70, 347), (52, 364), (4, 317), (6, 312)]), (60, 50))
add(FLOOR5, "518", "518", "восточная часть", poly_ab([(4, 317), (52, 364), (36, 384), (-6, 384), (-10, 330)]), (50, 50))
add(FLOOR5, "519", "519", "восточная часть", poly_ab([(-44, 345), (-8, 345), (-2, 378), (-44, 378)]), (40, 33))
add(FLOOR5, "520", "520", "восточная часть", poly_ab([(-44, 378), (-2, 378), (-12, 396), (-66, 396), (-66, 378)]), (40, 20))
hex_split(FLOOR5, "508", "507")
FLOOR5["rooms"]["508"]["dx"] = -8; FLOOR5["rooms"]["508"]["dy"] = 0
FLOOR5["rooms"]["507"]["dx"] = -24; FLOOR5["rooms"]["507"]["dy"] = 4
FLOOR5["lifts"] = [S(9, 264)]          # на 5 этаже лифт только один

FLOORS = {"5": FLOOR5, "6": FLOOR6, "7": FLOOR7}

# ---------------------------------------------------------------- вспомогательное


def centroid(poly):
    x = sum(p[0] for p in poly) / len(poly)
    y = sum(p[1] for p in poly) / len(poly)
    return x, y


def pts(poly):
    return " ".join(f"{x:.1f},{y:.1f}" for x, y in poly)


def shrink(poly, d=0.9):
    cx, cy = centroid(poly)
    out = []
    for x, y in poly:
        dx, dy = x - cx, y - cy
        n = math.hypot(dx, dy) or 1
        out.append((x - dx / n * d, y - dy / n * d))
    return out


# палитра: https://coolors.co/1d798a-e3dee1-db5461-c3a46f-3e8914-db8ab0-adf6b1
TEAL, FOG, CORAL, GOLD, GREEN, PINK, MINT = "#1D798A", "#E3DEE1", "#DB5461", "#C3A46F", "#3E8914", "#DB8AB0", "#ADF6B1"
ACCENT = CORAL                # подсветка кабинета
INK = "#3B2E35"               # цифры кабинетов
WC_COL = PINK                 # туалеты
EXIT_COL = GREEN              # запасные выходы
LIFT_COL = TEAL               # лифты
STAIR_COL = GOLD              # лестница
# цвета зон: (заливка, обводка)
ZONE = {"верхнее крыло": ("#DDF9DF", "#7FCF8A"), "нижнее крыло": ("#F6EEDD", GOLD),
        "восточная часть": ("#FAE3EE", PINK), "большой блок слева": ("#D2EAEF", TEAL)}
VIEW = (-22, -52, 352, 520)       # x, y, w, h в единицах чертежа


def svg_floor(fl: dict, highlight: str | None = None, caption: str | None = None) -> str:
    x0, y0, w, h = VIEW
    o = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{x0} {y0} {w} {h}" width="1050" height="{1050 * h / w:.0f}" '
         f'font-family="Inter, DejaVu Sans, sans-serif">',
         '''<defs>
  <linearGradient id="bg" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#FBF9FA"/><stop offset="1" stop-color="#E3DEE1"/></linearGradient>
  <filter id="sh" x="-10%" y="-10%" width="120%" height="120%"><feDropShadow dx="0" dy="2.2" stdDeviation="3" flood-color="#3B2E35" flood-opacity="0.20"/></filter>
  <filter id="glow" x="-40%" y="-40%" width="180%" height="180%"><feDropShadow dx="0" dy="0" stdDeviation="4" flood-color="#DB5461" flood-opacity="0.85"/></filter>
  <filter id="psh" x="-50%" y="-50%" width="200%" height="200%"><feDropShadow dx="0" dy="2" stdDeviation="2" flood-color="#000" flood-opacity="0.35"/></filter>
</defs>''',
         f'<rect x="{x0}" y="{y0}" width="{w}" height="{h}" fill="url(#bg)"/>']

    # шапка
    o.append(f'<text x="{x0 + 22}" y="{y0 + 34}" font-size="23" font-weight="800" fill="{TEAL}">{fl["title"]}</text>')
    sub = caption or "Схема этажа · блок B"
    o.append(f'<text x="{x0 + 22}" y="{y0 + 52}" font-size="10.5" font-weight="600" fill="#8A7A80">{sub}</text>')

    rooms = fl["rooms"]
    # тень всего здания
    o.append('<g filter="url(#sh)">')
    o.append(f'<polygon points="{pts(fl["core"])}" fill="#F1ECEF"/>')
    for r in rooms.values():
        o.append(f'<polygon points="{pts(r["poly"])}" fill="#fff"/>')
    o.append('</g>')

    # холл
    o.append(f'<polygon points="{pts(fl["core"])}" fill="#EAE4E8" stroke="#CFC6CC" stroke-width="0.9" stroke-linejoin="round"/>')
    cx, cy = centroid(fl["core"])
    o.append(f'<text x="{cx - 26:.1f}" y="{cy + 4:.1f}" font-size="7.6" font-weight="700" fill="#A89CA3" text-anchor="middle" letter-spacing="1.4">ХОЛЛ</text>')

    # кабинеты
    for rid, r in rooms.items():
        hl = rid == highlight
        zf, zs = ZONE[r["zone"]]
        fill = ACCENT if hl else zf
        stroke = "#FFFFFF" if hl else zs
        sw = 1.5 if hl else 1.1
        flt = ' filter="url(#glow)"' if hl else ""
        o.append(f'<polygon points="{pts(shrink(r["poly"], 0.5))}" fill="{fill}" stroke="{stroke}" stroke-width="{sw}" '
                 f'stroke-linejoin="round"{flt}/>')
    # подписи
    for rid, r in rooms.items():
        cx, cy = centroid(r["poly"])
        cx += r.get("dx", 0); cy += r.get("dy", 0)
        hl = rid == highlight
        if hl:
            continue
        fs = 11.6 if len(r["label"]) <= 3 else 8.9
        if r.get("kind") == "hex":
            cy -= 7
            fs = 15.5
        if rid in fl["narrow"]:
            fs = 8.8 if "крыло" in r["zone"] else 10.2
        ty = cy + fs * 0.35 + (10 if hl else 0)
        o.append(f'<text x="{cx:.1f}" y="{ty:.1f}" font-size="{fs}" font-weight="800" text-anchor="middle" '
                 f'fill="{"#fff" if hl else INK}">{r["label"]}</text>')
    hx, hy = centroid(fl["hex"])
    if "710-712" in rooms and highlight != "710-712":
        o.append(f'<text x="{hx - 2:.1f}" y="{hy + 11:.1f}" font-size="8.4" font-weight="700" text-anchor="middle" fill="#1D798A">три кабинета</text>')

    # лифты, лестница, WC, выходы
    for lx, ly in fl["lifts"]:
        o.append(f'<g transform="translate({lx:.1f},{ly:.1f})"><rect x="-8" y="-10.5" width="16" height="21" rx="3.4" fill="{LIFT_COL}"/>'
                 f'<path d="M0 -7.2 L4 -2 H-4 Z M0 7.2 L4 2 H-4 Z" fill="#fff"/></g>')
    sx, sy = fl["stairs"]
    o.append(f'<g transform="translate({sx:.1f},{sy:.1f})"><rect x="-10.5" y="-10.5" width="21" height="21" rx="3.6" fill="{STAIR_COL}"/>'
             f'<path d="M-6.4 6 H-2.6 V2 H1.2 V-2 H5 V-6 H6.6 V6 Z" fill="#fff"/></g>')
    for (wx, wy), t in fl["wc"]:
        o.append(f'<g><circle cx="{wx}" cy="{wy}" r="11" fill="{WC_COL}" stroke="#fff" stroke-width="1.6"/>'
                 f'<text x="{wx}" y="{wy + 4.4}" font-size="12" font-weight="800" fill="#5B2443" text-anchor="middle">{t}</text></g>')
    for ex, ey in fl["exits"]:
        o.append(f'<g transform="translate({ex:.1f},{ey:.1f}) rotate(45)"><rect x="-13" y="-5.2" width="26" height="10.4" rx="3" fill="{EXIT_COL}"/>'
                 f'<text x="0" y="2.7" font-size="5.8" font-weight="800" fill="#fff" text-anchor="middle" letter-spacing="0.3">ВЫХОД</text></g>')

    # метка искомого кабинета
    if highlight:
        r = rooms[highlight]
        cx, cy = centroid(r["poly"])
        cx += r.get("dx", 0); cy += r.get("dy", 0)
        if highlight in fl["narrow"]:
            # узкий кабинет: кольца + точка, номер в облачке снаружи здания
            dx, dy = ((-0.7071, 0.7071) if r["zone"] == "нижнее крыло" else (0.7071, -0.7071))
            px, py = cx + dx * 46, cy + dy * 46
            for rad, op in ((24, 0.12), (16, 0.22)):
                o.append(f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="{rad}" fill="{ACCENT}" fill-opacity="{op}"/>')
            o.append(f'<line x1="{cx:.1f}" y1="{cy:.1f}" x2="{px:.1f}" y2="{py:.1f}" stroke="{ACCENT}" stroke-width="2" stroke-linecap="round"/>')
            o.append(f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="5.2" fill="{ACCENT}" stroke="#fff" stroke-width="1.8"/>')
            w = 18 + 8 * len(r["label"])
            o.append(f'<g filter="url(#psh)"><rect x="{px - w / 2:.1f}" y="{py - 11:.1f}" width="{w}" height="22" rx="11" fill="{ACCENT}" stroke="#fff" stroke-width="1.8"/>'
                     f'<text x="{px:.1f}" y="{py + 5:.1f}" font-size="14" font-weight="800" text-anchor="middle" fill="#fff">{r["label"]}</text></g>')
        else:
            for rad, op in ((30, 0.10), (21, 0.17), (13, 0.28)):
                o.append(f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="{rad}" fill="{ACCENT}" fill-opacity="{op}"/>')
            o.append(f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="13" fill="none" stroke="{ACCENT}" stroke-width="1.4" stroke-dasharray="3 2.4"/>')
            o.append(f'<g filter="url(#psh)" transform="translate({cx:.1f},{cy - 3:.1f})">'
                     f'<path d="M0 0 C-9 -12 -12 -17 -12 -23 A12 12 0 1 1 12 -23 C12 -17 9 -12 0 0 Z" fill="{ACCENT}" stroke="#fff" stroke-width="1.6"/>'
                     f'<circle cx="0" cy="-23" r="4.8" fill="#fff"/></g>')
            fs = 13.0 if len(r["label"]) <= 3 else 10.2
            o.append(f'<text x="{cx:.1f}" y="{cy + 14:.1f}" font-size="{fs}" font-weight="800" text-anchor="middle" fill="#fff">{r["label"]}</text>')

    # легенда
    ly = y0 + h - 26
    leg = [(LIFT_COL, "лифт"), (STAIR_COL, "лестница"), (WC_COL, "туалет"), (EXIT_COL, "выход")]
    lx = x0 + 22
    for col, t in leg:
        o.append(f'<rect x="{lx}" y="{ly - 7}" width="9" height="9" rx="2.5" fill="{col}"/>'
                 f'<text x="{lx + 13}" y="{ly + 0.8}" font-size="8" font-weight="700" fill="#5E5057">{t}</text>')
        lx += 22 + len(t) * 4.6
    o.append("</svg>")
    return "\n".join(o)


def render(svg: str, out: Path, width: int = 1050) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".svg")
    tmp.write_text(svg, "utf-8")
    subprocess.run(["rsvg-convert", "-w", str(width), "-o", str(out), str(tmp)], check=True)
    tmp.unlink()


# кабинеты, где пар не бывает: на общей схеме они есть, отдельные карты не рисуем
NO_CLASSES: set[str] = set()   # раньше тут были «кабинеты без пар», но пары там всё же бывают (718) — рисуем все


def main(only: str | None = None) -> None:
    index: dict = {}
    for fid, fl in FLOORS.items():
        out = ROOT / "png" / fid
        render(svg_floor(fl), out / "_plan.png")
        for rid, r in fl["rooms"].items():
            img = f"png/{fid}/{rid}.png"
            if rid in NO_CLASSES:
                img = f"png/{fid}/_plan.png"          # пар там не бывает: показываем общую схему этажа
            else:
                cap = f"Кабинет {r['label']} · {fl['title']} · блок B"
                render(svg_floor(fl, highlight=rid, caption=cap), out / f"{rid}.png")
            nums = rid.split("-") if "-" in rid else [rid]
            if len(nums) == 2 and nums[0].isdigit() and nums[1].isdigit():
                nums = [str(n) for n in range(int(nums[0]), int(nums[1]) + 1)]
            for n in nums:
                index[n] = {"floor": int(fid), "img": img, "hl": rid not in NO_CLASSES}
    txt = json.dumps(index, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    (ROOT / "rooms.json").write_text(txt, "utf-8")
    (ROOT.parent / "worker" / "src" / "rooms.json").write_text(txt, "utf-8")
    print(f"готово: {len(index)} кабинетов на {len(FLOORS)} этажах")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else None)
