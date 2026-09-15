"""
One-off helper: generates a fresh VAPID keypair for push notifications.

Run it (locally, or from Render's Shell tab) and paste the two printed
values into VAPID_PUBLIC_KEY / VAPID_PRIVATE_KEY (see .env.example) —
on Render that's Environment > Add Environment Variable. The private key
is a real credential (whoever has it can send push notifications posing
as this app) — set it as an env var, don't commit it anywhere.

Usage: python3 scripts/generate_vapid_keys.py
"""
import base64

from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives import serialization


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def main():
    private_key = ec.generate_private_key(ec.SECP256R1())
    public_key = private_key.public_key()

    private_bytes = private_key.private_numbers().private_value.to_bytes(32, "big")
    public_bytes = public_key.public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
    )

    print("VAPID_PUBLIC_KEY=" + _b64url(public_bytes))
    print("VAPID_PRIVATE_KEY=" + _b64url(private_bytes))


if __name__ == "__main__":
    main()
