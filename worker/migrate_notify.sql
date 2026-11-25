-- Отдельные переключатели напоминаний в /notify. Выполнить ОДИН раз и ДО деплоя нового index.js.
ALTER TABLE users ADD COLUMN week_off INTEGER;
ALTER TABLE users ADD COLUMN day_off INTEGER;
ALTER TABLE users ADD COLUMN pre_off INTEGER;
