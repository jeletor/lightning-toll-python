"""
Simple macaroon implementation using HMAC-SHA256.

A macaroon is a bearer credential with embedded caveats.
Structure: { id, caveats, signature }

The id contains the payment hash (binding the macaroon to a specific payment).
Caveats restrict where/when/how the macaroon can be used.
The signature is chained HMAC — each caveat is folded into the sig.

This is a direct port of the Node.js lightning-toll macaroon module.
The wire format (base64url JSON) is identical and interoperable.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from base64 import urlsafe_b64decode, urlsafe_b64encode
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class Macaroon:
    """Decoded macaroon structure."""
    id: str                        # payment hash
    caveats: List[str]             # e.g. ["expires_at = 123", "endpoint = /api/x"]
    signature: str                 # hex-encoded HMAC chain result
    raw: str = ""                  # base64url-encoded JSON (the wire format)


@dataclass
class VerifyResult:
    """Result of macaroon verification."""
    valid: bool
    error: Optional[str] = None
    payment_hash: Optional[str] = None


def create_macaroon(secret: str, **opts: Any) -> Macaroon:
    """
    Create a new macaroon.

    Args:
        secret: Server's HMAC secret.
        payment_hash: Lightning payment hash (required).
        expires_at: Unix timestamp for expiry.
        endpoint: Bound endpoint path.
        method: HTTP method restriction.
        ip: Client IP restriction.

    Returns:
        Macaroon with id, caveats, signature, and raw (base64url wire format).
    """
    if not secret:
        raise ValueError("Macaroon secret is required")

    payment_hash = opts.get("payment_hash")
    if not payment_hash:
        raise ValueError("payment_hash is required for macaroon")

    identifier = payment_hash

    # Build caveats (same order as Node.js version)
    caveats: List[str] = []
    if opts.get("expires_at"):
        caveats.append(f"expires_at = {opts['expires_at']}")
    if opts.get("endpoint"):
        caveats.append(f"endpoint = {opts['endpoint']}")
    if opts.get("method"):
        caveats.append(f"method = {opts['method']}")
    if opts.get("ip"):
        caveats.append(f"ip = {opts['ip']}")

    # Chain HMAC: start with HMAC(secret, id), then fold each caveat
    sig = hmac.new(
        secret.encode("utf-8") if isinstance(secret, str) else secret,
        identifier.encode("utf-8"),
        hashlib.sha256,
    ).digest()

    for caveat in caveats:
        sig = hmac.new(sig, caveat.encode("utf-8"), hashlib.sha256).digest()

    signature = sig.hex()

    # Encode as base64url JSON for transport (same format as Node.js)
    payload = {"id": identifier, "caveats": caveats, "signature": signature}
    raw = urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode("utf-8")).decode("ascii")
    # Strip padding to match base64url (Node.js base64url doesn't pad)
    raw = raw.rstrip("=")

    return Macaroon(id=identifier, caveats=caveats, signature=signature, raw=raw)


def decode_macaroon(raw: str) -> Optional[Macaroon]:
    """
    Decode a raw macaroon string back to its components.

    Args:
        raw: Base64url-encoded macaroon string.

    Returns:
        Macaroon or None if decoding fails.
    """
    try:
        # Add padding back if needed
        padded = raw + "=" * (-len(raw) % 4)
        json_bytes = urlsafe_b64decode(padded)
        parsed = json.loads(json_bytes.decode("utf-8"))

        if not parsed.get("id") or not parsed.get("signature") or not isinstance(parsed.get("caveats"), list):
            return None

        return Macaroon(
            id=parsed["id"],
            caveats=parsed["caveats"],
            signature=parsed["signature"],
            raw=raw,
        )
    except Exception:
        return None


def verify_macaroon(
    secret: str,
    macaroon: Macaroon,
    context: Optional[Dict[str, str]] = None,
) -> VerifyResult:
    """
    Verify a macaroon's signature and caveats.

    Args:
        secret: Server's HMAC secret.
        macaroon: Decoded macaroon to verify.
        context: Request context for caveat verification.
            - endpoint: Current request path.
            - method: Current HTTP method.
            - ip: Client IP.

    Returns:
        VerifyResult with valid flag, optional error, and payment_hash.
    """
    if context is None:
        context = {}

    if not macaroon or not macaroon.id or not macaroon.signature:
        return VerifyResult(valid=False, error="Invalid macaroon structure", payment_hash=None)

    # Recompute chained HMAC
    sig = hmac.new(
        secret.encode("utf-8") if isinstance(secret, str) else secret,
        macaroon.id.encode("utf-8"),
        hashlib.sha256,
    ).digest()

    for caveat in macaroon.caveats:
        sig = hmac.new(sig, caveat.encode("utf-8"), hashlib.sha256).digest()

    expected_sig = sig.hex()

    # Constant-time comparison
    if not hmac.compare_digest(
        bytes.fromhex(macaroon.signature),
        bytes.fromhex(expected_sig),
    ):
        return VerifyResult(valid=False, error="Invalid macaroon signature", payment_hash=macaroon.id)

    # Verify caveats
    for caveat in macaroon.caveats:
        parts = caveat.split(" = ", 1)
        if len(parts) != 2:
            return VerifyResult(
                valid=False,
                error=f"Malformed caveat: {caveat}",
                payment_hash=macaroon.id,
            )

        key = parts[0].strip()
        value = parts[1].strip()

        if key == "expires_at":
            expires_at = int(value)
            if time.time() > expires_at:
                return VerifyResult(valid=False, error="Macaroon expired", payment_hash=macaroon.id)

        elif key == "endpoint":
            if context.get("endpoint") and context["endpoint"] != value:
                return VerifyResult(
                    valid=False,
                    error=f"Endpoint mismatch: expected {value}, got {context['endpoint']}",
                    payment_hash=macaroon.id,
                )

        elif key == "method":
            if context.get("method") and context["method"].upper() != value.upper():
                return VerifyResult(
                    valid=False,
                    error=f"Method mismatch: expected {value}, got {context['method']}",
                    payment_hash=macaroon.id,
                )

        elif key == "ip":
            if context.get("ip") and context["ip"] != value:
                return VerifyResult(
                    valid=False,
                    error=f"IP mismatch: expected {value}, got {context['ip']}",
                    payment_hash=macaroon.id,
                )
        # else: unknown caveats are ignored (forward-compatible)

    return VerifyResult(valid=True, payment_hash=macaroon.id)


def verify_preimage(preimage: str, payment_hash: str) -> bool:
    """
    Verify that a preimage matches a payment hash.
    payment_hash = SHA256(preimage)

    Args:
        preimage: Hex-encoded preimage.
        payment_hash: Hex-encoded payment hash.

    Returns:
        True if SHA256(preimage) == payment_hash.
    """
    if not preimage or not payment_hash:
        return False
    try:
        computed = hashlib.sha256(bytes.fromhex(preimage)).hexdigest()
        return hmac.compare_digest(
            bytes.fromhex(computed),
            bytes.fromhex(payment_hash),
        )
    except Exception:
        return False
