#!/usr/bin/env python3
"""anekdotov.net: «лучшие анекдоты дня» -> out/jokes.sql (запас для бота, если Cloudflare не пускают на сайт).

Строки кладутся в таблицу jokes с day = 'p:<хэш>'. Бот берёт оттуда анекдот, только если прямой запрос не удался.
"""
from __future__ import annotations

import hashlib
import html as htmllib
import re
import sys
from pathlib import Path

import httpx

URL = "https://anekdotov.net/anekdot/day/"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
      "Accept-Language": "ru-RU,ru;q=0.9"}
# тот же фильтр, что в worker/src/index.js (JOKE_BAD)
BAD = re.compile(r"х[уy][йеёяию]|пизд|бляд|бля[тд]ь|пидор|пидр|мудак|залуп|гандон|[её]б(?:ать|ан[аоуыи]?|ал[аио]?|аш|ну[лт]|ун)|проститутк|шлюх|сиськ|трахат|трахн|секс", re.I)
ITEM = re.compile(r'<div class="anekdot">\s*<p>([\s\S]*?)</p>\s*</div>', re.I)


def clean(x: str) -> str:
    x = re.sub(r"<br\s*/?>", "\n", x, flags=re.I)
    x = re.sub(r"<[^>]+>", "", x)
    x = htmllib.unescape(x).replace("\xa0", " ")
    x = re.sub(r"[ \t\r\f\v]+", " ", x)
    return re.sub(r" *\n *", "\n", x).strip()


def parse(page: str) -> list[str]:
    out = []
    for m in ITEM.finditer(page):
        t = clean(m.group(1))
        if 30 <= len(t) <= 900 and not BAD.search(t) and t not in out:
            out.append(t)
    return out


def q(s: str) -> str:
    return "'" + s.replace("'", "''") + "'"


def main() -> int:
    r = httpx.get(URL, headers=UA, timeout=30, follow_redirects=True)
    print("HTTP", r.status_code, len(r.content))
    r.raise_for_status()
    m = re.search(rb'charset=["\']?([\w-]+)', r.content[:2048]) or re.search(r"charset=([\w-]+)", r.headers.get("content-type", ""))
    cs = (m.group(1).decode() if m and isinstance(m.group(1), bytes) else m.group(1) if m else "windows-1251")
    page = r.content.decode(cs, errors="replace")
    items = parse(page)
    print("анекдотов:", len(items))
    Path("out").mkdir(exist_ok=True)
    sql = "".join(f"INSERT OR IGNORE INTO jokes (day, text) VALUES ({q('p:' + hashlib.md5(t.encode()).hexdigest()[:12])}, {q(t)});\n" for t in items)
    Path("out/jokes.sql").write_text(sql, encoding="utf-8")
    return 0 if items else 1


if __name__ == "__main__":
    sys.exit(main())
