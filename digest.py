"""
object_digest(obj_id, period_days=7) -> dict

Единая функция сводки по объекту для дайджест-экрана и PDF.
Только детерминированный SQL, никакого ИИ.

Источник денег — substages.total_price (volume × unit_price).
doc_packages не имеет поля amount — пакеты не суммируются деньгами.

Временны́е метки (реальные поля в БД):
  substages.completed_at       — подэтап завершён
  doc_packages.completed_at    — пакет завершён
  doc_packages.submitted_at    — пакет отправлен на согласование
  defects.verified_at          — замечание принято технадзором
  defects.resolved_at          — замечание устранено подрядчиком
  approval_steps.acted_at      — шаг согласования исполнен
"""

from datetime import date, timedelta
from db import query_db


# Метки приоритетов для сортировки и отображения
_PRIORITY_ORDER = (
    "CASE priority WHEN 'critical' THEN 1 WHEN 'high' THEN 2 "
    "WHEN 'normal' THEN 3 WHEN 'low' THEN 4 ELSE 5 END"
)

_ROLE_LABELS = {
    'inspector': 'Технадзор',
    'pto':       'ПТО',
    'manager':   'Руководитель',
    'accountant': 'Бухгалтерия',
    'foreman':   'Прораб',
    'contractor': 'Подрядчик',
}


def _days_overdue(plan_end_date: str, today: str) -> int:
    """Число дней просрочки (> 0 если просрочено)."""
    try:
        return (date.fromisoformat(today) - date.fromisoformat(plan_end_date)).days
    except Exception:
        return 0


