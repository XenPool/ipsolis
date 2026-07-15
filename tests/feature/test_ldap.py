"""On-prem LDAP portal login against the real testlab DC (winsrv1).

Drives ``POST /portal/auth/ldap`` — the on-prem AD sign-in path — end to end:

* bogus credentials → a real NTLM bind attempt that fails → 401 + error page;
* the configured bind account (``ad.username`` / ``ad.password``, read from
  app_config) → a successful bind → 302 redirect into the portal with a session
  cookie set.

The success path uses the credential the app already holds for AD lookups (a
real, valid domain account), so no test-user password is needed. Gated on the
``ad`` fixture — skipped when the DC is unreachable.
"""
import pytest
import requests

from conftest import BASE_URL


def test_ldap_login_rejects_bad_credentials():
    r = requests.post(
        BASE_URL + "/portal/auth/ldap",
        data={"username": "zz-test-nobody", "password": "definitely-wrong-zz"},
        allow_redirects=False, timeout=30)
    assert r.status_code == 401, r.text


def test_ldap_login_success_with_bind_account(ad, query):
    rows = query("SELECT key, value FROM app_config WHERE key IN ('ad.username','ad.password')")
    cfg = {k: v for k, v in rows}
    user, pw = cfg.get("ad.username"), cfg.get("ad.password")
    if not user or not pw:
        pytest.skip("AD bind credentials not present in app_config")

    s = requests.Session()
    r = s.post(BASE_URL + "/portal/auth/ldap",
               data={"username": user, "password": pw},
               allow_redirects=False, timeout=30)
    # successful bind → redirect into the portal, session established
    assert r.status_code == 302, r.text
    set_cookie = r.headers.get("set-cookie", "").lower()
    assert "session" in set_cookie, f"no session cookie set: {r.headers}"
