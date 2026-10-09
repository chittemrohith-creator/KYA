"""Municipal Chairman console — approvals, conflicts, schedules, audit logs, statements."""
from flask import Blueprint, redirect, render_template, request, url_for, abort, jsonify

from .models import (
    db, utcnow, Department, User, Project, OfficialPost, CitizenPost, OfficialResponse,
    CoordinationMessage, Conflict, JointSchedule, Notification, AuditLog,
    ROLE_CHAIRMAN, ROLE_EMPLOYEE, ROLE_DEPT_HEAD, USER_STATUS_PENDING,
)
from . import services
from .services import BusinessRuleError
from . import chairman_required, current_user

bp = Blueprint("chairman", __name__, url_prefix="/chairman")


def _me():
    return current_user()


@bp.route("/")
@chairman_required
def dashboard():
    u = _me()
    stats = {
        "pending_employees": User.query.filter_by(status=USER_STATUS_PENDING).count(),
        "pending_posts": OfficialPost.query.filter_by(status="pending_chairman").count(),
        "pending_projects": Project.query.filter_by(status="pending_chairman").count(),
        "open_conflicts": Conflict.query.filter(Conflict.status.in_(["open", "escalated"])).count(),
        "pending_schedules": JointSchedule.query.filter_by(status="pending_chairman").count(),
        "delayed_projects": Project.query.filter_by(status="delayed").count(),
        "unexplained_delays": Project.query.filter_by(unexplained_delay=True).count(),
    }
    hotspots = []
    for c in Conflict.query.all():
        key = (c.project_a.road_name or c.project_a.address)
        if key:
            hotspots.append(key)
    hotspot_counts = {}
    for h in hotspots:
        hotspot_counts[h] = hotspot_counts.get(h, 0) + 1
    notifs = Notification.query.filter_by(user_id=u.id).order_by(Notification.created_at.desc()).limit(15).all()
    return render_template("chairman/dashboard.html", stats=stats, hotspots=hotspot_counts,
                           notifications=notifs)


@bp.route("/employees")
@chairman_required
def employees():
    pending = User.query.filter_by(status=USER_STATUS_PENDING).all()
    allstaff = User.query.filter(User.role.in_([ROLE_EMPLOYEE, ROLE_DEPT_HEAD])).all()
    return render_template("chairman/employees.html", pending=pending, allstaff=allstaff)


@bp.route("/employees/<int:uid>/approve", methods=["POST"])
@chairman_required
def approve_employee(uid):
    target = db.session.get(User, uid) or abort(404)
    services.approve_employee(target, _me(), request=request)
    db.session.commit()
    return redirect(url_for("chairman.employees"))


@bp.route("/employees/<int:uid>/reject", methods=["POST"])
@chairman_required
def reject_employee(uid):
    target = db.session.get(User, uid) or abort(404)
    services.reject_employee(target, _me(), request.form.get("reason", ""), request=request)
    db.session.commit()
    return redirect(url_for("chairman.employees"))


@bp.route("/employees/<int:uid>/suspend", methods=["POST"])
@chairman_required
def suspend_employee(uid):
    target = db.session.get(User, uid) or abort(404)
    services.suspend_employee(target, _me(), request.form.get("reason", ""), request=request)
    db.session.commit()
    return redirect(url_for("chairman.employees"))


@bp.route("/posts")
@chairman_required
def posts():
    pending = OfficialPost.query.filter_by(status="pending_chairman").order_by(OfficialPost.submitted_at).all()
    responses_pending = OfficialResponse.query.filter_by(status="pending_chairman").all()
    recent = OfficialPost.query.filter(OfficialPost.status != "pending_chairman") \
        .order_by(OfficialPost.created_at.desc()).limit(25).all()
    return render_template("chairman/posts.html", pending=pending, recent=recent,
                           responses_pending=responses_pending)


