"""Deep workflow integration tests (isolated temp DB; no real SMS ever sent).

Covers spec sections that smoke tests only touched at page level:
  * Joint scheduling (section 15/16.4): proposal validation, Chairman approval
    publishing 🤝 Coordinated Work + resolving conflicts, rejection requiring a
    reason (rule 28), non-Chairman approvers denied (rule 7), audit records.
  * Delay handling (section 20 / lifecycle rules 6-7): run_delay_sweep auto-
    delays past-due approved projects, deadline-soon notification, unexplained
    delay after the 48h window, post_delay_reason flow creating a pending
    official post, delayed->completed requiring a reason.
  * Pending staff account lifecycle (16.1): registration -> pending, login
    blocked, Chairman approve enables login, re-registration of same code
    rejected, audit trail entries.
  * Subscriptions (citizen feature): create/delete own-only, duplicate ignored,
    project approval notifies matching subscribers, privacy (other users'
    subscription pages inaccessible).
"""
from datetime import date, timedelta

import pytest

from app.models import (
    db, Project, JointSchedule, AuditLog, Notification, Subscription, User,
    OfficialPost, Conflict, Department,
)
from app import services
from app.services import BusinessRuleError


def _chairman(app):
    with app.app_context():
        return User.query.filter_by(role="chairman").first()


def _mk_project(app, dept_slug, title, lat, lon, start, end, status="approved", ward=None):
    """Insert a project directly (bypassing draft flow) for sweep/joint tests."""
    with app.app_context():
        dept = Department.query.filter_by(slug=dept_slug).first()
        creator = User.query.filter_by(department_id=dept.id, role="employee").first() \
            or _chairman(app)
        p = Project(title=title, description="test", department_id=dept.id,
                    category="road" if dept_slug == "roads" else "drainage",
                    contractor_name="Acme Infra", budget=100000,
                    start_date=start, end_date=end, status=status,
                    progress_percentage=50, latitude=lat, longitude=lon,
                    address=f"{title} site", ward=ward or "Ward 1", road_name="MG Road",
                    created_by=creator.id)
        db.session.add(p)
        db.session.commit()
        return p.id


# ---------------------------------------------------------------------------
# Joint scheduling (sections 15 & 16.4)
# ---------------------------------------------------------------------------

def test_joint_schedule_requires_two_departments_and_projects(app):
    pid1 = _mk_project(app, "roads", "JointA", 12.9750, 77.5999,
                       date(2026, 10, 12), date(2026, 10, 13))
    pid2 = _mk_project(app, "roads", "JointB", 12.9751, 77.6000,
                       date(2026, 10, 12), date(2026, 10, 13))
    # two projects but one department -> rule violation
    with app.app_context():
        u = User.query.filter_by(employee_code="RD-1001").first()
        with pytest.raises(BusinessRuleError, match="2\\+ departments"):
            services.propose_joint_schedule(u, [pid1, pid2], "MG Road",
                                            date(2026, 10, 12), date(2026, 10, 12),
                                            "same-road work")


def test_joint_schedule_common_location_enforced(app):
    pid1 = _mk_project(app, "roads", "JointNear", 12.9750, 77.5999,
                       date(2026, 10, 12), date(2026, 10, 13))
    pid2 = _mk_project(app, "drainage", "JointFar", 12.9000, 77.7000,  # ~12 km away
                       date(2026, 10, 12), date(2026, 10, 13))
    with app.app_context():
        u = User.query.filter_by(employee_code="RD-1001").first()
        with pytest.raises(BusinessRuleError, match="common location"):
            services.propose_joint_schedule(u, [pid1, pid2], "Not co-located",
                                            date(2026, 10, 12), date(2026, 10, 12),
                                            "nope")


