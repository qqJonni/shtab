# CLAUDE.md — проект «ШТАБ» (v2, чистый проект)

> Постоянный контекст для Claude Code. Держать в корне репозитория. Перечитывать перед каждым модулем. Если код расходится с этим файлом — доверять коду и сообщить о расхождении. Полная картина — в `ШТАБ_v2_Мастер-спецификация.md`.

## Продукт
«ШТАБ» — платформа управления строительством полного цикла и документооборотом для девелопера: объект → этап строительства → подэтап, назначение подрядчиков, замечания, закрытие работ с маршрутом согласования первичных документов (КС-2/КС-3 и др.), снабжение давальческим материалом, уведомления, дашборды. Первый клиент — ГК «Федерация» (Пермь). Ориентир — PlanRadar, но дешевле, в РФ, на языке российских документов, под процессы клиента.

Это **новый проект с нуля**. Из прошлой версии переносим только дизайн-язык и проверенный стек.

## Стек (не менять без явной причины)
- Flask (app-factory) + Flask-Login
- **PostgreSQL** (psycopg2-binary==2.9.9); строка подключения — `DATABASE_URL` из `.env`; SQLite удалён
- Bootstrap 5.3.3 + Bootstrap Icons + шрифт Inter; кастом в `static/css/crm.css`
- openpyxl (экспорт + генерация форм КС)
- Уведомления — внутренний центр (таблица + колокольчик); web/email-push позже

## Архитектура (с самого начала — чисто и модульно)
```
app.py            — app-factory, регистрация модулей, init_db, run_migrations
config.py         — константы, роли, настройки, SECRET_KEY из .env
db.py             — get_db, query_db, execute_db, get/set_setting, notify()
helpers.py        — декораторы (@role_required), утилиты фото, расчёты, уведомления
routes/           — модули по доменам: auth, objects, stages, substages,
                    defects, packages (согласование), supply, notifications,
                    dashboards, admin
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

**Путь на сервере уточнить при первом деплое.** Рабочее название сервиса — `shtab` (по аналогии с репозиторием). Ориентировочный путь: `/var/www/shtab/`. После деплоя скорректировать команду ниже.

```bash
# crontab -e  (от пользователя www-data или root)
# Каждый понедельник в 08:00
0 8 * * 1 cd /var/www/shtab && /var/www/shtab/venv/bin/python3 -c \
  "from routes.digest import send_weekly_digests; send_weekly_digests()" \
  >> /var/log/shtab-digest.log 2>&1
```

Проверка вручную (на сервере):
```bash
cd /var/www/shtab
python3 -c "from routes.digest import send_weekly_digests; send_weekly_digests()"
```

Повторный запуск безопасен — `notify()` добавляет новую запись, дублей в логике нет (cron запускается раз в неделю).

---

## Рабочий процесс
Модулями (0–6, см. мастер-спецификацию). Для каждого — отдельное ТЗ с критериями приёмки, шаги по одному, после шага — самопроверка. Перед стартом модуля — `git checkout -b <модуль>`.
