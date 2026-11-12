-- PicPac: схема PostgreSQL. Идемпотентна (можно запускать повторно).
-- Применить вручную:  psql "$DATABASE_URL" -f db/schema.sql      или автоматически:  python db.py init

CREATE TABLE IF NOT EXISTS users (
    id          TEXT PRIMARY KEY,
    email       TEXT NOT NULL,
    name        TEXT NOT NULL,
    role        TEXT NOT NULL DEFAULT 'client' CHECK (role IN ('client', 'admin')),
    pw_hash     TEXT NOT NULL,
    created     DOUBLE PRECISION NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS users_email_uq ON users (lower(email));

-- Сессии: храним только SHA-256 токена, сам токен знает лишь клиент
CREATE TABLE IF NOT EXISTS sessions (
    tok_hash    TEXT PRIMARY KEY,
    uid         TEXT NOT NULL,
    role        TEXT NOT NULL,
    name        TEXT NOT NULL,
    email       TEXT NOT NULL DEFAULT '',
    guest       BOOLEAN NOT NULL DEFAULT FALSE,
    created     DOUBLE PRECISION NOT NULL,
    expires     DOUBLE PRECISION NOT NULL
);
CREATE INDEX IF NOT EXISTS sessions_uid_idx ON sessions (uid);

-- Проекты (альбомы и фотопечать): у каждого есть владелец; метаданные и раскладка в data (JSONB), сами фото в photo_blobs
CREATE TABLE IF NOT EXISTS projects (
    id          TEXT PRIMARY KEY,
    owner       TEXT NOT NULL,
    data        JSONB NOT NULL,
    updated     DOUBLE PRECISION NOT NULL
);
CREATE INDEX IF NOT EXISTS projects_owner_idx ON projects (owner);

-- Фото вошедших пользователей лежат в БД (у гостей они во временном хранилище и переезжают сюда после входа)
CREATE TABLE IF NOT EXISTS photo_blobs (
    project_id  TEXT NOT NULL,
    name        TEXT NOT NULL,
    data        BYTEA NOT NULL,
    updated     DOUBLE PRECISION NOT NULL,
    PRIMARY KEY (project_id, name)
);

-- Воронка (аналитика): только обезличенный id и название шага
CREATE TABLE IF NOT EXISTS funnel_events (
    ts          DOUBLE PRECISION NOT NULL,
    uid         TEXT NOT NULL,
    ev          TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS funnel_ts_idx ON funnel_events (ts);

CREATE TABLE IF NOT EXISTS orders (
    code        TEXT PRIMARY KEY,
    client      TEXT NOT NULL,
    idem_key    TEXT NOT NULL DEFAULT '',
    data        JSONB NOT NULL,
    created     DOUBLE PRECISION NOT NULL
);
CREATE INDEX IF NOT EXISTS orders_client_idx ON orders (client, created DESC);
-- повторная отправка заказа с тем же ключом не создаёт второй заказ (МП-05)
CREATE UNIQUE INDEX IF NOT EXISTS orders_idem_uq ON orders (client, idem_key) WHERE idem_key <> '';

CREATE TABLE IF NOT EXISTS support_threads (
    uid         TEXT PRIMARY KEY,
    data        JSONB NOT NULL,
    updated     DOUBLE PRECISION NOT NULL
);

CREATE TABLE IF NOT EXISTS audit (
    id          BIGSERIAL PRIMARY KEY,
    ts          TEXT NOT NULL,
    actor       TEXT NOT NULL,
    role        TEXT NOT NULL,
    action      TEXT NOT NULL,
    object      TEXT NOT NULL DEFAULT '',
    result      TEXT NOT NULL DEFAULT 'ok',
    extra       JSONB
);
