"""Department employee workspace — drafts, submissions, coordination posting, report responses."""
from datetime import date

from flask import Blueprint, redirect, render_template, request, url_for, abort, jsonify

from .models import (
    db, utcnow, Department, User, Project, ProjectPhoto, OfficialPost, CitizenPost,
    CoordinationMessage, Conflict, JointSchedule, Notification,
    ROLE_EMPLOYEE, ROLE_DEPT_HEAD, MESSAGE_TYPES,
)
from . import services
from .services import BusinessRuleError
from . import employee_required, current_user

bp = Blueprint("employee", __name__, url_prefix="/workspace")


def _me():
    return current_user()


@bp.route("/")
@employee_required
def workspace():
    u = _me()
    dept_pids = [p.id for p in Project.query.filter_by(department_id=u.department_id).all()]
    projects = Project.query.filter_by(department_id=u.department_id).all()
    posts = OfficialPost.query.filter_by(department_id=u.department_id).all()
    conflicts = Conflict.query.filter(
        (Conflict.project_a_id.in_(dept_pids)) | (Conflict.project_b_id.in_(dept_pids))).all() if dept_pids else []
    notifs = Notification.query.filter_by(user_id=u.id).order_by(Notification.created_at.desc()).limit(10).all()
    reports = CitizenPost.query.filter_by(status="published").all()
    return render_template("workspace/home.html", u=u, projects=projects, posts=posts,
                           conflicts=conflicts, notifications=notifs, reports=reports[:20])


@bp.route("/projects")
@employee_required
def projects():
    u = _me()
    items = Project.query.filter_by(department_id=u.department_id).order_by(Project.created_at.desc()).all()
    return render_template("workspace/projects.html", projects=items)


@bp.route("/projects/new", methods=["GET", "POST"])
@employee_required
def new_project():
    u = _me()
    error = None
    if request.method == "POST":
        f = request.form
        try:
            if not f.get("latitude") or not f.get("longitude"):
                raise BusinessRuleError("Projects cannot be created without a location (rule 1).")
            if not f.get("start_date") or not f.get("end_date"):
                raise BusinessRuleError("Projects cannot be created without start and end dates (rule 2).")
            lat, lon = services.validate_coordinates(f["latitude"], f["longitude"], required=True)
            if date.fromisoformat(f["end_date"]) < date.fromisoformat(f["start_date"]):
                raise BusinessRuleError("End date must not precede start date.")
            if not f.get("title", "").strip():
                raise BusinessRuleError("Title is required.")
            p = Project(
                title=f["title"].strip(), description=f.get("description", ""),
                department_id=u.department_id, category=f.get("category", "road"),
                contractor_name=f.get("contractor_name", ""), budget=f.get("budget") or None,
                start_date=date.fromisoformat(f["start_date"]), end_date=date.fromisoformat(f["end_date"]),
                latitude=lat, longitude=lon,
                address=f.get("address", ""), ward=f.get("ward", ""), road_name=f.get("road_name", ""),
                status="draft", created_by=u.id,
            )
            db.session.add(p)
            db.session.flush()
            services.audit("project.draft_created", u, "project", p.id, request=request)
            if f.get("submit_now"):
                services.submit_project(p, u, request=request)
            db.session.commit()
            return redirect(url_for("employee.project_detail", pid=p.id))
        except (BusinessRuleError, ValueError, KeyError) as e:
            db.session.rollback()
            error = str(e)
    return render_template("workspace/project_new.html", error=error)


