"""
CJ Dropshipping API v2 client.

The CJ API does not accept the API key directly: it has to be exchanged for an
access token (valid ~15 days, and CJ rate-limits the exchange), which is cached
on disk and reused.
"""

import json
import logging
import time
from pathlib import Path
from typing import Any, Dict, Optional
import httpx

from config import BASE_DIR, config

logger = logging.getLogger("CJClient")

TOKEN_CACHE = BASE_DIR / ".cj_token.json"
# Refresh a day before CJ's 15-day expiry.
TOKEN_TTL_SECONDS = 14 * 24 * 3600


class CJAPIError(Exception):
    """Raised when CJ answers with an HTTP error or with result=false."""

    def __init__(self, status_code: int, message: str, response_text: str = ""):
        self.status_code = status_code
        self.message = message
        self.response_text = response_text
        super().__init__(f"[CJ {status_code}] {message}")


class CJClient:
    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        token_cache: Optional[Path] = None,
    ):
        self.api_key = api_key if api_key is not None else config.SUPPLIER_API_KEY
        self.base_url = (base_url or config.SUPPLIER_API_BASE_URL).rstrip("/")
        self.token_cache = token_cache or TOKEN_CACHE
        self._token: Optional[str] = None

    def _read_cached_token(self) -> Optional[str]:
        try:
            cached = json.loads(self.token_cache.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if cached.get("api_key_tail") != self.api_key[-6:] or cached.get("expires_at", 0) <= time.time():
            return None
        return cached.get("access_token")

    async def get_access_token(self, client: httpx.AsyncClient) -> str:
        if self._token:
            return self._token
        cached = self._read_cached_token()
        if cached:
            self._token = cached
            return cached
        if not self.api_key:
            raise CJAPIError(401, "SUPPLIER_API_KEY is not configured")

        data = await self._request(client, "POST", "/authentication/getAccessToken", json={"apiKey": self.api_key}, auth=False)
        token = data["accessToken"]
        try:
            self.token_cache.write_text(
                json.dumps({"access_token": token, "api_key_tail": self.api_key[-6:], "expires_at": time.time() + TOKEN_TTL_SECONDS}),
                encoding="utf-8",
            )
        except OSError as e:
            logger.warning(f"Could not cache CJ access token: {e}")
        self._token = token
        return token

    async def _request(self, client: httpx.AsyncClient, method: str, path: str, auth: bool = True, **kwargs) -> Any:
        headers = {"Content-Type": "application/json"}
        if auth:
            headers["CJ-Access-Token"] = await self.get_access_token(client)
        response = await client.request(method, f"{self.base_url}{path}", headers=headers, **kwargs)
        if response.status_code >= 400:
            raise CJAPIError(response.status_code, f"HTTP {response.status_code}", response.text)
        body = response.json()
        if not body.get("result"):
            raise CJAPIError(int(body.get("code") or 0), body.get("message") or "CJ request failed", response.text)
        return body.get("data")

    async def create_order(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        async with httpx.AsyncClient(timeout=30.0) as client:
            return await self._request(client, "POST", "/shopping/order/createOrderV2", json=payload)

    async def get_stock_and_cost(self, sku: str) -> Dict[str, Any]:
        """Returns the CJ variant cost and total stock across warehouses for a CJ variant SKU."""
        async with httpx.AsyncClient(timeout=15.0) as client:
            product = await self._request(client, "GET", "/product/query", params={"variantSku": sku})
            variant = next((v for v in product.get("variants", []) if v.get("variantSku") == sku), None)
            if variant is None:
                raise CJAPIError(404, f"CJ variant {sku} not found")
            warehouses = await self._request(client, "GET", "/product/stock/queryBySku", params={"sku": sku}) or []
            return {
                "sku": sku,
                "wholesale_cost": float(variant["variantSellPrice"]),
                "available_stock": sum(int(w.get("totalInventoryNum") or 0) for w in warehouses),
            }
