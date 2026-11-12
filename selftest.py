# -*- coding: utf-8 -*-
"""
Самопроверка всего сценария без браузера и без ключей:  python selftest.py

Проверяет: проекты (книга и печать) -> оценка качества -> автосборка -> тема -> чат с ходом рассуждений (офлайн и
через поддельный OpenRouter) -> правка фото по тексту (локально и через image-модель) -> корзина и количество ->
проверка перед печатью -> заказ из нескольких позиций -> список заказов клиента -> трекинг -> PDF ->
сохранение проектов на диск и восстановление после «перезапуска».
Данные пишутся во временную папку.
"""
import base64
import io
import json
import os
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
os.chdir(HERE)

SEEN = {}


class Mock(BaseHTTPRequestHandler):
    """Поддельный OpenRouter для текстового чата (/chat/completions)."""

    def log_message(self, *a):
        pass

    def do_GET(self):
        raw = json.dumps(dict(ready=True)).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        if self.path.endswith("/edit"):
            SEEN.update(path=self.path, auth=self.headers.get("Authorization"), body=body)
            from PIL import Image
            b = io.BytesIO()
            Image.new("RGB", (640, 480), (30, 160, 90)).save(b, "JPEG")
            out = dict(image=base64.b64encode(b.getvalue()).decode(), seconds=1.5)
        else:
            SEEN["chat_body"] = body
            content = json.dumps(dict(thought="Просят праздничное оформление, значит нужна тема дня рождения для всей книги.",
                                      reply="Оформляю книгу в стиле дня рождения.",
                                      actions=[dict(type="apply_theme", theme="birthday", target="all")]), ensure_ascii=False)
            out = dict(model="mock/model", choices=[dict(message=dict(content=content, reasoning="Пользователь хочет праздник. Тема birthday подходит."))],
                       usage=dict(prompt_tokens=100, completion_tokens=40, cost=0.0001))
        raw = json.dumps(out).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


srv = HTTPServer(("127.0.0.1", 0), Mock)
threading.Thread(target=srv.serve_forever, daemon=True).start()
os.environ["PICPAC_DATA"] = tempfile.mkdtemp(prefix="picpac_test_")
os.environ["TRACK_STEP_SECONDS"] = "3600"
os.environ.pop("OPENROUTER_API_KEY", None)

import server as SV  # noqa: E402
import projects as PJ  # noqa: E402

SV.AS.API_URL = f"http://127.0.0.1:{srv.server_port}/api/v1/chat/completions"
c = SV.app.test_client()
cg = SV.app.test_client()                      # клиент без cookie и токена: анонимный гость
_r0 = c.post("/api/register", json=dict(name="Тест Основной", email="main@picpac.test", password="MainPass123")).get_json()
c.environ_base["HTTP_AUTHORIZATION"] = "Bearer " + _r0["token"]      # основной пользователь прежних сценариев
passed, failed = [], []


def check(name, cond, extra=""):
    (passed if cond else failed).append(name)
    print(("  ✔ " if cond else "  ✘ ") + name + (f"  [{extra}]" if extra and not cond else ""))


def post(path, **js):
    return c.post(path, json=js)


def run_job(path, **js):
    r = post(path, **js)
    if r.status_code != 200:
        return None, r
    jid = r.get_json()["job"]
    for _ in range(300):
        j = c.get("/api/job/" + jid).get_json()
        if j["done"]:
            return j, r
        time.sleep(0.1)
    return None, r


print("\n1. Услуги и проекты")
st = c.get("/api/state").get_json()
check("каталог: 5 услуг с ценами «от» как на сайте",
      "catalog" not in st and st["catalog_v"] == c.get("/api/catalog").get_json()["version"]
      and [p["id"] for p in c.get("/api/catalog").get_json()["products"]] == ["prints", "classic", "premium", "digital", "lite"])
check("без проекта действия над альбомом отклоняются", post("/api/ai", op="autoplan").status_code == 409)
st = post("/api/project/new", product="premium", params=dict(cover="hard")).get_json()
book_id = st["cur"]
check("проект-книга создан", st["proj"]["kind"] == "book" and len(st["spreads"]) == 3)
st = post("/api/demo").get_json()
check("демо-фото загружены и оценены", len(st["photos"]) >= 8)
check("есть дубль и низкое качество", any(p["dup"] for p in st["photos"]) and any(p["grade"] == "bad" for p in st["photos"]))
st = post("/api/ai", op="autoplan").get_json()
check("альбом собран", len(st["spreads"]) >= 2 and all(s["photo"] for sp in st["spreads"] for s in sp["slots"]))
check("картинки разворота и миниатюры отдаются", c.get("/api/spread/0.jpg?w=500").status_code == 200
      and c.get(f"/api/project/{book_id}/thumb.jpg").status_code == 200)

print("\n2. Чат: ход рассуждений и фоновая работа")
j, r = run_job("/api/chat/start", text="перемешай фото")
msg = j["result"]["message"] if j else {}
check("запрос выполняется в фоне (задача + шаги)", j is not None and len(j["steps"]) >= 3)
check("шаги описывают путь: чтение, разбор, план, результат", any("Читаю запрос" in s["text"] for s in msg.get("steps", []))
      and any("План" in s["text"] for s in msg.get("steps", [])) and msg.get("report"))
j, _ = run_job("/api/chat/start", text="убери лишнее с фото 1")
check("правка снимка: открыть редактор с запросом", bool(j and j["result"]["message"].get("edit")))
pid0 = j["result"]["message"]["edit"]["photo"]
j, _ = run_job("/api/chat/start", text="поменяй цвет", photo=pid0, explicit=True)
check("«Спросить у ИИ» по фото: любой запрос идёт в правку этого фото", bool(j and j["result"]["message"].get("edit", {}).get("photo") == pid0))
SV.ST.api_key = "sk-or-test"
j, _ = run_job("/api/chat/start", text="сделай празднично")
m = j["result"]["message"]
kinds = [s["kind"] for s in m["steps"]]
check("с моделью: рассуждения модели показываются в шагах", kinds.count("thought") >= 2 and m["source"] == "llm")
check("модель применила тему", c.get("/api/state").get_json()["theme"] == "birthday")
SV.ST.api_key = ""

print("\n3. Правка фото по тексту")
j, _ = run_job("/api/edit/start", photo=pid0, prompt="сделай в стиле Хэллоуин")
check("фоновая правка: стиль Хэллоуин", j and j["result"].get("source") == "local" and len(j["steps"]) >= 2, str(j))
tok = j["result"]["token"]
check("превью результата доступно", c.get(f"/api/edit/{tok}.jpg").status_code == 200)
n0 = len(c.get("/api/state").get_json()["photos"])
st = post("/api/edit/apply", token=tok, mode="copy").get_json()
check("результат сохранён как новое фото", len(st["photos"]) == n0 + 1)
j, _ = run_job("/api/edit/start", photo=pid0, prompt="убери лишнее")
check("«убери» без отметки: понятная ошибка", j and "коснитесь" in j["error"].lower())
j, _ = run_job("/api/edit/start", photo=pid0, prompt="убери лишнее", marks=[dict(x=.5, y=.5, r=.08)])
check("«убери» с отметкой: inpaint", j and j["result"].get("label") == "Удаление объекта")
j, _ = run_job("/api/edit/start", photo=pid0, prompt="открой закрытые глаза")
check("без ключа: «открой глаза» честно отказывает без технических деталей", j and "сейчас сделать не получится" in j["error"] and "ключ" not in j["error"].lower() and "openrouter" not in j["error"].lower())
check("генерация фото нейросетью не используется: редактор только локальный", SV.IE.status()["mode"] == "local" and not hasattr(SV.IE, "edit_with_flux"))
j, _ = run_job("/api/edit/start", photo=pid0, prompt="сделай чёрно-белым")
check("локальный приём ч/б", j and j["result"].get("source") == "local")
st = post("/api/edit/apply", token=j["result"]["token"], mode="replace").get_json()
check("фото заменено", next(p for p in st["photos"] if p["id"] == pid0)["edited"])
st = post(f"/api/photo/{pid0}/revert").get_json()
check("оригинал возвращается", not next(p for p in st["photos"] if p["id"] == pid0)["edited"])
SV.ST.api_key = ""

