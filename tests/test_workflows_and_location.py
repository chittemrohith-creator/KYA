"""Integration & regression tests for continued CivicSync work.

Covers (isolated temp DB; no real SMS — OTP is returned as demo_otp by design):
  * sqlite:///:memory: URI handling regression (bootstrap must not join a path)
  * Citizen report location UX: map-pick (hidden coords), address-only truthful
    storage (NULL lat/lng), rejection of missing/invalid location, no typed
    coordinate fields required
  * Citizen privacy on public pages (no phone leak)
  * Employee draft -> Chairman approve/reject -> publish workflow
  * Pending employee blocked from workspace
  * Conflict detection still fires after category fix
  * Audit entries recorded for approvals and citizen posts
  * Wrong-role access denied across UI and JSON API
"""
import pytest
import re

from app.bootstrap import _resolve_db_uri
from app.models import db, CitizenPost, OfficialPost, Conflict, AuditLog


# --------------------------------------------------------------------------
# bootstrap / DB URI regression
# --------------------------------------------------------------------------

def test_resolve_db_uri_memory_variants():
    root = "/repo"
    assert _resolve_db_uri("sqlite:///:memory:", root) == "sqlite:///:memory:"
    assert _resolve_db_uri("sqlite:///:memory:?cache=shared", root).startswith("sqlite:///:memory:")
    # relative path still made absolute; absolute untouched
    assert _resolve_db_uri("sqlite:///civicsync.db", root) == f"sqlite:///{root}/civicsync.db"
    assert _resolve_db_uri("sqlite:////tmp/x.db", root) == "sqlite:////tmp/x.db"


def test_app_boots_with_in_memory_uri():
    """create_app must succeed with sqlite:///:memory: (regression)."""
    import os, sys
    sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
    from app import create_app
    a = create_app({"TESTING": True, "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:"})
    with a.app_context():
        db.session.remove()
        db.engine.dispose()


# --------------------------------------------------------------------------
# citizen report location UX
# --------------------------------------------------------------------------

def test_report_form_has_no_visible_coordinate_inputs(citizen_client):
    page = citizen_client.get("/dashboard/reports")
    assert page.status_code == 200
    html = page.get_data(as_text=True)
    assert "You never need to type coordinates" in html
    assert 'type="number"' not in html                      # no numeric lat/long boxes
    assert re.search(r'<input(?=[^>]*type="hidden")(?=[^>]*name="latitude")[^>]*>', html)          # coords are internal only
    assert "Click here to place your location pin" in html


def test_map_picked_report_created_with_internal_coordinates(app, citizen_client):
    r = citizen_client.post("/dashboard/reports", data={
        "body": "Open manhole on MG Road near bus stop",
        "category": "drainage",
        "address": "",
        "location_source": "map",
        "latitude": "12.9760", "longitude": "77.6055",
    }, follow_redirects=False)
    assert r.status_code == 302
    with app.app_context():
        cp = CitizenPost.query.order_by(CitizenPost.id.desc()).first()
        assert cp.body.startswith("Open manhole")
        assert abs(cp.latitude - 12.9760) < 1e-6   # stored internally from map pick
        assert abs(cp.longitude - 77.6055) < 1e-6


def test_address_only_report_stores_null_coordinates_truthfully(app, citizen_client):
    r = citizen_client.post("/dashboard/reports", data={
        "body": "Streetlight not working for a week",
        "category": "electricity",
        "address": "12th Main Road, Indiranagar near park gate",
        "location_source": "",
        "latitude": "", "longitude": "",
    })
    assert r.status_code == 302
    with app.app_context():
        cp = CitizenPost.query.order_by(CitizenPost.id.desc()).first()
        assert cp.latitude is None and cp.longitude is None  # nothing fabricated
        assert cp.address.startswith("12th Main Road")
    page = citizen_client.get(r.headers["Location"])
    body = page.get_data(as_text=True)
    assert "Streetlight not working" in body
    assert "address only — not shown on the map" in body     # truthful public display


def test_report_without_any_location_is_rejected(citizen_client):
    page = citizen_client.post("/dashboard/reports", data={
        "body": "Garbage not collected", "category": "other",
        "address": "short", "location_source": "", "latitude": "", "longitude": "",
    })
    assert page.status_code == 200  # re-rendered form
    html = page.get_data(as_text=True)
    assert "Location required" in html
    assert "at least 8 characters" in html


def test_report_invalid_map_coordinates_rejected(citizen_client):
    page = citizen_client.post("/dashboard/reports", data={
        "body": "Bad pick", "category": "road",
        "location_source": "map", "latitude": "999", "longitude": "77.6",
    })
    assert "outside valid coordinates" in page.get_data(as_text=True)


def test_report_missing_body_rejected_even_with_pin(citizen_client):
    page = citizen_client.post("/dashboard/reports", data={
        "body": "   ", "category": "road",
        "location_source": "map", "latitude": "12.97", "longitude": "77.60",
    })
    assert "Description is required" in page.get_data(as_text=True)


def test_citizen_phone_never_leaks_on_public_pages(client, citizen_client):
    citizen_client.post("/dashboard/reports", data={
        "body": "Pothole cluster near school", "category": "road",
        "address": "School Road behind temple gate east", "location_source": "",
        "latitude": "", "longitude": "",
    })
    listing = client.get("/citizen-reports").get_data(as_text=True)
    assert "+919888888888" not in listing
    assert "Citizen — Phone Verified" in listing


