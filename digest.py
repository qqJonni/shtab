"""
object_digest(obj_id) -> dict

Единая функция сводки по объекту для дайджест-экрана и PDF.
Только детерминированный SQL, никакого ИИ.
Источник денег — substages.total_price (volume × unit_price).
Временны́е метки: substages.completed_at, doc_packages.completed_at,
                  defects.verified_at / resolved_at, approval_steps.acted_at.
"""

from datetime import date, timedelta
from db import query_db


def object_digest(obj_id: int) -> dict:
    today = date.today().isoformat()
    week_ago = (date.today() - timedelta(days=7)).isoformat()

    # ── Финансы ─────────────────────────────────────────────────────────────
    # contract_sum: сумма по контрактам этапов (может быть NULL у части)
    contract_row = query_db(
        "SELECT SUM(contract_amount) as s FROM construction_stages WHERE object_id = ?",
        (obj_id,), one=True)
    contract_sum = contract_row['s'] or 0

    # smeta_sum / completed_sum / in_progress_sum — из substages.total_price
    money = query_db(
        "SELECT ss.status, COALESCE(ss.total_price, 0) as tp "
        "FROM substages ss "
        "JOIN construction_stages cs ON ss.stage_id = cs.id "
        "WHERE cs.object_id = ?", (obj_id,))
    smeta_sum = sum(r['tp'] for r in money)
    completed_sum = sum(r['tp'] for r in money if r['status'] in ('done', 'closed', 'approved'))
    in_progress_sum = sum(r['tp'] for r in money if r['status'] == 'in_progress')

    # выполнено за последние 7 дней (по substages.completed_at)
    week_completed_row = query_db(
        "SELECT COALESCE(SUM(ss.total_price), 0) as s "
        "FROM substages ss "
        "JOIN construction_stages cs ON ss.stage_id = cs.id "
        "WHERE cs.object_id = ? AND ss.completed_at >= ? "
        "AND ss.status IN ('done', 'closed', 'approved')",
        (obj_id, week_ago), one=True)
    week_completed_sum = week_completed_row['s'] or 0

    # ── Подэтапы ─────────────────────────────────────────────────────────────
    subs = query_db(
        "SELECT ss.status, ss.plan_end_date, ss.completed_at "
        "FROM substages ss "
        "JOIN construction_stages cs ON ss.stage_id = cs.id "
        "WHERE cs.object_id = ?", (obj_id,))

    sub_total      = len(subs)
    sub_done       = sum(1 for s in subs if s['status'] in ('done', 'closed', 'approved'))
    sub_in_progress = sum(1 for s in subs if s['status'] == 'in_progress')
    sub_not_started = sum(1 for s in subs if s['status'] == 'not_started')
    sub_overdue    = sum(
        1 for s in subs
        if s['plan_end_date'] and s['plan_end_date'] < today
        and s['status'] not in ('done', 'closed', 'approved')
    )
    sub_done_week  = sum(
        1 for s in subs
        if s['completed_at'] and s['completed_at'][:10] >= week_ago
        and s['status'] in ('done', 'closed', 'approved')
    )
    progress_pct = round(sub_done / sub_total * 100) if sub_total else 0

    # ── Этапы ─────────────────────────────────────────────────────────────────
    stages = query_db(
        "SELECT cs.id, cs.name, cs.status, cs.plan_start_date, cs.plan_end_date, "
        "cs.contract_amount, cs.contract_number, org.name as contractor_name "
        "FROM construction_stages cs "
        "LEFT JOIN organizations org ON cs.contractor_id = org.id "
        "WHERE cs.object_id = ? ORDER BY cs.order_num", (obj_id,))

    stages_list = []
    for s in stages:
        st = dict(s)
        sub_rows = query_db(
            "SELECT status, COALESCE(total_price, 0) as tp "
            "FROM substages WHERE stage_id = ?", (s['id'],))
        st_total = len(sub_rows)
        st_done  = sum(1 for r in sub_rows if r['status'] in ('done', 'closed', 'approved'))
        st['sub_total']     = st_total
        st['sub_done']      = st_done
        st['progress_pct']  = round(st_done / st_total * 100) if st_total else 0
        st['smeta_sum']     = sum(r['tp'] for r in sub_rows)
        st['completed_sum'] = sum(r['tp'] for r in sub_rows if r['status'] in ('done', 'closed', 'approved'))
        st['overdue'] = bool(
            s['plan_end_date'] and s['plan_end_date'] < today
            and s['status'] not in ('done', 'suspended')
        )
        stages_list.append(st)

    stage_total    = len(stages_list)
    stage_done     = sum(1 for s in stages_list if s['status'] == 'done')
    stage_overdue  = sum(1 for s in stages_list if s['overdue'])

    # ── Замечания ─────────────────────────────────────────────────────────────
    defects_open = query_db(
        "SELECT COUNT(*) as c FROM defects "
        "WHERE object_id = ? AND status NOT IN ('closed', 'verified', 'rejected')",
        (obj_id,), one=True)['c']

    defects_overdue = query_db(
        "SELECT COUNT(*) as c FROM defects "
        "WHERE object_id = ? AND due_date < ? "
        "AND status NOT IN ('closed', 'verified', 'rejected')",
        (obj_id, today), one=True)['c']

    defects_closed_week = query_db(
        "SELECT COUNT(*) as c FROM defects "
        "WHERE object_id = ? "
        "AND status IN ('closed', 'verified') "
        "AND COALESCE(verified_at, resolved_at) >= ?",
        (obj_id, week_ago), one=True)['c']

    # топ-5 открытых замечаний по приоритету
    priority_order = "CASE priority WHEN 'critical' THEN 1 WHEN 'high' THEN 2 WHEN 'normal' THEN 3 WHEN 'low' THEN 4 ELSE 5 END"
    top_defects = query_db(
        f"SELECT d.id, d.title, d.priority, d.due_date, d.status, "
        f"cs.name as stage_name "
        f"FROM defects d "
        f"LEFT JOIN construction_stages cs ON d.stage_id = cs.id "
        f"WHERE d.object_id = ? AND d.status NOT IN ('closed', 'verified', 'rejected') "
        f"ORDER BY {priority_order}, d.due_date ASC NULLS LAST LIMIT 5",
        (obj_id,))

    # ── Согласования ─────────────────────────────────────────────────────────
    pkgs_in_review = query_db(
        "SELECT COUNT(*) as c FROM doc_packages dp "
        "JOIN substages ss ON dp.substage_id = ss.id "
        "JOIN construction_stages cs ON ss.stage_id = cs.id "
        "WHERE cs.object_id = ? AND dp.status = 'in_review'",
        (obj_id,), one=True)['c']

    pkgs_returned = query_db(
        "SELECT COUNT(*) as c FROM doc_packages dp "
        "JOIN substages ss ON dp.substage_id = ss.id "
        "JOIN construction_stages cs ON ss.stage_id = cs.id "
        "WHERE cs.object_id = ? AND dp.status = 'returned'",
        (obj_id,), one=True)['c']

    pkgs_completed = query_db(
        "SELECT COUNT(*) as c FROM doc_packages dp "
        "JOIN substages ss ON dp.substage_id = ss.id "
        "JOIN construction_stages cs ON ss.stage_id = cs.id "
        "WHERE cs.object_id = ? AND dp.status = 'completed'",
        (obj_id,), one=True)['c']

    # пакеты, зависшие на согласовании > 7 дней
    pkgs_stalled = query_db(
        "SELECT COUNT(*) as c FROM doc_packages dp "
        "JOIN substages ss ON dp.substage_id = ss.id "
        "JOIN construction_stages cs ON ss.stage_id = cs.id "
        "WHERE cs.object_id = ? AND dp.status = 'in_review' "
        "AND dp.submitted_at IS NOT NULL AND dp.submitted_at < ?",
        (obj_id, week_ago), one=True)['c']

    # пакеты завершённые за последние 7 дней (по doc_packages.completed_at)
    pkgs_completed_week = query_db(
        "SELECT COUNT(*) as c FROM doc_packages dp "
        "JOIN substages ss ON dp.substage_id = ss.id "
        "JOIN construction_stages cs ON ss.stage_id = cs.id "
        "WHERE cs.object_id = ? AND dp.status = 'completed' "
        "AND dp.completed_at >= ?",
        (obj_id, week_ago), one=True)['c']

    # на каком шаге цепочки зависли текущие пакеты
    stalled_by_role = query_db(
        "SELECT a.role, COUNT(*) as c "
        "FROM approval_steps a "
        "JOIN doc_packages dp ON a.package_id = dp.id "
        "JOIN substages ss ON dp.substage_id = ss.id "
        "JOIN construction_stages cs ON ss.stage_id = cs.id "
        "WHERE cs.object_id = ? AND dp.status = 'in_review' AND a.status = 'pending' "
        "GROUP BY a.role ORDER BY c DESC",
        (obj_id,))

    return {
        # финансы
        'contract_sum':       contract_sum,
        'smeta_sum':          smeta_sum,
        'completed_sum':      completed_sum,
        'in_progress_sum':    in_progress_sum,
        'week_completed_sum': week_completed_sum,
        'remaining_sum':      smeta_sum - completed_sum,

        # подэтапы
        'sub_total':        sub_total,
        'sub_done':         sub_done,
        'sub_in_progress':  sub_in_progress,
        'sub_not_started':  sub_not_started,
        'sub_overdue':      sub_overdue,
        'sub_done_week':    sub_done_week,
        'progress_pct':     progress_pct,

        # этапы
        'stage_total':   stage_total,
        'stage_done':    stage_done,
        'stage_overdue': stage_overdue,
        'stages':        stages_list,

        # замечания
        'defects_open':         defects_open,
        'defects_overdue':      defects_overdue,
        'defects_closed_week':  defects_closed_week,
        'top_defects':          [dict(r) for r in top_defects],

        # согласования
        'pkgs_in_review':      pkgs_in_review,
        'pkgs_returned':       pkgs_returned,
        'pkgs_completed':      pkgs_completed,
        'pkgs_stalled':        pkgs_stalled,
        'pkgs_completed_week': pkgs_completed_week,
        'stalled_by_role':     [dict(r) for r in stalled_by_role],

        # мета
        'generated_at': date.today().isoformat(),
        'period_days':  7,
    }
