"""Software-contract renewal reminder Beat (check_contract_renewals).

Slice 12 covered the cost math; this covers the renewal nudge: a contract that
has entered its ``notice_period_days`` window gets one reminder (stamped via
``last_renewal_reminder_at``), and a second Beat tick does not re-remind it.

Opt-in via ``contract.renewal_reminder_enabled`` (saved + restored). The scan is
instance-wide but the lab has no other contracts, and the assertions target this
contract only. One real reminder email is sent (real SMTP, like every order).
"""
import json
from datetime import date, timedelta

import pytest

from conftest import NS


@pytest.fixture
def renewal_env(api, db):
    """A contract inside its renewal window (renewal in 5 days, 10-day notice),
    with the reminder flag on. Restores the flag + email on teardown."""
    prev = {}
    with db.cursor() as cur:
        for k in ("contract.renewal_reminder_enabled", "contract.renewal_reminder_email"):
            cur.execute("SELECT value FROM app_config WHERE key=%s", (k,))
            row = cur.fetchone()
            prev[k] = row[0] if row else None

    renewal = (date.today() + timedelta(days=5)).isoformat()
    st, c = api.post("/admin/contracts", json={
        "vendor": f"{NS}-vendor-renew", "product": "ZZ Renewal",
        "contract_value": "1000.00", "currency": "EUR", "billing_interval": "annual",
        "renewal_date": renewal, "notice_period_days": 10})
    assert st == 201, c

    api.put("/admin/config/contract.renewal_reminder_enabled", json={"value": "true"})
    api.put("/admin/config/contract.renewal_reminder_email", json={"value": "kontakt@xenpool.de"})

    yield c["id"]

    api.put("/admin/config/contract.renewal_reminder_enabled",
            json={"value": prev.get("contract.renewal_reminder_enabled") or "false"})
    api.put("/admin/config/contract.renewal_reminder_email",
            json={"value": prev.get("contract.renewal_reminder_email") or ""})


def _run_beat(worker):
    out = worker.run(
        "import json\n"
        "from tasks.workflows.contract_renewals import check_contract_renewals\n"
        "print('RESULT='+json.dumps(check_contract_renewals()))\n")
    return json.loads(out)


def _reminded_at(query, cid):
    return query("SELECT last_renewal_reminder_at FROM software_contracts WHERE id=%s", (cid,))[0][0]


def test_renewal_reminder_fires_once_then_dedups(worker, query, renewal_env):
    cid = renewal_env
    assert _reminded_at(query, cid) is None  # not yet reminded

    first = _run_beat(worker)
    assert first.get("due", 0) >= 1, first
    stamped = _reminded_at(query, cid)
    assert stamped is not None, "reminder did not stamp last_renewal_reminder_at"

    # a second tick must not re-remind this contract (dedup on the stamp)
    _run_beat(worker)
    assert _reminded_at(query, cid) == stamped
