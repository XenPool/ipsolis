"""Generate an Ed25519 keypair for Ipsolis license signing.

Writes the private key to ``scripts/license/private_key.pem`` (gitignored) and
prints the public key as a hex literal to paste into ``PUBLIC_KEY_HEX`` in
BOTH of these files:

    api/app/utils/license.py
    worker/tasks/utils/license.py

The private key must never be committed or distributed; it is used only to
sign ``.lic`` files for paying customers.

Usage:
    python scripts/license/generate_keypair.py [--out scripts/license/private_key.pem]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--out",
        type=Path,
        default=Path(__file__).parent / "private_key.pem",
        help="Path to write the private key PEM file",
    )
    args = ap.parse_args()

    if args.out.exists():
        sys.stderr.write(
            f"Refusing to overwrite existing key at {args.out}. "
            f"Remove it first if you really mean to regenerate.\n"
        )
        return 2

    private_key = Ed25519PrivateKey.generate()
    pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_bytes(pem)
    args.out.chmod(0o600)

    public_bytes = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )

    print(f"Private key written to: {args.out}")
    print("")
    print("Paste the following into PUBLIC_KEY_HEX in BOTH:")
    print("  api/app/utils/license.py")
    print("  worker/tasks/utils/license.py")
    print("")
    print(f'PUBLIC_KEY_HEX: str = "{public_bytes.hex()}"')
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
