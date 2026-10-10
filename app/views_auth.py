"""Auth flows: citizen signup+OTP, citizen login, employee login/register, logout."""
from datetime import timedelta

from flask import Blueprint, flash, jsonify, redirect, render_template, request, session, url_for

from .models import db, utcnow, ROLE_CITIZEN, ROLE_EMPLOYEE, ROLE_DEPT_HEAD, ROLE_CHAIRMAN, ROLE_ADMIN, User, USER_STATUS_ACTIVE
from . import services
from .services import BusinessRuleError, check_password, hash_phone
from . import login_user_record, logout_user, current_user

bp = Blueprint("auth", __name__)


@bp.route("/login")
def login_page():
    return render_template("login.html")


@bp.route("/signup")
def signup_page():
    return render_template("signup.html")


@bp.route("/employee/login")
def employee_login_page():
    return render_template("employee_login.html")


@bp.route("/employee/register")
def employee_register_page():
    from .models import Department
    return render_template("employee_register.html", departments=Department.query.all())


# ----------------------------------------------------------- citizen OTP signup (8.1)
@bp.route("/api/signup/citizen/start", methods=["POST"])
def api_signup_start():
    data = request.get_json(force=True) or {}
    phone = (data.get("phone") or "").strip()
    try:
        user, otp = services.begin_citizen_signup(phone)
        db.session.commit()
    except BusinessRuleError as e:
        return jsonify({"error": str(e)}), e.code
    # Demo build: no SMS gateway, so OTP is surfaced in the response + stored in session.
    session["signup_uid"] = user.id
    return jsonify({"ok": True, "demo_otp": otp, "message": "OTP sent (demo: shown here; expires in 5 minutes)."})


@bp.route("/api/signup/citizen/verify", methods=["POST"])
def api_signup_verify():
    data = request.get_json(force=True) or {}
    uid = session.get("signup_uid")
    user = db.session.get(User, uid) if uid else None
    if not user:
        return jsonify({"error": "Start a new signup."}), 400
    try:
        services.verify_citizen_otp(user, data.get("otp", ""), data.get("display_name"))
        db.session.commit()
    except BusinessRuleError as e:
        return jsonify({"error": str(e)}), e.code
    login_user_record(user)
    session.pop("signup_uid", None)
    services.audit("login", user, "session", request=request)
    db.session.commit()
    return jsonify({"ok": True, "redirect": "/dashboard"})


# ----------------------------------------------------------- citizen login (phone + password-less demo via OTP)
@bp.route("/api/login/citizen", methods=["POST"])
def api_login_citizen():
    data = request.get_json(force=True) or {}
    phone = (data.get("phone") or "").strip()
    secret = current_app_secret()
    u = User.query.filter_by(phone_hash=hash_phone(phone, secret), role=ROLE_CITIZEN).first()
    if not u or u.status != USER_STATUS_ACTIVE:
        return jsonify({"error": "No verified account for this phone. Sign up first."}), 401
    otp = services.gen_otp()
    u.otp_hash = services.hash_password(otp)
    u.otp_expires_at = utcnow() + timedelta(minutes=services.OTP_TTL_MINUTES)
    u.otp_attempts = 0
    db.session.commit()
    session["login_otp_uid"] = u.id
    return jsonify({"ok": True, "demo_otp": otp, "message": "Login OTP sent (expires in 5 minutes)."})


@bp.route("/api/login/citizen/verify", methods=["POST"])
def api_login_citizen_verify():
    data = request.get_json(force=True) or {}
    u = db.session.get(User, session.get("login_otp_uid") or 0) if session.get("login_otp_uid") else None
    if not u:
        return jsonify({"error": "Request a new OTP."}), 400
    try:
        if u.otp_expires_at is None or utcnow() > u.otp_expires_at:
            raise BusinessRuleError("OTP expired.")
        if u.otp_attempts >= services.OTP_MAX_ATTEMPTS:
            raise BusinessRuleError("Too many incorrect attempts.")
        if not check_password(data.get("otp", ""), u.otp_hash):
            u.otp_attempts += 1
            db.session.commit()
            raise BusinessRuleError("Incorrect OTP.")
        u.otp_hash = None
        u.last_login = utcnow()
        db.session.commit()
    except BusinessRuleError as e:
        return jsonify({"error": str(e)}), e.code
    login_user_record(u)
    services.audit("login.citizen", u, "session", request=request)
    db.session.commit()
    return jsonify({"ok": True, "redirect": "/dashboard"})


def current_app_secret():
    from .services import current_app_secret as s
    return s()


# ----------------------------------------------------------- employee login / register
@bp.route("/api/login/employee", methods=["POST"])
def api_login_employee():
    data = request.get_json(force=True) or {}
    code = (data.get("employee_code") or "").strip()
    pw = data.get("password") or ""
    # Staff login accepts employee code or official email (pre-seeded Chairman/Admin have no department code).
    u = User.query.filter_by(employee_code=code).first() if code else None
    if u is None and "@" in code:
        u = User.query.filter_by(email=code.lower()).first()
    if not u or not check_password(pw, u.password_hash or ""):
        return jsonify({"error": "Invalid credentials."}), 401
    if u.role == ROLE_CITIZEN:
        return jsonify({"error": "Invalid credentials."}), 401  # citizens use the phone+OTP login
    if u.status == "pending_verification":
        return jsonify({"error": "Account pending Chairman verification. You cannot access the workspace yet."}), 403
    if u.status in ("rejected", "suspended"):
        return jsonify({"error": f"Account {u.status}. Reason: {u.rejection_reason or '—'}"}), 403
    u.last_login = utcnow()
    login_user_record(u)
    services.audit("login.employee", u, "session", request=request)
    db.session.commit()
    home = "/chairman" if u.role == ROLE_CHAIRMAN else ("/admin" if u.role == ROLE_ADMIN else "/workspace")
    return jsonify({"ok": True, "redirect": home})


@bp.route("/api/register/employee", methods=["POST"])
def api_register_employee():
    data = request.get_json(force=True) or {}
    try:
        u = services.register_employee(
            full_name=data.get("full_name", "").strip(),
            employee_code=data.get("employee_code", "").strip(),
            department=data.get("department", "").strip(),
            designation=data.get("designation", "").strip(),
            email=data.get("email", "").strip(),
            phone=data.get("phone", "").strip(),
            password=data.get("password", ""),
            request=request,
        )
        db.session.commit()
    except BusinessRuleError as e:
        return jsonify({"error": str(e)}), e.code
    services.audit("employee.registration_submitted", u, "user", u.id, request=request)
    db.session.commit()
    return jsonify({"ok": True, "status": u.status,
                    "message": "Registration submitted. Status: pending_verification — the Municipal Chairman will review."})


@bp.route("/logout", methods=["POST", "GET"])
def logout():
    u = current_user()
    if u:
        services.audit("logout", u, "session", request=request)
        db.session.commit()
    logout_user()
    return redirect(url_for("public.home"))
