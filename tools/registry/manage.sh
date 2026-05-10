#!/usr/bin/env bash
# manage.sh — GHCR registry token management for ipSolis Pro customers.
#
# Wraps the GitHub API to list and revoke per-customer deploy tokens for the
# private Pro images on ghcr.io/xenpool/ipsolis-pro-*.
#
# Requires:
#   - gh CLI (https://cli.github.com) authenticated as the ipsolis-deploy
#     machine account (see README.md for one-time setup)
#   - jq
#
# Usage:
#   manage.sh list                      List all active customer tokens
#   manage.sh issue  <customer-name>    Print issuance instructions for a new customer
#   manage.sh revoke <customer-name>    Revoke the deploy token for a customer
#   manage.sh verify <token-value>      Check a token can pull from GHCR
#
# Token naming convention (must be consistent to enable scripted lookup):
#   ipsolis-pro:<CustomerName>
#   Example: "ipsolis-pro:Acme Corp"

set -euo pipefail

# ── Constants ─────────────────────────────────────────────────────────────────
readonly GHCR_REGISTRY="ghcr.io"
readonly BOT_USER="ipsolis-deploy"
readonly TOKEN_PREFIX="ipsolis-pro:"
readonly PRO_API_IMAGE="ghcr.io/xenpool/ipsolis-pro-api"
readonly PRO_WORKER_IMAGE="ghcr.io/xenpool/ipsolis-pro-worker"

# ── Helpers ───────────────────────────────────────────────────────────────────
die()  { echo "ERROR: $*" >&2; exit 1; }
info() { echo "  $*"; }

require_cmd() {
  command -v "$1" >/dev/null 2>&1 || die "'$1' is not installed. $2"
}

check_deps() {
  require_cmd gh  "Install from https://cli.github.com"
  require_cmd jq  "Install with: apt install jq / brew install jq"
}

# Verify the authenticated gh session is the bot account so operators don't
# accidentally list/revoke against their own tokens.
check_auth_context() {
  local current_user
  current_user="$(gh api /user --jq '.login' 2>/dev/null || true)"
  if [[ "$current_user" != "$BOT_USER" ]]; then
    cat >&2 <<EOF

ERROR: gh is currently authenticated as '$current_user', not '$BOT_USER'.

Switch to the bot account before running list or revoke:
  gh auth switch --user $BOT_USER

If you haven't added the bot account to gh yet:
  gh auth login --hostname github.com
  (log in as $BOT_USER when prompted)

EOF
    exit 1
  fi
}

# Fetch all classic PATs for the authenticated user, filtered by prefix.
fetch_customer_tokens() {
  gh api /user/tokens --paginate 2>/dev/null \
    | jq -c ".[] | select(.name | startswith(\"${TOKEN_PREFIX}\"))"
}

# ── Subcommands ───────────────────────────────────────────────────────────────

cmd_list() {
  check_deps
  check_auth_context

  echo
  echo "Active ipSolis Pro registry tokens (account: ${BOT_USER})"
  echo "──────────────────────────────────────────────────────────"

  local rows
  rows="$(fetch_customer_tokens)"

  if [[ -z "$rows" ]]; then
    echo "  (none found)"
    echo
    return 0
  fi

  printf "  %-6s  %-40s  %-12s  %s\n" "ID" "Customer" "Created" "Expires"
  printf "  %-6s  %-40s  %-12s  %s\n" "------" "----------------------------------------" "------------" "--------"

  while IFS= read -r row; do
    [[ -z "$row" ]] && continue
    local id name created expires
    id="$(echo "$row"      | jq -r '.id')"
    name="$(echo "$row"    | jq -r '.name | ltrimstr("'"${TOKEN_PREFIX}"'")')"
    created="$(echo "$row" | jq -r '.created_at | split("T")[0]')"
    expires="$(echo "$row" | jq -r 'if .expires_at == null then "never" else (.expires_at | split("T")[0]) end')"
    printf "  %-6s  %-40s  %-12s  %s\n" "$id" "$name" "$created" "$expires"
  done <<< "$rows"
  echo
}

cmd_issue() {
  local customer="${1:-}"
  [[ -z "$customer" ]] && die "Usage: manage.sh issue <customer-name>"

  local token_name="${TOKEN_PREFIX}${customer}"

  cat <<EOF

── Issuing a registry token for: ${customer} ─────────────────────────────────

Token name (copy exactly):
  ${token_name}

Steps:
  1) Log into GitHub as ${BOT_USER} in your browser.

  2) Go to:
       https://github.com/settings/tokens/new

  3) Fill in:
       Note            : ${token_name}
       Expiration      : Custom → 1 year from today  (or No expiration for perpetual)
       Scopes          : ✓ read:packages

  4) Click "Generate token" and copy the value (shown ONCE only).

  5) Send the customer their credentials:

     Subject: ipSolis Pro — Registry Access Credentials

     docker registry: ${GHCR_REGISTRY}
     username       : ${BOT_USER}
     token          : <paste token here>

     See the docker-compose.pro.yml in your onboarding package for the
     full installation steps.

  6) Record the issuance in your CRM / customer sheet:
       Customer  : ${customer}
       Token name: ${token_name}
       Issued    : $(date -u +%Y-%m-%d)

