"""AD lookup helper for the API.

Uses msldap to validate user identifiers (sAMAccountName or email) against
on-premises Active Directory.  msldap supports NTLM with message signing,
which modern Windows Server AD requires (LDAPServerIntegrity = Require signing).

AD config is read from env vars first; if AD_SERVER is not set, falls back
to the app_config table (configured via Admin -> Settings -> Active Directory).

When AD is not configured, lookup functions return an error indicating that
AD must be set up via the Admin UI.
"""
import asyncio
import logging
import os

logger = logging.getLogger(__name__)


def _load_ad_config_from_db() -> dict:
    """Read AD config from app_config table via synchronous psycopg2 connection."""
    database_url = os.getenv("DATABASE_URL", "")
    if not database_url:
        return {}
    sync_url = database_url.replace("postgresql+asyncpg://", "postgresql://")
    try:
        import psycopg2
        conn = psycopg2.connect(sync_url)
        cur = conn.cursor()
        cur.execute("SELECT key, value FROM app_config WHERE key LIKE 'ad.%'")
        rows = cur.fetchall()
        cur.close()
        conn.close()
        return {row[0]: row[1] for row in rows}
    except Exception as e:
        logger.warning("[ad_lookup] Could not read app_config from DB: %s", e)
        return {}


def _get_ad_config() -> dict:
    """
    Returns AD config dict with keys: server, port, base_dn, domain, username, password, use_ssl.
    Reads from env vars first; falls back to app_config table.
    Returns empty dict if neither source has AD_SERVER / ad.server.
    """
    server = os.getenv("AD_SERVER", "")
    if server:
        cfg = {
            "server": server,
            "port": int(os.getenv("AD_PORT", "389")),
            "base_dn": os.getenv("AD_BASE_DN", ""),
            "domain": os.getenv("AD_DOMAIN", ""),
            "username": os.getenv("AD_USERNAME", ""),
            "password": os.getenv("AD_PASSWORD", ""),
            "use_ssl": os.getenv("AD_USE_SSL", "false").lower() == "true",
        }
        # Attribute mapping always comes from the DB, even when server creds
        # are env-driven, so admins can change attribute names without a redeploy.
        cfg.update(_load_attribute_mapping_from_db())
        return cfg

    cfg = _load_ad_config_from_db()
    if not cfg.get("ad.server"):
        return {}
    # External-secret resolution: ``ad.password`` may be a literal or a
    # ``vault://...`` / ``ccp://...`` reference. Plain strings pass
    # through unchanged so existing installs keep working.
    from app.utils.secrets import resolve_secret_value_sync
    raw_password = cfg.get("ad.password", "")
    return {
        "server": cfg.get("ad.server", ""),
        "port": int(cfg.get("ad.port", "389")),
        "base_dn": cfg.get("ad.base_dn", ""),
        "domain": cfg.get("ad.domain", ""),
        "username": cfg.get("ad.username", ""),
        "password": resolve_secret_value_sync(raw_password),
        "use_ssl": cfg.get("ad.use_ssl", "false") == "true",
        "attr_department":  (cfg.get("ad.attribute.department")  or "department").strip(),
        "attr_cost_center": (cfg.get("ad.attribute.cost_center") or "").strip(),
        "attr_company":     (cfg.get("ad.attribute.company")     or "company").strip(),
        "attr_employee_id": (cfg.get("ad.attribute.employee_id") or "employeeID").strip(),
        "attr_title":       (cfg.get("ad.attribute.title")       or "title").strip(),
    }


def _load_attribute_mapping_from_db() -> dict:
    """Return ``{"attr_department": "department", ...}`` from app_config."""
    raw = _load_ad_config_from_db()
    return {
        "attr_department":  (raw.get("ad.attribute.department")  or "department").strip(),
        "attr_cost_center": (raw.get("ad.attribute.cost_center") or "").strip(),
        "attr_company":     (raw.get("ad.attribute.company")     or "company").strip(),
        "attr_employee_id": (raw.get("ad.attribute.employee_id") or "employeeID").strip(),
        "attr_title":       (raw.get("ad.attribute.title")       or "title").strip(),
    }


