"""Разбор .docx с расписанием КФ МГУ в список занятий.

Умеет два формата, которые реально лежат на tstmdl.msu.kz:
  * магистратура: колонки «Дни | пары | Время | <группа>», пары арабскими цифрами,
    занятие в ячейке в две строки: «АУД   Предмет \\n Тип   Преподаватель»;
  * бакалавриат: «Дни | Обучение | <курс, группы>», пары римскими цифрами,
    названия дней нарисованы картинками, в одной ячейке несколько занятий,
    каждое одной строкой: «АУД Предмет Лекции доцент КФ МГУ Фамилия И.О. (1) Э-11».
"""
from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass, field

from docx import Document
from docx.oxml.ns import qn
from docx.table import _Cell

DAYS = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота"]
MONTHS = {
    "января": 1, "февраля": 2, "марта": 3, "апреля": 4, "мая": 5, "июня": 6,
    "июля": 7, "августа": 8, "сентября": 9, "октября": 10, "ноября": 11, "декабря": 12,
}
ROMAN = {"I": 1, "II": 2, "III": 3, "IV": 4, "V": 5, "VI": 6, "VII": 7, "VIII": 8}

TIME_RE = re.compile(r"(\d{1,2})[.:](\d{2})\s*[-–—]\s*(\d{1,2})[.:](\d{2})")
CODE_RE = re.compile(r"([А-ЯЁ]{1,5})-(\d{2}(?:\s*,\s*\d{2})*)")
UP, LOW = "А-ЯЁӘІҢҒҮҰҚӨҺ", "а-яёәіңғүұқөһ"   # + казахские буквы
FIO = rf"[{UP}][{LOW}{UP}\-]+\s+[{UP}]\.\s?[{UP}]?\.?"
FIO_RE = re.compile(FIO)
ROOM_RE = re.compile(
    r"^(?P<room>\d{3,4}[А-Яа-яA-Za-z]?(?:\s+[Zz][Oo][Oo][Mm](?:-\d+)?)?"
    r"|ДО(?:\s+[Zz][Oo][Oo][Mm](?:-\d+)?)?"
    r"|[Zz][Oo][Oo][Mm](?:-\d+)?)(?=\s|$)"
)
# Регистр важен: «Научно-исследовательский семинар» (в названии) не тип занятия.
TYPE_RE = re.compile(
    r"(?<!\S)(Лекци[яи]|Лек\.|Сем\.|Лаб\.|Практ\.|Семинар(?:ы)?|Лаборатор\w+(?:\s+работ\w+)?|Консультаци\w+|"
    r"Зач[её]т\w*|Экзамен\w*|Практическ\w+\s+занят\w+)(?!\S)"
)
TITLE_WORD = r"(?:ст\.\s?н\.\s?с\.|ст\.\s?преп\.?|ст\.преп|доцент|доц\.|профессор|проф\.|преп\.|ассистент|асс\.|сотр\.)"
TEACHER_START_RE = re.compile(r"(?<!\S)" + TITLE_WORD + r"(?!\w)")
TITLE_TAIL_RE = re.compile(r"\s+(ст\.\s?преп\.?|доцент|доц\.|профессор|проф\.|преп\.|ассистент|асс\.)$")
ZOOM_RE = re.compile(
    r"(?i)zoom(?:-(\d+))?[\s:|,]*(?:идентификатор:?\s*)?(\d[\d ]{7,12}\d)\s*,?\s*код\s*доступа:?\s*(\w+)"
)
DATE_WORDS_RE = re.compile(
    r"с\s+(\d{1,2})(?:\s+([а-яё]+))?\s+по\s+(\d{1,2})\s+([а-яё]+)\s+(\d{4})", re.I
)
DATE_DOTS_RE = re.compile(r"[CcСс]\s*(\d{1,2})\.(\d{2})\.(\d{4})\s*по\s*(\d{1,2})\.(\d{2})\.(\d{4})")
CODE_ONLY_RE = re.compile(r"[А-ЯЁ]{1,5}-\d{2}")
WEEK_NO_RE = re.compile(r"(\d+)\s*неделя")


# ---------------------------------------------------------------- helpers

