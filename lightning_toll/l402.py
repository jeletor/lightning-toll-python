"""
L402 protocol header parsing and formatting.

Implements the L402 (formerly LSAT) protocol for HTTP 402 Payment Required.

WWW-Authenticate: L402 invoice="lnbc...", macaroon="..."
Authorization: L402 <macaroon>:<preimage>

Direct port of the Node.js lightning-toll l402 module.
Wire format is identical and interoperable.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional


@dataclass
class L402Credentials:
    """Parsed L402 authorization credentials."""
    macaroon: str
    preimage: str


def format_challenge(invoice: str, macaroon: str) -> str:
    """
    Format a WWW-Authenticate header value for a 402 response.

    Args:
        invoice: Bolt11 invoice string.
        macaroon: Base64url-encoded macaroon.

    Returns:
        WWW-Authenticate header value.
    """
    return f'L402 invoice="{invoice}", macaroon="{macaroon}"'


def format_challenge_body(
    invoice: str,
    macaroon: str,
    payment_hash: str,
    amount_sats: int,
    description: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Format a full 402 response body.

    Args:
        invoice: Bolt11 invoice string.
        macaroon: Base64url-encoded macaroon.
        payment_hash: Payment hash (hex).
        amount_sats: Amount in satoshis.
        description: Invoice description.

    Returns:
        Dict suitable for JSON response.
    """
    return {
        "status": 402,
        "message": "Payment Required",
        "paymentHash": payment_hash,
        "invoice": invoice,
        "macaroon": macaroon,
        "amountSats": amount_sats,
        "description": description,
        "protocol": "L402",
        "instructions": {
            "step1": "Pay the Lightning invoice above",
            "step2": "Get the preimage from the payment receipt",
            "step3": "Retry the request with header: Authorization: L402 <macaroon>:<preimage>",
        },
    }


def parse_authorization(auth_header: Optional[str]) -> Optional[L402Credentials]:
    """
    Parse an Authorization: L402 header.

    Format: L402 <macaroon>:<preimage>

    Args:
        auth_header: Full Authorization header value.

    Returns:
        L402Credentials or None if parsing fails.
    """
    if not auth_header or not isinstance(auth_header, str):
        return None

    trimmed = auth_header.strip()

    # Check for L402 prefix (case-insensitive)
    if not trimmed.lower().startswith("l402 "):
        return None

    credentials = trimmed[5:].strip()
    colon_idx = credentials.find(":")
    if colon_idx == -1:
        return None

    macaroon = credentials[:colon_idx]
    preimage = credentials[colon_idx + 1:]

    if not macaroon or not preimage:
        return None

    return L402Credentials(macaroon=macaroon, preimage=preimage)
