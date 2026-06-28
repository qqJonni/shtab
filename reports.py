from db import query_db


def summary_cards():
    return {
        'objects_active': query_db("SELECT COUNT(*) as c FROM objects WHERE status='active'", one=True)['c'],
        'objects_archived': query_db("SELECT COUNT(*) as c FROM objects WHERE status='archived'", one=True)['c'],
        'stages_total': query_db("SELECT COUNT(*) as c FROM construction_stages", one=True)['c'],
        'stages_in_progress': query_db("SELECT COUNT(*) as c FROM construction_stages WHERE status='in_progress'", one=True)['c'],
        'stages_done': query_db("SELECT COUNT(*) as c FROM construction_stages WHERE status='done'", one=True)['c'],
        'substages_total': query_db("SELECT COUNT(*) as c FROM substages", one=True)['c'],
        'substages_not_started': query_db("SELECT COUNT(*) as c FROM substages WHERE status='not_started'", one=True)['c'],
        'substages_in_progress': query_db("SELECT COUNT(*) as c FROM substages WHERE status='in_progress'", one=True)['c'],
        'substages_done': query_db("SELECT COUNT(*) as c FROM substages WHERE status='done'", one=True)['c'],
        'substages_closed': query_db("SELECT COUNT(*) as c FROM substages WHERE status='closed'", one=True)['c'],
        'substages_approved': query_db("SELECT COUNT(*) as c FROM substages WHERE status='approved'", one=True)['c'],
        'defects_open': query_db("SELECT COUNT(*) as c FROM defects WHERE status IN ('open','in_progress')", one=True)['c'],
        'defects_overdue': query_db("SELECT COUNT(*) as c FROM defects WHERE due_date < date('now') AND status NOT IN ('closed','verified')", one=True)['c'],
        'defects_closed': query_db("SELECT COUNT(*) as c FROM defects WHERE status IN ('closed','verified')", one=True)['c'],
        'packages_in_review': query_db("SELECT COUNT(*) as c FROM doc_packages WHERE status='in_review'", one=True)['c'],
        'packages_completed': query_db("SELECT COUNT(*) as c FROM doc_packages WHERE status='completed'", one=True)['c'],
        'mr_active': query_db("SELECT COUNT(*) as c FROM material_requests WHERE status IN ('submitted','approved','processing')", one=True)['c'],
    }


def chart_substage_statuses():
    return {
        'not_started': query_db("SELECT COUNT(*) as c FROM substages WHERE status='not_started'", one=True)['c'],
        'in_progress': query_db("SELECT COUNT(*) as c FROM substages WHERE status='in_progress'", one=True)['c'],
        'done': query_db("SELECT COUNT(*) as c FROM substages WHERE status='done'", one=True)['c'],
        'closed': query_db("SELECT COUNT(*) as c FROM substages WHERE status='closed'", one=True)['c'],
        'approved': query_db("SELECT COUNT(*) as c FROM substages WHERE status='approved'", one=True)['c'],
    }


def chart_schedule_health():
    from datetime import date
    today = date.today().isoformat()
    done = query_db("SELECT COUNT(*) as c FROM construction_stages WHERE status='done'", one=True)['c']
    on_track = query_db(
        "SELECT COUNT(*) as c FROM construction_stages WHERE status IN ('planned','in_progress') "
        "AND (plan_end_date IS NULL OR plan_end_date >= ?)", (today,), one=True)['c']
    overdue = query_db(
        "SELECT COUNT(*) as c FROM construction_stages WHERE status IN ('planned','in_progress') "
        "AND plan_end_date IS NOT NULL AND plan_end_date < ?", (today,), one=True)['c']
    suspended = query_db("SELECT COUNT(*) as c FROM construction_stages WHERE status='suspended'", one=True)['c']
    return {'done': done, 'on_track': on_track, 'overdue': overdue, 'suspended': suspended}


def chart_defects_priority():
    rows = query_db(
        "SELECT priority, COUNT(*) as c FROM defects WHERE status NOT IN ('closed','verified') GROUP BY priority")
    return {r['priority']: r['c'] for r in rows}


def chart_packages_pipeline():
    rows = query_db(
        "SELECT a.role, COUNT(*) as c FROM approval_steps a "
        "JOIN doc_packages dp ON a.package_id = dp.id "
        "WHERE dp.status = 'in_review' AND a.status = 'pending' GROUP BY a.role")
    return {r['role']: r['c'] for r in rows}


def objects_summary():
    objects = query_db(
        "SELECT o.*, "
        "(SELECT COUNT(*) FROM construction_stages cs2 WHERE cs2.object_id=o.id) as stages_count, "
        "(SELECT COUNT(*) FROM defects d2 WHERE d2.object_id=o.id AND d2.status NOT IN ('closed','verified')) as defects_open "
        "FROM objects o WHERE o.status='active' ORDER BY o.name")
    result = []
    for obj in objects:
        o = dict(obj)
        subs = query_db(
            "SELECT ss.status, ss.total_price FROM substages ss "
            "JOIN construction_stages cs ON ss.stage_id=cs.id WHERE cs.object_id=?", (o['id'],))
        total = len(subs)
        done = sum(1 for s in subs if s['status'] in ('done', 'closed', 'approved'))
        o['substages_total'] = total
        o['substages_done'] = done
        o['progress'] = round(done / total * 100) if total > 0 else 0
        o['total_sum'] = sum(s['total_price'] or 0 for s in subs)
        o['completed_sum'] = sum(s['total_price'] or 0 for s in subs if s['status'] in ('done', 'closed', 'approved'))
        pkgs = query_db(
            "SELECT COUNT(*) as c FROM doc_packages dp "
            "JOIN substages ss ON dp.substage_id=ss.id "
            "JOIN construction_stages cs ON ss.stage_id=cs.id "
            "WHERE cs.object_id=? AND dp.status IN ('in_review','returned')", (o['id'],))
        o['packages_active'] = pkgs[0]['c'] if pkgs else 0
        result.append(o)
    return result
