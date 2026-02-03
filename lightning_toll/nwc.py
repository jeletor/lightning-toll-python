"""
Minimal NWC (Nostr Wallet Connect) client for Python.

Implements NIP-47 to communicate with Lightning wallets via Nostr relays.
Supports: make_invoice, lookup_invoice.

NWC Flow:
1. Parse the NWC URL to get: relay URL, wallet pubkey, secret key
2. Connect to the relay via WebSocket
3. Send NIP-47 encrypted requests (kind 23194)
4. Receive encrypted responses (kind 23195)
5. Encryption uses NIP-04 (shared secret from ECDH)
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import secrets
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional
from urllib.parse import parse_qs, urlparse

import websockets
import websockets.client

from .crypto import get_public_key, nip04_decrypt, nip04_encrypt


@dataclass
class NwcConfig:
    """Parsed NWC URL configuration."""
    relay_url: str
    wallet_pubkey: str
    secret_key: str
    client_pubkey: str  # derived from secret_key


@dataclass
class InvoiceResult:
    """Result from create_invoice."""
    invoice: str
    payment_hash: str


@dataclass
class PaymentResult:
    """Result from pay_invoice."""
    preimage: str
    payment_hash: str


@dataclass
class LookupResult:
    """Result from lookup_invoice."""
    paid: bool
    preimage: Optional[str] = None
    settled_at: Optional[int] = None


def parse_nwc_url(nwc_url: str) -> NwcConfig:
    """
    Parse an NWC (Nostr Wallet Connect) URL.

    Format: nostr+walletconnect://<wallet_pubkey>?relay=<relay_url>&secret=<secret_key>

    Args:
        nwc_url: The NWC connection string.

    Returns:
        NwcConfig with relay_url, wallet_pubkey, secret_key, client_pubkey.
    """
    parsed = urlparse(nwc_url)

    if parsed.scheme != "nostr+walletconnect":
        raise ValueError(f"Invalid NWC URL scheme: {parsed.scheme} (expected nostr+walletconnect)")

    wallet_pubkey = parsed.netloc or parsed.hostname or ""
    if not wallet_pubkey:
        raise ValueError("NWC URL missing wallet pubkey")

    params = parse_qs(parsed.query)
    relay_url = params.get("relay", [None])[0]
    secret_key = params.get("secret", [None])[0]

    if not relay_url:
        raise ValueError("NWC URL missing relay parameter")
    if not secret_key:
        raise ValueError("NWC URL missing secret parameter")

    client_pubkey = get_public_key(secret_key)

    return NwcConfig(
        relay_url=relay_url,
        wallet_pubkey=wallet_pubkey,
        secret_key=secret_key,
        client_pubkey=client_pubkey,
    )


def _serialize_event(event: Dict[str, Any]) -> str:
    """Serialize a Nostr event for signing (NIP-01)."""
    return json.dumps(
        [
            0,
            event["pubkey"],
            event["created_at"],
            event["kind"],
            event["tags"],
            event["content"],
        ],
        separators=(",", ":"),
        ensure_ascii=False,
    )


def _sign_event(event: Dict[str, Any], secret_key: str) -> Dict[str, Any]:
    """Sign a Nostr event with the given secret key."""
    from coincurve import PrivateKey

    serialized = _serialize_event(event)
    event_hash = hashlib.sha256(serialized.encode("utf-8")).digest()
    event["id"] = event_hash.hex()

    sk = PrivateKey(bytes.fromhex(secret_key))
    sig = sk.sign_schnorr(event_hash)
    event["sig"] = sig.hex()

    return event


class NwcWallet:
    """
    Minimal NWC wallet client.

    Connects to a Nostr relay and sends NIP-47 requests to a wallet service.
    """

    def __init__(self, nwc_url: str):
        """
        Initialize the NWC wallet client.

        Args:
            nwc_url: NWC connection string (nostr+walletconnect://...).
        """
        self.config = parse_nwc_url(nwc_url)
        self._ws: Optional[websockets.client.WebSocketClientProtocol] = None
        self._connected = False

    async def _ensure_connected(self) -> websockets.client.WebSocketClientProtocol:
        """Ensure we have an active WebSocket connection."""
        if self._ws is not None and self._connected:
            try:
                await self._ws.ping()
                return self._ws
            except Exception:
                self._connected = False
                self._ws = None

        self._ws = await websockets.connect(self.config.relay_url)
        self._connected = True
        return self._ws

    async def _send_nwc_request(
        self,
        method: str,
        params: Dict[str, Any],
        timeout_ms: int = 30000,
    ) -> Dict[str, Any]:
        """
        Send a NIP-47 request and wait for the response.

        Args:
            method: NWC method name (e.g., "make_invoice", "lookup_invoice").
            params: Method parameters.
            timeout_ms: Timeout in milliseconds.

        Returns:
            Parsed response result dict.
        """
        ws = await self._ensure_connected()

        # Build the NIP-47 request content
        request_content = json.dumps({"method": method, "params": params})

        # Encrypt with NIP-04
        encrypted_content = nip04_encrypt(
            self.config.secret_key,
            self.config.wallet_pubkey,
            request_content,
        )

        # Build the Nostr event (kind 23194 = NIP-47 request)
        event = {
            "kind": 23194,
            "pubkey": self.config.client_pubkey,
            "created_at": int(time.time()),
            "tags": [["p", self.config.wallet_pubkey]],
            "content": encrypted_content,
        }

        # Sign the event
        signed_event = _sign_event(event, self.config.secret_key)

        # Subscribe to responses first
        sub_id = secrets.token_hex(16)
        sub_filter = {
            "kinds": [23195],
            "authors": [self.config.wallet_pubkey],
            "#p": [self.config.client_pubkey],
            "#e": [signed_event["id"]],
        }
        await ws.send(json.dumps(["REQ", sub_id, sub_filter]))

        # Publish the request event
        await ws.send(json.dumps(["EVENT", signed_event]))

        # Wait for response
        timeout_sec = timeout_ms / 1000
        deadline = time.time() + timeout_sec

        try:
            while time.time() < deadline:
                remaining = deadline - time.time()
                if remaining <= 0:
                    break

                try:
                    raw_msg = await asyncio.wait_for(ws.recv(), timeout=remaining)
                except asyncio.TimeoutError:
                    break

                msg = json.loads(raw_msg)

                # Handle EVENT messages (NIP-47 response, kind 23195)
                if isinstance(msg, list) and len(msg) >= 3 and msg[0] == "EVENT" and msg[1] == sub_id:
                    response_event = msg[2]

                    # Decrypt the response
                    decrypted = nip04_decrypt(
                        self.config.secret_key,
                        self.config.wallet_pubkey,
                        response_event["content"],
                    )
                    result = json.loads(decrypted)

                    # Close subscription
                    await ws.send(json.dumps(["CLOSE", sub_id]))

                    if result.get("error"):
                        error = result["error"]
                        raise RuntimeError(
                            f"NWC error: {error.get('message', 'Unknown error')} "
                            f"(code: {error.get('code', 'N/A')})"
                        )

                    return result.get("result", {})

        finally:
            # Always try to close the subscription
            try:
                await ws.send(json.dumps(["CLOSE", sub_id]))
            except Exception:
                pass

        raise TimeoutError(f"NWC request timed out after {timeout_sec}s")

    async def create_invoice(
        self,
        amount_sats: int,
        description: str = "",
        expiry: int = 300,
    ) -> InvoiceResult:
        """
        Create a Lightning invoice via NWC.

        Args:
            amount_sats: Amount in satoshis.
            description: Invoice description.
            expiry: Expiry time in seconds.

        Returns:
            InvoiceResult with invoice and payment_hash.
        """
        # NWC uses millisats
        result = await self._send_nwc_request("make_invoice", {
            "amount": amount_sats * 1000,  # millisats
            "description": description,
            "expiry": expiry,
        })

        invoice = result.get("invoice", "")
        payment_hash = result.get("payment_hash", "")

        if not invoice:
            raise RuntimeError("NWC make_invoice returned no invoice")

        return InvoiceResult(invoice=invoice, payment_hash=payment_hash)

    async def pay_invoice(self, invoice: str) -> PaymentResult:
        """
        Pay a Lightning invoice via NWC.

        Args:
            invoice: Bolt11 invoice string.

        Returns:
            PaymentResult with preimage and payment_hash.
        """
        result = await self._send_nwc_request("pay_invoice", {
            "invoice": invoice,
        }, timeout_ms=60000)

        preimage = result.get("preimage", "")
        if not preimage:
            raise RuntimeError("NWC pay_invoice returned no preimage")

        return PaymentResult(
            preimage=preimage,
            payment_hash=result.get("payment_hash", ""),
        )

    async def lookup_invoice(self, payment_hash: str) -> LookupResult:
        """
        Look up an invoice by payment hash.

        Args:
            payment_hash: Hex-encoded payment hash.

        Returns:
            LookupResult with paid status, preimage, and settled_at.
        """
        result = await self._send_nwc_request("lookup_invoice", {
            "payment_hash": payment_hash,
        })

        paid = result.get("settled_at") is not None or result.get("preimage") is not None

        return LookupResult(
            paid=paid,
            preimage=result.get("preimage"),
            settled_at=result.get("settled_at"),
        )

    async def wait_for_payment(
        self,
        payment_hash: str,
        timeout_ms: int = 300000,
        poll_interval_ms: int = 2000,
    ) -> LookupResult:
        """
        Wait for an invoice to be paid by polling lookup_invoice.

        Args:
            payment_hash: Hex-encoded payment hash.
            timeout_ms: Maximum wait time in milliseconds.
            poll_interval_ms: Polling interval in milliseconds.

        Returns:
            LookupResult with paid status.
        """
        deadline = time.time() + (timeout_ms / 1000)
        poll_sec = poll_interval_ms / 1000

        while time.time() < deadline:
            try:
                result = await self.lookup_invoice(payment_hash)
                if result.paid:
                    return result
            except Exception:
                pass  # Ignore transient errors during polling

            await asyncio.sleep(poll_sec)

        return LookupResult(paid=False)

    async def close(self) -> None:
        """Close the WebSocket connection."""
        if self._ws is not None:
            try:
                await self._ws.close()
            except Exception:
                pass
            self._ws = None
            self._connected = False
