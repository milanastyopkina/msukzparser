#!/usr/bin/env python3
"""Moodle КФ МГУ (гостевой вход) -> data/schedule.json -> out/load.sql (для Cloudflare D1).

  python parser/sync.py                       # боевой запуск
  python parser/sync.py --local-dir samples/  # разобрать .doc/.docx из папки (без сети)
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from doc_parser import ParsedDoc, parse_codes, parse_docx, squash  # noqa: E402

BASE = os.getenv("MOODLE_URL", "https://tstmdl.msu.kz").rstrip("/")
COURSE_ID = int(os.getenv("MOODLE_COURSE_ID", "2"))
MAX_FILE = 30 * 1024 * 1024   # больше 30 МБ — не расписание (защита от «бомб»)
UA = {"User-Agent": "Mozilla/5.0 (compatible; msu-schedule-bot/1.0)"}
KEEP_DAYS_JSON = 14     # сколько дней после конца недели держим её в schedule.json
KEEP_DAYS_SQL = 7       # и в базе бота


# --------------------------------------------------------------- Moodle

def academic_year_start(today: dt.date) -> int:
    return today.year if today.month >= 8 else today.year - 1


def section_wanted(name: str, today: dt.date) -> bool:
    """Нужны только «Бакалавриат» и «Магистратура <год>»; прошлые учебные годы и графики — нет."""
    low = name.lower()
    if "бакалавриат" not in low and "магистратура" not in low:
        return False
    if "архив" in low:
        return False
    m = re.search(r"(\d{4})\s*[-–/]\s*(\d{2,4})", name)
    if m and int(m.group(1)) < academic_year_start(today):
        return False
    return True


def moodle_login(client) -> None:
    from bs4 import BeautifulSoup
    r = client.get(f"{BASE}/login/index.php")
    tok = BeautifulSoup(r.text, "lxml").find("input", {"name": "logintoken"})
    client.post(f"{BASE}/login/index.php", data={
        "username": "guest", "password": "guest", "logintoken": tok["value"] if tok else ""})


def list_resources(client, today: dt.date) -> list[dict]:
    from bs4 import BeautifulSoup
    r = client.get(f"{BASE}/course/view.php?id={COURSE_ID}")
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "lxml")
    out = []
    for sec in soup.select("li.section.main, li.course-section, div.course-section"):
        h = sec.select_one(".sectionname, .section-title, h3")
        name = squash(h.get_text(" ")) if h else ""
        if not section_wanted(name, today):
            continue
        for a in sec.select("a[href*='mod/resource/view.php']"):
            for x in a.select(".accesshide"):
                x.decompose()
            m = re.search(r"id=(\d+)", a["href"])
            if not m:
                continue
            out.append({"id": int(m.group(1)), "title": squash(a.get_text(" ")),
                        "url": a["href"], "section": name})
    # в списке могут встречаться дубли ссылок (боковое меню)
    seen, uniq = set(), []
    for x in out:
        if x["id"] not in seen:
            seen.add(x["id"])
            uniq.append(x)
    return uniq


def download(client, url: str) -> bytes:
    from bs4 import BeautifulSoup
    r = client.get(url)
    if "text/html" in r.headers.get("content-type", ""):
        if "/login/" in str(r.url):
            moodle_login(client)
            r = client.get(url)
        if "text/html" in r.headers.get("content-type", ""):
            a = BeautifulSoup(r.text, "lxml").select_one(
                ".resourceworkaround a, .resourcecontent a, a[href*='pluginfile.php']")
            if a and a.get("href"):
                r = client.get(a["href"])
    r.raise_for_status()
    if len(r.content) > MAX_FILE:
        raise ValueError(f"файл слишком большой: {len(r.content)} байт")
    return r.content


def to_docx(blobs: dict[int, bytes], tmp: Path) -> tuple[dict[int, Path], dict[int, str]]:
    """Сохраняет файлы, старые .doc конвертирует LibreOffice одним запуском."""
    paths: dict[int, Path] = {}
    errors: dict[int, str] = {}
    need_conv: list[tuple[int, Path]] = []
    for rid, data in blobs.items():
        if data[:2] == b"PK":
            p = tmp / f"{rid}.docx"
            p.write_bytes(data)
            paths[rid] = p
        elif data[:4] == b"\xd0\xcf\x11\xe0":
            p = tmp / f"{rid}.doc"
            p.write_bytes(data)
            need_conv.append((rid, p))
        else:
            errors[rid] = "не Word-файл (возможно, страница входа)"
    if need_conv:
        soffice = shutil.which("soffice") or shutil.which("libreoffice")
        if not soffice:
            for rid, _ in need_conv:
                errors[rid] = "нужен LibreOffice для .doc"
        else:
            out = tmp / "conv"
            out.mkdir()
            cmd = [soffice, f"-env:UserInstallation=file://{tmp}/lo_profile", "--headless",
                   "--convert-to", "docx", "--outdir", str(out)] + [str(p) for _, p in need_conv]
            subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=900)
            for rid, p in need_conv:
                q = out / f"{rid}.docx"
                if q.exists():
                    paths[rid] = q
                else:
                    errors[rid] = "LibreOffice не смог конвертировать .doc"
    return paths, errors


# ------------------------------------------------------------- сборка

def title_codes(title: str) -> list[str]:
    return parse_codes(title)


def related(a: str, b: str) -> bool:
    """ВМ-11 и ВМК-11 — одна группа под разными названиями (одна буквенная часть начинается с другой)."""
    (pa, na), (pb, nb) = a.rsplit("-", 1), b.rsplit("-", 1)
    return na == nb and (pa.startswith(pb) or pb.startswith(pa))


CODE_OK = re.compile(r"[А-ЯЁ]{1,5}-\d{2}")


PAIR_TIMES = {1: ("09:00", "10:30"), 2: ("10:40", "12:10"), 3: ("12:30", "14:00"),
              4: ("14:10", "15:40"), 5: ("15:50", "17:20"), 6: ("17:30", "19:00")}


def parse_manual_json(data: dict) -> ParsedDoc:
    """Ручной файл manual/*.json — для случаев, когда расписания в виде документа нет (например, только фото на стенде)."""
    pd = ParsedDoc()
    mon = dt.date.fromisoformat(data["week_start"])
    if mon.weekday() != 0:
        raise ValueError("week_start должен быть понедельником")
    pd.week_start, pd.week_end = mon, mon + dt.timedelta(days=5)
    pd.academic_year = mon.year if mon.month >= 8 else mon.year - 1
    zoom = data.get("zoom") or {}
    for it in data["lessons"]:
        a, b = PAIR_TIMES[int(it["pair"])]
        z = "zoom" in str(it.get("room", "")).lower()
        for code in it["groups"]:
            g = pd.groups.setdefault(code, {"program": data.get("program", ""), "lessons": []})
            g["lessons"].append({
                "dow": int(it["day"]), "pair": int(it["pair"]), "start": it.get("start", a), "end": it.get("end", b),
                "subject": it["subject"], "kind": it.get("kind", ""), "room": it.get("room", ""),
                "teacher": it.get("teacher", ""), "sub": it.get("sub", ""), "mode": it.get("mode", ""),
                "zid": zoom.get("id", "") if z else "", "zcode": zoom.get("code", "") if z else ""})
    return pd


def build(resources: list[dict], paths: dict[int, Path], errors: dict[int, str],
          today: dt.date) -> tuple[dict, list[dict]]:
    groups: dict[str, dict] = {}
    report: list[dict] = []
    ay_now = academic_year_start(today)
    for res in sorted(resources, key=lambda r: r["id"]):
        rid = res["id"]
        rep = {"id": rid, "title": res["title"], "groups": [], "lessons": 0, "note": ""}
        report.append(rep)
        if rid in errors and res.get("json") is None:
            rep["note"] = errors[rid]
            continue
        try:
            pd: ParsedDoc = (parse_manual_json(res["json"]) if res.get("json") is not None
                             else parse_docx(str(paths[rid]), fallback_codes=title_codes(res["title"])))
        except Exception as e:  # noqa: BLE001 — один плохой файл не должен ронять всё
            rep["note"] = f"ошибка разбора: {type(e).__name__}: {e}"
            continue
        if pd.warnings:
            rep["note"] = "; ".join(pd.warnings)
        if not pd.groups:
            continue
        if pd.academic_year and pd.academic_year < ay_now:
            rep["note"] = "прошлый учебный год"
            continue
        degree = "master" if "магистр" in res["section"].lower() else "bachelor"
        fresh = bool(pd.week_start) and pd.week_end >= today - dt.timedelta(days=KEEP_DAYS_JSON)
        if not fresh:
            rep["note"] = (rep["note"] + "; " if rep["note"] else "") + "неделя уже прошла"
        for code, g in pd.groups.items():
            # ВАЖНО: код группы берём только из самого файла. Название файла на Moodle бывает неверным
            # (файл «ВМ-21,22» может содержать расписание экологов Г-21,22).
            if not CODE_OK.fullmatch(code):   # кнопки бота и callback_data — только нормальные коды
                rep["note"] += f" (пропущена странная группа {code[:20]!r})"
                continue
            gi = groups.setdefault(code, {"degree": degree, "course": int(code.split("-")[1][0]),
                                          "program": g["program"], "weeks": {}})
            if g["program"]:
                gi["program"] = g["program"]
            rep["groups"].append(code)
            if not fresh:
                continue
            key = pd.week_start.isoformat()
            # надёжность источника: вручную положенный файл (2) > название на Moodle совпало с группой (1) > остальное (0)
            rank = 2 if res.get("manual") else (1 if code in title_codes(res["title"]) else 0)
            if key in gi["weeks"] and gi["weeks"][key].get("rank", 0) > rank:
                continue
            lessons = sorted(g["lessons"], key=lambda l: (l["dow"], l["start"], l["room"], l["subject"], l["sub"]))
            gi["weeks"][key] = {"week_no": pd.week_no, "src": rid, "rank": rank, "lessons": lessons}
            rep["lessons"] += len(lessons)
    # Группы, которые названы на Moodle, но в самом файле другие (или файла с ними нет): показываем в списке
    # с пустым расписанием — чтобы студент находил свою группу и видел «расписание ещё не выложено».
    for res in resources:
        degree = "master" if "магистр" in res["section"].lower() else "bachelor"
        for code in title_codes(res["title"]):
            if not CODE_OK.fullmatch(code) or code in groups:
                continue
            if any(g["degree"] == degree and related(code, c) for c, g in groups.items()):
                continue        # это та же группа под другим названием (ВМ-11 / ВМК-11)
            groups[code] = {"degree": degree, "course": int(code.split("-")[1][0]), "program": "", "weeks": {}}
            report.append({"id": res["id"], "title": res["title"], "groups": [code], "lessons": 0,
                           "note": f"в файле другая группа — {code} добавлена без расписания"})
    return {k: groups[k] for k in sorted(groups)}, report


# ------------------------------------------------------------------ SQL

def q(v, n: int = 300) -> str:
    if v is None or v == "":
        return "NULL"
    if isinstance(v, int):
        return str(v)
    v = re.sub(r"[\x00-\x08\x0b-\x1f]", " ", str(v))[:n]   # управляющие символы и слишком длинные поля
    return "'" + v.replace("'", "''") + "'"


def to_min(hhmm: str) -> int:
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def build_sql(groups: dict, today: dt.date, ver: int) -> str:
    lines = ["-- generated by parser/sync.py"]
    rows = []
    for code, g in groups.items():
        for monday, w in g["weeks"].items():
            mon = dt.date.fromisoformat(monday)
            if mon + dt.timedelta(days=5) < today - dt.timedelta(days=KEEP_DAYS_SQL):
                continue
            for l in w["lessons"]:
                date = (mon + dt.timedelta(days=l["dow"] - 1)).isoformat()
                rows.append("(" + ",".join([
                    str(ver), q(code), q(date), str(l["pair"]), str(to_min(l["start"])), str(to_min(l["end"])),
                    q(l["subject"]), q(l["kind"]), q(l["room"]), q(l["teacher"]), q(l["sub"]),
                    q(l["mode"]), q(l["zid"]), q(l["zcode"])]) + ")")
    if groups:
        gr = ",".join(f"({q(c)},{q(g['degree'])},{g['course']},{q(g['program'] or c)})" for c, g in groups.items())
        lines.append(f"INSERT OR REPLACE INTO groups (code, degree, course, program) VALUES {gr};")
        codes = ",".join(q(c) for c in groups)
        lines.append(f"DELETE FROM groups WHERE code NOT IN ({codes});")
    cols = "ver, group_code, date, pair, start_min, end_min, subject, kind, room, teacher, sub, mode, zid, zcode"
    for i in range(0, len(rows), 60):
        lines.append(f"INSERT INTO lessons ({cols}) VALUES\n" + ",\n".join(rows[i:i + 60]) + ";")
    lines.append(f"INSERT OR REPLACE INTO meta (k, v) VALUES ('ver', '{ver}');")
    lines.append(f"INSERT OR REPLACE INTO meta (k, v) VALUES ('updated', '{dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds')}');")
    lines.append(f"DELETE FROM lessons WHERE ver <> {ver};")
    return "\n".join(lines) + "\n"


# ------------------------------------------------------------------ main

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="data/schedule.json")
    ap.add_argument("--sql", default="out/load.sql")
    ap.add_argument("--today", help="YYYY-MM-DD (для тестов)")
    ap.add_argument("--local-dir", help="папка с .doc/.docx вместо Moodle")
    ap.add_argument("--manual-dir", default="manual", help="папка с файлами, положенными вручную (приоритетнее Moodle)")
    args = ap.parse_args()
    today = dt.date.fromisoformat(args.today) if args.today else dt.datetime.now(dt.timezone(dt.timedelta(hours=5))).date()

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        if args.local_dir:
            resources, blobs = [], {}
            for i, f in enumerate(sorted(Path(args.local_dir).glob("*.doc*"))):
                resources.append({"id": i + 1, "title": f.stem, "url": str(f),
                                  "section": "Магистратура" if f.stem.lower().startswith("master") else "Бакалавриат"})
                blobs[i + 1] = f.read_bytes()
        else:
            import httpx
            with httpx.Client(headers=UA, follow_redirects=True, timeout=60) as client:
                moodle_login(client)
                resources = list_resources(client, today)
                print(f"Moodle: найдено файлов расписания: {len(resources)}")
                if not resources:
                    print("::error::список файлов пуст — Moodle изменил вёрстку или недоступен")
                    return 1
                blobs = {}
                for r in resources:
                    try:
                        blobs[r["id"]] = download(client, r["url"])
                    except Exception as e:  # noqa: BLE001
                        print(f"::warning::не скачался {r['id']} {r['title']}: {e}")
                    time.sleep(0.1)
        md = Path(args.manual_dir)
        if md.is_dir() and not args.local_dir:
            for i, f in enumerate(sorted([*md.glob("*.doc"), *md.glob("*.docx")])):
                rid = 900001 + i
                resources.append({"id": rid, "title": f.stem, "url": str(f), "manual": True,
                                  "section": "Магистратура" if re.search(r"магистр|master", f.stem, re.I) else "Бакалавриат"})
                blobs[rid] = f.read_bytes()
                print(f"вручную: {f.name}")
            for i, f in enumerate(sorted(md.glob("*.json"))):
                rid = 910001 + i
                try:
                    data = json.loads(f.read_text("utf-8"))
                    resources.append({"id": rid, "title": f.stem, "url": str(f), "manual": True, "json": data,
                                      "section": "Магистратура" if data.get("degree") == "master" else "Бакалавриат"})
                    print(f"вручную: {f.name}")
                except Exception as e:  # noqa: BLE001
                    print(f"::warning::{f.name}: не удалось прочитать ({e})")
        paths, errors = to_docx(blobs, tmp)
        for r in resources:
            if r["id"] not in blobs:
                errors.setdefault(r["id"], "не скачался")
        groups, report = build(resources, paths, errors, today)

    print(f"\n{'id':>5} | {'групп':<22} | зан. | примечание")
    for r in report:
        print(f"{r['id']:>5} | {','.join(r['groups'])[:22]:<22} | {r['lessons']:>4} | {r['note']}  ({r['title'][:45]})"
              if (r["note"] or not r["lessons"]) else
              f"{r['id']:>5} | {','.join(r['groups'])[:22]:<22} | {r['lessons']:>4} |")
    bad = [r for r in report if r["note"] and r["note"] not in ("неделя уже прошла", "прошлый учебный год")
           and not r["note"].startswith("(дубль")]
    for r in bad:
        print(f"::warning::{r['id']} {r['title']}: {r['note']}")
    if not groups:
        print("::error::не распознано ни одной группы — старые данные не трогаю")
        return 1

    total = sum(len(w["lessons"]) for g in groups.values() for w in g["weeks"].values())
    print(f"\nГрупп: {len(groups)}, недель-групп: {sum(len(g['weeks']) for g in groups.values())}, занятий: {total}")

    schedule = {"generated_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
                "source": f"{BASE}/course/view.php?id={COURSE_ID}", "groups": groups}
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    # generated_at меняется при каждом запуске -> сравниваем без него, иначе коммит каждый раз
    body = json.dumps(groups, ensure_ascii=False, sort_keys=True, indent=1)
    old_body = None
    if out.exists():
        try:
            old_body = json.dumps(json.loads(out.read_text("utf-8"))["groups"], ensure_ascii=False, sort_keys=True, indent=1)
        except Exception:  # noqa: BLE001
            pass
    changed = body != old_body
    if changed:
        out.write_text(json.dumps(schedule, ensure_ascii=False, sort_keys=True, indent=1), "utf-8")
    print("schedule.json:", "ИЗМЕНЁН" if changed else "без изменений", "(в git не коммитится: там Zoom-коды)")

    ver = int(time.time())
    sql = build_sql(groups, today, ver)
    sql_path = Path(args.sql)
    sql_path.parent.mkdir(parents=True, exist_ok=True)
    sql_path.write_text(sql, "utf-8")
    import hashlib
    # хеш SQL без номера версии: меняется при смене данных и при смене окна недель
    sql_hash = hashlib.sha256("\n".join(l for l in sql.splitlines() if "'updated'" not in l and "'ver'" not in l)
                              .replace(str(ver), "VER").encode()).hexdigest()
    (sql_path.parent / "load.sha256").write_text(sql_hash, "utf-8")
    print(f"SQL: {sql_path} ({len(sql) // 1024} КБ), sha256={sql_hash[:12]}")
    (sql_path.parent / "checked.sql").write_text(
        f"INSERT OR REPLACE INTO meta (k, v) VALUES ('checked', '{dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds')}');\n", "utf-8")
    gho = os.getenv("GITHUB_OUTPUT")
    if gho:
        with open(gho, "a") as f:
            f.write(f"sql_hash={sql_hash}\nchanged={'true' if changed else 'false'}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