def object_digest(obj_id: int, period_days: int = 7) -> dict:
    today    = date.today().isoformat()
    since    = (date.today() - timedelta(days=period_days)).isoformat()
    stall_threshold = since   # пакет «зависший», если submitted_at < since

    # ════════════════════════════════════════════════════════════════════════
    # ФИНАНСЫ
    # Источник: substages.total_price (надёжно: volume × unit_price).
    # contract_sum — SUM(cs.contract_amount), может быть частично NULL.
    # ════════════════════════════════════════════════════════════════════════

    contract_row = query_db(
        "SELECT COALESCE(SUM(contract_amount), 0) as s "
        "FROM construction_stages WHERE object_id = ?",
        (obj_id,), one=True)
    contract_sum = contract_row['s'] or 0

    # Все подэтапы с ценой и статусом одним запросом
    sub_money = query_db(
        "SELECT ss.id, ss.name, ss.status, ss.stage_id, "
        "COALESCE(ss.total_price, 0) as tp, "
        "ss.completed_at, ss.plan_end_date "
        "FROM substages ss "
        "JOIN construction_stages cs ON ss.stage_id = cs.id "
        "WHERE cs.object_id = ?", (obj_id,))

    smeta_sum       = sum(r['tp'] for r in sub_money)
    completed_sum   = sum(r['tp'] for r in sub_money
                         if r['status'] in ('done', 'closed', 'approved'))
    in_progress_sum = sum(r['tp'] for r in sub_money
                         if r['status'] == 'in_progress')

    # Выполнено за период (completed_at попадает в окно)
    period_completed_subs = [
        r for r in sub_money
        if r['completed_at'] and r['completed_at'][:10] >= since
        and r['status'] in ('done', 'closed', 'approved')
    ]
    period_completed_sum = sum(r['tp'] for r in period_completed_subs)

    # % выполнения от суммы договоров
    contract_pct = round(completed_sum / contract_sum * 100) if contract_sum else None

    # ════════════════════════════════════════════════════════════════════════
    # ПОДЭТАПЫ — прогресс и просрочка
    # ════════════════════════════════════════════════════════════════════════

    sub_total       = len(sub_money)
    sub_done        = sum(1 for s in sub_money
                         if s['status'] in ('done', 'closed', 'approved'))
    sub_in_progress = sum(1 for s in sub_money if s['status'] == 'in_progress')
    sub_not_started = sum(1 for s in sub_money if s['status'] == 'not_started')
    progress_pct    = round(sub_done / sub_total * 100) if sub_total else 0

    # Просроченные подэтапы — список с деталями
    overdue_subs_raw = query_db(
        "SELECT ss.id, ss.name, ss.status, ss.plan_end_date, ss.stage_id, "
        "cs.name as stage_name "
        "FROM substages ss "
        "JOIN construction_stages cs ON ss.stage_id = cs.id "
        "WHERE cs.object_id = ? "
        "AND ss.plan_end_date IS NOT NULL AND ss.plan_end_date < ? "
        "AND ss.status NOT IN ('done', 'closed', 'approved') "
        "ORDER BY ss.plan_end_date ASC",
        (obj_id, today))
    overdue_subs = [
        {**dict(r), 'days_overdue': _days_overdue(r['plan_end_date'], today)}
        for r in overdue_subs_raw
    ]

    # Завершённые за период — список
    period_done_subs = query_db(
        "SELECT ss.id, ss.name, ss.status, ss.completed_at, "
        "COALESCE(ss.total_price, 0) as total_price, "
        "cs.name as stage_name "
        "FROM substages ss "
        "JOIN construction_stages cs ON ss.stage_id = cs.id "
        "WHERE cs.object_id = ? "
        "AND ss.completed_at IS NOT NULL AND ss.completed_at >= ? "
        "AND ss.status IN ('done', 'closed', 'approved') "
        "ORDER BY ss.completed_at DESC",
        (obj_id, since))

    # ════════════════════════════════════════════════════════════════════════
    # ЭТАПЫ
    # ════════════════════════════════════════════════════════════════════════

    stages_raw = query_db(
        "SELECT cs.id, cs.name, cs.status, cs.plan_start_date, cs.plan_end_date, "
        "cs.contract_amount, cs.contract_number, org.name as contractor_name "
        "FROM construction_stages cs "
        "LEFT JOIN organizations org ON cs.contractor_id = org.id "
        "WHERE cs.object_id = ? ORDER BY cs.order_num", (obj_id,))

    stages_list = []
    for s in stages_raw:
        st = dict(s)
        sub_rows = [r for r in sub_money if r['stage_id'] == s['id']]
        st_total = len(sub_rows)
        st_done  = sum(1 for r in sub_rows if r['status'] in ('done', 'closed', 'approved'))
        st['sub_total']     = st_total
        st['sub_done']      = st_done
        st['progress_pct']  = round(st_done / st_total * 100) if st_total else 0
        st['smeta_sum']     = sum(r['tp'] for r in sub_rows)
        st['completed_sum'] = sum(r['tp'] for r in sub_rows
                                  if r['status'] in ('done', 'closed', 'approved'))
        st['overdue'] = bool(
            s['plan_end_date'] and s['plan_end_date'] < today
            and s['status'] not in ('done', 'suspended')
        )
        if st['overdue']:
            st['days_overdue'] = _days_overdue(s['plan_end_date'], today)
        stages_list.append(st)

    stage_total   = len(stages_list)
    stage_done    = sum(1 for s in stages_list if s['status'] == 'done')
    stage_overdue = sum(1 for s in stages_list if s['overdue'])
    overdue_stages = [s for s in stages_list if s['overdue']]

    # ════════════════════════════════════════════════════════════════════════
    # ЗАМЕЧАНИЯ
    # ════════════════════════════════════════════════════════════════════════

    # Счётчики
    defects_open = query_db(
        "SELECT COUNT(*) as c FROM defects "
        "WHERE object_id = ? AND status IN ('open', 'in_progress')",
        (obj_id,), one=True)['c']

    defects_overdue = query_db(
        "SELECT COUNT(*) as c FROM defects "
        "WHERE object_id = ? AND due_date < ? "
        "AND status NOT IN ('closed', 'verified', 'rejected')",
        (obj_id, today), one=True)['c']

    # Устранены за период (status='resolved' — ждут проверки технадзором)
    defects_resolved_week = query_db(
        "SELECT COUNT(*) as c FROM defects "
        "WHERE object_id = ? AND status = 'resolved' "
        "AND resolved_at >= ?",
        (obj_id, since), one=True)['c']

    # Закрыты/приняты за период (verified_at)
    defects_verified_week = query_db(
        "SELECT COUNT(*) as c FROM defects "
        "WHERE object_id = ? AND status IN ('closed', 'verified') "
        "AND verified_at >= ?",
        (obj_id, since), one=True)['c']

    # Топ-5 открытых по приоритету + просрочке
    top_defects = query_db(
        f"SELECT d.id, d.title, d.priority, d.due_date, d.status, "
        f"d.resolved_at, cs.name as stage_name "
        f"FROM defects d "
        f"LEFT JOIN construction_stages cs ON d.stage_id = cs.id "
        f"WHERE d.object_id = ? "
        f"AND d.status NOT IN ('closed', 'verified', 'rejected') "
        f"ORDER BY {_PRIORITY_ORDER}, d.due_date ASC NULLS LAST LIMIT 5",
        (obj_id,))
    top_defects_list = []
    for r in top_defects:
        d = dict(r)
        d['overdue'] = bool(r['due_date'] and r['due_date'] < today)
        if d['overdue']:
            d['days_overdue'] = _days_overdue(r['due_date'], today)
        top_defects_list.append(d)

    # ════════════════════════════════════════════════════════════════════════
    # СОГЛАСОВАНИЯ
    # ════════════════════════════════════════════════════════════════════════

    # Зависшие пакеты — список с ролью, которая держит, и числом дней
    stalled_pkgs_raw = query_db(
        "SELECT dp.id, dp.submitted_at, dp.status, ss.name as substage_name, "
        "a.role as pending_role "
        "FROM doc_packages dp "
        "JOIN substages ss ON dp.substage_id = ss.id "
        "JOIN construction_stages cs ON ss.stage_id = cs.id "
        "JOIN approval_steps a ON a.package_id = dp.id "
        "WHERE cs.object_id = ? AND dp.status = 'in_review' "
        "AND a.status = 'pending' "
        "AND dp.submitted_at IS NOT NULL AND dp.submitted_at < ? "
        "ORDER BY dp.submitted_at ASC",
        (obj_id, stall_threshold))
    stalled_pkgs = []
    for r in stalled_pkgs_raw:
        p = dict(r)
        p['days_waiting'] = _days_overdue(r['submitted_at'][:10], today)
        p['pending_role_label'] = _ROLE_LABELS.get(r['pending_role'], r['pending_role'])
        stalled_pkgs.append(p)

    # Возвращённые подрядчику
    returned_pkgs = query_db(
        "SELECT dp.id, dp.created_at, ss.name as substage_name, "
        "cs.name as stage_name "
        "FROM doc_packages dp "
        "JOIN substages ss ON dp.substage_id = ss.id "
        "JOIN construction_stages cs ON ss.stage_id = cs.id "
        "WHERE cs.object_id = ? AND dp.status = 'returned' "
        "ORDER BY dp.created_at DESC",
        (obj_id,))

    pkgs_in_review = query_db(
        "SELECT COUNT(*) as c FROM doc_packages dp "
        "JOIN substages ss ON dp.substage_id = ss.id "
        "JOIN construction_stages cs ON ss.stage_id = cs.id "
        "WHERE cs.object_id = ? AND dp.status = 'in_review'",
        (obj_id,), one=True)['c']

    pkgs_completed = query_db(
        "SELECT COUNT(*) as c FROM doc_packages dp "
        "JOIN substages ss ON dp.substage_id = ss.id "
        "JOIN construction_stages cs ON ss.stage_id = cs.id "
        "WHERE cs.object_id = ? AND dp.status = 'completed'",
        (obj_id,), one=True)['c']

    pkgs_completed_week = query_db(
        "SELECT COUNT(*) as c FROM doc_packages dp "
        "JOIN substages ss ON dp.substage_id = ss.id "
        "JOIN construction_stages cs ON ss.stage_id = cs.id "
        "WHERE cs.object_id = ? AND dp.status = 'completed' "
        "AND dp.completed_at >= ?",
        (obj_id, since), one=True)['c']

    # Сводка по ролям, на которых сейчас стоят pending-шаги
    stalled_by_role = query_db(
        "SELECT a.role, COUNT(*) as c "
        "FROM approval_steps a "
        "JOIN doc_packages dp ON a.package_id = dp.id "
        "JOIN substages ss ON dp.substage_id = ss.id "
        "JOIN construction_stages cs ON ss.stage_id = cs.id "
        "WHERE cs.object_id = ? AND dp.status = 'in_review' "
        "AND a.status = 'pending' "
        "GROUP BY a.role ORDER BY c DESC",
        (obj_id,))

    # ════════════════════════════════════════════════════════════════════════
    # ЖУРНАЛ — последние записи за период
    # ════════════════════════════════════════════════════════════════════════

    journal_entries = query_db(
        "SELECT je.id, je.entry_date, je.work_type, je.text, je.weather, "
        "u.full_name as author_name, org.name as contractor_name "
        "FROM journal_entries je "
        "LEFT JOIN users u ON je.author_id = u.id "
        "LEFT JOIN organizations org ON je.contractor_id = org.id "
        "WHERE je.object_id = ? AND je.entry_date >= ? "
        "ORDER BY je.entry_date DESC LIMIT 20",
        (obj_id, since))

    # ════════════════════════════════════════════════════════════════════════
    # СБОРКА
    # ════════════════════════════════════════════════════════════════════════

    return {
        # ── Финансы ──
        'contract_sum':          contract_sum,
        'smeta_sum':             smeta_sum,
        'completed_sum':         completed_sum,
        'in_progress_sum':       in_progress_sum,
        'remaining_sum':         smeta_sum - completed_sum,
        'period_completed_sum':  period_completed_sum,   # выполнено за период
        'contract_pct':          contract_pct,           # % от суммы договоров (None если договор=0)

        # ── Подэтапы — сводка ──
        'sub_total':        sub_total,
        'sub_done':         sub_done,
        'sub_in_progress':  sub_in_progress,
        'sub_not_started':  sub_not_started,
        'sub_overdue':      len(overdue_subs),
        'sub_done_week':    len(period_done_subs),
        'progress_pct':     progress_pct,

        # ── Подэтапы — списки ──
        'overdue_subs':     [dict(r) for r in overdue_subs],
        'period_done_subs': [dict(r) for r in period_done_subs],

        # ── Этапы ──
        'stage_total':    stage_total,
        'stage_done':     stage_done,
        'stage_overdue':  stage_overdue,
        'overdue_stages': overdue_stages,
        'stages':         stages_list,

        # ── Замечания ──
        'defects_open':           defects_open,
        'defects_overdue':        defects_overdue,
        'defects_resolved_week':  defects_resolved_week,  # устранены, ждут проверки
        'defects_verified_week':  defects_verified_week,  # приняты технадзором
        'top_defects':            top_defects_list,

        # ── Согласования ──
        'pkgs_in_review':      pkgs_in_review,
        'pkgs_returned':       len(returned_pkgs),
        'pkgs_completed':      pkgs_completed,
        'pkgs_stalled':        len(stalled_pkgs),
        'pkgs_completed_week': pkgs_completed_week,
        'stalled_pkgs':        stalled_pkgs,             # список с ролью + дней ожидания
        'returned_pkgs':       [dict(r) for r in returned_pkgs],
        'stalled_by_role':     [dict(r) for r in stalled_by_role],

        # ── Журнал ──
        'journal':  [dict(r) for r in journal_entries],

        # ── Мета ──
        'generated_at': today,
        'period_days':  period_days,
        'period_since': since,
    }
