# CLAUDE.md — проект «ШТАБ» (v2, чистый проект)

> Постоянный контекст для Claude Code. Держать в корне репозитория. Перечитывать перед каждым модулем. Если код расходится с этим файлом — доверять коду и сообщить о расхождении. Полная картина — в `ШТАБ_v2_Мастер-спецификация.md`.

## Инфраструктура (единственный источник правды)
- **Домен:** `shtab-crm.ru` (HTTPS). Старый `shtab-otdelki.ru` — другой сервер, другой проект, не использовать.
- **GitHub:** `github.com/qqJonni/shtab` — единственный репозиторий.
- **Локальная папка:** `/Users/valeriy/Desktop/shtab` — здесь вся разработка.
- **Сервер:** VPS `shtab-crm.ru`, `/var/www/shtab/`, сервис `shtab`, venv в `/var/www/shtab/venv/`.
- **Деплой:** `git push origin main` → на сервере `git pull && systemctl restart shtab`.
- **Миграции после деплоя:** `cd /var/www/shtab && source venv/bin/activate && python3 -c "from app import init_db; init_db()"`

## Продукт
«ШТАБ» — платформа управления строительством полного цикла и документооборотом для девелопера: объект → этап строительства → подэтап, назначение подрядчиков, замечания, закрытие работ с маршрутом согласования первичных документов (КС-2/КС-3 и др.), снабжение давальческим материалом, уведомления, дашборды. Первый клиент — ГК «Федерация» (Пермь). Ориентир — PlanRadar, но дешевле, в РФ, на языке российских документов, под процессы клиента.

Это **новый проект с нуля**. Из прошлой версии переносим только дизайн-язык и проверенный стек.

## Стек (не менять без явной причины)
- Flask (app-factory) + Flask-Login
- **PostgreSQL** (psycopg2-binary==2.9.9); строка подключения — `DATABASE_URL` из `.env`; SQLite удалён
- Bootstrap 5.3.3 + Bootstrap Icons + шрифт Inter; кастом в `static/css/crm.css`
- openpyxl (экспорт + генерация форм КС)
- Уведомления — внутренний центр (таблица + колокольчик) + Web Push (VAPID/pywebpush, `routes/pwa.py`, таблица `push_subscriptions`); email-push пока нет

## Архитектура (с самого начала — чисто и модульно)
```
app.py            — app-factory, регистрация модулей, init_db, run_migrations
config.py         — константы, роли, настройки, SECRET_KEY из .env
db.py             — get_db, query_db, execute_db, get/set_setting, notify()
helpers.py        — декораторы (@role_required), утилиты фото, расчёты, уведомления
routes/           — модули по доменам: auth, objects (объекты/этапы/подэтапы/
                    команда объекта), defects, packages (согласование КС),
                    supply, notifications, dashboards, admin, export,
                    report_page (конструктор отчётов), guest, plans (планы
                    этажей + пины замечаний), pwa (manifest/SW/Web Push),
                    smeta (импорт сметы), digest (сводка по объекту),
                    id_module (исполнительная документация), journal
                    (журнал производства работ + PDF-отчёт по объекту)
templates/        — по доменам; base.html (sidebar/topbar/мобильное меню)
static/           — css/crm.css; папки загрузок (фото, документы) вне git
```
Навигация в sidebar и мобильном меню строится **по роли**. Доступ — через `@role_required(...)`, не хардкодом в шаблонах.

## Дизайн-система — СОХРАНЯТЬ СТРОГО
Фон `#F5F6FA` · Карточки `#FFFFFF` · Сайдбар `#1E293B` · Акцент `#3B82F6` · Успех `#10B981` · Опасность `#EF4444` · Предупреждение `#F59E0B`. Шрифт Inter. Сайдбар 240px, топбар 60px, мобильный навбар `#1E293B` с бургером. Карточки radius 12px, тень `0 1px 3px rgba(0,0,0,.08)`; кнопки 8px; поля 44px/8px; модалки 16px. Бейджи статусов — мягкие цвета (фон+текст). Логин/регистрация — градиент `#1E293B→#334155`, белая карточка по центру. Новый экран обязан выглядеть как часть единого CRM.

