"""Remove all members from one or more Active Directory groups.

Reads ad.* config from app_config (via DATABASE_URL). Takes one or
more group DNs as positional arguments, enumerates `member` values
for each, and removes them via LDAP MODIFY/DELETE operations.

Prints a JSON summary to stdout. Exits 0 only if *every* group's
reset succeeded (or was already empty); exits 1 otherwise.

Individual results are returned per group so the caller can report
exactly which clear operation failed.
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


async def _fetch_members(client, group_dn: str) -> list[str]:
    """Return all DN values of the `member` attribute for the given group."""
    members: list[str] = []
    async for entry, err in client.pagedsearch(
        "(objectClass=*)",
        ["member"],
        tree=group_dn,
        search_scope=0,  # base — the group entry itself
    ):
        if err:
            raise RuntimeError(f"Search on '{group_dn}' failed: {err}")
        if not isinstance(entry, dict):
            continue
        attrs = entry.get("attributes") or {}
        m = attrs.get("member")
        if m is None:
            continue
        members = m if isinstance(m, list) else [m]
    return members


async def _clear_group(client, group_dn: str) -> dict:
    """Remove every `member` value from a single group DN."""
    try:
        members = await _fetch_members(client, group_dn)
    except Exception as exc:
        return {"group_dn": group_dn, "success": False, "removed": 0, "error": str(exc)}

    if not members:
        return {"group_dn": group_dn, "success": True, "removed": 0,
                "message": "Group already has no members."}

    removed = 0
    errors: list[str] = []
    for member_dn in members:
        changes = {"member": [("delete", [member_dn])]}
        try:
            ok, err = await client.modify(group_dn, changes)
        except Exception as exc:
            ok, err = False, exc
        if ok:
            removed += 1
        else:
            errors.append(f"{member_dn}: {err}")

    result = {"group_dn": group_dn, "removed": removed, "total": len(members)}
    if errors:
        result["success"] = False
        result["error"] = "; ".join(errors[:5]) + (f" (+{len(errors)-5} more)" if len(errors) > 5 else "")
    else:
        result["success"] = True
    return result


async def _run(group_dns: list[str]) -> dict:
    from msldap.commons.factory import LDAPConnectionFactory

    cfg = _load_ad_config()
    for k in ("server", "base_dn", "username", "password"):
        if not cfg.get(k):
            return {"success": False, "error": f"app_config key 'ad.{k}' is empty"}

    client = LDAPConnectionFactory.from_url(_build_url(cfg)).get_client()
    _, err = await client.connect()
    if err:
        return {"success": False, "error": f"LDAP connect/bind failed: {err}"}

    try:
        per_group = []
        for dn in group_dns:
            per_group.append(await _clear_group(client, dn))
    finally:
        await client.disconnect()

    overall_ok = all(g.get("success") for g in per_group)
    total_removed = sum(int(g.get("removed") or 0) for g in per_group)
    return {
        "success": overall_ok,
        "total_removed": total_removed,
        "groups": per_group,
    }


def main() -> None:
    dns = [a for a in sys.argv[1:] if a and a.strip()]
    if not dns:
        print(json.dumps({"success": False,
                          "error": "usage: ad_clear_group_members.py <GroupDN> [<GroupDN> ...]"}))
        sys.exit(2)

    try:
        result = asyncio.run(_run(dns))
    except Exception as exc:
        result = {"success": False, "error": f"{type(exc).__name__}: {exc}"}

    print(json.dumps(result))
    sys.exit(0 if result.get("success") else 1)


if __name__ == "__main__":
    main()
