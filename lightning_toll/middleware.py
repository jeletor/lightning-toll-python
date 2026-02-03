"""
FastAPI middleware / dependency logic for L402 toll gates.

Provides the core request handling: check for L402 auth, verify macaroons,
issue 402 challenges with Lightning invoices.

Direct port of the Node.js lightning-toll middleware.js.
"""

from __future__ import annotations

import asyncio
import re
import time
from typing import Any, Callable, Dict, Optional, Union

from .l402 import format_challenge, format_challenge_body, parse_authorization
from .macaroon import (
    create_macaroon,
    decode_macaroon,
    verify_macaroon,
    verify_preimage,
)
from .stats import TollStats


def parse_window(window: Union[str, int, None]) -> int:
    """
    Parse a time window string like '1h', '30m', '1d' to milliseconds.

    Args:
        window: Time window as string ('1h', '30m', '1d') or milliseconds as int.

    Returns:
        Window duration in milliseconds.
    """
    if isinstance(window, int):
        return window
    if not window or not isinstance(window, str):
        return 3600000  # default 1h

    match = re.match(r"^(\d+)(ms|s|m|h|d)$", window)
    if not match:
        return 3600000

    num = int(match.group(1))
    unit = match.group(2)
    multipliers = {"ms": 1, "s": 1000, "m": 60000, "h": 3600000, "d": 86400000}
    return num * multipliers.get(unit, 3600000)


def get_client_id(request: Any) -> str:
    """
    Get client identifier from a request.

    Supports FastAPI/Starlette Request objects.
    Prefers X-Forwarded-For, falls back to client host.

    Args:
        request: FastAPI Request object.

    Returns:
        Client identifier string.
    """
    # Try X-Forwarded-For first
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()

    # Fall back to client host
    if hasattr(request, "client") and request.client:
        return request.client.host or "unknown"

    return "unknown"