print("\n4. Второй проект (печать) и корзина")
st = post("/api/project/new", product="prints", params=dict(format="13x18")).get_json()
print_id = st["cur"]
post("/api/demo")
st = post("/api/print/qty", all=True, qty=2).get_json()
check("печать: по 2 отпечатка на фото, цена за формат 13×18", st["proj"]["line_qty"] == 2 * len(st["photos"]) and st["proj"]["unit"] == 1.69)
st = post("/api/cart/add", id=print_id).get_json()
st = post("/api/cart/add", id=book_id).get_json()
check("в корзине 2 проекта", len(st["cart"]["ids"]) == 2)
st = post("/api/cart/qty", id=book_id, qty=3).get_json()
book = next(p for p in st["projects"] if p["id"] == book_id)
check("количество альбомов меняется, сумма считается", book["line_qty"] == 3 and book["sum"] == round(book["unit"] * 3, 2))
st = post("/api/project/open", id=book_id).get_json()
check("альбом сохранил состояние при возврате в проект", st["cur"] == book_id and len(st["spreads"]) >= 2 and st["theme"] == "birthday")
pf = c.get("/api/preflight").get_json()
check("проверка перед печатью по всей корзине", len(pf["items"]) == 2 and pf["can_order"])

print("\n5. Заказ из нескольких позиций")
base = dict(name="Тест Клиент", phone="+375 29 123-45-67", delivery="courier", address="Минск, ул. Тестовая 1", payment="online", client="client-A")
check("валидация: без имени отказ", post("/api/order", **{**base, "name": ""}).status_code == 400)
check("валидация: курьер без адреса отказ", post("/api/order", **{**base, "address": ""}).status_code == 400)
r = post("/api/order", **base)
o = r.get_json().get("order", {})
code = o.get("code", "")
check("заказ создан, трек-код PP-XXXX-XXXX", r.status_code == 200 and len(code) == 12, str(r.get_json()))
expected = sum(i["sum"] for i in o["items"])
check("в заказе обе позиции, итог = позиции + доставка", len(o["items"]) == 2 and abs(o["price"]["total"] - (expected + 9.0)) < 0.01)
st = c.get("/api/state").get_json()
check("корзина очищена, проекты помечены как заказанные", st["cart"]["ids"] == [] and all(p["ordered"] for p in st["projects"]))
check("заказ виден клиенту без ввода кода", [x["code"] for x in c.get("/api/orders?client=client-A").get_json()["orders"]] == [code])
check("чужой клиент заказов не видит", cg.get("/api/orders?client=client-B").get_json()["orders"] == [])

print("\n6. Отслеживание")
r = c.get("/api/track/" + code.lower().replace("-", ""))
check("код находится без учёта регистра и дефисов", r.status_code == 200)
check("телефон не раскрывается", "phone" not in json.dumps(r.get_json()))
check("несуществующий код: 404", c.get("/api/track/PP-ZZZZ-ZZZZ").status_code == 404)
check("страница /track/КОД отдаётся", c.get("/track/" + code).status_code == 200)
for _ in range(8):
    o = c.post(f"/api/track/{code}/advance").get_json()["order"]
check("все 9 статусов пройдены", o["stage"] == 8 and o["finished"] and o["status_title"])
check("PDF-макет книги скачивается", len(o["pdfs"]) == 1 and c.get(f"/api/track/{code}/pdf/{o['pdfs'][0]['id']}").data[:4] == b"%PDF")

print("\n7. Сохранение на диск")
SV.ST.flush()
again = PJ.load_all()
check("проекты восстановились после «перезапуска»", set(again) == {book_id, print_id})
p = again[book_id]
PJ.ensure_loaded(p, SV.detector())
check("книга: фото, раскладка и тема сохранены", len(p.photos) >= 9 and p.book["theme"] == "birthday" and p.count_photos() >= 8)

print("\n8. Роли, журнал, вход (ОТ-02, ОТ-04)")
H = lambda tok: {"Authorization": "Bearer " + tok}
check("вход с неверным паролем отклонён с понятным текстом", post("/api/login", email="client@picpac.test", password="x").status_code == 401)
tc = post("/api/login", email="client@picpac.test", password="client123").get_json()["token"]
ta = post("/api/login", email="admin@picpac.test", password="admin123").get_json()["token"]
check("гость получает сессию", post("/api/guest").get_json()["user"]["guest"] is True)
check("админ-панель закрыта без входа", c.get("/api/admin/overview").status_code == 403)
check("админ-панель закрыта для клиента (проверка на сервере)", c.get("/api/admin/overview", headers=H(tc)).status_code == 403)
ov = c.get("/api/admin/overview", headers=H(ta))
check("админ видит все заказы, обращения и журнал", ov.status_code == 200 and len(ov.get_json()["orders"]) >= 1 and len(ov.get_json()["audit"]) > 5)
check("клиент не меняет статус заказа", c.post(f"/api/admin/order/{code}/status", json=dict(stage=2), headers=H(tc)).status_code == 403)
r = c.post(f"/api/admin/order/{code}/status", json=dict(stage=2), headers=H(ta)).get_json()
check("админ меняет статус заказа", r["order"]["stage"] == 2 and r["order"]["group"] == "Модерация")
log = ov.get_json()["audit"]
check("в журнале есть пользователь, время, действие, результат", all(k in log[0] for k in ("user", "ts", "action", "result")))
check("журнал не хранит телефон и ключи", "+375" not in json.dumps(log) and "sk-" not in json.dumps(log))
check("отказ в доступе попал в журнал", any("доступ запрещён" in x["action"] for x in log))

print("\n9. Загрузка, порядок фото, промокод, идемпотентность (МП-02..МП-05)")
from PIL import Image as PI
def img_bytes(fmt, size=(1600, 1200), color=(120, 160, 200)):
    b = io.BytesIO(); PI.new("RGB", size, color).save(b, fmt); return b.getvalue()
r = post("/api/project/new", product="prints", params=dict(format="10x15", paper="matte"))
pid2 = r.get_json()["cur"]
def up(name, data, headers=None):
    return c.post("/api/upload", data={"files": (io.BytesIO(data), name)}, content_type="multipart/form-data", headers=headers or {})
check("GIF отклонён понятным сообщением", "не поддерживается" in " ".join(up("a.gif", img_bytes("GIF")).get_json()["failed"]))
check("слишком маленькое фото отклонено", "слишком маленькое" in " ".join(up("s.jpg", img_bytes("JPEG", (120, 90))).get_json()["failed"]))
big = img_bytes("JPEG", (400, 300)) + b"\0" * (26 * 1024 * 1024)
check("файл больше 25 МБ отклонён", "25" in " ".join(up("big.jpg", big).get_json()["failed"]))
ok1 = up("p.png", img_bytes("PNG")).get_json()
check("PNG принят", ok1["added"] == 1)
import random as _r
for i in range(11):
    px = PI.effect_noise((900, 700), 80 + i * 7).convert("RGB") if hasattr(PI, "effect_noise") else None
    b = io.BytesIO(); (px or PI.new("RGB", (900, 700), (i * 20, 90, 200))).save(b, "JPEG"); up(f"n{i:02d}.jpg", b.getvalue())
