"""Shared pytest fixtures — isolated temp-file SQLite database per test.

The app factory seeds on startup (departments, Chairman/Admin, demo scenario).
Tests never touch civicsync.db or any deployment database. No real SMS is ever
sent: the OTP endpoints return the code in the JSON response (`demo_otp`) by
design of this development build, so tests read it from the response instead.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app import create_app  # noqa: E402


@pytest.fixture()
def app(tmp_path):
    dbfile = tmp_path / "test.db"
    application = create_app({
        "TESTING": True,
        "SQLALCHEMY_DATABASE_URI": f"sqlite:///{dbfile}",
        "WTF_CSRF_ENABLED": False,
    })
    yield application
    with application.app_context():
        from app.models import db
        db.session.remove()
        db.engine.dispose()
    if dbfile.exists():
        os.remove(dbfile)


@pytest.fixture()
def client(app):
    return app.test_client()


def login_staff(client, identifier, password):
    """Log in via the existing employee/staff JSON login endpoint."""
    return client.post("/api/login/employee",
                       json={"employee_code": identifier, "password": password})


@pytest.fixture()
def roads_client(app):
    c = app.test_client()
    r = login_staff(c, "RD-1001", "roads123")
    assert r.status_code == 200, r.get_json()
    return c


@pytest.fixture()
def chairman_client(app):
    c = app.test_client()
    r = login_staff(c, "chairman@civic.municipality", "chairman123")
    assert r.status_code == 200, r.get_json()
    return c


@pytest.fixture()
def admin_client(app):
    c = app.test_client()
    r = login_staff(c, "AD-0001", "admin123")
    assert r.status_code == 200, r.get_json()
    return c


@pytest.fixture()
def citizen_client(app):
    """Phone + OTP login for the seeded demo citizen (+919888888888)."""
    c = app.test_client()
    start = c.post("/api/login/citizen", json={"phone": "+919888888888"})
    assert start.status_code == 200, start.get_json()
    otp = start.get_json()["demo_otp"]
    verify = c.post("/api/login/citizen/verify", json={"otp": otp})
    assert verify.status_code == 200, verify.get_json()
    return c