def test_joint_schedule_full_approve_flow_with_audit_and_publication(app, roads_client, chairman_client):
    today = date(2026, 10, 12)
    pid1 = _mk_project(app, "roads", "Relay MG Road", 12.9750, 77.5999, today, today + timedelta(days=1))
    pid2 = _mk_project(app, "drainage", "Drain MG Road", 12.9752, 77.6001, today, today + timedelta(days=1))
    with app.app_context():
        emp = User.query.filter_by(employee_code="RD-1001").first()
        js = services.propose_joint_schedule(emp, [pid1, pid2], "MG Road",
                                             today, today, "Avoid repeated digging")
        assert js.status == "pending_chairman"
        js_id = js.id
        db.session.commit()

    # Chairman notified about pending joint schedule
    ch = _chairman(app)
    with app.app_context():
        notes = Notification.query.filter_by(user_id=ch.id).all()
        assert any("Joint schedule pending" in n.title for n in notes)

    # UI approve path
    r = chairman_client.post(f"/chairman/schedules/{js_id}/approve", follow_redirects=True)
    assert r.status_code == 200
    with app.app_context():
        js = db.session.get(JointSchedule, js_id)
        assert js.status == "approved"
        assert js.approved_by == ch.id
        # public coordination wall shows the decision message
        from app.models import CoordinationMessage
        msgs = CoordinationMessage.query.filter_by(message_type="decision").all()
        assert any("🤝 Coordinated Work — MG Road" in m.body for m in msgs)
        # audit records for proposal + approval exist
        actions = [a.action for a in AuditLog.query.filter_by(target_type="joint_schedule").all()]
        assert "joint_schedule.proposed" in actions
        assert "joint_schedule.approved" in actions
        # linked projects are visible on the public joint-schedule page
    page = chairman_client.get(f"/joint-schedules/{js_id}")
    assert page.status_code == 200
    assert b"Coordinated Work" in page.data or b"MG Road" in page.data


def test_joint_schedule_rejection_requires_reason_and_nonchairman_denied(app, roads_client, chairman_client):
    today = date(2026, 10, 12)
    pid1 = _mk_project(app, "roads", "RejectMe A", 12.9750, 77.5999, today, today)
    pid2 = _mk_project(app, "drainage", "RejectMe B", 12.9751, 77.6000, today, today)
    with app.app_context():
        emp = User.query.filter_by(employee_code="RD-1001").first()
        js = services.propose_joint_schedule(emp, [pid1, pid2], "Test Spot", today, today, "why")
        js_id = js.id
        db.session.commit()

    # employee cannot approve their own department's proposal (rule 7)
    r = roads_client.post(f"/chairman/schedules/{js_id}/approve")
    assert r.status_code in (302, 403, 405)  # redirected to login or forbidden
    with app.app_context():
        assert db.session.get(JointSchedule, js_id).status == "pending_chairman"

    # Chairman rejects without reason -> rule 28 error surfaces as form failure
    r = chairman_client.post(f"/chairman/schedules/{js_id}/reject", data={"reason": ""},
                             follow_redirects=True)
    with app.app_context():
        js = db.session.get(JointSchedule, js_id)
        # service raised; status must not have silently become approved/rejected-empty
        assert js.status in ("pending_chairman", "rejected")
        if js.status == "rejected":
            assert js.rejection_reason  # never rejected blank

    # proper rejection with reason works
    r = chairman_client.post(f"/chairman/schedules/{js_id}/reject",
                             data={"reason": "Dates clash with water pipeline"})
    assert r.status_code == 302
    with app.app_context():
        js = db.session.get(JointSchedule, js_id)
        assert js.status == "rejected"
        assert "water pipeline" in js.rejection_reason
        assert any(a.action == "joint_schedule.rejected" for a in
                   AuditLog.query.filter_by(target_type="joint_schedule").all())


# ---------------------------------------------------------------------------
# Delay handling (section 20, lifecycle rules 6-7)
# ---------------------------------------------------------------------------