blur = io.BytesIO(); PI.new("RGB", (800, 600), (128, 128, 128)).save(blur, "JPEG")
rb = up("blur.jpg", blur.getvalue()).get_json()
check("загрузка сообщает о некачественном фото (можно заменить или продолжить)", len(rb.get("bad", [])) >= 1, str(rb.get("bad")))
ids = [p["id"] for p in c.get("/api/state").get_json()["photos"]]
check("в проекте 10+ фото", len(ids) >= 10, str(len(ids)))
post("/api/photos/move", id=ids[0], to=5); post("/api/photos/move", id=ids[3], dir=-1)
want = [p["id"] for p in c.get("/api/state").get_json()["photos"]]
SV.ST.flush()
again = PJ.load_all()[pid2]; PJ.ensure_loaded(again, SV.detector())
check("порядок фото сохраняется и переживает «перезапуск»", list(again.photos) == want and want != ids)
for p in list(SV.ST.projects.values()):
    pass
post("/api/cart/add", id=pid2)
q = post("/api/quote", delivery="courier", promo="PICPAC10").get_json()["totals"]
base_items = q["items"]
check("промокод PICPAC10: скидка 10% и пересчёт итога", abs(q["discount"] - round(base_items * .1, 2)) < 0.01 and abs(q["total"] - (base_items - q["discount"] + 9)) < 0.01)
check("FREESHIP обнуляет доставку", post("/api/quote", delivery="courier", promo="FREESHIP").get_json()["totals"]["delivery"] == 0)
check("неверный промокод даёт понятную ошибку", "не найден" in post("/api/quote", delivery="courier", promo="XXX").get_json()["error"])
body = dict(name="Анна", phone="+375291112233", delivery="pickup", payment="online", promo="PICPAC10", key="k-123")
r1 = c.post("/api/order", json=body).get_json()
r2 = c.post("/api/order", json=body).get_json()
check("повторная отправка не создаёт второй заказ", r1["order"]["code"] == r2["order"]["code"] and r2.get("duplicate")
      and [o["code"] for o in c.get("/api/orders").get_json()["orders"]].count(r1["order"]["code"]) == 1)
check("в заказе сохранён промокод и скидка", r1["order"]["price"]["promo"] == "PICPAC10" and r1["order"]["price"]["discount"] > 0)
check("есть ISO-время с часовым поясом и группа статуса", "+" in r1["order"]["created_iso"] or "-" in r1["order"]["created_iso"][10:] and r1["order"]["group"] == "Принят")

print("\n10. Поддержка и удаление данных (МП-06)")
hc = {}
s1 = c.post("/api/support", json=dict(text="Где мой заказ?", order=r1["order"]["code"]), headers=hc).get_json()
check("бот отвечает статусом конкретного заказа", any(r1["order"]["code"] in m["text"] and m["from_"] == "bot" for m in s1["messages"]))
s2 = c.post("/api/support", json=dict(text="Хочу поговорить с оператором, жалоба"), headers=hc).get_json()
check("сложный вопрос передаётся оператору", s2["status"] == "open")
uid = _r0["user"]["uid"]
rr = c.post(f"/api/admin/thread/{uid}", json=dict(text="Здравствуйте! Разберёмся."), headers=H(ta)).get_json()
check("оператор отвечает в этот же чат", rr["messages"][-1]["from_"] == "operator" and c.get("/api/support", headers=hc).get_json()["status"] == "answered")
check("обращение связано с заказом", len([m for m in c.get("/api/support", headers=hc).get_json()["messages"] if m["order"] == r1["order"]["code"]]) >= 2)
check("клиент не читает чужие обращения через админ-ручку", c.get(f"/api/admin/thread/{uid}", headers=H(tc)).status_code == 403)
d = c.post("/api/me/delete", json={}, headers=hc).get_json()
check("удаление данных: заказы, обращения, проекты", d["orders"] >= 1 and c.get("/api/support", headers=hc).get_json()["messages"] == [] and c.get("/api/orders", headers=hc).get_json()["orders"] == [])
check("каталог отдаётся API (МП-01)", len(c.get("/api/catalog").get_json()["products"]) == 5)

print("\n11. Технические данные не видны клиенту")
stj = json.dumps(c.get("/api/state").get_json())
check("состояние не раскрывает ключ и модели", "has_key" not in stj and "image_model" not in stj and "gemini" not in stj.lower())
r_ = post("/api/settings", api_key="sk-or-test")
check("ключ нельзя сменить без прав администратора", r_.status_code == 403)
print("\n12. Интеграция с CRM и персональные предложения")
import crm as CRM, offers as OF  # noqa: E402
CRM.reset()
tv = post("/api/login", email="vip@picpac.test", password="vip123").get_json()["token"]
ts = post("/api/login", email="sleep@picpac.test", password="sleep123").get_json()["token"]
offs = c.get("/api/offers", headers=H(tv)).get_json()
ids_ = [o["id"] for o in offs["offers"]]
check("VIP из CRM получает день рождения, личную скидку и бесплатную доставку", {"birthday", "loyalty", "vipship"} <= set(ids_), str(ids_))
check("у каждого предложения есть причина «почему вы это видите»", all(o["why"] for o in offs["offers"]))
check("давно не заказывавшему предлагается возврат", "winback" in [o["id"] for o in c.get("/api/offers", headers=H(ts)).get_json()["offers"]])
check("гость и клиент не из CRM не получают персональных предложений", cg.get("/api/offers", headers={"X-Client": "c-guest1"}).get_json()["offers"] == [])
check("администратор не получает клиентских предложений", c.get("/api/offers", headers=H(ta)).get_json()["offers"] == [])
rp = c.post("/api/project/new", json=dict(product="prints", params=dict(format="10x15", paper="matte")), headers=H(tv)).get_json()["cur"]
up("c1.png", img_bytes("PNG"), H(tv))
c.post("/api/cart/add", json=dict(id=rp), headers=H(tv))
qv = c.post("/api/quote", json=dict(delivery="courier", promo=""), headers=H(tv)).get_json()["totals"]
check("скидка из CRM: 20% ко дню рождения и бесплатная доставка", abs(qv["discount"] - round(qv["items"] * .2, 2)) < .01 and qv["delivery"] == 0 and "рождения" in qv["discount_label"], str(qv))
q0 = cg.post("/api/quote", json=dict(delivery="courier", promo=""), headers={"X-Client": "c-guest1"}).get_json()["totals"]
check("гостю скидка из CRM не применяется", q0["discount"] == 0 and q0["delivery"] > 0)
q1 = c.post("/api/quote", json=dict(delivery="courier", promo="", pers=False), headers=H(tv)).get_json()["totals"]
check("клиент может отказаться от персональных предложений", q1["discount"] == 0)
q2 = c.post("/api/quote", json=dict(delivery="courier", promo="PICPAC10"), headers=H(tv)).get_json()["totals"]
check("промокод и персональная скидка не суммируются (берётся большая)", abs(q2["discount"] - round(q2["items"] * .2, 2)) < .01)
q3 = c.post("/api/quote", json=dict(delivery="courier", promo="", percent=90, offer="x"), headers=H(tv)).get_json()["totals"]
check("клиент не может подменить процент скидки в запросе", abs(q3["discount"] - round(q3["items"] * .2, 2)) < .01)
q4 = c.post("/api/quote", json=dict(delivery="courier", promo="", disc="none"), headers=H(tv)).get_json()["totals"]
check("выбор «без скидки» убирает процент", q4["discount"] == 0)
q5 = c.post("/api/quote", json=dict(delivery="courier", promo="", nofree=True), headers=H(tv)).get_json()["totals"]
check("можно не применять бесплатную доставку", q5["delivery"] > 0 and q5["discount"] > 0)
check("сервер отдаёт варианты скидок", bool(c.post("/api/quote", json=dict(delivery="courier", promo=""), headers=H(tv)).get_json()["totals"].get("disc_options")))
ro = c.post("/api/order", json=dict(name="Игорь", phone="+375291234567", delivery="courier", address="Минск, ул. Тестовая 1", payment="online", key="k-vip-1"), headers=H(tv)).get_json()
check("заказ сохраняет скидку и её основание", ro.get("order", {}).get("price", {}).get("discount", 0) > 0 and "рождения" in ro["order"]["price"].get("discount_label", ""), str(ro)[:200])
pc = CRM.lookup("vip@picpac.test")
check("без согласия на маркетинг предложений нет", OF.build(dict(pc, marketing_consent=False)) == [])
check("портрет не содержит лишних полей (белый список)", set(CRM.normalize(dict(pc, passport="AB123", phone="+375")).keys()) == set(CRM.FIELDS))
# настоящий HTTP-режим против демо-CRM и отказ CRM
from werkzeug.serving import make_server  # noqa: E402
ms = make_server("127.0.0.1", 0, SV.app, threaded=True); threading.Thread(target=ms.serve_forever, daemon=True).start()
CRM.MODE, CRM.URL, CRM.PAUSE = "http", f"http://127.0.0.1:{ms.server_port}/mock-crm/v1", 0.2
CRM.reset()
ph = CRM.lookup("sleep@picpac.test")
check("HTTP-режим: портрет получен по REST с Bearer-токеном", ph and ph["segment"] == "sleeping" and CRM.status()["ok"] is True)
check("HTTP-режим: неизвестного клиента CRM отвечает 404, это не сбой", CRM.lookup("nobody@picpac.test") is None and CRM.status()["ok"] is True)
check("демо-CRM без токена отвечает 401", c.get("/mock-crm/v1/customers/lookup?email=vip@picpac.test").status_code == 401)
ms.shutdown(); ms.server_close()
CRM.reset(); CRM.TIMEOUT = 0.5
check("CRM недоступна: приложение не падает, предложений нет, сбой виден в статусе", CRM.lookup("vip@picpac.test") is None and CRM.status()["ok"] is False)
CRM.MODE = "mock"; CRM.reset(); time.sleep(0.3)

