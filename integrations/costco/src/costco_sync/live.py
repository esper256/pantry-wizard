"""Live Costco client built on costco-mcp-server.

The dependency owns Azure AD B2C refresh tokens and the warehouse receipt
queries. Price lookup is a spike on the same warehouse-scoped products query:
if Costco rejects the extra fields, names still resolve and current prices
are left unchanged.
"""

from __future__ import annotations

from costco_sync.models import AuthError, PriceLookupResult, RangeRejected, SearchHit
from costco_sync.normalize import parse_catalog_prices

_PRICE_QUERY = """
query products($clientId: String!, $itemNumbers: [String], $locale: [String], $warehouseNumber: String!) {
  products(clientId: $clientId, itemNumbers: $itemNumbers, locale: $locale, warehouseNumber: $warehouseNumber) {
    catalogData {
      itemNumber
      description { shortDescription }
      price
      listPrice
      offerPrice
      warehousePrice
      regularPrice
      priceValidThrough
    }
  }
}
"""


class CostcoSource:
    def __init__(self, account: str | None = None, *, policy: str | None = None) -> None:
        from costco_mcp_server.api import CostcoAPI
        from costco_mcp_server.auth import CostcoAuth

        from costco_sync.b2c import install_policy

        install_policy(policy)
        self._auth = CostcoAuth(account)
        self._api = CostcoAPI(self._auth)

    @property
    def account(self) -> str:
        return self._auth.account

    def status(self) -> dict:
        return self._auth.check_status()

    def save_refresh_token(self, refresh_token: str) -> None:
        self._auth.save_refresh_token(refresh_token)
        from costco_sync.privacy import tighten_private_files

        tighten_private_files()

    def list_warehouse_receipts(self, start_date: str, end_date: str) -> dict:
        self._require_auth()
        return self._call(self._api.list_receipts, start_date, end_date, "warehouse", "all")

    def get_receipt_detail(self, barcode: str) -> dict:
        self._require_auth()
        return self._call(self._api.get_receipt_detail, barcode)

    def lookup_product_names(self, item_numbers: list[str], warehouse_number: str) -> dict[str, str]:
        self._require_auth()
        return self._call(self._api.lookup_products, item_numbers, warehouse_number)

    def lookup_prices(self, item_numbers: list[str], warehouse_number: str) -> PriceLookupResult:
        self._require_auth()
        if not item_numbers:
            return PriceLookupResult(ok=True, quotes=[])
        try:
            catalog = self._price_catalog(item_numbers, warehouse_number)
        except AuthError:
            raise
        except Exception as exc:
            if _is_auth_failure(exc):
                raise AuthError("Costco authentication failed") from exc
            return PriceLookupResult(ok=False, quotes=[])
        return PriceLookupResult(ok=True, quotes=parse_catalog_prices(catalog))

    def search_products(self, query: str, warehouse_number: str, limit: int = 5) -> list[SearchHit]:
        """Name search is intentionally empty until a stable item-number match exists.

        A wrong SKU is worse than leaving a shopping-list row unmatched.
        """
        del query, warehouse_number, limit
        return []

    def _require_auth(self) -> None:
        if not self._auth.is_authenticated:
            raise AuthError(
                f"Costco account '{self._auth.account}' has no refresh token. "
                "Run `costco-sync auth` and copy the refreshtoken secret from Chrome DevTools."
            )
        try:
            self._auth.get_bearer_token()
        except AuthError:
            raise
        except Exception as exc:
            raise AuthError("Costco authentication failed") from exc

    def _call(self, method, *args):
        try:
            return method(*args)
        except (AuthError, RangeRejected):
            raise
        except Exception as exc:
            if _is_auth_failure(exc):
                raise AuthError("Costco authentication failed") from exc
            if _is_range_rejected(exc):
                raise RangeRejected("Costco rejected this receipt date range") from exc
            raise

    def _price_catalog(self, item_numbers: list[str], warehouse_number: str) -> list[dict]:
        from costco_mcp_server.api import PRODUCT_GRAPHQL_ENDPOINT
        from costco_mcp_server.auth import WCS_CLIENT_ID
        from curl_cffi import requests as curl_requests

        impersonate = getattr(
            __import__("costco_mcp_server.api", fromlist=["_IMPERSONATE"]),
            "_IMPERSONATE",
            "chrome131",
        )
        found: list[dict] = []
        for offset in range(0, len(item_numbers), 20):
            batch = item_numbers[offset : offset + 20]
            variables = {
                "itemNumbers": batch,
                "clientId": WCS_CLIENT_ID,
                "locale": ["en-US"],
                "warehouseNumber": warehouse_number,
            }
            response = curl_requests.post(
                PRODUCT_GRAPHQL_ENDPOINT,
                json={"query": _PRICE_QUERY, "variables": variables},
                headers=self._api._headers(),
                impersonate=impersonate,
                timeout=30,
            )
            if response.status_code == 401:
                raise AuthError("Costco authentication failed")
            response.raise_for_status()
            body = response.json()
            if body.get("errors"):
                raise RuntimeError("Costco product query rejected the price selection set")
            found.extend(body.get("data", {}).get("products", {}).get("catalogData") or [])
        return found


def _is_auth_failure(exc: BaseException) -> bool:
    text = str(exc).casefold()
    response = getattr(exc, "response", None)
    body = str(getattr(response, "text", "") or "").casefold()
    url = str(getattr(response, "url", "") or "").casefold()
    blob = text + "\n" + body
    if "/oauth2/v2.0/token" in url or "invalid_grant" in blob or "aadb2c" in blob:
        return True
    if "not authenticated" in text or "authentication failed" in text:
        return True
    return _status_code(exc) == 401


def _is_range_rejected(exc: BaseException) -> bool:
    if _is_auth_failure(exc):
        return False
    response = getattr(exc, "response", None)
    url = str(getattr(response, "url", "") or "").casefold()
    if "signin.costco.com" in url or "/oauth2/" in url:
        return False
    return _status_code(exc) in {400, 422}


def _status_code(exc: BaseException) -> int | None:
    direct = getattr(exc, "status_code", None)
    if isinstance(direct, int):
        return direct
    response = getattr(exc, "response", None)
    code = getattr(response, "status_code", None)
    return code if isinstance(code, int) else None
