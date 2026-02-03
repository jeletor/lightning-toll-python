"""Tests for the toll middleware with FastAPI."""

import hashlib
import time
from dataclasses import dataclass
from typing import Optional
from unittest.mock import AsyncMock, MagicMock

import pytest

from lightning_toll import create_toll
from lightning_toll.macaroon import create_macaroon


SECRET = "test-secret-for-toll-middleware"
PAYMENT_HASH = "b1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6b1b2"
PREIMAGE = "deadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeef"


@dataclass
class FakeInvoiceResult:
    invoice: str = "lnbc20n1pjqtest..."
    payment_hash: str = PAYMENT_HASH


@dataclass
class FakeLookupResult:
    paid: bool = False
    preimage: Optional[str] = None
    settled_at: Optional[int] = None


def make_fake_wallet():
    """Create a mock wallet for testing."""
    wallet = AsyncMock()
    wallet.create_invoice = AsyncMock(return_value=FakeInvoiceResult())
    wallet.lookup_invoice = AsyncMock(return_value=FakeLookupResult())
    wallet.wait_for_payment = AsyncMock(return_value=FakeLookupResult())
    return wallet


def make_fake_request(
    path: str = "/api/test",
    method: str = "GET",
    auth_header: Optional[str] = None,
    client_host: str = "127.0.0.1",
):
    """Create a mock FastAPI Request object."""
    request = MagicMock()
    request.url.path = path
    request.method = method
    request.headers = {}
    if auth_header:
        request.headers["authorization"] = auth_header
    request.client = MagicMock()
    request.client.host = client_host
    return request


class TestCreateToll:
    def test_creates_with_wallet_instance(self):
        wallet = make_fake_wallet()
        toll = create_toll(wallet=wallet, secret=SECRET)
        assert toll is not None
        assert toll.wallet is wallet

    def test_requires_secret(self):
        wallet = make_fake_wallet()
        with pytest.raises(ValueError, match="secret is required"):
            create_toll(wallet=wallet, secret="")

    def test_requires_wallet_or_url(self):
        with pytest.raises(ValueError, match="wallet_url or wallet is required"):
            create_toll(secret=SECRET)

    def test_requires_create_invoice_method(self):
        with pytest.raises(ValueError, match="create_invoice"):
            create_toll(wallet=object(), secret=SECRET)