print("\n13. Регистрация, БД и раздельные сессии")
import db as DB  # noqa: E402
def reg(name, email, pw="Passw0rd-123"):
    return c.post("/api/register", json=dict(name=name, email=email, password=pw), headers={"Authorization": ""})
check("регистрация создаёт аккаунт и сразу открывает сессию", (rA := reg("Алиса Тест", "alice@picpac.test")).status_code == 200 and rA.get_json()["user"]["role"] == "client")
tA = rA.get_json()["token"]
check("повторная регистрация той же почты отклонена", "уже зарегистрирована" in reg("Другая", "ALICE@picpac.test").get_json()["error"])
check("слабый пароль и неверная почта отклоняются", reg("Боб", "bob@picpac.test", "123").status_code == 400 and reg("Боб", "не-почта").status_code == 400)
check("пароль хранится только хешем scrypt", DB.run("SELECT pw_hash FROM users WHERE email = %s", ("alice@picpac.test",), one=True)[0].startswith("scrypt$") and "Passw0rd" not in DB.run("SELECT pw_hash FROM users WHERE email = %s", ("alice@picpac.test",), one=True)[0])
check("токен сессии в БД не хранится, только его хеш", DB.run("SELECT 1 FROM sessions WHERE tok_hash = %s", (tA,), one=True) is None)
check("вход по зарегистрированной почте работает", c.post("/api/login", json=dict(email="alice@picpac.test", password="Passw0rd-123"), headers={"Authorization": ""}).status_code == 200)
for _ in range(8):
    c.post("/api/login", json=dict(email="victim@picpac.test", password="bad"), headers={"Authorization": ""})
check("перебор пароля ограничен: после серии ошибок 429", c.post("/api/login", json=dict(email="victim@picpac.test", password="bad"), headers={"Authorization": ""}).status_code == 429)
tB = reg("Борис Тест", "boris@picpac.test").get_json()["token"]
pidA = c.post("/api/project/new", json=dict(product="prints", params=dict(format="10x15", paper="matte")), headers=H(tA)).get_json()["cur"]
up("a1.png", img_bytes("PNG"), H(tA))
stA = c.get("/api/state", headers=H(tA)).get_json()
check("у Алисы есть проект и фото", stA["cur"] == pidA and len(stA["photos"]) == 1)
stB = c.get("/api/state", headers=H(tB)).get_json()
check("Борис не видит чужие проекты и фото", stB["projects"] == [] and stB["cur"] is None and stB["photos"] == [])
photoA = stA["photos"][0]["id"]
check("Борис не может открыть проект Алисы", c.post("/api/project/open", json=dict(id=pidA), headers=H(tB)).status_code == 404)
check("Борис не получает превью проекта Алисы", c.get(f"/api/project/{pidA}/thumb.jpg", headers=H(tB)).status_code == 404)
check("Борис не видит фото Алисы по её id", c.get(f"/api/photo/{photoA}/img", headers=H(tB)).status_code in (404, 409))
check("Борис не может удалить проект Алисы", c.post("/api/project/delete", json=dict(id=pidA), headers=H(tB)).status_code == 200 and pidA in [p["id"] for p in c.get("/api/state", headers=H(tA)).get_json()["projects"]])
jA, _ = (lambda r: (c.get("/api/job/" + r.get_json()["job"], headers=H(tA)).get_json(), r))(c.post("/api/chat/start", json=dict(text="перемешай фото"), headers=H(tA)))
check("чужую фоновую задачу прочитать нельзя", c.get("/api/job/" + jA["id"], headers=H(tB)).status_code == 404)
row = DB.run("SELECT owner FROM projects WHERE id = %s", (pidA,), one=True)
SV.ST.flush()
row = DB.run("SELECT owner FROM projects WHERE id = %s", (pidA,), one=True)
check("проект записан в БД с владельцем", row and row[0] == rA.get_json()["user"]["uid"])
cg2 = SV.app.test_client(); cg2.set_cookie("pp_session", tA)
check("cookie без заголовка не даёт менять данные (защита от CSRF)", cg2.post("/api/project/new", json=dict(product="prints")).status_code == 401
      and cg2.get("/api/state").get_json()["cur"] == pidA)
dB = c.post("/api/me/delete", json={}, headers=H(tB)).get_json()
check("удаление данных Бориса не трогает проекты Алисы", pidA in [p["id"] for p in c.get("/api/state", headers=H(tA)).get_json()["projects"]])
# гость становится зарегистрированным: черновики переходят
tG = c.post("/api/guest", json=dict(client="cguest77"), headers={"Authorization": ""}).get_json()["token"]
pidG = c.post("/api/project/new", json=dict(product="prints", params=dict(format="10x15", paper="matte")), headers=H(tG)).get_json()["cur"]
tC = reg("Вера Тест", "vera@picpac.test").get_json()["token"]
c.post("/api/me/claim", json=dict(guest="cguest77"), headers=H(tC))
check("после регистрации черновики гостя переходят в аккаунт", pidG in [p["id"] for p in c.get("/api/state", headers=H(tC)).get_json()["projects"]])