@bp.route("/projects/<int:pid>", methods=["GET", "POST"])
@employee_required
def project_detail(pid):
    u = _me()
    p = db.session.get(Project, pid) or abort(404)
    if p.department_id != u.department_id:
        abort(403)  # cannot access other departments' private drafts
    error = None
    if request.method == "POST":
        action = request.form.get("action")
        try:
            if action == "submit" and p.status == "draft":
                services.submit_project(p, u, request=request)
            elif action == "start" and p.status == "approved":
                services.start_project(p, u, request=request)
            elif action == "progress" and p.status in ("in_progress", "delayed"):
                p.progress_percentage = max(0, min(100, int(request.form.get("progress", p.progress_percentage))))
                services.audit("project.progress_updated", u, "project", p.id,
                               metadata={"progress": p.progress_percentage}, request=request)
            elif action == "complete" and p.status in ("in_progress", "delayed"):
                if p.progress_percentage != 100 or not request.form.get("note", "").strip():
                    raise BusinessRuleError("Completion requires progress 100% and a completion note.")
                if p.status == "delayed" and not p.delay_reason:
                    raise BusinessRuleError("Publish a delay reason before completing delayed work.")
                upload = request.files.get("photo") or request.files.get("photo_file")
                if upload and upload.filename:
                    from .uploads import save_image
                    photo_url = save_image(upload)
                    db.session.add(ProjectPhoto(project_id=p.id, photo_url=photo_url,
                                                kind="completion", caption=request.form.get("caption", "Completion photo")[:300],
                                                uploaded_by=u.id))
                    db.session.flush()
                    services.audit("project.photo_uploaded", u, "project", p.id, metadata={"photo_url": photo_url}, request=request)
                services.complete_project(p, u, request.form.get("note", ""), request=request)
            elif action == "delay_reason" and p.status == "delayed":
                services.post_delay_reason(p, u, request.form.get("reason", ""), request=request)
            elif action == "dates" and p.status in ("draft", "approved", "pending_chairman"):
                if request.form.get("start_date"):
                    p.start_date = date.fromisoformat(request.form["start_date"])
                if request.form.get("end_date"):
                    p.end_date = date.fromisoformat(request.form["end_date"])
                if p.end_date < p.start_date:
                    raise BusinessRuleError("End date must not precede start date.")
                db.session.flush()
                services.detect_conflicts(p)  # conflict detection runs on date/location change
                services.audit("project.dates_changed", u, "project", p.id, request=request)
            else:
                raise BusinessRuleError("Action not allowed from current state.")
            db.session.commit()
            return redirect(url_for("employee.project_detail", pid=p.id))
        except (BusinessRuleError, ValueError, KeyError) as e:
            db.session.rollback()
            error = str(e)
    photos = ProjectPhoto.query.filter_by(project_id=p.id).all()
    return render_template("workspace/project_detail.html", p=p, photos=photos, error=error)


@bp.route("/posts")
@employee_required
def posts():
    u = _me()
    items = OfficialPost.query.filter_by(department_id=u.department_id).order_by(OfficialPost.created_at.desc()).all()
    return render_template("workspace/posts.html", posts=items)


@bp.route("/posts/new", methods=["GET", "POST"])
@employee_required
def new_post():
    u = _me()
    error = None
    if request.method == "POST":
        f = request.form
        try:
            post = OfficialPost(project_id=f.get("project_id") or None,
                                department_id=u.department_id, employee_id=u.id,
                                title=f["title"].strip(), body=f["body"].strip(), status="draft")
            db.session.add(post)
            db.session.flush()
            services.audit("post.draft_created", u, "official_post", post.id, request=request)
            if f.get("submit_now"):
                services.submit_post(post, u, request=request)
            db.session.commit()
            return redirect(url_for("employee.post_detail", post_id=post.id))
        except (BusinessRuleError, KeyError) as e:
            db.session.rollback()
            error = str(e) or "Title and body are required."
    dept_projects = Project.query.filter_by(department_id=u.department_id).all()
    return render_template("workspace/post_new.html", error=error, projects=dept_projects)


