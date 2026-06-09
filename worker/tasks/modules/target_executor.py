"""Modul: Target Executor – Config-driven Gruppen-Zugriff.

Reads 'targets' from asset_types and adds/removes principals from groups.
Writes deterministic order change log for revoke.

targets-Format (JSONB in asset_types):
    [{"type": "ad_group", "identifier": "CN=App-Users,OU=...", "principal_source": "requester"}]

principal_source-Werte:
    "requester"    – user_email des Antragstellers
    "rdp_users"    – rdp_users-Liste aus der Order
    "admin_users"  – admin_users-Liste aus der Order
    "all_users"    – alle drei kombiniert
"""

import json
import logging

from sqlalchemy import text
from sqlalchemy.orm import Session

from tasks.modules.config_reader import get_config, get_config_int

logger = logging.getLogger(__name__)


# ── LDAP helpers (msldap – supports NTLM signing for modern Windows Server) ─────

def _build_msldap_url(db: Session) -> tuple[str, str]:
    """Returns (msldap_url, base_dn) using ad.* keys from app_config."""
    from urllib.parse import quote

    server_host = get_config(db, "ad.server", "dc.example.com")
    server_port = get_config_int(db, "ad.port", 389)
    bind_user = get_config(db, "ad.username", "")
    bind_password = get_config(db, "ad.password", "")
    domain = get_config(db, "ad.domain", "")
    base_dn = get_config(db, "ad.base_dn", "DC=example,DC=com")

    raw_user = f"{domain}\\{bind_user}" if domain else bind_user
    url = (f"ldap+ntlm-password://{quote(raw_user, safe='')}:"
           f"{quote(bind_password, safe='')}@{server_host}:{server_port}")
    return url, base_dn


async def _resolve_dn_async(principal: str, client, base_dn: str) -> str:
    """Resolves an email or sAMAccountName to the user's full DN in AD."""
    if "@" in principal:
        # Try mail first, fall back to UPN (userPrincipalName) — covers users with no mail attribute
        ldap_filter = f"(|(mail={principal})(userPrincipalName={principal}))"
    else:
        sam = principal.split("\\")[-1] if "\\" in principal else principal
        ldap_filter = f"(sAMAccountName={sam})"

    async for entry, err in client.pagedsearch(ldap_filter, ["distinguishedName"], tree=base_dn):
        if err:
            raise ValueError(f"LDAP search error: {err}")
        dn = entry["attributes"].get("distinguishedName")
        if isinstance(dn, list):
            dn = dn[0]
        if dn:
            return dn
    raise ValueError(f"User '{principal}' not found in AD")


# ── Principal resolution ────────────────────────────────────────────────────────

def _resolve_principals(
    principal_source: str,
    user_email: str,
    rdp_users: list,
    admin_users: list,
) -> list[str]:
    """Returns the list of affected user principals."""
    if principal_source == "requester":
        return [user_email] if user_email else []
    if principal_source == "rdp_users":
        return list(rdp_users or [])
    if principal_source == "admin_users":
        return list(admin_users or [])
    if principal_source == "all_users":
        principals: set[str] = set()
        if user_email:
            principals.add(user_email)
        principals.update(rdp_users or [])
        principals.update(admin_users or [])
        return list(principals)
    return []


# ── Change-Log Helper ──────────────────────────────────────────────────────────

def _write_change_log(
    db: Session,
    order_id: int,
    target_type: str,
    identifier: str,
    action: str,
    principal: str,
    state: str,
    metadata: dict | None = None,
    idempotency_key: str | None = None,
    resolved_object_id: str | None = None,
) -> None:
    db.execute(
        text("""
            INSERT INTO order_change_log
                (order_id, target_type, identifier, action, principal, state,
                 executed_at, metadata, idempotency_key, resolved_object_id)
            VALUES (:oid, :ttype, :ident, :action, :principal, :state,
                    NOW(), CAST(:meta AS jsonb), :ikey, :robj)
        """),
        {
            "oid": order_id,
            "ttype": target_type,
            "ident": identifier,
            "action": action,
            "principal": principal,
            "state": state,
            "meta": json.dumps(metadata) if metadata else "null",
            "ikey": idempotency_key,
            "robj": resolved_object_id,
        },
    )
    db.commit()