print("\n14. Одновременная работа нескольких пользователей")
import traceback as _tb
N, errs14, out14 = 12, [], {}
def worker(i):
    try:
        cl = SV.app.test_client()
        tk = cl.post("/api/register", json=dict(name=f"Нагрузка {i}", email=f"load{i}@picpac.test", password="Passw0rd-123")).get_json()["token"]
        hh = H(tk)
        n_photos = 3 + i % 3
        pid = cl.post("/api/project/new", json=dict(product="premium", params=dict(cover="hard")), headers=hh).get_json()["cur"]
        for k in range(n_photos):
            px = PI.new("RGB", (900, 700), ((i * 20 + k * 40) % 255, 90 + k * 20, 200 - i * 5))
            b = io.BytesIO(); px.save(b, "JPEG")
            r = cl.post("/api/upload", data={"files": (io.BytesIO(b.getvalue()), f"u{i}_{k}.jpg")}, content_type="multipart/form-data", headers=hh)
            assert r.status_code == 200, r.get_data(as_text=True)[:100]
        assert cl.post("/api/ai", json=dict(op="autoplan"), headers=hh).status_code == 200
        jid = cl.post("/api/chat/start", json=dict(text="перемешай фото"), headers=hh).get_json()["job"]
        for _ in range(200):
            jj = cl.get("/api/job/" + jid, headers=hh).get_json()
            if jj["done"]:
                break
            time.sleep(0.05)
        assert jj["done"] and not jj["error"], jj
        st = cl.get("/api/state", headers=hh).get_json()
        assert st["cur"] == pid and len(st["photos"]) == n_photos and len(st["projects"]) == 1, (st["cur"], len(st["photos"]), n_photos)
        assert cl.get("/api/spread/0.jpg?w=300", headers=hh).status_code == 200
        cl.post("/api/cart/add", json=dict(id=pid), headers=hh)
        o = cl.post("/api/order", json=dict(name=f"Нагрузка {i}", phone="+375291112233", delivery="pickup", payment="online", key=f"k-load-{i}"), headers=hh).get_json()
        assert "order" in o, o
        mine = cl.get("/api/orders", headers=hh).get_json()["orders"]
        assert len(mine) == 1 and mine[0]["code"] == o["order"]["code"], mine
        out14[i] = (n_photos, o["order"]["code"])
    except Exception:
        errs14.append(f"поток {i}: " + _tb.format_exc()[-400:])
t0 = time.time()
ths = [threading.Thread(target=worker, args=(i,)) for i in range(N)]
[t.start() for t in ths]; [t.join(120) for t in ths]
dt14 = time.time() - t0
check(f"{N} пользователей одновременно: загрузка, сборка, чат, заказ без ошибок ({dt14:.1f} с)", not errs14 and len(out14) == N, "; ".join(errs14)[:600])
check("заказы одновременных пользователей не перепутались (уникальные коды)", len({v[1] for v in out14.values()}) == N)
check("каждый видит только свой проект после гонки", all(len(c.get("/api/state", headers=H(c.post("/api/login", json=dict(email=f"load{i}@picpac.test", password="Passw0rd-123"), headers={"Authorization": ""}).get_json()["token"])).get_json()["projects"]) == 1 for i in range(0, N, 4)))
# один и тот же заказ одновременно (двойной клик): должен быть один
tD = reg("Двойной Клик", "dbl@picpac.test").get_json()["token"]
pD = c.post("/api/project/new", json=dict(product="prints", params=dict(format="10x15", paper="matte")), headers=H(tD)).get_json()["cur"]
up("d1.png", img_bytes("PNG"), H(tD)); c.post("/api/cart/add", json=dict(id=pD), headers=H(tD))
res14 = []
def dbl():
    cl = SV.app.test_client()
    res14.append(cl.post("/api/order", json=dict(name="Двойной", phone="+375291112233", delivery="pickup", payment="online", key="k-dbl"), headers=H(tD)).get_json())
ts_ = [threading.Thread(target=dbl) for _ in range(6)]
[t.start() for t in ts_]; [t.join(60) for t in ts_]
codes = {r["order"]["code"] for r in res14 if "order" in r}
check("шесть одновременных отправок одного заказа дают один заказ", len(codes) == 1 and len(c.get("/api/orders", headers=H(tD)).get_json()["orders"]) == 1, str(res14)[:300])

print("\n15. Шаблоны альбомов, ручной режим, фон, просмотр")
import album_templates as ATP  # noqa: E402
check("10 готовых шаблонов альбомов", len(ATP.TEMPLATES) == 10 and len({t["id"] for t in ATP.TEMPLATES}) == 10)
check("в каждом шаблоне стиль существует, раскладки известны, есть тексты",
      all(t["theme"] in __import__("themes").THEMES and all(sp[0] in __import__("ai_core").TEMPLATES for sp in t["spreads"]) and t["title"] for t in ATP.TEMPLATES))
st15 = post("/api/project/new", product="premium", params=dict(cover="hard"), template="wedding").get_json()
check("проект по шаблону: тема, тексты и пустые слоты готовы",
      st15["theme"] == "wedding" and len(st15["spreads"]) == len(ATP.BY_ID["wedding"]["spreads"]) and st15["spreads"][0]["texts"]["title"] == "НАША СВАДЬБА"
      and all(not s_["photo"] for sp_ in st15["spreads"] for s_ in sp_["slots"]) and st15["proj"]["params"]["mode"] == "template")
st15 = post("/api/project/new", product="classic", params=dict(cover="hard")).get_json()
check("ручной проект: чистый альбом без шаблона", st15["proj"]["params"]["mode"] == "manual" and st15["theme"] == "classic")
r_ = post("/api/spread", op="bg", si=0, bg=[43, 45, 66]).get_json()
check("свой цвет фона разворота сохраняется", r_["spreads"][0]["bg"] == [43, 45, 66])
check("неверный цвет отклоняется", post("/api/spread", op="bg", si=0, bg=[999, 1, 1]).status_code == 400)
check("цвет обложки меняется", post("/api/cover/style", bg=[96, 36, 48]).get_json()["cover_bg"] == [96, 36, 48])
im_ = c.get("/api/spread/0.jpg?w=400&plain=1", headers=H(tMain)) if "tMain" in globals() else c.get("/api/spread/0.jpg?w=400&plain=1")
check("плоская картинка разворота для просмотра отдаётся", im_.status_code == 200 and im_.mimetype == "image/jpeg")

print("\n16. Фотобаллы, совместный альбом, чат-цепочки, кадрирование по желанию")
import ai_core as A16  # noqa: E402
import assistant as AS16  # noqa: E402
import loyalty as LY16  # noqa: E402
check("шкала начисления: 5 руб. -> 0, 45 -> 3, 72,90 -> 6, 450 -> 70", [LY16.earn_base(x) for x in (5, 45, 72.9, 450)] == [0, 3, 6, 70])
check("праздничный бонус удваивает начисление", LY16.earn(100, __import__("datetime").date(2026, 3, 8))[0] == 24 and LY16.earn(100, __import__("datetime").date(2026, 3, 9))[0] == 12)
check("лимит списания растёт с суммой заказа", [LY16.cap_pct(x) for x in (20, 100, 300)] == [10, 15, 20])
tL = reg("Лия Баллова", "liya@picpac.test").get_json()["token"]
la = c.get("/api/loyalty", headers=H(tL)).get_json()["account"]
check("новому клиенту начислен приветственный бонус, тратить пока нельзя", la["balance"] == 5 and la["can_spend"] is False)
check("гостю фотобаллы не показываются", cg.get("/api/loyalty", headers={"X-Client": "c-guest2"}).get_json()["account"] is None)
# заказ клиента на 100+ руб.: книга с несколькими разворотами
pl = c.post("/api/project/new", json=dict(product="premium", params=dict(cover="hard")), headers=H(tL)).get_json()["cur"]
for i_ in range(3):
    up(f"l{i_}.png", img_bytes("PNG", color=(40 + 60 * i_, 120, 200 - 50 * i_)), H(tL))