def lookup_user(identifier: str) -> dict:
    """
    Looks up a user in Active Directory.

    Args:
        identifier: sAMAccountName or email

    Returns:
        {"success": True, "display_name": str, "email": str, "sam_account": str}
        {"success": False, "error": str}

    When AD is not configured, accepts the identifier as-is so the portal
    remains functional during initial setup (logs a warning).
    """
    if not identifier.strip():
        return {"success": False, "error": "Empty input"}

    ad_config = _get_ad_config()
    if not ad_config:
        return {"success": False, "error": "Active Directory is not configured. Configure AD via Admin > Settings > Active Directory."}

    return _msldap_lookup_sync(identifier, ad_config)


def snapshot_requester_attrs(email: str) -> dict[str, str | None]:
    """Return Order ``requester_*`` columns from an AD lookup on ``email``.

    Used to populate the chargeback snapshot at order-creation time so
    the cost report can slice spend by consuming team without
    re-querying AD per report build. Best-effort: every failure path
    (empty input, AD not configured, lookup error, unsuccessful
    response) returns an empty dict so the caller can splat ``**``
    onto an ``Order`` constructor without ever blocking the order.
    Logs at WARNING when AD is configured but the lookup itself fails.
    """
    if not email or not email.strip():
        return {}
    try:
        ad_lookup = lookup_user(email)
    except Exception as exc:  # noqa: BLE001
        logger.warning("AD attribute snapshot failed for %s: %s", email, exc)
        return {}
    if not ad_lookup.get("success"):
        return {}
    return {
        "requester_sam_account": ad_lookup.get("sam_account") or None,
        "requester_department":  ad_lookup.get("department") or None,
        "requester_cost_center": ad_lookup.get("cost_center") or None,
        "requester_company":     ad_lookup.get("company") or None,
        "requester_employee_id": ad_lookup.get("employee_id") or None,
        "requester_title":       ad_lookup.get("title") or None,
    }


def lookup_manager(identifier: str) -> dict:
    """
    Looks up the manager of a user in Active Directory.

    Args:
        identifier: sAMAccountName or email of the user whose manager to look up

    Returns:
        {"success": True, "manager": {"email": str, "display_name": str, "sam_account": str}}
        {"success": True, "manager": None}  -- user found but no manager set
        {"success": False, "error": str}
    """
    if not identifier.strip():
        return {"success": False, "error": "Empty input"}

    ad_config = _get_ad_config()
    if not ad_config:
        return {"success": False, "error": "Active Directory is not configured. Configure AD via Admin > Settings > Active Directory."}

    return _msldap_manager_sync(identifier, ad_config)


def _msldap_manager_sync(identifier: str, ad_config: dict) -> dict:
    """Synchronous wrapper around the async manager lookup."""
    try:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop and loop.is_running():
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(asyncio.run, _msldap_lookup_manager(identifier, ad_config))
                return future.result(timeout=15)
        else:
            return asyncio.run(_msldap_lookup_manager(identifier, ad_config))
    except Exception as e:
        logger.error("AD manager lookup failed for '%s': %s", identifier, e)
        return {"success": False, "error": f"AD connection error: {e}"}


