# ⚡ lightning-toll (Python)

**Drop-in FastAPI dependency that puts any API endpoint behind a Lightning paywall.** Consumers pay per request with Bitcoin Lightning — no API keys to manage, no billing system, no Stripe integration. Just `pip install lightning-toll`, wrap your routes, and start earning sats.

Implements the [L402 protocol](https://docs.lightning.engineering/the-lightning-network/l402) with proper macaroon credentials. **Wire-format compatible** with the [Node.js lightning-toll](https://github.com/jeletor/lightning-toll) package — same macaroon format, same headers, fully interoperable.

## Installation

```bash
pip install lightning-toll

# With FastAPI support (recommended)
pip install "lightning-toll[fastapi]"

# For development
pip install "lightning-toll[dev]"
```

## Quick Start

### Server (5 lines)

```python
from fastapi import Depends, FastAPI
from lightning_toll import create_toll

app = FastAPI()
toll = create_toll(wallet_url="nostr+walletconnect://...", secret="your-hmac-secret")

@app.get("/api/joke")
async def joke(payment=Depends(toll(sats=5))):
    return {"joke": "Why do programmers prefer dark mode? Light attracts bugs."}
```

### Client (3 lines)

```python
from lightning_toll.client import toll_fetch

response = await toll_fetch("https://api.example.com/api/joke", wallet_url="nostr+walletconnect://...")
data = response.json()  # Paid 5 sats automatically
```

## How It Works — L402 Protocol

```
Client                                Server
  |                                      |
  |  GET /api/joke                       |
  |  ─────────────────────────────────>  |
  |                                      |
  |  402 Payment Required                |
  |  WWW-Authenticate: L402 invoice="..",|
  |    macaroon=".."                     |
  |  <─────────────────────────────────  |
  |                                      |
  |  [Pays Lightning invoice]            |
  |  [Gets preimage as receipt]          |
  |                                      |
  |  GET /api/joke                       |
  |  Authorization: L402 <mac>:<preimage>|
  |  ─────────────────────────────────>  |
  |                                      |
  |  200 OK { joke: "..." }             |
  |  <─────────────────────────────────  |
```

1. Client requests an endpoint without payment
2. Server returns **402 Payment Required** with a Lightning invoice and a macaroon
3. Client pays the invoice with any Lightning wallet
4. Client retries with `Authorization: L402 <macaroon>:<preimage>`
5. Server verifies the preimage matches the payment hash, checks the macaroon, and grants access

## API Reference

### `create_toll(**options) → Toll`

Creates a toll booth instance. Returns a `Toll` object for creating per-route dependencies.

```python
from lightning_toll import create_toll

toll = create_toll(
    # Required (one of)
    wallet_url="nostr+walletconnect://...",  # NWC connection string
    # wallet=my_wallet_instance,            # Or pre-created wallet

    # Required
    secret="hmac-signing-secret",            # For macaroon HMAC signatures

    # Optional
    default_sats=10,       # Default price if not set per-route (default: 10)
    invoice_expiry=300,    # Invoice expiry in seconds (default: 300 = 5 min)
    macaroon_expiry=3600,  # How long a paid macaroon stays valid (default: 3600 = 1 hour)
    bind_endpoint=True,    # Bind macaroons to the specific endpoint (default: True)
    bind_method=True,      # Bind macaroons to the HTTP method (default: True)
    bind_ip=False,         # Bind macaroons to client IP (default: False)

    # Callbacks
    on_payment=lambda info: print(f"Paid: {info['amount_sats']} sats"),
)
```

### `toll(**route_options) → FastAPI Dependency`

Create a FastAPI dependency for a route. Use with `Depends()`.

```python
from fastapi import Depends

# Fixed price
@app.get("/api/data")
async def data(payment=Depends(toll(sats=21))):
    return {"data": "..."}

# Dynamic price based on request
@app.get("/api/search")
async def search(payment=Depends(toll(
    price=lambda req: 50 if req.query_params.get("premium") else 10,
    description=lambda req: f"Search: {req.query_params.get('q', '')}"
))):
    return {"results": []}

# Free tier + paid
@app.get("/api/data")
async def data(payment=Depends(toll(
    sats=21,
    free_requests=10,     # Free requests per window per client
    free_window="1h"      # Window: '30m', '1h', '1d', etc.
))):
    return {"data": "..."}
```

#### Route Options

| Option | Type | Description |
|--------|------|-------------|
| `sats` | `int` | Fixed price in satoshis |
| `price` | `(request) → int` | Dynamic pricing function |
| `description` | `str \| (request) → str` | Invoice description |
| `free_requests` | `int` | Free requests per window per client |
| `free_window` | `str \| int` | Free tier window (`'1h'`, `'30m'`, `'1d'`, or milliseconds) |

### Payment Info

The dependency returns a dict with payment info:

```python
@app.get("/api/data")
async def data(payment=Depends(toll(sats=5))):
    if payment["paid"]:
        print(payment["payment_hash"])
        print(payment["amount_sats"])
    if payment.get("free"):
        print("Free tier request")
    return {"data": "..."}
```

### `toll.require(**options)` — Decorator Style

```python
@app.get("/api/data")
@toll.require(sats=5)
async def data(request: Request, payment: dict = None):
    return {"data": "..."}
```

> **Note:** When using the decorator, include `request: Request` as a parameter. Payment info is injected as `payment` if the parameter exists.

### `toll.dashboard_data()` — Stats

```python
@app.get("/api/stats")
async def stats():
    return toll.dashboard_data()
```

Returns:
```json
{
  "totalRevenue": 1250,
  "totalRequests": 340,
  "totalPaid": 125,
  "uniquePayers": 42,
  "endpoints": {
    "/api/joke": { "revenue": 500, "requests": 100, "paid": 100, "free": 0 }
  },
  "recentPayments": [
    {
      "endpoint": "/api/joke",
      "amountSats": 5,
      "payerId": "203.0.113.1",
      "paymentHash": "abc123...",
      "timestamp": 1706817600000
    }
  ]
}
```

## Client SDK

### `TollClient`

A client that automatically handles L402 payment flows:

```python
from lightning_toll.client import TollClient

client = TollClient(
    wallet_url="nostr+walletconnect://...",
    max_sats=100,       # Budget cap per request (default: 100)
    auto_retry=True,    # Auto-pay and retry on 402 (default: True)
    headers={"User-Agent": "MyApp/1.0"}
)

# Transparent fetch — handles 402 automatically
response = await client.fetch("https://api.example.com/joke")
data = response.json()

# Per-request budget override
response = await client.fetch("https://api.example.com/expensive", max_sats=500)

# Check spending
print(client.get_stats())

# Clean up
await client.close()
```

### `toll_fetch(url, **options)`

One-shot fetch with auto-payment — no client setup needed:

```python
from lightning_toll.client import toll_fetch

response = await toll_fetch(
    "https://api.example.com/joke",
    wallet_url="nostr+walletconnect://...",
    max_sats=50
)
data = response.json()
```

#### Options

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `wallet_url` | `str` | required* | NWC connection string |
| `wallet` | `object` | — | Pre-created wallet instance |
| `max_sats` | `int` | `50` | Max sats to auto-pay |
| `method` | `str` | `"GET"` | HTTP method |
| `headers` | `dict` | `{}` | Request headers |
| `body` | `any` | — | Request body |

## NWC Wallet Setup

lightning-toll uses [Nostr Wallet Connect (NWC)](https://nwc.dev) to create invoices and process payments. You need an NWC-compatible Lightning wallet:

### Recommended: Alby Hub

1. Sign up at [getalby.com](https://getalby.com)
2. Go to **Settings → Wallet Connections → Add Connection**
3. Copy the NWC URL (starts with `nostr+walletconnect://`)

### Other NWC Wallets

- **LNbits** with NWC extension
- **Mutiny Wallet**
- Any wallet implementing [NIP-47](https://github.com/nostr-protocol/nips/blob/master/47.md)

### Using the NWC Client Directly

```python
from lightning_toll.nwc import NwcWallet

wallet = NwcWallet("nostr+walletconnect://...")

# Create an invoice
result = await wallet.create_invoice(amount_sats=100, description="Test")
print(result.invoice)       # lnbc...
print(result.payment_hash)  # hex

# Check if paid
lookup = await wallet.lookup_invoice(result.payment_hash)
print(lookup.paid)

# Wait for payment
result = await wallet.wait_for_payment(payment_hash, timeout_ms=60000)

await wallet.close()
```

## Macaroon System

Macaroons are bearer credentials with embedded restrictions (caveats). lightning-toll uses HMAC-SHA256 chained signatures, identical to the Node.js version.

### How Macaroons Work

```
1. Server creates macaroon:
   HMAC(secret, paymentHash) → sig₁
   HMAC(sig₁, "expires_at = 1706900000") → sig₂
   HMAC(sig₂, "endpoint = /api/joke") → final_signature

2. Macaroon = { id: paymentHash, caveats: [...], signature: final_sig }
   Encoded as base64url JSON for transport.

3. Verification: recompute the HMAC chain and compare signatures (timing-safe).
```

### Supported Caveats

| Caveat | Description | Default |
|--------|-------------|---------|
| `expires_at` | Unix timestamp — macaroon expires after this | Always set |
| `endpoint` | Path the macaroon is valid for | Set when `bind_endpoint=True` |
| `method` | HTTP method restriction | Set when `bind_method=True` |
| `ip` | Client IP restriction | Set when `bind_ip=True` |

### Using Macaroons Directly

```python
from lightning_toll import create_macaroon, decode_macaroon, verify_macaroon, verify_preimage

# Create
mac = create_macaroon("secret", payment_hash="abc123...", expires_at=1706900000)
print(mac.raw)  # base64url encoded

# Decode
decoded = decode_macaroon(mac.raw)
print(decoded.id)       # payment hash
print(decoded.caveats)  # list of caveat strings

# Verify
result = verify_macaroon("secret", decoded, {"endpoint": "/api/data"})
print(result.valid)  # True/False
print(result.error)  # Error message if invalid

# Verify preimage
valid = verify_preimage(preimage_hex, payment_hash_hex)
```

## 402 Response Format

When a client hits a toll-gated endpoint without payment:

```
HTTP/1.1 402 Payment Required
WWW-Authenticate: L402 invoice="lnbc50n1pj...", macaroon="eyJpZCI..."
Content-Type: application/json

{
  "status": 402,
  "message": "Payment Required",
  "paymentHash": "a1b2c3d4...",
  "invoice": "lnbc50n1pj...",
  "macaroon": "eyJpZCI...",
  "amountSats": 5,
  "description": "Random joke",
  "protocol": "L402",
  "instructions": {
    "step1": "Pay the Lightning invoice above",
    "step2": "Get the preimage from the payment receipt",
    "step3": "Retry the request with header: Authorization: L402 <macaroon>:<preimage>"
  }
}
```

## Node.js Interoperability

This Python package produces **identical wire format** to the Node.js [lightning-toll](https://github.com/jeletor/lightning-toll):

- Same base64url JSON macaroon encoding
- Same HMAC-SHA256 chained signature algorithm
- Same caveat format (`key = value`)
- Same L402 header format
- Same 402 response body structure

A macaroon created by the Node.js server can be verified by the Python server and vice versa (given the same secret). Clients written for either version work with both servers.

## Security Considerations

- **Use a strong secret.** At least 32 random characters: `python -c "import secrets; print(secrets.token_hex(32))"`
- **HTTPS in production.** Macaroons and preimages are bearer credentials.
- **Invoice expiry.** Default 5 minutes. Shorter = safer.
- **Macaroon expiry.** Default 1 hour. A paid macaroon can be reused within this window.
- **IP binding.** Enable `bind_ip=True` to tie macaroons to client IPs. Beware of NAT/proxies.

## Demo

Run the included demo server:

```bash
pip install -e ".[dev]"

# With mock wallet (for testing the L402 flow)
python examples/fastapi_demo.py

# With real wallet
NWC_URL="nostr+walletconnect://..." python examples/fastapi_demo.py
```

Open `http://localhost:8402` for the welcome page, then try:

```bash
curl http://localhost:8402/api/joke    # → 402 + invoice
curl http://localhost:8402/api/stats   # → revenue dashboard
```

## Why Lightning Instead of API Keys?

| | API Keys / Stripe | lightning-toll |
|---|---|---|
| **Setup** | Hours–days | Minutes (5 lines of code) |
| **User friction** | Sign up, credit card | Scan QR, pay instantly |
| **Minimum payment** | $0.50+ | 1 sat (~$0.0005) |
| **Chargebacks** | Yes | No — Lightning is final |
| **KYC** | Yes | No |
| **Global** | Restricted | Works everywhere, instantly |
| **Privacy** | Full identity | Pseudonymous |
| **Settlement** | Days–weeks | Instant |

## License

MIT — [Jeletor](https://github.com/jeletor)
