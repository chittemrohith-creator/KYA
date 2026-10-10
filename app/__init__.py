"""Application factory, auth decorators, seed data."""
import os
from datetime import date, timedelta

from flask import Flask, g, jsonify, redirect, render_template, request, session, url_for

from .models import (
    db, utcnow,
    ROLE_CITIZEN, ROLE_EMPLOYEE, ROLE_DEPT_HEAD, ROLE_CHAIRMAN, ROLE_ADMIN,
    USER_STATUS_ACTIVE, USER_STATUS_PENDING,
    Department, User, Project, OfficialPost, CitizenPost,
    CoordinationMessage, Subscription,
)
from . import services
from .services import hash_password, set_app_secret


# ------------------------------------------------------------------ auth helpers
def login_user_record(user):
    session["uid"] = user.id
    session["role"] = user.role


def logout_user():
    session.clear()


def current_user():
    uid = session.get("uid")
    if not uid:
        return None
    return db.session.get(User, uid)


def role_required(*roles, active_only=True):
    def deco(fn):
        from functools import wraps

        @wraps(fn)
        def wrapper(*a, **kw):
            u = current_user()
            if u is None or u.role not in roles:
                if request.path.startswith("/api/"):
                    return jsonify({"error": "Forbidden"}), 403
                return redirect(url_for("auth.login_page"))
            if active_only and u.status != USER_STATUS_ACTIVE:
                if request.path.startswith("/api/"):
                    return jsonify({"error": "Account not active"}), 403
                return render_template("message.html",
                                       title="Not verified",
                                       body="Your account is awaiting Chairman verification.")
            g.user = u
            return fn(*a, **kw)
        return wrapper
    return deco


def chairman_required(fn):
    return role_required(ROLE_CHAIRMAN)(fn)


def employee_required(fn):
    return role_required(ROLE_EMPLOYEE, ROLE_DEPT_HEAD)(fn)


def admin_required(fn):
    return role_required(ROLE_ADMIN)(fn)


# ------------------------------------------------------------------ seeding
DEFAULT_DEPARTMENTS = [
    ("Roads", "roads"), ("Drainage", "drainage"), ("Water Supply", "water-supply"),
    ("Electricity", "electricity"), ("Telecom", "telecom"), ("Sanitation", "sanitation"),
]


def seed_all(app, demo=True):
    """Seed departments + pre-seeded Chairman/Admin. Optionally demo accounts & MG Road scenario."""
    with app.app_context():
        db.create_all()
        secret = app.config["APP_SECRET"]
        dept_map = {}
        for name, slug in DEFAULT_DEPARTMENTS:
            d = Department.query.filter_by(slug=slug).first()
            if not d:
                d = Department(name=name, slug=slug,
                               description=f"Municipal {name} department.",
                               contact_email=f"contact@civic.municipality.{slug}",
                               contact_phone="+91-80-2000-0000",
                               office_address="Municipal Corporation Building, Civic Center")
                db.session.add(d)
                db.session.flush()
            dept_map[slug] = d

        if not User.query.filter_by(role=ROLE_CHAIRMAN).first():
            chair = User(role=ROLE_CHAIRMAN, full_name="Municipal Chairman",
                         display_name="Municipal Chairman",
                         email="chairman@civic.municipality",
                         phone_hash=services.hash_phone("+919000000001", secret),
                         phone_encrypted=services.encrypt_phone("+919000000001", secret),
                         phone_verified=True, status=USER_STATUS_ACTIVE,
                         password_hash=hash_password("chairman123"))
            db.session.add(chair)
        if not User.query.filter_by(role=ROLE_ADMIN).first():
            adm = User(role=ROLE_ADMIN, full_name="System Administrator",
                       email="admin@civic.municipality",
                       phone_hash=services.hash_phone("+919000000002", secret),
                       phone_encrypted=services.encrypt_phone("+919000000002", secret),
                       phone_verified=True, status=USER_STATUS_ACTIVE,
                       password_hash=hash_password("admin123"))
            db.session.add(adm)
        db.session.commit()

        if demo:
            seed_demo(app, dept_map)
        return dept_map