async def _msldap_lookup_manager(identifier: str, ad_config: dict) -> dict:
    """Look up a user's manager DN, then resolve the manager's identity."""
    from msldap.commons.factory import LDAPConnectionFactory
    from urllib.parse import quote

    server_host = ad_config["server"]
    server_port = ad_config["port"]
    base_dn = ad_config["base_dn"]
    bind_user = ad_config["username"]
    bind_password = ad_config["password"]
    domain = ad_config["domain"]
    use_ssl = ad_config.get("use_ssl", False)

    if "@" in identifier:
        ldap_filter = f"(|(mail={identifier})(userPrincipalName={identifier}))"
    else:
        sam = identifier.split("\\")[-1] if "\\" in identifier else identifier
        ldap_filter = f"(sAMAccountName={sam})"

    scheme = "ldaps+ntlm-password" if use_ssl else "ldap+ntlm-password"
    user_escaped = quote(f"{domain}\\{bind_user}" if domain else bind_user, safe="")
    pass_escaped = quote(bind_password, safe="")
    url = f"{scheme}://{user_escaped}:{pass_escaped}@{server_host}:{server_port}"

    factory = LDAPConnectionFactory.from_url(url)
    client = factory.get_client()
    await client.connect()

    try:
        # Step 1: Find user and get manager DN
        attrs = ["manager"]
        found = None
        async for entry, err in client.pagedsearch(ldap_filter, attrs, tree=base_dn):
            if err:
                return {"success": False, "error": str(err)}
            found = entry["attributes"]
            break

        if found is None:
            return {"success": False, "error": f"User '{identifier}' not found in AD"}

        manager_dn = found.get("manager")
        if isinstance(manager_dn, list):
            manager_dn = manager_dn[0] if manager_dn else None
        if manager_dn:
            manager_dn = str(manager_dn)

        if not manager_dn:
            return {"success": True, "manager": None}

        # Step 2: Resolve manager DN to get their identity
        mgr_attrs = ["mail", "displayName", "sAMAccountName", "userPrincipalName"]
        mgr_filter = f"(distinguishedName={manager_dn})"
        mgr_found = None
        async for entry, err in client.pagedsearch(mgr_filter, mgr_attrs, tree=base_dn):
            if err:
                return {"success": False, "error": f"Manager DN lookup error: {err}"}
            mgr_found = entry["attributes"]
            break

        if not mgr_found:
            return {"success": True, "manager": None}

        def _attr(key):
            v = mgr_found.get(key)
            if isinstance(v, list):
                return v[0] if v else None
            return str(v) if v else None

        return {
            "success": True,
            "manager": {
                "email": _attr("mail") or _attr("userPrincipalName") or "",
                "display_name": _attr("displayName") or "",
                "sam_account": _attr("sAMAccountName") or "",
            },
        }
    finally:
        await client.disconnect()


def _ldap_escape_filter(value: str) -> str:
    """Escape a value for safe interpolation into an LDAP search filter (RFC 4515).

    Needed when we put a distinguishedName (which may contain parentheses,
    commas, backslashes) into a ``(manager=<dn>)`` filter.
    """
    out = []
    for ch in value:
        if ch == "\\":
            out.append("\\5c")
        elif ch == "*":
            out.append("\\2a")
        elif ch == "(":
            out.append("\\28")
        elif ch == ")":
            out.append("\\29")
        elif ch == "\x00":
            out.append("\\00")
        else:
            out.append(ch)
    return "".join(out)


def lookup_direct_reports(identifier: str) -> dict:
    """Return the direct reports (team) of a manager from Active Directory.

    The inverse of :func:`lookup_manager`: resolve the manager's DN, then
    find every user whose ``manager`` attribute points at it.

    Returns:
        {"success": True, "reports": [{"email", "display_name", "sam_account"}, ...]}
        {"success": False, "error": str}
    """
    if not identifier.strip():
        return {"success": False, "error": "Empty input"}
    ad_config = _get_ad_config()
    if not ad_config:
        return {"success": False, "error": "Active Directory is not configured. Configure AD via Admin > Settings > Active Directory."}
    return _msldap_direct_reports_sync(identifier, ad_config)


