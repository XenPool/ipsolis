"""Autonomous feature-test harness — runs against the live DEV compose stack.

Unlike ``api/tests`` (pure unit tests, everything mocked) this suite drives the
running stack over HTTP + DB, exercising whole features end-to-end. It:

* talks to the API on ``IPSOLIS_URL`` with the admin key,
* reads/writes the DB directly on ``localhost:5432`` for setup + assertions,
* inspects the **mock-receiver** (testlab) on ``IPSOLIS_MOCK_URL`` for
  Slack / Teams / mock-Graph delivery,
* mints the same HMAC-signed tokens the app does (approval / attestation),
* namespaces all fixtures under ``zz-test`` and tears them down.

Credentials + DSN come from the repo-root ``.env`` (ADMIN_API_KEY,
API_SECRET_KEY, POSTGRES_*), overridable via env vars. Nothing here touches
real Teams/SMTP config — those stay real; tests that need a mock (Slack, Graph)
set the relevant config surgically and restore it.

Run:  cd tests/feature && python -m pytest -q
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import subprocess
import time
from pathlib import Path

import psycopg2
import pytest
import requests

# ── Config (from .env at repo root, env-overridable) ─────────────────────────

_REPO_ROOT = Path(__file__).resolve().parents[2]


def _load_dotenv() -> dict[str, str]:
    env: dict[str, str] = {}
    f = _REPO_ROOT / ".env"
    if f.exists():
        for line in f.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip().strip('"').strip("'")
    return env


_ENV = _load_dotenv()


def _cfg(key: str, default: str = "") -> str:
    return os.environ.get(key) or _ENV.get(key) or default


BASE_URL = _cfg("IPSOLIS_URL", "http://localhost:8000").rstrip("/")
MOCK_URL = _cfg("IPSOLIS_MOCK_URL", "http://localhost:9000").rstrip("/")
ADMIN_KEY = _cfg("ADMIN_API_KEY")
SECRET_KEY = _cfg("API_SECRET_KEY")
_DB = dict(
    host=_cfg("IPSOLIS_DB_HOST", "localhost"),
    port=int(_cfg("IPSOLIS_DB_PORT", "5432")),
    dbname=_cfg("POSTGRES_DB", "ipsolis"),
    user=_cfg("POSTGRES_USER", "xpuser"),
    password=_cfg("POSTGRES_PASSWORD", ""),
)

NS = "zz-test"  # namespace prefix for all test-created rows


# ── HTTP client ──────────────────────────────────────────────────────────────

class ApiClient:
    def __init__(self, base: str, admin_key: str):
        self.base = base
        self.s = requests.Session()
        self.s.headers["X-Admin-Key"] = admin_key

    def _do(self, method: str, path: str, **kw):
        r = self.s.request(method, self.base + path, timeout=30, **kw)
        try:
            body = r.json()
        except ValueError:
            body = r.text
        return r.status_code, body

    def get(self, path, **kw):
        return self._do("GET", path, **kw)

    def post(self, path, json=None, **kw):
        return self._do("POST", path, json=json, **kw)

    def put(self, path, json=None, **kw):
        return self._do("PUT", path, json=json, **kw)

    def delete(self, path, **kw):
        return self._do("DELETE", path, **kw)


@pytest.fixture(scope="session")
def api() -> ApiClient:
    if not ADMIN_KEY:
        pytest.skip("ADMIN_API_KEY not available (set it in .env or env)")
    c = ApiClient(BASE_URL, ADMIN_KEY)
    st, _ = c.get("/health")
    if st != 200:
        pytest.skip(f"ipSolis API not reachable at {BASE_URL} (health {st})")
    return c


# ── DB access ────────────────────────────────────────────────────────────────

@pytest.fixture(scope="session")
def db():
    try:
        conn = psycopg2.connect(**_DB)
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"Postgres not reachable at {_DB['host']}:{_DB['port']}: {exc}")
    conn.autocommit = True
    yield conn
    conn.close()


def _q(db, sql, params=None):
    with db.cursor() as cur:
        cur.execute(sql, params or ())
        if cur.description:
            return cur.fetchall()
    return []


@pytest.fixture
def query(db):
    return lambda sql, params=None: _q(db, sql, params)


# ── Mock-receiver (testlab) ──────────────────────────────────────────────────

class Mock:
    def __init__(self, base: str):
        self.base = base

    def up(self) -> bool:
        try:
            return requests.get(self.base + "/health", timeout=3).status_code == 200
        except Exception:  # noqa: BLE001
            return False

    def reset(self):
        requests.post(self.base + "/reset", timeout=5)

    def recent(self, path: str, limit: int = 20):
        r = requests.get(self.base + "/recent", params={"path": path, "limit": limit}, timeout=5)
        return r.json().get("items", [])


@pytest.fixture(scope="session")
def mock() -> Mock:
    m = Mock(MOCK_URL)
    if not m.up():
        pytest.skip(f"testlab mock-receiver not up at {MOCK_URL} "
                    "(docker compose -f docker-compose.testlab.yml up -d mock-receiver)")
    return m


# ── Real Active Directory (via `docker compose exec worker`) ─────────────────
# The host isn't domain-joined and msldap lives in the worker image, so AD
# operations run inside the worker container. Each helper feeds a short script
# to `python -` over stdin and parses a single ``RESULT=<json>`` line.

class AD:
    def _run(self, script: str, timeout: int = 120) -> str:
        p = subprocess.run(
            ["docker", "compose", "exec", "-T", "worker", "python", "-"],
            cwd=str(_REPO_ROOT), input=script, capture_output=True, text=True, timeout=timeout)
        if p.returncode != 0:
            raise RuntimeError(f"worker exec failed: {(p.stderr or p.stdout)[-400:]}")
        for line in reversed(p.stdout.splitlines()):
            if line.startswith("RESULT="):
                return line[len("RESULT="):]
        raise RuntimeError(f"no RESULT from worker: {p.stdout[-400:]} / {p.stderr[-200:]}")

    def members(self, group_dn: str) -> list[str]:
        """sAMAccountNames of direct user members of the group (lowercased)."""
        out = self._run(
            "import json\n"
            "from tasks.modules.db import get_worker_session\n"
            "from tasks.modules.target_executor import list_ad_group_members\n"
            f"ms=list_ad_group_members({group_dn!r}, get_worker_session())\n"
            "print('RESULT='+json.dumps([ (m.get('sam') or '').lower() for m in ms ]))\n")
        return json.loads(out)

    def add_out_of_band(self, group_dn: str, principal: str) -> None:
        """Add a principal to the group *outside* ipSolis (simulates drift)."""
        self._run(
            "from tasks.modules.db import get_worker_session\n"
            "from tasks.modules.target_executor import _grant_ad_group\n"
            f"_grant_ad_group({group_dn!r}, {principal!r}, get_worker_session(), "
            "target={'create_if_missing': True})\n"
            "print('RESULT=ok')\n")

    def delete_group(self, group_dn: str) -> None:
        """Best-effort delete of a test group (teardown)."""
        self._run(
            "import asyncio\n"
            "from tasks.modules.db import get_worker_session\n"
            "from tasks.modules.target_executor import _build_msldap_url\n"
            "from msldap.commons.factory import LDAPConnectionFactory\n"
            f"dn={group_dn!r}\n"
            "async def _d():\n"
            "    c=LDAPConnectionFactory.from_url(_build_msldap_url(get_worker_session())[0]).get_client()\n"
            "    await c.connect()\n"
            "    try:\n"
            "        await c.delete(dn)\n"
            "    finally:\n"
            "        await c.disconnect()\n"
            "asyncio.run(_d())\n"
            "print('RESULT=ok')\n")

    def run_drift(self) -> dict:
        """Run the drift reconciliation task synchronously in the worker."""
        out = self._run(
            "import json\n"
            "from tasks.workflows.drift_reconcile import reconcile_drift\n"
            "print('RESULT='+json.dumps(reconcile_drift()))\n", timeout=180)
        return json.loads(out)


@pytest.fixture(scope="session")
def ad() -> AD:
    a = AD()
    try:  # probe: bind + a harmless member read
        a.members("CN=Users,CN=Builtin,DC=xenpool,DC=local")
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"real AD not reachable from worker ({type(e).__name__}); "
                    "skipping @requires_ad tests")
    return a


# ── Signed-token minter (same format as app.utils.*_token) ───────────────────

def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _mint(payload: dict) -> str:
    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    body = _b64url(raw)
    sig = hmac.new(SECRET_KEY.encode("utf-8"), body.encode("ascii"), hashlib.sha256).hexdigest()
    return f"{body}.{sig}"


@pytest.fixture(scope="session")
def tokens():
    if not SECRET_KEY:
        pytest.skip("API_SECRET_KEY not available")

    class Tokens:
        def approval(self, aid: int) -> str:
            return _mint({"aid": int(aid), "exp": int(time.time()) + 3600, "v": 1})

        def attestation(self, aid: int) -> str:
            return _mint({"aid": int(aid), "exp": int(time.time()) + 3600, "v": 1, "kind": "attestation"})

    return Tokens()


# ── SCIM bearer token (created + torn down) ──────────────────────────────────

@pytest.fixture
def scim_token(api):
    st, t = api.post("/admin/api-tokens", json={"name": f"{NS}-scim", "scopes": ["scim:read", "scim:write"]})
    assert st == 201, t
    raw = t.get("raw_token")
    tid = t.get("id")
    yield raw
    api.delete(f"/admin/api-tokens/{tid}")


def scim_get(path: str, token: str, **params):
    return requests.get(BASE_URL + "/scim/v2" + path, headers={"Authorization": f"Bearer {token}"},
                        params=params, timeout=15)


def scim_send(method: str, path: str, token: str, body: dict | None = None):
    return requests.request(method, BASE_URL + "/scim/v2" + path,
                            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                            json=body, timeout=15)


# ── Namespaced cleanup (before + after the whole session) ────────────────────

def _purge(db):
    """Delete every zz-test-* row this suite may create, FK-safe."""
    sqls = [
        # orders in zz groups or for zz users → free their pool assets first
        "UPDATE asset_pool SET status='Free', current_order_id=NULL, expires_at=NULL "
        "WHERE current_order_id IN (SELECT o.id FROM orders o LEFT JOIN order_groups og ON og.id=o.order_group_id "
        "  LEFT JOIN asset_types at ON at.id=o.asset_type_id "
        "  WHERE og.origin='scim' OR at.name LIKE %s OR lower(o.user_email) LIKE %s)",
        "DELETE FROM order_approvals WHERE order_id IN (SELECT o.id FROM orders o LEFT JOIN asset_types at ON at.id=o.asset_type_id WHERE at.name LIKE %s OR lower(o.user_email) LIKE %s)",
        "DELETE FROM order_steps WHERE order_id IN (SELECT o.id FROM orders o LEFT JOIN asset_types at ON at.id=o.asset_type_id WHERE at.name LIKE %s OR lower(o.user_email) LIKE %s)",
        "DELETE FROM order_change_log WHERE identifier LIKE %s OR order_id IN (SELECT o.id FROM orders o LEFT JOIN asset_types at ON at.id=o.asset_type_id WHERE at.name LIKE %s OR lower(o.user_email) LIKE %s)",
        "DELETE FROM drift_findings WHERE identifier LIKE %s",
        "DELETE FROM attestation_artifacts WHERE lower(recipient_email) LIKE %s",
        "DELETE FROM orders WHERE lower(user_email) LIKE %s OR asset_type_id IN (SELECT id FROM asset_types WHERE name LIKE %s)",
        "DELETE FROM order_groups WHERE lower(recipient_email) LIKE %s OR bundle_name LIKE %s",
        "DELETE FROM scim_identities WHERE lower(user_email) LIKE %s",
        "DELETE FROM assignment_rules WHERE name LIKE %s",
        "DELETE FROM bundle_positions WHERE bundle_id IN (SELECT id FROM bundles WHERE name LIKE %s)",
        "DELETE FROM bundles WHERE name LIKE %s",
        "DELETE FROM software_contracts WHERE vendor LIKE %s",
        "DELETE FROM asset_pool WHERE name LIKE %s",
        "DELETE FROM asset_types WHERE name LIKE %s",
    ]
    like = f"{NS}%"
    email_like = f"{NS}%@%"
    contains = f"%{NS}%"  # matches zz-test anywhere (e.g. inside an AD group DN)
    params = [
        (like, email_like),                  # UPDATE asset_pool
        (like, email_like), (like, email_like),  # order_approvals, order_steps
        (contains, like, email_like),        # order_change_log
        (contains,),                         # drift_findings
        (email_like,),                       # attestation_artifacts
        (email_like, like),                  # orders
        (email_like, like),                  # order_groups
        (email_like,),                       # scim_identities
        (like,),                             # assignment_rules
        (like,),                             # bundle_positions
        (like,),                             # bundles
        (like,),                             # software_contracts
        (like,),                             # asset_pool
        (like,),                             # asset_types
    ]
    with db.cursor() as cur:
        for sql, p in zip(sqls, params):
            try:
                cur.execute(sql, p)
            except Exception:  # noqa: BLE001 — best-effort cleanup, keep going
                db.rollback()


@pytest.fixture(scope="session", autouse=True)
def _clean(db):
    _purge(db)
    yield
    _purge(db)
