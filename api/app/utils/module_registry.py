"""Metadaten-Spiegel der Module-Registry für die Admin-UI.

Enthält dieselbe Struktur wie worker/tasks/modules/registry.py,
aber ohne Funktionsreferenzen – nur für UI-Dropdowns und Dokumentation.
"""

# Gruppierte Modul-Metadaten (kein Import aus worker – API hat keinen Zugriff)
MODULE_METADATA: list[dict] = [
    # ── Pool ──────────────────────────────────────────────────────────────────
    {
        "key": "pool.reserve_asset",
        "group": "pool",
        "description": "Reserviert ein freies Asset aus dem Pool für die Bestellung",
        "params": ["order_id", "asset_type_id", "expires_at"],
        "output_keys": ["asset_id", "asset_name"],
    },
    {
        "key": "pool.check_capacity",
        "group": "pool",
        "description": "Prüft ob Pool-Kapazität für pooled Assets noch frei ist",
        "params": ["asset_type_id", "pool_capacity"],
        "output_keys": [],
    },
    {
        "key": "pool.set_asset_busy",
        "group": "pool",
        "description": "Setzt ein Asset auf BUSY (nach Bereitstellung oder Verlängerung)",
        "params": ["asset_id", "order_id", "expires_at"],
        "output_keys": [],
    },
    {
        "key": "pool.release_asset",
        "group": "pool",
        "description": "Gibt ein Asset zurück in den Pool (Status: FREE)",
        "params": ["asset_id"],
        "output_keys": [],
    },
    # ── Active Roles ──────────────────────────────────────────────────────────
    {
        "key": "active_roles.set_rdp_group",
        "group": "active_roles",
        "description": "Befüllt die RDP-AD-Gruppe der VM mit den angegebenen Benutzern",
        "params": ["asset_name", "rdp_users"],
        "output_keys": [],
    },
    {
        "key": "active_roles.set_admin_group",
        "group": "active_roles",
        "description": "Befüllt die Admin-AD-Gruppe der VM mit den angegebenen Benutzern",
        "params": ["asset_name", "admin_users"],
        "output_keys": [],
    },
    {
        "key": "active_roles.remove_all_groups",
        "group": "active_roles",
        "description": "Entfernt alle AD-Gruppen der VM (bei Rückgabe)",
        "params": ["asset_name"],
        "output_keys": [],
    },
    # ── vSphere ───────────────────────────────────────────────────────────────
    {
        "key": "vsphere.update_vmware_tools",
        "group": "vsphere",
        "description": "Aktualisiert VMware Tools auf der VM via PowerCLI",
        "params": ["asset_name"],
        "output_keys": [],
    },
    {
        "key": "vsphere.restart_vm",
        "group": "vsphere",
        "description": "Startet die VM neu via vSphere",
        "params": ["asset_name"],
        "output_keys": [],
    },
    # ── SCCM ──────────────────────────────────────────────────────────────────
    {
        "key": "sccm.trigger_reinstall",
        "group": "sccm",
        "description": "Löst SCCM-Tasksequenz für unattended VM-Neuinstallation aus",
        "params": ["asset_name"],
        "output_keys": [],
    },
    # ── Notifications ─────────────────────────────────────────────────────────
    {
        "key": "notifications.send_confirmation",
        "group": "notifications",
        "description": "Sendet zweisprachige Bestellbestätigung an Besteller und Owner",
        "params": [
            "user_email", "user_name", "owner_email", "owner_name",
            "asset_type_name", "asset_type_description",
            "requested_from", "expires_at", "snow_req", "snow_ritm",
        ],
        "output_keys": [],
    },
    {
        "key": "notifications.send_provision_confirmation",
        "group": "notifications",
        "description": "Sendet Bereitstellungsbestätigung mit VM-Name und RDP-Zugang",
        "params": ["user_email", "user_name", "asset_name", "rdp_users", "expires_at"],
        "output_keys": [],
    },
    {
        "key": "notifications.send_reclaim",
        "group": "notifications",
        "description": "Benachrichtigt den User über die Rückführung seiner VM in den Pool",
        "params": ["user_email", "user_name", "asset_name"],
        "output_keys": [],
    },
    # ── Target Executor ───────────────────────────────────────────────────────
    {
        "key": "target_executor.grant",
        "group": "target_executor",
        "description": "Liest targets aus asset_types und fügt Principals zu Gruppen hinzu (AD/Entra)",
        "params": ["order_id", "asset_type_id", "user_email", "rdp_users", "admin_users"],
        "output_keys": [],
    },
    {
        "key": "target_executor.revoke",
        "group": "target_executor",
        "description": "Invertiert alle grant-Einträge aus dem Change-Log (deterministisches Revoke)",
        "params": ["user_email", "asset_type_id"],
        "output_keys": [],
    },
]

# Index für schnellen Zugriff per Key
MODULE_MAP: dict[str, dict] = {m["key"]: m for m in MODULE_METADATA}

# Gruppen-Reihenfolge für UI
MODULE_GROUPS = ["pool", "active_roles", "vsphere", "sccm", "notifications", "target_executor"]