## Роли
`manager` (Руководитель), `pto` (Инженер ПТО), `inspector` (Технадзор), `foreman` (Прораб), `supply` (Снабженец), `accountant` (Бухгалтер), `contractor` (Подрядчик), `admin` (системный), `guest` (по токену).

## Доменная модель (кратко; детали — в мастер-спецификации)
`objects → construction_stages → substages`. Документы этапа `stage_documents`. Закрытие подэтапа: `doc_packages` + `package_documents` + `approval_steps` (цепочка технадзор→прораб→ПТО→руководитель→бухгалтерия с возвратом подрядчику и прямым возвратом, видимость согласований). Замечания: `defects` + `defect_photos` + `defect_history`. Снабжение: `material_requests` + `material_request_items` + `material_request_history`. Сквозное: `organizations`, `users`, `notifications`, `settings`, `guest_tokens`.

## Незыблемые правила
1. Менять по одному модулю за раз; после модуля он запускается и не ломает предыдущие.
2. Данные = деньги и юр. документы: аккуратность с целостностью, FK, каскадами; не терять фото и документы.
3. `init_db()` идемпотентен; миграции в `run_migrations()` безопасны при повторе.
4. psycopg2 RealDictRow не сериализуется в JSON — перед `tojson` делать `[dict(r) for r in rows]`.
5. Папки загрузок (фото, документы) — вне git (`.gitignore`).
6. Seed-пользователи всех ролей создаются только если таблица users пуста.
7. Срок подэтапа не превышает срок этапа (валидация).
8. Каждое значимое действие в воркфлоу пишет уведомление получателю и (для замечаний/согласований) запись в историю.
9. Формы КС-2/КС-3/счёт-фактура/отчёт по давальческому — по бланкам клиента (приложит Лео); до того — загрузка файлами.

## Модуль «Импорт сметы» — влит в main (ветка smeta-import)

### Что сделано (Шаги 1–5)
Загрузка xlsx/csv/pdf прямо со страницы этапа → парсинг → редактируемый предпросмотр → создание подэтапов одним кликом.

### Новые файлы
```
smeta_parser.py       — детерминированный парсер xlsx/csv; parse_pdf() → (rows, note)
ai_extractor.py       — ИИ-фолбэк: ai_extract(text) → rows; OCR: ocr_pdf(filepath)
routes/smeta.py       — 4 маршрута (upload, preview, confirm, cancel)
templates/smeta/preview.html  — экран предпросмотра с редактированием позиций
static/smeta/         — временные файлы загрузок (вне git)
```

### Маршруты
| URL | Метод | Endpoint |
|-----|-------|----------|
| `/stages/<id>/smeta/upload` | POST | `smeta_upload` |
| `/stages/<id>/smeta/<imp_id>/preview` | GET | `smeta_preview` |
| `/stages/<id>/smeta/<imp_id>/confirm` | POST | `smeta_confirm` |
| `/stages/<id>/smeta/<imp_id>/cancel` | POST | `smeta_cancel` |

Доступ: только роли `('pto', 'manager', 'admin')` → константа `SMETA_ROLES`.

### БД
Таблица `smeta_imports` (миграция идемпотентна в `run_migrations()`):
```sql
id, stage_id, filename, source_type (xlsx|csv|pdf),
status (parsed|confirmed|failed), rows_json,
uploaded_by, uploaded_at, confirmed_at
```

### Логика парсинга
1. **xlsx/csv** → детерминированный парсер: ищет заголовки через синонимы (рус/анг), парсит русские числа (`1 006,5` → `1006.5`), нормализует единицы.
2. **PDF с текстовым слоем** → pdfplumber → детерминированный парсер → если 0 строк → ИИ-фолбэк.
3. **PDF-скан** → Yandex Vision OCR (`ocr_pdf()`) → если есть текст → `ai_extract()` → note=`'ocr_ai'`.
4. **ИИ-фолбэк** — абстракция за `AI_PROVIDER` в `.env`: `stub` (пустой список, без сети) | `yandexgpt` | `gigachat`.

### Переменные окружения (только в `.env`, не в git)
```
AI_PROVIDER=yandexgpt          # или stub (для разработки)
YANDEX_GPT_API_KEY=...
YANDEX_FOLDER_ID=b1g2klri3f64ma6n1ovk
YANDEX_GPT_MODEL=yandexgpt/latest
```

