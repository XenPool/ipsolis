# E2E smoke tests (Playwright)

Browser-level smoke tests that drive the **running** ip·Solis stack over
HTTP — like a real user. They run on the **host** (or CI runner), not
inside the containers, so they need no app dependencies.

```
tests/e2e/
├─ conftest.py          # base_url + admin_api_key fixtures
├─ test_health.py       # /health serves, DB reachable
└─ test_admin_login.py  # operator reaches the admin dashboard
```

Keep this suite **thin** — a handful of critical journeys, not full
coverage. Unit/integration tests live in `api/tests/` and run *inside*
the container; this layer sits on top of them.

## Run locally (Windows / PowerShell)

One-time setup:

```powershell
python -m venv .venv-e2e
.\.venv-e2e\Scripts\Activate.ps1
pip install -r tests/e2e/requirements.txt
playwright install chromium
```

Every run — the stack must be up first:

```powershell
docker compose up -d                       # app at http://localhost:8000
$env:ADMIN_API_KEY = "<your .env ADMIN_API_KEY>"

pytest tests/e2e/                           # headless
pytest tests/e2e/ --headed                  # watch the browser
pytest tests/e2e/ --headed --slowmo 800     # slowed down, step by step
```

Record a new journey by clicking through the UI:

```powershell
playwright codegen http://localhost:8000/ui/
```

Debug a failure interactively:

```powershell
$env:PWDEBUG = "1"; pytest tests/e2e/test_admin_login.py
```

## Config

| Env var | Default | Purpose |
|---|---|---|
| `IPSOLIS_BASE_URL` | `http://localhost:8000` | Root URL of the running stack |
| `ADMIN_API_KEY` | — (test skips if unset) | Legacy admin-login path |

## In CI

`.github/workflows/ci.yml` runs this suite headless on every push/PR to
`dev`: it builds and starts the compose stack, runs the in-container
unit tests, then runs these E2E tests from the runner host. On failure a
Playwright **trace** is uploaded as a build artifact — open it with
`playwright show-trace <file>` to replay every step with DOM snapshots.

> On a fresh CI database `admin_users` is empty, so `test_admin_login`
> takes the **first-run setup** path (creating a superadmin). Locally,
> where an admin already exists, it uses the legacy `ADMIN_API_KEY`
> login. Both assert the dashboard loads.