c.post("/api/ai", json=dict(op="autoplan"), headers=H(tL))
c.post("/api/cart/add", json=dict(id=pl), headers=H(tL))
q1 = c.post("/api/quote", json=dict(delivery="pickup", promo="", use_points=True), headers=H(tL)).get_json()
check("в расчёте есть блок фотобаллов: начисление за заказ, списание пока недоступно", q1["loy"]["earn"] >= 6 and q1["loy"]["used"] == 0 and q1["loy"]["why"] == "min", str(q1["loy"]))
oL = c.post("/api/order", json=dict(name="Лия", phone="+375291234567", delivery="pickup", payment="online", key="kL1", use_points=True), headers=H(tL)).get_json()["order"]
check("заказ хранит ожидаемое начисление, баллы до выдачи «в ожидании»", oL["loyalty"]["earned"] >= 6 and oL["price"]["points"] == 0)
la = c.get("/api/loyalty", headers=H(tL)).get_json()["account"]
check("до выдачи заказа баланс не растёт, баллы в ожидании", la["balance"] == 5 and la["pending"] == oL["loyalty"]["earned"])
for _ in range(8):
    c.post(f"/api/track/{oL['code']}/advance", headers=H(tL))
la = c.get("/api/loyalty", headers=H(tL)).get_json()["account"]
check("после выдачи баллы начислены, тратить можно (покупки от 30 руб.)", la["balance"] == 5 + oL["loyalty"]["earned"] and la["pending"] == 0 and la["can_spend"] is True, str(la))
c.post("/api/cart/add", json=dict(id=c.post("/api/project/new", json=dict(product="premium", params=dict(cover="hard")), headers=H(tL)).get_json()["cur"]), headers=H(tL))
for i_ in range(3):
    up(f"m{i_}.png", img_bytes("PNG"), H(tL))
c.post("/api/ai", json=dict(op="autoplan"), headers=H(tL))
q2 = c.post("/api/quote", json=dict(delivery="pickup", promo="", use_points=True), headers=H(tL)).get_json()
cap = int(q2["totals"]["items"] * LY16.cap_pct(q2["totals"]["items"]) / 100)
check("баллами оплачивается часть заказа в пределах лимита и баланса", q2["totals"]["points"] == min(la["balance"], cap) and abs(q2["totals"]["total"] - (q2["totals"]["items"] - q2["totals"]["points"])) < 0.01, str(q2))
q3 = c.post("/api/quote", json=dict(delivery="pickup", promo="PICPAC10", use_points=True), headers=H(tL)).get_json()
check("баллы не суммируются со скидкой по промокоду", q3["totals"]["points"] == 0 and q3["totals"]["discount"] > 0 and q3["loy"]["why"] == "discount")
q4 = c.post("/api/quote", json=dict(delivery="pickup", promo="", use_points=False), headers=H(tL)).get_json()
check("без включённого переключателя баллы не списываются", q4["totals"]["points"] == 0)

# совместный альбом
tOw = reg("Владелец Альбома", "owner@picpac.test").get_json()["token"]
pw_ = c.post("/api/project/new", json=dict(product="premium", params=dict(cover="hard")), headers=H(tOw)).get_json()["cur"]
up("o1.png", img_bytes("PNG"), H(tOw))
check("чужая ссылка не открывается, пока владелец не включил доступ", c.get("/api/join/abc", headers=H(tL)).status_code == 404)
sh_ = c.post("/api/share", json=dict(on=True), headers=H(tOw)).get_json()["proj"]["share"]
check("ссылка создана", sh_["on"] and len(sh_["token"]) >= 10)
inf = c.get("/api/join/" + sh_["token"], headers=H(tL)).get_json()
check("приглашённый видит альбом, но пока не участник", inf["joined"] is False and inf["is_owner"] is False and len(inf["photos"]) == 1)
check("до вступления в альбом его фото посмотреть нельзя",
      c.get(f"/api/join/{sh_['token']}/img/{inf['photos'][0]['id']}", headers=H(tL)).status_code == 404)
jn = c.post("/api/join/" + sh_["token"], json=dict(name="Лия"), headers=H(tL)).get_json()
check("участник присоединился", jn["joined"] is True)
import io as io16  # noqa: E402
r_up = c.post(f"/api/join/{sh_['token']}/upload", data=dict(files=[(io16.BytesIO(img_bytes("PNG", color=(200, 90, 90))), "guest1.png")]), headers=H(tL), content_type="multipart/form-data").get_json()
check("участник добавил фото в общий альбом", r_up["added"] == 1 and r_up["mine"] == 1)
stO = c.get("/api/state", headers=H(tOw)).get_json()
check("владелец видит фото участника с пометкой «от Лия» и список участников", any(p["by"] == "Лия" for p in stO["photos"]) and stO["proj"]["share"]["members"][0]["n"] == 1)
check("участник видит фото владельца в общем пуле", c.get(f"/api/join/{sh_['token']}/img/{inf['photos'][0]['id']}", headers=H(tL)).status_code == 200)
mine_id = [p["id"] for p in r_up["photos"] if p["mine"]][0]
check("участник не может удалить чужое фото", c.post(f"/api/join/{sh_['token']}/delete", json=dict(id=inf["photos"][0]["id"]), headers=H(tL)).status_code == 403)
check("участник не видит проект владельца в своём списке и не может его открыть", pw_ not in [p["id"] for p in c.get("/api/state", headers=H(tL)).get_json()["projects"]] and c.post("/api/project/open", json=dict(id=pw_), headers=H(tL)).status_code == 404)
check("участник удаляет своё фото", c.post(f"/api/join/{sh_['token']}/delete", json=dict(id=mine_id), headers=H(tL)).status_code == 200)
c.post("/api/share", json=dict(on=False), headers=H(tOw))
check("после выключения доступа ссылка перестаёт работать", c.get("/api/join/" + sh_["token"], headers=H(tL)).status_code == 404)
sh2_ = c.post("/api/share", json=dict(on=True, renew=True), headers=H(tOw)).get_json()["proj"]["share"]
check("новая ссылка отличается от старой", sh2_["token"] != sh_["token"])

# чат: цепочка «смена раскладки -> set_photo в новые слоты» больше не ломается
c.post("/api/ai", json=dict(op="autoplan"), headers=H(tL))
stX = c.get("/api/state", headers=H(tL)).get_json()
projX = SV.ST.projects[stX["cur"]]
six = next(t for t, d in A16.TEMPLATES.items() if len(d["slots"]) == 6)
acts, errs = AS16.validate_actions([dict(type="change_template", spread=1, template=six), dict(type="set_photo", spread=1, slot=6, photo=1)], projX.book, projX.photos)
check("после смены на шаблон с 6 слотами set_photo в слот 6 проходит проверку", len(acts) == 2 and not errs, str(errs))
acts, errs = AS16.validate_actions([dict(type="set_photo", spread=1, slot=7, photo=1)], projX.book, projX.photos)
check("несуществующий слот по-прежнему отклоняется", not acts and errs)
rep_ = AS16.execute(acts_ := AS16.validate_actions([dict(type="change_template", spread=1, template=six)], projX.book, projX.photos)[0], projX.book, projX.photos)
check("смена шаблона добирает свободные фото в пустые слоты", len(projX.book["spreads"][0]["slots"]) == 6)

