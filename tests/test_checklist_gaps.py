"""Gap-closing integration tests: contractor statistics, citizen 24h edit
boundary + flag threshold/duplicate protection, admin department CRUD RBAC,
and truthful address-only labeling on the public listing."""
from datetime import datetime, timedelta, timezone

import pytest

from app.models import (db, AuditLog, CitizenPost, Department, Project, User,
                        ROLE_ADMIN, ROLE_CHAIRMAN, ROLE_CITIZEN, ROLE_EMPLOYEE)
from app import services


@pytest.fixture()
def ctx(app):
    with app.app_context():
        yield app


def _citizen(phone, name="Cit"):
    u = User(role=ROLE_CITIZEN, phone_hash=phone, phone_verified=True,
             display_name=name, status="active")
    db.session.add(u); db.session.commit()
    return u


# ---------------------------------------------------------------- contractor
def test_contractor_repeated_delay_statistics(ctx):
    roads = Department.query.filter_by(slug="roads").first()
    c = User.query.filter_by(role=ROLE_CHAIRMAN).first()
    assert c is not None and c.employee_code == "CH-0001"

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    for i in range(3):
        p = Project(title=f"Contractor job {i}", description="d",
                    department_id=roads.id, category="road",
                    contractor_name="Shivaji Infratech",
                    start_date=(now - timedelta(days=40)).date(),
                    end_date=(now - timedelta(days=5)).date(),
                    status="delayed", progress_percentage=60,
                    latitude=12.9, longitude=77.5, ward="W1",
                    road_name="Test Road", created_by=c.id, approved_by=c.id,
                    updated_at=now - timedelta(days=i))
        db.session.add(p)
    # one late-but-completed project also counts as delay history
    p = Project(title="Contractor job late-done", description="d",
                department_id=roads.id, category="road",
                contractor_name="Shivaji Infratech",
                start_date=(now - timedelta(days=60)).date(),
                end_date=(now - timedelta(days=30)).date(),
                actual_end_date=(now - timedelta(days=25)).date(),
                status="completed", progress_percentage=100,
                latitude=12.9, longitude=77.5, ward="W1",
                road_name="Test Road", created_by=c.id, approved_by=c.id)
    db.session.add(p); db.session.commit()

    stats = services.contractor_stats("Shivaji Infratech")
    assert stats["projects"] == 4
    assert stats["completed"] == 1
    assert stats["on_time_rate"] == 0.0         # the only completion finished late
    assert stats["delay_history"] >= 3          # current-delayed count

    # repeated-delay department flag via the sweep
    result = services.run_delay_sweep(now=datetime.utcnow())
    assert roads.id in result["repeat_offender_departments"]

    # public contractor page renders the accountability numbers
    client = ctx.test_client()
    r = client.get("/contractors/Shivaji Infratech")
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert "Shivaji Infratech" in body and "on-time" in body.lower()


# ------------------------------------------------------------- 24h boundary
def test_citizen_24h_edit_boundary(ctx):
    author = _citizen("+910000000001", "Asha")
    now = datetime.utcnow()
    fresh = CitizenPost(user_id=author.id, body="Fresh pothole report",
                        address="MG Road near clock tower", category="road",
                        status="published", flag_count=0,
                        created_at=now - timedelta(hours=23))
    old = CitizenPost(user_id=author.id, body="Old pothole report",
                      address="MG Road near clock tower", category="road",
                      status="published", flag_count=0,
                      created_at=now - timedelta(hours=25))
    db.session.add_all([fresh, old]); db.session.commit()

    services.edit_citizen_post(fresh, author, new_body="Corrected text")
    assert fresh.body == "Corrected text"

    with pytest.raises(services.BusinessRuleError):
        services.edit_citizen_post(old, author, new_body="Too late")
    with pytest.raises(services.BusinessRuleError):
        services.delete_citizen_post(old, author)
    assert old.body == "Old pothole report"     # unchanged after 24h (rule 22)

    # another citizen cannot edit it even inside the window
    other = _citizen("+910000000002", "Bala")
    with pytest.raises(services.BusinessRuleError):
        services.edit_citizen_post(fresh, other, new_body="hacked")


# ----------------------------------------------------------- flags/review
def test_flag_threshold_duplicate_and_self_protection(ctx):
    author = _citizen("+910000000010", "Author")
    post = CitizenPost(user_id=author.id, body="Suspect report",
                       address="Some Street Market Lane", category="other",
                       status="published", flag_count=0,
                       created_at=datetime.utcnow())
    db.session.add(post); db.session.commit()

    with pytest.raises(services.BusinessRuleError):
        services.flag_citizen_post(post, author)   # no self-flagging

    flaggers = [_citizen(f"+91000000001{i}", f"F{i}") for i in range(1, 7)]
    services.flag_citizen_post(post, flaggers[0])
    first_count = post.flag_count
    services.flag_citizen_post(post, flaggers[0])  # duplicate same user
    assert post.flag_count == first_count          # idempotent

    for f in flaggers[1:4]:
        services.flag_citizen_post(post, f)
    assert post.status == "published"              # 4 flags: still published
    services.flag_citizen_post(post, flaggers[4])  # 5th flag
    assert post.status == "under_review"
    assert AuditLog.query.filter_by(action="citizen_post.under_review",
                                     target_id=post.id).count() == 1

    # once under review further flags do not change state
    services.flag_citizen_post(post, flaggers[5])
    assert post.status == "under_review"


