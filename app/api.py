"""JSON API — public data open (rule 29); private data behind role auth (rule 30)."""
from functools import wraps

from flask import Blueprint, jsonify, request, abort

from .models import (
    db, Department, Project, OfficialPost, CitizenPost, CoordinationMessage,
    JointSchedule, Conflict, Notification, AuditLog, User,
    ROLE_CHAIRMAN, ROLE_EMPLOYEE, ROLE_DEPT_HEAD, ROLE_ADMIN, ROLE_CITIZEN,
)
from . import services
from .services import BusinessRuleError
from . import current_user

bp = Blueprint("api", __name__, url_prefix="/api")


def _auth(*roles):
    def deco(fn):
        @wraps(fn)
        def wrapper(**kw):
            u = current_user()
            if u is None or u.role not in roles:
                return jsonify({"error": "Forbidden"}), 403
            return fn(user=u, **kw)
        return wrapper
    return deco


def project_public_dict(p):
    """Public view — internal approval chain, creator and notes excluded (section 7)."""
    return {
        "id": p.id, "title": p.title, "description": p.description,
        "department": p.department.name, "department_slug": p.department.slug,
        "category": p.category, "contractor": p.contractor_name, "budget": str(p.budget) if p.budget else None,
        "start_date": str(p.start_date), "end_date": str(p.end_date),
        "status": p.status, "progress_percentage": p.progress_percentage,
        "latitude": p.latitude, "longitude": p.longitude, "address": p.address,
        "ward": p.ward, "road_name": p.road_name,
        "delay_reason": p.delay_reason,
        "delay_label": ("⚠️ Delayed — Reason Pending" if p.unexplained_delay else
                        "⚠️ Delayed" if p.status == "delayed" else None),
        "conflict_flagged": p.conflict_flagged,
        "badge": f"✅ Verified Official — {p.department.name}",
        "approved_at": p.approved_at.isoformat() if p.approved_at else None,
    }


PUBLIC_STATUSES = ("approved", "in_progress", "completed", "verified_complete", "delayed")


@bp.route("/projects")
def api_projects():
    items = Project.query.filter(Project.status.in_(PUBLIC_STATUSES)).all()
    q = (request.args.get("q") or "").lower()
    dept = request.args.get("department")
    ward = request.args.get("ward")
    status = request.args.get("status")
    if q:
        items = [p for p in items if q in " ".join(
            filter(None, [p.title, p.description, p.road_name, p.ward, p.contractor_name, p.department.name])).lower()]
    if dept:
        items = [p for p in items if p.department.slug == dept]
    if ward:
        items = [p for p in items if (p.ward or "").lower() == ward.lower()]
    if status:
        items = [p for p in items if p.status == status]
    return {"count": len(items), "projects": [project_public_dict(p) for p in items]}


@bp.route("/projects/<int:pid>")
def api_project(pid):
    p = db.session.get(Project, pid) or abort(404)
    if p.status not in PUBLIC_STATUSES:
        abort(404)
    d = project_public_dict(p)
    d["official_posts"] = [{"title": op.title, "body": op.body,
                            "published_at": op.published_at.isoformat() if op.published_at else None,
                            "badge": f"✅ Verified Official — {op.department.name}"}
                           for op in OfficialPost.query.filter_by(project_id=p.id, status="published").all()]
    d["citizen_reports"] = [{"id": c.id, "body": c.body, "status": c.status,
                             "badge": "👤 Citizen — Phone Verified"}
                            for c in CitizenPost.query.filter_by(linked_project_id=p.id)
                            .filter(CitizenPost.status != "removed").all()]
    return d


@bp.route("/departments")
def api_departments():
    return {"departments": [{"id": d.id, "name": d.name, "slug": d.slug,
                              "description": d.description,
                              "contact_email": d.contact_email, "contact_phone": d.contact_phone}
                             for d in Department.query.all()]}


@bp.route("/coordination")
def api_coordination():
    msgs = CoordinationMessage.query.order_by(CoordinationMessage.created_at.asc()).all()
    out = []
    for m in msgs:
        frm = db.session.get(Department, m.from_department_id) if m.from_department_id else None
        label = frm.name if frm else ("Municipal Chairman" if m.message_type == "decision" else "System")
        out.append({"id": m.id, "from": label, "to": [db.session.get(Department, d).name
                                                      for d in (m.to_department_ids or [])],
                    "type": m.message_type, "body": m.body, "dates": m.proposed_dates,
                    "location": m.location, "at": m.created_at.isoformat()})
    return {"messages": out}


@bp.route("/citizen-reports")
def api_citizen_reports():
    posts = CitizenPost.query.filter(CitizenPost.status.in_(["published", "under_review", "resolved"])).all()
    return {"reports": [{"id": c.id, "body": c.body, "category": c.category, "address": c.address,
                         "status": c.status, "flag_count": c.flag_count,
                         "created_at": c.created_at.isoformat(),
                         "display_name": c.user.display_name if c.user else "Anonymous Citizen",
                         "badge": "👤 Citizen — Phone Verified"} for c in posts]}


