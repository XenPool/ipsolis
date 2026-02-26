"""Module Registry – Zentrale Liste aller verfügbaren Module für den Dynamic Runner.

Jedes Modul hat:
- fn:          Aufrufbare Funktion
- needs_db:    True wenn die Funktion eine DB-Session als erstes Argument erwartet
- description: Kurzbeschreibung für die Admin-UI
- params:      Liste der erwarteten Parameter-Namen (aus params_template)
- output_keys: Schlüssel im Result-Dict die in den Execution-Kontext übernommen werden
- group:       Gruppierung für Admin-UI-Dropdowns
"""

from tasks.modules import active_roles, notifications, pool_manager, sccm, target_executor, vsphere


# ── Adapter-Funktionen für Notifications ──────────────────────────────────────
# Die Original-Funktionen haben komplexe Signaturen; diese Adapter nehmen
# flache kwargs und delegieren korrekt.

def _notify_send_confirmation(
    db,
    user_email: str,
    user_name: str,
    owner_email: str | None = None,
    owner_name: str | None = None,
    asset_type_name: str = "",
    asset_type_description: str | None = None,
    requested_from=None,
    expires_at=None,
    snow_req: str | None = None,
    snow_ritm: str | None = None,
) -> dict:
    from datetime import datetime, timezone
    if requested_from is None:
        requested_from = datetime.now(timezone.utc)
    if expires_at is None:
        expires_at = datetime.now(timezone.utc)
    return notifications.send_order_confirmation(
        db=db,
        user_email=user_email or "",
        user_name=user_name or "",
        owner_email=owner_email,
        owner_name=owner_name,
        asset_type_name=asset_type_name or "",
        asset_type_description=asset_type_description or "",
        requested_from=requested_from,
        requested_until=expires_at,
        snow_req=snow_req,
        snow_ritm=snow_ritm,
    )


def _notify_send_provision(
    user_email: str,
    user_name: str,
    asset_name: str,
    rdp_users: list | None = None,
    expires_at=None,
) -> dict:
    from datetime import datetime, timezone
    if expires_at is None:
        expires_at = datetime.now(timezone.utc)
    return notifications.send_provision_confirmation(
        user_email=user_email or "",
        user_name=user_name or "",
        asset_name=asset_name or "",
        rdp_users=rdp_users or [],
        expires_at=expires_at,
    )


def _notify_send_reclaim(
    user_email: str,
    user_name: str,
    asset_name: str,
) -> dict:
    return notifications.send_reclaim_notification(
        user_email=user_email or "",
        user_name=user_name or "",
        asset_name=asset_name or "",
    )


# ── Registry ──────────────────────────────────────────────────────────────────

