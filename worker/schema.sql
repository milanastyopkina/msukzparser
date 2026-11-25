CREATE TABLE IF NOT EXISTS groups (code TEXT PRIMARY KEY, degree TEXT, course INTEGER, program TEXT);
CREATE TABLE IF NOT EXISTS lessons (
  ver INTEGER NOT NULL, group_code TEXT NOT NULL, date TEXT NOT NULL, pair INTEGER,
  start_min INTEGER NOT NULL, end_min INTEGER NOT NULL, subject TEXT, kind TEXT, room TEXT,
  teacher TEXT, sub TEXT, mode TEXT, zid TEXT, zcode TEXT);
CREATE INDEX IF NOT EXISTS ix_l_gd ON lessons(group_code, date);
CREATE INDEX IF NOT EXISTS ix_l_ds ON lessons(date, start_min);
CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT);
INSERT OR IGNORE INTO meta(k,v) VALUES('ver','0');
DROP VIEW IF EXISTS cur_lessons;
CREATE VIEW cur_lessons AS SELECT * FROM lessons WHERE ver = (SELECT CAST(v AS INTEGER) FROM meta WHERE k='ver');
CREATE TABLE IF NOT EXISTS users (chat_id INTEGER PRIMARY KEY, group_code TEXT, notify INTEGER DEFAULT 1, map_pre INTEGER DEFAULT 0, jokes INTEGER, fun_off INTEGER, week_off INTEGER, day_off INTEGER, pre_off INTEGER);
-- для уже созданной базы один раз выполнить: ALTER TABLE users ADD COLUMN map_pre INTEGER DEFAULT 0;
CREATE TABLE IF NOT EXISTS sent (chat_id INTEGER, k TEXT, PRIMARY KEY(chat_id, k));
CREATE TABLE IF NOT EXISTS news (id INTEGER PRIMARY KEY, feed TEXT, feeds TEXT, title TEXT, url TEXT, seen INTEGER);
CREATE TABLE IF NOT EXISTS subs (chat_id INTEGER, feed TEXT, since INTEGER, PRIMARY KEY(chat_id, feed));
CREATE INDEX IF NOT EXISTS ix_users_group ON users(group_code);

CREATE TABLE IF NOT EXISTS jokes (day TEXT PRIMARY KEY, text TEXT NOT NULL);