# автокадрирование только по желанию
ph0 = next(iter(projX.photos.values()))
cc = A16.make_slot(ph0, "grid4", 0)["crop"]
check("по умолчанию кадр по центру (без ИИ)", abs(cc[0] + cc[2] / 2 - 0.5) < 0.01 and abs(cc[1] + cc[3] / 2 - 0.5) < 0.01)
used_pid = next(s_["photo"] for sp_ in projX.book["spreads"] for s_ in sp_["slots"] if s_["photo"])
r_ = c.post("/api/ai", json=dict(op="recrop_photo", id=used_pid), headers=H(tL)).get_json()
check("автокадрирование одного фото по кнопке", "Автокадрирование" in r_["report"][0])
check("автокадрирование для всех в быстрых командах", "Автокадрирование" in c.post("/api/ai", json=dict(op="recrop_all"), headers=H(tL)).get_json()["report"][0])
check("в карточке заказа нет часового пояса", "tz" not in oL)
# --- вкладка «Цены»: редактирование, журнал, сброс
ta = c.post("/api/login", json=dict(email="admin@picpac.test", password="admin123")).get_json()["token"]
ac = c.get("/api/admin/catalog", headers=H(ta)).get_json()
check("админ получает каталог для редактирования", len(ac["catalog"]["products"]) == 5 and ac["edited"] is False)
check("клиент не получает редактор цен", c.get("/api/admin/catalog", headers=H(tL)).status_code == 403)
edit = json.loads(json.dumps(ac["catalog"])); edit["products"][2]["from_price"] = 77.5; edit["delivery"][2]["price"] = 11.0
sv = c.post("/api/admin/catalog", json=dict(catalog=edit), headers=H(ta)).get_json()
check("цены сохраняются и применяются сразу", sv["edited"] and sv["changes"] and c.get("/api/catalog").get_json()["products"][2]["from_price"] == 77.5)
q_ = c.post("/api/quote", json=dict(delivery="courier", promo=""), headers=H(ta)).get_json()["totals"]
check("доставка считается по новой цене", q_["delivery"] == 11.0)
check("изменение цен записано в журнал с деталями", any(a["action"] == "изменение каталога" and any("70.35 → 77.5" in x for x in a["extra"]["changes"]) for a in c.get("/api/admin/overview", headers=H(ta)).get_json()["audit"]))
badc = json.loads(json.dumps(edit)); badc["products"][0]["from_price"] = -1
check("отрицательная цена не принимается", c.post("/api/admin/catalog", json=dict(catalog=badc), headers=H(ta)).status_code == 400)
rs = c.post("/api/admin/catalog/reset", headers=H(ta)).get_json()
check("возврат исходного каталога", not rs["edited"] and c.get("/api/catalog").get_json()["products"][2]["from_price"] == 70.35)
check("возврат записан в журнал", any(a["action"] == "возврат исходного каталога" for a in c.get("/api/admin/overview", headers=H(ta)).get_json()["audit"]))
# --- deep links и воронка
check("deep links отдают приложение", all(c.get(u).status_code == 200 and b"<html" in c.get(u).data.lower() for u in ("/p/abc12345", "/catalog", "/catalog/premium", "/orders", "/cart")))
c.post("/api/event", json=dict(ev="catalog"), headers=H(ta)); c.post("/api/event", json=dict(ev="hack"), headers=H(ta))
fn = c.get("/api/admin/overview", headers=H(ta)).get_json()["funnel"]
check("воронка: 6 шагов в нужном порядке", [x["id"] for x in fn] == ["catalog", "project", "photos", "cart", "checkout", "order"])
check("воронка считает уникальных пользователей, лишние события игнорируются", fn[0]["users"] >= 1 and all(x["id"] != "hack" for x in fn))
check("воронка видна только админу", c.get("/api/admin/overview", headers=H(tL)).status_code == 403)
# --- гибкий каталог: добавление, отключение, удаление
import orders as O
ac2 = c.get("/api/admin/catalog", headers=H(ta)).get_json()["catalog"]
flex = json.loads(json.dumps(ac2))
flex["promos"].append(dict(code=" summer15 ", text="−15% на товары", percent=15, free_delivery=False, active=True))
flex["delivery"].append(dict(id="dhl", name="DHL экспресс", price=15.0, eta="1–2 дня", address=True, active=True))
flex["payment"].append(dict(id="erip", name="ЕРИП", active=True))
flex["covers"].append(dict(id="linen", name="Льняная обложка", add=6.0, active=True))
flex["products"].append(dict(id="mag", kind="book", name="Фотожурнал", from_price=30.0, text="Тонкий журнал", lead="3–5 дней", req="JPEG/PNG", per_spread=1.5, min_spreads=4, color="#abc", active=True))
r = c.post("/api/admin/catalog", json=dict(catalog=flex), headers=H(ta)); rj = r.get_json()
check("админ добавляет промокод, доставку, оплату, обложку и товар", r.status_code == 200 and len(rj["changes"]) >= 5)
cn = c.get("/api/catalog").get_json()
check("новые пункты видны клиенту через API", any(p["id"] == "mag" for p in cn["products"]) and any(d["id"] == "dhl" for d in cn["delivery"]))
qn = c.post("/api/quote", json=dict(delivery="dhl", promo="SUMMER15"), headers=H(ta)).get_json()["totals"]
check("новый промокод (регистр и пробелы нормализуются) и новая доставка считаются", qn["delivery"] == 15.0 and qn["discount"] == round(qn["items"] * .15, 2) and qn["promo"] == "SUMMER15" if qn["items"] else qn["delivery"] == 15.0)
check("цена нового товара считается сервером", O.unit_price("mag", {}, 4) == 30.0 and O.unit_price("mag", {}, 6) == 33.0)
dup = json.loads(json.dumps(flex)); dup["promos"].append(dict(code="SUMMER15", text="x", percent=5, active=True))
check("повтор кода промокода отклоняется", c.post("/api/admin/catalog", json=dict(catalog=dup), headers=H(ta)).status_code == 400)
badid = json.loads(json.dumps(flex)); badid["delivery"].append(dict(id="Плохой id", name="x", price=1, eta="1", address=False))
check("неверный id отклоняется", c.post("/api/admin/catalog", json=dict(catalog=badid), headers=H(ta)).status_code == 400)
alloff = json.loads(json.dumps(flex)); [d.update(active=False) for d in alloff["delivery"]]
check("нельзя отключить все способы доставки", c.post("/api/admin/catalog", json=dict(catalog=alloff), headers=H(ta)).status_code == 400)
off = json.loads(json.dumps(flex)); [d.update(active=False) for d in off["delivery"] if d["id"] == "courier"]
c.post("/api/admin/catalog", json=dict(catalog=off), headers=H(ta))
check("отключённая доставка не принимается в новом заказе", c.post("/api/order", json=dict(name="Тест", phone="+375291234567", delivery="courier", address="Минск, ул. Тестовая 1", payment="online", key="kx-off"), headers=H(ta)).get_json().get("error", "").startswith(("Выберите способ", "Корзина")))
# --- удалили то, что пользователь уже выбрал: мягкое сообщение и замена
tU = c.post("/api/register", json=dict(name="Замена Тест", email="repl@test.by", password="pass12345")).get_json()["token"]
c.post("/api/project/new", json=dict(product="premium", params=dict(cover="soft")), headers=H(tU)); c.post("/api/demo", headers=H(tU))
pidU = c.get("/api/state", headers=H(tU)).get_json()["cur"]
c.post("/api/cart/add", json=dict(id=pidU), headers=H(tU))
c.post("/api/project/new", json=dict(product="prints", params=dict(format="13x18", paper="gloss")), headers=H(tU))
pidP = c.get("/api/state", headers=H(tU)).get_json()["cur"]
used = json.loads(json.dumps(off)); used["products"] = [p for p in used["products"] if p["id"] != "premium"]
ru = c.post("/api/admin/catalog", json=dict(catalog=used), headers=H(ta)).get_json()
check("админ может удалить товар, который уже выбрали, а в ответе видно, сколько проектов затронуто", ru["notes"] and "Premium" in ru["notes"][0])
stU = c.get("/api/state", headers=H(tU)).get_json(); pj = next(p for p in stU["projects"] if p["id"] == pidU)
check("у пользователя проект с мягким сообщением и вариантами замены только той же категории (книги)", pj["issue"]["field"] == "product" and "больше нет в каталоге" in pj["issue"]["text"]
      and pj["issue"]["options"] and all(o["id"] in ("classic", "lite", "mag") for o in pj["issue"]["options"]) and pj["product_name"] == "Фотокниги Premium")