MODULE_REGISTRY: dict[str, dict] = {

    # ── Pool ──────────────────────────────────────────────────────────────────
    "pool.reserve_asset": {
        "fn": pool_manager.reserve_asset,
        "needs_db": True,
        "description": "Reserviert ein freies Asset aus dem Pool für die Bestellung",
        "params": ["order_id", "asset_type_id", "expires_at"],
        "output_keys": ["asset_id", "asset_name"],
        "group": "pool",
    },
    "pool.check_capacity": {
        "fn": pool_manager.check_capacity,
        "needs_db": True,
        "description": "Prüft ob Pool-Kapazität für pooled Assets noch frei ist",
        "params": ["asset_type_id", "pool_capacity"],
        "output_keys": [],
        "group": "pool",
    },
    "pool.set_asset_busy": {
        "fn": pool_manager.set_asset_busy,
        "needs_db": True,
        "description": "Setzt ein Asset auf BUSY (nach Bereitstellung oder Verlängerung)",
        "params": ["asset_id", "order_id", "expires_at"],
        "output_keys": [],
        "group": "pool",
    },
    "pool.release_asset": {
        "fn": pool_manager.release_asset,
        "needs_db": True,
        "description": "Gibt ein Asset zurück in den Pool (Status: FREE)",
        "params": ["asset_id"],
        "output_keys": [],
        "group": "pool",
    },

    # ── Active Roles ──────────────────────────────────────────────────────────
    "active_roles.set_rdp_group": {
        "fn": active_roles.set_rdp_group,
        "needs_db": False,
        "description": "Befüllt die RDP-AD-Gruppe der VM mit den angegebenen Benutzern",
        "params": ["asset_name", "rdp_users"],
        "output_keys": [],
        "group": "active_roles",
    },
    "active_roles.set_admin_group": {
        "fn": active_roles.set_admin_group,
        "needs_db": False,
        "description": "Befüllt die Admin-AD-Gruppe der VM mit den angegebenen Benutzern",
        "params": ["asset_name", "admin_users"],
        "output_keys": [],
        "group": "active_roles",
    },
    "active_roles.remove_all_groups": {
        "fn": active_roles.remove_all_groups,
        "needs_db": False,
        "description": "Entfernt alle AD-Gruppen der VM (bei Rückgabe)",
        "params": ["asset_name"],
        "output_keys": [],
        "group": "active_roles",
    },

    # ── vSphere ───────────────────────────────────────────────────────────────
    "vsphere.update_vmware_tools": {
        "fn": vsphere.update_vmware_tools,
        "needs_db": False,
        "description": "Aktualisiert VMware Tools auf der VM via PowerCLI",
        "params": ["asset_name"],
        "output_keys": [],
        "group": "vsphere",
    },
    "vsphere.restart_vm": {
        "fn": vsphere.restart_vm,
        "needs_db": False,
        "description": "Startet die VM neu via vSphere",
        "params": ["asset_name"],
        "output_keys": [],
        "group": "vsphere",
    },

    # ── SCCM ──────────────────────────────────────────────────────────────────
    "sccm.trigger_reinstall": {
        "fn": sccm.trigger_reinstall,
        "needs_db": False,
        "description": "Löst SCCM-Tasksequenz für unattended VM-Neuinstallation aus",
        "params": ["asset_name"],
        "output_keys": [],
        "group": "sccm",
    },

    # ── Notifications ─────────────────────────────────────────────────────────
    "notifications.send_confirmation": {
        "fn": _notify_send_confirmation,
        "needs_db": True,
        "description": "Sendet zweisprachige Bestellbestätigung an Besteller und Owner",
        "params": [
            "user_email", "user_name", "owner_email", "owner_name",
            "asset_type_name", "asset_type_description",
            "requested_from", "expires_at", "snow_req", "snow_ritm",
        ],
        "output_keys": [],
        "group": "notifications",
    },
    "notifications.send_provision_confirmation": {
        "fn": _notify_send_provision,
        "needs_db": False,
        "description": "Sendet Bereitstellungsbestätigung mit VM-Name und RDP-Zugang",
        "params": ["user_email", "user_name", "asset_name", "rdp_users", "expires_at"],
        "output_keys": [],
        "group": "notifications",
    },
    "notifications.send_reclaim": {
        "fn": _notify_send_reclaim,
        "needs_db": False,
        "description": "Benachrichtigt den User über die Rückführung seiner VM in den Pool",
        "params": ["user_email", "user_name", "asset_name"],
        "output_keys": [],
        "group": "notifications",
    },

    # ── Target Executor ───────────────────────────────────────────────────────
    "target_executor.grant": {
        "fn": target_executor.grant,
        "needs_db": True,
        "description": "Liest targets aus asset_types und fügt Principals zu Gruppen hinzu (AD/Entra)",
        "params": ["order_id", "asset_type_id", "user_email", "rdp_users", "admin_users"],
        "output_keys": [],
        "group": "target_executor",
    },
    "target_executor.revoke": {
        "fn": target_executor.revoke,
        "needs_db": True,
        "description": "Invertiert alle grant-Einträge aus dem Change-Log (deterministisches Revoke)",
        "params": ["user_email", "asset_type_id"],
        "output_keys": [],
        "group": "target_executor",
    },
}
