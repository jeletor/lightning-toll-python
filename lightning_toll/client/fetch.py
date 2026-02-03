"""
Auto-pay fetch wrapper for L402-paywalled APIs.

When an endpoint returns 402, automatically pays the Lightning invoice
and retries the request with the L402 authorization header.

Direct port of the Node.js lightning-toll client/fetch.js.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Union

import httpx

from ..nwc import NwcWallet


@dataclass
class TollResponse:
    """Response from a toll-gated request."""
    status_code: int
    headers: Dict[str, str]
    body: Any
    paid: bool = False
    amount_sats: int = 0
    payment_hash: Optional[str] = None

    def json(self) -> Any:
        """Return body as parsed JSON (already parsed)."""
        return self.body

    @property
    def ok(self) -> bool:
        return 200 <= self.status_code < 300


async def auto_pay(
    url: str,
    wallet: Any,
    method: str = "GET",
    headers: Optional[Dict[str, str]] = None,
    body: Any = None,
    max_sats: int = 100,
    auto_retry: bool = True,
) -> TollResponse:
    """
    Fetch a URL with automatic L402 payment handling.

    Args:
        url: URL to fetch.
        wallet: NwcWallet instance.
        method: HTTP method.
        headers: Request headers.
        body: Request body (for POST/PUT).
        max_sats: Maximum sats to pay per request.
        auto_retry: Automatically pay and retry on 402.

    Returns:
        TollResponse with status, headers, body, and payment info.
    """
    if not wallet:
        raise ValueError("lightning-toll/client: wallet is required")

    req_headers = dict(headers or {})

    async with httpx.AsyncClient() as client:
        # Make the initial request
        kwargs: Dict[str, Any] = {"method": method, "url": url, "headers": req_headers}
        if body is not None:
            if isinstance(body, (dict, list)):
                kwargs["json"] = body
            else:
                kwargs["content"] = body

        response = await client.request(**kwargs)

        # If not 402, return as-is
        if response.status_code != 402:
            try:
                resp_body = response.json()
            except Exception:
                resp_body = response.text
            return TollResponse(
                status_code=response.status_code,
                headers=dict(response.headers),
                body=resp_body,
            )

        # If auto-retry is disabled, return the 402
        if not auto_retry:
            try:
                resp_body = response.json()
            except Exception:
                resp_body = response.text
            return TollResponse(
                status_code=402,
                headers=dict(response.headers),
                body=resp_body,
            )

        # Parse the 402 response
        try:
            challenge = response.json()
        except Exception:
            raise RuntimeError("lightning-toll/client: Could not parse 402 response body")

        invoice = challenge.get("invoice")
        macaroon = challenge.get("macaroon")

        if not invoice:
            raise RuntimeError("lightning-toll/client: 402 response missing invoice")
        if not macaroon:
            raise RuntimeError("lightning-toll/client: 402 response missing macaroon")

        # Check budget
        amount_sats = challenge.get("amountSats", 0)
        if amount_sats > max_sats:
            raise RuntimeError(
                f"lightning-toll/client: Price {amount_sats} sats exceeds budget of {max_sats} sats"
            )

        # Pay the invoice
        pay_result = await wallet.pay_invoice(invoice)
        if not pay_result or not pay_result.preimage:
            raise RuntimeError("lightning-toll/client: Payment failed — no preimage returned")

        # Retry with L402 authorization
        auth_header = f"L402 {macaroon}:{pay_result.preimage}"
        retry_headers = {**req_headers, "Authorization": auth_header}

        kwargs["headers"] = retry_headers
        retry_response = await client.request(**kwargs)

        try:
            resp_body = retry_response.json()
        except Exception:
            resp_body = retry_response.text

        return TollResponse(
            status_code=retry_response.status_code,
            headers=dict(retry_response.headers),
            body=resp_body,
            paid=True,
            amount_sats=amount_sats,
            payment_hash=challenge.get("paymentHash"),
        )


class TollClient:
    """
    Automated L402 payment client.

    Wraps HTTP requests with automatic Lightning payment handling.
    When an endpoint returns 402, the client pays the invoice and retries.

    Usage:
        client = TollClient(wallet_url="nostr+walletconnect://...")
        response = await client.fetch("https://api.example.com/data")
        data = response.json()
    """

    def __init__(
        self,
        wallet_url: Optional[str] = None,
        wallet: Optional[Any] = None,
        max_sats: int = 100,
        auto_retry: bool = True,
        headers: Optional[Dict[str, str]] = None,
    ):
        """
        Initialize the TollClient.

        Args:
            wallet_url: NWC connection string.
            wallet: Pre-created wallet instance.
            max_sats: Budget cap per request.
            auto_retry: Auto-pay and retry on 402.
            headers: Default headers for all requests.
        """
        if wallet is not None:
            self.wallet = wallet
        elif wallet_url:
            self.wallet = NwcWallet(wallet_url)
        else:
            raise ValueError("TollClient: wallet_url or wallet is required")

        self.max_sats = max_sats
        self.auto_retry = auto_retry
        self.default_headers = headers or {}

        # Track spending
        self.total_spent = 0
        self.request_count = 0
        self.payment_count = 0

    async def fetch(
        self,
        url: str,
        method: str = "GET",
        headers: Optional[Dict[str, str]] = None,
        body: Any = None,
        max_sats: Optional[int] = None,
        auto_retry: Optional[bool] = None,
    ) -> TollResponse:
        """
        Fetch a URL with automatic L402 payment handling.

        Args:
            url: URL to fetch.
            method: HTTP method.
            headers: Additional headers (merged with defaults).
            body: Request body.
            max_sats: Override budget cap for this request.
            auto_retry: Override auto-retry for this request.

        Returns:
            TollResponse with status, headers, body, and payment info.
        """
        self.request_count += 1

        merged_headers = {**self.default_headers, **(headers or {})}
        effective_max = max_sats if max_sats is not None else self.max_sats
        effective_retry = auto_retry if auto_retry is not None else self.auto_retry

        result = await auto_pay(
            url=url,
            wallet=self.wallet,
            method=method,
            headers=merged_headers,
            body=body,
            max_sats=effective_max,
            auto_retry=effective_retry,
        )

        if result.paid:
            self.payment_count += 1
            self.total_spent += result.amount_sats

        return result

    def get_stats(self) -> Dict[str, Any]:
        """Get spending statistics."""
        return {
            "total_spent": self.total_spent,
            "request_count": self.request_count,
            "payment_count": self.payment_count,
        }

    async def close(self) -> None:
        """Close the wallet connection."""
        if hasattr(self.wallet, "close"):
            await self.wallet.close()


async def toll_fetch(
    url: str,
    wallet_url: Optional[str] = None,
    wallet: Optional[Any] = None,
    max_sats: int = 50,
    method: str = "GET",
    headers: Optional[Dict[str, str]] = None,
    body: Any = None,
) -> TollResponse:
    """
    One-shot toll fetch with auto-payment.

    Convenience function that creates a temporary wallet, fetches the URL
    with automatic 402 handling, and returns the result.

    Args:
        url: URL to fetch.
        wallet_url: NWC connection string.
        wallet: Pre-created wallet instance.
        max_sats: Maximum sats to pay.
        method: HTTP method.
        headers: Request headers.
        body: Request body.

    Returns:
        TollResponse with status, headers, body, and payment info.
    """
    if wallet is not None:
        wallet_instance = wallet
    elif wallet_url:
        wallet_instance = NwcWallet(wallet_url)
    else:
        raise ValueError("toll_fetch: wallet_url or wallet is required")

    try:
        return await auto_pay(
            url=url,
            wallet=wallet_instance,
            method=method,
            headers=headers,
            body=body,
            max_sats=max_sats,
            auto_retry=True,
        )
    finally:
        if wallet_url and hasattr(wallet_instance, "close"):
            await wallet_instance.close()
