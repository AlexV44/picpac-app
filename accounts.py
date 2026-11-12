# -*- coding: utf-8 -*-
"""
Пользователи, регистрация, вход, гостевой режим и сессии (таблицы users и sessions в БД).

Роли: client (клиент picPac) и admin (оператор/администратор).
  - пароли хранятся только как scrypt-хеш с индивидуальной солью;
  - токен сессии случаен (32 байта), в БД лежит только его SHA-256, срок жизни 30 дней;
  - вход и регистрация ограничены по числу попыток (защита от перебора);
  - демо-записи (client@, vip@, sleep@, admin@picpac.test) создаются при первом запуске, их пароли публичные и тестовые.
Права проверяет сервер (server.py, декоратор need()).
"""
from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import threading
import time
import uuid

import db

_LOCK = threading.RLock()
SESSION_DAYS = 30
MIN_PASSWORD = 8
EMAIL_RX = re.compile(r"^[^@\s]{1,64}@[^@\s]+\.[^@\s]{2,}$")

# Демо-записи: НЕ реальные люди и НЕ реальные пароли.
DEMO = [
    ("client@picpac.test", "u_client", "client", "Анна Тестова", "client123"),
    ("vip@picpac.test", "u_vip", "client", "Игорь Тестов", "vip123"),
    ("sleep@picpac.test", "u_sleep", "client", "Ольга Тестова", "sleep123"),
    ("admin@picpac.test", "u_admin", "admin", "Администратор", "admin123"),
]
ROLE_NAMES = dict(client="Клиент", admin="Администратор")


# ---------------------------------------------------------------- пароли
def hash_password(pw: str) -> str:
    salt = secrets.token_bytes(16)
    h = hashlib.scrypt(pw.encode(), salt=salt, n=2 ** 14, r=8, p=1, dklen=32)
    return "scrypt$" + salt.hex() + "$" + h.hex()


def check_password(pw: str, stored: str) -> bool:
    try:
        _, salt, h = stored.split("$")
        got = hashlib.scrypt(pw.encode(), salt=bytes.fromhex(salt), n=2 ** 14, r=8, p=1, dklen=32)
        return hmac.compare_digest(got.hex(), h)
    except Exception:
        return False


_DUMMY = hash_password("dummy-password")


def ensure_demo():
    """Создаёт демо-записи, если их ещё нет."""
    for email, uid, role, name, pw in DEMO:
        if not db.run("SELECT 1 FROM users WHERE lower(email) = %s", (email,), one=True):
            try:
                db.run("INSERT INTO users (id, email, name, role, pw_hash, created) VALUES (%s, %s, %s, %s, %s, %s)",
                       (uid, email, name, role, hash_password(pw), time.time()))
            except Exception:
                pass                       # другой поток успел раньше


# ---------------------------------------------------------------- защита от перебора
_attempts: dict = {}


def throttled(key: str, limit: int = 8, window: int = 300) -> bool:
    """True, если с этого ключа (ip|email) слишком много неудачных попыток за окно."""
    now = time.time()
    with _LOCK:
        l = [t for t in _attempts.get(key, []) if now - t < window]
        _attempts[key] = l
        return len(l) >= limit


def fail(key: str):
    with _LOCK:
        _attempts.setdefault(key, []).append(time.time())


def clear_fails(key: str):
    with _LOCK:
        _attempts.pop(key, None)


# ---------------------------------------------------------------- регистрация и вход
def validate_registration(name: str, email: str, password: str) -> str:
    name, email = str(name or "").strip(), str(email or "").strip().lower()
    if len(name) < 2 or len(name) > 60:
        return "Укажите имя (от 2 символов)"
    if not EMAIL_RX.match(email) or len(email) > 120:
        return "Проверьте адрес электронной почты"
    if len(str(password or "")) < MIN_PASSWORD:
        return f"Пароль должен быть не короче {MIN_PASSWORD} символов"
    if str(password).lower() in ("password", "12345678", "qwertyui", "11111111"):
        return "Этот пароль слишком простой"
    return ""


def register(name: str, email: str, password: str):
    """Создаёт клиента и сессию. Возвращает (token, user) или (None, текст ошибки)."""
    msg = validate_registration(name, email, password)
    if msg:
        return None, msg
    email = email.strip().lower()
    uid = "u" + uuid.uuid4().hex[:12]
    with _LOCK:
        if db.run("SELECT 1 FROM users WHERE lower(email) = %s", (email,), one=True):
            return None, "Эта почта уже зарегистрирована. Войдите или используйте другую."
        try:
            db.run("INSERT INTO users (id, email, name, role, pw_hash, created) VALUES (%s, %s, %s, 'client', %s, %s)",
                   (uid, email, name.strip(), hash_password(password), time.time()))
        except Exception:
            return None, "Эта почта уже зарегистрирована. Войдите или используйте другую."
    return _new_session(dict(uid=uid, role="client", name=name.strip(), guest=False, email=email))


def login(email: str, password: str):
    email = str(email).strip().lower()
    r = db.run("SELECT id, role, name, pw_hash FROM users WHERE lower(email) = %s", (email,), one=True)
    if not r:
        check_password(str(password), _DUMMY)         # одинаковое время ответа: нельзя выяснить, есть ли такая почта
        return None
    if not check_password(str(password), r[3]):
        return None
    return _new_session(dict(uid=r[0], role=r[1], name=r[2], guest=False, email=email))


def guest(client_id: str = ""):
    uid = client_id if client_id and client_id.startswith("c") and len(client_id) <= 24 else "c" + secrets.token_hex(5)
    return _new_session(dict(uid=uid, role="client", name="Гость", guest=True, email=""))


# ---------------------------------------------------------------- сессии
def _th(tok: str) -> str:
    return hashlib.sha256(tok.encode()).hexdigest()


def _new_session(user: dict):
    tok = secrets.token_urlsafe(32)
    now = time.time()
    db.run("DELETE FROM sessions WHERE expires < %s", (now,))
    db.run("INSERT INTO sessions (tok_hash, uid, role, name, email, guest, created, expires) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
           (_th(tok), user["uid"], user["role"], user["name"], user.get("email", ""), bool(user["guest"]), now, now + SESSION_DAYS * 86400))
    return tok, user


def by_token(tok: str):
    if not tok:
        return None
    r = db.run("SELECT uid, role, name, email, guest, expires FROM sessions WHERE tok_hash = %s", (_th(tok),), one=True)
    if not r or r[5] < time.time():
        return None
    return dict(uid=r[0], role=r[1], name=r[2], email=r[3], guest=bool(r[4]))


def logout(tok: str):
    if tok:
        db.run("DELETE FROM sessions WHERE tok_hash = %s", (_th(tok),))


def forget_user(uid: str):
    """Удаление данных: закрыть все сессии пользователя (запись в users остаётся, пока он сам не удалит аккаунт)."""
    db.run("DELETE FROM sessions WHERE uid = %s", (uid,))


def name_of(uid: str) -> str:
    """Имя владельца для страницы приглашения (только имя, без почты)."""
    r = db.run("SELECT name FROM users WHERE id = %s", (uid,), one=True)
    return r[0] if r else ""
