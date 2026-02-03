"""Tests for the L402 protocol module."""

import pytest

from lightning_toll.l402 import (
    format_challenge,
    format_challenge_body,
    parse_authorization,
)


class TestFormatChallenge:
    def test_basic_format(self):
        result = format_challenge("lnbc50n1pj...", "eyJpZCI...")
        assert result == 'L402 invoice="lnbc50n1pj...", macaroon="eyJpZCI..."'

    def test_with_real_values(self):
        invoice = "lnbc20n1pjq5xxxxxxxxxxxxxxxxxxxxxxx"
        macaroon = "eyJpZCI6ImFiYzEyMyIsImNhdmVhdHMiOltdLCJzaWduYXR1cmUiOiJ4eHgiCg"
        result = format_challenge(invoice, macaroon)
        assert result.startswith("L402 ")
        assert f'invoice="{invoice}"' in result
        assert f'macaroon="{macaroon}"' in result


class TestFormatChallengeBody:
    def test_includes_all_fields(self):
        body = format_challenge_body(
            invoice="lnbc50n1...",
            macaroon="eyJpZCI...",
            payment_hash="abc123",
            amount_sats=5,
            description="Test invoice",
        )
        assert body["status"] == 402
        assert body["message"] == "Payment Required"
        assert body["invoice"] == "lnbc50n1..."
        assert body["macaroon"] == "eyJpZCI..."
        assert body["paymentHash"] == "abc123"
        assert body["amountSats"] == 5
        assert body["description"] == "Test invoice"
        assert body["protocol"] == "L402"
        assert "instructions" in body
        assert "step1" in body["instructions"]
        assert "step2" in body["instructions"]
        assert "step3" in body["instructions"]

    def test_null_description(self):
        body = format_challenge_body(
            invoice="lnbc...",
            macaroon="eyJ...",
            payment_hash="abc",
            amount_sats=1,
        )
        assert body["description"] is None


class TestParseAuthorization:
    def test_valid_l402_header(self):
        result = parse_authorization("L402 eyJpZCI6ImFiYzEyMyJ9:deadbeef0123")
        assert result is not None
        assert result.macaroon == "eyJpZCI6ImFiYzEyMyJ9"
        assert result.preimage == "deadbeef0123"

    def test_case_insensitive_prefix(self):
        result = parse_authorization("l402 mac123:pre456")
        assert result is not None
        assert result.macaroon == "mac123"
        assert result.preimage == "pre456"

    def test_with_whitespace(self):
        result = parse_authorization("  L402   mac:pre  ")
        assert result is not None
        assert result.macaroon == "mac"
        assert result.preimage == "pre"

    def test_missing_colon(self):
        result = parse_authorization("L402 macaroonwithoutpreimage")
        assert result is None

    def test_empty_macaroon(self):
        result = parse_authorization("L402 :preimage")
        assert result is None

    def test_empty_preimage(self):
        result = parse_authorization("L402 macaroon:")
        assert result is None

    def test_not_l402(self):
        result = parse_authorization("Bearer token123")
        assert result is None

    def test_none_input(self):
        result = parse_authorization(None)
        assert result is None

    def test_empty_string(self):
        result = parse_authorization("")
        assert result is None

    def test_non_string_input(self):
        result = parse_authorization(12345)
        assert result is None

    def test_multiple_colons(self):
        """Preimage part can contain colons (shouldn't happen but be safe)."""
        result = parse_authorization("L402 mac:pre:extra:colons")
        assert result is not None
        assert result.macaroon == "mac"
        assert result.preimage == "pre:extra:colons"

    def test_preimage_with_long_hex(self):
        preimage = "a" * 64
        macaroon = "eyJpZCI6Imhhc2giLCJjYXZlYXRzIjpbXSwic2lnbmF0dXJlIjoic2lnIn0"
        result = parse_authorization(f"L402 {macaroon}:{preimage}")
        assert result is not None
        assert result.macaroon == macaroon
        assert result.preimage == preimage
