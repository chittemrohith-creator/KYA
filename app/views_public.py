"""Public pages — accessible without login (rule 29). Employee identities never exposed."""
from flask import Blueprint, render_template, request, abort

from .models import (
    db, Department, Project, OfficialPost, CitizenPost, CoordinationMessage,
    JointSchedule, Conflict, User, ROLE_CHAIRMAN,
)
from .services import contractor_stats

bp = Blueprint("public", __name__)

PUBLIC_PROJECT_STATUSES = ("approved", "in_progress", "completed", "verified_complete", "delayed")


def _dept_messages(dept_id):
    """Messages sent by or addressed to a department (JSON list stored as text in SQLite)."""
    all_msgs = CoordinationMessage.query.order_by(CoordinationMessage.created_at.desc()).all()
    return [m for m in all_msgs
            if m.from_department_id == dept_id or dept_id in (m.to_department_ids or [])]


def _js_for_project(project_id):
    return [js for js in JointSchedule.query.filter_by(status="approved").all()
            if project_id in (js.project_ids or [])]


def _visible_projects():
    return Project.query.filter(Project.status.in_(PUBLIC_PROJECT_STATUSES)).order_by(Project.start_date.desc()).all()


@bp.route("/")
def home():
    projects = _visible_projects()
    reports = CitizenPost.query.filter_by(status="published").order_by(CitizenPost.created_at.desc()).limit(6).all()
    posts = OfficialPost.query.filter_by(status="published").order_by(OfficialPost.published_at.desc()).limit(6).all()
    conflicts = Conflict.query.filter_by(status="open").count()
    joint = JointSchedule.query.filter_by(status="approved").count()
    return render_template("home.html", projects=projects[:6], reports=reports, posts=posts,
                           conflict_count=conflicts, joint_count=joint,
                           departments=Department.query.order_by(Department.name).all())


@bp.route("/map")
def map_page():
    return render_template("map.html", departments=Department.query.order_by(Department.name).all())


@bp.route("/api/mapdata")
def map_data():
    """Public pins with consistent layer, department, status, ward and search filters."""
    import math
    kind = request.args.get("kind", "").strip()
    department = request.args.get("department", "").strip().lower()
    status = request.args.get("status", "").strip().lower()
    ward = request.args.get("ward", "").strip().lower()
    query = request.args.get("q", "").strip().lower()
    pins = []

    def add(pin, projects=(), search_text=""):
        if pin.get("lat") is None or pin.get("lng") is None:
            return
        if not math.isfinite(pin["lat"]) or not math.isfinite(pin["lng"]) or not -90 <= pin["lat"] <= 90 or not -180 <= pin["lng"] <= 180:
            return
        citizen = pin["kind"] == "citizen_report"
        if kind == "official" and citizen or kind == "citizen" and not citizen:
            return
        if kind and kind not in ("official", "citizen") and pin["kind"] != kind:
            return
        # Related metadata is restricted to projects already publicly visible.
        related = [p for p in projects if p and p.status in PUBLIC_PROJECT_STATUSES]
        if department and not any(department in (p.department.slug.lower(), p.department.name.lower()) for p in related):
            return
        if status and pin.get("status", "").lower() != status:
            return
        if ward and not any((p.ward or "").lower() == ward for p in related):
            return
        haystack = " ".join([pin.get("title", ""), pin.get("department", ""), search_text] + [" ".join(filter(None, [p.title, p.description, p.road_name, p.ward, p.contractor_name, p.department.name])) for p in related]).lower()
        if query and query not in haystack:
            return
        pins.append(pin)

    for p in _visible_projects():
        add({"kind": "project", "color": "green", "id": p.id, "title": p.title,
             "lat": p.latitude, "lng": p.longitude, "status": p.status,
             "department": p.department.name, "department_slug": p.department.slug,
             "ward": p.ward or "", "badge": f"✅ Verified Official — {p.department.name}",
             "delayed_label": "⚠️ Delayed — Reason Pending" if p.unexplained_delay else "⚠️ Delayed" if p.status == "delayed" else None}, [p])
    for cp in CitizenPost.query.filter_by(status="published").all():
        linked = db.session.get(Project, cp.linked_project_id) if cp.linked_project_id else None
        add({"kind": "citizen_report", "color": "blue", "id": cp.id,
             "title": cp.body[:70], "lat": cp.latitude, "lng": cp.longitude,
             "status": cp.status, "department": "", "badge": "👤 Citizen — Phone Verified"},
            [linked] if linked else [], cp.body + " " + (cp.address or ""))
    for conflict in Conflict.query.filter(Conflict.status.in_(["open", "escalated"])).all():
        a, b = conflict.project_a, conflict.project_b
        # Never leak a pending/draft title through a public conflict pin.
        if not a or not b:
            continue
        visible = [p for p in (a, b) if p.status in PUBLIC_PROJECT_STATUSES]
        if not visible:
            continue
        a = visible[0]
        public_title = " vs ".join(p.title for p in visible)
        add({"kind": "conflict", "color": "orange", "id": conflict.id,
             "title": f"⚠️ Conflict: {public_title} ({conflict.severity})",
             "lat": a.latitude, "lng": a.longitude, "status": conflict.status}, [a, b])
    for js in JointSchedule.query.filter_by(status="approved").all():
        projects = [db.session.get(Project, pid) for pid in js.project_ids or []]
        projects = [p for p in projects if p and p.status in PUBLIC_PROJECT_STATUSES]
        if not projects:
            continue
        first = projects[0]
        names = "+".join(sorted({p.department.name for p in projects}))
        add({"kind": "joint", "color": "purple", "id": js.id,
             "title": f"🤝 Coordinated Work — {js.location} ({names})",
             "lat": first.latitude, "lng": first.longitude, "status": js.status}, projects)
    return {"pins": pins}


