> Launch status: local demo verified; public deployment is blocked until the requirements in [LAUNCH.md](LAUNCH.md) are completed.

# CivicSync

A public coordination and transparency platform for municipal public works. CivicSync connects verified departments and phone-verified citizens through Municipal Chairman-approved projects, open schedule coordination, private citizen reporting, published delay reasons, and audit logs.

Built with Flask + SQLAlchemy (SQLite by default). Server-rendered Jinja templates at the repository root (`templates/`, `static/`), JSON API under `/api/*`.

---

## Supported Python version

**Python 3.12** (tested with 3.12.10). Python 3.10+ should work; 3.9 and older are not tested.

---

## Quick start — Windows (PowerShell)

```powershell
# 1. Clone
git clone https://github.com/chittemrohith-creator/KYA.git
cd KYA

# 2. Create and activate a virtual environment
python -m venv .venv
.\.venv\Scripts\Activate.ps1
# If activation is blocked, run once:
#   Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser

# 3. Install dependencies
python -m pip install --upgrade pip
pip install -r requirements.txt

# 4. Start the development server
python run.py
```

Then open **http://127.0.0.1:5001** in a browser. Stop with `Ctrl+C`.

## Quick start — macOS / Linux

```bash
# 1. Clone
git clone https://github.com/chittemrohith-creator/KYA.git
cd KYA

# 2. Create and activate a virtual environment
python3 -m venv .venv
source .venv/bin/activate

# 3. Install dependencies
python -m pip install --upgrade pip
pip install -r requirements.txt

# 4. Start the development server
python run.py
```

Open **http://127.0.0.1:5001**. The server binds to `127.0.0.1:5001` only (see `run.py`) — it is a development server, not a public deployment.

---

## Configuration (environment variables)

All are optional for local development; the app falls back to insecure dev defaults. Set them explicitly if you care about session/data integrity:

| Variable | Default | Purpose |
|---|---|---|
| `CIVICSYNC_SECRET` | `dev-login-secret` | Flask `SECRET_KEY` — signs session cookies. |
| `CIVICSYNC_DATA_SECRET` | `dev-secret-change-me` | Key used to hash/encrypt citizen phone numbers at rest (`app/bootstrap.py` / `app/services.py`). Changing it invalidates existing phone hashes and OTP lookups. |
| `CIVICSYNC_DB` | `sqlite:///civicsync.db` | SQLAlchemy database URI. Relative SQLite paths are resolved against the repository root, so the working directory does not matter. |

Examples:

```powershell
# PowerShell
$env:CIVICSYNC_SECRET = "a-long-random-string"
$env:CIVICSYNC_DATA_SECRET = "another-long-random-string"
$env:CIVICSYNC_DB = "sqlite:///C:/tmp/civicsync-dev.db"
python run.py
```

```bash
# bash
export CIVICSYNC_SECRET="a-long-random-string"
export CIVICSYNC_DATA_SECRET="another-long-random-string"
export CIVICSYNC_DB="sqlite:////tmp/civicsync-dev.db"
python run.py
```

> ⚠️ **The dev-default secrets and the seeded credentials below are unsafe for any public or production deployment.** Never commit real secrets. For anything beyond localhost, use a production WSGI server (gunicorn/waitress), strong random secrets, HTTPS, and a fresh database without demo accounts.

---

## Database initialization & seeding

On startup, `create_app()` calls `seed_all()` (`app/__init__.py`):

1. `db.create_all()` creates all tables in the configured SQLite database (created on first run at `<repo-root>/civicsync.db`).
2. Seeds the six default departments: Roads, Drainage, Water Supply, Electricity, Telecom, Sanitation.
3. Pre-seeds the **Municipal Chairman** and **System Administrator** accounts (spec §8.3–8.4 — these roles cannot self-register).
4. Unless `TESTING_SEED_MINIMAL` is set, seeds the **demo scenario**: MG Road Relaying (approved, Roads) vs Storm Drain Repair (pending Chairman approval, Drainage) at the same coordinates → automatic conflict detection posts an orange `conflict_alert` pin and a Coordination Hub message; plus a published official post, a private draft post, a citizen report, and a subscription.

Seeding is idempotent — existing rows are detected by unique keys and not duplicated. Delete `civicsync.db` to reset to a clean state.

---

## Seeded local-development accounts

These come directly from `seed_all()` / `seed_demo()` in `app/__init__.py`. **Local demo only — do not use anywhere real.**

### Staff login (`/employee/login`, POST JSON to `/api/login/employee`)
Login accepts either the employee code **or** the official email.

| Role | Code | Email | Password | Notes |
|---|---|---|---|---|
| Municipal Chairman | `CH-0001` | chairman@civic.municipality | `chairman123` | Lands on `/chairman` |
| System Administrator | `AD-0001` | admin@civic.municipality | `admin123` | Lands on `/admin`; cannot approve official work (rule 26) |
| Employee — Roads (active) | `RD-1001` | ravi@roads.municipality | `roads123` | Lands on `/workspace` |
| Employee — Drainage (active) | `DR-2002` | sunita@drainage.municipality | `drainage123` | Lands on `/workspace` |
| Employee — Water Supply (**pending**) | `WT-3003` | amit@water.municipality | `water123` | Login returns **403** until the Chairman approves the account |

### Citizen login (`/login`, phone + OTP)
Citizens authenticate with a phone number and a 6-digit OTP — there is no seeded citizen password. The seeded demo citizen has display name **Priya S** and phone **`+919888888888`**.

