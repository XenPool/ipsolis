"""Admin UI authentication routes (login / logout / first-run setup).

These routes have NO auth dependency — they must be accessible without a session.
Registered under /ui prefix, included in main.py before ui.router.

RBAC slice 1: replaces the binary "single ADMIN_API_KEY = god mode"
login with per-user accounts. The legacy key still works as a
back-compat fallback (treated as superadmin); real users live in
``admin_users`` and authenticate with PBKDF2-hashed passwords.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.models.admin_user import AdminUser
from app.templates_instance import templates
from app.utils.password import hash_password, verify_password
from app.utils.password_policy import (
    is_locked,
    password_must_be_changed,
    read_policy,
    record_failed_login,
    record_successful_login,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ui", tags=["admin-auth"])


async def _admin_users_count(db: AsyncSession) -> int:
    result = await db.execute(select(func.count()).select_from(AdminUser))
    return int(result.scalar_one())


@router.get("/login", response_class=HTMLResponse)
async def admin_login_page(request: Request, db: AsyncSession = Depends(get_db)):
    """Renders the admin login form, or the first-run setup form when empty."""
    if request.session.get("admin_authenticated"):
        return RedirectResponse(url="/ui/", status_code=302)
    first_run = (await _admin_users_count(db)) == 0
    return templates.TemplateResponse("admin/login.html", {
        "request": request,
        "error": None,
        "first_run": first_run,
    })


@router.post("/login", response_class=HTMLResponse)
async def admin_login_submit(
    request: Request,
    password: str = Form(...),
    username: str = Form(default=""),
    db: AsyncSession = Depends(get_db),
):
    """Validates credentials and establishes a session.

    Two recognised paths:

    * Legacy: empty username + password matches ``settings.ADMIN_API_KEY``.
      Establishes a virtual ``superadmin`` session attributed as
      ``admin:legacy_key`` in the audit log.
    * Per-user: username + password matched against ``admin_users``
      with ``is_active = true``. Updates ``last_login_at`` on success.
      Session attribution is ``admin:session:<username>:<role>``.
    """
    username_norm = (username or "").strip().lower()

    # Legacy path — preserved verbatim so existing setups don't break.
    if not username_norm:
        if password == settings.ADMIN_API_KEY:
            next_url = request.session.pop("admin_next", "/ui/")
            request.session["admin_authenticated"] = True
            request.session["admin_user"] = "admin"
            request.session["admin_role"] = "superadmin"
            request.session["admin_via"] = "legacy_key"
            logger.info("Admin login: legacy key (back-compat)")
            return RedirectResponse(url=next_url, status_code=303)

    # Per-user path — RBAC slice 4 adds rotation + lockout checks on top
    # of the slice-1 username/password match.
    if username_norm:
        result = await db.execute(
            select(AdminUser).where(AdminUser.username == username_norm)
        )
        user = result.scalar_one_or_none()
        now = datetime.now(timezone.utc)
        policy = await read_policy(db)

        if user and user.is_active:
            # Lockout takes precedence over password verification so a
            # locked account doesn't get to keep guessing — the answer
            # is always "locked, try again later".
            locked, unlock_at = is_locked(user, policy, now)
            if locked:
                first_run = (await _admin_users_count(db)) == 0
                msg = "Account is locked. Try again after {}.".format(
                    unlock_at.strftime("%Y-%m-%d %H:%M UTC") if unlock_at else "the lockout window"
                )
                logger.info("Admin login refused (locked): user=%s", user.username)
                return templates.TemplateResponse("admin/login.html", {
                    "request": request,
                    "error": msg,
                    "first_run": first_run,
                }, status_code=423)

            if verify_password(password, user.password_hash):
                # Successful login — reset bad-password counter, stamp
                # last_login_at, then check whether the password has
                # aged out and we need to push the user to /my-account.
                await record_successful_login(db, user, now)
                next_url = request.session.pop("admin_next", "/ui/")
                request.session["admin_authenticated"] = True
                request.session["admin_user"] = user.username
                request.session["admin_role"] = user.role
                request.session["admin_via"] = "user"
                if password_must_be_changed(user, policy, now):
                    request.session["must_change_password"] = True
                    logger.info("Admin login: user=%s rotation due", user.username)
                    return RedirectResponse(url="/ui/my-account?rotate=1", status_code=303)
                logger.info("Admin login: user=%s role=%s", user.username, user.role)
                return RedirectResponse(url=next_url, status_code=303)

            # Bad password — increment counter (which may now lock the
            # account) and re-render with a generic error. We don't tell
            # the user "your account just got locked" on this exact
            # response: the next attempt will see the lockout banner.
            just_locked = await record_failed_login(db, user, policy, now)
            if just_locked:
                logger.warning(
                    "Admin login: user=%s LOCKED after %d failed attempts",
                    user.username, user.failed_login_count,
                )

    # Fallback — re-render with error and first-run flag refreshed.
    first_run = (await _admin_users_count(db)) == 0
    return templates.TemplateResponse("admin/login.html", {
        "request": request,
        "error": "Incorrect username or password.",
        "first_run": first_run,
    }, status_code=401)


@router.post("/setup", response_class=HTMLResponse)
async def admin_first_run_setup(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    password_confirm: str = Form(...),
    db: AsyncSession = Depends(get_db),
):
    """Creates the first superadmin when ``admin_users`` is empty.

    Idempotent against races: re-checks the count inside the request and
    fails the form if a user has been created in the meantime (e.g. two
    operators hitting the setup form simultaneously).
    """
    if (await _admin_users_count(db)) > 0:
        return templates.TemplateResponse("admin/login.html", {
            "request": request,
            "error": "Setup already complete. Sign in instead.",
            "first_run": False,
        }, status_code=409)

    username_norm = (username or "").strip().lower()
    if (
        not username_norm
        or len(username_norm) < 3
        or len(username_norm) > 128
        or not all(c.isalnum() or c in "._@-" for c in username_norm)
    ):
        return templates.TemplateResponse("admin/login.html", {
            "request": request,
            "error": "Username must be 3-128 chars: letters, digits, dot, underscore, @, hyphen.",
            "first_run": True,
        }, status_code=422)
    if len(password or "") < 12:
        return templates.TemplateResponse("admin/login.html", {
            "request": request,
            "error": "Password must be at least 12 characters.",
            "first_run": True,
        }, status_code=422)
    if password != password_confirm:
        return templates.TemplateResponse("admin/login.html", {
            "request": request,
            "error": "Passwords do not match.",
            "first_run": True,
        }, status_code=422)

    user = AdminUser(
        username=username_norm,
        password_hash=hash_password(password),
        role="superadmin",
        is_active=True,
        created_by="first-run-setup",
        # RBAC slice 4: stamp the rotation clock at create time so the
        # admin doesn't get force-expired the first time a rotation
        # policy is enabled (without this the column would be NULL and
        # ``password_must_be_changed`` would treat that as "never").
        password_set_at=datetime.now(timezone.utc),
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    # Auto-login after setup so the operator lands on the dashboard.
    request.session["admin_authenticated"] = True
    request.session["admin_user"] = user.username
    request.session["admin_role"] = user.role
    request.session["admin_via"] = "user"
    logger.info("First-run setup: created superadmin %s", user.username)
    # Land the brand-new superadmin in the guided setup wizard rather than a
    # blank dashboard — it walks them through the essential integrations.
    return RedirectResponse(url="/ui/setup-wizard", status_code=303)


@router.post("/logout")
async def admin_logout(request: Request):
    """Clears the admin session and redirects to the login page."""
    request.session.clear()
    return RedirectResponse(url="/ui/login", status_code=303)