# --------------------------------------------------------------------------
# staff lifecycle: pending account, drafts, chairman approve/reject
# --------------------------------------------------------------------------

def _make_draft_post(roads_client, title, body):
    resp = roads_client.post("/workspace/posts/new", data={"title": title, "body": body})
    assert resp.status_code == 302, resp.get_data(as_text=True)[:400]
    return resp.headers["Location"]  # /workspace/posts/<id>


def test_pending_employee_blocked_from_workspace(client):
    r = client.post("/api/login/employee", json={"employee_code": "WT-3003", "password": "water123"})
    assert r.status_code == 403


def test_employee_draft_post_requires_chairman_approval(app, roads_client, client):
    _make_draft_post(roads_client, "Weekend resurfacing notice", "MG Road resurfacing this weekend.")
    with app.app_context():
        op = OfficialPost.query.filter_by(title="Weekend resurfacing notice").one()
        assert op.status == "draft"
    pub = client.get("/coordination").get_data(as_text=True) + client.get("/projects").get_data(as_text=True)
    assert "Weekend resurfacing notice" not in pub   # drafts never public


def test_chairman_approve_publishes_and_rejects_with_reason(app, chairman_client, roads_client):
    loc = _make_draft_post(roads_client, "Drain cleaning schedule", "Scheduled drain cleaning in ward 7.")
    post_id = int(loc.rstrip("/").split("/")[-1])
    # submit for approval
    roads_client.post(f"/workspace/posts/{post_id}", data={"action": "submit"})
    with app.app_context():
        op = db.session.get(OfficialPost, post_id)
        assert op.status == "pending_chairman"
    # reject without reason must fail (rule 28) — BusinessRuleError -> 500 handler or redirect;
    # verify status unchanged either way
    chairman_client.post(f"/chairman/posts/{post_id}/reject", data={"reason": ""})
    with app.app_context():
        op = db.session.get(OfficialPost, post_id)
        assert op.status == "pending_chairman"
    # proper rejection then resubmit and approve
    chairman_client.post(f"/chairman/posts/{post_id}/reject", data={"reason": "Add exact dates."})
    with app.app_context():
        op = db.session.get(OfficialPost, post_id)
        assert op.status == "rejected" and op.rejection_reason == "Add exact dates."
    roads_client.post(f"/workspace/posts/{post_id}", data={"action": "submit"})
    chairman_client.post(f"/chairman/posts/{post_id}/approve", data={})
    with app.app_context():
        op = db.session.get(OfficialPost, post_id)
        assert op.status in ("approved", "published")
        assert AuditLog.query.filter(AuditLog.action.like("%post%")).count() > 0


# --------------------------------------------------------------------------
# conflict detection (spec core scenario)
# --------------------------------------------------------------------------

def test_seeded_mg_road_conflict_exists_and_maps_orange(app, client):
    with app.app_context():
        assert Conflict.query.count() >= 1
    pins = client.get("/api/mapdata").get_json()["pins"]
    assert any(p["color"] == "orange" for p in pins)
    hub = client.get("/coordination").get_data(as_text=True)
    assert "Conflict" in hub or "⚠️" in hub


# --------------------------------------------------------------------------
# role-based access control on UI and API
# --------------------------------------------------------------------------

@pytest.mark.parametrize("path,expected_login", [
    ("/dashboard", "/login"),
    ("/workspace/", "/employee/login"),
    ("/chairman/", "/employee/login"),
    ("/admin/", "/employee/login"),
])
def test_anonymous_redirected_to_correct_login(client, path, expected_login):
    r = client.get(path)
    while r.status_code in (301, 302, 307, 308):
        if expected_login in r.headers["Location"]:
            break
        r = client.get(r.headers["Location"])
    assert expected_login in r.headers["Location"], (path, r.headers["Location"])
    # and the login page itself renders successfully
    page = client.get(expected_login)
    assert page.status_code == 200


def test_wrong_role_denied_ui_and_api(roads_client, chairman_client):
    # authenticated-but-wrong-role on HTML areas: redirected to the correct login,
    # protected content never rendered (final page is a login page)
    for c, area in ((roads_client, "/chairman/"), (chairman_client, "/workspace/")):
        r = c.get(area, follow_redirects=True)
        assert r.status_code == 200
        html = r.get_data(as_text=True)
        assert "Login" in html or "Sign in" in html or "log in" in html.lower()
    # JSON API: explicit 403 for employees, 200 for Chairman
    assert roads_client.get("/api/audit-logs").status_code == 403
    assert chairman_client.get("/api/audit-logs").status_code == 200


def test_admin_cannot_approve_official_posts(app, admin_client, roads_client):
    loc = _make_draft_post(roads_client, "Admin test post", "Body text.")
    post_id = int(loc.rstrip("/").split("/")[-1])
    roads_client.post(f"/workspace/posts/{post_id}", data={"action": "submit"})
    r = admin_client.post(f"/chairman/posts/{post_id}/approve", data={})
    assert r.status_code in (302, 403)
    with app.app_context():
        op = db.session.get(OfficialPost, post_id)
        assert op.status == "pending_chairman"   # rule 26 intact

