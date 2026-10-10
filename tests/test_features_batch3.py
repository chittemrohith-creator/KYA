"""Batch-3 feature tests: admin department CRUD RBAC + safe delete, completion
photo upload validation/serving, map data filters. Isolated temp DB only."""
import io
import os
import re
from PIL import Image
from datetime import date

import pytest

from app.models import (
    db, Department, Project, User, AuditLog, CitizenPost, ProjectPhoto,
)
from app import services


# --------------------------------------------------------------- admin departments CRUD

def test_anonymous_and_wrong_roles_cannot_reach_department_admin(client, roads_client, chairman_client):
    assert client.get("/admin/departments").status_code in (301, 302)
    # employee hitting admin area -> redirected/denied
    r = roads_client.get("/admin/departments")
    assert r.status_code in (302, 403)
    # Chairman is NOT admin — must not get the admin CRUD page (rule separation)
    r = chairman_client.get("/admin/departments")
    assert r.status_code in (302, 403)


def test_admin_create_validates_slug_and_duplicate(admin_client):
    r = admin_client.post("/admin/departments", data={"action": "create", "name": "Parks", "slug": "Bad Slug!"})
    assert r.status_code == 200 and "lowercase" in r.get_data(as_text=True)
    r = admin_client.post("/admin/departments", data={"action": "create", "name": "Parks", "slug": "parks"})
    assert r.status_code == 302
    with admin_client.application.app_context():
        assert Department.query.filter_by(slug="parks").count() == 1
    # duplicate slug refused
    r = admin_client.post("/admin/departments", data={"action": "create", "name": "Parks2", "slug": "parks"})
    assert r.status_code == 200 and "already exists" in r.get_data(as_text=True)
    with admin_client.application.app_context():
        assert AuditLog.query.filter_by(action="department.created").count() == 1


def test_admin_edit_updates_and_audits(admin_client):
    with admin_client.application.app_context():
        d = Department(name="Sports", slug="sports")
        db.session.add(d); db.session.commit()
        did = d.id
    r = admin_client.post("/admin/departments", data={"action": "edit", "dept_id": did,
                                                      "name": "Sports & Recreation", "slug": "sports",
                                                      "description": "parks dept", "contact_email": "", "contact_phone": ""})
    assert r.status_code == 302
    with admin_client.application.app_context():
        d = db.session.get(Department, did)
        assert d.name == "Sports & Recreation"
        assert AuditLog.query.filter_by(action="department.updated", target_id=did).count() == 1


def test_delete_blocked_when_referenced_no_cascade(admin_client):
    """Roads has seeded projects/users — deletion must be refused, records intact."""
    with admin_client.application.app_context():
        roads = Department.query.filter_by(slug="roads").first()
        rid = roads.id
        proj_before = Project.query.filter_by(department_id=rid).count()
    r = admin_client.post("/admin/departments", data={"action": "delete", "dept_id": rid})
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert "Cannot delete" in body
    with admin_client.application.app_context():
        assert db.session.get(Department, rid) is not None          # not deleted
        assert Project.query.filter_by(department_id=rid).count() == proj_before  # no cascade
        assert AuditLog.query.filter_by(action="department.delete_blocked", target_id=rid).count() == 1


def test_delete_allowed_when_unreferenced(admin_client):
    with admin_client.application.app_context():
        d = Department(name="Temp Dept", slug="temp-dept")
        db.session.add(d); db.session.commit()
        did = d.id
    r = admin_client.post(f"/admin/departments/{did}/delete", data={})
    assert r.status_code == 302
    with admin_client.application.app_context():
        assert db.session.get(Department, did) is None
        assert AuditLog.query.filter_by(action="department.deleted", target_id=did).count() == 1


# --------------------------------------------------------------- completion photo upload

def _mk_in_progress_project(app):
    with app.app_context():
        roads = Department.query.filter_by(slug="roads").first()
        creator = User.query.filter_by(employee_code="RD-1001").first()
        p = Project(title="Upload Proof Road", description="d", department_id=roads.id,
                    category="road", contractor_name="Acme Infra", budget=1000,
                    start_date=date(2026, 9, 1), end_date=date(2026, 10, 1),
                    status="in_progress", progress_percentage=100,
                    latitude=12.97, longitude=77.60, address="Test St", ward="Ward 1",
                    road_name="Test St", created_by=creator.id)
        db.session.add(p); db.session.commit()
        return p.id


def _post_complete(roads_client, pid, **kw):
    data = {"action": "complete", "note": kw.pop("note", "Done")}
    data.update(kw)
    return roads_client.post(f"/workspace/projects/{pid}", data=data)


