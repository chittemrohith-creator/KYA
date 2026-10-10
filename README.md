# CivicSync

A municipal public-works coordination and transparency platform built with Flask, SQLAlchemy and server-rendered Jinja templates. Verified staff submit projects and official posts, the Municipal Chairman reviews them, and phone-verified citizens can report issues and follow public work.

> **Current status: verified local demo.** The recovered workflow continuation and homepage road-alignment fixes are merged into `main`. Public hosting is **not approved**: the requirements in [LAUNCH.md](LAUNCH.md) remain outstanding, and production startup is intentionally blocked. Do not remove that guard to deploy the demo.

## Latest verified update — 10 October 2026

[PR #5](https://github.com/chittemrohith-creator/KYA/pull/5) continues the saved Qwen work on the existing application; it is not a rebuild. The workflow and road-alignment update was merged as commit [`d0c2e2c`](https://github.com/chittemrohith-creator/KYA/commit/d0c2e2c567eb754024e655ce5f3e94ea84f27c82).

| Verification | Result |
|---|---|
| Independent local regression suite | **113 tests passed**, exit code 0 |
| GitHub checks for the reviewed PR | **Both test jobs passed** |
| Jinja template compilation | **All 51 templates compiled** |
| Uploaded source integrity | All 88 repository files matched the tested local source byte-for-byte |
| Real-browser checks | Desktop homepage inspected; citizen-report map filtering and heatmap rendering verified |

Five `datetime.utcnow()` deprecation warnings remain in recovered tests. Full mobile, accessibility, load/security and complete browser end-to-end validation have **not** been claimed. These results establish a tested local-demo checkpoint, not public-launch approval.

### What changed

- Homepage roads and building footprints share one coherent **2:1 isometric grid**. Road lanes sit outside building footprints, and the scene moves together so decorative parallax does not misalign it.
- Public map filters now work through the same API for **layer, department, status, ward and search**. Clustering uses compatible markers; the density heatmap and safe text-list fallback remain available.
- Public conflict pins omit private pending/draft project names.
- Address-only citizen reports retain `NULL` coordinates, display truthful labels, and stay off the map. Without a map pin, an address/landmark must contain at least eight characters.
- Department deletion retains administrator-only access, checks scalar and JSON-held references, and audits blocked deletion attempts. Historical records are not cascaded away.
- Completion uploads retain genuine Pillow decoding, randomized JPEG names and visibility-controlled media serving. Recovered tests use real image fixtures rather than fake PNG bytes.
- Delay explanations remain pending until Chairman approval; the recovered tests verify both submission and approved publication.
- SQLite in-memory URI query variants and portable relative-path handling are repaired.

## Main workflows

**Citizens:** local phone/OTP signup and login, map-picked or address-only reports, public threads, own-report edits/deletion within the allowed 24-hour window, flagging, subscriptions and notifications. Phone values are not displayed on public pages.

**Department employees:** private project/post drafts, submission for Chairman review, progress updates, validated completion-photo proof, coordination messages, joint-work proposals and responses to citizen reports. Pending staff cannot enter the workspace.

**Municipal Chairman:** employee verification, project/post approvals and reasoned rejection, joint-schedule review, conflict handling, delay monitoring and audit review. Delay explanations become public only after approval.

**System Administrator:** department maintenance and audited administration. Administrator access does not grant authority to approve official work.

**Public visitors:** approved projects, departments, published citizen reports, approved joint schedules, coordination information, contractor statistics and filtered map layers. Drafts and pending project detail pages remain private.

## Requirements

- Python **3.12**, independently tested with Python 3.12.10. Python 3.10+ may work but is not established by this verification.
- Git to clone or update the repository.
- Network access for Leaflet/plugins and OpenStreetMap tiles. No external address geocoding is performed.

## Quick start — Windows

Run in PowerShell after installing Python and Git:

```powershell
git clone https://github.com/chittemrohith-creator/KYA.git
cd KYA
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe run.py
```

If `python` is unavailable but the Windows Python launcher is installed, create the environment with `py -3.12 -m venv .venv`.

Open **http://127.0.0.1:5001**. Stop with `Ctrl+C`. No environment activation or permanent PowerShell execution-policy change is required.

After the initial setup:

```powershell
.\start-demo.cmd
# Optional alternative port:
.\start-demo.cmd -Port 5002
```

The wrapper uses this repository's `.venv` and relaxes script policy only for its own process. Alternatively:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\start-demo.ps1 -Port 5002
```

## Quick start — macOS / Linux

```bash
git clone https://github.com/chittemrohith-creator/KYA.git
cd KYA
python3 -m venv .venv
./.venv/bin/python -m pip install -r requirements.txt
./.venv/bin/python run.py
```

Open **http://127.0.0.1:5001**. The development server binds to localhost; it is not a production WSGI deployment.

### Update an existing checkout

Stop the demo before updating. Preserve local changes and back up any database or uploaded files you need to retain, then run from the repository folder:

```bash
git pull --ff-only
```

If Git reports local changes or diverged history, resolve those deliberately; do not overwrite your work or delete your database just to pull an update. Reinstall requirements with your repository's `.venv` interpreter if dependencies changed, then restart the demo.

## Local configuration

These settings are for the local demo only. Changing environment variables does **not** make the application production-ready.

| Variable | Default | Purpose |
|---|---|---|
| `CIVICSYNC_SECRET` | `dev-login-secret` | Signs Flask session cookies. |
| `CIVICSYNC_DATA_SECRET` | `dev-secret-change-me` | Used by demo phone hashing/obfuscation. Changing it breaks existing phone-hash lookups. This is not production authenticated encryption. |
| `CIVICSYNC_DB` | `sqlite:///civicsync.db` | Database URI; relative SQLite paths resolve against the repository root. In-memory variants are supported for isolated tests. |
| `CIVICSYNC_ENV` | `development` | Setting this to `production` intentionally refuses startup; see [LAUNCH.md](LAUNCH.md). |

Never commit real secrets. Default secrets, known demo passwords, demo phone obfuscation and disclosed demo OTPs are unsuitable for public use.

## Demo database and accounts

On startup the demo creates tables and idempotently seeds departments, staff and an MG Road scenario. The default database is `<repo-root>/civicsync.db`; a configured database URI may point elsewhere. Keep local data isolated from real municipal or citizen information.

Staff sign in at `/employee/login` using their employee code or official email:

| Role | Employee code | Demo password | Destination |
|---|---|---|---|
| Chairman | `CH-0001` | `chairman123` | `/chairman` |
| Administrator | `AD-0001` | `admin123` | `/admin` |
| Roads employee — active | `RD-1001` | `roads123` | `/workspace` |
| Drainage employee — active | `DR-2002` | `drainage123` | `/workspace` |
| Water Supply employee — pending | `WT-3003` | `water123` | Login blocked until Chairman approval |

Citizen demo: display name **Priya S**, phone **`+919888888888`**, login at `/login`. Citizens use OTPs, not a seeded password.

**No SMS provider is configured.** Signup/login start endpoints return a `demo_otp` in JSON and the local pages display it. OTPs expire after five minutes with three incorrect attempts allowed. No real SMS is sent. Anyone who could reach this demo could see the returned code—keep it local.

To reset only disposable synthetic demo data, stop the server and identify the configured database first. Back up anything you need, then remove the intended demo database and restart to reseed. Uploaded files are separate from the database and are not reset automatically. Never use this reset procedure on real data.

## Reports, maps and completion proof

### Citizen report location

Choose a location on the report map and drag the marker, or enter an address/landmark of at least eight characters. No typed coordinates are required. Internally supplied coordinates must be paired, finite and in range; none are guessed. Address-only reports remain in the feed but are never assigned fabricated map pins.

### Public map API

Example read-only queries:

```text
/api/mapdata
/api/mapdata?kind=official
/api/mapdata?kind=citizen
/api/mapdata?kind=project&department=roads&status=approved
/api/mapdata?kind=project&ward=Ward%2010&q=MG%20Road
```

Layer values include `project`, `citizen_report`, `conflict` and `joint`; `official` excludes citizen reports, and `citizen` selects them. Department filters accept a department slug or exact name. Ward and department filters on related pins use publicly visible linked projects; unlinked citizen reports do not gain invented department/ward metadata.

The API is local. Leaflet, optional clustering/heatmap plugins and map tiles are third-party network resources. If Leaflet is unavailable, mapped records fall back to a safe text list. Address-only reporting is still available. If tiles alone fail, markers and filters remain usable; missing optional plugins disable only their corresponding features.

### Completion photo proof

An active owning department can upload JPEG, PNG or WebP proof through its project workspace. Maximum size is **5 MB** and **20 megapixels**. Pillow genuinely decodes the image and rewrites it as JPEG with a randomized filename; the original filename and metadata are discarded. SVG, arbitrary URLs and corrupt image bytes are not accepted as completion proof.

Media routes enforce project visibility. Completion requires progress **100%**, a nonempty note and at least one completion photo. Delayed projects also require an approved, published delay explanation.

### Joint work and delays

Joint proposals require at least two departments, eligible projects, at least one project from the proposing employee's department, valid dates and all project pairs within **200 metres**. Chairman review is mandatory. The delay sweep is run manually from the Chairman console; a hosted scheduled job is not configured.

## Running tests

Windows:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pytest tests\ -q
```

macOS / Linux:

```bash
./.venv/bin/python -m pip install -r requirements-dev.txt
./.venv/bin/python -m pytest tests/ -q
```

Tests use isolated temporary SQLite databases and synthetic accounts; they do not use the demo database or send real SMS. Coverage includes:

- Public pages, authentication, account status and role-based permissions.
- Citizen report locations, privacy, edit boundaries, flags and subscriptions.
- Project/post approval, delay reasons, completion proof and joint scheduling.
- Safe department maintenance, reference protections and auditing.
- Map filters, public conflict-title privacy and clustering/heatmap wiring.
- Bright-theme contracts, no-JavaScript reveal fallback, reduced-motion behavior and shared isometric road/building geometry.
- The production-startup refusal guard.

The current verified checkpoint is **113 passing tests**. Automated tests and the limited browser checks above do not replace a complete production security/accessibility review.

## UI and accessibility boundaries

The interface uses a light warm-white canvas, sky-blue/teal accents, system fonts and rounded cards. Homepage decorative parallax, card tilt and reveal effects are progressive enhancements; server-rendered content remains visible without JavaScript. Effects honor reduced-motion preferences, and pointer enhancements are disabled on touch devices. A skip link and visible focus styles are present.

The road-alignment fix uses a common SVG projection and unified scene movement. Narrow-screen CSS puts the illustration in normal document flow, but complete mobile-device and accessibility end-to-end coverage remains outstanding.

## Before any public launch

Use [LAUNCH.md](LAUNCH.md) as the launch checklist. Remaining requirements include real SMS delivery without OTP disclosure, request/login rate limiting, CSRF protection, independently generated secrets, HTTPS and secure cookies, safe staff provisioning without known passwords, authenticated phone encryption/key management, production serving, migrations, persistent storage, backups/restoration, logging/health checks and independent browser/security/accessibility verification.

**Hosting/domain/SMS recommendations and incomplete staging-security modules have not been integrated into `main`. No public deployment has been performed.** Do not bypass the production guard or mistake passing local tests for launch authorization.

## Troubleshooting

- **Port 5001 already in use:** select another port, such as `.\start-demo.cmd -Port 5002`.
- **PowerShell policy blocks a script:** use `.\start-demo.cmd` or run the repository's `.venv` Python directly; no permanent policy change is needed.
- **`ModuleNotFoundError: flask`:** install `requirements.txt` with the same `.venv` interpreter used to start the application.
- **Phone login breaks after changing the data secret:** restore the original secret for existing data, or deliberately reset only a backed-up disposable demo database. Do not casually rotate it on existing data.
- **Old UI after an update:** restart the local server and reload the page. The changed main assets have versioned URLs.
- **Map/CDN failure:** use the fallback behavior described above; no external geocoder is used.
- **Production startup refused:** this is intentional. Complete and review [LAUNCH.md](LAUNCH.md), rather than removing the guard.

## Project layout

```text
app/                  Flask factory, models, services, role blueprints,
                      path bootstrap and validated upload handling
templates/            Server-rendered Jinja pages at repository root
static/               Theme, progressive enhancements and map scripts
tests/                Smoke, workflow, completion, security-boundary
                      and recovered-regression tests
run.py                Local development entrypoint
start-demo.cmd        Windows wrapper for the PowerShell launcher
start-demo.ps1        Repository-local Windows demo launcher
LAUNCH.md             Public-launch requirements and refusal guard context
requirements.txt      Runtime dependencies
requirements-dev.txt  Test dependencies
```