Flow (from `app/views_auth.py`):
1. `POST /api/signup/citizen/start` `{"phone": "+91..."}` → creates a pending OTP; response includes **`demo_otp`** — the OTP is returned in the JSON instead of being sent by SMS. There is no SMS gateway; no real messages are ever sent.
2. `POST /api/signup/citizen/verify` `{"otp": "...", "display_name": "..."}` → account active, session logged in.
3. Login uses the same pattern: `POST /api/login/citizen` then `POST /api/login/citizen/verify`.
OTPs expire after 5 minutes and allow 3 wrong attempts (`services.OTP_TTL_MINUTES` / `OTP_MAX_ATTEMPTS`).

> The `demo_otp` field in API responses exists only because there is no SMS provider in this build. It is visible to anyone who can reach the server — another reason this must stay local.

---

## Running the tests

Install test dependencies (includes runtime deps):

```bash
pip install -r requirements-dev.txt
```

```bash
python -m pytest tests/ -q
```

PowerShell equivalent:

```powershell
pip install -r requirements-dev.txt
python -m pytest tests\ -q
```

The suite (`tests/test_smoke_public.py`, `tests/test_smoke_auth_roles.py`) runs against an **isolated temporary SQLite database per test** (`tmp_path` fixture + `TESTING_SEED_MINIMAL` where appropriate) via Flask's test client. It never touches `civicsync.db` and sends no SMS/OTP messages. Coverage: app creation/seeding, all public pages render without missing-template errors, static CSS/JS served, `/api/mapdata` JSON shape and pin colors, staff login (Chairman/Admin/employee), pending-account rejection, role dashboards, anonymous and wrong-role access denial/redirect, API RBAC, and the citizen OTP signup flow using the returned `demo_otp`.

---

## Troubleshooting

- **`Address already in use` on port 5001** — something else holds the port (macOS AirPlay Receiver uses 5001). Edit the `port=` value in `run.py` or free the port (`Get-NetTCPConnection -LocalPort 5001` / `lsof -i :5001`).
- **PowerShell: "cannot be loaded because running scripts is disabled"** — activate failed; run `Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser` once, then re-activate.
- **`ModuleNotFoundError: flask`** — the venv isn't activated; `pip list` should show Flask. Re-run the activation command for your OS.
- **Stale/broken data after changing `CIVICSYNC_DATA_SECRET`** — phone hashes were computed with the old key. Delete `civicsync.db` and restart to reseed.
- **Reset the demo** — stop the server, delete `civicsync.db` (repo root), start again; everything reseeds automatically.
- **Windows CRLF noise in git diffs** — optional: `git config core.autocrlf true`.
- **Map tiles don't load offline** — Leaflet tiles come from OpenStreetMap CDN; pins, filters, and `/api/mapdata` still work without internet.

## Project layout

```
app/            Flask package: models, services (business rules, conflict detection),
                blueprints (public/auth/employee/chairman/admin/citizen/api)
templates/      Jinja templates (repository root, wired via app/bootstrap.py)
static/         style.css, app.js
tests/          pytest smoke tests (isolated temp DB)
run.py          Development entrypoint
requirements.txt / requirements-dev.txt
```


## Local completion build (10 October 2026)

This is a separate local copy; the original source and original demo database were not modified. No public deployment or external geocoding is performed.

From this folder on Windows, run `start-demo.cmd` (or `powershell -ExecutionPolicy Bypass -File .\start-demo.ps1`). It uses the sibling `demo-venv` and defaults to http://127.0.0.1:5001 so an existing demo on 5001 is not interrupted. Pass `-Port 5002` to select another port. It creates its own civicsync.db on first launch. Install dependencies with `uv pip install --python ..\demo-venv\Scripts\python.exe -r requirements.txt -r requirements-dev.txt` if required.

Run tests: `..\demo-venv\Scripts\python.exe -m pytest -q`. Each test uses a separate temporary database.

Citizen reports accept an address / landmark without coordinates; these reports remain visible in the feed, but do not appear on a map. Alternatively click the report map and drag the marker. Coordinate fields are hidden, optional and validated for paired, finite values in range. No guessed coordinates or geocoding are used.

The public map includes layer, status and department filters, optional marker clustering and a density heatmap. Leaflet, plugins and OpenStreetMap tiles require network access. If the library fails, a text list is shown; address-only reports remain possible without the map. Heatmap/clustering gracefully fall back if those optional libraries fail.

Completion proof is a local JPEG, PNG or WebP image, max 5 MB and 20 megapixels, genuinely decoded by Pillow and rewritten as metadata-free JPEG under a random filename. Original filenames, SVG and arbitrary URLs are not accepted as completion proof. The upload route enforces project visibility; only the owning active department can submit proof through its workspace. Completion still requires progress 100% and a nonempty note.

Joint work can be proposed from the workspace coordination screen by an active employee with at least one own-department project. It needs two departments, eligible submitted/active projects, valid dates and every project pair within 200 metres. Chairman review is required.

Admin department edit/delete is audited. Deletion refuses any scalar or JSON-held department reference; historical records are preserved.

Demo credentials: Chairman CH-0001 / chairman123; Admin AD-0001 / admin123; Roads RD-1001 / roads123; Drainage DR-2002 / drainage123. Citizen demo phone +919888888888 uses the local returned demo OTP. These are synthetic fixtures, not real personal data.

### Important boundaries
Development demonstration only: demo OTP disclosure, default development secrets, demo-grade phone obfuscation, Flask development server, and no production CSRF/rate limiting or SMS provider. Do not expose publicly. Map tiles/libraries are third-party network resources; no address is sent for geocoding. Delay sweep is manual via Chairman console, not a hosted scheduled job. Uploaded files are local and need backup in a real deployment. Real-world browser/device coverage, accessibility audits and production load/security testing are not claimed.