def is_owner_managed_by(requester_email: str, owner_email: str) -> dict:
    """Verify that ``requester_email`` is the AD manager of ``owner_email``.

    Used to gate the "manager orders on behalf of a report" flow: the
    relationship is confirmed against AD (the requester can't just claim to
    be someone's manager). Returns a dict so callers can distinguish a
    genuine "not the manager" from an AD error.

    Returns:
        {"success": True, "is_manager": bool}
        {"success": False, "error": str}
    """
    if not requester_email.strip() or not owner_email.strip():
        return {"success": False, "error": "Empty input"}
    if requester_email.strip().lower() == owner_email.strip().lower():
        # Ordering for yourself is not a manager-for-report relationship.
        return {"success": True, "is_manager": False}
    mgr = lookup_manager(owner_email)
    if not mgr.get("success"):
        return {"success": False, "error": mgr.get("error", "AD lookup failed")}
    manager = mgr.get("manager")
    if not manager:
        return {"success": True, "is_manager": False}
    mgr_email = (manager.get("email") or "").strip().lower()
    return {"success": True, "is_manager": mgr_email == requester_email.strip().lower()}


def _msldap_direct_reports_sync(identifier: str, ad_config: dict) -> dict:
    """Synchronous wrapper around the async direct-reports lookup."""
    try:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop and loop.is_running():
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(asyncio.run, _msldap_direct_reports(identifier, ad_config))
                return future.result(timeout=15)
        else:
            return asyncio.run(_msldap_direct_reports(identifier, ad_config))
    except Exception as e:
        logger.error("AD direct-reports lookup failed for '%s': %s", identifier, e)
        return {"success": False, "error": f"AD connection error: {e}"}


async def _msldap_direct_reports(identifier: str, ad_config: dict) -> dict:
    """Resolve the manager's DN, then page through users whose ``manager`` = that DN."""
    from msldap.commons.factory import LDAPConnectionFactory
    from urllib.parse import quote

    server_host = ad_config["server"]
    server_port = ad_config["port"]
    base_dn = ad_config["base_dn"]
    bind_user = ad_config["username"]
    bind_password = ad_config["password"]
    domain = ad_config["domain"]
    use_ssl = ad_config.get("use_ssl", False)

    if "@" in identifier:
        ldap_filter = f"(|(mail={identifier})(userPrincipalName={identifier}))"
    else:
        sam = identifier.split("\\")[-1] if "\\" in identifier else identifier
        ldap_filter = f"(sAMAccountName={sam})"

    scheme = "ldaps+ntlm-password" if use_ssl else "ldap+ntlm-password"
    user_escaped = quote(f"{domain}\\{bind_user}" if domain else bind_user, safe="")
    pass_escaped = quote(bind_password, safe="")
    url = f"{scheme}://{user_escaped}:{pass_escaped}@{server_host}:{server_port}"

    factory = LDAPConnectionFactory.from_url(url)
    client = factory.get_client()
    await client.connect()

    try:
        # Step 1: resolve the manager's distinguishedName.
        mgr_dn = None
        async for entry, err in client.pagedsearch(ldap_filter, ["distinguishedName"], tree=base_dn):
            if err:
                return {"success": False, "error": str(err)}
            if not entry:
                continue
            a = entry.get("attributes") or {}
            dn = a.get("distinguishedName")
            if isinstance(dn, list):
                dn = dn[0] if dn else None
            mgr_dn = str(dn) if dn else None
            break

        if not mgr_dn:
            return {"success": False, "error": f"User '{identifier}' not found in AD"}

        # Step 2: find every user whose manager attribute points at that DN.
        reports_filter = f"(manager={_ldap_escape_filter(mgr_dn)})"
        attrs = ["mail", "displayName", "sAMAccountName", "userPrincipalName"]
        reports: list[dict] = []
        async for entry, err in client.pagedsearch(reports_filter, attrs, tree=base_dn):
            if err:
                return {"success": False, "error": str(err)}
            if not entry:
                continue
            a = entry.get("attributes") or {}

            def _attr(key):
                v = a.get(key)
                if isinstance(v, list):
                    v = v[0] if v else None
                if v is None or v == "" or v == b"":
                    return None
                if isinstance(v, bytes):
                    return v.decode("utf-8", errors="replace")
                return str(v)

            email = _attr("mail") or _attr("userPrincipalName")
            if not email:
                continue
            reports.append({
                "email": email,
                "display_name": _attr("displayName") or email,
                "sam_account": _attr("sAMAccountName") or "",
            })

        reports.sort(key=lambda r: (r["display_name"] or "").lower())
        return {"success": True, "reports": reports}
    finally:
        await client.disconnect()


