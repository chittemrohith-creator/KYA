"""CivicSync services — business rules, conflict detection, approvals, notifications, audit."""
import hashlib
import math
import random
import re
from datetime import datetime, timedelta, timezone

import bcrypt

from .models import (
    db, utcnow,
    ROLE_CITIZEN, ROLE_EMPLOYEE, ROLE_DEPT_HEAD, ROLE_CHAIRMAN, ROLE_ADMIN,
    USER_STATUS_PENDING, USER_STATUS_ACTIVE, USER_STATUS_REJECTED, USER_STATUS_SUSPENDED,
    Department, User, Project, ProjectPhoto, OfficialPost, CitizenPost, OfficialResponse,
    CoordinationMessage, Conflict, JointSchedule, Subscription, Notification, AuditLog,
)

PHONE_REGEX = re.compile(r"^\+?\d{7,15}$")

# categories considered "road-related" or "underground utilities" for conflict rule 4
ROAD_CATEGORIES = {"road", "asphalt", "relaying", "paving"}
UNDERGROUND_CATEGORIES = {"water", "drainage", "sewer", "telecom", "fiber", "electricity", "gas", "underground_utility"}


# ------------------------------------------------------------------ identity helpers
def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt(rounds=10)).decode("utf-8")


def check_password(password: str, hashed: str) -> bool:
    if not hashed:
        return False
    return bcrypt.checkpw(password.encode("utf-8"), hashed.encode("utf-8"))


def encrypt_phone(phone: str, secret: str) -> str:
    """Encrypted-at-rest placeholder using keyed HMAC stream (demo-grade).

    Production would use AES-GCM via a KMS. Phone is never stored in plaintext
    and never shown publicly (rules 10, 25 / privacy section).
    """
    key = hashlib.sha256(secret.encode()).digest()
    out = bytes(b ^ key[i % len(key)] for i, b in enumerate(phone.encode()))
    return "enc:" + out.hex()


def decrypt_phone(token: str, secret: str) -> str:
    if not token or not token.startswith("enc:"):
        return ""
    key = hashlib.sha256(secret.encode()).digest()
    raw = bytes.fromhex(token[4:])
    return bytes(b ^ key[i % len(key)] for i, b in enumerate(raw)).decode()


def hash_phone(phone: str, secret: str) -> str:
    return hashlib.sha256(f"{secret}:{phone}".encode()).hexdigest()


def public_badge_for(user_or_dept_name, role=None):
    """Public display strings exactly per spec section 7."""
    if role == ROLE_CHAIRMAN:
        return "🏛️ Municipal Chairman — Official Statement"
    if role in (ROLE_EMPLOYEE, ROLE_DEPT_HEAD):
        return f"✅ Verified Official — {user_or_dept_name}"
    return "👤 Citizen — Phone Verified"


# ------------------------------------------------------------------ audit log (insert-only)
def audit(action, user=None, target_type="", target_id=None, metadata=None, request=None):
    ip = request.remote_addr if request is not None else ""
    ua = request.headers.get("User-Agent", "")[:300] if request is not None else ""
    entry = AuditLog(
        user_id=user.id if user else None,
        role=user.role if user else "system",
        action=action,
        target_type=target_type,
        target_id=target_id,
        metadata_json=metadata or {},
        ip_address=ip or "",
        user_agent=ua,
    )
    db.session.add(entry)
    return entry


# ------------------------------------------------------------------ notifications
def notify_user(user, title, body="", link=""):
    if user is None:
        return None
    n = Notification(user_id=user.id, title=title, body=body, link=link)
    db.session.add(n)
    return n


def notify_role(role, title, body="", link="", department_id=None):
    users = User.query.filter_by(role=role, status=USER_STATUS_ACTIVE).all()
    if department_id is not None:
        users = [u for u in users if u.department_id == department_id]
    for u in users:
        notify_user(u, title, body, link)
    return users


def notify_chairman(title, body="", link=""):
    return notify_role(ROLE_CHAIRMAN, title, body, link)


def notify_department_subscribers(ward, category, title, body, link):
    subs = Subscription.query.all()
    notified = []
    for s in subs:
        if (not s.ward or s.ward == ward) and (not s.category or s.category == category):
            u = db.session.get(User, s.user_id)
            if u and u.status == USER_STATUS_ACTIVE:
                notify_user(u, title, body, link)
                notified.append(u.id)
    return notified


# ------------------------------------------------------------------ project approval chain
PROJECT_ALLOWED_TRANSITIONS = {
    "draft": {"pending_chairman", "cancelled"},
    "pending_chairman": {"approved", "rejected", "draft"},
    "approved": {"in_progress", "cancelled", "delayed"},
    "in_progress": {"completed", "delayed", "cancelled"},
    "delayed": {"in_progress", "completed", "cancelled"},
    "completed": {"verified_complete"},
    "rejected": set(),
    "cancelled": set(),
    "verified_complete": set(),
}