# ── Handler-Funktionen ─────────────────────────────────────────────────────────

_GROUP_NOT_FOUND_MARKERS = (
    "no such object",      # generic LDAP "0x20" descriptor variants
    "nosuchobject",        # CamelCase variant emitted by some libs
    "0x208a",              # AD: ERROR_DS_OBJ_NOT_FOUND in hex
    "no_object",           # msldap shorthand
    "object does not",     # paraphrased forms
)


def _parse_group_cn(dn: str) -> str:
    """Pull the leftmost ``CN=...`` value from a group DN.

    ``CN=My App Users,OU=Provisioned,OU=Groups,DC=example,DC=com`` → ``My App Users``.

    Raises ``ValueError`` for DNs that don't start with ``CN=``, since the
    create path needs a sensible ``sAMAccountName`` (=CN by convention) and
    a non-CN-prefixed DN means the operator typed the wrong thing.
    """
    head = dn.split(",", 1)[0].strip()
    if "=" not in head:
        raise ValueError(f"Invalid DN: {dn!r}")
    attr, _, val = head.partition("=")
    if attr.strip().lower() != "cn":
        raise ValueError(f"Group DN must start with 'CN=', got {head!r}")
    return val.strip()


async def _create_ad_group_async(client, group_dn: str) -> None:
    """Create a Security Global AD group at ``group_dn`` via msldap.

    Defaults: Security + Global scope (``groupType = -2147483646``). The
    bind account needs ``Create child`` permission on the parent OU; if
    not, the call surfaces the AD/LDAP error verbatim so the operator
    can fix the ACL or the OU path. ``sAMAccountName`` is taken from
    the leftmost ``CN`` value — AD enforces uniqueness at the domain
    level, so don't reuse an existing CN under a different OU.
    """
    cn = _parse_group_cn(group_dn)
    attrs = {
        "objectClass": ["top", "group"],
        "sAMAccountName": cn,
        # -2147483646 = 0x80000002 = ADS_GROUP_TYPE_SECURITY_ENABLED |
        # ADS_GROUP_TYPE_GLOBAL_GROUP. The most conservative default
        # for an ipSolis-managed access group; tenants that need
        # Universal / DomainLocal can pre-create the group themselves.
        "groupType": "-2147483646",
    }
    # msldap exposes a generic ``add`` that takes (dn, attrs); some
    # builds use ``add_obj``. Try the canonical name first.
    add_fn = getattr(client, "add", None) or getattr(client, "add_obj", None)
    if add_fn is None:
        raise RuntimeError(
            "Installed msldap has neither client.add() nor client.add_obj(); "
            "cannot create AD group programmatically."
        )
    _, err = await add_fn(group_dn, attrs)
    if err is not None:
        raise RuntimeError(f"LDAP add (group create) failed: {err}")


def _grant_ad_group(identifier: str, principal: str, db: Session, *, target: dict | None = None) -> dict:
    """Adds principal to the AD group identified by its DN.

    When ``target['create_if_missing']`` is true and the membership add
    fails because the group does not exist, the worker tries to create
    the group (Security Global, in the OU implied by the DN) before
    retrying. Any other error is raised verbatim.
    """
    import asyncio
    from msldap.commons.factory import LDAPConnectionFactory

    url, base_dn = _build_msldap_url(db)
    create_if_missing = bool((target or {}).get("create_if_missing", False))

    async def _do():
        factory = LDAPConnectionFactory.from_url(url)
        client = factory.get_client()
        await client.connect()
        try:
            user_dn = await _resolve_dn_async(principal, client, base_dn)
            _, err = await client.add_user_to_group(user_dn, identifier)
            if err is None:
                return user_dn
            err_s = str(err).lower()
            # Idempotent success — the user is already a member.
            if "already" in err_s or "exists" in err_s:
                return user_dn
            # Group doesn't exist + the operator opted into auto-create.
            if create_if_missing and any(m in err_s for m in _GROUP_NOT_FOUND_MARKERS):
                logger.info(
                    "AD group %s missing; attempting create (create_if_missing=true)",
                    identifier,
                )
                await _create_ad_group_async(client, identifier)
                _, err2 = await client.add_user_to_group(user_dn, identifier)
                if err2 is not None and "already" not in str(err2).lower():
                    raise RuntimeError(f"LDAP add_user_to_group after create failed: {err2}")
                return user_dn
            raise RuntimeError(f"LDAP add_user_to_group failed: {err}")
        finally:
            await client.disconnect()

    user_dn = asyncio.run(_do())
    logger.info("AD group grant OK: %s → %s (dn=%s)", principal, identifier, user_dn)
    return {"success": True, "user_dn": user_dn}