@bp.route("/posts/<int:post_id>/approve", methods=["POST"])
@chairman_required
def approve_post(post_id):
    post = db.session.get(OfficialPost, post_id) or abort(404)
    services.approve_post(post, _me(), request=request)
    db.session.commit()
    return redirect(url_for("chairman.posts"))


@bp.route("/posts/<int:post_id>/reject", methods=["POST"])
@chairman_required
def reject_post(post_id):
    post = db.session.get(OfficialPost, post_id) or abort(404)
    services.reject_post(post, _me(), request.form.get("reason", ""), request=request)
    db.session.commit()
    return redirect(url_for("chairman.posts"))


@bp.route("/responses/<int:rid>/approve", methods=["POST"])
@chairman_required
def approve_response(rid):
    r = db.session.get(OfficialResponse, rid) or abort(404)
    services.approve_response(r, _me(), request=request)
    cp = r.citizen_post
    cp.status = "resolved" if request.form.get("mark_resolved") else cp.status
    db.session.commit()
    return redirect(url_for("chairman.posts"))


@bp.route("/projects")
@chairman_required
def projects():
    pending = Project.query.filter_by(status="pending_chairman").all()
    delayed = Project.query.filter(Project.status == "delayed").order_by(Project.end_date).all()
    return render_template("chairman/projects.html", pending=pending, delayed=delayed)


@bp.route("/projects/<int:pid>/approve", methods=["POST"])
@chairman_required
def approve_project(pid):
    p = db.session.get(Project, pid) or abort(404)
    services.approve_project(p, _me(), request=request)
    db.session.commit()
    return redirect(url_for("chairman.projects"))


@bp.route("/projects/<int:pid>/reject", methods=["POST"])
@chairman_required
def reject_project(pid):
    p = db.session.get(Project, pid) or abort(404)
    services.reject_project(p, _me(), request.form.get("reason", ""), request=request)
    db.session.commit()
    return redirect(url_for("chairman.projects"))


@bp.route("/projects/<int:pid>/override", methods=["POST"])
@chairman_required
def override_project(pid):
    """Chairman can override any project status with reason."""
    p = db.session.get(Project, pid) or abort(404)
    new_status = request.form.get("status")
    reason = request.form.get("reason", "")
    if new_status not in services.PROJECT_ALLOWED_TRANSITIONS and new_status not in (
            "cancelled", "delayed", "in_progress", "completed"):
        abort(400)
    if not reason:
        return render_template("message.html", title="Reason required",
                               body="Status overrides require a reason.")
    old = p.status
    p.status = new_status
    services.audit("project.status_override", _me(), "project", p.id,
                   metadata={"from": old, "to": new_status, "reason": reason}, request=request)
    db.session.commit()
    return redirect(url_for("chairman.projects"))


@bp.route("/projects/<int:pid>/verify", methods=["POST"])
@chairman_required
def verify_project(pid):
    p = db.session.get(Project, pid) or abort(404)
    services.verify_complete_project(p, _me(), request=request)
    db.session.commit()
    return redirect(url_for("chairman.projects"))


@bp.route("/conflicts")
@chairman_required
def conflicts():
    items = Conflict.query.order_by(Conflict.created_at.desc()).all()
    return render_template("chairman/conflicts.html", conflicts=items)


@bp.route("/conflicts/<int:cidx>/resolve", methods=["POST"])
@chairman_required
def resolve_conflict(cidx):
    c = db.session.get(Conflict, cidx) or abort(404)
    resolution = request.form.get("resolution", "")
    action = request.form.get("decision")  # joint | delay | reroute | cancel
    if action in ("cancel",):
        other = c.project_b
        other.status = "cancelled"
        services.audit("project.cancelled_by_chairman", _me(), "project", other.id,
                       metadata={"reason": resolution}, request=request)
    services.resolve_conflict(c, _me(), f"{action}: {resolution}", request=request)
    db.session.commit()
    return redirect(url_for("chairman.conflicts"))


@bp.route("/schedules")
@chairman_required
def schedules():
    pending = JointSchedule.query.filter_by(status="pending_chairman").all()
    decided = JointSchedule.query.filter(JointSchedule.status != "pending_chairman").all()
    return render_template("chairman/schedules.html", pending=pending, decided=decided)


