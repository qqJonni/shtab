#!/usr/bin/env python3
"""
One-shot migration: SQLite (shtab.db) → PostgreSQL.
Preserves all primary keys; resets sequences after load.
Run once, then discard.
"""

import sqlite3
import os
import sys
import psycopg2
import psycopg2.extras
from dotenv import load_dotenv

load_dotenv()

SQLITE_PATH = os.path.join(os.path.dirname(__file__), 'shtab.db')
PG_URL = os.environ['DATABASE_URL']

# FK-safe load order (parents before children)
TABLE_ORDER = [
    'settings',
    'defect_types',
    'organizations',
    'objects',
    'users',
    'guest_tokens',
    'construction_stages',
    'substages',
    'substage_photos',
    'stage_documents',
    'defects',
    'defect_photos',
    'defect_audio',
    'defect_history',
    'object_plans',
    'journal_entries',
    'journal_photos',
    'doc_packages',
    'package_documents',
    'approval_steps',
    'material_requests',
    'material_request_items',
    'material_request_history',
    'notifications',
]

# Column renames: (sqlite_col) → (pg_col) per table
COL_RENAMES = {
    'material_requests': {'current_role': 'route_role'},
}


def get_pg_columns(pg_cur, table):
    pg_cur.execute(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_schema='public' AND table_name=%s ORDER BY ordinal_position",
        (table,)
    )
    return [r[0] for r in pg_cur.fetchall()]


def migrate_table(sqlite_conn, pg_conn, table):
    renames = COL_RENAMES.get(table, {})

    # Read all rows from SQLite
    sq_cur = sqlite_conn.cursor()
    sq_cur.execute(f'SELECT * FROM "{table}"')
    rows = sq_cur.fetchall()
    if not rows:
        return 0

    sqlite_cols = [desc[0] for desc in sq_cur.description]

    # Build PG column names (apply renames)
    pg_col_names = [renames.get(c, c) for c in sqlite_cols]

    # Get actual PG columns to filter out any that don't exist
    pg_cur = pg_conn.cursor()
    pg_existing = get_pg_columns(pg_cur, table)

    # Only keep columns that exist in PG
    filtered = [(sc, pc) for sc, pc in zip(sqlite_cols, pg_col_names) if pc in pg_existing]
    if not filtered:
        print(f'  WARNING: no matching columns for {table}')
        return 0

    sq_cols_f = [f[0] for f in filtered]
    pg_cols_f = [f[1] for f in filtered]

    # Build INSERT with explicit column list (preserves id)
    placeholders = ', '.join(['%s'] * len(pg_cols_f))
    col_list = ', '.join(f'"{c}"' for c in pg_cols_f)
    sql = f'INSERT INTO "{table}" ({col_list}) VALUES ({placeholders}) ON CONFLICT (id) DO NOTHING'

    # Map rows to tuples in correct column order
    sq_idx = {c: i for i, c in enumerate(sqlite_cols)}
    batch = []
    for row in rows:
        vals = tuple(row[sq_idx[sc]] for sc in sq_cols_f)
        batch.append(vals)

    pg_cur.executemany(sql, batch)
    pg_conn.commit()
    pg_cur.close()
    return len(batch)


def reset_sequences(pg_conn):
    pg_cur = pg_conn.cursor()
    # Get all tables with SERIAL sequences
    pg_cur.execute("""
        SELECT table_name
        FROM information_schema.tables
        WHERE table_schema = 'public' AND table_type = 'BASE TABLE'
    """)
    tables = [r[0] for r in pg_cur.fetchall()]

    for t in tables:
        try:
            pg_cur.execute(f"""
                SELECT setval(
                    pg_get_serial_sequence('{t}', 'id'),
                    (SELECT COALESCE(MAX(id), 1) FROM "{t}")
                )
            """)
            pg_conn.commit()
        except Exception:
            pg_conn.rollback()
            # Table has no sequence (no SERIAL id) — skip

    pg_cur.close()


def row_counts(sqlite_conn, pg_conn):
    sq_cur = sqlite_conn.cursor()
    pg_cur = pg_conn.cursor()

    print('\n' + '='*62)
    print(f'{"Table":<30} {"SQLite":>8} {"PG":>8} {"Match":>6}')
    print('='*62)

    all_ok = True
    for t in TABLE_ORDER:
        sq_cur.execute(f'SELECT COUNT(*) FROM "{t}"')
        sq_n = sq_cur.fetchone()[0]

        pg_cur.execute(f'SELECT COUNT(*) FROM "{t}"')
        pg_n = pg_cur.fetchone()[0]

        match = '✓' if sq_n == pg_n else '✗ MISMATCH'
        if sq_n != pg_n:
            all_ok = False
        print(f'{t:<30} {sq_n:>8} {pg_n:>8} {match:>6}')

    print('='*62)
    pg_cur.close()
    return all_ok