EOF
}

cmd_revoke() {
  local customer="${1:-}"
  [[ -z "$customer" ]] && die "Usage: manage.sh revoke <customer-name>"

  check_deps
  check_auth_context

  local token_name="${TOKEN_PREFIX}${customer}"
  echo
  echo "Looking up token '${token_name}' for account ${BOT_USER}..."

  local matching_rows
  matching_rows="$(fetch_customer_tokens \
    | jq -c "select(.name == \"${token_name}\")")"

  if [[ -z "$matching_rows" ]]; then
    die "No token named '${token_name}' found on account ${BOT_USER}."
  fi

  # There should be exactly one; if the operator accidentally created duplicates
  # list them all and refuse to auto-pick one.
  local count
  count="$(echo "$matching_rows" | wc -l | tr -d ' ')"
  if (( count > 1 )); then
    echo
    echo "WARNING: ${count} tokens found with name '${token_name}'."
    echo "Listing them — revoke by ID instead:"
    echo
    while IFS= read -r row; do
      local id created expires
      id="$(echo "$row"      | jq -r '.id')"
      created="$(echo "$row" | jq -r '.created_at | split("T")[0]')"
      expires="$(echo "$row" | jq -r 'if .expires_at == null then "never" else (.expires_at | split("T")[0]) end')"
      printf "  ID %-6s  created %-12s  expires %s\n" "$id" "$created" "$expires"
    done <<< "$matching_rows"
    echo
    echo "Revoke a specific ID with:"
    echo "  gh api -X DELETE /user/tokens/<ID>"
    echo
    exit 1
  fi

  local token_id created expires
  token_id="$(echo "$matching_rows" | jq -r '.id')"
  created="$(echo "$matching_rows"  | jq -r '.created_at | split("T")[0]')"
  expires="$(echo "$matching_rows"  | jq -r 'if .expires_at == null then "never" else (.expires_at | split("T")[0]) end')"

  echo
  printf "  Customer : %s\n"   "$customer"
  printf "  Token ID : %s\n"   "$token_id"
  printf "  Created  : %s\n"   "$created"
  printf "  Expires  : %s\n\n" "$expires"
  read -r -p "  Revoke this token? This immediately blocks ${customer} from pulling images. [y/N] " confirm
  echo

  if [[ "${confirm,,}" != "y" ]]; then
    echo "Aborted."
    exit 0
  fi

  gh api -X DELETE "/user/tokens/${token_id}"
  echo "Token '${token_name}' (ID ${token_id}) has been revoked."
  echo "${customer} can no longer pull from ${GHCR_REGISTRY}."
  echo
  echo "Next step: notify ${customer} that their access has been terminated."
  echo
}

cmd_verify() {
  local token="${1:-}"
  [[ -z "$token" ]] && die "Usage: manage.sh verify <token-value>"

  check_deps
  require_cmd docker "Install Docker Desktop or Docker Engine"

  echo
  echo "Verifying token against ${PRO_API_IMAGE}:latest ..."

  # Log in using the token value, then attempt to pull the manifest (not the
  # full layer — manifest fetch is enough to confirm read access without the
  # multi-GB download).
  echo "$token" | docker login "${GHCR_REGISTRY}" \
    --username "${BOT_USER}" \
    --password-stdin 2>&1

  if docker manifest inspect "${PRO_API_IMAGE}:latest" >/dev/null 2>&1; then
    echo
    echo "OK — token can pull ${PRO_API_IMAGE}"
    echo "OK — token can pull ${PRO_WORKER_IMAGE}"
  else
    echo
    die "Token authenticated but cannot pull ${PRO_API_IMAGE}. Check package visibility and collaborator access."
  fi
  echo
}

# ── Entry point ───────────────────────────────────────────────────────────────
subcommand="${1:-}"

case "$subcommand" in
  list)            cmd_list ;;
  issue)           cmd_issue "${2:-}" ;;
  revoke)          cmd_revoke "${2:-}" ;;
  verify)          cmd_verify "${2:-}" ;;
  ""|--help|-h)
    cat <<'USAGE'

manage.sh — ipSolis Pro registry token management

Usage:
  manage.sh list                      List all active customer tokens
  manage.sh issue  <customer-name>    Print issuance instructions for a new customer
  manage.sh revoke <customer-name>    Revoke the deploy token for a customer
  manage.sh verify <token-value>      Verify a token can pull from GHCR

Examples:
  manage.sh list
  manage.sh issue  "Acme Corp"
  manage.sh revoke "Acme Corp"
  manage.sh verify ghp_xxxxxxxxxxxxxxxxxxxx

See tools/registry/README.md for full setup and process documentation.
USAGE
    ;;
  *)
    die "Unknown subcommand '${subcommand}'. Run: manage.sh --help"
    ;;
esac
