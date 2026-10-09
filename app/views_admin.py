"""System Administrator — maintenance only. Cannot approve official work (rule 26)."""
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
def departments():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        slug = request.form.get("slug", "").strip().lower().replace(" ", "-")
        if name and slug and not Department.query.filter_by(slug=slug).first():
            d = Department(name=name, slug=slug, description=request.form.get("description", ""),
                           contact_email=request.form.get("contact_email", ""),
                           contact_phone=request.form.get("contact_phone", ""))
            db.session.add(d)
            services.audit("department.created", current_user(), "department", None,
                           metadata={"name": name}, request=request)
            db.session.commit()
        return redirect(url_for("admin.departments"))
    return render_template("admin/departments.html", departments=Department.query.all())


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