class BusinessRuleError(Exception):
    def __init__(self, message, code=400):
        super().__init__(message)
        self.code = code


def submit_project(project, employee, request=None):
    """draft -> pending_chairman (rule: only approved employees; chairman approves)."""
    if employee.role not in (ROLE_EMPLOYEE, ROLE_DEPT_HEAD) or employee.status != USER_STATUS_ACTIVE:
        raise BusinessRuleError("Only verified active employees can submit projects.")
    if project.status != "draft":
        raise BusinessRuleError(f"Project cannot be submitted from state '{project.status}'.")
    project.status = "pending_chairman"
    detect_conflicts(project)
    notify_chairman("New project pending approval",
                    f"'{project.title}' ({project.department.name}) awaiting your review.",
                    link=f"/chairman/projects")
    audit("project.submitted", employee, "project", project.id, request=request)
    return project


def approve_project(project, chairman, request=None):
    if chairman.role != ROLE_CHAIRMAN or chairman.status != USER_STATUS_ACTIVE:
        raise BusinessRuleError("Only the Municipal Chairman approves projects.")
    if chairman.id == project.created_by:
        raise BusinessRuleError("Chairman cannot approve their own project (rule 24 applies to all approvers).")
    if project.status != "pending_chairman":
        raise BusinessRuleError(f"Project cannot be approved from state '{project.status}'.")
    project.status = "approved"
    project.approved_by = chairman.id
    project.approved_at = utcnow()
    notify_user(db.session.get(User, project.created_by), "Project approved",
                f"'{project.title}' is now public.", link=f"/projects/{project.id}")
    notify_department_subscribers(project.ward, project.category,
                                  "New project in your ward",
                                  f"{project.department.name}: '{project.title}'",
                                  link=f"/projects/{project.id}")
    audit("project.approved", chairman, "project", project.id, request=request)
    return project


def reject_project(project, chairman, reason, request=None):
    if chairman.role != ROLE_CHAIRMAN:
        raise BusinessRuleError("Only the Municipal Chairman rejects projects.")
    if not reason:
        raise BusinessRuleError("All rejections must include a reason (rule 28).")
    if project.status != "pending_chairman":
        raise BusinessRuleError(f"Project cannot be rejected from state '{project.status}'.")
    project.status = "rejected"
    meta = {"rejection_reason": reason}
    notify_user(db.session.get(User, project.created_by),
                "Project rejected", f"'{project.title}' — Reason: {reason}",
                link=f"/workspace/projects/{project.id}")
    audit("project.rejected", chairman, "project", project.id, metadata=meta, request=request)
    return project


def start_project(project, employee, request=None):
    if project.status != "approved":
        raise BusinessRuleError("Projects cannot move to in_progress without Chairman approval (lifecycle rule 4).")
    project.status = "in_progress"
    project.actual_start_date = project.actual_start_date or utcnow().date()
    audit("project.started", employee, "project", project.id, request=request)
    return project


def complete_project(project, employee, note, request=None):
    """Rule 5: progress=100%, >=1 completion photo, completion note.
    Rule 7: delayed projects need a delay reason before completing."""
    if project.status not in ("in_progress", "delayed"):
        raise BusinessRuleError(f"Cannot mark completed from state '{project.status}'.")
    if project.progress_percentage != 100:
        raise BusinessRuleError("Cannot mark completed without progress = 100% (rule 5).")
    has_completion_photo = ProjectPhoto.query.filter_by(project_id=project.id, kind="completion").count() >= 1
    if not has_completion_photo:
        raise BusinessRuleError("Cannot mark completed without at least one completion photo (rule 5).")
    if not note:
        raise BusinessRuleError("Cannot mark completed without a completion note (rule 5).")
    if project.status == "delayed" and not project.delay_reason:
        raise BusinessRuleError("Delayed projects require a published delay reason before completion (rule 7/18).")
    project.status = "completed"
    project.completion_note = note
    project.actual_end_date = utcnow().date()
    notify_user(db.session.get(User, project.created_by), "Project completed",
                f"'{project.title}' marked completed.", link=f"/projects/{project.id}")
    notify_department_subscribers(project.ward, project.category,
                                  "Project completed", f"'{project.title}' is complete.",
                                  link=f"/projects/{project.id}")
    audit("project.completed", employee, "project", project.id, metadata={"note": note}, request=request)
    return project


def verify_complete_project(project, chairman, request=None):
    if chairman.role != ROLE_CHAIRMAN:
        raise BusinessRuleError("Only the Chairman verifies completion.")
    if project.status != "completed":
        raise BusinessRuleError("Only completed projects can be verified.")
    project.status = "verified_complete"
    audit("project.verified_complete", chairman, "project", project.id, request=request)
    return project