# ---------------------------------------------------- admin dept CRUD RBAC
def test_admin_department_create_and_role_limits(ctx):
    admin = User.query.filter_by(role=ROLE_ADMIN).first()
    assert admin is not None and admin.employee_code == "AD-0001"

    client = ctx.test_client()
    # anonymous: clean redirect to staff login; nothing created
    r = client.get("/admin/departments")
    assert r.status_code == 302 and "/employee/login" in r.headers["Location"]
    # wrong role: chairman may not create departments.
    # NOTE: the app's current_user() reads session["uid"]; a stale injected
    # "user_id" key must be cleared or the previous (anonymous/absent) lookup wins.
    ch = User.query.filter_by(role=ROLE_CHAIRMAN).first()
    with client.session_transaction() as s:
        s.clear(); s["uid"] = ch.id; s["role"] = ROLE_CHAIRMAN
    r = client.post("/admin/departments",
                    data={"name": "Parks", "slug": "parks"})
    assert r.status_code in (302, 403)
    assert Department.query.filter_by(slug="parks").first() is None   # no effect
    r = client.get("/admin/departments")
    assert r.status_code in (403, 302)
    if r.status_code == 302:
        assert "/admin" not in r.headers["Location"]                  # access not granted

    # admin creates a department successfully
    r = client.post("/api/login/employee",
                    json={"employee_code": "AD-0001", "password": "admin123"})
    assert r.status_code == 200, r.get_json()
    with client.session_transaction() as s:
        assert s.get("role") == ROLE_ADMIN
    r = client.post("/admin/departments",
                    data={"name": "Parks & Recreation", "slug": "parks",
                          "description": "green spaces",
                          "contact_email": "parks@municipality"},
                    follow_redirects=True)
    assert r.status_code == 200
    d = Department.query.filter_by(slug="parks").first()
    assert d is not None and d.name == "Parks & Recreation"
    logs = AuditLog.query.filter_by(action="department.created").all()
    assert len(logs) == 1 and logs[0].user_id == admin.id   # JSON metadata: filter in Python
    # duplicate slug rejected silently (no crash, no second row)
    client.post("/admin/departments", data={"name": "Parks2", "slug": "parks"})
    assert Department.query.filter_by(slug="parks").count() == 1
    # new department appears on the public list without login
    # (Jinja escapes '&' -> '&amp;' in HTML output)
    r = client.get("/departments")
    page = r.get_data(as_text=True)
    assert r.status_code == 200
    assert "Parks &amp; Recreation" in page or "Parks & Recreation" in page
    assert "parks" in page   # slug link


# ----------------------------------------------- address-only public label
def test_public_listing_labels_address_only_truthfully(ctx):
    c1 = _citizen("+910000000020", "Mapped")
    c2 = _citizen("+910000000021", "AddressOnly")
    mapped = CitizenPost(user_id=c1.id, body="Mapped streetlight out",
                         category="electricity", status="published",
                         latitude=12.97, longitude=77.59, flag_count=0,
                         created_at=datetime.utcnow())
    addr = CitizenPost(user_id=c2.id, body="Water leaking near temple",
                       category="water", status="published",
                       address="Temple Street, 4th Cross",
                       latitude=None, longitude=None, flag_count=0,
                       created_at=datetime.utcnow())
    db.session.add_all([mapped, addr]); db.session.commit()

    client = ctx.test_client()
    body = client.get("/citizen-reports").get_data(as_text=True)
    assert "address only — not shown on map" in body      # truthful label
    assert "Temple Street, 4th Cross" in body
    assert "Mapped streetlight out" in body

    # map pins exclude the coordinate-less report (pins use "kind"; verified shape)
    pins = client.get("/api/mapdata").get_json()["pins"]
    report_pins = [p for p in pins if p.get("kind") == "citizen_report"]
    assert all(p["lat"] is not None for p in report_pins)
    mapped_ids = {mapped.id}
    assert any(p["id"] in mapped_ids and p["lat"] == 12.97 for p in report_pins)
    assert not any(p["id"] == addr.id for p in report_pins)   # address-only never pinned

    # privacy: no phone material anywhere on the public listing
    assert "+910000000020" not in body and c2.phone_hash not in body
