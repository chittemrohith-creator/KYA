"""CivicSync data model — mirrors the complete schema in the product spec (section 22)."""
from datetime import datetime, timezone

from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()


def utcnow():
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------- role constants
ROLE_CITIZEN = "citizen"
ROLE_EMPLOYEE = "employee"
ROLE_DEPT_HEAD = "dept_head"
ROLE_CHAIRMAN = "chairman"
ROLE_ADMIN = "admin"

USER_STATUS_PENDING = "pending_verification"
USER_STATUS_ACTIVE = "active"
USER_STATUS_REJECTED = "rejected"
USER_STATUS_SUSPENDED = "suspended"

PROJECT_STATUSES = [
    "draft", "pending_chairman", "approved", "in_progress", "completed",
    "verified_complete", "rejected", "delayed", "cancelled",
]

POST_STATUSES = ["draft", "pending_chairman", "approved", "published", "rejected", "archived"]

CITIZEN_POST_STATUSES = ["published", "under_review", "resolved", "removed"]

MESSAGE_TYPES = ["proposal", "objection", "confirmation", "update", "conflict_alert", "escalation", "decision"]


class Department(db.Model):
    __tablename__ = "departments"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False, unique=True)
    slug = db.Column(db.String(120), nullable=False, unique=True)
    description = db.Column(db.Text, default="")
    contact_email = db.Column(db.String(200), default="")
    contact_phone = db.Column(db.String(50), default="")
    office_address = db.Column(db.String(300), default="")
    created_at = db.Column(db.DateTime, default=utcnow)

    users = db.relationship("User", backref="department", lazy=True)
    projects = db.relationship("Project", backref="department", lazy=True)


class User(db.Model):
    __tablename__ = "users"
    id = db.Column(db.Integer, primary_key=True)
    role = db.Column(db.String(20), nullable=False)  # citizen|employee|dept_head|chairman|admin
    phone_encrypted = db.Column(db.String(500))       # encrypted at rest, never shown publicly
    phone_hash = db.Column(db.String(64), index=True)  # sha256 for duplicate lookup
    phone_verified = db.Column(db.Boolean, default=False)
    email = db.Column(db.String(200))
    password_hash = db.Column(db.String(200))
    display_name = db.Column(db.String(120))
    full_name = db.Column(db.String(200))
    employee_code = db.Column(db.String(60), unique=True)
    department_id = db.Column(db.Integer, db.ForeignKey("departments.id"))
    designation = db.Column(db.String(120))
    status = db.Column(db.String(30), default=USER_STATUS_ACTIVE)
    approved_by = db.Column(db.Integer, db.ForeignKey("users.id"))
    approved_at = db.Column(db.DateTime)
    rejection_reason = db.Column(db.Text)
    otp_hash = db.Column(db.String(80))
    otp_expires_at = db.Column(db.DateTime)
    otp_attempts = db.Column(db.Integer, default=0)
    created_at = db.Column(db.DateTime, default=utcnow)
    updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow)
    last_login = db.Column(db.DateTime)

    approver = db.relationship("User", remote_side=[id])


class Project(db.Model):
    __tablename__ = "projects"
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text, default="")
    department_id = db.Column(db.Integer, db.ForeignKey("departments.id"), nullable=False)
    category = db.Column(db.String(60), nullable=False)  # road | underground_utility | other
    contractor_name = db.Column(db.String(200), default="")
    budget = db.Column(db.Numeric(14, 2), nullable=True)
    start_date = db.Column(db.Date, nullable=False)   # rule: cannot create without dates
    end_date = db.Column(db.Date, nullable=False)
    actual_start_date = db.Column(db.Date)
    actual_end_date = db.Column(db.Date)
    status = db.Column(db.String(30), default="draft", nullable=False)
    progress_percentage = db.Column(db.Integer, default=0)
    latitude = db.Column(db.Float, nullable=False)     # rule: cannot create without location
    longitude = db.Column(db.Float, nullable=False)
    address = db.Column(db.String(300), default="")
    ward = db.Column(db.String(80), default="")
    road_name = db.Column(db.String(120), default="")
    delay_reason = db.Column(db.Text)
    delay_reason_posted_at = db.Column(db.DateTime)
    unexplained_delay = db.Column(db.Boolean, default=False)
    completion_note = db.Column(db.Text)
    conflict_flagged = db.Column(db.Boolean, default=False)
    created_by = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    approved_by = db.Column(db.Integer, db.ForeignKey("users.id"))
    approved_at = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, default=utcnow)
    updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow)

    creator = db.relationship("User", foreign_keys=[created_by])
    approver = db.relationship("User", foreign_keys=[approved_by])
    photos = db.relationship("ProjectPhoto", backref="project", lazy=True)


