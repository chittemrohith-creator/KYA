"""Redesign UI contract tests — bright modern civic theme.

Guarantees the new look is actually served and that decorative JS never
hides content (no-JS usable), while preserving all functional contracts
(forms, endpoints, privacy rules) from the original app.
"""


def test_home_has_hero_and_ctas(client):
    r = client.get("/")
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    # hero section with isometric city layers + parallax hook
    assert 'class="hero"' in html
    assert "city-layer back" in html and "city-layer front" in html
    assert "data-parallax" in html
    # three clear calls to action pointing at real routes
    assert 'href="/projects"' in html or "View Projects" in html
    assert "Explore Map" in html and "/map" in html
    assert "Report an Issue" in html and "/signup" in html
    # skip link for keyboard users
    assert "skip-link" in html


def test_home_metrics_are_real_db_data(client, app):
    """KPI counts must equal actual database state — never invented numbers."""
    from app.models import Department, Conflict, JointSchedule
    with app.app_context():
        dept_count = Department.query.count()
        open_conflicts = Conflict.query.filter_by(status="open").count()
        approved_js = JointSchedule.query.filter_by(status="approved").count()
    html = client.get("/").get_data(as_text=True)
    assert f"<b>{dept_count}</b>" in html
    assert f"<b>{open_conflicts}</b>" in html
    assert f"<b>{approved_js}</b>" in html


def test_css_is_bright_modern_civic_theme(client):
    r = client.get("/static/style.css")
    assert r.status_code == 200
    css = r.get_data(as_text=True)
    assert "--canvas: #f8fafc" in css          # light canvas
    assert "--sky: #0ea5e9" in css             # sky blue civic palette
    assert "--teal: #0f766e" in css            # teal accent
    assert "--amber: #d97706" in css           # restrained amber
    assert "--navy: #0b2545" in css            # dark navy text base
    assert "prefers-reduced-motion" in css     # motion accessibility honored


def test_js_respects_reduced_motion_and_degrades_without_js(client):
    js = client.get("/static/app.js").get_data(as_text=True)
    assert "prefers-reduced-motion" in js
    # reveal elements get .visible via JS; CSS fallback keeps them hidden only when JS runs,
    # so verify template ships reveal class but content itself is server-rendered:
    html = client.get("/").get_data(as_text=True)
    assert "reveal" in html
    # core homepage content (project titles) is present in raw HTML without any JS execution
    assert "MG Road" in html


def test_auth_pages_use_new_shell_but_keep_form_ids(client):
    for path, marker in [("/login", 'id="phone"'), ("/signup", 'id="display_name"'),
                         ("/employee/login", 'id="password"')]:
        r = client.get(path)
        assert r.status_code == 200
        html = r.get_data(as_text=True)
        assert "auth-shell" in html, path
        assert marker in html, path  # existing JS/ids untouched


def test_map_page_still_uses_leaflet_with_cluster_and_heat(client):
    html = client.get("/map").get_data(as_text=True)
    assert "leaflet@1.9.4" in html
    assert "markercluster" in html.lower()
    assert "heat" in html.lower()
    assert "data-mapdata-url" in html  # existing endpoint wiring preserved


def test_reveal_content_is_visible_without_javascript(client):
    css = client.get("/static/style.css").get_data(as_text=True)
    assert ".reveal { opacity: 1; transform: none;" in css
    assert ".reveal.reveal-pending { opacity: 0;" in css
    js = client.get("/static/app.js").get_data(as_text=True)
    assert 'el.classList.add("reveal-pending")' in js


def test_metrics_are_layered_above_decorative_city(client):
    css = client.get("/static/style.css").get_data(as_text=True)
    assert ".hero-metrics { position: relative; z-index: 3;" in css