def test_upload_rejects_bad_extension(app, roads_client):
    pid = _mk_in_progress_project(app)
    r = _post_complete(roads_client, pid, photo_file=(io.BytesIO(b"notanimage"), "evil.exe"))
    assert r.status_code == 200 and "not a valid supported image" in r.get_data(as_text=True)
    with app.app_context():
        assert db.session.get(Project, pid).status == "in_progress"


def test_upload_rejects_empty_and_oversize(app, roads_client):
    pid = _mk_in_progress_project(app)
    r = _post_complete(roads_client, pid, photo_file=(io.BytesIO(b""), "a.png"))
    assert "empty" in r.get_data(as_text=True)
    big = io.BytesIO(b"x" * (6 * 1024 * 1024))
    r = _post_complete(roads_client, pid, photo_file=(big, "b.png"))
    text = r.get_data(as_text=True)
    assert ("5 MB" in text or r.status_code in (400, 413))  # size guard at view or Werkzeug level


def test_upload_success_random_filename_served_and_traversal_safe(app, roads_client):
    pid = _mk_in_progress_project(app)
    image = io.BytesIO()
    Image.new("RGB", (8, 8), "blue").save(image, format="PNG")
    png = image.getvalue()
    r = _post_complete(roads_client, pid,
                       photo_file=(io.BytesIO(png), "../../secret.png"),
                       caption="Final surface")
    assert r.status_code == 302, r.get_data(as_text=True)
    with app.app_context():
        ph = ProjectPhoto.query.filter_by(project_id=pid).first()
        assert ph is not None and ph.photo_url.startswith("/media/")
        fname = ph.photo_url.split("/")[-1]
        assert re.fullmatch(r"[a-f0-9]{32}\.jpg", fname)  # user name and metadata discarded
        assert ".." not in fname and "/" not in fname
        assert ph.caption == "Final surface"
        p = db.session.get(Project, pid)
        assert p.status == "completed"   # proof rule satisfied by real upload
    # traversal attempts on the serving route are rejected
    bad = roads_client.get("/uploads/projects/..%2F..%2Fapp%2Fmodels.py")
    assert bad.status_code in (400, 404)
    missing = roads_client.get("/uploads/projects/proj-deadbeef.png")
    assert missing.status_code == 404
    good = roads_client.get("/media/" + fname)
    assert good.status_code == 200 and good.mimetype == "image/jpeg"
    with Image.open(io.BytesIO(good.data)) as decoded:
        assert decoded.format == "JPEG" and decoded.size == (8, 8)
    assert good.data != png  # sanitized re-encoding, not original untrusted bytes


# --------------------------------------------------------------- map data filters

def test_mapdata_filters_consistent_with_projects_search(app, client):
    with app.app_context():
        drain = Department.query.filter_by(slug="drainage").first()
        creator = User.query.filter_by(employee_code="DR-2002").first()
        p = Project(title="Drain Pilot XYZ", description="d", department_id=drain.id,
                    category="drainage", contractor_name="Acme", budget=1,
                    start_date=date(2026, 10, 1), end_date=date(2026, 10, 5),
                    status="approved", latitude=12.98, longitude=77.61,
                    address="X st", ward="Ward 9", road_name="X Street", created_by=creator.id)
        db.session.add(p); db.session.commit()
    allpins = client.get("/api/mapdata").get_json()["pins"]
    titles = {pin["title"] for pin in allpins}
    assert any("Drain Pilot XYZ" in t for t in titles)
    off = client.get("/api/mapdata?kind=official").get_json()["pins"]
    assert all(pin["kind"] != "citizen_report" for pin in off)
    cit = client.get("/api/mapdata?kind=citizen").get_json()["pins"]
    assert all(pin["kind"] == "citizen_report" for pin in cit)
    dep = client.get("/api/mapdata?department=drainage").get_json()["pins"]
    assert all(pin["kind"] != "project" or "Drain" in pin["title"] or pin.get("department") == "Drainage" for pin in dep)
    ward = client.get("/api/mapdata?ward=Ward%209&kind=official").get_json()["pins"]
    assert [pin["title"] for pin in ward] and all("Drain Pilot XYZ" in pin["title"] for pin in ward)
    bogus = client.get("/api/mapdata?department=nope").get_json()["pins"]
    assert bogus == []
    # privacy: citizen pins carry badge only, never phone/user id
    for pin in cit:
        assert "phone" not in str(pin).lower().replace("phone verified", "")
        assert "user_id" not in pin


def test_map_page_renders_filter_controls(client):
    r = client.get("/map")
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    for needle in ('name="department"', 'name="status"', 'name="ward"', 'name="heatmap"',
                   "leaflet.markercluster", "leaflet-heat"):
        assert needle in html
