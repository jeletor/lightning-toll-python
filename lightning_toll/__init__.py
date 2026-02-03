"""
⚡ lightning-toll — L402 Lightning paywalls for FastAPI.

Monetize any API endpoint with Bitcoin Lightning micropayments.
Python/FastAPI equivalent of the lightning-toll npm package.

Usage:
    from lightning_toll import create_toll
    from fastapi import Depends

    toll = create_toll(wallet_url="nostr+walletconnect://...", secret="your-secret")

    @app.get("/api/data")
    async def data(payment=Depends(toll(sats=5))):
        return {"data": "...", "paid": payment["paid"]}
"""

from .l402 import (
    L402Credentials,
    format_challenge,
    format_challenge_body,
    parse_authorization,
)
from .macaroon import (
    Macaroon,
    VerifyResult,
    create_macaroon,
    decode_macaroon,
    verify_macaroon,
    verify_preimage,
)
from .stats import TollStats
from .toll import Toll, create_toll

__version__ = "0.1.0"

__all__ = [
    # Main API
    "create_toll",
    "Toll",
    # Macaroon
    "create_macaroon",
    "decode_macaroon",
    "verify_macaroon",
    "verify_preimage",
    "Macaroon",
    "VerifyResult",
    # L402
    "format_challenge",
    "format_challenge_body",
    "parse_authorization",
    "L402Credentials",
    # Stats
    "TollStats",
]
