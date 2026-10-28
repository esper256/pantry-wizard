"""Live Costco client built on costco-mcp-server.

The dependency owns Azure AD B2C refresh tokens and the warehouse receipt
queries. Current prices come from the product summary API the costco.com
product page calls. ``CatalogDataType`` does not expose ``price`` or
``listPrice``, and ``priceData`` on the products GraphQL query is a catalog
price that does not follow the warehouse or an instant-savings promotion.

Shelf stock and the in-warehouse temporary price discount come from the
warehouse-mode service the Costco app uses for its In-Warehouse view.
That call needs no member token. The costco.com inventory batch call
answers 429 to this client, so it is not used.
"""

from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request

from costco_sync.models import AuthError, PriceLookupResult, PriceQuote, RangeRejected, SearchHit
from costco_sync.normalize import observe_warehouse_item, parse_summary_prices

# From the product page's productDetailApiV2Config. These are public client
# ids shipped in costco.com's HTML, not member secrets.
_SUMMARY_URL = "https://gdx-api.costco.com/catalog/product/product-api/v2/products/summary"
_SUMMARY_CLIENT_IDENTIFIER = "b1be4e95-8696-4d93-8f50-5b5632922209"
_SUMMARY_BATCH = 20
PRICE_BATCH_PAUSE_SECONDS = 20
WAREHOUSE_LOOKUP_PAUSE_SECONDS = 1
_WAREHOUSE_URL = (
    "https://gdx-api-mobileapps.costco.com/consumer-mobile/core/warehouse-mode/v1/mobile/"
    "whmodebffservice/items"
)
_WAREHOUSE_STOP = {403, 429}


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
            fetched = self._price_summaries(item_numbers, warehouse_number)
        except AuthError:
            raise
        except Exception as exc:
            if _is_auth_failure(exc):
                raise AuthError("Costco authentication failed") from exc
            print(_summary_failure_text(exc, len(item_numbers)), file=sys.stderr)
            return PriceLookupResult(ok=False, quotes=[], checked=[])
        note = ", ".join(fetched.statuses)
        if not fetched.checked:
            return PriceLookupResult(ok=False, quotes=[], checked=[], note=note)
        quotes = parse_summary_prices(fetched.products, warehouse_number)
        self._attach_warehouse(quotes, fetched.checked, warehouse_number)
        return PriceLookupResult(
            ok=True,
            quotes=quotes,
            checked=fetched.checked,
            note=note,
        )

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

    def _attach_warehouse(
        self,
        quotes: list[PriceQuote],
        checked: list[str],
        warehouse_number: str,
        *,
        pause_seconds: float = WAREHOUSE_LOOKUP_PAUSE_SECONDS,
        sleep=None,
        post=None,
    ) -> None:
        """Ask the warehouse-mode service once per priced item, then stop if it refuses."""
        if sleep is None:
            sleep = time.sleep
        if post is None:
            post = _post_warehouse_search
        by_number = {quote.item_number: quote for quote in quotes}
        wanted = [sku for sku in checked if sku in by_number]
        for index, sku in enumerate(wanted):
            if index and pause_seconds:
                sleep(pause_seconds)
            item, failure = post(sku, warehouse_number)
            if failure in _WAREHOUSE_STOP:
                print(
                    f"Costco warehouse lookup stopped after HTTP {failure}. "
                    f"{len(wanted) - index} items stay unknown.",
                    file=sys.stderr,
                )
                return
            if failure is not None:
                print("Costco warehouse lookup failed for 1 item.", file=sys.stderr)
                continue
            observe_warehouse_item(by_number[sku], item)

    def _price_summaries(
        self,
        item_numbers: list[str],
        warehouse_number: str,
        *,
        pause_seconds: float = PRICE_BATCH_PAUSE_SECONDS,
        sleep=None,
    ):
        if sleep is None:
            sleep = time.sleep
        from costco_mcp_server.auth import WCS_CLIENT_ID
        from curl_cffi import requests as curl_requests

        impersonate = getattr(
            __import__("costco_mcp_server.api", fromlist=["_IMPERSONATE"]),
            "_IMPERSONATE",
            "chrome131",
        )
        found: list[dict] = []
        checked: list[str] = []
        statuses: list[str] = []
        offsets = range(0, len(item_numbers), _SUMMARY_BATCH)
        for index, offset in enumerate(offsets):
            if index and pause_seconds:
                sleep(pause_seconds)
            batch = item_numbers[offset : offset + _SUMMARY_BATCH]
            products, failure = _summary_batch(
                curl_requests.get, batch, warehouse_number, impersonate, WCS_CLIENT_ID
            )
            if failure is not None:
                if pause_seconds:
                    sleep(pause_seconds)
                products, failure = _summary_batch(
                    curl_requests.get, batch, warehouse_number, impersonate, WCS_CLIENT_ID
                )
            if failure is not None:
                print(_summary_failure_text(failure, len(batch)), file=sys.stderr)
                status = _failure_status(failure)
                if status and status not in statuses:
                    statuses.append(status)
                continue
            found.extend(products)
            checked.extend(batch)
        return _SummaryFetch(found, checked, statuses)


