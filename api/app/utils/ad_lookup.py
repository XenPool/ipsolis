"""AD lookup helper for the API.

Uses msldap to validate user identifiers (sAMAccountName or email) against
on-premises Active Directory.  msldap supports NTLM with message signing,
which modern Windows Server AD requires (LDAPServerIntegrity = Require signing).

AD config is read from env vars first; if AD_SERVER is not set, falls back
to the app_config table (configured via Admin -> Settings -> Active Directory).

When AD is not configured, all identifiers are accepted with a warning log
so the portal remains usable during initial setup.
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
        return {
            "server": server,
            "port": int(os.getenv("AD_PORT", "389")),
            "base_dn": os.getenv("AD_BASE_DN", ""),
            "domain": os.getenv("AD_DOMAIN", ""),
            "username": os.getenv("AD_USERNAME", ""),
            "password": os.getenv("AD_PASSWORD", ""),
            "use_ssl": os.getenv("AD_USE_SSL", "false").lower() == "true",
        }

    cfg = _load_ad_config_from_db()
    if not cfg.get("ad.server"):
        return {}
    return {
        "server": cfg.get("ad.server", ""),
        "port": int(cfg.get("ad.port", "389")),
        "base_dn": cfg.get("ad.base_dn", ""),
        "domain": cfg.get("ad.domain", ""),
        "username": cfg.get("ad.username", ""),
        "password": cfg.get("ad.password", ""),
        "use_ssl": cfg.get("ad.use_ssl", "false") == "true",
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
        logger.warning("[ad_lookup] AD not configured — accepting '%s' without validation. "
                       "Configure AD via Admin -> Settings -> Active Directory.", identifier)
        return _accept_without_validation(identifier)

    return _msldap_lookup_sync(identifier, ad_config)


def _accept_without_validation(identifier: str) -> dict:
    """Accept an identifier when AD is not available. Derive display values from the input."""
    sam = identifier.split("\\")[-1] if "\\" in identifier else identifier
    sam = sam.split("@")[0].lower().strip()
    email = identifier if "@" in identifier else ""
    display = identifier.replace("\\", " ").replace(".", " ").replace("@", " at ").strip().title()
    return {
        "success": True,
        "email": email or identifier,
        "display_name": display or identifier,
        "sam_account": sam,
    }


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
        attrs = ["mail", "displayName", "sAMAccountName", "userPrincipalName"]
        found = None
        async for entry, err in client.pagedsearch(ldap_filter, attrs, tree=base_dn):
            if err:
                return {"success": False, "error": str(err)}
            found = entry["attributes"]
            break

        if not found:
            return {"success": False, "error": f"User '{identifier}' not found in AD"}

        def _attr(key):
            v = found.get(key)
            if isinstance(v, list):
                return v[0] if v else None
            return str(v) if v else None

        return {
            "success": True,
            "email": _attr("mail") or _attr("userPrincipalName") or identifier,
            "display_name": _attr("displayName") or identifier,
            "sam_account": _attr("sAMAccountName") or identifier,
        }
    finally:
        await client.disconnect()