@bp.route("/schedules/<int:jid>/approve", methods=["POST"])
@chairman_required
def approve_schedule(jid):
    js = db.session.get(JointSchedule, jid) or abort(404)
    services.approve_joint_schedule(js, _me(), request=request)
    db.session.commit()
    return redirect(url_for("chairman.schedules"))


@bp.route("/schedules/<int:jid>/reject", methods=["POST"])
@chairman_required
def reject_schedule(jid):
    js = db.session.get(JointSchedule, jid) or abort(404)
    services.reject_joint_schedule(js, _me(), request.form.get("reason", ""), request=request)
    db.session.commit()
    return redirect(url_for("chairman.schedules"))


@bp.route("/statements", methods=["POST"])
@chairman_required
def statement():
    """Chairman official statements are auto-published (role definition 6.4)."""
    u = _me()
    body = request.form.get("body", "").strip()
    if not body:
        abort(400)
    msg = CoordinationMessage(from_department_id=None, to_department_ids=[],
                              message_type="decision", body=body,
                              created_by=u.id, status="published")
    db.session.add(msg)
    services.audit("chairman.statement_published", u, "coordination_message", None, request=request)
    db.session.commit()
    return redirect(url_for("public.coordination"))


@bp.route("/reports/<int:cid>/remove", methods=["POST"])
@chairman_required
def remove_report(cid):
    cp = db.session.get(CitizenPost, cid) or abort(404)
    services.remove_citizen_post(cp, _me(), request.form.get("reason", ""))
    db.session.commit()
    return redirect(request.referrer or url_for("public.citizen_reports"))


@bp.route("/audit-logs")
@chairman_required
def audit_logs():
    q = request.args.get("action", "")
    logs = AuditLog.query
    if q:
        logs = logs.filter(AuditLog.action.like(f"%{q}%"))
    items = logs.order_by(AuditLog.created_at.desc()).limit(300).all()
    return render_template("chairman/audit_logs.html", logs=items, q=q)


@bp.route("/audit-logs/export.csv")
@chairman_required
def audit_export():
    import csv, io
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["id", "user_id", "role", "action", "target_type", "target_id", "metadata", "ip", "ua", "created_at"])
    for l in AuditLog.query.order_by(AuditLog.created_at.asc()).all():
        w.writerow([l.id, l.user_id, l.role, l.action, l.target_type, l.target_id,
                    l.metadata_json, l.ip_address, l.user_agent, l.created_at])
    services.audit("data.export", _me(), "audit_log", None, request=request)
    db.session.commit()
    return buf.getvalue(), 200, {"Content-Type": "text/csv",
                                 "Content-Disposition": "attachment; filename=audit_logs.csv"}


@bp.route("/departments")
@chairman_required
def departments():
    from sqlalchemy import func
    rows = []
    for dept in Department.query.order_by(Department.name).all():
        total = Project.query.filter_by(department_id=dept.id).count()
        delayed = Project.query.filter_by(department_id=dept.id, status="delayed").count()
        unex = Project.query.filter_by(department_id=dept.id, unexplained_delay=True).count()
        rows.append({"dept": dept, "total": total, "delayed": delayed, "unexplained": unex,
                     "delay_rate": round(delayed / total * 100, 1) if total else 0})
    return render_template("chairman/departments.html", rows=rows)


@bp.route("/notifications")
@chairman_required
def notifications():
    u = _me()
    items = Notification.query.filter_by(user_id=u.id).order_by(Notification.created_at.desc()).all()
    for n in items:
        n.read = True
    db.session.commit()
    return render_template("chairman/notifications.html", notifications=items)


@bp.route("/run-delay-sweep", methods=["POST"])
@chairman_required
def run_sweep():
    result = services.run_delay_sweep()
    services.unresolved_conflict_check()
    db.session.commit()
    return jsonify(result)