def post_delay_reason(project, employee, reason, request=None):
    """Section 20: dept posts the delay reason (as an official record, permanent)."""
    if project.status != "delayed":
        raise BusinessRuleError("Delay reasons are posted for delayed projects.")
    project.delay_reason = reason
    project.delay_reason_posted_at = utcnow()
    project.unexplained_delay = False
    p = OfficialPost(project_id=project.id, department_id=project.department_id,
                     employee_id=employee.id, title=f"Delay reason — {project.title}",
                     body=reason, status="pending_chairman", submitted_at=utcnow())
    db.session.add(p)
    db.session.flush()
    notify_chairman("Delay reason pending approval",
                    f"'{project.title}': {reason[:120]}", link="/chairman/posts")
    audit("project.delay_reason_posted", employee, "project", project.id,
          metadata={"reason": reason, "post_id": p.id}, request=request)
    return p


def run_delay_sweep(now=None):
    """Nightly job: auto-delay past-due projects; unexplained after 48h without reason."""
    now = now or utcnow()
    today = now.date()
    swept = []
    due_soon = []
    for prj in Project.query.filter(Project.status.in_(["approved", "in_progress"])).all():
        if prj.end_date < today:
            prj.status = "delayed"
            swept.append(prj.id)
            audit("project.auto_delayed", None, "project", prj.id,
                  metadata={"end_date": str(prj.end_date)})
            notify_department_subscribers(prj.ward, prj.category, "Project delayed",
                                          f"'{prj.title}' is delayed — ⚠️ Delayed — Reason Pending",
                                          link=f"/projects/{prj.id}")
        elif prj.end_date and (prj.end_date - today).days <= 3 and prj.status == "in_progress":
            due_soon.append(prj.id)
            creator = db.session.get(User, prj.created_by)
            notify_user(creator, "Deadline approaching",
                        f"'{prj.title}' ends on {prj.end_date}.", link=f"/workspace/projects/{prj.id}")
    for prj in Project.query.filter_by(status="delayed", unexplained_delay=False).all():
        deadline = prj.end_date + timedelta(hours=48)
        posted = prj.delay_reason_posted_at and prj.delay_reason_posted_at.date() <= deadline.date()
        if not posted and now.date() > deadline.date():
            prj.unexplained_delay = True
            notify_chairman("Unexplained delay",
                            f"'{prj.title}' ({prj.department.name}) passed the 48h window with no reason.",
                            link=f"/chairman/projects")
            audit("project.unexplained_delay", None, "project", prj.id)
    # repeated delays this quarter -> flag department
    flagged = []
    q_start = _quarter_start(now.date())
    for dept in Department.query.all():
        cnt = Project.query.filter(
            Project.department_id == dept.id,
            Project.status.in_(["delayed"]),
            Project.updated_at >= datetime.combine(q_start, datetime.min.time()),
        ).count()
        if cnt >= 3:
            flagged.append(dept.id)
            notify_chairman("Repeated delays",
                            f"{dept.name} has {cnt} delayed projects this quarter.",
                            link=f"/chairman/departments")
    return {"delayed": swept, "due_soon": due_soon, "repeat_offender_departments": flagged}