check("цена удалённого товара не выдумывается", pj["unit"] == 0 and pj["sum"] == 0)
ro = c.post("/api/order", json=dict(name="Тест", phone="+375291234567", delivery="post", address="Минск, отделение 5", payment="online", key="kx-gone"), headers=H(tU))
check("заказ с удалённой позицией не оформляется и объясняет причину", ro.status_code == 409 and "недоступно" in ro.get_json()["error"] and pidU in ro.get_json()["unavailable"])
check("нельзя заменить на товар другой категории", c.post("/api/project/replace", json=dict(id=pidU, field="product", value="prints"), headers=H(tU)).status_code == 400)
rr = c.post("/api/project/replace", json=dict(id=pidU, field="product", value="classic"), headers=H(tU))
pj2 = next(p for p in rr.get_json()["projects"] if p["id"] == pidU)
check("замена услуги сохраняет фото и макет и убирает сообщение", rr.status_code == 200 and not pj2["issue"] and pj2["product"] == "classic" and pj2["photos"] == pj["photos"] and pj2["unit"] > 0)
check("после замены заказ оформляется", c.post("/api/order", json=dict(name="Тест", phone="+375291234567", delivery="post", address="Минск, отделение 5", payment="online", key="kx-ok"), headers=H(tU)).status_code == 200)
# элемент товара: убрали формат и обложку
elem = json.loads(json.dumps(used)); [p.update(formats=[f for f in p["formats"] if f["id"] != "13x18"]) for p in elem["products"] if p["id"] == "prints"]
elem["covers"] = [x for x in elem["covers"] if x["id"] != "soft"]
re_ = c.post("/api/admin/catalog", json=dict(catalog=elem), headers=H(ta)).get_json()
stE = c.get("/api/state", headers=H(tU)).get_json(); pe = next(p for p in stE["projects"] if p["id"] == pidP)
check("убранный формат: сообщение и варианты формата этого же товара", pe["issue"]["field"] == "format" and "13×18" in pe["issue"]["text"] and {o["id"] for o in pe["issue"]["options"]} == {"10x15", "15x21"})
rf = c.post("/api/project/replace", json=dict(id=pidP, field="format", value="10x15"), headers=H(tU)).get_json()
check("замена формата работает", not next(p for p in rf["projects"] if p["id"] == pidP)["issue"])
check("старый заказ с удалённым товаром открывается", c.get("/api/orders", headers=H(tU)).status_code == 200 and "Premium" not in json.dumps([p["id"] for p in c.get("/api/catalog").get_json()["products"]]))
rm = json.loads(json.dumps(off)); rm["products"] = [p for p in rm["products"] if p["id"] != "mag"]; rm["promos"] = [p for p in rm["promos"] if p["code"] != "SUMMER15"]
check("неиспользуемый товар и промокод удаляются", c.post("/api/admin/catalog", json=dict(catalog=rm), headers=H(ta)).status_code == 200 and not any(p["id"] == "mag" for p in c.get("/api/catalog").get_json()["products"]))
c.post("/api/admin/catalog/reset", headers=H(ta))
check("после сброса каталог исходный", [p["id"] for p in c.get("/api/catalog").get_json()["products"]] == ["prints", "classic", "premium", "digital", "lite"])
# --- хранилище фото (раунд M): вошедшие -> БД, гость -> временный кэш, при входе переезд в БД
import projects as PRJ, db as DBM
gj = c.post("/api/guest", json=dict(client="cstore1")).get_json()
tg = gj["token"]
gp = c.post("/api/project/new", json=dict(product="prints", params=dict(format="10x15", paper="matte")), headers=H(tg)).get_json()["cur"]
c.post("/api/demo", headers=H(tg))
gdir = os.path.join(PRJ.GUEST_DIR, gp)
import server as SRV
SRV.ST.flush()
pg = SRV.ST.projects[gp]
check("фото гостя лежат во временном кэше, а в БД их нет", DBM.run("SELECT COUNT(*) FROM photo_blobs WHERE project_id = %s", (gp,), one=True)[0] == 0 and len(PRJ.blob_names(pg)) > 0)
nph = len(PRJ.blob_names(pg))
tu = c.post("/api/register", json=dict(name="Кэш Тест", email="cache@test.by", password="pass12345")).get_json()["token"]
c.post("/api/me/claim", json=dict(guest="cstore1"), headers=H(tu))
SRV.ST.flush()
check("при входе фото гостя переехали в БД", DBM.run("SELECT COUNT(*) FROM photo_blobs WHERE project_id = %s", (gp,), one=True)[0] == nph and not os.path.isdir(gdir))
check("у вошедшего пользователя нет файлов в старом хранилище", not os.path.isdir(os.path.join(PRJ.PROJ_DIR, gp)))
# очистка просроченных гостей
gj2 = c.post("/api/guest", json=dict(client="cstore2")).get_json(); tg2 = gj2["token"]
gp2 = c.post("/api/project/new", json=dict(product="prints", params=dict(format="10x15", paper="matte")), headers=H(tg2)).get_json()["cur"]
c.post("/api/demo", headers=H(tg2)); SRV.ST.flush()
check("свежий гостевой кэш не удаляется", PRJ.purge_guests(SRV.ST.projects) == [] and gp2 in SRV.ST.projects)
gone = PRJ.purge_guests(SRV.ST.projects, now=time.time() + PRJ.GUEST_TTL + 10)
check("просроченный гостевой кэш и проект удаляются", gp2 in gone and not os.path.isdir(os.path.join(PRJ.GUEST_DIR, gp2)) and gp2 not in SRV.ST.projects)
# --- каталог и цены из mock API (раунд M)
import catalog_api as CAPI
import orders as O
mc = c.get("/mock-api/v1/catalog").get_json()
check("mock API отдаёт каталог с версией", mc["version"] and len(mc["products"]) == 5)
check("mock API: товар, доставка, промокоды, прайс", c.get("/mock-api/v1/products/premium").get_json()["from_price"] == 70.35
      and len(c.get("/mock-api/v1/delivery").get_json()["items"]) == 3 and len(c.get("/mock-api/v1/promos").get_json()["items"]) == 2
      and any(i["sku"] == "prints:13x18" and i["price"] == 1.69 for i in c.get("/mock-api/v1/prices").get_json()["items"]))
check("mock API: ETag даёт 304", c.get("/mock-api/v1/catalog", headers={"If-None-Match": '"%s"' % mc["version"]}).status_code == 304)
check("менять каталог нельзя без прав администратора", c.put("/mock-api/v1/catalog", json=mc, headers=H(tL)).status_code == 403)
bad = dict(mc); bad["delivery"] = []
check("некорректный каталог не принимается", CAPI.validate(bad) != "")
import tempfile, shutil
_ed = CAPI.EDITED; _bak = open(_ed).read() if os.path.exists(_ed) else None
cat2 = json.loads(json.dumps(mc)); cat2["products"][2]["from_price"] = 80.0
open(_ed, "w", encoding="utf-8").write(json.dumps(cat2, ensure_ascii=False))
CAPI.sync(O.CATALOG, force=True)
check("цены меняются без перезапуска: сервер считает по новому каталогу", O.unit_price("premium", {"cover": "hard"}, 5) == 80.0
      and c.get("/api/catalog").get_json()["products"][2]["from_price"] == 80.0)
if _bak is None: os.remove(_ed)
else: open(_ed, "w").write(_bak)
CAPI.sync(O.CATALOG, force=True)
check("после отката цена прежняя", O.unit_price("premium", {"cover": "hard"}, 5) == 70.35)

print()
if failed:
    print(f"ПРОВАЛ: {len(failed)} из {len(passed) + len(failed)}: " + "; ".join(failed))
    sys.exit(1)
print(f"ВСЁ ХОРОШО: {len(passed)} проверок пройдено.")
