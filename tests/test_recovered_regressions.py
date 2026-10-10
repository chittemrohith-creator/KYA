"""Focused regressions for finishing saved Qwen work on verified main."""
import re
import xml.etree.ElementTree as ET
from app.models import db, Project, CitizenPost


def test_home_city_roads_share_the_building_projection(client):
    html = client.get('/').get_data(as_text=True)
    assert 'data-isometric-grid="2:1"' in html
    scene = html[html.index('data-isometric-grid'):html.index('<div class="hero-inner"')]
    svgs = [ET.fromstring(s) for s in re.findall(r'<svg\b.*?</svg>', scene, re.S)]
    assert len(svgs) == 3
    assert {svg.attrib['viewBox'] for svg in svgs} == {'0 0 560 320'}
    roads = [p for svg in svgs for p in svg.iter('polygon') if 'data-road-axis' in p.attrib]
    assert {p.attrib['data-road-axis'] for p in roads} == {'u', 'v'}
    for polygon in roads:
        points = [tuple(map(float, point.split(','))) for point in polygon.attrib['points'].split()]
        for start, end in zip(points, points[1:] + points[:1]):
            assert abs(abs((end[1]-start[1])/(end[0]-start[0])) - .5) < .01
    for svg in svgs:
        for polygon in svg.iter('polygon'):
            if 'data-building-footprint' not in polygon.attrib:
                continue
            points = [tuple(map(float, point.split(','))) for point in polygon.attrib['points'].split()]
            u = [((x-280)/42+(y-75)/21)/2 for x,y in points]
            v = [((y-75)/21-(x-280)/42)/2 for x,y in points]
            assert max(u) < 2.05 or min(u) > 2.95
            assert max(v) < 2.05 or min(v) > 2.95


def test_city_motion_preserves_relative_alignment(client):
    css = client.get('/static/style.css').get_data(as_text=True)
    assert '.city-layer.back, .city-layer.mid, .city-layer.front { transform: none;' in css
    assert '.city-stage, .city-layer, .card' in css
    assert '.city-stage { position: relative;' in css  # narrow-screen flow fallback


def test_map_conflict_titles_do_not_leak_pending_projects(app, client):
    with app.app_context():
        pending = Project.query.filter_by(status='pending_chairman').first()
        assert pending is not None
        private_title = pending.title
    pins = client.get('/api/mapdata').get_json()['pins']
    assert any(pin['kind'] == 'conflict' for pin in pins)
    assert private_title not in str(pins)


def test_map_query_ward_status_and_unknown_department_filters(client):
    matching = client.get('/api/mapdata?kind=project&q=MG%20Road&ward=Ward%2010&status=approved').get_json()['pins']
    assert matching and all(pin['kind'] == 'project' and pin['status'] == 'approved' and pin['ward'] == 'Ward 10' for pin in matching)
    assert client.get('/api/mapdata?q=NONEXISTENTXYZ').get_json()['pins'] == []
    assert client.get('/api/mapdata?department=NONEXISTENTXYZ').get_json()['pins'] == []


def test_dedicated_department_delete_cannot_bypass_rbac(roads_client, chairman_client, client):
    for caller in (roads_client, chairman_client, client):
        response = caller.post('/admin/departments/1/delete')
        assert response.status_code in (302, 403)


def test_short_address_rejected_at_service_boundary(app):
    import pytest
    from app import services
    from app.models import User
    with app.app_context():
        user = User.query.filter_by(role='citizen').first()
        with pytest.raises(services.BusinessRuleError, match='at least 8 characters'):
            services.create_citizen_post(user, 'Problem near here', address='short')
        report = services.create_citizen_post(user, 'Problem near here', address='Market gate')
        assert report.latitude is None and report.longitude is None
        db.session.rollback()