def seed_demo(app, dept_map=None):
    """Demo scenario mirroring the spec example: Roads relaying MG Road, Drainage repair same stretch."""
    secret = app.config["APP_SECRET"]
    dept_map = dept_map or {d.slug: d for d in Department.query.all()}
    chair = User.query.filter_by(role=ROLE_CHAIRMAN).first()
    if chair is None:
        return

    roads_emp = User.query.filter_by(employee_code="RD-1001").first()
    if not roads_emp:
        roads_emp = User(role=ROLE_EMPLOYEE, full_name="Ravi Kumar", employee_code="RD-1001",
                         department_id=dept_map["roads"].id, designation="Assistant Engineer",
                         email="ravi@roads.municipality", phone_verified=True,
                         status=USER_STATUS_ACTIVE, approved_by=chair.id, approved_at=utcnow(),
                         password_hash=hash_password("roads123"),
                         phone_hash=services.hash_phone("+919111111111", secret),
                         phone_encrypted=services.encrypt_phone("+919111111111", secret))
        drain_emp = User(role=ROLE_EMPLOYEE, full_name="Sunita Rao", employee_code="DR-2002",
                         department_id=dept_map["drainage"].id, designation="Junior Engineer",
                         email="sunita@drainage.municipality", phone_verified=True,
                         status=USER_STATUS_ACTIVE, approved_by=chair.id, approved_at=utcnow(),
                         password_hash=hash_password("drainage123"),
                         phone_hash=services.hash_phone("+919222222222", secret),
                         phone_encrypted=services.encrypt_phone("+919222222222", secret))
        pending_emp = User(role=ROLE_EMPLOYEE, full_name="Amit Verma", employee_code="WT-3003",
                           department_id=dept_map["water-supply"].id, designation="Engineer",
                           email="amit@water.municipality", status=USER_STATUS_PENDING,
                           password_hash=hash_password("water123"),
                           phone_hash=services.hash_phone("+919333333333", secret),
                           phone_encrypted=services.encrypt_phone("+919333333333", secret))
        citizen = User(role=ROLE_CITIZEN, display_name="Priya S", phone_verified=True,
                       status=USER_STATUS_ACTIVE,
                       phone_hash=services.hash_phone("+919888888888", secret),
                       phone_encrypted=services.encrypt_phone("+919888888888", secret))
        db.session.add_all([roads_emp, drain_emp, pending_emp, citizen])
        db.session.flush()

    if not Project.query.filter_by(title="MG Road Relaying — Phase 1").first():
        # MG Road, Bengaluru approx coordinates
        p1 = Project(title="MG Road Relaying — Phase 1",
                     description="Resurfacing of MG Road from Circle to 1st Block.",
                     department_id=dept_map["roads"].id, category="road",
                     contractor_name="BuildRight Infra", budget=4500000,
                     start_date=date(2026, 10, 12), end_date=date(2026, 10, 14),
                     status="approved", progress_percentage=60,
                     latitude=12.9756, longitude=77.6051,
                     address="MG Road, Ward 10", ward="Ward 10", road_name="MG Road",
                     created_by=roads_emp.id, approved_by=chair.id, approved_at=utcnow())
        p2 = Project(title="Storm Drain Repair — MG Road Stretch",
                     description="Replacement of collapsed drain section near the MG Road circle.",
                     department_id=dept_map["drainage"].id, category="drainage",
                     contractor_name="AquaFlow Contractors", budget=1800000,
                     start_date=date(2026, 10, 12), end_date=date(2026, 10, 15),
                     status="pending_chairman", progress_percentage=0,
                     latitude=12.9757, longitude=77.6052,
                     address="MG Road near Circle, Ward 10", ward="Ward 10", road_name="MG Road",
                     created_by=drain_emp.id)
        db.session.add_all([p1, p2])
        db.session.flush()
        services.detect_conflicts(p2)

        post = OfficialPost(project_id=p1.id, department_id=dept_map["roads"].id,
                            employee_id=roads_emp.id,
                            title="MG Road night work schedule",
                            body="Relaying will proceed 10PM–5AM to reduce traffic impact.",
                            status="published", submitted_at=utcnow(), approved_by=chair.id,
                            approved_at=utcnow(), published_at=utcnow())
        draft = OfficialPost(department_id=dept_map["drainage"].id, employee_id=drain_emp.id,
                             title="Draft: Pre-monsoon drain inspection results",
                             body="Inspection covered 42 km of storm drains…", status="draft")
        db.session.add_all([post, draft])

        cp = CitizenPost(user_id=User.query.filter_by(display_name="Priya S").first().id,
                         body="Open manhole on MG Road footpath near the metro exit — safety hazard.",
                         category="drainage", latitude=12.9758, longitude=77.6050,
                         address="MG Road Metro Exit", status="published")
        db.session.add(cp)
        db.session.flush()
        db.session.add(Subscription(user_id=cp.user_id, ward="Ward 10", category=""))
        db.session.add(CoordinationMessage(
            from_department_id=dept_map["roads"].id,
            to_department_ids=[dept_map["drainage"].id],
            project_id=p1.id, message_type="proposal",
            body="Relaying MG Road 12–14 Oct. Requesting Drainage confirm pending work on this stretch.",
            proposed_dates="2026-10-12 to 2026-10-14", location="MG Road",
            created_by=roads_emp.id))
        db.session.commit()


# ------------------------------------------------------------------ app factory
def create_app(test_config=None):
    app = Flask(__name__)
    app.config.update(
        SECRET_KEY=os.environ.get("CIVICSYNC_SECRET", "dev-login-secret"),
        APP_SECRET=os.environ.get("CIVICSYNC_DATA_SECRET", "dev-secret-change-me"),
        SQLALCHEMY_DATABASE_URI=os.environ.get("CIVICSYNC_DB", "sqlite:///civicsync.db"),
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
        JSON_SORT_KEYS=False,
    )
    if test_config:
        app.config.update(test_config)
    from .bootstrap import configure_app_paths
    configure_app_paths(app)  # templates/ and static/ live at the repository root
    set_app_secret(app.config["APP_SECRET"])
    db.init_app(app)

    from . import views_public, views_auth, views_employee, views_chairman, views_admin, views_citizen, api
    app.register_blueprint(views_public.bp)
    app.register_blueprint(views_auth.bp)
    app.register_blueprint(views_employee.bp)
    app.register_blueprint(views_chairman.bp)
    app.register_blueprint(views_admin.bp)
    app.register_blueprint(views_citizen.bp)
    app.register_blueprint(api.bp)

    @app.context_processor
    def inject_globals():
        u = None
        try:
            u = current_user()
        except RuntimeError:
            u = None
        return {
            "current_user_obj": u,
            "badge_official": services.public_badge_for,
            "ROLE_CHAIRMAN": ROLE_CHAIRMAN,
            "dt": lambda x: x.strftime("%d %b %Y, %H:%M") if x else "—",
            "dd": lambda x: x.strftime("%d %b %Y") if x else "—",
        }

    @app.errorhandler(services.BusinessRuleError)
    def handle_rule(e):
        if request.path.startswith("/api/"):
            return jsonify({"error": str(e)}), e.code
        return render_template("message.html", title="Action not allowed", body=str(e)), e.code

    with app.app_context():
        seed_all(app, demo=not app.config.get("TESTING_SEED_MINIMAL", False))
    return app
