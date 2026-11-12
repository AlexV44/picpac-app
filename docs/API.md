# REST API (кратко)

Заголовок `Authorization: Bearer <token>` (или `X-Client: <guest id>` для гостя). Ошибки: JSON `{"error": "понятный текст"}` и код 4xx/5xx.
Сущности из рекомендуемого контракта ТЗ: `user` (аккаунты), `customer`/`order` (заказы), `product` (каталог), `project`, `order_status` (`STATUSES`), `interaction` (обращения), `document` (PDF макеты).

| Метод и путь | Назначение | Доступ |
|---|---|---|
| GET `/api/catalog` | каталог, цены, сроки, доставка, промокоды, лимиты и `version` (ETag); берётся из catalog_api | все |
| GET `/mock-api/v1/catalog`, `/products[/<id>]`, `/delivery`, `/promos`, `/prices` | встроенный mock API каталога | все |
| PUT `/mock-api/v1/catalog` | заменить каталог и цены без перезапуска | админ |
| GET `/api/state` | состояние: `me`, проекты, корзина, текущий проект | все |
| POST `/api/guest`, `/api/login`, `/api/logout` | сессии | все |
| POST `/api/me/claim` | перенос гостевых заказов в аккаунт | вошедший |
| POST `/api/me/delete` | удалить свои данные | клиент |
| POST `/api/project/new|open|close|delete|params` | проекты (черновики) | клиент |
| POST `/api/upload` | загрузка JPEG/PNG; ответ: `added`, `failed[]`, `bad[]` | клиент |
| POST `/api/photos/move`, `/api/photo/<id>` | порядок, поворот, исключение, удаление фото | клиент |
| POST `/api/cart/add|qty`, `/api/print/qty` | корзина, количество | клиент |
| GET `/api/preflight` | проверка корзины перед печатью | клиент |
| POST `/api/quote` | пересчёт цены (доставка, промокод) | клиент |
| POST `/api/order` | оформить заказ; поле `key` делает запрос идемпотентным | клиент |
| GET `/api/orders` | история заказов текущего пользователя | клиент |
| GET `/api/track/<code>` | статус по коду | по коду |
| GET/POST `/api/support` | чат поддержки, поле `order` связывает с заказом | клиент |
| POST `/api/chat/start`, `/api/edit/start`; GET `/api/job/<id>` | фоновые задачи ИИ с шагами | клиент |
| GET `/api/admin/overview` | все заказы, обращения, журнал | **admin** |
| POST `/api/admin/order/<code>/status` | сменить статус | **admin** |
| GET/POST `/api/admin/thread/<uid>` | читать и отвечать клиенту | **admin** |


## CRM и персональные предложения
- `GET /api/offers?where=home|checkout`: предложения для вошедшего клиента (`source: crm|none`, `customer`, `offers[]` с `title`, `text`, `why`, `badge`, `percent`, `free_delivery`, `cta`).
- `POST /api/quote`, `POST /api/order`: поле `pers` (по умолчанию true). Скидку и её основание (`discount_label`) считает сервер.
- `GET /mock-crm/v1/customers/lookup?email=`, `GET /mock-crm/v1/customers/<id>`: демо-CRM (Bearer `CRM_TOKEN`).
- `GET /api/admin/overview` содержит `crm` (режим, связь, число запросов).
Подробности: `docs/CRM.md`.

## Аккаунты и настройки (раунд H)
- `POST /api/register` {name,email,password} -> токен сессии и пользователь (409 если почта занята, 429 при переборе)
- `POST /api/project/auto_enhance` {on:bool} — автоулучшение для всего проекта (по умолчанию выключено)
- `POST /api/photo/<pid>/enhance` {on:bool} — улучшение одного фото
- `GET /api/admin/overview` теперь содержит `crm`, `flux` (теперь всегда «локальный редактор»), `db`

## Шаблоны и оформление (раунд J)
- `POST /api/project/new` принимает `template` (id шаблона из `album_templates`) для фотокниги; без него создаётся ручной альбом. В `params` сохраняются `mode` (`template`|`manual`) и `tpl`.
- `POST /api/spread {op:"bg", si, bg:[r,g,b]|null}` — свой цвет фона разворота. `POST /api/cover/style {bg:[r,g,b]|null}` — цвет обложки.
- `GET /api/spread/<i>.jpg?plain=1` и `GET /api/cover.jpg?plain=1` — картинки без служебных меток для режима просмотра.

## Раунд M
| Метод | Назначение | Кто |
|---|---|---|
| GET `/api/admin/catalog` | каталог для редактирования во вкладке «Цены» | админ |
| POST `/api/admin/catalog` `{catalog}` | сохранить цены; изменения (было → стало) пишутся в журнал | админ |
| POST `/api/admin/catalog/reset` | вернуть исходный каталог (seed/catalog.json) | админ |
| POST `/api/event` `{ev: catalog|checkout}` | шаг воронки, который видит только интерфейс | все |
| GET `/p/<проект>`, `/catalog[/<услуга>]`, `/orders`, `/cart` | deep links (после входа открывают нужный экран) | все |
| GET `/api/admin/overview` | теперь содержит `funnel` (6 шагов, уникальные пользователи за 30 дней) | админ |

### Гибкий каталог (раунд M)
Каталог (`/api/admin/catalog`, `PUT /mock-api/v1/catalog`) целиком редактируется администратором: товары (книги и печать), форматы и бумага, обложки, способы доставки и оплаты, промокоды. У товаров, обложек, доставки, оплаты и промокодов есть флаг `active` (по умолчанию `true`): отключённые не предлагаются новым покупателям, а старые проекты и заказы работают. Удалить то, что уже есть в проектах или заказах, нельзя, сервер вернёт подсказку отключить пункт. Id пунктов: латиница, цифры, `_` и `-`; коды промокодов приводятся к верхнему регистру.

### Замена удалённого из каталога (раунд M)
Администратор может удалить товар, обложку, формат или бумагу, даже если пользователи уже выбрали их. Сохранение возвращает `notes` (сколько проектов затронуто). У проекта в `/api/state` появляется `issue {field, what, text, options[]}` с мягким пояснением и вариантами замены (для товара только той же категории), цена удалённого товара не выдумывается (0). `POST /api/project/replace {id, field: product|cover|format|paper, value}` выполняет замену, фото и макет сохраняются. До замены `/api/order` возвращает 409 с `unavailable`, а добавление в корзину отклоняется. Если удалён способ доставки или оплаты, оформление само выбирает следующий и сообщает об этом.