def _grant_entra_group(identifier: str, principal: str, db: Session, *, target: dict | None = None) -> dict:
    """Adds principal to the Entra group identified by identifier (MS Graph)."""
    raise NotImplementedError("Entra group grant not yet implemented")


def _revoke_ad_group(identifier: str, principal: str, db: Session) -> dict:
    """Removes principal from the AD group identified by its DN."""
    import asyncio
    from msldap.commons.factory import LDAPConnectionFactory

    url, base_dn = _build_msldap_url(db)

    async def _do():
        factory = LDAPConnectionFactory.from_url(url)
        client = factory.get_client()
        await client.connect()
        try:
            user_dn = await _resolve_dn_async(principal, client, base_dn)
            _, err = await client.del_user_from_group(user_dn, identifier)
            if err:
                err_s = str(err).lower()
                # Treat "already not a member" as success (idempotent)
                # AD error 0x561 (WILL_NOT_PERFORM / problem 5003) = user not in group
                if not any(s in err_s for s in ("no such", "not a member", "will_not_perform", "0x561", "5003")):
                    raise RuntimeError(f"LDAP del_user_from_group failed: {err}")
            return user_dn
        finally:
            await client.disconnect()

    user_dn = asyncio.run(_do())
    logger.info("AD group revoke OK: %s ← %s (dn=%s)", principal, identifier, user_dn)
    return {"success": True, "user_dn": user_dn}


def _revoke_entra_group(identifier: str, principal: str, db: Session) -> dict:
    """Removes principal from Entra group identifier."""
    raise NotImplementedError("Entra group revoke not yet implemented")


_GRANT_HANDLERS: dict[str, object] = {
    "ad_group": _grant_ad_group,
    "entra_group": _grant_entra_group,
}

_REVOKE_HANDLERS: dict[str, object] = {
    "ad_group": _revoke_ad_group,
    "entra_group": _revoke_entra_group,
}


# ── Public module functions ───────────────────────────────────────────────