class _SummaryFetch:
    def __init__(self, products: list[dict], checked: list[str], statuses: list[str]) -> None:
        self.products = products
        self.checked = checked
        self.statuses = statuses


def _post_warehouse_search(item_number: str, warehouse_number: str):
    """One warehouse-mode search. No member token and no anti-bot bypass.

    Returns ``(item, None)`` for an exact item number, ``(None, None)`` when
    the service answered without that number, or ``(None, status)`` on failure.
    """
    body = {
        "type": "com.costco.app.core.model.network.BffIOHRequest",
        "currentWarehouse": str(warehouse_number),
        "selectedWarehouse": str(warehouse_number),
        "searchTerm": str(item_number),
        "maxWhCount": "10",
        "sortField": "lastWeek",
        "sortDirection": "desc",
        "locale": "en_US",
        "relevance": 80,
        "validMember": True,
    }
    request = urllib.request.Request(
        _WAREHOUSE_URL,
        data=json.dumps(body).encode(),
        method="POST",
        headers={
            "User-Agent": "device/Android",
            "Content-Type": "application/json; charset=utf-8",
            "Accept": "application/json",
            "Cache-Control": "no-cache",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            payload = json.loads(response.read().decode())
    except urllib.error.HTTPError as exc:
        return None, exc.code
    except Exception:
        return None, "error"
    if not isinstance(payload, dict):
        return None, "error"
    for item in payload.get("items") or []:
        if isinstance(item, dict) and str(item.get("itemNumber") or "") == str(item_number):
            return item, None
    return None, None


def _summary_batch(get, batch: list[str], warehouse_number: str, impersonate: str, client_id: str):
    try:
        response = get(
            _SUMMARY_URL,
            params={
                "clientId": client_id,
                "items": ",".join(batch),
                "whsNumber": warehouse_number,
                "locales": "en-us",
            },
            headers={
                "Accept": "application/json",
                "client-identifier": _SUMMARY_CLIENT_IDENTIFIER,
                "costco-env": "prd",
                "Origin": "https://www.costco.com",
                "Referer": "https://www.costco.com/",
            },
            impersonate=impersonate,
            timeout=30,
        )
    except AuthError:
        raise
    except Exception as exc:
        if _is_auth_failure(exc):
            raise AuthError("Costco authentication failed") from exc
        return [], exc
    if response.status_code != 200:
        failure = RuntimeError(f"Costco price summary returned HTTP {response.status_code}")
        failure.status_code = response.status_code  # type: ignore[attr-defined]
        if _is_auth_failure(failure):
            raise AuthError("Costco authentication failed") from failure
        return [], failure
    try:
        body = response.json()
    except Exception as exc:
        return [], exc
    if not isinstance(body, dict) or "productData" not in body:
        return [], RuntimeError("Costco price summary did not return productData")
    products = body.get("productData") or []
    if not isinstance(products, list):
        return [], RuntimeError("Costco price summary did not return productData")
    return products, None


def _failure_status(exc: BaseException) -> str:
    code = _status_code(exc)
    if code is None:
        return ""
    return f"HTTP {code}"


def _summary_failure_text(exc: BaseException, count: int) -> str:
    text = str(exc).strip()
    if text.startswith("Costco price summary"):
        return f"{text} for {count} items."
    return f"Costco price summary request failed for {count} items."


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