def test_delay_sweep_auto_delays_past_due_and_flags_unexplained(app, citizen_client):
    # subscribe the demo citizen to Ward 1 so sweep fan-out is deterministic
    citizen_client.post("/dashboard/subscriptions", data={"ward": "Ward 1", "category": ""})
    long_ago = date.today() - timedelta(days=5)
    soon = date.today() + timedelta(days=2)
    pid_late = _mk_project(app, "roads", "Overdue Road Work", 12.97, 77.60,
                           long_ago - timedelta(days=10), long_ago)
    pid_soon = _mk_project(app, "sanitation", "Due Soon Sweep", 12.98, 77.61,
                           date.today(), soon, status="in_progress")
    with app.app_context():
        result = services.run_delay_sweep()
        db.session.commit()
        p_late = db.session.get(Project, pid_late)
        assert p_late.status == "delayed"
        assert pid_late in result["delayed"]
        assert pid_soon in result["due_soon"]
        # end_date was 5 days ago -> the 48h reason window has already passed,
        # so the sweep must immediately mark it unexplained (section 20 rules 3-4)
        assert p_late.unexplained_delay is True
        ch = User.query.filter_by(role="chairman").first()
        assert any("Unexplained delay" in n.title for n in
                   Notification.query.filter_by(user_id=ch.id).all())
        assert any(a.action == "project.unexplained_delay" and a.target_id == pid_late
                   for a in AuditLog.query.all())

    # A project delayed yesterday (within the 48h window) stays explained=False
    pid_recent = _mk_project(app, "electricity", "Recent Overrun", 12.99, 77.62,
                             date.today() - timedelta(days=6), date.today() - timedelta(days=1))
    with app.app_context():
        services.run_delay_sweep()
        db.session.commit()
        p = db.session.get(Project, pid_recent)
        assert p.status == "delayed"
        assert p.unexplained_delay is False
        # spec section 20/17: ward subscribers are told about the delay
        # ("Project delayed ... ⚠️ Delayed — Reason Pending"); the seeded demo
        # citizen subscribes to Ward 10 and our projects default to Ward 1, so
        # assert on notification content existing at all rather than a specific
        # recipient, plus Chairman unexplained-delay notice was asserted above.
        sub_uid = Subscription.query.filter_by(ward="Ward 1").first().user_id
        notes = Notification.query.filter_by(user_id=sub_uid).all()
        assert any("Project delayed" == n.title and "Reason Pending" in (n.body or "")
                   for n in notes), [n.title for n in notes]


def test_post_delay_reason_creates_pending_official_post(app, roads_client, chairman_client):
    with app.app_context():
        from app.models import Department
        roads = Department.query.filter_by(slug="roads").first()
        p = Project.query.filter_by(status="approved", department_id=roads.id).first() \
            or Project.query.filter_by(status="approved").first()
        p.status = "delayed"
        p.end_date = date.today() - timedelta(days=1)
        db.session.commit()
        pid = p.id
        emp = User.query.filter_by(employee_code="RD-1001").first()

    r = roads_client.post(f"/workspace/projects/{pid}",
                          data={"action": "delay_reason", "reason": "Monsoon halted work"},
                          follow_redirects=True)
    assert r.status_code == 200
    with app.app_context():
        p = db.session.get(Project, pid)
        assert p.delay_reason is None  # pending explanation must not publish prematurely
        assert p.unexplained_delay is False
        post = OfficialPost.query.filter_by(project_id=pid).order_by(OfficialPost.id.desc()).first()
        assert post is not None and post.status == "pending_chairman"
        assert "Delay reason" in post.title and post.body == "Monsoon halted work"
        post_id = post.id
        assert any(a.action == "project.delay_reason_posted" for a in AuditLog.query.all())
    approved = chairman_client.post(f"/chairman/posts/{post_id}/approve")
    assert approved.status_code == 302
    with app.app_context():
        assert db.session.get(Project, pid).delay_reason == "Monsoon halted work"


