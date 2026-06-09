"""Celery Task: Install a PowerShell module from PSGallery or a manual zip upload.

Reads the desired module from the ps_modules DB table.
- source_type='gallery': runs Install-Module -Scope CurrentUser (persisted via Docker volume).
- source_type='upload': extracts the stored zip to ~/.local/share/powershell/Modules/.
Updates the status to installed / failed.
"""

import io
import logging
import os
import shutil
import subprocess
import zipfile

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from tasks import app

logger = logging.getLogger(__name__)

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+psycopg2://xpuser:changeme@localhost:5432/ipsolis",
).replace("postgresql+asyncpg://", "postgresql+psycopg2://")


def _get_db_session() -> Session:
    from tasks.modules.db import get_worker_session
    return get_worker_session()


def _set_status(db: Session, ps_module_id: int, status: str, **kwargs) -> None:
    sets = ["status = :status", "updated_at = NOW()"]
    params: dict = {"id": ps_module_id, "status": status}
    for col, val in kwargs.items():
        sets.append(f"{col} = :{col}")
        params[col] = val
    db.execute(text(f"UPDATE ps_modules SET {', '.join(sets)} WHERE id = :id"), params)
    db.commit()


def _install_from_upload(db: Session, ps_module_id: int, module_name: str, upload_data) -> dict:
    """Extract a manually-uploaded zip into the PowerShell Modules directory."""
    if not upload_data:
        err = "No upload data found — please upload a zip file first"
        _set_status(db, ps_module_id, "failed", error_log=err)
        return {"success": False, "error": err}

    modules_root = os.path.expanduser("~/.local/share/powershell/Modules")
    target_dir = os.path.join(modules_root, module_name)

    _set_status(db, ps_module_id, "installing", error_log=None, installed_version=None)
    logger.info("ps_module_installer: installing %s from upload (zip size=%d)", module_name, len(upload_data))

    try:
        # Remove any previous install
        if os.path.exists(target_dir):
            shutil.rmtree(target_dir)

        with zipfile.ZipFile(io.BytesIO(bytes(upload_data))) as zf:
            # Detect structure: zip may contain {ModuleName}/ subfolder or be flat
            top_dirs = {p.split("/")[0] for p in zf.namelist() if "/" in p}
            if module_name in top_dirs:
                # Zip contains {ModuleName}/... — extract to modules_root directly
                zf.extractall(modules_root)
            else:
                # Flat zip — extract into modules_root/{ModuleName}/
                os.makedirs(target_dir, exist_ok=True)
                zf.extractall(target_dir)

        # Verify a .psd1 or .psm1 exists in the target dir
        psd1_path = None
        if os.path.isdir(target_dir):
            for f in os.listdir(target_dir):
                if f.lower().endswith(".psd1"):
                    psd1_path = os.path.join(target_dir, f)
                    break
            found = psd1_path is not None or any(
                f.lower().endswith(".psm1") for f in os.listdir(target_dir)
            )
        else:
            found = False

        if not found:
            raise RuntimeError(f"No .psd1/.psm1 found in extracted module dir: {target_dir}")

        # Try to read ModuleVersion from the .psd1 manifest
        installed_version = "manual"
        if psd1_path:
            try:
                import re
                content = open(psd1_path, encoding="utf-8", errors="replace").read()
                m = re.search(r"ModuleVersion\s*=\s*['\"]([^'\"]+)['\"]", content)
                if m:
                    installed_version = m.group(1)
            except Exception:
                pass

        _set_status(db, ps_module_id, "installed", installed_version=installed_version)
        logger.info("ps_module_installer: installed %s from upload, version=%s", module_name, installed_version)
        return {"success": True, "installed_version": installed_version}

    except Exception as exc:
        logger.error("ps_module_installer: upload install failed for %s: %s", module_name, exc)
        _set_status(db, ps_module_id, "failed", error_log=str(exc)[:4000])
        return {"success": False, "error": str(exc)}


@app.task(
    name="tasks.workflows.ps_module_installer.install_ps_module",
    bind=True,
    queue="provision",
)
def install_ps_module(self, ps_module_id: int) -> dict:
    """Install or reinstall a PS module from PSGallery."""
    db = _get_db_session()
    try:
        row = db.execute(
            text("SELECT id, name, required_version, source_type, upload_data FROM ps_modules WHERE id = :id"),
            {"id": ps_module_id},
        ).fetchone()

        if not row:
            return {"success": False, "error": f"ps_module id={ps_module_id} not found"}

        module_name = row.name
        required_version = row.required_version
        source_type = row.source_type or "gallery"

        if source_type == "upload":
            return _install_from_upload(db, ps_module_id, module_name, row.upload_data)

        _set_status(db, ps_module_id, "installing", error_log=None, installed_version=None)
        logger.info("ps_module_installer: installing %s (version=%s)", module_name, required_version or "latest")

        # ── Install ────────────────────────────────────────────────────────────
        version_clause = (
            f"-RequiredVersion '{required_version}' " if required_version else ""
        )
        ps_cmd = (
            "Set-PSRepository -Name PSGallery -InstallationPolicy Trusted; "
            f"Install-Module -Name '{module_name}' {version_clause}"
            "-Scope CurrentUser -Force -SkipPublisherCheck -AllowClobber"
        )

        result = subprocess.run(
            ["pwsh", "-NonInteractive", "-NoProfile", "-Command", ps_cmd],
            capture_output=True,
            text=True,
            timeout=600,
        )

        if result.returncode != 0:
            error = (result.stderr or result.stdout or "unknown error").strip()
            _set_status(db, ps_module_id, "failed", error_log=error[:4000])
            logger.error("ps_module_installer: failed %s: %s", module_name, error[:200])
            return {"success": False, "error": error}

        # ── Verify installation succeeded via Get-InstalledModule ──────────────
        # This is the authoritative check: if the module is not found locally
        # after a successful Install-Module call, PSGallery silently accepted an
        # unknown module name (e.g. due to API issues). Treat that as failure.
        ver_result = subprocess.run(
            [
                "pwsh", "-NonInteractive", "-NoProfile", "-Command",
                f"(Get-InstalledModule -Name '{module_name}' -ErrorAction SilentlyContinue).Version",
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        installed_version = ver_result.stdout.strip()

        if not installed_version:
            error = (
                f"Module '{module_name}' was not installed. "
                "The module name may not exist in the PSGallery. "
                "Please verify the exact name at https://www.powershellgallery.com"
            )
            _set_status(db, ps_module_id, "failed", error_log=error)
            logger.warning("ps_module_installer: Get-InstalledModule returned nothing for %s", module_name)
            return {"success": False, "error": error}

        _set_status(db, ps_module_id, "installed", installed_version=installed_version)
        logger.info("ps_module_installer: installed %s=%s", module_name, installed_version)
        return {"success": True, "installed_version": installed_version}

    except subprocess.TimeoutExpired:
        _set_status(db, ps_module_id, "failed", error_log="Installation timed out after 600s")
        return {"success": False, "error": "timeout"}
    except Exception as exc:
        logger.exception("ps_module_installer: unexpected error for id=%s", ps_module_id)
        try:
            _set_status(db, ps_module_id, "failed", error_log=str(exc)[:4000])
        except Exception:
            pass
        return {"success": False, "error": str(exc)}
    finally:
        db.close()
