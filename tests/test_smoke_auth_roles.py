"""Auth flows, role dashboards, and access control smoke tests."""


def test_login_pages_render(client):
    for path in ("/login", "/signup", "/employee/login", "/employee/register"):
        r = client.get(path)
        assert r.status_code == 200, path
    assert "Department official login" in client.get("/employee/login").get_data(as_text=True)


def test_staff_login_demo_employee(roads_client):
    r = roads_client.get("/api/me")
    user = r.get_json()["user"]
    assert user["role"] == "employee"
    assert user["department"] == "Roads"


def test_bad_password_rejected(client):
    r = client.post("/api/login/employee",
                    json={"employee_code": "RD-1001", "password": "wrong"})
    assert r.status_code == 401
    assert "Invalid credentials" in r.get_json()["error"]


def test_pending_employee_cannot_access_workspace(client):
    # Seeded pending employee (WT-3003 / water123) must be blocked until approval.
    r = client.post("/api/login/employee",
                    json={"employee_code": "WT-3003", "password": "water123"})
    assert r.status_code == 403
    assert "pending" in r.get_json()["error"].lower()
    ws = client.get("/workspace", follow_redirects=True)
    assert "/employee/login" in ws.request.path  # landed on staff login page
    assert "Department official login" in ws.get_data(as_text=True)


def test_employee_dashboard(roads_client):
    r = roads_client.get("/workspace/", follow_redirects=True)
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert "MG Road Relaying" in body
    # workspace pages
    for path in ("/workspace/projects", "/workspace/posts", "/workspace/coordination",
                 "/workspace/reports", "/workspace/notifications"):
        assert roads_client.get(path).status_code == 200, path


def test_chairman_dashboard(chairman_client):
    r = chairman_client.get("/chairman/", follow_redirects=True)
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert "Chairman" in body or "pending" in body.lower()
    for path in ("/chairman/employees", "/chairman/posts", "/chairman/projects",
                 "/chairman/conflicts", "/chairman/schedules", "/chairman/audit-logs",
                 "/chairman/departments", "/chairman/notifications"):
        assert chairman_client.get(path).status_code == 200, path


def test_admin_dashboard(admin_client):
    r = admin_client.get("/admin/", follow_redirects=True)
    assert r.status_code == 200
    for path in ("/admin/departments", "/admin/users", "/admin/seed", "/admin/system"):
        assert admin_client.get(path).status_code == 200, path


def test_citizen_dashboard(citizen_client):
    r = citizen_client.get("/dashboard")
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert "Open manhole" in body  # seeded demo report shown to its owner
    for path in ("/dashboard/reports", "/dashboard/subscriptions",
                 "/dashboard/notifications", "/profile"):
        assert citizen_client.get(path).status_code == 200, path
    # privacy: full phone never rendered, only masked last digits
    prof = citizen_client.get("/profile").get_data(as_text=True)
    assert "+919888888888" not in prof


def _denied(client, path):
    """GET path: must end up at a login page (via 302 auth redirect and/or the
    app's strict-slash 308), never at the protected content."""
    r = client.get(path, follow_redirects=True)
    assert r.status_code == 200, (path, r.status_code)
    assert "/login" in r.request.path, (path, r.request.path)


def test_wrong_role_denied_or_redirected(client, roads_client, chairman_client, citizen_client):
    # raw behavior: unauthenticated access issues an auth redirect (302)
    anon = client.get("/chairman/")
    assert anon.status_code == 302
    assert "/login" in anon.headers["Location"]
    # anonymous -> redirected to login
    _denied(client, "/workspace")
    _denied(client, "/chairman")
    _denied(client, "/admin")
    _denied(client, "/dashboard")
    # employee cannot open chairman console / citizen dashboard
    _denied(roads_client, "/chairman")
    _denied(roads_client, "/dashboard")
    # chairman cannot open employee workspace
    _denied(chairman_client, "/workspace")
    # citizen cannot open any staff area
    _denied(citizen_client, "/workspace")
    _denied(citizen_client, "/chairman/audit-logs")
    # API RBAC: audit logs forbidden for non-chairman roles
    assert roads_client.get("/api/audit-logs").status_code == 403
    assert citizen_client.get("/api/audit-logs").status_code == 403
    assert chairman_client.get("/api/audit-logs").status_code == 200
    # employee identity endpoint restricted
    assert roads_client.get("/api/employees/1/identity").status_code == 403
    assert chairman_client.get("/api/employees/1/identity").status_code == 200


def test_citizen_otp_signup_flow(client):
    start = client.post("/api/signup/citizen/start", json={"phone": "+919777777777"})
    assert start.status_code == 200
    otp = start.get_json()["demo_otp"]
    assert otp and len(otp) == 6
    bad = client.post("/api/signup/citizen/verify", json={"otp": "000000", "display_name": "Test"})
    assert bad.status_code >= 400  # wrong OTP rejected
    ok = client.post("/api/signup/citizen/verify", json={"otp": otp, "display_name": "Test Citizen"})
    assert ok.status_code == 200 and ok.get_json()["ok"]
    me = client.get("/api/me").get_json()["user"]
    assert me["role"] == "citizen" and me["phone_verified"]