@bp.route("/joint-schedules")
def api_joint_schedules():
    js = JointSchedule.query.filter_by(status="approved").all()
    return {"schedules": [{
        "id": j.id,
        "departments": [db.session.get(Department, d).name for d in j.department_ids],
        "location": j.location, "start": str(j.approved_start_date), "end": str(j.approved_end_date),
        "reason": j.reason,
        "badge": "🤝 Coordinated Work — Approved",
        "no_show_departments": [db.session.get(Department, d).name for d in (j.no_show_departments or [])],
    } for j in js]}


@bp.route("/conflicts")
def api_conflicts():
    cs = Conflict.query.filter(Conflict.status.in_(["open", "escalated"])).all()
    return {"conflicts": [{"id": c.id, "severity": c.severity,
                           "project_a": c.project_a.title, "project_b": c.project_b.title,
                           "distance_meters": c.distance_meters, "overlap": c.date_overlap,
                           "status": c.status} for c in cs]}


# ----------------------------------------------------------- authenticated endpoints
@bp.route("/me")
def api_me():
    u = current_user()
    if not u:
        return {"user": None}
    data = {"id": u.id, "role": u.role, "status": u.status,
            "phone_verified": bool(u.phone_verified)}
    if u.role == ROLE_CITIZEN:
        data["display_name"] = u.display_name
        data["public_badge"] = "👤 Citizen — Phone Verified"
    elif u.role in (ROLE_EMPLOYEE, ROLE_DEPT_HEAD):
        data["department"] = u.department.name if u.department else None
        data["public_badge"] = f"✅ Verified Official — {u.department.name}" if u.department else None
        # identity fields visible to the owner only, never via public endpoints
        data["full_name"] = u.full_name
        data["employee_code"] = u.employee_code
    elif u.role == ROLE_CHAIRMAN:
        data["public_badge"] = "🏛️ Municipal Chairman"
    return {"user": data}


@bp.route("/notifications")
@_auth(ROLE_CITIZEN, ROLE_EMPLOYEE, ROLE_DEPT_HEAD, ROLE_CHAIRMAN, ROLE_ADMIN)
def api_notifications(user=None):
    items = Notification.query.filter_by(user_id=user.id).order_by(Notification.created_at.desc()).limit(50).all()
    return {"notifications": [{"id": n.id, "title": n.title, "body": n.body, "link": n.link,
                               "read": n.read, "at": n.created_at.isoformat()} for n in items]}


@bp.route("/audit-logs")
@_auth(ROLE_CHAIRMAN, ROLE_ADMIN)
def api_audit_logs(user=None):
    logs = AuditLog.query.order_by(AuditLog.created_at.desc()).limit(200).all()
    return {"logs": [{"id": l.id, "user_id": l.user_id, "role": l.role, "action": l.action,
                      "target": f"{l.target_type}:{l.target_id}", "metadata": l.metadata_json,
                      "ip": l.ip_address, "at": l.created_at.isoformat()} for l in logs]}


@bp.route("/employees/<int:uid>/identity")
@_auth(ROLE_CHAIRMAN, ROLE_ADMIN)
def api_employee_identity(user=None, uid=None):
    """Employee identity visible to Chairman/Admin only (privacy section)."""
    t = db.session.get(User, uid) or abort(404)
    return {"id": t.id, "full_name": t.full_name, "employee_code": t.employee_code,
            "designation": t.designation, "department": t.department.name if t.department else None,
            "status": t.status, "email": t.email}


@bp.route("/search")
def api_search():
    """Search by title, road name, ward, department, contractor, keyword (section 19)."""
    q = (request.args.get("q") or "").lower().strip()
    if not q:
        return {"results": []}
    results = []
    for p in Project.query.filter(Project.status.in_(PUBLIC_STATUSES)).all():
        hay = " ".join(filter(None, [p.title, p.description, p.road_name, p.ward,
                                     p.contractor_name, p.department.name, p.category])).lower()
        if q in hay:
            results.append({"type": "project", "id": p.id, "title": p.title,
                            "url": f"/projects/{p.id}"})
    for d in Department.query.all():
        if q in d.name.lower() or q in d.slug:
            results.append({"type": "department", "id": d.id, "title": d.name,
                            "url": f"/departments/{d.slug}"})
    for c in CitizenPost.query.filter_by(status="published").all():
        if q in (c.body or "").lower() or q in (c.address or "").lower():
            results.append({"type": "citizen_report", "id": c.id, "title": c.body[:60],
                            "url": f"/citizen-reports/{c.id}"})
    return {"results": results[:30]}
