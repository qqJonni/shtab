"""
ИИ-фолбэк для смет + OCR скан-PDF.

Интерфейсы:
  ai_extract(table_text) -> list[dict]   — извлечение позиций из текста
  ocr_pdf(filepath) -> str               — OCR скан-PDF через Yandex Vision

Конфиг (env):
  AI_PROVIDER: stub | yandexgpt | gigachat   (default: stub)
  YANDEX_GPT_API_KEY, YANDEX_FOLDER_ID       — для yandexgpt и OCR

Данные уходят в Yandex Cloud (ЦОД в РФ). Зарубежные API не задействованы.
"""

import base64
import json
import os
import re
from typing import Optional

import config

# Все известные единицы из конфига — для пометки unit_unknown
_KNOWN_UNITS: set[str] = set(config.UNITS)


# ── Схема и промпты ────────────────────────────────────────────────────────

_SCHEMA_EXAMPLE = """{
  "positions": [
    {
      "name": "Устройство стяжки пола",
      "unit": "м2",
      "quantity": 45.5,
      "unit_price": 350.00,
      "total": 15925.00
    }
  ]
}"""

_SYSTEM_PROMPT = """\
Ты — точный парсер строительных ценовых документов (ЦД / смет). \
Извлеки ТОЛЬКО позиции работ и верни JSON.

═══ СТРУКТУРА ДОКУМЕНТА ═══
Ценовой документ состоит из разделов. Каждый раздел содержит:
  • одну или несколько СТРОК РАБОТЫ (главная позиция раздела)
  • под ней — строки МАТЕРИАЛОВ (расшифровка комплектующих)

Колонки таблицы: Наименование | Ед.изм | Объём (кол-во) | Цена за ед. | Общая стоимость

ВАЖНО: «Общая стоимость» позиции = стоимость работы + стоимость материалов.
Поэтому Объём × Цена ≠ Общая стоимость — это нормально, НЕ используй это
как критерий правильности.

═══ ЧТО ВКЛЮЧАТЬ ═══
Строки работ — глаголы действия:
  устройство, прокладка, монтаж, изоляция, укладка, установка, демонтаж,
  разработка, уплотнение, гидроизоляция, армирование, заделка, бурение и т.п.

═══ ЧТО ПРОПУСКАТЬ (без исключений) ═══
  • Материалы: любые товарные наименования — трубы, кольца, фитинги, битум,
    песок, щебень, арматура, кабель, плиты, крепёж, марки/артикулы (KC10.6,
    d110мм, ПП 300, ЛайтРок и т.п.)
  • Разделы: «Раздел N», «Глава», «Итого», «Всего», «На сумму», «Итоговая
    стоимость», «Без НДС»
  • Спецтехника отдельной строкой (Кран 25т и т.п.) — пропускать
  • Реквизиты, подписи, даты, юридические тексты

═══ КАК ЧИТАТЬ ЧИСЛА ПОСЛЕ OCR ═══
После OCR числа из разных колонок перемешаны. Для каждой строки работы ищи:
  1. quantity  — небольшое число (обычно 1–10 000), количество единиц
  2. unit_price — цена ТОЛЬКО труда за единицу (без материалов)
  3. total     — quantity × unit_price (итог конкретной работы)
Если уверенности нет — оставь поле null, не угадывай.

КРИТИЧНО — не путать итог РАЗДЕЛА с итогом строки:
  • Итог раздела — крупное число ДО перечня позиций («Раздел X … 4 261 472,60»)
    → это НЕ total конкретной работы, игнорируй его при заполнении total.
  • Total строки работы идёт ПОСЛЕ её наименования и числа quantity × unit_price.
  • Проверяй: total ≈ quantity × unit_price (±5%). Если не сходится — total=null.

═══ ФОРМАТ ОТВЕТА ═══
Только валидный JSON, никакого текста вокруг, никаких markdown-блоков.\
"""

_USER_PROMPT_TPL = """\
Текст ценового документа извлечён из скан-PDF через OCR.
Колонки разделены табуляцией (\\t). Порядок колонок таблицы:
Наименование | Ед.изм | Объём | Цена за ед. | Общая стоимость

Если табуляций нет — колонки перемешаны, восстанови по смыслу.

---
{table_text}
---

Верни JSON строго по схеме:
{schema}\
"""


# ── Публичный интерфейс ────────────────────────────────────────────────────

