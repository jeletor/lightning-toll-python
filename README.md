# ⚡ lightning-toll (Python)

L402 Lightning paywalls for FastAPI. Monetize any API endpoint with Bitcoin Lightning micropayments.

Add a paywall to any FastAPI route with a single `Depends()` call. Clients pay a Lightning invoice, retry with proof, and get access. No API keys to manage, no billing system, no Stripe. Just Lightning.

Python equivalent of [`lightning-toll`](https://www.npmjs.com/package/lightning-toll) (Node.js). Same L402 protocol, interoperable macaroon format.

## Installation

```bash
pip install git+https://github.com/jeletor/lightning-toll-python.git
```

With FastAPI extras:

```bash
pip install "lightning-toll[fastapi] @ git+https://github.com/jeletor/lightning-toll-python.git"
```

## Quick Start

### Server (FastAPI)

```python
import os
from fastapi import Depends, FastAPI
from lightning_toll import create_toll

app = FastAPI()

toll = create_toll(
    wallet_url=os.environ["NWC_URL"],
    secret=os.environ["TOLL_SECRET"],
)

@app.get("/api/joke")
async def joke(payment=Depends(toll(sats=5, description="Random joke"))):
    return {"joke": "Why did the sat cross the mempool? To get to the other chain."}
```

### Client (auto-pay)

```python
from lightning_toll.client import toll_fetch

response = await toll_fetch(
    "https://example.com/api/joke",
    wallet_url="nostr+walletconnect://...",
    max_sats=50,
)
data = response.json()
```

## How L402 Works

```
Client                                  Server
  |                                       |
  |  GET /api/joke                        |
  |-------------------------------------->|
  |                                       |
  |  402 Payment Required                 |
  |  WWW-Authenticate: L402               |
  |    invoice="lnbc...", macaroon="..."  |
  |<--------------------------------------|
  |                                       |
  |  [Client pays Lightning invoice]      |
  |                                       |
  |  GET /api/joke                        |
  |  Authorization: L402 <mac>:<preimage> |
  |-------------------------------------->|
  |                                       |
  |  200 OK { "joke": "..." }             |
  |<--------------------------------------|
```

## API Reference

### `create_toll(**kwargs) → Toll`

Create a toll gate instance.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `wallet_url` | `str` | required | NWC connection string |
| `secret` | `str` | required | HMAC secret for macaroon signing |
| `default_sats` | `int` | `10` | Default price if not specified per-route |
| `invoice_expiry` | `int` | `300` | Invoice expiry in seconds |
| `macaroon_expiry` | `int` | `3600` | Macaroon validity after payment |

### `toll(sats=N, description=...) → FastAPI Dependency`

Returns a FastAPI dependency that enforces payment.

```python
@app.get("/api/data")
async def data(payment=Depends(toll(sats=21))):
    return {"data": "...", "paid": True}
```

**Dynamic pricing:**

```python
@app.get("/api/search")
async def search(q: str, payment=Depends(toll(
    price=lambda req: 50 if req.query_params.get("premium") else 10
))):
    ...
```

### `toll.get_stats() → dict`

Returns payment statistics: total revenue, requests, unique payers, per-endpoint breakdown.

### `TollClient(wallet_url, max_sats) → client`

Auto-pay client that handles the 402 → pay → retry loop.

```python
from lightning_toll.client import TollClient

client = TollClient(wallet_url="nostr+walletconnect://...", max_sats=100)
response = await client.fetch("https://example.com/api/data")
```

### `toll_fetch(url, wallet_url, max_sats) → response`

Simple function wrapper around TollClient.

## Macaroon System

Macaroons are HMAC-SHA256 chained tokens with caveats:

| Caveat | Purpose |
|--------|---------|
| `expires_at` | Prevents replay after expiry |
| `endpoint` | Locks macaroon to specific route |
| `method` | Locks to HTTP method |
| `payment_hash` | Ties to specific payment |

Macaroons are cryptographically signed and verified with timing-safe comparison.

## NWC Wallet

The package includes a minimal NWC (Nostr Wallet Connect) client that handles:
- Invoice creation via NIP-47
- Payment status polling
- NIP-04 encrypted communication

Compatible with Alby Hub, LNbits, and any NWC-compatible wallet.

## Security

- Macaroons use HMAC-SHA256 with chained caveats — cannot be forged
- Preimage verification: `SHA256(preimage) == payment_hash`
- Timing-safe comparison for all signature checks
- Keep `TOLL_SECRET` private — it signs all macaroons
- Keep `NWC_URL` private — it controls the wallet

## Dependencies

- `websockets` — NWC relay communication
- `httpx` — HTTP client (for TollClient)
- `coincurve` — secp256k1 ECDH for NIP-04
- `pycryptodome` — AES-256-CBC for NIP-04 encryption

## Related

- [lightning-toll (Node.js)](https://github.com/jeletor/lightning-toll) — Express/Node.js version, same protocol
- [lightning-agent](https://github.com/jeletor/lightning-agent) — Lightning toolkit for AI agents
- [ai-wot](https://github.com/jeletor/ai-wot) — Decentralized trust protocol
- [login-with-lightning](https://github.com/jeletor/login-with-lightning) — LNURL-auth widget

## License

MIT
