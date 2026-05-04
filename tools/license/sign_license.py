"""Sign an Ipsolis license payload with an Ed25519 private key.

Reads a JSON payload (without a ``signature`` field), signs it with the given
private key, and writes the signed ``.lic`` file (JSON with an added
``signature`` field, base64-encoded).

The payload must be a JSON object with at least the following fields:

    license_id   – UUID or unique identifier
    licensee     – Customer / organization name
    edition      – "enterprise" (use "community" for test fallbacks)
    max_users    – 0 = unlimited
    max_asset_types – 0 = unlimited
    issued_at    – ISO-8601 timestamp
    expires_at   – ISO-8601 timestamp
    features     – list; use ["all"] for full Enterprise access

Optional install-binding field:

    install_uuid – UUID copied from the customer's install (visible on the
                   License page in the admin UI, or in the ``app_config``
                   row keyed ``install.uuid``). When present, the runtime
                   verifier rejects the license on any other install,
                   preventing license-sharing across deployments.
                   Omit the field for portable licenses (legacy behaviour).

After signing, the script re-loads the output file and verifies the signature
against the derived public key as a safety check.

Usage:
    python tools/license/sign_license.py \
        --key tools/license/private_key.pem \
        --payload payload.json \
        --out license/ipsolis.lic
"""
from __future__ import annotations

import argparse
import base64
import json
import sys
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


def _canonical_bytes(payload: dict) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--key", type=Path, required=True, help="Private key PEM path")
    ap.add_argument("--payload", type=Path, required=True, help="JSON payload file")
    ap.add_argument("--out", type=Path, required=True, help="Output .lic path")
    args = ap.parse_args()

    if not args.key.exists():
        sys.stderr.write(f"Private key not found: {args.key}\n")
        return 2
    if not args.payload.exists():
        sys.stderr.write(f"Payload not found: {args.payload}\n")
        return 2

    private_key = serialization.load_pem_private_key(args.key.read_bytes(), password=None)
    if not isinstance(private_key, Ed25519PrivateKey):
        sys.stderr.write(f"Key at {args.key} is not an Ed25519 private key\n")
        return 2

    payload = json.loads(args.payload.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        sys.stderr.write("Payload must be a JSON object\n")
        return 2
    if "signature" in payload:
        sys.stderr.write("Payload must not contain a 'signature' field\n")
        return 2

    message = _canonical_bytes(payload)
    signature = private_key.sign(message)

    # Self-verify before writing.
    public_key = private_key.public_key()
    try:
        public_key.verify(signature, message)
    except InvalidSignature:
        sys.stderr.write("Self-verification failed — refusing to write output\n")
        return 3

    signed = dict(payload)
    signed["signature"] = base64.b64encode(signature).decode("ascii")

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(signed, indent=2), encoding="utf-8")
    print(f"Signed license written to: {args.out}")
    print(f"Licensee: {payload.get('licensee', '(unknown)')}")
    print(f"Edition:  {payload.get('edition', '(unknown)')}")
    print(f"Expires:  {payload.get('expires_at', '(none)')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
