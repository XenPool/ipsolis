"""Standalone runbooks — ad-hoc trigger, failure semantics, cron scheduling.

Drives the ``standalone_runner`` execution engine against the live stack with
self-contained PowerShell modules (each prints one JSON object and exits 0, so
success is decided by the ``success`` field — no external system involved):

* ad-hoc multi-step run → all steps succeed, in order;
* a critical step failing → run marked failed, later non-``always_run`` steps
  skipped, ``always_run`` finaliser still executes;
* ``check_cron_schedules`` (run in the worker) dispatches a due cron runbook.
"""
import json
import time

import pytest

from conftest import NS

# stdout must be *only* the JSON object — the runner json.loads() the whole
# stdout and reads ``success`` (exit 0 either way).
OK_SCRIPT = 'Write-Output \'{"success": true, "marker": "zz-ok"}\'\n'
FAIL_SCRIPT = 'Write-Output \'{"success": false, "error": "zz intentional failure"}\'\n'


@pytest.fixture(scope="module")
def modules(api):
    st, ok = api.post("/admin/script-modules",
                      json={"name": f"{NS}-rb-ok", "script_content": OK_SCRIPT})
    assert st == 201, ok
    st, bad = api.post("/admin/script-modules",
                       json={"name": f"{NS}-rb-fail", "script_content": FAIL_SCRIPT})
    assert st == 201, bad
    return {"ok": ok["id"], "bad": bad["id"]}


def _mk_runbook(api, name, **kw):
    st, rb = api.post("/admin/standalone-runbooks", json={"name": name, **kw})
    assert st == 201, rb
    return rb["id"]


def _add_step(api, rbid, pos, name, module_id, **kw):
    st, s = api.post(f"/admin/standalone-runbooks/{rbid}/steps", json={
        "position": pos, "step_name": name, "script_module_id": module_id, **kw})
    assert st == 201, s


def _run_and_wait(api, rbid, timeout=60):
    st, r = api.post(f"/admin/standalone-runbooks/{rbid}/trigger")
    assert st == 200, r
    run_id = r["run_id"]
    for _ in range(timeout):
        _, run = api.get(f"/admin/standalone-runbooks/{rbid}/runs/{run_id}")
        if run.get("status") in ("success", "failed", "cancelled"):
            return run
        time.sleep(1)
    return run


def test_standalone_multistep_success(api, modules):
    rbid = _mk_runbook(api, f"{NS}-rb-ok-flow")
    _add_step(api, rbid, 1, "step-one", modules["ok"])
    _add_step(api, rbid, 2, "step-two", modules["ok"])

    run = _run_and_wait(api, rbid)
    assert run.get("status") == "success", run
    steps = sorted(run.get("steps", []), key=lambda s: s["position"])
    assert [s["status"] for s in steps] == ["success", "success"], steps
    assert [s["position"] for s in steps] == [1, 2]


def test_standalone_critical_failure_skips_then_always_runs(api, modules):
    rbid = _mk_runbook(api, f"{NS}-rb-fail-flow")
    _add_step(api, rbid, 1, "ok-first", modules["ok"])
    # critical failure; retry_count=1 so it fails fast (no 3× re-exec)
    _add_step(api, rbid, 2, "boom", modules["bad"], is_critical=True, retry_count=1)
    _add_step(api, rbid, 3, "should-skip", modules["ok"])
    _add_step(api, rbid, 4, "finaliser", modules["ok"], always_run=True)

    run = _run_and_wait(api, rbid)
    assert run.get("status") == "failed", run
    by_name = {s["step_name"]: s["status"] for s in run.get("steps", [])}
    assert by_name.get("ok-first") == "success", by_name
    assert by_name.get("boom") == "failed", by_name
    # after a critical failure, non-always_run steps are skipped …
    assert by_name.get("should-skip") == "skipped", by_name
    # … but the always_run finaliser still executes
    assert by_name.get("finaliser") == "success", by_name


def test_cron_schedules_due_runbook(api, worker, db, query, modules):
    """A cron-enabled runbook whose expression is due this minute is picked up
    by check_cron_schedules and gets a scheduled run. The scan is instance-wide
    but only fires runbooks *due now* — the same thing the real Beat does every
    minute — and there are no other cron runbooks in the lab.
    """
    rbid = _mk_runbook(api, f"{NS}-rb-cron",
                       cron_expression="* * * * *", cron_enabled=True)
    _add_step(api, rbid, 1, "tick", modules["ok"])

    out = worker.run(
        "import json\n"
        "from tasks.workflows.standalone_runner import check_cron_schedules\n"
        "print('RESULT='+json.dumps(check_cron_schedules()))\n")
    assert isinstance(json.loads(out).get("dispatched", 0), int)

    # our runbook got a scheduled run (trigger='scheduled'). Either this manual
    # call dispatched it, or the real Beat scheduler (which also ticks every
    # minute) already did within this minute — the dedup means our call then
    # reports dispatched=0, but a scheduled run still exists either way.
    rows = query(
        "SELECT status FROM standalone_runbook_runs "
        "WHERE runbook_id=%s AND trigger='scheduled' ORDER BY id DESC", (rbid,))
    assert rows, "no scheduled run created for the cron runbook"

    # and it completes successfully once the worker drains it
    for _ in range(60):
        rows = query("SELECT status FROM standalone_runbook_runs "
                     "WHERE runbook_id=%s AND trigger='scheduled' ORDER BY id DESC", (rbid,))
        if rows and rows[0][0] in ("success", "failed", "cancelled"):
            break
        time.sleep(1)
    assert rows[0][0] == "success", rows
