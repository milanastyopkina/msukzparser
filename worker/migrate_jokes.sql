-- Анекдот дня: выполнить один раз (консоль D1 или wrangler d1 execute msu-schedule --remote --file migrate_jokes.sql)
ALTER TABLE users ADD COLUMN jokes INTEGER;
ALTER TABLE users ADD COLUMN fun_off INTEGER;
CREATE TABLE IF NOT EXISTS jokes (day TEXT PRIMARY KEY, text TEXT NOT NULL);