@bp.route("/posts/<int:post_id>", methods=["GET", "POST"])
@employee_required
def post_detail(post_id):
    u = _me()
    post = db.session.get(OfficialPost, post_id) or abort(404)
    if post.department_id != u.department_id:
        abort(403)
    error = None
    if request.method == "POST":
        action = request.form.get("action")
        try:
            if action == "edit":
                services.edit_published_post_guard(post)
                if post.status not in ("draft", "rejected"):
                    raise BusinessRuleError("Only drafts or rejected posts can be edited.")
                post.title = request.form.get("title", post.title)
                post.body = request.form.get("body", post.body)
                post.status = "draft"
                services.audit("post.edited", u, "official_post", post.id, request=request)
            elif action == "submit":
                services.submit_post(post, u, request=request)
            else:
                raise BusinessRuleError("Unknown action.")
            db.session.commit()
            return redirect(url_for("employee.post_detail", post_id=post.id))
        except (BusinessRuleError, ValueError, KeyError) as e:
            db.session.rollback()
            error = str(e)
    return render_template("workspace/post_detail.html", post=post, error=error)


@bp.route("/coordination", methods=["GET", "POST"])
@employee_required
def coordination():
    u = _me()
    error = None
    if request.method == "POST":
        f = request.form
        try:
            to_ids = [int(x) for x in f.getlist("to_departments")]
            msg = services.post_coordination_message(
                u.department, to_ids, f.get("message_type", "update"),
                f.get("body", "").strip(), u,
                project_id=f.get("project_id") or None,
                proposed_dates=f.get("proposed_dates", ""), location=f.get("location", ""),
                request=request)
            db.session.commit()
            if f.get("escalate_after"):
                pass
            return redirect(url_for("public.coordination"))
        except (BusinessRuleError, ValueError) as e:
            db.session.rollback()
            error = str(e) or "Message required."
    my_msgs = [m for m in CoordinationMessage.query.order_by(CoordinationMessage.created_at.desc()).all()
               if m.from_department_id == u.department_id or u.department_id in (m.to_department_ids or [])]
    dept_projects = Project.query.filter_by(department_id=u.department_id).all()
    return render_template("workspace/coordination.html", messages=my_msgs, error=error,
                           departments=Department.query.filter(Department.id != u.department_id).all(),
                           projects=dept_projects, joint_projects=Project.query.filter(Project.status.in_(["pending_chairman", "approved", "in_progress", "delayed"])).all(), message_types=[t for t in MESSAGE_TYPES if t not in ("conflict_alert", "decision")])


@bp.route("/reports")
@employee_required
def reports():
    u = _me()
    items = CitizenPost.query.filter(CitizenPost.status.in_(["published", "under_review"])) \
        .order_by(CitizenPost.created_at.desc()).all()
    return render_template("workspace/reports.html", posts=items)


@bp.route("/reports/<int:cid>/respond", methods=["POST"])
@employee_required
def respond_report(cid):
    u = _me()
    cp = db.session.get(CitizenPost, cid) or abort(404)
    services.respond_to_report(request.form.get("body", "").strip(), u, cp, request=request)
    db.session.commit()
    return redirect(url_for("employee.reports"))


@bp.route("/notifications")
@employee_required
def notifications():
    u = _me()
    items = Notification.query.filter_by(user_id=u.id).order_by(Notification.created_at.desc()).all()
    for n in items:
        n.read = True
    db.session.commit()
    return render_template("workspace/notifications.html", notifications=items)


@bp.route("/joint-schedules/propose", methods=["POST"])
@employee_required
def propose_joint():
    u = _me()
    f = request.form
    try:
        pids = [int(x) for x in f.getlist("project_ids")]
        services.propose_joint_schedule(
            u, pids, f.get("location", ""),
            date.fromisoformat(f["start_date"]) if f.get("start_date") else None,
            date.fromisoformat(f["end_date"]) if f.get("end_date") else None,
            f.get("reason", ""), request=request)
        db.session.commit()
    except (ValueError, KeyError) as exc:
        db.session.rollback()
        raise BusinessRuleError("Use valid project IDs and dates.") from exc
    return redirect(url_for("public.joint_schedules"))