### Защита данных при замене (replace)
`_check_replace_allowed(stage_id)` — блокирует режим «Заменить все» если на подэтапах уже есть: пакеты КС, фото, заявки на материалы, замечания или подэтапы в работе. Кнопка дизейблится на preview-экране; попытка в обход → flash + redirect.

### Важные нюансы
- `parse_pdf()` возвращает `(list[dict], note: str)`, `parse_file()` возвращает `list[dict]` (обратная совместимость).
- Исходный файл автоматически прикрепляется к этапу как документ типа `price_doc`.
- OCR скан-PDF с мержеными ячейками (работа+материалы в одной строке): имена позиций верны, суммы ±1%, qty/price ненадёжны — пользователь правит на preview-экране.
- На prod Yandex Vision + YandexGPT — данные остаются в РФ (Yandex Cloud). Внешние API (OpenAI и др.) не используются.

---

## Модуль «Дайджест по объекту» — ветка object-digest

### Что сделано (Шаги 1–3)
Сводный экран руководителя по объекту + PDF-экспорт + скрипт еженедельной рассылки.

### Новые файлы
```
digest.py                   — object_digest(obj_id, period_days=7) -> dict
routes/digest.py            — маршруты + _build_digest_pdf() + send_weekly_digests()
templates/digest/view.html  — экран сводки с выбором периода
```

### Маршруты
| URL | Endpoint |
|-----|----------|
| `/objects/<id>/digest` | `object_digest_view` |
| `/objects/<id>/digest/pdf` | `object_digest_pdf` |

Доступ: `('manager', 'admin', 'pto')`.

### Источники данных
- Деньги: `substages.total_price` (надёжно), `construction_stages.contract_amount`
- Временны́е метки: `substages.completed_at`, `doc_packages.completed_at / submitted_at`, `defects.verified_at / resolved_at`
- `doc_packages.amount` не существует — не использовать

### Еженедельная рассылка — cron на сервере

```bash
# crontab -e  (от пользователя root)
# Каждый понедельник в 08:00
0 8 * * 1 cd /var/www/shtab && /var/www/shtab/venv/bin/python3 -c \
  "from routes.digest import send_weekly_digests; send_weekly_digests()" \
  >> /var/log/shtab-digest.log 2>&1
```

Проверка вручную (на сервере):
```bash
cd /var/www/shtab && source venv/bin/activate
python3 -c "from routes.digest import send_weekly_digests; send_weekly_digests()"
```

Повторный запуск безопасен — `notify()` добавляет новую запись, дублей в логике нет (cron запускается раз в неделю).

---

## Модуль «Исполнительная документация (ИД)» — влит в main (ветка id-module)

### Что сделано (Шаги 0–5)
К каждому этапу строительства — чеклист обязательных и необязательных документов ИД. Подрядчик загружает файлы. Готовность считается по обязательным пунктам. Пакет ИД проходит согласование inspector→pto→manager. Этап нельзя закрыть без принятого пакета ИД и завершённых подэтапов.

### Новые файлы
```
routes/id_module.py           — все маршруты ИД
templates/id/package_detail.html — страница пакета ИД
static/id_docs/<stage_id>/   — загруженные файлы (вне git)
```

### Таблицы БД (идемпотентные миграции в run_migrations)
```
id_item_types      — справочник типов документов ИД (18 позиций, seed)
id_checklist_items — состав ИД по этапу (stage_id, type_id, title, is_required, order_num)
id_documents       — файлы к пунктам (item_id, filename, original_name, uploaded_by)
id_packages        — пакеты ИД (stage_id, contractor_id, status: draft/in_review/returned/accepted)
id_approval_steps  — шаги цепочки (package_id, step_order, role, status: pending/waiting/approved/returned)
```

