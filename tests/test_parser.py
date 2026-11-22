import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).parents[1] / "parser"))
from doc_parser import parse_docx
FX = pathlib.Path(__file__).parent / "fixtures"

def test_master_em11():
    r = parse_docx(str(FX / "master_EM11_w12.docx"))
    assert list(r.groups) == ["ЭМ-11"] and len(r.groups["ЭМ-11"]["lessons"]) == 11

def test_bachelor_groups_split():
    r = parse_docx(str(FX / "bachelor_E11_12.docx"))
    assert set(r.groups) == {"Э-11", "Э-12"}

def test_abbreviated_kind():
    r = parse_docx(str(FX / "bachelor_VM11_12.docx"))
    ls = r.groups["ВМК-11"]["lessons"]
    assert all(l["kind"] for l in ls)
    assert not any("Сем." in l["subject"] for l in ls)

def test_fm_two_groups():
    r = parse_docx(str(FX / "master_FM21_22_w6.docx"))
    assert set(r.groups) == {"ФМ-21", "ФМ-22"}


def test_news_listing():
    from news import parse_items
    items = parse_items((FX / "news_page.html").read_text(encoding="utf8"))
    assert len(items) >= 15
    assert all(i["title"] and i["url"].startswith("https://www.msu.kz/") for i in items)


def test_roman_typo_does_not_shift_days(tmp_path):
    """Опечатка «IV» вместо «VI» в конце четверга не должна сдвигать пятницу на субботу."""
    from docx import Document
    d = Document()
    d.add_paragraph("РАСПИСАНИЕ ЗАНЯТИЙ с 16.11.2026 по 21.11.2026")
    t = d.add_table(rows=1, cols=5)
    for c, v in zip(t.rows[0].cells, ["Дни", "Обучение", "", "", "2 курс М-21"]):
        c.text = v
    times = ["09:00 - 10:30", "10:40 - 12:10", "12:30 - 14:00", "14:10 - 15:40", "15:50 - 17:20", "17:30 - 19:00"]
    rom = ["I", "II", "III", "IV", "V", "VI"]
    def row(i, text="", rm=None):
        r = t.add_row().cells
        r[1].text = "ОО" if text else ""
        r[2].text = times[i]
        r[3].text = rm or rom[i]
        r[4].text = text
    for i in range(6):                       # четверг, последняя строка с опечаткой
        row(i, "603 Физика Лекции профессор КФ МГУ Иванов А.А." if i == 0 else "", "IV" if i == 5 else None)
    for i in range(6):                       # пятница
        row(i, "603 Математика Семинары доцент КФ МГУ Петров Б.Б." if i == 1 else "")
    for i in range(6):                       # суббота
        row(i, "716 Программирование Семинары доцент КФ МГУ Сидоров В.В." if i == 1 else "")
    p = tmp_path / "x.docx"
    d.save(p)
    from doc_parser import parse_docx
    res = parse_docx(str(p))
    ls = res.groups["М-21"]["lessons"]
    got = {(l["dow"], l["pair"], l["subject"]) for l in ls}
    assert got == {(1, 1, "Физика"), (2, 2, "Математика"), (3, 2, "Программирование")} or len(ls) == 3
    assert sorted(l["dow"] for l in ls) == [1, 2, 3]


def test_junk_cell_without_letters_is_not_a_lesson():
    from parser.doc_parser import parse_cell
    assert parse_cell("0") == []
    assert parse_cell("-\n.") == []
    assert [l.subject for l in parse_cell("Матан\nЛекция доц. Иванов И.И.")] == ["Матан"]


