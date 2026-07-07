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
      "name": "Устройство стяжки",
      "unit": "м2",
      "quantity": 45.5,
      "unit_price": 350.00,
      "total": 15925.00
    }
  ]
}"""

_SYSTEM_PROMPT = (
    "Ты — парсер строительных смет. Извлеки позиции работ из текста. "
    "Верни ТОЛЬКО валидный JSON по заданной схеме. "
    "Не домысливай: если поле неизвестно — оставь null или пустую строку. "
    "Пропускай строки-разделы, заголовки групп, итоговые строки без конкретной работы. "
    "Единицы измерения бери как есть из текста."
)

_USER_PROMPT_TPL = (
    "Текст сметы:\n---\n{table_text}\n---\n\n"
    "Верни JSON строго по схеме (без пояснений, только JSON):\n{schema}"
)


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

def ocr_pdf(filepath: str) -> str:
    """
    Распознаёт текст скан-PDF через Yandex Vision OCR.
    Возвращает распознанный текст или '' при ошибке/отсутствии ключей.
    Данные уходят в Yandex Cloud (ЦОД в РФ).
    Env: YANDEX_GPT_API_KEY, YANDEX_FOLDER_ID
    """
    import urllib.request
    import urllib.error

    api_key = os.environ.get('YANDEX_GPT_API_KEY', '')
    folder_id = os.environ.get('YANDEX_FOLDER_ID', '')
    if not api_key or not folder_id:
        return ''

    with open(filepath, 'rb') as f:
        content_b64 = base64.b64encode(f.read()).decode('ascii')

    payload = {
        'mimeType': 'application/pdf',
        'languageCodes': ['ru', 'en'],
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
            body = json.loads(resp.read().decode('utf-8'))
    except urllib.error.HTTPError as e:
        raise RuntimeError(f'Yandex Vision OCR HTTP {e.code}: {e.read().decode()}')

    return _extract_ocr_text(body)


def _extract_ocr_text(body: dict) -> str:
    """Собирает строки из ответа Yandex Vision OCR в плоский текст."""
    lines = []

    # Новый формат: result.textAnnotation.blocks[].lines[].text
    annotation = (body.get('result') or {}).get('textAnnotation') or {}
    # Прямой список строк (если есть)
    for line in annotation.get('lines') or []:
        t = line.get('text', '').strip()
        if t:
            lines.append(t)

    if not lines:
        # Альтернатив: обходим блоки вручную
        for block in annotation.get('blocks') or []:
            for line in block.get('lines') or []:
                t = line.get('text', '').strip()
                if t:
                    lines.append(t)

    return '\n'.join(lines)