### Маршруты
| URL | Endpoint |
|-----|----------|
| POST `/stages/<id>/id/add` | `id_item_add` |
| POST `/stages/<id>/id/<item_id>/delete` | `id_item_delete` |
| POST `/stages/<id>/id/<item_id>/toggle-required` | `id_item_toggle_required` |
| POST `/stages/<id>/id/<item_id>/move/<dir>` | `id_item_move` |
| POST `/stages/<id>/id/<item_id>/upload` | `id_file_upload` |
| GET  `/stages/<id>/id/files/<file_id>/download` | `id_file_download` |
| POST `/stages/<id>/id/files/<file_id>/delete` | `id_file_delete` |
| GET  `/id-packages/<id>` | `id_package_detail` |
| POST `/stages/<id>/id/submit` | `id_package_submit` |
| POST `/id-packages/<id>/resubmit` | `id_package_resubmit` |
| POST `/id-packages/<id>/approve` | `id_package_approve` |
| POST `/id-packages/<id>/return` | `id_package_return` |

### Цепочка согласования
`ID_APPROVAL_CHAIN` в `config.py`:  `inspector → pto → manager`. Прямой возврат к вернувшей роли при resubmit (как в КС-пакетах).

### Константы доступа
- `ID_EDITORS = ('manager','pto','inspector','admin')` — набирают/редактируют состав
- `ID_UPLOADERS = ('contractor','foreman','manager','pto','admin')` — загружают файлы
- Подрядчик-uploader — только если `organization_id == stage.contractor_id`

### Гейт закрытия этапа (stage_edit)
Переход в `done` блокируется если: не все подэтапы в `done/closed/approved` **ИЛИ** у этапа есть ИД-пункты и пакет ИД не в `accepted`. Индикатор готовности — жёлтый баннер с замком на странице этапа (только manager/admin).

---

---

## Модуль «Мультитенантность» — влит в main (ветка multitenancy)

### Что сделано (Шаги 2–7, проверка 55/56 OK)

Полная изоляция тенантов: каждый застройщик (developer-org) видит только свои объекты, пакеты, замечания, пользователей. Подрядчик — межтенантный кейс, видит этапы у всех застройщиков где назначен.

### Ключевые механизмы

**Anchor изоляции:** `objects.developer_id → organizations(id)` — всё дочернее наследует принадлежность через объект.

**helpers.py:**
- `accessible_object_ids(user)` — admin→все; contractor→объекты где их org в этапах; остальные→объекты своей org
- `assert_object_access(user, object_id)` — abort(403) при попытке обратиться к чужому объекту
- `TEAM_ROLES` / `get_object_team(object_id)` — команда объекта по ролям

**Регистрация (Шаг 5):**
- Только по `join_code` организации, без самовыбора роли (`role='pending'`, `is_approved=0`)
- Апрув admin/manager с назначением роли; manager видит и апрувит только своих (tenant-scope)
- `_assert_same_tenant()` в admin.py блокирует действия над пользователями чужого тенанта

**Команда объекта (Шаг 4):**
- Таблица `object_team(object_id, role, user_id UNIQUE(object_id,role))`
- При отправке пакета КС/ИД: `approver_id` берётся из `object_team` (не «любой с ролью»)
- Видимость пакетов: `approver_id=me OR (approver_id IS NULL AND role=me.role)` (обратная совместимость)
- Валидация полноты команды перед отправкой; смена участника → переназначение pending-шагов
- Снабжение: notify конкретного pto/supply из команды объекта

**Создание тенанта (admin):**
- `/admin/organizations` → создать org → автогенерируется `join_code` (8 симв.)
- Показывается в таблице; можно пересгенерировать или деактивировать (старый код перестаёт принимать регистрации)

### Новые таблицы / колонки
```sql
organizations.join_code TEXT UNIQUE      -- код для регистрации
organizations.status TEXT DEFAULT 'active'
objects.developer_id → organizations(id) -- anchor тенанта
object_team(id, object_id, role, user_id, UNIQUE(object_id,role))
approval_steps.approver_id               -- конкретный пользователь
id_approval_steps.approver_id
```

### Инструменты разработчика
```bash
python3 seed_multitenant_test.py         # создать 2 тенанта + подрядчика
python3 seed_multitenant_test.py --wipe  # очистить (маркер [SEED])
python3 test_isolation.py                # прогнать чек-лист (55 проверок)
```

### Проверка при деплое
После `init_db()` выполнить `python3 test_isolation.py` — должно быть 0 FAIL.
Колонка `approver_id` добавляется миграцией в `run_migrations()` — идемпотентно.