def grant(
    db: Session,
    order_id: int,
    asset_type_id: int,
    user_email: str,
    rdp_users: list | None = None,
    admin_users: list | None = None,
    asset_name: str = "",
) -> dict:
    """Reads targets from asset_types, adds principals to groups,
    writes order change log.

    Identifiers may contain {asset_name} which is substituted with the
    assigned asset name (e.g. CN=XenPool-VDI-{asset_name}-RDP-Users,...).

    Returns:
        {"success": True, "grants": n}
        {"success": False, "grants": n, "errors": [...]}
    """
    row = db.execute(
        text("SELECT targets FROM asset_types WHERE id = :id"),
        {"id": asset_type_id},
    ).fetchone()

    if not row or not row[0]:
        logger.info("[target_executor] No targets defined for asset_type_id=%s", asset_type_id)
        return {"success": True, "grants": 0}

    targets: list[dict] = row[0]
    granted = 0
    errors: list[str] = []

    for target in targets:
        target_type = target.get("type", "")
        identifier = target.get("identifier", "").format(asset_name=asset_name)
        principal_source = target.get("principal_source", "requester")

        principals = _resolve_principals(
            principal_source,
            user_email or "",
            rdp_users or [],
            admin_users or [],
        )

        handler = _GRANT_HANDLERS.get(target_type)
        if not handler:
            logger.warning("[target_executor] Unknown target type: %s", target_type)
            _write_change_log(
                db, order_id, target_type, identifier, "grant",
                principal="(unknown)", state="failed",
                metadata={"error": "Unknown target type: " + target_type},
            )
            continue

        for principal in principals:
            ikey = f"order-{order_id}-{target_type}-{identifier}-{principal}"

            # Idempotency check: grant already executed successfully → skip
            existing = db.execute(
                text("""
                    SELECT 1 FROM order_change_log
                    WHERE idempotency_key = :k AND state = 'success'
                    LIMIT 1
                """),
                {"k": ikey},
            ).fetchone()
            if existing:
                logger.info("[target_executor] Skipping duplicate grant (idempotent): %s", ikey)
                granted += 1
                continue

            try:
                # Pass the full target dict so handlers can read per-target
                # flags like ``create_if_missing`` without needing a new
                # dispatch table entry per option.
                result = handler(identifier, principal, db, target=target)
                state = "success" if result.get("success") else "failed"
                _write_change_log(
                    db, order_id, target_type, identifier, "grant",
                    principal=principal, state=state,
                    metadata={},
                    idempotency_key=ikey,
                )
                if state == "success":
                    granted += 1
                else:
                    errors.append("grant " + target_type + ":" + identifier + " for " + principal + " failed")
            except Exception as e:
                logger.error(
                    "[target_executor] grant error: %s:%s principal=%s – %s",
                    target_type, identifier, principal, e,
                )
                _write_change_log(
                    db, order_id, target_type, identifier, "grant",
                    principal=principal, state="failed",
                    metadata={"error": str(e)},
                    idempotency_key=ikey,
                )
                errors.append(str(e))

    if errors:
        return {"success": False, "grants": granted, "errors": errors}
    return {"success": True, "grants": granted}


def revoke(
    db: Session,
    user_email: str,
    asset_type_id: int,
) -> dict:
    """Finds all successful grant entries for user_email + asset_type_id
    in the order change log and inverts them deterministically.

    Sets state = 'rolled_back' for successfully rolled back entries.

    Returns:
        {"success": True, "revokes": n}
        {"success": False, "revokes": n, "errors": [...]}
    """
    rows = db.execute(
        text("""
            SELECT cl.id, cl.target_type, cl.identifier, cl.principal
            FROM order_change_log cl
            JOIN orders o ON o.id = cl.order_id
            WHERE o.user_email = :email
              AND o.asset_type_id = :at
              AND cl.action = 'grant'
              AND cl.state = 'success'
            ORDER BY cl.id DESC
        """),
        {"email": user_email, "at": asset_type_id},
    ).fetchall()

    if not rows:
        logger.info(
            "[target_executor] No change log entries to revoke for user=%s asset_type=%s",
            user_email, asset_type_id,
        )
        return {"success": True, "revokes": 0}

    revoked = 0
    errors: list[str] = []

    for row in rows:
        log_id, target_type, identifier, principal = row[0], row[1], row[2], row[3]
        handler = _REVOKE_HANDLERS.get(target_type)

        if not handler:
            logger.warning("[target_executor] No revoke handler for type: %s", target_type)
            db.execute(
                text("UPDATE order_change_log SET state = 'failed' WHERE id = :id"),
                {"id": log_id},
            )
            db.commit()
            continue

        try:
            result = handler(identifier, principal, db)
            new_state = "rolled_back" if result.get("success") else "failed"
            db.execute(
                text("UPDATE order_change_log SET state = :state WHERE id = :id"),
                {"state": new_state, "id": log_id},
            )
            db.commit()
            if new_state == "rolled_back":
                revoked += 1
            else:
                errors.append("revoke " + target_type + ":" + identifier + " for " + principal + " failed")
        except Exception as e:
            logger.error(
                "[target_executor] revoke error: %s:%s principal=%s – %s",
                target_type, identifier, principal, e,
            )
            db.execute(
                text("UPDATE order_change_log SET state = 'failed' WHERE id = :id"),
                {"id": log_id},
            )
            db.commit()
            errors.append(str(e))

    if errors:
        return {"success": False, "revokes": revoked, "errors": errors}
    return {"success": True, "revokes": revoked}