class TollMiddleware:
    """
    Core toll gate logic for a specific route configuration.

    This is created by Toll.__call__ and used as a FastAPI dependency.
    """

    def __init__(self, config: Dict[str, Any], route_opts: Dict[str, Any]):
        self.config = config
        self.route_opts = route_opts

        # Free tier tracking: client_id → { count, window_start }
        self._free_tier_map: Dict[str, Dict[str, Any]] = {}
        self._free_requests = route_opts.get("free_requests", 0)
        self._free_window_ms = parse_window(route_opts.get("free_window", "1h"))

    def _resolve_price(self, request: Any) -> int:
        """Resolve the price for this request."""
        price_fn = self.route_opts.get("price")
        if callable(price_fn):
            return price_fn(request)
        sats = self.route_opts.get("sats")
        if isinstance(sats, int):
            return sats
        return self.config["default_sats"]

    def _resolve_description(self, request: Any) -> str:
        """Resolve the description for this request."""
        desc = self.route_opts.get("description")
        if callable(desc):
            return desc(request)
        if isinstance(desc, str):
            return desc
        return f"API access: {request.method} {request.url.path}"

    def _check_free_tier(self, client_id: str) -> bool:
        """Check if client has free requests remaining."""
        if self._free_requests <= 0:
            return False

        now = time.time() * 1000  # ms
        entry = self._free_tier_map.get(client_id)

        if not entry or (now - entry["window_start"]) > self._free_window_ms:
            entry = {"count": 0, "window_start": now}
            self._free_tier_map[client_id] = entry

        if entry["count"] < self._free_requests:
            entry["count"] += 1
            return True

        return False

    async def __call__(self, request: Any) -> Dict[str, Any]:
        """
        Process a request through the toll gate.

        This is the FastAPI dependency function.

        Args:
            request: FastAPI Request object.

        Returns:
            Payment info dict (attached as the dependency result).

        Raises:
            HTTPException: 402 if payment is required, 401 if auth is invalid.
        """
        from fastapi import HTTPException
        from fastapi.responses import JSONResponse

        client_id = get_client_id(request)
        endpoint = request.url.path

        wallet = self.config["wallet"]
        secret = self.config["secret"]
        stats: TollStats = self.config["stats"]
        invoice_expiry = self.config["invoice_expiry"]
        macaroon_expiry = self.config["macaroon_expiry"]
        bind_endpoint = self.config["bind_endpoint"]
        bind_method = self.config["bind_method"]
        bind_ip = self.config["bind_ip"]
        on_payment = self.config.get("on_payment")

        # Check for existing L402 authorization
        auth_header = request.headers.get("authorization")
        l402_creds = parse_authorization(auth_header)

        if l402_creds:
            # Client is presenting credentials — verify them
            decoded = decode_macaroon(l402_creds.macaroon)
            if not decoded:
                raise HTTPException(status_code=401, detail={"error": "Invalid macaroon"})

            # Verify macaroon signature and caveats
            context = {}
            if bind_endpoint:
                context["endpoint"] = endpoint
            if bind_method:
                context["method"] = request.method
            if bind_ip:
                context["ip"] = client_id

            mac_result = verify_macaroon(secret, decoded, context)
            if not mac_result.valid:
                raise HTTPException(status_code=401, detail={"error": mac_result.error})

            # Verify preimage matches payment hash
            if not verify_preimage(l402_creds.preimage, decoded.id):
                raise HTTPException(
                    status_code=401,
                    detail={"error": "Invalid preimage — does not match payment hash"},
                )

            # Record the payment in stats
            price = self._resolve_price(request)
            stats.record(endpoint, True, price, client_id, decoded.id)

            # Return payment info
            return {
                "paid": True,
                "payment_hash": decoded.id,
                "amount_sats": price,
                "client_id": client_id,
            }

        # No L402 credentials — check free tier
        if self._check_free_tier(client_id):
            stats.record(endpoint, False, 0, client_id)
            return {"paid": False, "free": True, "client_id": client_id}

        # No auth, no free tier — issue a 402 challenge
        try:
            amount_sats = self._resolve_price(request)
            description = self._resolve_description(request)

            # Create Lightning invoice via wallet
            invoice_result = await wallet.create_invoice(
                amount_sats=amount_sats,
                description=description,
                expiry=invoice_expiry,
            )

            if not invoice_result or not invoice_result.invoice or not invoice_result.payment_hash:
                raise HTTPException(
                    status_code=500,
                    detail={"error": "Failed to create Lightning invoice"},
                )

            # Create macaroon bound to this payment
            expires_at = int(time.time()) + macaroon_expiry
            mac_opts: Dict[str, Any] = {
                "payment_hash": invoice_result.payment_hash,
                "expires_at": expires_at,
            }
            if bind_endpoint:
                mac_opts["endpoint"] = endpoint
            if bind_method:
                mac_opts["method"] = request.method
            if bind_ip:
                mac_opts["ip"] = client_id

            macaroon = create_macaroon(secret, **mac_opts)

            # Build 402 response
            www_auth = format_challenge(invoice_result.invoice, macaroon.raw)
            body = format_challenge_body(
                invoice=invoice_result.invoice,
                macaroon=macaroon.raw,
                payment_hash=invoice_result.payment_hash,
                amount_sats=amount_sats,
                description=description,
            )

            # Fire on_payment callback when payment is received (async, non-blocking)
            if on_payment:
                async def _monitor_payment():
                    try:
                        result = await wallet.wait_for_payment(
                            invoice_result.payment_hash,
                            timeout_ms=invoice_expiry * 1000,
                        )
                        if result.paid:
                            try:
                                on_payment({
                                    "payment_hash": invoice_result.payment_hash,
                                    "amount_sats": amount_sats,
                                    "endpoint": endpoint,
                                    "preimage": result.preimage,
                                    "settled_at": result.settled_at,
                                    "client_id": client_id,
                                })
                            except Exception:
                                pass  # Don't crash on callback errors
                    except Exception:
                        pass  # Timeout or error — ignore

                asyncio.create_task(_monitor_payment())

            # Return 402 with the challenge
            raise HTTPException(
                status_code=402,
                detail=body,
                headers={"WWW-Authenticate": www_auth},
            )

        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(
                status_code=500,
                detail={"error": f"Toll booth error: {str(e)}"},
            )
