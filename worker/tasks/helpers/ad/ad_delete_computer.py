"""Delete a computer account from Active Directory by sAMAccountName.

Reads ad.* config from app_config (via DATABASE_URL), takes the VM name
as argv[1], and performs an LDAP delete against the matching
`computer` object.  Prints a JSON result to stdout; exits 0 on success,
1 on failure.

Auth uses msldap's NTLM-password scheme (supports LDAP signing, which
modern AD requires on port 389). If the computer has leaf children
(BitLocker recovery info, service connection points, ...) the initial
delete will fail with "NOT_ALLOWED_ON_NONLEAF" — we then recursively
delete the children and retry.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from urllib.parse import quote


def _load_ad_config() -> dict:
    import psycopg2

    db_url = os.environ.get("DATABASE_URL", "")
    if not db_url:
        raise RuntimeError("DATABASE_URL is not set")
    # Strip any SQLAlchemy driver suffix (e.g. postgresql+asyncpg:// or +psycopg2://).
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


async def _delete_children(client, parent_dn: str) -> None:
    """Recursively delete direct children of parent_dn (leaf-first)."""
    children: list[str] = []
    async for entry, err in client.pagedsearch(
        "(objectClass=*)",
        ["distinguishedName"],
        tree=parent_dn,
        search_scope=1,  # oneLevel
    ):
        if err:
            raise RuntimeError(f"Child search failed under '{parent_dn}': {err}")
        dn = _dn_from_entry(entry)
        if dn and dn.lower() != parent_dn.lower():
            children.append(dn)

    for child in children:
        await _delete_children(client, child)
        ok, err = await client.delete(child)
        if not ok:
            raise RuntimeError(f"Delete child '{child}' failed: {err}")


async def _run(vm_name: str) -> dict:
    from msldap.commons.factory import LDAPConnectionFactory

    cfg = _load_ad_config()
    for k in ("server", "base_dn", "username", "password"):
        if not cfg.get(k):
            return {"success": False, "error": f"app_config key 'ad.{k}' is empty"}

    url = _build_url(cfg)
    base_dn = cfg["base_dn"]
    sam = vm_name if vm_name.endswith("$") else f"{vm_name}$"
    ldap_filter = f"(&(objectClass=computer)(sAMAccountName={sam}))"

    client = LDAPConnectionFactory.from_url(url).get_client()
    _, err = await client.connect()
    if err:
        return {"success": False, "error": f"LDAP connect/bind failed: {err}"}

    try:
        matches: list[str] = []
        async for entry, err in client.pagedsearch(ldap_filter, ["distinguishedName"], tree=base_dn):
            if err:
                return {"success": False, "error": str(err)}
            dn = _dn_from_entry(entry)
            if dn:
                matches.append(dn)

        if not matches:
            return {
                "success": True,
                "deleted": 0,
                "dn": None,
                "message": f"No computer account named '{vm_name}' in AD.",
            }
        if len(matches) > 1:
            return {
                "success": False,
                "error": f"Multiple computer accounts match '{vm_name}'",
                "count": len(matches),
                "dns": matches,
            }

        dn = matches[0]
        ok, err = await client.delete(dn)
        if not ok:
            msg = str(err or "").lower()
            if "notallowedonnonleaf" in msg.replace(" ", "") or "not_allowed_on_non_leaf" in msg:
                await _delete_children(client, dn)
                ok, err = await client.delete(dn)
                if not ok:
                    return {"success": False, "error": f"Delete (after child cleanup) failed: {err}", "dn": dn}
            else:
                return {"success": False, "error": f"LDAP delete failed: {err}", "dn": dn}

        return {"success": True, "deleted": 1, "dn": dn}
    finally:
        await client.disconnect()


def main() -> None:
    if len(sys.argv) < 2 or not sys.argv[1].strip():
        print(json.dumps({"success": False, "error": "usage: ad_delete_computer.py <VMName>"}))
        sys.exit(2)

    vm_name = sys.argv[1].strip()
    try:
        result = asyncio.run(_run(vm_name))
    except Exception as exc:
        result = {"success": False, "error": f"{type(exc).__name__}: {exc}"}

    print(json.dumps(result))
    sys.exit(0 if result.get("success") else 1)


if __name__ == "__main__":
    main()
