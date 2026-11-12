# База данных

Таблицы (db/schema.sql): users, sessions (хранится SHA-256 токена), projects (JSONB), orders (уникальный индекс по idem_key: повторная отправка не создаёт дубль), support_threads, audit.

## Запуск на локальном PostgreSQL
1. `pip install "psycopg[binary]"`
2. `export DATABASE_URL=postgresql://USER:PASS@localhost:5432/picpac`
3. `python db.py init` — создаст базу `picpac`, если её нет, и применит схему (повторный запуск безопасен).
4. `python db.py check` — проверка соединения и списка таблиц.
5. `python server.py`

Без DATABASE_URL работает SQLite (файл в data/). Фото лежат на диске в data/, в БД только метаданные и документы проекта.
Демо-аккаунты создаются при первом запуске (client@/vip@/sleep@/admin@picpac.test).

## Хранение фото (раунд M)
- Вошедший пользователь: фото в таблице `photo_blobs (project_id, name, data BYTEA, updated)`; имя `<id фото>.jpg` и `<id фото>.orig.jpg` для оригинала изменённого фото.
- Гость (uid начинается с `c`): фото во временном кэше `data/guest_tmp/<проект>/`; проекты без активности дольше `GUEST_TTL_HOURS` (24) удаляются вместе с фото, проверка каждые 30 минут и при запуске.
- Вход гостя в аккаунт (`/api/me/claim`): проекты переходят к аккаунту, фото из кэша переносятся в БД.
- Старые файлы `data/projects/<id>/photos` при запуске переносятся автоматически (вошедшие → БД, гости → кэш).
- Таблица `funnel_events (ts, uid, ev)`: обезличенные шаги воронки.
- Резервная копия: достаточно базы данных (и `data/orders` с PDF макетов).