def _quarter_start(d):
    return datetime(d.year, 3 * ((d.month - 1) // 3) + 1, 1).date()


# ------------------------------------------------------------------ official posts
def submit_post(post, employee, request=None):
    if employee.status != USER_STATUS_ACTIVE or employee.role not in (ROLE_EMPLOYEE, ROLE_DEPT_HEAD):
        raise BusinessRuleError("Only approved employees can submit official posts (rule 1).")
    if post.status not in ("draft", "rejected"):
        raise BusinessRuleError("Only drafts or rejected posts can be submitted.")
    post.status = "pending_chairman"
    post.submitted_at = utcnow()
    post.rejection_reason = None
    notify_chairman("New official post pending", f"'{post.title}' ({post.department.name})",
                    link="/chairman/posts")
    audit("post.submitted", employee, "official_post", post.id, request=request)
    return post


def approve_post(post, chairman, request=None):
    if chairman.role != ROLE_CHAIRMAN:
        raise BusinessRuleError("Only the Chairman approves official posts (rule 5).")
    if chairman.id == post.employee_id:
        raise BusinessRuleError("Employees cannot approve their own posts (rule 24).")
    if post.status != "pending_chairman":
        raise BusinessRuleError("Only pending posts can be approved.")
    post.status = "published"
    post.approved_by = chairman.id
    post.approved_at = utcnow()
    post.published_at = utcnow()
    notify_user(db.session.get(User, post.employee_id), "Post published",
                f"'{post.title}' is now public.", link=f"/projects/{post.project_id}" if post.project_id else "/departments/" + post.department.slug)
    audit("post.approved_published", chairman, "official_post", post.id, request=request)
    return post


def reject_post(post, chairman, reason, request=None):
    if chairman.role != ROLE_CHAIRMAN:
        raise BusinessRuleError("Only the Chairman rejects official posts.")
    if not reason:
        raise BusinessRuleError("All rejections must include a reason (rule 28).")
    if post.status != "pending_chairman":
        raise BusinessRuleError("Only pending posts can be rejected.")
    post.status = "rejected"
    post.rejection_reason = reason
    notify_user(db.session.get(User, post.employee_id), "Post rejected",
                f"'{post.title}' — Reason: {reason}", link=f"/workspace/posts/{post.id}")
    audit("post.rejected", chairman, "official_post", post.id,
          metadata={"reason": reason}, request=request)
    return post


def edit_published_post_guard(post):
    """Rule 21: published posts cannot be edited; corrections are new posts."""
    if post.status == "published":
        raise BusinessRuleError("Published official posts cannot be edited. Post a correction referencing this post.")


# ------------------------------------------------------------------ citizen posts
EDIT_WINDOW_HOURS = 24
FLAG_THRESHOLD = 5


def create_citizen_post(user, body, **kwargs):
    if user.role != ROLE_CITIZEN or not user.phone_verified:
        raise BusinessRuleError("Citizens must verify phone before posting (rule 8).")
    cp = CitizenPost(user_id=user.id, body=body, **kwargs)
    db.session.add(cp)
    db.session.flush()
    audit("citizen_post.created", user, "citizen_post", cp.id)
    return cp


def edit_citizen_post(post, user, new_body=None, new_photo=None):
    if post.user_id != user.id:
        raise BusinessRuleError("You can only edit your own report.")
    age = utcnow() - post.created_at.replace(tzinfo=timezone.utc) if post.created_at.tzinfo is None else utcnow() - post.created_at
    if age > timedelta(hours=EDIT_WINDOW_HOURS):
        raise BusinessRuleError("Reports are permanent after 24 hours (rule 22).")
    if new_body:
        post.body = new_body
    if new_photo:
        post.photo_url = new_photo
    audit("citizen_post.edited", user, "citizen_post", post.id)
    return post


def delete_citizen_post(post, user):
    if post.user_id != user.id:
        raise BusinessRuleError("You can only delete your own report.")
    age = utcnow() - post.created_at.replace(tzinfo=timezone.utc) if post.created_at.tzinfo is None else utcnow() - post.created_at
    if age > timedelta(hours=EDIT_WINDOW_HOURS):
        raise BusinessRuleError("Reports are permanent after 24 hours (rule 22).")
    post.status = "removed"
    audit("citizen_post.deleted_by_author", user, "citizen_post", post.id)
    return post


def flag_citizen_post(post, user):
    if post.user_id == user.id:
        raise BusinessRuleError("You cannot flag your own report.")
    flags = list(post.flagged_by or [])
    if user.id in flags:
        return post  # idempotent
    flags.append(user.id)
    post.flagged_by = flags
    post.flag_count = len(flags)
    if post.flag_count >= FLAG_THRESHOLD and post.status == "published":
        post.status = "under_review"
        audit("citizen_post.under_review", None, "citizen_post", post.id,
              metadata={"flag_count": post.flag_count})
    audit("citizen_post.flagged", user, "citizen_post", post.id)
    return post


def remove_citizen_post(post, chairman, reason):
    if chairman.role not in (ROLE_CHAIRMAN, ROLE_ADMIN):
        raise BusinessRuleError("Only Chairman can remove reports with reason.")
    if not reason:
        raise BusinessRuleError("Removal requires a reason (rule 28).")
    post.status = "removed"
    post.removal_reason = reason
    audit("citizen_post.removed", chairman, "citizen_post", post.id, metadata={"reason": reason})
    return post


def find_duplicate_reports(lat, lon, category, radius_m=150, window_days=7):
    """Edge case: duplicate citizen reports — system suggests linking."""
    cutoff = utcnow() - timedelta(days=window_days)
    candidates = []
    for cp in CitizenPost.query.filter(CitizenPost.status == "published",
                                       CitizenPost.created_at >= cutoff).all():
        if cp.latitude is None or cp.category != category:
            continue
        if haversine_meters(lat, lon, cp.latitude, cp.longitude) <= radius_m:
            candidates.append(cp)
    return candidates


def respond_to_report(response_body, employee, citizen_post, request=None):
    if employee.status != USER_STATUS_ACTIVE or employee.role not in (ROLE_EMPLOYEE, ROLE_DEPT_HEAD):
        raise BusinessRuleError("Only verified officials can post official responses.")
    r = OfficialResponse(citizen_post_id=citizen_post.id, employee_id=employee.id,
                         department_id=employee.department_id, body=response_body,
                         status="pending_chairman")
    db.session.add(r)
    db.session.flush()
    notify_chairman("Official response pending approval",
                    f"Response to report #{citizen_post.id}",
                    link="/chairman/posts")
    audit("official_response.submitted", employee, "official_response", r.id, request=request)
    return r


def approve_response(resp, chairman, request=None):
    if chairman.role != ROLE_CHAIRMAN:
        raise BusinessRuleError("Only the Chairman approves official responses.")
    resp.status = "approved"
    resp.approved_by = chairman.id
    resp.approved_at = utcnow()
    cp = db.session.get(CitizenPost, resp.citizen_post_id)
    owner = db.session.get(User, cp.user_id)
    notify_user(owner, "Official response to your report",
                resp.body[:200], link=f"/citizen-reports/{cp.id}")
    audit("official_response.approved", chairman, "official_response", resp.id, request=request)
    return resp


# ------------------------------------------------------------------ coordination hub
def post_coordination_message(from_dept, to_dept_ids, message_type, body, employee,
                              project_id=None, proposed_dates="", location="", request=None):
    if employee.status != USER_STATUS_ACTIVE or employee.role not in (ROLE_EMPLOYEE, ROLE_DEPT_HEAD):
        raise BusinessRuleError("Only approved employees can post in the Coordination Hub (rule 3).")
    if employee.department_id != from_dept.id:
        raise BusinessRuleError("Employees post on behalf of their own department.")
    if message_type not in MESSAGE_TYPES:
        raise BusinessRuleError(f"Invalid message type '{message_type}'.")
    m = CoordinationMessage(from_department_id=from_dept.id, to_department_ids=list(to_dept_ids),
                            project_id=project_id, message_type=message_type, body=body,
                            proposed_dates=proposed_dates, location=location, created_by=employee.id)
    db.session.add(m)
    db.session.flush()
    for did in to_dept_ids:
        heads = User.query.filter_by(department_id=did, status=USER_STATUS_ACTIVE).filter(
            User.role.in_([ROLE_EMPLOYEE, ROLE_DEPT_HEAD])).all()
        for h in heads:
            notify_user(h, f"New coordination message from {from_dept.name}", body[:160],
                        link="/coordination")
    audit("coordination.message", employee, "coordination_message", m.id,
          metadata={"type": message_type}, request=request)
    return m


def escalate_to_chairman(message, employee, request=None):
    esc = post_coordination_message(
        db.session.get(Department, message.from_department_id), [],
        "escalation",
        f"Escalating thread #{message.thread_id or message.id}: no resolution within 72h.",
        employee, project_id=message.project_id, request=request)
    notify_chairman("Coordination escalation", esc.body, link="/coordination")
    return esc


# ------------------------------------------------------------------ conflict detection (section 14)
CONFLICT_RADIUS_M = 200
ADJACENCY_DAYS = 7


def haversine_meters(lat1, lon1, lat2, lon2):
    R = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


def categories_related(a, b):
    """Condition 4: same/adjacent category (both road-related or both underground utilities)."""
    if a == b:
        return True
    sa, sb = (a or "").lower(), (b or "").lower()
    group_a = "road" if sa in ROAD_CATEGORIES else "ug" if sa in UNDERGROUND_CATEGORIES or sa in {"water", "drainage", "telecom", "electricity", "sewer", "fiber", "gas"} else None
    group_b = "road" if sb in ROAD_CATEGORIES else "ug" if sb in UNDERGROUND_CATEGORIES or sb in {"water", "drainage", "telecom", "electricity", "sewer", "fiber", "gas"} else None
    return group_a is not None and group_a == group_b


def dates_overlap(a_start, a_end, b_start, b_end):
    return a_start <= b_end and a_end >= b_start


def days_between_ranges(a_start, a_end, b_start, b_end):
    """Gap in days between two ranges (0 if overlapping)."""
    if dates_overlap(a_start, a_end, b_start, b_end):
        return 0
    if a_end < b_start:
        return (b_start - a_end).days
    return (a_start - b_end).days


def detect_conflicts(project, now=None):
    """Runs on creation/date-location change/submission. Returns new conflicts."""
    found = []
    active_statuses = ("pending_chairman", "approved", "in_progress", "delayed")
    others = Project.query.filter(Project.id != project.id,
                                  Project.status.in_(active_statuses)).all()
    for other in others:
        # condition 3: different departments
        if other.department_id == project.department_id:
            continue
        dist = haversine_meters(project.latitude, project.longitude,
                                other.latitude, other.longitude)
        spatial = dist <= CONFLICT_RADIUS_M
        temporal = dates_overlap(project.start_date, project.end_date,
                                 other.start_date, other.end_date)
        adjacent = (not temporal) and days_between_ranges(
            project.start_date, project.end_date, other.start_date, other.end_date) <= ADJACENCY_DAYS
        related = categories_related(project.category, other.category)
        if not (spatial and (temporal or adjacent) and related):
            continue
        same_pair = Conflict.query.filter(
            ((Conflict.project_a_id == project.id) & (Conflict.project_b_id == other.id)) |
            ((Conflict.project_a_id == other.id) & (Conflict.project_b_id == project.id)),
            Conflict.status == "open").first()
        if same_pair:
            continue
        severity = "low"
        if temporal:
            same_road = (project.road_name or "").strip().lower() == (other.road_name or "").strip().lower() and project.road_name
            severity = "high" if same_road else "medium"
        c = Conflict(project_a_id=project.id, project_b_id=other.id,
                     distance_meters=round(dist, 1), date_overlap=temporal,
                     severity=severity)
        db.session.add(c)
        db.session.flush()
        project.conflict_flagged = True
        other.conflict_flagged = True
        # auto-post conflict_alert in Hub (section 14 step 2) — system generated
        alert = CoordinationMessage(
            from_department_id=None, to_department_ids=[project.department_id, other.department_id],
            project_id=project.id, message_type="conflict_alert",
            body=(f"⚠️ Conflict: {project.department.name} '{project.title}' and "
                  f"{other.department.name} '{other.title}' overlap near "
                  f"{project.address or project.road_name or 'the same stretch'} "
                  f"({c.distance_meters}m apart). Suggest joint work."),
            location=project.address or project.road_name, status="open")
        db.session.add(alert)
        db.session.flush()
        notify_chairman("Conflict detected",
                        f"{severity.upper()} conflict between '{project.title}' and '{other.title}'.",
                        link="/chairman/conflicts")
        audit("conflict.detected", None, "conflict", c.id,
              metadata={"severity": severity, "distance_m": c.distance_meters,
                        "projects": [project.id, other.id]})
        found.append(c)
    return found


def resolve_conflict(conflict, resolver, resolution_text, request=None):
    conflict.status = "resolved"
    conflict.resolution = resolution_text
    conflict.resolved_by = resolver.id
    conflict.resolved_at = utcnow()
    for pid in (conflict.project_a_id, conflict.project_b_id):
        p = db.session.get(Project, pid)
        if p:
            open_others = Conflict.query.filter(
                Conflict.status == "open",
                (Conflict.project_a_id == pid) | (Conflict.project_b_id == pid)).count()
            if open_others == 0:
                p.conflict_flagged = False
    audit("conflict.resolved", resolver, "conflict", conflict.id,
          metadata={"resolution": resolution_text}, request=request)
    return conflict


def unresolved_conflict_check(now=None):
    """If unresolved 72h -> prompt Chairman (section 14 step 6, rule 15)."""
    now = now or utcnow()
    stale = Conflict.query.filter_by(status="open").all()
    prompted = []
    for c in stale:
        created = c.created_at.replace(tzinfo=timezone.utc) if c.created_at.tzinfo is None else c.created_at
        if now - created >= timedelta(hours=72):
            c.status = "escalated"
            notify_chairman("Conflict unresolved for 72h",
                            f"Conflict #{c.id} ('{c.project_a.title}' vs '{c.project_b.title}') needs a decision.",
                            link="/chairman/conflicts")
            audit("conflict.escalated_72h", None, "conflict", c.id)
            prompted.append(c.id)
    return prompted


# ------------------------------------------------------------------ joint scheduling (section 15)
def propose_joint_schedule(employee, project_ids, location, start_date, end_date, reason, request=None):
    depts = set()
    for pid in project_ids:
        p = db.session.get(Project, pid)
        if not p:
            raise BusinessRuleError(f"Project {pid} not found.")
        if p.department_id != employee.department_id:
            raise BusinessRuleError("Proposer's projects must belong to their department.")
        depts.add(p.department_id)
    if len(project_ids) < 2 or len(depts) < 2:
        raise BusinessRuleError("Joint schedules need 2+ departments and 2+ projects (rules 1-2).")
    pts = [db.session.get(Project, pid) for pid in project_ids]
    for a, b in zip(pts, pts[1:]):
        if haversine_meters(a.latitude, a.longitude, b.latitude, b.longitude) > CONFLICT_RADIUS_M:
            raise BusinessRuleError("Projects must share a common location (within 200m) (rule 3).")
    js = JointSchedule(project_ids=list(project_ids), department_ids=sorted(depts),
                       location=location, approved_start_date=start_date,
                       approved_end_date=end_date, reason=reason, status="pending_chairman")
    db.session.add(js)
    db.session.flush()
    notify_chairman("Joint schedule pending approval",
                    f"{location}: {'+'.join(db.session.get(Department, d).name for d in js.department_ids)}",
                    link="/chairman/schedules")
    audit("joint_schedule.proposed", employee, "joint_schedule", js.id, request=request)
    return js


def approve_joint_schedule(js, chairman, request=None):
    if chairman.role != ROLE_CHAIRMAN:
        raise BusinessRuleError("Only the Chairman approves joint schedules (rule 7).")
    if js.status != "pending_chairman":
        raise BusinessRuleError("Only pending joint schedules can be approved.")
    js.status = "approved"
    js.approved_by = chairman.id
    js.approved_at = utcnow()
    for pid in js.project_ids:
        p = db.session.get(Project, pid)
        if p:
            p.conflict_flagged = False
            open_c = Conflict.query.filter(
                Conflict.status == "open",
                (Conflict.project_a_id == pid) | (Conflict.project_b_id == pid)).all()
            for c in open_c:
                resolve_conflict(c, chairman, f"Resolved via joint schedule #{js.id}: {js.reason}")
    msg = CoordinationMessage(from_department_id=None, to_department_ids=js.department_ids,
                              message_type="decision",
                              body=(f"🤝 Coordinated Work — {js.location}\n"
                                    f"Dates: {js.approved_start_date} to {js.approved_end_date}\n"
                                    f"Reason: {js.reason}\nStatus: Approved by Municipal Chairman"),
                              location=js.location, status="approved")
    db.session.add(msg)
    for pid in js.project_ids:
        p = db.session.get(Project, pid)
        if p:
            notify_department_subscribers(p.ward, p.category, "Joint work approved nearby",
                                          f"Coordinated work at {js.location} on {js.approved_start_date}.",
                                          link=f"/joint-schedules/{js.id}")
    audit("joint_schedule.approved", chairman, "joint_schedule", js.id, request=request)
    return js


def reject_joint_schedule(js, chairman, reason, request=None):
    if chairman.role != ROLE_CHAIRMAN:
        raise BusinessRuleError("Only the Chairman rejects joint schedules.")
    if not reason:
        raise BusinessRuleError("Rejections require a reason (rule 28).")
    js.status = "rejected"
    js.rejection_reason = reason
    audit("joint_schedule.rejected", chairman, "joint_schedule", js.id,
          metadata={"reason": reason}, request=request)
    return js


def log_no_show(js, department_id):
    """Rule 8: if a department fails to show up, it is logged and visible."""
    nos = list(js.no_show_departments or [])
    if department_id not in nos:
        nos.append(department_id)
        js.no_show_departments = nos
        audit("joint_schedule.no_show", None, "joint_schedule", js.id,
              metadata={"department_id": department_id})
    return js


# ------------------------------------------------------------------ employee verification (16.1)
def register_employee(full_name, employee_code, department, designation, email, phone, password, request=None):
    if not all([full_name, employee_code, department, designation, email, phone, password]):
        raise BusinessRuleError("All registration fields are required.")
    if not PHONE_REGEX.match(phone):
        raise BusinessRuleError("Invalid phone number format.")
    if User.query.filter_by(employee_code=employee_code).first():
        raise BusinessRuleError("Employee code already registered.")
    dept = Department.query.filter_by(slug=department).first() or \
        Department.query.filter_by(name=department).first()
    if not dept:
        raise BusinessRuleError(f"Unknown department '{department}'.")
    u = User(role=ROLE_EMPLOYEE, full_name=full_name, employee_code=employee_code,
             department_id=dept.id, designation=designation, email=email,
             password_hash=hash_password(password), status=USER_STATUS_PENDING,
             phone_hash=hash_phone(phone, current_app_secret()),
             phone_encrypted=encrypt_phone(phone, current_app_secret()))
    db.session.add(u)
    db.session.flush()
    notify_chairman("New employee registration",
                    f"{employee_code} — {dept.name} ({designation}) awaits verification.",
                    link="/chairman/employees")
    audit("employee.registered", u, "user", u.id, request=request)
    return u


def approve_employee(target, chairman, request=None):
    if chairman.role != ROLE_CHAIRMAN:
        raise BusinessRuleError("Only the Chairman approves employees (rule 4).")
    if target.status != USER_STATUS_PENDING:
        raise BusinessRuleError("Only pending employees can be approved.")
    target.status = USER_STATUS_ACTIVE
    target.phone_verified = True
    target.approved_by = chairman.id
    target.approved_at = utcnow()
    target.rejection_reason = None
    notify_user(target, "Account approved", "You can now log in to your department workspace.",
                link="/workspace")
    audit("employee.approved", chairman, "user", target.id, request=request)
    return target


def reject_employee(target, chairman, reason, request=None):
    if chairman.role != ROLE_CHAIRMAN:
        raise BusinessRuleError("Only the Chairman rejects employees.")
    if not reason:
        raise BusinessRuleError("Rejections require a reason (rule 28).")
    target.status = USER_STATUS_REJECTED
    target.rejection_reason = reason
    notify_user(target, "Registration rejected", f"Reason: {reason}. You may reapply.", link="/employee/register")
    audit("employee.rejected", chairman, "user", target.id, metadata={"reason": reason}, request=request)
    return target


def suspend_employee(target, chairman, reason, request=None):
    """Edge case: employee leaves — Chairman deactivates; historical posts stay attributed to dept."""
    if chairman.role != ROLE_CHAIRMAN:
        raise BusinessRuleError("Only the Chairman can suspend employees.")
    target.status = USER_STATUS_SUSPENDED
    target.rejection_reason = reason or "Suspended"
    audit("employee.suspended", chairman, "user", target.id, metadata={"reason": reason}, request=request)
    return target


def transfer_chairman(current_chairman, new_chairman, admin, request=None):
    """Edge case: chairman unavailable — Admin temporarily assigns approval authority. Logged."""
    if admin.role != ROLE_ADMIN:
        raise BusinessRuleError("Only the System Admin transfers approval authority.")
    current_chairman.role = ROLE_DEPT_HEAD if current_chairman.department_id else ROLE_ADMIN
    current_chairman.designation = "Former Chairman (temporary transfer)"
    new_chairman.role = ROLE_CHAIRMAN
    audit("chairman.authority_transferred", admin, "user", new_chairman.id,
          metadata={"from": current_chairman.id, "to": new_chairman.id}, request=request)
    return new_chairman


# ------------------------------------------------------------------ citizen signup (8.1)
OTP_TTL_MINUTES = 5
OTP_MAX_ATTEMPTS = 3


def gen_otp():
    return f"{random.SystemRandom().randint(0, 999999):06d}"


def begin_citizen_signup(phone):
    if not PHONE_REGEX.match(phone):
        raise BusinessRuleError("Phone must include country code, e.g. +919876543210.")
    ph = hash_phone(phone, current_app_secret())
    existing = User.query.filter_by(phone_hash=ph).first()
    if existing:
        raise BusinessRuleError("A phone-verified account already exists for this number. Please log in.", code=409)
    otp = gen_otp()
    u = User(role=ROLE_CITIZEN, phone_hash=ph,
             phone_encrypted=encrypt_phone(phone, current_app_secret()),
             status=USER_STATUS_PENDING, phone_verified=False,
             otp_hash=hash_password(otp), otp_expires_at=utcnow() + timedelta(minutes=OTP_TTL_MINUTES),
             otp_attempts=0)
    db.session.add(u)
    db.session.flush()
    audit("citizen.signup_started", u, "user", u.id)
    return u, otp  # OTP returned here only because there is no SMS gateway in demo


def verify_citizen_otp(user, otp, display_name=None):
    if user.otp_expires_at is None or utcnow() > user.otp_expires_at:
        raise BusinessRuleError("OTP expired. Request a new code.")
    if user.otp_attempts >= OTP_MAX_ATTEMPTS:
        raise BusinessRuleError("Too many incorrect attempts. OTP expired.")
    if not check_password(otp, user.otp_hash):
        user.otp_attempts += 1
        db.session.commit()
        raise BusinessRuleError("Incorrect OTP.")
    user.phone_verified = True
    user.status = USER_STATUS_ACTIVE
    user.display_name = display_name or "Anonymous Citizen"
    user.otp_hash = None
    user.otp_expires_at = None
    audit("citizen.phone_verified", user, "user", user.id)
    return user


_current_app_secret = {"v": "dev-secret-change-me"}


def set_app_secret(s):
    _current_app_secret["v"] = s


def current_app_secret():
    return _current_app_secret["v"]


# ------------------------------------------------------------------ contractor accountability (20)
def contractor_stats(contractor_name):
    projects = Project.query.filter(Project.contractor_name == contractor_name).all()
    total = len(projects)
    delayed = sum(1 for p in projects if p.status in ("delayed",) or p.unexplained_delay)
    on_time_completed = sum(1 for p in projects
                            if p.status in ("completed", "verified_complete")
                            and p.actual_end_date and p.actual_end_date <= p.end_date)
    completed = sum(1 for p in projects if p.status in ("completed", "verified_complete"))
    feedback_posts = CitizenPost.query.filter(
        CitizenPost.linked_project_id.in_([p.id for p in projects] or [-1])).all()
    resolved = sum(1 for cp in feedback_posts if cp.status == "resolved")
    score = round(100 * (on_time_completed / completed if completed else 1.0)) 
    return {
        "contractor": contractor_name,
        "projects": total,
        "completed": completed,
        "on_time_rate": round(on_time_completed / completed * 100, 1) if completed else None,
        "delay_history": delayed,
        "citizen_feedback_score": score,
        "linked_reports": len(feedback_posts),
        "reports_resolved": resolved,
    }