class TestTollMiddleware:
    """Test the toll gate middleware behavior."""

    @pytest.mark.asyncio
    async def test_returns_402_without_auth(self):
        """Unauthenticated request should get a 402 challenge."""
        wallet = make_fake_wallet()
        toll = create_toll(wallet=wallet, secret=SECRET, default_sats=5)
        middleware = toll(sats=5)

        request = make_fake_request()

        # The middleware should raise an HTTPException with status 402
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc_info:
            await middleware(request)

        assert exc_info.value.status_code == 402
        body = exc_info.value.detail
        assert body["status"] == 402
        assert body["invoice"] == "lnbc20n1pjqtest..."
        assert body["amountSats"] == 5
        assert body["protocol"] == "L402"
        assert "macaroon" in body

    @pytest.mark.asyncio
    async def test_accepts_valid_l402_auth(self):
        """Request with valid L402 auth should pass through."""
        wallet = make_fake_wallet()
        toll = create_toll(
            wallet=wallet,
            secret=SECRET,
            bind_endpoint=False,
            bind_method=False,
        )
        middleware = toll(sats=5)

        # Create a valid macaroon and preimage
        preimage_bytes = bytes.fromhex(PREIMAGE)
        payment_hash = hashlib.sha256(preimage_bytes).hexdigest()

        mac = create_macaroon(
            SECRET,
            payment_hash=payment_hash,
            expires_at=int(time.time()) + 3600,
        )

        auth_header = f"L402 {mac.raw}:{PREIMAGE}"
        request = make_fake_request(auth_header=auth_header)

        result = await middleware(request)
        assert result["paid"] is True
        assert result["payment_hash"] == payment_hash

    @pytest.mark.asyncio
    async def test_rejects_invalid_macaroon(self):
        """Request with garbage macaroon should get 401."""
        wallet = make_fake_wallet()
        toll = create_toll(wallet=wallet, secret=SECRET)
        middleware = toll(sats=5)

        request = make_fake_request(auth_header="L402 garbage:deadbeef")

        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc_info:
            await middleware(request)

        assert exc_info.value.status_code == 401

    @pytest.mark.asyncio
    async def test_rejects_wrong_preimage(self):
        """Request with wrong preimage should get 401."""
        wallet = make_fake_wallet()
        toll = create_toll(
            wallet=wallet,
            secret=SECRET,
            bind_endpoint=False,
            bind_method=False,
        )
        middleware = toll(sats=5)

        mac = create_macaroon(
            SECRET,
            payment_hash=PAYMENT_HASH,
            expires_at=int(time.time()) + 3600,
        )

        # Wrong preimage (won't hash to PAYMENT_HASH)
        wrong_preimage = "0000000000000000000000000000000000000000000000000000000000000000"
        auth_header = f"L402 {mac.raw}:{wrong_preimage}"
        request = make_fake_request(auth_header=auth_header)

        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc_info:
            await middleware(request)

        assert exc_info.value.status_code == 401
        assert "preimage" in exc_info.value.detail["error"].lower()

    @pytest.mark.asyncio
    async def test_rejects_expired_macaroon(self):
        """Request with expired macaroon should get 401."""
        wallet = make_fake_wallet()
        toll = create_toll(
            wallet=wallet,
            secret=SECRET,
            bind_endpoint=False,
            bind_method=False,
        )
        middleware = toll(sats=5)

        preimage_bytes = bytes.fromhex(PREIMAGE)
        payment_hash = hashlib.sha256(preimage_bytes).hexdigest()

        mac = create_macaroon(
            SECRET,
            payment_hash=payment_hash,
            expires_at=int(time.time()) - 100,  # already expired
        )

        auth_header = f"L402 {mac.raw}:{PREIMAGE}"
        request = make_fake_request(auth_header=auth_header)

        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc_info:
            await middleware(request)

        assert exc_info.value.status_code == 401
        assert "expired" in exc_info.value.detail["error"].lower()

    @pytest.mark.asyncio
    async def test_free_tier(self):
        """Free tier requests should pass through without payment."""
        wallet = make_fake_wallet()
        toll = create_toll(wallet=wallet, secret=SECRET)
        middleware = toll(sats=5, free_requests=3, free_window="1h")

        request = make_fake_request(client_host="10.0.0.1")

        # First 3 requests should be free
        for _ in range(3):
            result = await middleware(request)
            assert result["paid"] is False
            assert result["free"] is True

        # 4th request should require payment
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc_info:
            await middleware(request)
        assert exc_info.value.status_code == 402

    @pytest.mark.asyncio
    async def test_stats_tracked(self):
        """Stats should be updated on paid requests."""
        wallet = make_fake_wallet()
        toll = create_toll(
            wallet=wallet,
            secret=SECRET,
            bind_endpoint=False,
            bind_method=False,
        )
        middleware = toll(sats=10)

        preimage_bytes = bytes.fromhex(PREIMAGE)
        payment_hash = hashlib.sha256(preimage_bytes).hexdigest()

        mac = create_macaroon(
            SECRET,
            payment_hash=payment_hash,
            expires_at=int(time.time()) + 3600,
        )

        auth_header = f"L402 {mac.raw}:{PREIMAGE}"
        request = make_fake_request(auth_header=auth_header)

        await middleware(request)

        stats = toll.stats.to_dict()
        assert stats["totalRevenue"] == 10
        assert stats["totalPaid"] == 1
        assert stats["totalRequests"] == 1


class TestTollDashboard:
    def test_dashboard_returns_stats(self):
        wallet = make_fake_wallet()
        toll = create_toll(wallet=wallet, secret=SECRET)

        # Record some stats directly
        toll.stats.record("/api/test", True, 5, "client1", "hash1")
        toll.stats.record("/api/test", True, 5, "client2", "hash2")
        toll.stats.record("/api/other", False, 0, "client3")

        data = toll.dashboard_data()
        assert data["totalRevenue"] == 10
        assert data["totalPaid"] == 2
        assert data["totalRequests"] == 3
        assert data["uniquePayers"] == 2
        assert "/api/test" in data["endpoints"]
        assert "/api/other" in data["endpoints"]