def test_completed_requires_photo_note_and_delay_reason(app, roads_client):
    """Lifecycle rules 5 & 7: completion proof + delay reason before completed."""
    from app.models import ProjectPhoto
    with app.app_context():
        p = Project.query.filter_by(status="approved").first()
        p.status = "delayed"
        p.progress_percentage = 100
        db.session.commit()
        pid = p.id
        emp = User.query.filter_by(employee_code="RD-1001").first()

        # delayed without a reason -> cannot complete (rule 7)
        with pytest.raises(BusinessRuleError, match="delay reason"):
            services.complete_project(p, emp, note="finished late")

        # reason present but no completion photo -> still blocked (rule 5)
        p.delay_reason = "Monsoon"
        db.session.commit()
        with pytest.raises(BusinessRuleError, match="completion photo"):
            services.complete_project(p, emp, note="finished late")

        # photo present but empty note -> blocked (rule 5)
        db.session.add(ProjectPhoto(project_id=pid, photo_url="/static/x.jpg",
                                    caption="done", kind="completion", uploaded_by=emp.id))
        db.session.commit()
        with pytest.raises(BusinessRuleError, match="completion note"):
            services.complete_project(p, emp, note="")

        # all proofs present -> completes
        services.complete_project(p, emp, note="Finished after monsoon break")
        db.session.commit()
        assert db.session.get(Project, pid).status == "completed"


# ---------------------------------------------------------------------------
# Pending staff account lifecycle (16.1)
# ---------------------------------------------------------------------------

def test_new_employee_registration_approval_flow(client, chairman_client, app):
    reg = client.post("/api/register/employee", json={
        "full_name": "Test Person", "employee_code": "TS-9001",
        "department": "telecom", "designation": "Engineer",
        "email": "ts9001@telecom.municipality", "phone": "+919000000901",
        "password": "secret123",
    })
    assert reg.status_code == 200, reg.get_json()
    assert reg.get_json()["status"] == "pending_verification"

    # pending account cannot log in to workspace
    bad = client.post("/api/login/employee",
                      json={"employee_code": "TS-9001", "password": "secret123"})
    assert bad.status_code in (401, 403)

    # duplicate employee code rejected
    dup = client.post("/api/register/employee", json={
        "full_name": "Dup", "employee_code": "TS-9001", "department": "telecom",
        "designation": "X", "email": "dup@x.y", "phone": "+919000000902",
        "password": "aaaa1111"})
    assert dup.status_code >= 400

    with app.app_context():
        uid = User.query.filter_by(employee_code="TS-9001").first().id

    # Chairman approves via UI
    r = chairman_client.post(f"/chairman/employees/{uid}/approve", follow_redirects=True)
    assert r.status_code == 200
    with app.app_context():
        u = db.session.get(User, uid)
        assert u.status == "active" and u.approved_by is not None
        acts = [a.action for a in AuditLog.query.filter_by(target_type="user").all()]
        assert "employee.registration_submitted" in acts
        assert "employee.approved" in acts

    # now login succeeds and lands in workspace
    ok = client.post("/api/login/employee",
                     json={"employee_code": "TS-9001", "password": "secret123"})
    assert ok.status_code == 200 and ok.get_json()["redirect"] == "/workspace"

    # admin cannot approve employees (rule 4) — separate session
    c2 = app.test_client()
    lr = c2.post("/api/login/employee", json={"employee_code": "AD-0001", "password": "admin123"})
    assert lr.status_code == 200
    with app.app_context():
        pend = User(full_name="P2", employee_code="TS-9002", role="employee",
                    department_id=User.query.filter_by(employee_code="TS-9001").first().department_id,
                    designation="E", email="p2@x.y", status="pending_verification",
                    password_hash="x")
        db.session.add(pend)
        db.session.commit()
        pid2 = pend.id
    r2 = c2.post(f"/chairman/employees/{pid2}/approve")
    assert r2.status_code in (302, 403)  # redirected to login / denied
    with app.app_context():
        assert db.session.get(User, pid2).status == "pending_verification"


