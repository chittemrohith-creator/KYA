"""System Administrator — maintenance only. Cannot approve official work (rule 26)."""
import re
from sqlalchemy import inspect
from flask import Blueprint, redirect, render_template, request, url_for, abort, jsonify

from .models import (
    db, Department, User, Project, OfficialPost, CitizenPost, CoordinationMessage,
    Conflict, JointSchedule, Notification, AuditLog, ROLE_ADMIN,
)
from . import services
from .services import BusinessRuleError
from . import admin_required, current_user

bp = Blueprint("admin", __name__, url_prefix="/admin")


@bp.route("/")
@admin_required
def dashboard():
    counts = {
        "departments": Department.query.count(),
        "users": User.query.count(),
        "projects": Project.query.count(),
        "official_posts": OfficialPost.query.count(),
        "citizen_reports": CitizenPost.query.count(),
        "coordination_messages": CoordinationMessage.query.count(),
        "conflicts": Conflict.query.count(),
        "joint_schedules": JointSchedule.query.count(),
        "audit_entries": AuditLog.query.count(),
    }
    return render_template("admin/dashboard.html", counts=counts)


@bp.route("/departments", methods=["GET", "POST"])
@admin_required
def departments(delete_id=None):
    error = None
    if request.method == "POST":
        try:
            action = "delete" if delete_id is not None else request.form.get("action", "create")
            did = delete_id if delete_id is not None else (request.form.get("department_id", type=int) or request.form.get("dept_id", type=int))
            d = db.session.get(Department, did) if did else None
            if action in ("edit", "delete") and not d:
                abort(404)
            if action == "delete":
                # Check every scalar FK as well as JSON-held department references.
                for mapper in db.Model.registry.mappers:
                    for col in mapper.columns:
                        if any(fk.target_fullname == "departments.id" for fk in col.foreign_keys):
                            if db.session.query(mapper.class_).filter(col == d.id).first():
                                raise BusinessRuleError("Cannot delete: department is referenced and cannot be deleted.")
                if any(d.id in (m.to_department_ids or []) for m in CoordinationMessage.query.all()) or any(d.id in (j.department_ids or []) or d.id in (j.no_show_departments or []) for j in JointSchedule.query.all()):
                    raise BusinessRuleError("Cannot delete: department has coordination references and cannot be deleted.")
                services.audit("department.deleted", current_user(), "department", d.id, metadata={"name": d.name}, request=request)
                db.session.delete(d)
            elif action in ("create", "edit"):
                name = request.form.get("name", "").strip()
                slug = request.form.get("slug", "").strip().lower()
                if not name or len(name) > 120 or not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", slug) or len(slug) > 120:
                    raise BusinessRuleError("Name and valid lowercase slug are required.")
                if Department.query.filter((Department.name == name) | (Department.slug == slug)).filter(Department.id != (d.id if d else -1)).first():
                    raise BusinessRuleError("Name or slug already exists.")
                if d is None:
                    d = Department()
                    db.session.add(d)
                d.name, d.slug = name, slug
                for field in ("description", "contact_email", "contact_phone", "office_address"):
                    setattr(d, field, request.form.get(field, ""))
                db.session.flush()
                services.audit("department." + ("created" if action == "create" else "updated"), current_user(), "department", d.id, request=request)
            else:
                raise BusinessRuleError("Unknown action.")
            db.session.commit()
            return redirect(url_for("admin.departments"))
        except BusinessRuleError as exc:
            db.session.rollback()
            if action == "delete" and d is not None:
                services.audit("department.delete_blocked", current_user(), "department", d.id,
                               metadata={"reason": str(exc)}, request=request)
                db.session.commit()
            error = str(exc)
    return render_template("admin/departments.html", departments=Department.query.all(), error=error)


@bp.route("/users")
@admin_required
def users():
    items = User.query.order_by(User.created_at.desc()).all()
    return render_template("admin/users.html", users=items)


@bp.route("/users/<int:uid>/transfer-chairman", methods=["POST"])
@admin_required
def transfer_chairman(uid):
    """Edge case: Chairman unavailable — Admin temporarily assigns approval authority. Logged."""
    new = db.session.get(User, uid) or abort(404)
    current = User.query.filter_by(role="chairman").first()
    services.transfer_chairman(current, new, current_user(), request=request)
    db.session.commit()
    return redirect(url_for("admin.users"))


@bp.route("/seed", methods=["GET", "POST"])
@admin_required
def seed():
    if request.method == "POST":
        from . import seed_demo
        from flask import current_app
        seed_demo(current_app._get_current_object())
        services.audit("demo_data.seeded", current_user(), "system", None, request=request)
        db.session.commit()
        return redirect(url_for("admin.seed"))
    return render_template("admin/seed.html")


@bp.route("/system")
@admin_required
def system():
    health = {
        "database": str(db.engine.url).split("?")[0],
        "tables": len(db.Model.registry.mappers),
        "audit_integrity": "append-only (no update/delete endpoints exposed)",
    }
    return render_template("admin/system.html", health=health)


@bp.route("/departments/<int:did>/delete", methods=["POST"])
@admin_required
def delete_department(did):
    return departments(delete_id=did)
