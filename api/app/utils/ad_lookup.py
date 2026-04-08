"""AD lookup helper for the API.

Lightweight version of the worker module active_directory.py.
Uses the same mock (all identifiers accepted) in dev mode.
In production: ldap3 (must be added to requirements.txt).

AD config is read from env vars first; if AD_SERVER is not set, falls back
to the app_config table (configured via Admin → Settings → Active Directory).
"""
import logging
import os

logger = logging.getLogger(__name__)

ENVIRONMENT = os.getenv("ENVIRONMENT", "development")

_MOCK_USERS: dict[str, dict] = {
    "s.muster": {
        "success": True,
        "email": "stefan.muster@xenpool.de",
        "display_name": "Stefan Muster",
        "sam_account": "s.muster",
    },
    "p.nutzer": {
        "success": True,
        "email": "peter.nutzer@xenpool.de",
        "display_name": "Peter Nutzer",
        "sam_account": "p.nutzer",
    },
}


def _load_ad_config_from_db() -> dict:
    """Read AD config from app_config table via synchronous psycopg2 connection."""
    database_url = os.getenv("DATABASE_URL", "")
    if not database_url:
        return {}
    # Convert asyncpg URL to psycopg2-compatible URL
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
    Returns AD config dict with keys: server, port, base_dn, domain, username, password.
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
        }

    # Fall back to app_config table
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
    }


def lookup_user(identifier: str) -> dict:
    """
    Looks up a user in Active Directory.

    Args:
        identifier: sAMAccountName or email

    Returns:
        {"success": True, "display_name": str, "email": str, "sam_account": str}
        {"success": False, "error": str}

    Falls back to mock mode in development. In production reads AD config from
    env vars or app_config table.
    """
    if not identifier.strip():
        return {"success": False, "error": "Empty input"}

    if ENVIRONMENT == "development":
        return _mock_lookup(identifier)

    try:
        import ldap3  # noqa: F401
    except ImportError:
        logger.error("[ad_lookup] ldap3 not installed — cannot validate users in production")
        return {"success": False, "error": "AD lookup not available (ldap3 not installed)"}

    ad_config = _get_ad_config()
    if not ad_config:
        logger.error("[ad_lookup] AD not configured — set AD_SERVER env var or configure via Admin → Settings → Active Directory")
        return {"success": False, "error": "AD lookup not configured"}

    return _ldap_lookup(identifier, ad_config)


def _mock_lookup(identifier: str) -> dict:
    sam = identifier.split("\\")[-1] if "\\" in identifier else identifier
    sam = sam.split("@")[0].lower().strip()

    if sam in _MOCK_USERS:
        result = _MOCK_USERS[sam].copy()
        logger.info("[MOCK] AD found: %s (%s)", result["display_name"], result["email"])
        return result

    # Generic fallback: accept identifier as valid user
    display = identifier.replace("\\", " ").replace(".", " ").replace("@", " ").title().strip()
    email = identifier if "@" in identifier else f"{sam}@xenpool.de"
    logger.info("[MOCK] AD fallback for '%s'", identifier)
    return {
        "success": True,
        "email": email,
        "display_name": display or identifier,
        "sam_account": sam,
    }


def _ldap_lookup(identifier: str, ad_config: dict) -> dict:
    try:
        import ldap3
        import ldap3.utils.conv
    except ImportError:
        return {"success": False, "error": "ldap3 not installed (add to requirements.txt)"}

    server_host = ad_config["server"]
    server_port = ad_config["port"]
    base_dn = ad_config["base_dn"]
    bind_user = ad_config["username"]
    bind_password = ad_config["password"]
    domain = ad_config["domain"]

    if "@" in identifier:
        esc = ldap3.utils.conv.escape_filter_chars(identifier)
        # Search mail OR userPrincipalName — AD often has UPN set but mail attribute empty
        ldap_filter = f"(|(mail={esc})(userPrincipalName={esc}))"
    else:
        sam = identifier.split("\\")[-1] if "\\" in identifier else identifier
        ldap_filter = f"(sAMAccountName={ldap3.utils.conv.escape_filter_chars(sam)})"

    bind_dn = f"{domain}\\{bind_user}" if domain else bind_user

    try:
        server = ldap3.Server(server_host, port=server_port, get_info=ldap3.NONE)
        conn = ldap3.Connection(server, user=bind_dn, password=bind_password, auto_bind=True)
        conn.search(
            search_base=base_dn,
            search_filter=ldap_filter,
            search_scope=ldap3.SUBTREE,
            attributes=["mail", "displayName", "sAMAccountName"],
        )
        if not conn.entries:
            return {"success": False, "error": f"Benutzer '{identifier}' nicht im AD gefunden"}

        entry = conn.entries[0]
        return {
            "success": True,
            "email": str(entry.mail) if entry.mail else identifier,
            "display_name": str(entry.displayName) if entry.displayName else identifier,
            "sam_account": str(entry.sAMAccountName) if entry.sAMAccountName else identifier,
        }
    except Exception as e:
        logger.error("LDAP lookup failed for '%s': %s", identifier, e)
        return {"success": False, "error": str(e)}
