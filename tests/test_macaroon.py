"""Tests for the macaroon module."""

import time

import pytest

from lightning_toll.macaroon import (
    create_macaroon,
    decode_macaroon,
    verify_macaroon,
    verify_preimage,
)


SECRET = "test-secret-key-for-hmac-signing"
PAYMENT_HASH = "a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2"


class TestCreateMacaroon:
    def test_creates_with_payment_hash(self):
        mac = create_macaroon(SECRET, payment_hash=PAYMENT_HASH)
        assert mac.id == PAYMENT_HASH
        assert isinstance(mac.signature, str)
        assert len(mac.signature) == 64  # 32 bytes hex
        assert isinstance(mac.raw, str)
        assert len(mac.raw) > 0

    def test_creates_with_caveats(self):
        mac = create_macaroon(
            SECRET,
            payment_hash=PAYMENT_HASH,
            expires_at=1700000000,
            endpoint="/api/data",
            method="GET",
        )
        assert len(mac.caveats) == 3
        assert "expires_at = 1700000000" in mac.caveats
        assert "endpoint = /api/data" in mac.caveats
        assert "method = GET" in mac.caveats

    def test_requires_secret(self):
        with pytest.raises(ValueError, match="secret is required"):
            create_macaroon("", payment_hash=PAYMENT_HASH)

    def test_requires_payment_hash(self):
        with pytest.raises(ValueError, match="payment_hash is required"):
            create_macaroon(SECRET)

    def test_caveat_order_matches_node(self):
        """Caveats must be in the same order as Node.js: expires_at, endpoint, method, ip."""
        mac = create_macaroon(
            SECRET,
            payment_hash=PAYMENT_HASH,
            expires_at=1700000000,
            endpoint="/api/test",
            method="POST",
            ip="1.2.3.4",
        )
        assert mac.caveats == [
            "expires_at = 1700000000",
            "endpoint = /api/test",
            "method = POST",
            "ip = 1.2.3.4",
        ]


class TestEncodeDecode:
    def test_roundtrip(self):
        """Create → encode → decode should preserve all fields."""
        mac = create_macaroon(
            SECRET,
            payment_hash=PAYMENT_HASH,
            expires_at=1700000000,
            endpoint="/api/data",
        )

        decoded = decode_macaroon(mac.raw)
        assert decoded is not None
        assert decoded.id == mac.id
        assert decoded.caveats == mac.caveats
        assert decoded.signature == mac.signature

    def test_decode_invalid_base64(self):
        assert decode_macaroon("not-valid-base64!!!") is None

    def test_decode_invalid_json(self):
        import base64

        bad = base64.urlsafe_b64encode(b"not json").decode().rstrip("=")
        assert decode_macaroon(bad) is None

    def test_decode_missing_fields(self):
        import base64
        import json

        bad = base64.urlsafe_b64encode(json.dumps({"id": "x"}).encode()).decode().rstrip("=")
        assert decode_macaroon(bad) is None


class TestVerifyMacaroon:
    def test_valid_macaroon(self):
        mac = create_macaroon(
            SECRET,
            payment_hash=PAYMENT_HASH,
            expires_at=int(time.time()) + 3600,
            endpoint="/api/data",
            method="GET",
        )
        decoded = decode_macaroon(mac.raw)
        result = verify_macaroon(SECRET, decoded, {"endpoint": "/api/data", "method": "GET"})
        assert result.valid is True
        assert result.payment_hash == PAYMENT_HASH

    def test_wrong_secret_fails(self):
        mac = create_macaroon(SECRET, payment_hash=PAYMENT_HASH)
        decoded = decode_macaroon(mac.raw)
        result = verify_macaroon("wrong-secret", decoded)
        assert result.valid is False
        assert "signature" in result.error.lower()

    def test_expired_macaroon(self):
        mac = create_macaroon(
            SECRET,
            payment_hash=PAYMENT_HASH,
            expires_at=int(time.time()) - 100,  # already expired
        )
        decoded = decode_macaroon(mac.raw)
        result = verify_macaroon(SECRET, decoded)
        assert result.valid is False
        assert "expired" in result.error.lower()

    def test_endpoint_mismatch(self):
        mac = create_macaroon(
            SECRET,
            payment_hash=PAYMENT_HASH,
            expires_at=int(time.time()) + 3600,
            endpoint="/api/data",
        )
        decoded = decode_macaroon(mac.raw)
        result = verify_macaroon(SECRET, decoded, {"endpoint": "/api/other"})
        assert result.valid is False
        assert "endpoint mismatch" in result.error.lower()

    def test_method_mismatch(self):
        mac = create_macaroon(
            SECRET,
            payment_hash=PAYMENT_HASH,
            expires_at=int(time.time()) + 3600,
            method="GET",
        )
        decoded = decode_macaroon(mac.raw)
        result = verify_macaroon(SECRET, decoded, {"method": "POST"})
        assert result.valid is False
        assert "method mismatch" in result.error.lower()

    def test_method_case_insensitive(self):
        mac = create_macaroon(
            SECRET,
            payment_hash=PAYMENT_HASH,
            expires_at=int(time.time()) + 3600,
            method="GET",
        )
        decoded = decode_macaroon(mac.raw)
        result = verify_macaroon(SECRET, decoded, {"method": "get"})
        assert result.valid is True

    def test_no_context_skips_caveats(self):
        """If context doesn't include endpoint/method, those caveats are skipped."""
        mac = create_macaroon(
            SECRET,
            payment_hash=PAYMENT_HASH,
            expires_at=int(time.time()) + 3600,
            endpoint="/api/data",
            method="GET",
        )
        decoded = decode_macaroon(mac.raw)
        result = verify_macaroon(SECRET, decoded, {})
        assert result.valid is True

    def test_tampered_caveat_fails(self):
        mac = create_macaroon(
            SECRET,
            payment_hash=PAYMENT_HASH,
            expires_at=int(time.time()) + 3600,
        )
        decoded = decode_macaroon(mac.raw)
        # Tamper with the caveat
        decoded.caveats[0] = "expires_at = 9999999999"
        result = verify_macaroon(SECRET, decoded)
        assert result.valid is False
        assert "signature" in result.error.lower()


class TestVerifyPreimage:
    def test_valid_preimage(self):
        import hashlib

        preimage = "deadbeef" * 8  # 32 bytes hex
        payment_hash = hashlib.sha256(bytes.fromhex(preimage)).hexdigest()
        assert verify_preimage(preimage, payment_hash) is True

    def test_invalid_preimage(self):
        assert verify_preimage("0000" * 8, "ffff" * 8) is False

    def test_empty_inputs(self):
        assert verify_preimage("", "abc123") is False
        assert verify_preimage("abc123", "") is False
        assert verify_preimage(None, None) is False

    def test_non_hex_input(self):
        assert verify_preimage("not-hex", "also-not-hex") is False


class TestInteroperability:
    """Test that Python macaroons match Node.js format exactly."""

    def test_json_wire_format(self):
        """The base64url-encoded payload should be valid JSON with id, caveats, signature."""
        import base64
        import json

        mac = create_macaroon(SECRET, payment_hash=PAYMENT_HASH)

        # Decode the raw format
        padded = mac.raw + "=" * (-len(mac.raw) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded))

        assert "id" in payload
        assert "caveats" in payload
        assert "signature" in payload
        assert isinstance(payload["caveats"], list)
        assert payload["id"] == PAYMENT_HASH

    def test_no_base64_padding(self):
        """Node.js base64url doesn't pad. Python should match."""
        mac = create_macaroon(SECRET, payment_hash=PAYMENT_HASH)
        assert "=" not in mac.raw