def check_group_membership(identifier: str, group_dn: str) -> dict:
    """
    Check if a user is a member of an AD group (recursive/transitive).

    Args:
        identifier: sAMAccountName or email of the user
        group_dn: Distinguished Name of the group

    Returns:
        {"success": True, "is_member": bool}
        {"success": False, "error": str}

    When AD is not configured (dev mode), always returns is_member=True.
    """
    if not identifier.strip() or not group_dn.strip():
        return {"success": False, "error": "Empty input"}

    ad_config = _get_ad_config()
    if not ad_config:
        return {"success": False, "error": "Active Directory is not configured. Configure AD via Admin > Settings > Active Directory."}

    return _msldap_check_membership_sync(identifier, group_dn, ad_config)


def _msldap_check_membership_sync(identifier: str, group_dn: str, ad_config: dict) -> dict:
    """Synchronous wrapper around the async membership check."""
    try:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop and loop.is_running():
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(asyncio.run, _msldap_check_membership(identifier, group_dn, ad_config))
                return future.result(timeout=15)
        else:
            return asyncio.run(_msldap_check_membership(identifier, group_dn, ad_config))
    except Exception as e:
        logger.error("AD group membership check failed for '%s' in '%s': %s", identifier, group_dn, e)
        return {"success": False, "error": f"AD connection error: {e}"}


async def _msldap_check_membership(identifier: str, group_dn: str, ad_config: dict) -> dict:
    """Check transitive group membership using LDAP_MATCHING_RULE_IN_CHAIN."""
    from msldap.commons.factory import LDAPConnectionFactory
    from urllib.parse import quote

    server_host = ad_config["server"]
    server_port = ad_config["port"]
    base_dn = ad_config["base_dn"]
    bind_user = ad_config["username"]
    bind_password = ad_config["password"]
    domain = ad_config["domain"]
    use_ssl = ad_config.get("use_ssl", False)

    # Build filter to find the user AND check transitive membership in one query.
    # OID 1.2.840.113556.1.4.1941 = LDAP_MATCHING_RULE_IN_CHAIN (recursive memberOf)
    if "@" in identifier:
        user_part = f"(|(mail={identifier})(userPrincipalName={identifier}))"
    else:
        sam = identifier.split("\\")[-1] if "\\" in identifier else identifier
        user_part = f"(sAMAccountName={sam})"

    ldap_filter = f"(&{user_part}(memberOf:1.2.840.113556.1.4.1941:={group_dn}))"

    scheme = "ldaps+ntlm-password" if use_ssl else "ldap+ntlm-password"
    user_escaped = quote(f"{domain}\\{bind_user}" if domain else bind_user, safe="")
    pass_escaped = quote(bind_password, safe="")
    url = f"{scheme}://{user_escaped}:{pass_escaped}@{server_host}:{server_port}"

    factory = LDAPConnectionFactory.from_url(url)
    client = factory.get_client()
    await client.connect()

    try:
        found = False
        async for entry, err in client.pagedsearch(ldap_filter, ["sAMAccountName"], tree=base_dn):
            if err:
                return {"success": False, "error": str(err)}
            found = True
            break

        return {"success": True, "is_member": found}
    finally:
        await client.disconnect()