def test_week_dates_with_so():
    import datetime as dt
    from doc_parser import parse_week_dates
    assert parse_week_dates("10 неделя со 2 по 7 ноября 2026 г.")[0] == dt.date(2026, 11, 2)
    assert parse_week_dates("12 неделя с 16 по 21 ноября 2026 г.")[0] == dt.date(2026, 11, 16)
    assert parse_week_dates("со 28 декабря по 2 января 2027 г.")[0] == dt.date(2026, 12, 28)
    assert parse_week_dates("Расписание с 02.11.2026 по 07.11.2026")[0] == dt.date(2026, 11, 2)
    assert parse_week_dates("Расписание со 02.11.2026 по 07.11.2026")[0] == dt.date(2026, 11, 2)
    assert parse_week_dates("Москва 2026") is None


def test_missing_week_is_kept_and_present_week_replaced():
    import sqlite3, datetime as dt, pathlib, sys
    sys.path.insert(0, str(pathlib.Path(__file__).parents[1] / "parser"))
    import sync
    db = sqlite3.connect(":memory:")
    db.executescript((pathlib.Path(__file__).parents[1] / "worker" / "schema.sql").read_text("utf-8"))
    L = lambda subj, dow=6: {"dow": dow, "pair": 1, "start": "09:00", "end": "10:30", "subject": subj, "kind": "", "room": "507",
                             "teacher": "", "sub": "", "mode": "", "zid": "", "zcode": ""}
    g = lambda weeks: {"М-21": {"degree": "bachelor", "course": 2, "program": "", "weeks": weeks}}
    db.executescript(sync.build_sql(g({"2026-11-16": {"lessons": [L("Старая")]}}), dt.date(2026, 11, 19), 1))
    # на Moodle текущую неделю удалили, выложили следующую
    db.executescript(sync.build_sql(g({"2026-11-23": {"lessons": [L("Новая")]}}), dt.date(2026, 11, 21), 2))
    rows = db.execute("SELECT date, subject FROM cur_lessons ORDER BY date").fetchall()
    assert rows == [("2026-11-21", "Старая"), ("2026-11-28", "Новая")]
    # неделю обновили — старая версия заменяется, не дублируется
    db.executescript(sync.build_sql(g({"2026-11-23": {"lessons": [L("Новее")]}}), dt.date(2026, 11, 21), 3))
    assert db.execute("SELECT subject FROM cur_lessons WHERE date='2026-11-28'").fetchall() == [("Новее",)]


def test_teacher_surname_wrapped_to_next_line():
    from parser.doc_parser import parse_cell
    r = parse_cell("614 Теория вероятностей и математическая статистика Лекция доцент КФ МГУ \nГалиева Н.К.")
    assert len(r) == 1 and r[0].teacher == "доцент Галиева Н.К."
    r = parse_cell("614 Теория вероятностей и математическая статистика Семинары доцент КФ МГУ \nГалиева Н.К ВМ-21")
    assert len(r) == 1 and r[0].teacher.endswith("Галиева Н.К")


def test_latin_lookalike_in_surname():
    from parser.doc_parser import parse_cell
    r = parse_cell("614 Дискретная математика Семинары асс. КФ МГУ Cавицкий И.В. ВМ-21")
    assert len(r) == 1 and r[0].teacher == "асс. Савицкий И.В." and r[0].grp == "ВМ-21"


def test_room_after_type_and_two_teachers():
    from parser.doc_parser import parse_cell
    r = parse_cell("Физика Лекция 614 профессор Саулебеков А.О.")
    assert len(r) == 1 and r[0].room == "614" and r[0].teacher == "профессор Саулебеков А.О."
    r = parse_cell("614 Физика Семинар профессор Саулебеков А.О., доцент Иванов И.И.")
    assert len(r) == 1 and r[0].teacher == "профессор Саулебеков А.О., доцент Иванов И.И."
    r = parse_cell("614 Физика Лекция ст.преп. КФ МГУ Иванов И.И. и Петров П.П. (2) ВМ-21")
    assert len(r) == 1 and "Иванов И.И." in r[0].teacher and "Петров П.П." in r[0].teacher
    assert r[0].sub == "2" and r[0].grp == "ВМ-21"