def squash(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").replace("\xa0", " ")).strip()


def parse_codes(text: str) -> list[str]:
    """'Э-11,12' -> ['Э-11','Э-12'];  'ФМ-21' -> ['ФМ-21']."""
    out: list[str] = []
    for m in CODE_RE.finditer(text or ""):
        for n in re.split(r"\s*,\s*", m.group(2)):
            c = f"{m.group(1)}-{n}"
            if c not in out:
                out.append(c)
    return out


def hm(h: str, m: str) -> str:
    return f"{int(h):02d}:{m}"


def parse_week_dates(text: str) -> tuple[dt.date, dt.date] | None:
    m = DATE_DOTS_RE.search(text)
    if m:
        d1, mo1, y1, d2, mo2, y2 = map(int, m.groups())
        return dt.date(y1, mo1, d1), dt.date(y2, mo2, d2)
    m = DATE_WORDS_RE.search(text)
    if m:
        d1, mon1, d2, mon2, y = m.groups()
        mo2 = MONTHS.get(mon2.lower())
        if not mo2:
            return None
        mo1 = MONTHS.get((mon1 or "").lower(), mo2)
        y2 = int(y)
        y1 = y2 - 1 if mo1 > mo2 else y2
        return dt.date(y1, mo1, int(d1)), dt.date(y2, mo2, int(d2))
    return None


def parse_zooms(text: str) -> dict[str, tuple[str, str]]:
    """{'' : (id, code)} или {'1': ..., '2': ...} для Zoom-1/Zoom-2."""
    zooms: dict[str, tuple[str, str]] = {}
    for m in ZOOM_RE.finditer(text):
        key = m.group(1) or ""
        zooms.setdefault(key, (squash(m.group(2)), m.group(3)))
    return zooms


# ------------------------------------------------------ cell -> lessons

@dataclass
class Lesson:
    room: str = ""
    subject: str = ""
    kind: str = ""
    teacher: str = ""
    sub: str = ""      # подгруппа «(1)», «(2)»…
    grp: str = ""      # занятие только для этой группы («… Э-11»)

    def empty(self) -> bool:
        return not (self.room or self.subject or self.kind or self.teacher)


def norm_kind(k: str) -> str:
    k = k.strip()
    low = k.lower()
    if low.startswith(("лекци", "лек.")):
        return "Лекция"
    if low.startswith(("семинар", "сем.")):
        return "Семинар"
    if low.startswith("лаб"):
        return "Лабораторная"
    if low.startswith("практ"):
        return "Практика"
    return k[:1].upper() + k[1:]


def clean_teacher(t: str) -> str:
    t = re.sub(r"(преп|доц|проф|асс|пр)\.(?=[А-ЯЁ])", r"\1. ", t)
    t = re.sub(r"\b(КФ\s+)?МГУ\b", " ", t)        # «доцент КФ МГУ Иванов» -> «доцент Иванов»
    t = re.sub(r"\b(доцент|профессор|ст\.?\s?преп\.?|преп\.?|асс\.?|в\.н\.с\.|с\.н\.с\.)\s+(?=(доцент|профессор|ст\.?\s?преп|преп|асс)\b)", "", t)
    return squash(t)


def _take_teacher(cur: Lesson, text: str) -> None:
    """text — всё, что после типа занятия: «доц. МГУ Фамилия И.О. (2) Э-11»."""
    text = squash(text)
    if not text:
        return
    m = FIO_RE.search(text)
    if not m:
        cur.teacher = clean_teacher(cur.teacher + " " + text)
        return
    cur.teacher = clean_teacher(cur.teacher + " " + text[: m.end()])
    tail = text[m.end():]
    s = re.search(r"\((\d)\)", tail)
    if s:
        cur.sub = s.group(1)
    codes = parse_codes(tail)
    if codes:
        cur.grp = codes[0]


def parse_cell(text: str) -> list[Lesson]:
    lines = [squash(x) for x in re.split(r"[\n\r\v\x0b]+", text or "")]
    lines = [x for x in lines if x]
    out: list[Lesson] = []
    cur = Lesson()

    def flush() -> None:
        nonlocal cur
        if not cur.empty():
            out.append(cur)
        cur = Lesson()

    for ln in lines:
        if CODE_ONLY_RE.fullmatch(ln):          # подпись группы на отдельной строке
            target = cur if not cur.empty() else (out[-1] if out else None)
            if target is not None:
                target.grp = parse_codes(ln)[0]
            continue
        room_m = ROOM_RE.match(ln)
        if not cur.empty() and (cur.teacher or (room_m and cur.subject)):
            flush()
        rest = ln
        if room_m and not cur.room:
            cur.room = squash(room_m.group("room"))
            rest = ln[room_m.end():].strip()
        t = TYPE_RE.search(rest)
        if t:
            subj_part = rest[: t.start()].strip()
            if not cur.kind:
                cur.kind = norm_kind(t.group(1))
            after = rest[t.end():].strip()
            tt = TITLE_TAIL_RE.search(subj_part)
            if tt:  # «Микроэкономика-1 асс. Семинары КФ МГУ Фамилия И.О.»
                after = (tt.group(1) + " " + after).strip()
                subj_part = subj_part[: tt.start()].strip()
        else:
            ts = TEACHER_START_RE.search(rest)
            fio = FIO_RE.search(rest)
            if ts and fio and ts.start() < fio.start():
                subj_part, after = rest[: ts.start()].strip(), rest[ts.start():].strip()
            elif fio:
                subj_part, after = rest[: fio.start()].strip(), rest[fio.start():].strip()
            else:
                subj_part, after = rest, ""
        if subj_part:
            cur.subject = (cur.subject + " " + subj_part).strip()
        _take_teacher(cur, after)
    flush()
    return [x for x in out if x.subject]


# ----------------------------------------------------------- the document

@dataclass
class ParsedDoc:
    week_start: dt.date | None = None       # понедельник
    week_end: dt.date | None = None
    week_no: int | None = None
    academic_year: int | None = None       # год начала учебного года
    direction: str = ""
    groups: dict[str, dict] = field(default_factory=dict)   # code -> {program, lessons: []}
    warnings: list[str] = field(default_factory=list)


def doc_text(doc: Document) -> str:
    """Весь текст документа, включая рамки и вложенные блоки."""
    body = doc.element.body
    paras = ["".join(t.text or "" for t in p.iter(qn("w:t"))) for p in body.iter(qn("w:p"))]
    return "\n".join(p for p in paras if p.strip())


def _row_cells(row):
    """[(cell, grid_index)] по сетке таблицы; при кривой вертикальной склейке — запасной путь."""
    try:
        return list(enumerate(row.cells))
    except Exception:
        out, gi = [], row._tr.grid_before or 0
        for tc in row._tr.tc_lst:
            out.append((gi, _Cell(tc, row.table)))
            span = tc.grid_span or 1
            for k in range(1, span):
                out.append((gi + k, _Cell(tc, row.table)))
            gi += span
        return out


def _unique_cells(row):
    """Уникальные ячейки строки в порядке слева направо: [(text, first_col, last_col)]."""
    seen: dict = {}
    order: list = []
    for gi, c in _row_cells(row):
        tc = c._tc
        if tc in seen:
            seen[tc][2] = gi
        else:
            seen[tc] = [c, gi, gi]
            order.append(tc)
    return [(seen[tc][0].text, seen[tc][1], seen[tc][2]) for tc in order]


def _pair_number(text: str) -> int | None:
    t = squash(text).upper().replace("І", "I")  # кириллическая «І»
    if re.fullmatch(r"\d", t):
        return int(t)
    return ROMAN.get(t)


def parse_docx(path: str, fallback_codes: list[str] | None = None) -> ParsedDoc:
    doc = Document(path)
    text = doc_text(doc)
    res = ParsedDoc()

    dates = parse_week_dates(text)
    if dates:
        start = dates[0]
        res.week_start = start - dt.timedelta(days=start.weekday())
        res.week_end = res.week_start + dt.timedelta(days=5)
    else:
        res.warnings.append("не найдены даты недели")
    m = WEEK_NO_RE.search(text)
    if m:
        res.week_no = int(m.group(1))
    m = re.search(r"(\d{4})\s*[/-]\s*(\d{4})\s*учебн", text)
    if m:
        res.academic_year = int(m.group(1))
    m = re.search(r"направление\s*[«\"]([^»\"]+)[»\"]", text)
    if m:
        res.direction = squash(m.group(1)).capitalize()

    zooms = parse_zooms(text)

    headers: list[dict] = []        # [{c0, c1, codes, program}]
    day: int | None = None
    named_days = False
    prev_pair = 0

    for table in doc.tables:
        own_headers: list[dict] = []
        inherited = False
        rows = list(table.rows)
        # 1. заголовок таблицы
        for row in rows:
            cells = _unique_cells(row)
            if cells and squash(cells[0][0]).lower() == "дни":
                for txt, c0, c1 in cells[1:]:
                    codes = parse_codes(txt)
                    if codes:
                        program = squash(CODE_RE.sub("", re.sub(r"\d\s*курс", "", txt))).strip(" ,.;:-")
                        own_headers.append({"c0": c0, "c1": c1, "codes": codes, "program": program})
                break
        if own_headers:
            headers = own_headers
        else:
            inherited = True

        # 2. строки с занятиями
        for row in rows:
            cells = _unique_cells(row)
            time_i = pair_i = None
            for i, (txt, c0, c1) in enumerate(cells):
                t = squash(txt)
                if time_i is None and TIME_RE.fullmatch(t):
                    time_i = i
                elif pair_i is None and _pair_number(t) is not None and len(t) <= 4:
                    pair_i = i
            if time_i is None or pair_i is None:
                continue
            tm = TIME_RE.fullmatch(squash(cells[time_i][0]))
            pair = _pair_number(cells[pair_i][0])
            start, end = hm(tm.group(1), tm.group(2)), hm(tm.group(3), tm.group(4))

            # день недели
            found = None
            for txt, _, _ in cells[: min(time_i, pair_i)]:
                low = squash(txt).lower()
                for di, name in enumerate(DAYS):
                    if name in low:
                        found = di
            if found is not None:
                day, named_days = found, True
            elif not named_days:
                day = 0 if day is None else (day + 1 if pair < prev_pair else day)
            prev_pair = pair
            if day is None or day > 5:
                res.warnings.append(f"день недели вышел за пределы Пн–Сб (пара {pair})")
                continue

            # режим обучения (ОО / ДО / ДОО) — только у бакалавриата
            mode = ""
            for txt, _, _ in cells[: min(time_i, pair_i)]:
                t = squash(txt).upper()
                if t in ("ДО", "ДОО", "ОО"):      # если в строке и «ДОО», и «ОО» — не знаем, чьё что
                    mode = t

            # ячейки с занятиями — всё правее времени и номера пары
            last_meta = max(time_i, pair_i)
            for txt, c0, c1 in cells[last_meta + 1:]:
                if not squash(txt):
                    continue
                if inherited and len(headers) != 1:
                    codes_here = [c for h in headers for c in h["codes"]]
                    programs = {c: h["program"] for h in headers for c in h["codes"]}
                else:
                    ov = [h for h in headers if not (c1 < h["c0"] or c0 > h["c1"])]
                    if inherited and len(headers) == 1:
                        ov = headers
                    codes_here = [c for h in ov for c in h["codes"]]
                    programs = {c: h["program"] for h in ov for c in h["codes"]}
                if not codes_here:
                    codes_here = list(fallback_codes or [])
                    programs = {c: "" for c in codes_here}
                if not codes_here:
                    res.warnings.append("не удалось определить группу для ячейки")
                    continue
                for les in parse_cell(txt):
                    targets = codes_here
                    if les.grp:
                        if les.grp in codes_here:
                            targets = [les.grp]
                        else:  # в шапке «ВМК-11», в ячейке «ВМ-11» — сверяем по номеру группы
                            num = les.grp.split("-")[-1]
                            same = [c for c in codes_here if c.split("-")[-1] == num]
                            if len(same) == 1:
                                targets = same
                    zid = zcode = ""
                    if "zoom" in les.room.lower() or mode == "ДО":
                        mz = re.search(r"zoom-(\d+)", les.room, re.I)
                        z = zooms.get(mz.group(1)) if mz else None
                        if z is None:
                            z = zooms.get("") or (next(iter(zooms.values())) if len(zooms) == 1 else None)
                        if z:
                            zid, zcode = z
                    for code in targets:
                        g = res.groups.setdefault(code, {"program": programs.get(code, ""), "lessons": []})
                        g["lessons"].append({
                            "dow": day + 1, "pair": pair, "start": start, "end": end,
                            "subject": les.subject, "kind": les.kind, "room": les.room,
                            "teacher": les.teacher, "sub": les.sub, "mode": mode,
                            "zid": zid, "zcode": zcode,
                        })
    if not named_days and res.groups:
        if day != 5:
            res.warnings.append(f"дни определены по сбросу номера пары, найдено дней: {(day or 0) + 1} из 6")
    # без дублей (одно и то же занятие, растянутое на две строки таблицы, остаётся: у него разные пары)
    for g in res.groups.values():
        seen, uniq = set(), []
        for l in g["lessons"]:
            k = tuple(l.items())
            if k not in seen:
                seen.add(k)
                uniq.append(l)
        g["lessons"] = uniq
    for g in res.groups.values():
        if not g["program"]:
            g["program"] = res.direction
    if not res.groups:
        res.warnings.append("занятий не найдено")
    return res
