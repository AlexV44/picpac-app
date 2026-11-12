# -*- coding: utf-8 -*-
"""
Слой базы данных.

  DATABASE_URL=postgresql://user:password@localhost:5432/picpac   -> PostgreSQL (драйвер psycopg 3 или psycopg2)
  переменная не задана                                              -> SQLite-файл data/picpac.db (для разработки и тестов)

Схема одна для обоих: db/schema.sql (PostgreSQL); для SQLite она автоматически приводится к его типам.
Команды:
  python db.py init     создать базу данных (если её нет) и применить схему
  python db.py check    проверить подключение и показать таблицы

Соединение своё у каждого потока (сервер многопоточный), автокоммит: каждая операция атомарна.
Запросы пишутся с %s и ::jsonb (стиль PostgreSQL), для SQLite они переписываются.
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
SCHEMA = os.path.join(HERE, "db", "schema.sql")
URL = os.environ.get("DATABASE_URL", "").strip()
IS_PG = URL.startswith(("postgres://", "postgresql://"))
_local = threading.local()
_init_lock = threading.Lock()
_ready = False


def _data_dir():
    import orders as O            # путь к данным задаётся там (PICPAC_DATA)
    return O.DATA


def _sqlite_path():
    return os.environ.get("PICPAC_DB_FILE") or os.path.join(_data_dir(), "picpac.db")


def _driver():
    try:
        import psycopg                       # type: ignore
        return "psycopg", psycopg
    except ImportError:
        try:
            import psycopg2                  # type: ignore
            return "psycopg2", psycopg2
        except ImportError:
            raise RuntimeError("Для PostgreSQL установите драйвер: pip install \"psycopg[binary]\" (или psycopg2-binary)")


def _connect_pg(url):
    name, mod = _driver()
    if name == "psycopg":
        return mod.connect(url, autocommit=True, connect_timeout=5)
    c = mod.connect(url, connect_timeout=5)
    c.autocommit = True
    return c


def _conn():
    c = getattr(_local, "c", None)
    if c is not None:
        return c
    if IS_PG:
        c = _connect_pg(URL)
    else:
        os.makedirs(os.path.dirname(_sqlite_path()), exist_ok=True)
        c = sqlite3.connect(_sqlite_path(), timeout=30, isolation_level=None, check_same_thread=False)
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA busy_timeout=30000")
    _local.c = c
    return c


def _sql(q: str) -> str:
    return q if IS_PG else q.replace("%s::jsonb", "?").replace("%s", "?")


def _j(v):
    if v is None or isinstance(v, (dict, list)):
        return v
    return json.loads(v)


def dumps(o) -> str:
    return json.dumps(o, ensure_ascii=False, default=lambda x: list(x) if isinstance(x, (tuple, set)) else str(x))


def run(q: str, params=(), one=False, many=False):
    """Выполняет запрос. one: первая строка или None; many: список строк; иначе число затронутых строк."""
    ensure()
    for attempt in (0, 1):
        try:
            c = _conn()
            cur = c.cursor()
            cur.execute(_sql(q), tuple(params))
            if one:
                r = cur.fetchone()
                cur.close()
                return r
            if many:
                r = cur.fetchall()
                cur.close()
                return r
            n = cur.rowcount
            cur.close()
            return n
        except (sqlite3.OperationalError, Exception) as e:
            # разорванное соединение с PostgreSQL: пересоздаём один раз
            if attempt == 0 and IS_PG and e.__class__.__name__ in ("OperationalError", "InterfaceError"):
                _local.c = None
                continue
            raise


def _schema_for_sqlite(sql: str) -> str:
    return (sql.replace("BIGSERIAL PRIMARY KEY", "INTEGER PRIMARY KEY AUTOINCREMENT").replace("JSONB", "TEXT")
               .replace("BYTEA", "BLOB").replace("DOUBLE PRECISION", "REAL").replace("BOOLEAN", "INTEGER").replace("FALSE", "0").replace("TRUE", "1"))


def _statements(sql: str):
    out, buf = [], []
    for line in sql.splitlines():
        if line.strip().startswith("--"):
            continue
        buf.append(line)
        if line.rstrip().endswith(";"):
            out.append("\n".join(buf).strip())
            buf = []
    return [s for s in out if s]


def create_database():
    """PostgreSQL: создаёт базу из DATABASE_URL, если её ещё нет (нужны права CREATEDB)."""
    if not IS_PG:
        return False
    from urllib.parse import urlsplit, urlunsplit
    u = urlsplit(URL)
    dbname = u.path.lstrip("/") or "postgres"
    admin = urlunsplit((u.scheme, u.netloc, "/postgres", u.query, ""))
    c = _connect_pg(admin)
    cur = c.cursor()
    cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (dbname,))
    if cur.fetchone():
        c.close()
        return False
    cur.execute('CREATE DATABASE "%s"' % dbname.replace('"', ""))
    c.close()
    return True


def init(verbose=False):
    """Применяет схему (идемпотентно). Для PostgreSQL сначала создаёт базу, если её нет."""
    global _ready
    with _init_lock:
        if IS_PG:
            try:
                made = create_database()
                if verbose and made:
                    print("база данных создана")
            except Exception as e:
                if verbose:
                    print("создание базы пропущено:", e)
        with open(SCHEMA, encoding="utf-8") as f:
            sql = f.read()
        if not IS_PG:
            sql = _schema_for_sqlite(sql)
        c = _conn()
        for st in _statements(sql):
            cur = c.cursor()
            cur.execute(st)
            cur.close()
        _ready = True
        if verbose:
            print("схема применена:", "PostgreSQL" if IS_PG else "SQLite " + _sqlite_path())


def ensure():
    if not _ready:
        init()


def kind() -> str:
    return "PostgreSQL" if IS_PG else "SQLite"


def tables():
    if IS_PG:
        return [r[0] for r in run("SELECT table_name FROM information_schema.tables WHERE table_schema = 'public' ORDER BY 1", many=True)]
    return [r[0] for r in run("SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY 1", many=True)]


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "check"
    if cmd == "init":
        init(verbose=True)
        print("таблицы:", ", ".join(tables()))
    else:
        ensure()
        print(kind(), "подключено; таблицы:", ", ".join(tables()))