class ProjectPhoto(db.Model):
    __tablename__ = "project_photos"
    id = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(db.Integer, db.ForeignKey("projects.id"), nullable=False)
    photo_url = db.Column(db.String(400), nullable=False)
    caption = db.Column(db.String(300), default="")
    kind = db.Column(db.String(20), default="general")  # general | completion
    uploaded_by = db.Column(db.Integer, db.ForeignKey("users.id"))
    created_at = db.Column(db.DateTime, default=utcnow)


class OfficialPost(db.Model):
    __tablename__ = "official_posts"
    id = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(db.Integer, db.ForeignKey("projects.id"))
    department_id = db.Column(db.Integer, db.ForeignKey("departments.id"), nullable=False)
    employee_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)  # never shown publicly
    title = db.Column(db.String(200), nullable=False)
    body = db.Column(db.Text, nullable=False)
    status = db.Column(db.String(30), default="draft")
    submitted_at = db.Column(db.DateTime)
    approved_by = db.Column(db.Integer, db.ForeignKey("users.id"))
    approved_at = db.Column(db.DateTime)
    rejection_reason = db.Column(db.Text)
    published_at = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, default=utcnow)

    employee = db.relationship("User", foreign_keys=[employee_id])
    approver = db.relationship("User", foreign_keys=[approved_by])
    department = db.relationship("Department")
    project = db.relationship("Project")
    attachments = db.relationship("OfficialPostAttachment", backref="post", lazy=True)


class OfficialPostAttachment(db.Model):
    __tablename__ = "official_post_attachments"
    id = db.Column(db.Integer, primary_key=True)
    post_id = db.Column(db.Integer, db.ForeignKey("official_posts.id"), nullable=False)
    file_url = db.Column(db.String(400), nullable=False)
    file_type = db.Column(db.String(60), default="image")
    created_at = db.Column(db.DateTime, default=utcnow)


class CitizenPost(db.Model):
    __tablename__ = "citizen_posts"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    body = db.Column(db.Text, nullable=False)
    photo_url = db.Column(db.String(400))
    latitude = db.Column(db.Float)
    longitude = db.Column(db.Float)
    address = db.Column(db.String(300), default="")
    category = db.Column(db.String(60), default="other")
    linked_project_id = db.Column(db.Integer, db.ForeignKey("projects.id"))
    status = db.Column(db.String(30), default="published")
    flag_count = db.Column(db.Integer, default=0)
    flagged_by = db.Column(db.JSON, default=list)  # user ids that flagged
    removal_reason = db.Column(db.Text)
    duplicate_suggested_post_id = db.Column(db.Integer, db.ForeignKey("citizen_posts.id"))
    created_at = db.Column(db.DateTime, default=utcnow)
    updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow)

    user = db.relationship("User", foreign_keys=[user_id])
    linked_project = db.relationship("Project")


class OfficialResponse(db.Model):
    __tablename__ = "official_responses"
    id = db.Column(db.Integer, primary_key=True)
    citizen_post_id = db.Column(db.Integer, db.ForeignKey("citizen_posts.id"), nullable=False)
    employee_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    department_id = db.Column(db.Integer, db.ForeignKey("departments.id"), nullable=False)
    body = db.Column(db.Text, nullable=False)
    status = db.Column(db.String(30), default="pending_chairman")
    approved_by = db.Column(db.Integer, db.ForeignKey("users.id"))
    approved_at = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, default=utcnow)

    citizen_post = db.relationship("CitizenPost", backref=db.backref("responses", lazy=True))
    employee = db.relationship("User", foreign_keys=[employee_id])
    approver = db.relationship("User", foreign_keys=[approved_by])
    department = db.relationship("Department")


