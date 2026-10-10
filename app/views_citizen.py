"""Citizen dashboard — reports, subscriptions, notifications, profile."""
from datetime import timedelta

from flask import Blueprint, redirect, render_template, request, url_for, abort

from .models import (
    db, utcnow, CitizenPost, Notification, Subscription, Project, Department,
    ROLE_CITIZEN, USER_STATUS_ACTIVE, User,
)
from . import services
from .services import BusinessRuleError
from . import role_required, current_user

bp = Blueprint("citizen", __name__)

citizen_required = role_required(ROLE_CITIZEN)


@bp.route("/dashboard")
@citizen_required
def dashboard():
    u = current_user()
    my_reports = CitizenPost.query.filter_by(user_id=u.id).filter(CitizenPost.status != "removed") \
        .order_by(CitizenPost.created_at.desc()).all()
    notifs = Notification.query.filter_by(user_id=u.id).order_by(Notification.created_at.desc()).limit(10).all()
    subs = Subscription.query.filter_by(user_id=u.id).all()
    return render_template("citizen/dashboard.html", reports=my_reports, notifications=notifs, subs=subs)


@bp.route("/dashboard/reports", methods=["GET", "POST"])
@citizen_required
def reports():
    u = current_user()
    error = None
    dupes = []
    if request.method == "POST":
        f = request.form
        try:
            lat, lon = services.validate_coordinates(f.get("latitude"), f.get("longitude"))
            category = f.get("category", "other")
            if lat is not None and lon is not None:
                dupes = services.find_duplicate_reports(lat, lon, category)
            link_pid = f.get("linked_project_id") or None
            cp = services.create_citizen_post(
                u, body=f.get("body", "").strip(), photo_url=f.get("photo_url") or None,
                latitude=lat, longitude=lon, address=f.get("address", ""),
                category=category,
                linked_project_id=int(link_pid) if link_pid else None,
                duplicate_suggested_post_id=int(f["link_duplicate"]) if f.get("link_duplicate") else None,
            )
            db.session.commit()
            return redirect(url_for("citizen.report_page", cid=cp.id))
        except (BusinessRuleError, KeyError, ValueError) as e:
            db.session.rollback()
            error = str(e) or "Description is required."
    my_reports = CitizenPost.query.filter_by(user_id=u.id).filter(CitizenPost.status != "removed").all()
    projects = Project.query.filter(Project.status.in_(
        ("approved", "in_progress", "delayed", "completed", "verified_complete"))).all()
    return render_template("citizen/reports.html", reports=my_reports, error=error,
                           dupes=dupes, projects=projects, form=request.form)


@bp.route("/dashboard/reports/<int:cid>/edit", methods=["POST"])
@citizen_required
def edit_report(cid):
    cp = db.session.get(CitizenPost, cid) or abort(404)
    services.edit_citizen_post(cp, current_user(), request.form.get("body"))
    db.session.commit()
    return redirect(url_for("citizen.reports"))


@bp.route("/dashboard/reports/<int:cid>/delete", methods=["POST"])
@citizen_required
def delete_report(cid):
    cp = db.session.get(CitizenPost, cid) or abort(404)
    services.delete_citizen_post(cp, current_user())
    db.session.commit()
    return redirect(url_for("citizen.reports"))


@bp.route("/citizen-reports/<int:cid>/flag", methods=["POST"])
@citizen_required
def flag_report(cid):
    cp = db.session.get(CitizenPost, cid) or abort(404)
    services.flag_citizen_post(cp, current_user())
    db.session.commit()
    return redirect(request.referrer or url_for("public.citizen_report_page", cid=cid))


@bp.route("/citizen-reports/<int:cid>")
def report_page(cid):
    # public view reuses the public page
    return redirect(url_for("public.citizen_report_page", cid=cid))


@bp.route("/dashboard/subscriptions", methods=["GET", "POST"])
@citizen_required
def subscriptions():
    u = current_user()
    if request.method == "POST":
        ward = request.form.get("ward", "").strip()
        category = request.form.get("category", "").strip()
        if (ward or category) and not Subscription.query.filter_by(
                user_id=u.id, ward=ward, category=category).first():
            db.session.add(Subscription(user_id=u.id, ward=ward, category=category))
            services.audit("subscription.created", u, "subscription", None,
                           metadata={"ward": ward, "category": category}, request=request)
            db.session.commit()
        return redirect(url_for("citizen.subscriptions"))
    subs = Subscription.query.filter_by(user_id=u.id).all()
    return render_template("citizen/subscriptions.html", subs=subs)


@bp.route("/dashboard/subscriptions/<int:sid>/delete", methods=["POST"])
@citizen_required
def delete_subscription(sid):
    s = db.session.get(Subscription, sid) or abort(404)
    if s.user_id != current_user().id:
        abort(403)
    db.session.delete(s)
    db.session.commit()
    return redirect(url_for("citizen.subscriptions"))


@bp.route("/dashboard/notifications")
@citizen_required
def notifications():
    u = current_user()
    items = Notification.query.filter_by(user_id=u.id).order_by(Notification.created_at.desc()).all()
    for n in items:
        n.read = True
    services.audit("notifications.viewed", u, "notification", None, request=request)
    db.session.commit()
    return render_template("citizen/notifications.html", notifications=items)


@bp.route("/profile", methods=["GET", "POST"])
@citizen_required
def profile():
    u = current_user()
    if request.method == "POST":
        dn = request.form.get("display_name", "").strip()
        if dn:
            u.display_name = dn[:120]
            services.audit("profile.updated", u, "user", u.id, request=request)
            db.session.commit()
        return redirect(url_for("citizen.profile"))
    # Phone never shown publicly; masked even on own profile (privacy rules)
    masked = "••••••" + (services.decrypt_phone(u.phone_encrypted, services.current_app_secret())[-4:]
                         if u.phone_encrypted else "")
    return render_template("citizen/profile.html", u=u, masked_phone=masked)
