"""
⚡ lightning-toll FastAPI Demo

Complete working example of L402 Lightning paywalls with FastAPI.

Run:
    pip install -e ".[dev]"
    NWC_URL="nostr+walletconnect://..." TOLL_SECRET="your-secret" python examples/fastapi_demo.py

Or without a real wallet (uses a mock for testing):
    python examples/fastapi_demo.py
"""

import hashlib
import os
import secrets
import time

import uvicorn
from fastapi import Depends, FastAPI, Request

# --- Mock wallet for demo (when no NWC_URL is provided) ---


class MockWallet:
    """Mock wallet that generates fake invoices for testing the L402 flow."""

    async def create_invoice(self, amount_sats: int, description: str = "", expiry: int = 300):
        from dataclasses import dataclass

        payment_hash = hashlib.sha256(secrets.token_bytes(32)).hexdigest()

        @dataclass
        class Result:
            invoice: str
            payment_hash: str

        return Result(
            invoice=f"lnbc{amount_sats}0n1demo{secrets.token_hex(20)}",
            payment_hash=payment_hash,
        )

    async def wait_for_payment(self, payment_hash: str, timeout_ms: int = 300000):
        from dataclasses import dataclass

        @dataclass
        class Result:
            paid: bool = False
            preimage: str = None

        return Result()

    async def close(self):
        pass


# --- Setup ---

app = FastAPI(
    title="lightning-toll Demo",
    description="L402 Lightning paywall demo with FastAPI",
    version="0.1.0",
)

# Create the toll gate
nwc_url = os.environ.get("NWC_URL")
toll_secret = os.environ.get("TOLL_SECRET", "demo-secret-change-me-in-production")

if nwc_url:
    from lightning_toll import create_toll

    toll = create_toll(wallet_url=nwc_url, secret=toll_secret)
    print("⚡ Using real NWC wallet")
else:
    from lightning_toll import create_toll

    toll = create_toll(wallet=MockWallet(), secret=toll_secret)
    print("🧪 Using mock wallet (set NWC_URL for real payments)")


# --- Routes ---


@app.get("/")
async def root():
    """Welcome page with available endpoints."""
    return {
        "service": "lightning-toll demo",
        "description": "L402 Lightning paywall demo for FastAPI",
        "endpoints": {
            "GET /api/joke": {"price": "5 sats", "description": "Random programming joke"},
            "GET /api/time": {"price": "1 sat", "description": "Current server time"},
            "GET /api/fortune": {"price": "10 sats", "description": "Bitcoin fortune cookie"},
            "GET /api/free-tier": {
                "price": "21 sats (3 free/hr)",
                "description": "Free tier demo",
            },
            "GET /api/stats": {"price": "Free", "description": "Revenue dashboard"},
        },
        "how_to_pay": {
            "step1": "GET any paid endpoint to receive a 402 + Lightning invoice",
            "step2": "Pay the invoice with any Lightning wallet",
            "step3": "Retry with Authorization: L402 <macaroon>:<preimage>",
        },
    }


@app.get("/api/joke")
async def joke(payment=Depends(toll(sats=5, description="Random programming joke"))):
    """Get a random programming joke — 5 sats."""
    import random

    jokes = [
        "Why do programmers prefer dark mode? Because light attracts bugs.",
        "A SQL query walks into a bar, walks up to two tables and asks: 'Can I join you?'",
        "There are only 10 types of people: those who understand binary and those who don't.",
        "Why was the JavaScript developer sad? Because he didn't Node how to Express himself.",
        "A programmer's wife says 'Go to the store and get a loaf of bread. If they have eggs, get a dozen.' He comes home with 12 loaves.",
        "!false — it's funny because it's true.",
        "Why do Bitcoin HODLers make bad comedians? They never sell the punchline.",
    ]
    return {"joke": random.choice(jokes), "payment": payment}


@app.get("/api/time")
async def server_time(payment=Depends(toll(sats=1, description="Current server time"))):
    """Get current server time — 1 sat."""
    import datetime

    now = datetime.datetime.now(datetime.timezone.utc)
    return {
        "time": now.isoformat(),
        "unix": int(now.timestamp()),
        "block_height_estimate": "~" + str(int(now.timestamp()) // 600),
        "payment": payment,
    }


@app.get("/api/fortune")
async def fortune(payment=Depends(toll(sats=10, description="Bitcoin fortune cookie"))):
    """Get a Bitcoin-themed fortune — 10 sats."""
    import random

    fortunes = [
        "The blocks will keep coming. So will you.",
        "Your next UTXO will be your luckiest.",
        "A wise node operator once said: 'Fees are just applause for miners.'",
        "You will find a forgotten seed phrase in an unexpected place.",
        "The Lightning Network predicts fast payments in your future.",
        "Stack sats. Stay humble. The rest follows.",
        "Your channel capacity will grow like your conviction.",
    ]
    return {"fortune": random.choice(fortunes), "payment": payment}


@app.get("/api/free-tier")
async def free_tier(
    payment=Depends(
        toll(sats=21, free_requests=3, free_window="1h", description="Free tier demo")
    ),
):
    """Free tier demo — 3 free per hour, then 21 sats."""
    return {
        "message": "You accessed the free-tier endpoint!",
        "free": payment.get("free", False),
        "paid": payment.get("paid", False),
    }


@app.get("/api/stats")
async def stats():
    """Revenue dashboard — free."""
    return toll.dashboard_data()


# --- Run ---

if __name__ == "__main__":
    print("\n⚡ lightning-toll FastAPI Demo")
    print("=" * 40)
    print("Endpoints:")
    print("  GET /           — Welcome page")
    print("  GET /api/joke   — 5 sats")
    print("  GET /api/time   — 1 sat")
    print("  GET /api/fortune — 10 sats")
    print("  GET /api/free-tier — 3 free/hr, then 21 sats")
    print("  GET /api/stats  — Free dashboard")
    print()
    print("Test:")
    print("  curl http://localhost:8402/api/joke")
    print()
    uvicorn.run(app, host="0.0.0.0", port=8402)
