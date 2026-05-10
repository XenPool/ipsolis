# Registry Token Management — ipSolis Pro

Per-customer deploy tokens for `ghcr.io/xenpool/ipsolis-pro-*` images.

---

## How it works

- The **Community** images (`ghcr.io/xenpool/ipsolis-api`, `ipsolis-worker`) are
  public — anyone can pull them without authentication.
- The **Pro** images (`ghcr.io/xenpool/ipsolis-pro-api`, `ipsolis-pro-worker`) are
  private. Customers authenticate with a per-customer GitHub PAT issued from the
  `ipsolis-deploy` machine account.
- Each customer gets their own token so access can be revoked individually without
  affecting other customers.

---

## One-time setup (do this once per organisation)

### 1. Create the `ipsolis-deploy` machine account

1. Register a new GitHub account with username **`ipsolis-deploy`**.
2. Add it to the **XenPool** GitHub organisation as an outside collaborator
   (or as a member with a minimal role — it only needs package read access).

### 2. Grant the bot account access to the Pro packages

After the first CI release pushes the Pro images, go to each package and add
the bot as a collaborator:

```
https://github.com/orgs/xenpool/packages/container/ipsolis-pro-api/settings
https://github.com/orgs/xenpool/packages/container/ipsolis-pro-worker/settings
```

Under **Manage Access → Invite collaborators** → add `ipsolis-deploy` with the
**Read** role.

### 3. Set Community packages to public visibility

```
https://github.com/orgs/xenpool/packages/container/ipsolis-api/settings
https://github.com/orgs/xenpool/packages/container/ipsolis-worker/settings
```

Change visibility to **Public** so community users can pull without auth.

### 4. Add `ipsolis-deploy` to the `gh` CLI on your workstation

```bash
gh auth login --hostname github.com
# Log in as ipsolis-deploy when prompted (use a browser token or PAT
# that has at least read:org and admin:org scopes for token management)
```

Verify with:
```bash
gh auth status
```

---

## Daily operations

All commands run from the repo root:

```bash
bash tools/registry/manage.sh <subcommand>
```

> The script requires `gh` authenticated **as `ipsolis-deploy`**.
> Switch context: `gh auth switch --user ipsolis-deploy`

---

### Issue a token for a new customer

```bash
bash tools/registry/manage.sh issue "Acme Corp"
```

The script prints step-by-step instructions:

1. Create a classic PAT on `ipsolis-deploy` in the GitHub UI with:
   - **Note**: `ipsolis-pro:Acme Corp`  ← exact format, used for lookup
   - **Expiration**: 1 year (or "No expiration")
   - **Scope**: `read:packages`
2. Copy the token value (shown once).
3. Send the customer:
   - `docker login ghcr.io -u ipsolis-deploy --password-stdin` command
   - The token value
   - Their `docker-compose.pro.yml` from the onboarding package
4. Record the issuance in the CRM with the token name and issue date.

---

### List all active customer tokens

```bash
bash tools/registry/manage.sh list
```

Output:
```
Active ipSolis Pro registry tokens (account: ipsolis-deploy)
──────────────────────────────────────────────────────────
  ID      Customer                                 Created       Expires
  ------  ----------------------------------------  ------------  --------
  123456  Acme Corp                                2026-01-15    2027-01-15
  123789  Beta GmbH                                2026-03-01    never
```

---

### Revoke a token (customer cancels)

```bash
bash tools/registry/manage.sh revoke "Acme Corp"
```

The script:
1. Looks up the token by name `ipsolis-pro:Acme Corp` via GitHub API.
2. Shows the token details and asks for confirmation.
3. Calls `DELETE /user/tokens/{id}` to permanently invalidate it.
4. Subsequent `docker pull` attempts by the customer return 401.

Effect is **immediate** — no grace period.

---

### Verify a customer token

Before sending a token to a customer, confirm it works:

```bash
bash tools/registry/manage.sh verify ghp_xxxxxxxxxxxxxxxxxxxx
```

Performs a `docker login` + manifest inspect (no full image download).

---

## Token naming convention

All customer tokens **must** follow the naming pattern:

```
ipsolis-pro:<CustomerName>
```

Examples:
- `ipsolis-pro:Acme Corp`
- `ipsolis-pro:Beta GmbH`
- `ipsolis-pro:Internal-Staging`

The `manage.sh` script uses this prefix to distinguish ipSolis tokens from any
other PATs on the bot account. Do not deviate from this format.

---

## Customer docker login instructions

Send customers this snippet alongside their token:

```bash
# Log in to the XenPool container registry
echo "<YOUR_TOKEN>" | docker login ghcr.io -u ipsolis-deploy --password-stdin

# Verify access
docker manifest inspect ghcr.io/xenpool/ipsolis-pro-api:latest
```

For automated deployments (CI/CD or Docker Compose on a server), add to
`~/.docker/config.json` or use the compose `secrets:` mechanism — never
hard-code the token in a `docker-compose.yml` committed to a repo.

---

## Revocation checklist (customer cancellation)

When a customer cancels their subscription:

- [ ] `bash tools/registry/manage.sh revoke "<CustomerName>"`
- [ ] Confirm revocation in the script prompt
- [ ] Mark the customer inactive in the CRM
- [ ] Notify the customer that registry access has been terminated
- [ ] If the customer also has a signed `.lic` file: note the `expires_at` in
  the license — the file becomes inert naturally after expiry, but you can
  issue a replacement with an earlier `expires_at` if immediate license
  invalidation is needed (re-sign with `tools/license/sign_license.py`).

---

## Troubleshooting

**`gh` returns 404 on `/user/tokens`**
The authenticated session doesn't have the right scope. Generate a new PAT for
the `ipsolis-deploy` account with the `admin:org` scope and run
`gh auth login` again with that token.

**Token created but customer gets 401 on pull**
Check that `ipsolis-deploy` has been added as a collaborator with **Read**
access on both `ipsolis-pro-api` and `ipsolis-pro-worker` packages
(org → Packages → package settings → Manage Access).

**Multiple tokens found with the same name**
The `revoke` subcommand refuses to auto-pick. Use `gh api -X DELETE /user/tokens/<ID>`
directly after confirming the correct ID from `manage.sh list`.