def authenticate_user(username: str, password: str) -> dict:
    """Authenticate a portal user by binding to LDAP with their own credentials.

    Used by the ``onprem_ldap`` portal auth mode. The bind uses NTLM with the
    user's own password — no service account involved for the auth step itself.
    Attributes are fetched in the same connection after a successful bind.

    Returns:
        {"success": True, "email": str, "name": str, "sam_account": str, "upn": str}
        {"success": False, "error": str}
    """
    if not username.strip() or not password:
        return {"success": False, "error": "Username and password are required"}

    ad_config = _get_ad_config()
    if not ad_config:
        return {"success": False, "error": "Active Directory is not configured. Set up AD via Admin > Settings > Active Directory."}

    return _ldap_auth_sync(username.strip(), password, ad_config)


def _ldap_auth_sync(username: str, password: str, ad_config: dict) -> dict:
    """Sync wrapper — same threadpool pattern as the rest of ad_lookup."""
    try:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop and loop.is_running():
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(asyncio.run, _ldap_auth(username, password, ad_config))
                return future.result(timeout=15)
        else:
            return asyncio.run(_ldap_auth(username, password, ad_config))
    except Exception as e:
        logger.error("LDAP auth failed for '%s': %s", username, e)
        return {"success": False, "error": f"Authentication error: {e}"}


async def _ldap_auth(username: str, password: str, ad_config: dict) -> dict:
    """LDAP bind with the user's own credentials, then fetch identity attributes."""
    from msldap.commons.factory import LDAPConnectionFactory
    from urllib.parse import quote

    server_host = ad_config["server"]
    server_port = ad_config["port"]
    base_dn = ad_config["base_dn"]
    domain = ad_config["domain"]
    use_ssl = ad_config.get("use_ssl", False)

    # Accept DOMAIN\user, user@domain, or bare username
    if "@" in username:
        sam = username.split("@")[0]
    elif "\\" in username:
        sam = username.split("\\")[-1]
    else:
        sam = username

    scheme = "ldaps+ntlm-password" if use_ssl else "ldap+ntlm-password"
    user_escaped = quote(f"{domain}\\{sam}" if domain else sam, safe="")
    pass_escaped = quote(password, safe="")
    url = f"{scheme}://{user_escaped}:{pass_escaped}@{server_host}:{server_port}"

    factory = LDAPConnectionFactory.from_url(url)
    client = factory.get_client()

    _AUTH_FAIL_KEYWORDS = ("invalid credentials", "logon failure", "unwilling", "wrong password", "52e", "525", "not bound")

    try:
        await client.connect()
    except Exception as e:
        err_lower = str(e).lower()
        if any(k in err_lower for k in _AUTH_FAIL_KEYWORDS):
            return {"success": False, "error": "Invalid username or password"}
        return {"success": False, "error": f"LDAP connection failed: {e}"}

    try:
        ldap_filter = f"(sAMAccountName={sam})"
        attrs = ["mail", "displayName", "sAMAccountName", "userPrincipalName"]
        found = None
        async for entry, err in client.pagedsearch(ldap_filter, attrs, tree=base_dn):
            if err:
                err_lower = str(err).lower()
                if any(k in err_lower for k in _AUTH_FAIL_KEYWORDS):
                    return {"success": False, "error": "Invalid username or password"}
                return {"success": False, "error": str(err)}
            found = entry["attributes"]
            break

        if not found:
            return {"success": False, "error": "User account not found in directory"}

        def _attr(key):
            v = found.get(key)
            if isinstance(v, list):
                v = v[0] if v else None
            if v is None:
                return None
            if isinstance(v, bytes):
                return v.decode("utf-8", errors="replace")
            return str(v)

        upn = _attr("userPrincipalName") or ""
        email = _attr("mail") or upn or (f"{sam}@{domain}" if domain else sam)
        return {
            "success": True,
            "email": email,
            "name": _attr("displayName") or sam,
            "sam_account": _attr("sAMAccountName") or sam,
            "upn": upn,
        }
    finally:
        await client.disconnect()