# ---------------------------------------------------------------------------
# Subscriptions: permissions + privacy + notification fan-out
# ---------------------------------------------------------------------------

def test_subscription_create_duplicate_delete_and_privacy(app, citizen_client):
    r = citizen_client.post("/dashboard/subscriptions",
                            data={"ward": "Ward 7", "category": "road"},
                            follow_redirects=True)
    assert r.status_code == 200
    from app.services import hash_phone, current_app_secret
    with app.app_context():
        # the seeded demo citizen (Priya) is the only phone-verified login used here;
        # resolve her id via the same keyed hash the login uses — no guessing order.
        h = hash_phone("+919888888888", current_app_secret())
        me = User.query.filter(User.phone_hash == h).first()
        assert me is not None, "demo citizen must exist"
        my_uid = me.id
        mine = Subscription.query.filter_by(user_id=my_uid).all()
        assert any(x.ward == "Ward 7" for x in mine)
        sid = [x for x in mine if x.ward == "Ward 7"][0].id

    # duplicate is ignored (exactly one Ward 7 row for this user)
    citizen_client.post("/dashboard/subscriptions", data={"ward": "Ward 7", "category": "road"})
    with app.app_context():
        assert Subscription.query.filter_by(user_id=my_uid, ward="Ward 7").count() == 1

    # other citizen cannot delete mine -> 403
    c2 = app.test_client()
    st = c2.post("/api/signup/citizen/start", json={"phone": "+919777777777"})
    otp = st.get_json()["demo_otp"]
    vr = c2.post("/api/signup/citizen/verify", json={"otp": otp, "display_name": "Other"})
    assert vr.status_code == 200, vr.get_json()
    r2 = c2.post(f"/dashboard/subscriptions/{sid}/delete")
    assert r2.status_code == 403

    # owner deletes own
    r3 = citizen_client.post(f"/dashboard/subscriptions/{sid}/delete", follow_redirects=True)
    assert r3.status_code == 200
    with app.app_context():
        assert db.session.get(Subscription, sid) is None

    # anonymous cannot reach the page (redirect to citizen login)
    anon = app.test_client()
    r4 = anon.get("/dashboard/subscriptions")
    assert r4.status_code == 302 and "/login" in r4.headers["Location"]


def test_project_approval_notifies_matching_subscriber(app, citizen_client, chairman_client):
    # subscribe to a unique ward so fan-out targets exactly this subscriber
    citizen_client.post("/dashboard/subscriptions", data={"ward": "Ward 42", "category": "road"})
    with app.app_context():
        sub = Subscription.query.filter_by(ward="Ward 42").first()
        assert sub is not None
        sub_uid = sub.user_id
        emp = User.query.filter_by(employee_code="RD-1001").first()
        p = Project(title="Subscribed Road Fix", description="d",
                    department_id=emp.department_id, category="road",
                    start_date=date.today(), end_date=date.today() + timedelta(days=5),
                    status="pending_chairman", latitude=12.97, longitude=77.60,
                    ward="Ward 42", road_name="Ring Road", created_by=emp.id)
        db.session.add(p)
        db.session.commit()
        pid = p.id

    ch = _chairman(app)
    with app.app_context():
        p = db.session.get(Project, pid)
        services.approve_project(p, ch)
        db.session.commit()
        n = Notification.query.filter_by(user_id=sub_uid).all()
        assert any("New project in your ward" in x.title for x in n)
        # the seeded Ward-10 subscriber must NOT receive Ward-42 alerts
        other = [s2 for s2 in Subscription.query.all() if s2.user_id != sub_uid]
        for s2 in other:
            on = Notification.query.filter_by(user_id=s2.user_id).all()
            assert not any("Subscribed Road Fix" in (x.body or "") for x in on)
