"""
In-memory payment stats tracker.

Tracks revenue, request counts, unique payers, and recent payments.
Direct port of the Node.js lightning-toll TollStats class.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set


@dataclass
class PaymentRecord:
    """A single payment record."""
    endpoint: str
    amount_sats: int
    payer_id: str
    payment_hash: Optional[str]
    timestamp: float  # milliseconds since epoch (matches Node.js)


class TollStats:
    """In-memory payment statistics tracker."""

    def __init__(self, max_recent: int = 100):
        self.max_recent = max_recent

        # Totals
        self.total_revenue: int = 0
        self.total_requests: int = 0
        self.total_paid: int = 0

        # Per-endpoint: path → { revenue, requests, paid, free }
        self._endpoints: Dict[str, Dict[str, int]] = {}

        # Unique payers (by IP or pubkey)
        self._payers: Set[str] = set()

        # Recent payments (ring buffer)
        self._recent_payments: List[PaymentRecord] = []

    def record(
        self,
        endpoint: str,
        paid: bool,
        amount_sats: int = 0,
        payer_id: Optional[str] = None,
        payment_hash: Optional[str] = None,
    ) -> None:
        """
        Record a request (whether paid or free).

        Args:
            endpoint: Request path.
            paid: Whether this was a paid request.
            amount_sats: Amount paid in sats.
            payer_id: Payer identifier (IP or pubkey).
            payment_hash: Lightning payment hash.
        """
        self.total_requests += 1

        # Per-endpoint stats
        if endpoint not in self._endpoints:
            self._endpoints[endpoint] = {"revenue": 0, "requests": 0, "paid": 0, "free": 0}
        ep = self._endpoints[endpoint]
        ep["requests"] += 1

        if paid and amount_sats > 0:
            self.total_revenue += amount_sats
            self.total_paid += 1
            ep["revenue"] += amount_sats
            ep["paid"] += 1

            if payer_id:
                self._payers.add(payer_id)

            # Add to recent payments
            self._recent_payments.append(
                PaymentRecord(
                    endpoint=endpoint,
                    amount_sats=amount_sats,
                    payer_id=payer_id or "unknown",
                    payment_hash=payment_hash,
                    timestamp=time.time() * 1000,  # ms since epoch
                )
            )

            # Trim recent payments
            if len(self._recent_payments) > self.max_recent:
                self._recent_payments = self._recent_payments[-self.max_recent:]
        else:
            ep["free"] += 1

    def to_dict(self) -> Dict[str, Any]:
        """
        Get stats summary as a plain dict.

        Returns:
            Dict with stats matching the Node.js format.
        """
        endpoint_stats = {}
        for path, data in self._endpoints.items():
            endpoint_stats[path] = dict(data)

        recent = [
            {
                "endpoint": r.endpoint,
                "amountSats": r.amount_sats,
                "payerId": r.payer_id,
                "paymentHash": r.payment_hash,
                "timestamp": r.timestamp,
            }
            for r in self._recent_payments[-20:]
        ]
        recent.reverse()

        return {
            "totalRevenue": self.total_revenue,
            "totalRequests": self.total_requests,
            "totalPaid": self.total_paid,
            "uniquePayers": len(self._payers),
            "endpoints": endpoint_stats,
            "recentPayments": recent,
        }

    # Alias for compatibility
    def to_json(self) -> Dict[str, Any]:
        """Alias for to_dict() — matches Node.js toJSON()."""
        return self.to_dict()