def _msldap_lookup_sync(identifier: str, ad_config: dict) -> dict:
    """Synchronous wrapper around the async msldap lookup."""
    try:
        # Get or create an event loop — handles both threaded and main-thread contexts
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop and loop.is_running():
            # We're inside an async context (e.g. FastAPI) — run in a new thread
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(asyncio.run, _msldap_lookup(identifier, ad_config))
                return future.result(timeout=15)
        else:
            return asyncio.run(_msldap_lookup(identifier, ad_config))
    except Exception as e:
        logger.error("AD lookup failed for '%s': %s", identifier, e)
        return {"success": False, "error": f"AD connection error: {e}"}


async def _msldap_lookup(identifier: str, ad_config: dict) -> dict:
    from msldap.commons.factory import LDAPConnectionFactory
    from urllib.parse import quote

    server_host = ad_config["server"]
    server_port = ad_config["port"]
    base_dn = ad_config["base_dn"]
    bind_user = ad_config["username"]
    bind_password = ad_config["password"]
    domain = ad_config["domain"]
    use_ssl = ad_config.get("use_ssl", False)

    # Build LDAP filter
    if "@" in identifier:
        ldap_filter = f"(|(mail={identifier})(userPrincipalName={identifier}))"
    else:
        sam = identifier.split("\\")[-1] if "\\" in identifier else identifier
        ldap_filter = f"(sAMAccountName={sam})"

    # Build msldap connection URL
    scheme = "ldaps+ntlm-password" if use_ssl else "ldap+ntlm-password"
    user_escaped = quote(f"{domain}\\{bind_user}" if domain else bind_user, safe="")
    pass_escaped = quote(bind_password, safe="")
    url = f"{scheme}://{user_escaped}:{pass_escaped}@{server_host}:{server_port}"

    factory = LDAPConnectionFactory.from_url(url)
    client = factory.get_client()
    await client.connect()

    try:
        # Mandatory identity attrs + the configurable HR attrs needed for
        # chargeback. Empty mapping entries (cost_center default = "") are
        # filtered out so we don't ask AD for a literal "" attribute.
        identity_attrs = ["mail", "displayName", "sAMAccountName", "userPrincipalName"]
        hr_attr_keys = [
            ad_config.get("attr_department"),
            ad_config.get("attr_cost_center"),
            ad_config.get("attr_company"),
            ad_config.get("attr_employee_id"),
            ad_config.get("attr_title"),
        ]
        attrs = list(dict.fromkeys(identity_attrs + [a for a in hr_attr_keys if a]))

        found = None
        async for entry, err in client.pagedsearch(ldap_filter, attrs, tree=base_dn):
            if err:
                return {"success": False, "error": str(err)}
            found = entry["attributes"]
            break

        if not found:
            return {"success": False, "error": f"User '{identifier}' not found in AD"}

        def _attr(key):
            """Extract a single string value from the LDAP entry.

            msldap auto-decodes standard schema attributes (displayName,
            department, …) to ``str``, but custom attributes whose syntax
            it doesn't know (e.g. ``userCostCenter``) come back as raw
            ``bytes``. Always decode to UTF-8 so the value is safe to
            persist via asyncpg.
            """
            if not key:
                return None
            v = found.get(key)
            if isinstance(v, list):
                v = v[0] if v else None
            if v is None or v == "" or v == b"":
                return None
            if isinstance(v, bytes):
                try:
                    return v.decode("utf-8")
                except UnicodeDecodeError:
                    return v.decode("latin-1", errors="replace")
            return str(v)

        return {
            "success": True,
            "email": _attr("mail") or _attr("userPrincipalName") or identifier,
            "display_name": _attr("displayName") or identifier,
            "sam_account": _attr("sAMAccountName") or identifier,
            "department":  _attr(ad_config.get("attr_department")),
            "cost_center": _attr(ad_config.get("attr_cost_center")),
            "company":     _attr(ad_config.get("attr_company")),
            "employee_id": _attr(ad_config.get("attr_employee_id")),
            "title":       _attr(ad_config.get("attr_title")),
        }
    finally:
        await client.disconnect()