@bp.route("/departments")
def departments():
    return render_template("departments.html", departments=Department.query.order_by(Department.name).all())


@bp.route("/departments/<slug>")
def department_page(slug):
    dept = Department.query.filter_by(slug=slug).first() or abort(404)
    projects = Project.query.filter(Project.department_id == dept.id,
                                    Project.status.in_(PUBLIC_PROJECT_STATUSES)).all()
    posts = OfficialPost.query.filter_by(department_id=dept.id, status="published") \
        .order_by(OfficialPost.published_at.desc()).all()
    messages = _dept_messages(dept.id)
    # private drafts / internal notes are NOT shown here (section 9)
    return render_template("department.html", dept=dept, projects=projects, posts=posts, messages=messages)


@bp.route("/projects")
def projects_list():
    q = request.args.get("q", "").strip().lower()
    dept_slug = request.args.get("department", "")
    status = request.args.get("status", "")
    ward = request.args.get("ward", "")
    kind = request.args.get("kind", "")  # official|citizen
    items = _visible_projects()
    if dept_slug:
        d = Department.query.filter_by(slug=dept_slug).first()
        items = [p for p in items if d and p.department_id == d.id]
    if status:
        items = [p for p in items if p.status == status]
    if ward:
        items = [p for p in items if (p.ward or "").lower() == ward.lower()]
    if q:
        def match(p):
            hay = " ".join(filter(None, [p.title, p.description, p.road_name, p.ward,
                                         p.contractor_name, p.department.name])).lower()
            return q in hay
        items = [p for p in items if match(p)]
    return render_template("projects.html", projects=items, departments=Department.query.all(),
                           q=q, dept_slug=dept_slug, status=status, ward=ward)


@bp.route("/projects/<int:pid>")
def project_page(pid):
    p = db.session.get(Project, pid) or abort(404)
    if p.status not in PUBLIC_PROJECT_STATUSES:
        abort(404)  # drafts/pending/rejected are private
    reports = CitizenPost.query.filter_by(linked_project_id=p.id) \
        .filter(CitizenPost.status != "removed").all()
    posts = OfficialPost.query.filter_by(project_id=p.id, status="published").all()
    messages = CoordinationMessage.query.filter_by(project_id=p.id) \
        .order_by(CoordinationMessage.created_at.asc()).all()
    joint = _js_for_project(p.id)
    return render_template("project.html", p=p, reports=reports, posts=posts,
                           messages=messages, joint=joint,
                           delay_label=("⚠️ Delayed — Reason Pending" if p.unexplained_delay else
                                        f"⚠️ Delayed — {p.delay_reason}" if p.status == "delayed" and p.delay_reason else None))


@bp.route("/coordination")
def coordination():
    messages = CoordinationMessage.query.order_by(CoordinationMessage.created_at.asc()).all()
    threads = {}
    for m in messages:
        key = m.thread_id or m.id
        threads.setdefault(key, []).append(m)
    return render_template("coordination.html", messages=messages,
                           departments=Department.query.all())


@bp.route("/citizen-reports")
def citizen_reports():
    posts = CitizenPost.query.filter(CitizenPost.status.in_(["published", "under_review", "resolved"])) \
        .order_by(CitizenPost.created_at.desc()).all()
    return render_template("citizen_reports.html", posts=posts)


@bp.route("/citizen-reports/<int:cid>")
def citizen_report_page(cid):
    cp = db.session.get(CitizenPost, cid) or abort(404)
    if cp.status == "removed":
        abort(404)
    responses = [r for r in cp.responses if r.status == "approved"]
    return render_template("citizen_report.html", cp=cp, responses=responses)


@bp.route("/joint-schedules")
def joint_schedules():
    js = JointSchedule.query.filter_by(status="approved").order_by(JointSchedule.created_at.desc()).all()
    return render_template("joint_schedules.html", schedules=js)


@bp.route("/joint-schedules/<int:jid>")
def joint_schedule_page(jid):
    js = db.session.get(JointSchedule, jid) or abort(404)
    if js.status not in ("approved", "completed"):
        abort(404)  # negotiation details stay private until approved
    projs = [db.session.get(Project, pid) for pid in js.project_ids]
    depts = [db.session.get(Department, did) for did in js.department_ids]
    return render_template("joint_schedule.html", js=js, projects=projs, departments=depts)


@bp.route("/contractors/<name>")
def contractor_page(name):
    stats = contractor_stats(name)
    projs = Project.query.filter(Project.contractor_name == name,
                                 Project.status.in_(PUBLIC_PROJECT_STATUSES)).all()
    return render_template("contractor.html", stats=stats, projects=projs, name=name)


@bp.route("/about")
def about():
    return render_template("about.html")
