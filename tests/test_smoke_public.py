"""Smoke tests: app boot, DB init/seeding, public pages, static assets, map API."""
import json

from app.models import Department, Project, User, Conflict


def _seeded_project_id(client, title):
    data = json.loads(client.get("/api/projects").get_data())
    return next(p["id"] for p in data["projects"] if p["title"] == title)


def test_app_creates_and_seeds(app):
    with app.app_context():
        assert Department.query.count() >= 6
        assert User.query.filter_by(role="chairman").count() == 1
        assert User.query.filter_by(role="admin").count() == 1
        # demo MG Road scenario seeded, conflict detection ran on it
        assert Project.query.filter_by(title="MG Road Relaying — Phase 1").first() is not None
        assert Conflict.query.count() >= 1


PUBLIC_PAGES = [
    ("/", "Municipal public works"),
    ("/map", "Public works map"),
    ("/departments", "Roads"),
    ("/departments/roads", "Roads"),
    ("/projects", "MG Road Relaying"),
    ("/coordination", "Coordination"),
    ("/citizen-reports", ""),
    ("/joint-schedules", ""),
    ("/about", "CivicSync"),
]


def test_public_pages_render(client):
    for path, needle in PUBLIC_PAGES:
        r = client.get(path)
        assert r.status_code == 200, f"{path} -> {r.status_code}"
        if needle:
            assert needle in r.get_data(as_text=True), f"missing content on {path}"


def test_project_detail_public(client):
    pid = _seeded_project_id(client, "MG Road Relaying — Phase 1")
    r = client.get(f"/projects/{pid}")
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert "MG Road Relaying" in body
    # privacy: creator employee identity never shown publicly
    assert "Ravi Kumar" not in body
    assert "RD-1001" not in body


def test_private_drafts_are_404(client, app):
    # The seeded Drainage project is pending_chairman -> must NOT be public.
    data = json.loads(client.get("/api/projects").get_data())
    titles = [p["title"] for p in data["projects"]]
    assert "Storm Drain Repair — MG Road Stretch" not in titles
    with app.app_context():
        pending_id = Project.query.filter_by(status="pending_chairman").first().id
    r = client.get(f"/projects/{pending_id}")
    assert r.status_code == 404


def test_static_assets_served(client):
    css = client.get("/static/style.css")
    assert css.status_code == 200 and "text/css" in css.headers["Content-Type"]
    js = client.get("/static/app.js")
    assert js.status_code == 200 and "javascript" in js.headers["Content-Type"].lower()


def test_mapdata_json(client):
    r = client.get("/api/mapdata")
    assert r.status_code == 200
    pins = json.loads(r.get_data())["pins"]
    colors = {p["color"] for p in pins}
    assert "green" in colors   # official project pin
    assert "blue" in colors    # citizen report pin
    assert "orange" in colors  # conflict alert pin


def test_search_api(client):
    r = client.get("/api/search?q=mg road")
    assert r.status_code == 200
    results = json.loads(r.get_data())["results"]
    assert any(x["type"] == "project" for x in results)