---

## Модуль «График производства работ (ГПР)» — влит в main (ветка gpr-schedule)

### Что сделано (Шаги 0–8, чек-лист 44/44)
Управленческий слой для отчётности руководителя: Гант, план-факт, S-кривая освоения, baseline, вехи, экспорт. Сетевое планирование (зависимости, критический путь, ресурсы, .mpp) — осознанно НЕ в этом заходе (роадмап).

### Файлы
```
routes/schedule.py           — расчёты (get_schedule_data, get_s_curve) + все маршруты
templates/schedule/view.html — экран: кастомный SVG/CSS-Гант (JS-рендер), Chart.js S-кривая
```

### Экран `/objects/<id>/schedule` (кнопка «График» на объекте и дашборде)
- **Гант**: этапы (сворачиваются) → подэтапы; план-полоса (пунктир) + факт-полоса (цвет по риску: синий в срок / жёлтый риск ≤7 дн / красный просрочен / зелёный завершён); линия «сегодня»; масштабы день/неделя/месяц (px/день, JS-перерисовка); тултипы с датами/%/подрядчиком; строка «◆ Вехи» с ромбами
- **План-факт**: % по закрытым объёмам из completed-пакетов (этап — взвешенно по стоимости), отклонение в днях (заверш.: план−факт; в работе: −(сегодня−план) если план прошёл), светофор, 4 карточки итогов
- **S-кривая**: план = стоимость подэтапа равномерно по его периоду; факт = суммы принятых КС на completed_at + остаток стоимости завершённых на факт-финише; переключатель %/₽
- **Baseline**: «Утвердить график» (manager) → снапшот в schedule_baselines.data_json; колонка «от БЛ» = baseline_end − (факт-финиш | текущий план)
- **Вехи**: schedule_milestones, CRUD manager/pto, статусы pending/done/missed + вычисляемая просрочка

### Данные
- `substages`: + plan_start_date, actual_start_date, actual_end_date
- `construction_stages`: + actual_start_date, actual_end_date
- **Авто-факт**: подэтап in_progress → actual_start=сегодня; done → actual_end=сегодня; откат чистит; `recalc_stage_actuals()` (helpers) агрегирует факт этапа. Ручная правка — в форме подэтапа (manager/pto)
- Экспорт: PDF (reportlab, альбомный; «руб.» — символов ₽/◆ нет в TTF) и Excel (openpyxl)

### Права
Просмотр — все, кто видит объект (contractor — только свои этапы, `get_schedule_data(contractor_org_id=...)`); план/вехи/факт-корректировка — manager/pto/admin; baseline и экспорт по ролям. Все маршруты через `assert_object_access`.

---

## Модуль «Настройки» (тенант + пользователь) — влит в main (ветка tenant-settings)

Конфигурация по тенанту (организация-застройщик) и по пользователю. **Дефолты = текущее поведение**: ненастроенный тенант работает как раньше. Переключатели меняют доступ только ВНУТРИ тенанта — `assert_object_access` всегда поверх.

### Хранилище и хелперы (helpers.py)
- `tenant_settings(organization_id, key, value, UNIQUE)` + `get_tenant_setting/set_tenant_setting`; `current_tenant_setting(user, ...)`. `TENANT_DEFAULTS` — единая точка дефолтов.
- `user_settings(user_id, key, value, UNIQUE)` + `get_user_setting/set_user_setting`.
- Значения — TEXT; сложное (списки ролей) хранится JSON.

### Настраиваемые цепочки согласования
- `approval_chain_ks` / `approval_chain_id` (JSON-списки ролей из `CHAIN_ROLES`). Дефолт = константы `config.APPROVAL_CHAIN`/`ID_APPROVAL_CHAIN`.
- `get_chain_for_object(object_id, kind)` — цепочка ТЕНАНТА ОБЪЕКТА (`developer_id`); читается при создании новых пакетов (packages.py/id_module.py). Активные пакеты идут по шагам, уже записанным в БД. Валидация `object_team` — по настроенной цепочке. Класс «согласующих ролей» (фильтры pending) — статический `CHAIN_ROLES`.

