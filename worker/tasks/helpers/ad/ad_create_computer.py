"""Create a computer account in Active Directory.

Reads ad.* config from app_config (via DATABASE_URL), takes the VM name
as argv[1] and the target OU DN as argv[2], and creates a disabled
`computer` object (WORKSTATION_TRUST_ACCOUNT) under that OU.

Auth uses msldap's NTLM-password scheme (supports LDAP signing, which
modern AD requires on port 389). If an object with the same
sAMAccountName already exists anywhere under base_dn, the existing DN
is returned and the call is treated as a no-op success.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from urllib.parse import quote


WORKSTATION_TRUST_ACCOUNT = 4096


def _load_ad_config() -> dict:
    import psycopg2

    db_url = os.environ.get("DATABASE_URL", "")
    if not db_url:
        raise RuntimeError("DATABASE_URL is not set")
    if "+" in db_url.split("://", 1)[0]:
        scheme, rest = db_url.split("://", 1)
        dsn = scheme.split("+", 1)[0] + "://" + rest
    else:
        dsn = db_url

    conn = psycopg2.connect(dsn)
    try:
        cur = conn.cursor()
        cur.execute("SELECT key, value FROM app_config WHERE key LIKE 'ad.%%'")
        rows = cur.fetchall()
    finally:
        conn.close()

    return {key.split(".", 1)[1]: (val or "") for key, val in rows}


def _build_url(cfg: dict) -> str:
    server = cfg["server"]
    port = int(cfg.get("port") or "389")
    domain = cfg.get("domain") or ""
    use_ssl = str(cfg.get("use_ssl", "false")).lower() == "true"

    user_raw = f"{domain}\\{cfg['username']}" if domain and "\\" not in cfg["username"] else cfg["username"]
    user = quote(user_raw, safe="")
    pw = quote(cfg["password"], safe="")

    scheme = "ldaps+ntlm-password" if use_ssl else "ldap+ntlm-password"
    return f"{scheme}://{user}:{pw}@{server}:{port}"


def _dn_from_entry(entry) -> str | None:
    if isinstance(entry, dict):
        if entry.get("objectName"):
            return entry["objectName"]
        attrs = entry.get("attributes") or {}
        dn = attrs.get("distinguishedName")
        if isinstance(dn, list):
            return dn[0] if dn else None
        return dn
    return getattr(entry, "distinguishedName", None) or getattr(entry, "dn", None)


async def _find_existing(client, base_dn: str, sam: str) -> str | None:
    ldap_filter = f"(&(objectClass=computer)(sAMAccountName={sam}))"
    async for entry, err in client.pagedsearch(ldap_filter, ["distinguishedName"], tree=base_dn):
        if err:
            raise RuntimeError(f"search failed: {err}")
        dn = _dn_from_entry(entry)
        if dn:
            return dn
    return None


async def _dns_hostname(cfg: dict, vm_name: str) -> str:
    domain_fqdn = (cfg.get("domain_fqdn") or "").strip()
    if domain_fqdn:
        return f"{vm_name}.{domain_fqdn}"
    base_dn = cfg.get("base_dn") or ""
    dc_parts = [p.split("=", 1)[1] for p in base_dn.split(",") if p.strip().lower().startswith("dc=")]
    if dc_parts:
        return f"{vm_name}." + ".".join(dc_parts)
    return vm_name


async def _run(vm_name: str, ou_dn: str) -> dict:
    from msldap.commons.factory import LDAPConnectionFactory

    cfg = _load_ad_config()
    for k in ("server", "base_dn", "username", "password"):
        if not cfg.get(k):
            return {"success": False, "error": f"app_config key 'ad.{k}' is empty"}

    url = _build_url(cfg)
    base_dn = cfg["base_dn"]
    sam = vm_name if vm_name.endswith("$") else f"{vm_name}$"
    new_dn = f"CN={vm_name},{ou_dn}"
    dns_host = await _dns_hostname(cfg, vm_name)

    client = LDAPConnectionFactory.from_url(url).get_client()
    _, err = await client.connect()
    if err:
        return {"success": False, "error": f"LDAP connect/bind failed: {err}"}

    try:
        existing = await _find_existing(client, base_dn, sam)
        if existing:
            return {
                "success": True,
                "created": 0,
                "dn": existing,
                "message": f"Computer account '{vm_name}' already exists.",
            }

        attributes = {
            "objectClass": ["top", "person", "organizationalPerson", "user", "computer"],
            "cn": vm_name,
            "sAMAccountName": sam,
            "userAccountControl": str(WORKSTATION_TRUST_ACCOUNT),
            "dNSHostName": dns_host,
            "servicePrincipalName": [f"HOST/{vm_name}", f"HOST/{dns_host}"],
        }

        ok, err = await client.add(new_dn, attributes)
        if not ok:
            return {"success": False, "error": f"LDAP add failed: {err}", "dn": new_dn}

        return {"success": True, "created": 1, "dn": new_dn}
    finally:
        await client.disconnect()


def main() -> None:
    if len(sys.argv) < 3 or not sys.argv[1].strip() or not sys.argv[2].strip():
        print(json.dumps({"success": False, "error": "usage: ad_create_computer.py <VMName> <OU_DN>"}))
        sys.exit(2)

    vm_name = sys.argv[1].strip()
    ou_dn = sys.argv[2].strip()
    try:
        result = asyncio.run(_run(vm_name, ou_dn))
    except Exception as exc:
        result = {"success": False, "error": f"{type(exc).__name__}: {exc}"}

    print(json.dumps(result))
    sys.exit(0 if result.get("success") else 1)


if __name__ == "__main__":
    main()