class CoordinationMessage(db.Model):
    __tablename__ = "coordination_messages"
    id = db.Column(db.Integer, primary_key=True)
    # from_department_id is NULL for system-generated messages (conflict_alert),
    # Chairman statements (decision) and joint-schedule notices — see services.detect_conflicts
    # and views_chairman.statement. Employee department messages always set it.
    from_department_id = db.Column(db.Integer, db.ForeignKey("departments.id"), nullable=True)
    to_department_ids = db.Column(db.JSON, default=list)  # multiple recipients
    project_id = db.Column(db.Integer, db.ForeignKey("projects.id"))
    thread_id = db.Column(db.Integer, db.ForeignKey("coordination_messages.id"))
    message_type = db.Column(db.String(30), nullable=False)
    body = db.Column(db.Text, nullable=False)
    proposed_dates = db.Column(db.String(200), default="")
    location = db.Column(db.String(300), default="")
    status = db.Column(db.String(30), default="open")
    created_by = db.Column(db.Integer, db.ForeignKey("users.id"))  # internal only, never public
    created_at = db.Column(db.DateTime, default=utcnow)
    # messages are permanent and cannot be deleted; corrections come as new messages

    from_department = db.relationship("Department", foreign_keys=[from_department_id])
    project = db.relationship("Project")


class Conflict(db.Model):
    __tablename__ = "conflicts"
    id = db.Column(db.Integer, primary_key=True)
    project_a_id = db.Column(db.Integer, db.ForeignKey("projects.id"), nullable=False)
    project_b_id = db.Column(db.Integer, db.ForeignKey("projects.id"), nullable=False)
    distance_meters = db.Column(db.Float)
    date_overlap = db.Column(db.Boolean, default=False)
    severity = db.Column(db.String(10))  # high | medium | low
    status = db.Column(db.String(20), default="open")  # open | resolved | escalated
    resolution = db.Column(db.Text)
    resolved_by = db.Column(db.Integer, db.ForeignKey("users.id"))
    resolved_at = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, default=utcnow)

    project_a = db.relationship("Project", foreign_keys=[project_a_id])
    project_b = db.relationship("Project", foreign_keys=[project_b_id])


class JointSchedule(db.Model):
    __tablename__ = "joint_schedules"
    id = db.Column(db.Integer, primary_key=True)
    project_ids = db.Column(db.JSON, default=list)
    department_ids = db.Column(db.JSON, default=list)
    location = db.Column(db.String(300), nullable=False)
    approved_start_date = db.Column(db.Date)
    approved_end_date = db.Column(db.Date)
    reason = db.Column(db.Text, default="")
    status = db.Column(db.String(20), default="proposed")  # proposed|pending_chairman|approved|rejected|completed
    approved_by = db.Column(db.Integer, db.ForeignKey("users.id"))
    approved_at = db.Column(db.DateTime)
    rejection_reason = db.Column(db.Text)
    no_show_departments = db.Column(db.JSON, default=list)  # logged + visible if a dept fails to show
    created_at = db.Column(db.DateTime, default=utcnow)


class Subscription(db.Model):
    __tablename__ = "subscriptions"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    ward = db.Column(db.String(80), default="")
    category = db.Column(db.String(60), default="")
    created_at = db.Column(db.DateTime, default=utcnow)


class Notification(db.Model):
    __tablename__ = "notifications"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    title = db.Column(db.String(200))
    body = db.Column(db.Text, default="")
    link = db.Column(db.String(300), default="")
    read = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=utcnow)


class AuditLog(db.Model):
    __tablename__ = "audit_logs"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    role = db.Column(db.String(20), default="system")
    action = db.Column(db.String(120), nullable=False)
    target_type = db.Column(db.String(60), default="")
    target_id = db.Column(db.Integer)
    metadata_json = db.Column(db.JSON, default=dict)
    ip_address = db.Column(db.String(60), default="")
    user_agent = db.Column(db.String(300), default="")
    created_at = db.Column(db.DateTime, default=utcnow, index=True)

    # Immutability is enforced at the application layer (rules 19/20/25):
    # rows are insert-only; no update/delete endpoints exist for this table.