def ai_extract(table_text: str) -> list[dict]:
    """
    Отправляет текст таблицы в ИИ-провайдер, возвращает позиции.
    Никогда не бросает исключений — при ошибке возвращает [].
    """
    provider = os.environ.get('AI_PROVIDER', 'stub').lower().strip()
    try:
        if provider == 'yandexgpt':
            raw = _call_yandexgpt(table_text)
        elif provider == 'gigachat':
            raw = _call_gigachat(table_text)
        else:                           # stub — локальная разработка
            raw = _call_stub(table_text)
        return _parse_response(raw)
    except Exception:
        return []


# ── Провайдеры ─────────────────────────────────────────────────────────────

def _call_stub(table_text: str) -> str:
    """Заглушка: возвращает пустой список позиций. Поток работает без модели."""
    return '{"positions": []}'


def _call_yandexgpt(table_text: str) -> str:
    """
    YandexGPT Foundation Models API.
    Данные уходят в Yandex Cloud (ЦОД в РФ) — соответствует требованию «данные не покидают РФ».
    Env: YANDEX_GPT_API_KEY, YANDEX_FOLDER_ID
    Опционально: YANDEX_GPT_MODEL (default: yandexgpt-lite/latest — дешевле)
    """
    import urllib.request

    api_key = os.environ.get('YANDEX_GPT_API_KEY', '')
    folder_id = os.environ.get('YANDEX_FOLDER_ID', '')
    model_id = os.environ.get('YANDEX_GPT_MODEL', 'yandexgpt-lite/latest')

    if not api_key or not folder_id:
        raise ValueError('YANDEX_GPT_API_KEY и YANDEX_FOLDER_ID не заданы')

    prompt = _USER_PROMPT_TPL.format(
        table_text=table_text[:6000],   # лимит контекста
        schema=_SCHEMA_EXAMPLE,
    )
    payload = {
        'modelUri': f'gpt://{folder_id}/{model_id}',
        'completionOptions': {
            'stream': False,
            'temperature': 0.1,
            'maxTokens': '4000',
        },
        'messages': [
            {'role': 'system', 'text': _SYSTEM_PROMPT},
            {'role': 'user', 'text': prompt},
        ],
    }
    data = json.dumps(payload, ensure_ascii=False).encode('utf-8')
    req = urllib.request.Request(
        'https://llm.api.cloud.yandex.net/foundationModels/v1/completion',
        data=data,
        headers={
            'Content-Type': 'application/json',
            'Authorization': f'Api-Key {api_key}',
            'x-folder-id': folder_id,
        },
        method='POST',
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        body = json.loads(resp.read().decode('utf-8'))

    return body['result']['alternatives'][0]['message']['text']


def _call_gigachat(table_text: str) -> str:
    """
    GigaChat (Сбербанк). Данные остаются в РФ.
    Env: GIGACHAT_CLIENT_ID, GIGACHAT_CLIENT_SECRET
    Реализация — заглушка до получения доступа к API.
    """
    # TODO: реализовать когда будут credentials
    raise NotImplementedError('GigaChat: credentials не настроены')


# ── Разбор ответа модели ───────────────────────────────────────────────────

def _parse_response(raw: str) -> list[dict]:
    """Парсит JSON из ответа модели → структуры позиций."""
    raw = raw.strip()
    # Модель может обернуть JSON в markdown-блок ```json ... ```
    m = re.search(r'```(?:json)?\s*(.*?)```', raw, re.DOTALL)
    if m:
        raw = m.group(1).strip()
    # Ищем первый {...}
    m = re.search(r'\{.*\}', raw, re.DOTALL)
    if not m:
        return []
    try:
        data = json.loads(m.group())
    except json.JSONDecodeError:
        return []

    positions = data.get('positions', [])
    result = []
    for p in positions:
        name = str(p.get('name') or '').strip()
        if not name:
            continue
        unit = str(p.get('unit') or '').strip()

        def _f(val) -> Optional[float]:
            if val is None:
                return None
            try:
                return float(val)
            except (ValueError, TypeError):
                return None

        qty = _f(p.get('quantity'))
        price = _f(p.get('unit_price'))
        total = _f(p.get('total'))

        row = {
            'name': name,
            'unit': unit,
            'unit_unknown': unit not in _KNOWN_UNITS and bool(unit),
            'quantity': qty,
            'unit_price': price,
            'total': total,
            'confidence': _confidence_ai(qty, price, total),
            'source': 'ai',
            'raw': {
                'unit': unit,
                'quantity': str(qty if qty is not None else ''),
                'unit_price': str(price if price is not None else ''),
                'total': str(total if total is not None else ''),
            },
        }
        result.append(row)
    return result


def _confidence_ai(qty, price, total) -> float:
    """Уверенность для ИИ-строки: 0.6 базово + бонус за арифметику."""
    score = 0.60
    if qty is not None:
        score += 0.10
    if price is not None:
        score += 0.10
    if total is not None:
        score += 0.10
    if qty and price and total and total > 0:
        if abs(qty * price - total) / total < 0.02:
            score = min(score + 0.10, 1.0)
    return round(score, 2)


# ── Yandex Vision OCR ──────────────────────────────────────────────────────

def ocr_pdf_raw(filepath: str) -> dict:
    """Возвращает сырой JSON-ответ Yandex Vision OCR."""
    import urllib.request
    import urllib.error

    api_key = os.environ.get('YANDEX_GPT_API_KEY', '')
    folder_id = os.environ.get('YANDEX_FOLDER_ID', '')
    if not api_key or not folder_id:
        return {}

    with open(filepath, 'rb') as f:
        content_b64 = base64.b64encode(f.read()).decode('ascii')

    payload = {
        'mimeType': 'application/pdf',
        'languageCodes': ['ru', 'en'],
        'model': 'table',
        'content': content_b64,
    }
    data = json.dumps(payload, ensure_ascii=False).encode('utf-8')
    req = urllib.request.Request(
        'https://ocr.api.cloud.yandex.net/ocr/v1/recognizeText',
        data=data,
        headers={
            'Content-Type': 'application/json',
            'Authorization': f'Api-Key {api_key}',
            'x-folder-id': folder_id,
            'x-data-logging-enabled': 'false',
        },
        method='POST',
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read().decode('utf-8'))
    except urllib.error.HTTPError as e:
        raise RuntimeError(f'Yandex Vision OCR HTTP {e.code}: {e.read().decode()}')


def ocr_pdf(filepath: str) -> str:
    """
    Распознаёт скан-PDF через Yandex Vision OCR.
    Возвращает TSV-текст с реконструированными колонками таблицы,
    или плоский текст если координаты недоступны.
    """
    import urllib.request
    import urllib.error

    body = ocr_pdf_raw(filepath)
    if not body:
        return ''
    return _extract_ocr_text_filtered(body)


def _get_line_y(line: dict) -> int:
    """Возвращает Y-координату центра строки из boundingBox."""
    try:
        verts = line['boundingBox']['vertices']
        ys = [int(v.get('y', 0)) for v in verts]
        return (min(ys) + max(ys)) // 2
    except (KeyError, TypeError, ValueError):
        return 0


def _get_line_x(line: dict) -> int:
    """Возвращает X-координату левого края строки."""
    try:
        verts = line['boundingBox']['vertices']
        xs = [int(v.get('x', 0)) for v in verts]
        return min(xs)
    except (KeyError, TypeError, ValueError):
        return 0


def _merge_names_with_table(body: dict) -> str:
    """
    Соединяет текстовые блоки из левой части страницы (Наименование, Ед.изм)
    с ячейками детектированной таблицы (числа) по Y-координате.
    """
    annotation = (body.get('result') or {}).get('textAnnotation') or {}
    tables = annotation.get('tables') or []
    if not tables:
        return ''

    table = tables[0]

    # Граница таблицы по X — левый край детектированной таблицы
    try:
        xs = [int(v.get('x', 0)) for v in table['boundingBox']['vertices']]
        table_left_x = min(xs)
    except Exception:
        table_left_x = 3000

    rows_n = int(table.get('rowCount', 0))
    cols_n = int(table.get('columnCount', 0))
    if rows_n < 2:
        return ''

    # Строим матрицу таблицы + Y-центр каждой строки
    grid: list[list[str]] = [['' for _ in range(cols_n)] for _ in range(rows_n)]
    row_y: list[float] = [0.0] * rows_n
    row_cell_count: list[int] = [0] * rows_n

    for cell in table.get('cells') or []:
        r = int(cell.get('rowIndex', 0))
        c = int(cell.get('columnIndex', 0))
        if r >= rows_n or c >= cols_n:
            continue
        grid[r][c] = (cell.get('text') or '').replace('\n', ' ').strip()
        try:
            verts = cell['boundingBox']['vertices']
            ys = [int(v.get('y', 0)) for v in verts]
            row_y[r] += (min(ys) + max(ys)) / 2
            row_cell_count[r] += 1
        except Exception:
            pass

    for r in range(rows_n):
        if row_cell_count[r]:
            row_y[r] /= row_cell_count[r]

    # Собираем текстовые блоки из левой части страницы
    left_lines: list[dict] = []  # {text, y_center}
    for block in annotation.get('blocks') or []:
        try:
            bverts = block['boundingBox']['vertices']
            bxs = [int(v.get('x', 0)) for v in bverts]
            bys = [int(v.get('y', 0)) for v in bverts]
            if min(bxs) >= table_left_x:
                continue  # блок в зоне таблицы, пропускаем
            block_cx = (min(bxs) + max(bxs)) // 2
            if block_cx >= table_left_x:
                continue
        except Exception:
            continue

        for line in block.get('lines') or []:
            text = (line.get('text') or '').strip()
            if not text:
                continue
            try:
                lverts = line['boundingBox']['vertices']
                lys = [int(v.get('y', 0)) for v in lverts]
                cy = (min(lys) + max(lys)) / 2
            except Exception:
                continue
            left_lines.append({'text': text, 'y': cy})

    # Определяем колонки числовой части (qty, price, total) в таблице
    col_qty = col_price = col_total = -1
    for row in grid[:4]:
        for ci, cell in enumerate(row):
            t = cell.lower()
            if col_qty   == -1 and any(k in t for k in ['объем', 'объём']): col_qty   = ci
            if col_price == -1 and 'цена' in t:                               col_price = ci
            if col_total == -1 and any(k in t for k in ['общая', 'стоимость', 'итого']): col_total = ci

    def _get(row, ci):
        return row[ci] if ci >= 0 and ci < len(row) else ''

    # Для каждой строки таблицы ищем ближайший текстовый блок слева
    TOLERANCE = 120  # px — допуск по Y для совпадения
    result_lines = ['Строки сметы (Наименование | Объём | Цена | Итого):', '']

    for r in range(rows_n):
        qty_v   = _get(grid[r], col_qty)
        price_v = _get(grid[r], col_price)
        total_v = _get(grid[r], col_total)
        if not any([qty_v, price_v, total_v]):
            continue

        ry = row_y[r]
        # Ближайший левый блок по Y
        best = min(left_lines, key=lambda l: abs(l['y'] - ry), default=None)
        name_v = best['text'] if best and abs(best['y'] - ry) <= TOLERANCE else ''

        result_lines.append(
            f'Наименование: {name_v} | Объём: {qty_v} | Цена: {price_v} | Итого: {total_v}'
        )

    return '\n'.join(result_lines)


def _format_ocr_table(body: dict) -> str:
    """
    Форматирует распознанную OCR-таблицу (model=table) в читаемый текст
    с явными метками колонок для передачи в GPT.
    Определяет колонки по заголовкам и возвращает строки вида:
    «Наименование: X | Ед.: Y | Объём: Z | Цена: W | Итого: V»
    """
    annotation = (body.get('result') or {}).get('textAnnotation') or {}
    tables = annotation.get('tables') or []
    if not tables:
        return ''

    table = tables[0]
    rows_n = int(table.get('rowCount', 0))
    cols_n = int(table.get('columnCount', 0))
    if rows_n < 2 or cols_n < 2:
        return ''

    # Строим матрицу ячеек
    grid: list[list[str]] = [['' for _ in range(cols_n)] for _ in range(rows_n)]
    for cell in table.get('cells') or []:
        r = int(cell.get('rowIndex', 0))
        c = int(cell.get('columnIndex', 0))
        if r < rows_n and c < cols_n:
            grid[r][c] = (cell.get('text') or '').replace('\n', ' ').strip()

    # Определяем индексы колонок по заголовкам (ищем в первых 3 строках)
    col_name = col_unit = col_qty = col_price = col_total = -1
    _kw = {
        'name':  ['наименование', 'работ и затрат', 'вид работ'],
        'unit':  ['ед.изм', 'ед. изм', 'единица'],
        'qty':   ['объем', 'объём', 'кол-во', 'количество'],
        'price': ['цена'],
        'total': ['общая', 'стоимость', 'итого', 'сумма'],
    }
    for row in grid[:4]:
        for ci, cell in enumerate(row):
            t = cell.lower()
            if col_name  == -1 and any(k in t for k in _kw['name']):  col_name  = ci
            if col_unit  == -1 and any(k in t for k in _kw['unit']):  col_unit  = ci
            if col_qty   == -1 and any(k in t for k in _kw['qty']):   col_qty   = ci
            if col_price == -1 and any(k in t for k in _kw['price']): col_price = ci
            if col_total == -1 and any(k in t for k in _kw['total']): col_total = ci

    def _get(row, ci):
        return row[ci].strip() if ci >= 0 and ci < len(row) else ''

    # Если ключевые колонки найдены — форматируем с метками
    if col_name >= 0 and col_total >= 0:
        lines = [
            f'Структура таблицы (OCR с детекцией колонок):',
            f'  col{col_name}=Наименование | col{col_unit}=Ед. | '
            f'col{col_qty}=Объём | col{col_price}=Цена | col{col_total}=Итого', '',
        ]
        for row in grid:
            nv = _get(row, col_name)
            uv = _get(row, col_unit)
            qv = _get(row, col_qty)
            pv = _get(row, col_price)
            tv = _get(row, col_total)
            if not any([nv, qv, pv, tv]):
                continue
            lines.append(
                f'Наименование: {nv} | Ед.: {uv} | Объём: {qv} | Цена: {pv} | Итого: {tv}'
            )
        return '\n'.join(lines)

    # Fallback — сырая сетка
    lines = []
    for row in grid:
        row_text = ' | '.join(v for v in row if v)
        if row_text.strip():
            lines.append(row_text)
    return '\n'.join(lines)


def _reconstruct_table(body: dict) -> str:
    """
    Реконструирует порядок чтения из блоков OCR с координатами.
    Группирует блоки по Y (строки), сортирует по X внутри строки.
    Отрезает нижнюю треть страницы (реквизиты, подписи).
    Возвращает текст в правильном порядке чтения или '' если координат нет.
    """
    annotation = (body.get('result') or {}).get('textAnnotation') or {}
    page_h = int(annotation.get('height') or 0)

    # Граница зоны таблицы — верхние 72% страницы
    table_zone_max_y = int(page_h * 0.72) if page_h else 999999

    # Собираем все СТРОКИ из blocks с их центром Y
    ocr_lines: list[dict] = []  # {text, x, y}
    for block in annotation.get('blocks') or []:
        for line in block.get('lines') or []:
            text = line.get('text', '').strip()
            if not text:
                continue
            try:
                verts = line['boundingBox']['vertices']
                xs = [int(v.get('x', 0)) for v in verts]
                ys = [int(v.get('y', 0)) for v in verts]
                cy = (min(ys) + max(ys)) // 2
                cx = min(xs)
            except (KeyError, TypeError):
                continue
            if cy > table_zone_max_y:
                continue  # реквизиты и подписи
            ocr_lines.append({'text': text, 'x': cx, 'y': cy})

    if not ocr_lines:
        return ''

    # Группируем строки с близким Y (допуск 18px) в горизонтальные полосы
    ocr_lines.sort(key=lambda l: l['y'])
    bands: list[list[dict]] = []
    current: list[dict] = [ocr_lines[0]]
    for ln in ocr_lines[1:]:
        if abs(ln['y'] - current[0]['y']) <= 18:
            current.append(ln)
        else:
            bands.append(current)
            current = [ln]
    bands.append(current)

    # Внутри каждой полосы — сортируем по X (левая → правая)
    result_lines = []
    for band in bands:
        band.sort(key=lambda l: l['x'])
        merged = '  '.join(l['text'] for l in band)
        if merged.strip():
            result_lines.append(merged)

    return '\n'.join(result_lines)


def _extract_ocr_text(body: dict) -> str:
    """Плоский текст из OCR-ответа."""
    lines = []
    annotation = (body.get('result') or {}).get('textAnnotation') or {}
    for line in annotation.get('lines') or []:
        t = line.get('text', '').strip()
        if t:
            lines.append(t)
    if not lines:
        for block in annotation.get('blocks') or []:
            for line in block.get('lines') or []:
                t = line.get('text', '').strip()
                if t:
                    lines.append(t)
    return '\n'.join(lines)


# Паттерны реквизитов — строки которые не относятся к таблице работ
_REKVIZITY_RE = re.compile(
    r'(инн|огрн|р/сч|к/с|бик|тел\.|e-mail|юридический адрес|пермский край|'
    r'индивидуальный предприниматель|управляющая организация|генподрядчик|подрядчик|'
    r'в\.в\.|м\.и\.|м\.п\.|филиал|банк|доверенности|российская федерация)',
    re.IGNORECASE,
)


def _extract_ocr_text_filtered(body: dict) -> str:
    """
    Плоский текст из OCR с фильтрацией реквизитов и нерелевантных блоков.
    Обрезает всё после маркера конца таблицы (итоговая стоимость, подписи).
    """
    raw = _extract_ocr_text(body)
    lines = raw.splitlines()
    result = []
    for line in lines:
        stripped = line.strip()
        # Стоп: дошли до блока реквизитов или подписей
        if re.search(r'(подрядчик|генподрядчик|инн\s+\d|огрнип|р/сч)', stripped, re.IGNORECASE):
            break
        # Пропускаем строки реквизитов
        if _REKVIZITY_RE.search(stripped):
            continue
        result.append(line)
    return '\n'.join(result)