### Переключатели видимости модулей (наборы ролей по тенанту)
- `access_gpr` / `access_finance` / `access_digest` в `MODULE_ACCESS` (helpers.py) с дефолтными ролями = текущее поведение.
- `can_access(user, module)`, `can_access_for_object(user, module, obj)` (по тенанту объекта — для contractor/кросс-тенант), `can_see_finance(user)` (contractor/admin всегда True). Ни один не заменяет `assert_object_access`.
- Применение: ГПР/дайджест — роут-гарды (403) + скрытые ссылки; **финансы сквозняком** через context_processor `can_see_finance` (колонки цен/сумм пакета КС, карточка бюджета и режим ₽ S-кривой в ГПР, блок «Деньги» в дайджесте, колонка суммы на дашборде).
- manager залочен в finance/digest (нельзя отрезать управление).

### Брендинг и реквизиты
- `organizations.logo` (файл, `static/logos/<org_id>/`, вне git); `save_org_logo()`. Реквизиты — существующие поля `organizations`.
- Логотип тенанта в сайдбаре/мобильном бренде (context_processor `brand_logo`/`brand_name`); fallback — «ШТАБ». admin/contractor → всегда «ШТАБ».

### Уведомления (два уровня)
- Тенант: `notify_channels` [in_app/email/push], `notify_types` [approval/defect/supply], `digest_enabled`, `digest_weekday`, `digest_last_sent` (дедуп).
- Пользователь: `channel_email`, `channel_push`, `digest_subscribed`. `in_app` — базовый, не выключается.
- `notify()` (db.py): тип-роутинг тенанта (событийный тип выкл → не создаётся; digest/user всегда); каналы = пересечение(тенант, пользователь); email только при настроенном SMTP. contractor/admin — тип-фильтр не действует.
- Email: `config.EMAIL_*` из `.env` (`db._send_email`, smtplib+TLS, `APP_BASE_URL` для ссылок); без SMTP тихо пропускается.
- Cron `send_weekly_digests()` — **запускать ежедневно**: по каждому тенанту `digest_enabled` + `digest_weekday==сегодня` + дедуп; получатели manager/admin/pto тенанта, подписанные, по объектам тенанта. `force=True` — ручной прогон.

### Страница `/settings/<area>` (routes/settings.py)
Области: **personal** (все — профиль, каналы, подписка на дайджест), **organization** (manager своего тенанта, admin — цепочки, доступы, реквизиты/логотип, уведомления), **platform** (admin). Чужая область → 403. Пункт «Настройки» в сайдбаре/мобильном меню.

### Деплой-заметки
- Для email добавить `EMAIL_HOST/PORT/USER/PASSWORD/FROM` в серверный `.env`.
- Cron дайджеста переключить с недельного на **ежедневный** (расписание теперь пер-тенантное).

---

## Модуль «Публичный лендинг /preview» — влит в main (ветка landing-preview)

Маркетинговая витрина продукта по адресу `/preview` — публичная, без авторизации и без данных клиентов.

- `routes/landing.py`: `GET /preview` (публичный, без @login_required, ничего не читает из БД кроме записи заявки); `POST /preview/lead` — форма заявки; `/admin/leads` — список для admin/manager.
- Шаблон `templates/landing/preview.html` — **самостоятельный** (свой `<!doctype>`, не наследует base.html). Стили — `static/css/landing.css` (cache-busting `landing_version` = mtime; crm.css не трогается). PDF презентации в `static/ШТАБ_Презентация.pdf`.
- Форма «Запросить демо»: honeypot-поле `website` + rate-limit по IP (`_LAST_LEAD`, 60 сек); таблица `landing_leads` (идемпотентно в init_db); запись + `notify()` admin/manager.
- Публичность работает автоматически: в приложении нет глобального login-гейта, `@login_required` навешивается точечно. Лендинг его просто не имеет (как гостевой `guest_view`).
- Контент строго по копирайту ТЗ; адаптив 375/768/1280, mobile-first.


## Рабочий процесс
Модулями (0–6, см. мастер-спецификацию). Для каждого — отдельное ТЗ с критериями приёмки, шаги по одному, после шага — самопроверка. Перед стартом модуля — `git checkout -b <модуль>`.