def check_referential_integrity(pg_conn):
    pg_cur = pg_conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    issues = []

    checks = [
        # defects with plan_id point to existing object_plans
        ("Defects → object_plans",
         "SELECT COUNT(*) AS n FROM defects d "
         "WHERE d.plan_id IS NOT NULL "
         "AND NOT EXISTS (SELECT 1 FROM object_plans p WHERE p.id = d.plan_id)"),
        # defect_photos → defects
        ("defect_photos → defects",
         "SELECT COUNT(*) AS n FROM defect_photos dp "
         "WHERE NOT EXISTS (SELECT 1 FROM defects d WHERE d.id = dp.defect_id)"),
        # defect_audio → defects
        ("defect_audio → defects",
         "SELECT COUNT(*) AS n FROM defect_audio da "
         "WHERE NOT EXISTS (SELECT 1 FROM defects d WHERE d.id = da.defect_id)"),
        # defect_history → defects
        ("defect_history → defects",
         "SELECT COUNT(*) AS n FROM defect_history dh "
         "WHERE NOT EXISTS (SELECT 1 FROM defects d WHERE d.id = dh.defect_id)"),
        # approval_steps → doc_packages
        ("approval_steps → doc_packages",
         "SELECT COUNT(*) AS n FROM approval_steps a "
         "WHERE NOT EXISTS (SELECT 1 FROM doc_packages p WHERE p.id = a.package_id)"),
        # package_documents → doc_packages
        ("package_documents → doc_packages",
         "SELECT COUNT(*) AS n FROM package_documents pd "
         "WHERE NOT EXISTS (SELECT 1 FROM doc_packages p WHERE p.id = pd.package_id)"),
        # journal_photos → journal_entries
        ("journal_photos → journal_entries",
         "SELECT COUNT(*) AS n FROM journal_photos jp "
         "WHERE NOT EXISTS (SELECT 1 FROM journal_entries je WHERE je.id = jp.entry_id)"),
        # substages → construction_stages
        ("substages → construction_stages",
         "SELECT COUNT(*) AS n FROM substages s "
         "WHERE NOT EXISTS (SELECT 1 FROM construction_stages cs WHERE cs.id = s.stage_id)"),
    ]

    print('\nРеляционная целостность:')
    all_ok = True
    for label, sql in checks:
        pg_cur.execute(sql)
        row = pg_cur.fetchone()
        broken = row['n']
        status = '✓' if broken == 0 else f'✗ {broken} сломанных ссылок'
        if broken:
            all_ok = False
            issues.append(f'{label}: {broken} broken')
        print(f'  {label:<40} {status}')

    pg_cur.close()
    return all_ok, issues


def main():
    if not os.path.exists(SQLITE_PATH):
        print(f'ERROR: SQLite file not found: {SQLITE_PATH}')
        sys.exit(1)

    print(f'SQLite: {SQLITE_PATH}')
    print(f'PG:     {PG_URL}\n')

    sqlite_conn = sqlite3.connect(SQLITE_PATH)
    sqlite_conn.row_factory = sqlite3.Row

    pg_conn = psycopg2.connect(PG_URL)

    # Clear PG tables in reverse order to avoid FK violations
    print('Очищаю таблицы PG (обратный порядок FK)...')
    pg_cur = pg_conn.cursor()
    for t in reversed(TABLE_ORDER):
        pg_cur.execute(f'DELETE FROM "{t}"')
    pg_conn.commit()
    pg_cur.close()
    print('Готово.\n')

    # Migrate
    print('Переношу данные...')
    for table in TABLE_ORDER:
        n = migrate_table(sqlite_conn, pg_conn, table)
        print(f'  {table:<35} {n} строк')

    # Reset sequences
    print('\nСбрасываю сиквенсы...')
    reset_sequences(pg_conn)
    print('Готово.')

    # Verification
    counts_ok = row_counts(sqlite_conn, pg_conn)
    refs_ok, issues = check_referential_integrity(pg_conn)

    print()
    if counts_ok and refs_ok:
        print('✓ Миграция успешна. Все строки перенесены, связи целы.')
    else:
        print('✗ Есть проблемы:')
        if not counts_ok:
            print('  - Расхождение числа строк (см. таблицу выше)')
        for i in issues:
            print(f'  - {i}')
        sys.exit(1)

    sqlite_conn.close()
    pg_conn.close()


if __name__ == '__main__':
    main()
