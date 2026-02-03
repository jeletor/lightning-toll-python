"""
Main toll gate factory.

create_toll() creates a toll gate instance that can be used as a FastAPI
dependency or decorator to put API endpoints behind Lightning paywalls.

This is the Python equivalent of createToll() in the Node.js lightning-toll.
"""

from __future__ import annotations

import functools
from typing import Any, Callable, Dict, Optional, Union

from .middleware import TollMiddleware
from .nwc import NwcWallet
from .stats import TollStats


class Toll:
    """
    Toll gate instance.

    Created by create_toll(). Used as a FastAPI dependency factory or decorator.

    Usage as dependency:
        toll = create_toll(wallet_url="...", secret="...")
        @app.get("/api/data")
        async def data(payment=Depends(toll(sats=5))):
            return {"data": "..."}

    Usage as decorator:
        @app.get("/api/data")
        @toll.require(sats=5)
        async def data():
            return {"data": "..."}
    """

    def __init__(self, config: Dict[str, Any]):
        self._config = config
        self.stats: TollStats = config["stats"]
        self.wallet = config["wallet"]

    def __call__(self, **route_opts: Any) -> TollMiddleware:
        """
        Create a FastAPI dependency for a route.

        Args:
            sats: Fixed price in satoshis.
            price: Dynamic pricing callable (request) -> sats.
            description: Invoice description (str or callable).
            free_requests: Number of free requests per window per client.
            free_window: Time window for free tier ('1h', '30m', etc.).

        Returns:
            TollMiddleware instance usable with Depends().
        """
        return TollMiddleware(self._config, route_opts)

    def require(self, **route_opts: Any) -> Callable:
        """
        Decorator that requires payment before executing the handler.

        Usage:
            @app.get("/api/data")
            @toll.require(sats=5)
            async def data():
                return {"data": "..."}

        Note: When using as a decorator, the payment info is injected
        as a 'payment' keyword argument if the handler accepts it.
        """

        def decorator(func: Callable) -> Callable:
            middleware = TollMiddleware(self._config, route_opts)

            @functools.wraps(func)
            async def wrapper(*args: Any, **kwargs: Any) -> Any:
                # Extract request from kwargs (FastAPI injects it)
                from fastapi import Request

                request = kwargs.get("request")
                if request is None:
                    # Try to find it in args (shouldn't happen with FastAPI)
                    for arg in args:
                        if isinstance(arg, Request):
                            request = arg
                            break

                if request is None:
                    raise RuntimeError(
                        "toll.require() decorator needs a 'request: Request' parameter "
                        "in the route handler"
                    )

                # Run the toll middleware
                payment = await middleware(request)

                # Inject payment info if handler accepts it
                import inspect

                sig = inspect.signature(func)
                if "payment" in sig.parameters:
                    kwargs["payment"] = payment

                return await func(*args, **kwargs)

            return wrapper

        return decorator

    def dashboard(self) -> Callable:
        """
        Create a FastAPI route handler for the stats dashboard.

        Usage:
            @app.get("/api/stats")
            async def stats():
                return toll.dashboard_data()

        Or mount directly:
            app.add_api_route("/api/stats", toll.dashboard())
        """

        async def dashboard_handler() -> Dict[str, Any]:
            return self.stats.to_dict()

        return dashboard_handler

    def dashboard_data(self) -> Dict[str, Any]:
        """Get the current stats as a dict (for direct use in handlers)."""
        return self.stats.to_dict()


def create_toll(
    wallet_url: Optional[str] = None,
    wallet: Optional[Any] = None,
    secret: str = "",
    default_sats: int = 10,
    invoice_expiry: int = 300,
    macaroon_expiry: int = 3600,
    bind_endpoint: bool = True,
    bind_method: bool = True,
    bind_ip: bool = False,
    on_payment: Optional[Callable] = None,
) -> Toll:
    """
    Create a toll booth instance for gating API endpoints behind Lightning payments.

    Args:
        wallet_url: NWC connection string (nostr+walletconnect://...).
        wallet: Pre-created wallet instance (must have create_invoice method).
        secret: HMAC secret for signing macaroons (required).
        default_sats: Default price in sats if not specified per-route.
        invoice_expiry: Invoice expiry in seconds (default 300 = 5 min).
        macaroon_expiry: Macaroon validity after payment (default 3600 = 1 hour).
        bind_endpoint: Bind macaroons to specific endpoints (default True).
        bind_method: Bind macaroons to specific HTTP methods (default True).
        bind_ip: Bind macaroons to client IP (default False).
        on_payment: Callback when a payment is received.

    Returns:
        Toll instance.
    """
    if not secret:
        raise ValueError("lightning-toll: secret is required for macaroon signing")

    # Create or use wallet
    wallet_instance: Any
    if wallet is not None:
        if not hasattr(wallet, "create_invoice"):
            raise ValueError(
                "lightning-toll: wallet must have a create_invoice() method"
            )
        wallet_instance = wallet
    elif wallet_url:
        wallet_instance = NwcWallet(wallet_url)
    else:
        raise ValueError(
            "lightning-toll: wallet_url or wallet is required"
        )

    stats = TollStats()

    config = {
        "wallet": wallet_instance,
        "secret": secret,
        "stats": stats,
        "default_sats": default_sats,
        "invoice_expiry": invoice_expiry,
        "macaroon_expiry": macaroon_expiry,
        "bind_endpoint": bind_endpoint,
        "bind_method": bind_method,
        "bind_ip": bind_ip,
        "on_payment": on_payment,
    }

    return Toll(config)
