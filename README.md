# ШТАБ

Платформа управления строительством полного цикла и документооборотом для девелопера.

От заведения объекта и этапов до закрытия работ, согласования первичных документов (КС-2/КС-3) и снабжения давальческим материалом.

## Стек

- Flask + Flask-Login
- PostgreSQL (psycopg2)
- Bootstrap 5.3.3 + Inter
- openpyxl (Excel-экспорт, генерация КС)

## Запуск (локально)

```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # заполнить DATABASE_URL, SECRET_KEY и т.д.
python app.py
```
