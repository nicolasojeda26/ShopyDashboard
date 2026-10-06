"""
Fulfillment Agent: Automates dropshipping order fulfillment by processing Shopify orders/create
webhook payloads, dispatching REST purchase orders to the supplier API (e.g. CJ Dropshipping, DSers),
and syncing tracking numbers back to Shopify via GraphQL mutations.
"""

import logging
import os
import sys
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
import httpx
from pydantic import BaseModel, Field

from config import config
from tools.cj_client import CJAPIError, CJClient
from tools.shopify_client import ShopifyClient

logger = logging.getLogger("FulfillmentAgent")


class ShippingAddress(BaseModel):
    name: str = Field(default="Valued Customer")
    first_name: Optional[str] = Field(default="")
    last_name: Optional[str] = Field(default="")
    address1: str = Field(default="")
    address2: Optional[str] = Field(default=None)
    city: str = Field(default="")
    province: Optional[str] = Field(default="")
    province_code: Optional[str] = Field(default="")
    zip: str = Field(default="")
    country: str = Field(default="")
    country_code: str = Field(default="")
    phone: Optional[str] = Field(default=None)


class OrderLineItem(BaseModel):
    sku: str = Field(default="")
    title: str = Field(default="Dropship Item")
    quantity: int = Field(default=1)
    price: float = Field(default=0.0)
    variant_id: Optional[int] = None
    line_item_id: Optional[int] = None
    supplier_item_id: Optional[str] = None


class SupplierFulfillmentResult(BaseModel):
    # SUCCESS_FULFILLED (tracking synced to Shopify), SUPPLIER_ORDER_CREATED (CJ order created,
    # tracking arrives later), SKIPPED_AUTO_ORDER_DISABLED, or FAILED_*
    status: str = Field(default="SUCCESS_FULFILLED")
    shopify_order_id: str
    shopify_order_number: str
    supplier_po_number: str
    supplier_api_endpoint: str
    customer_name: str
    shipping_destination: str
    items_fulfilled: List[Dict[str, Any]]
    estimated_delivery_days: int = 7
    tracking_code: Optional[str] = None
    mock_tracking_code: Optional[str] = None
    tracking_company: str = "CJ_DROPSHIPPING"
    shopify_fulfillment_synced: bool = False
    shopify_fulfillment_id: Optional[str] = None
    fulfilled_at: str
    error_message: Optional[str] = None


class SupplierAPIError(Exception):
    """Custom exception for errors returned by the Dropshipping Supplier REST API."""
    def __init__(self, status_code: int, error_message: str, response_text: str = ""):
        self.status_code = status_code
        self.error_message = error_message
        self.response_text = response_text
        super().__init__(f"[HTTP {status_code}] {error_message}")


class FulfillmentAgent:
    """
    Automates Dropshipping Auto-Fulfillment from Shopify webhooks to the CJ Dropshipping API.
    """

    def __init__(
        self,
        cj_client: Optional[CJClient] = None,
        shopify_client: Optional[ShopifyClient] = None,
        dry_run: Optional[bool] = None,
        auto_order: Optional[bool] = None,
    ):
        self.cj_client = cj_client or CJClient()
        self.supplier_api_url = f"{self.cj_client.base_url}/shopping/order/createOrderV2"
        self.supplier_name = config.SUPPLIER_NAME or "CJ_DROPSHIPPING"
        self.shopify_client = shopify_client or ShopifyClient(dry_run=dry_run)
        self.dry_run = dry_run if dry_run is not None else config.DRY_RUN
        self.auto_order = auto_order if auto_order is not None else config.SUPPLIER_AUTO_ORDER

    def _log_error_to_file(self, order_id: str, error_type: str, details: str, status_code: Optional[int] = None):
        """
        Appends structured incident details to error_log.txt for resilience and monitoring.
        """
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        code_str = f" [HTTP_CODE: {status_code}]" if status_code else ""
        error_line = f"[{timestamp}] [ORDER_ID: {order_id}]{code_str} [{error_type}] Details: {details}\n"
        logger.error(error_line.strip())
        try:
            with open("error_log.txt", "a", encoding="utf-8") as err_f:
                err_f.write(error_line)
        except Exception as file_err:
            logger.warning(f"Could not append to error_log.txt: {file_err}")

    def extract_order_details(self, webhook_payload: Dict[str, Any]) -> Dict[str, Any]:
        """
        Parses Shopify orders/create JSON structure and extracts essential customer,
        shipping address & line items. Missing data is left empty, never invented.
        """
        order_id = str(webhook_payload.get("id", ""))
        order_name = str(webhook_payload.get("name") or f"#{webhook_payload.get('order_number', '')}")
        email = webhook_payload.get("email") or webhook_payload.get("contact_email")

        # 1. Customer & Shipping
        shipping = webhook_payload.get("shipping_address") or {}
        customer = webhook_payload.get("customer") or {}

        first_name = shipping.get("first_name") or customer.get("first_name") or ""
        last_name = shipping.get("last_name") or customer.get("last_name") or ""
        customer_name = shipping.get("name") or f"{first_name} {last_name}".strip()

        shipping_address = ShippingAddress(
            name=customer_name,
            first_name=first_name,
            last_name=last_name,
            address1=shipping.get("address1") or "",
            address2=shipping.get("address2"),
            city=shipping.get("city") or "",
            province=shipping.get("province") or "",
            province_code=shipping.get("province_code") or "",
            zip=shipping.get("zip") or "",
            country=shipping.get("country") or "",
            country_code=shipping.get("country_code") or "",
            phone=shipping.get("phone") or customer.get("phone"),
        )

        # 2. Line items & SKUs (the Shopify SKU is the CJ variant SKU)
        line_items: List[OrderLineItem] = []
        for item in webhook_payload.get("line_items", []):
            if item.get("requires_shipping") is False:
                continue
            line_items.append(
                OrderLineItem(
                    sku=item.get("sku") or "",
                    title=item.get("title", "Product"),
                    quantity=int(item.get("quantity", 1)),
                    price=float(item.get("price", 0.0)),
                    variant_id=item.get("variant_id"),
                    line_item_id=item.get("id"),
                )
            )

        return {
            "order_id": order_id,
            "order_name": order_name,
            "email": email,
            "customer_name": customer_name,
            "shipping_address": shipping_address,
            "line_items": line_items,
            "total_price": float(webhook_payload.get("total_price", 0.0) or sum(i.price * i.quantity for i in line_items)),
            "currency": webhook_payload.get("currency", "USD"),
        }

    def validate_order(self, order_info: Dict[str, Any]) -> List[str]:
        """Returns the reasons the order cannot be sent to CJ (empty list if it can)."""
        shipping: ShippingAddress = order_info["shipping_address"]
        problems = [
            f"missing shipping {field}"
            for field, value in [
                ("name", shipping.name),
                ("address1", shipping.address1),
                ("city", shipping.city),
                ("country_code", shipping.country_code),
            ]
            if not value
        ]
        if not order_info["line_items"]:
            problems.append("no shippable line items")
        problems += [f"line item '{i.title}' has no SKU" for i in order_info["line_items"] if not i.sku]
        return problems

    def map_to_supplier_payload(self, order_info: Dict[str, Any]) -> Dict[str, Any]:
        """
        Maps extracted Shopify order data to the CJ createOrderV2 schema.
        """
        shipping: ShippingAddress = order_info["shipping_address"]
        items: List[OrderLineItem] = order_info["line_items"]
        order_name = order_info["order_name"].replace("#", "")

        # Deterministic, so a retried webhook is rejected by CJ as a duplicate instead of shipping twice.
        supplier_order_number = f"SHOPIFY-{order_name}-{order_info['order_id']}"

        return {
            "orderNumber": supplier_order_number,
            "shippingCustomerName": shipping.name,
            "shippingAddress": shipping.address1,
            "shippingAddress2": shipping.address2 or "",
            "shippingCity": shipping.city,
            "shippingProvince": shipping.province or shipping.province_code,
            "shippingZip": shipping.zip,
            "shippingCountry": shipping.country,
            "shippingCountryCode": shipping.country_code,
            "shippingPhone": shipping.phone or "",
            "email": order_info.get("email") or "",
            "logisticName": config.SUPPLIER_LOGISTIC_NAME,
            "fromCountryCode": config.SUPPLIER_FROM_COUNTRY,
            "payType": config.SUPPLIER_PAY_TYPE,
            "remark": f"Shopify order {order_info['order_name']} ({self.shopify_client.shop_domain})",
            "products": [
                {
                    "sku": item.sku,
                    "quantity": item.quantity,
                    "storeLineItemId": str(item.line_item_id or ""),
                }
                for item in items
            ],
        }

    async def dispatch_to_supplier_api(self, supplier_payload: Dict[str, Any], order_id: str) -> Dict[str, Any]:
        """
        Creates the order in CJ. Handles API errors, out of stock, invalid tokens, and insufficient funds.
        Returns CJ's `data` object (orderId, orderStatus, ...).
        """
        if self.dry_run:
            logger.info(f"[Simulation/Dry-Run] Dispatching mock purchase order {supplier_payload['orderNumber']} to {self.supplier_api_url}")
            return {
                "orderNumber": supplier_payload["orderNumber"],
                "orderId": f"SIM-{uuid.uuid4().hex[:12].upper()}",
                "trackingNumber": f"CJ{uuid.uuid4().hex[:10].upper()}YQ",
                "logisticName": self.supplier_name,
                "orderStatus": "CREATED",
            }

        if not self.cj_client.api_key:
            message = "SUPPLIER_API_KEY is required for live fulfillment; refusing to simulate a supplier order."
            self._log_error_to_file(order_id, "SUPPLIER_CREDENTIALS_MISSING", message)
            raise SupplierAPIError(503, message)

        error_types = {
            400: "OUT_OF_STOCK_OR_BAD_REQUEST",
            401: "UNAUTHORIZED_TOKEN_INVALID",
            402: "INSUFFICIENT_FUNDS",
            429: "RATE_LIMIT_EXCEEDED",
        }
        try:
            logger.info(f"POST {self.supplier_api_url} | PO: {supplier_payload['orderNumber']}")
            data = await self.cj_client.create_order(supplier_payload)
            logger.info(f"✔ CJ order created: {data}")
            return data or {}
        except CJAPIError as e:
            error_type = error_types.get(e.status_code, "SUPPLIER_SERVER_ERROR" if e.status_code >= 500 else "SUPPLIER_REJECTED_ORDER")
            self._log_error_to_file(order_id, error_type, f"{e.message}: {e.response_text}", e.status_code)
            raise SupplierAPIError(e.status_code, e.message, e.response_text)
        except httpx.RequestError as req_err:
            err_msg = f"Network communication failure connecting to supplier API: {req_err}"
            self._log_error_to_file(order_id, "NETWORK_REQUEST_ERROR", err_msg)
            raise SupplierAPIError(503, err_msg, str(req_err))

    async def sync_tracking_to_shopify(
        self,
        order_id: str,
        tracking_number: str,
        tracking_company: str = "CJ_DROPSHIPPING",
        tracking_url: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Executes Shopify GraphQL mutation fulfillmentCreateV2 to attach supplier tracking code
        to the original customer order.
        """
        try:
            logger.info(f"🔗 Injecting tracking {tracking_number} ({tracking_company}) into Shopify Order {order_id}")
            res = await self.shopify_client.create_order_fulfillment(
                order_id=order_id,
                tracking_number=tracking_number,
                tracking_company=tracking_company,
                tracking_url=tracking_url,
            )
            return res
        except Exception as e:
            logger.error(f"Failed to sync tracking to Shopify for order {order_id}: {e}")
            self._log_error_to_file(order_id, "SHOPIFY_TRACKING_SYNC_ERROR", str(e))
            return {"error": str(e)}

    async def process_order_webhook(self, webhook_payload: Dict[str, Any]) -> SupplierFulfillmentResult:
        """
        Receives webhook, transforms details, creates the CJ order, logs errors if any, and
        synchronizes tracking back to Shopify when CJ already returned one.
        """
        order_info = self.extract_order_details(webhook_payload)
        order_id = order_info["order_id"]
        order_name = order_info["order_name"]
        shipping = order_info["shipping_address"]
        items = order_info["line_items"]
        now_iso = datetime.now().strftime("%Y-%m-%dT%H:%M:%SZ")

        logger.info(f"⚡ [FulfillmentAgent] Ingesting order {order_name} (Shopify ID: {order_id}) for {shipping.name}")

        supplier_payload = self.map_to_supplier_payload(order_info)
        po_number = supplier_payload["orderNumber"]

        items_summary = [
            {
                "sku": i.sku,
                "title": i.title,
                "quantity": i.quantity,
                "customer_price": i.price,
            }
            for i in items
        ]

        def result(status: str, **kwargs) -> SupplierFulfillmentResult:
            fields = dict(
                status=status,
                shopify_order_id=order_id,
                shopify_order_number=order_name,
                supplier_po_number=po_number,
                supplier_api_endpoint=self.supplier_api_url,
                customer_name=shipping.name,
                shipping_destination=f"{shipping.city}, {shipping.province} ({shipping.country_code})",
                items_fulfilled=items_summary,
                estimated_delivery_days=0,
                tracking_company=self.supplier_name,
                fulfilled_at=now_iso,
            )
            fields.update(kwargs)
            return SupplierFulfillmentResult(**fields)

        if not self.dry_run and not self.auto_order:
            logger.info(f"SUPPLIER_AUTO_ORDER is off; leaving order {order_name} to the CJ Shopify app.")
            return result("SKIPPED_AUTO_ORDER_DISABLED")

        problems = self.validate_order(order_info)
        if problems:
            message = "; ".join(problems)
            self._log_error_to_file(order_id, "INVALID_ORDER_DATA", message)
            return result("FAILED_INVALID_ORDER", error_message=message)

        try:
            supplier_data = await self.dispatch_to_supplier_api(supplier_payload, order_id=order_id)
        except SupplierAPIError as e:
            logger.warning(f"Supplier fulfillment rejected for order {order_id}: {e.error_message}")
            return result(f"FAILED_SUPPLIER_ERROR_{e.status_code}", error_message=f"[HTTP {e.status_code}] {e.error_message}")
        except Exception as general_err:
            self._log_error_to_file(order_id, "UNEXPECTED_FULFILLMENT_EXCEPTION", str(general_err))
            return result("FAILED_UNEXPECTED_ERROR", error_message=str(general_err))

        # CJ assigns tracking only after the parcel is dispatched; until then the Shopify order
        # stays unfulfilled rather than being marked shipped with a made-up number.
        tracking_number = supplier_data.get("trackingNumber")
        carrier = supplier_data.get("logisticName") or self.supplier_name
        if not tracking_number:
            return result(
                "SUPPLIER_ORDER_CREATED",
                estimated_delivery_days=7,
                tracking_company=carrier,
                supplier_po_number=str(supplier_data.get("orderId") or po_number),
            )

        shopify_res = await self.sync_tracking_to_shopify(
            order_id=order_id,
            tracking_number=tracking_number,
            tracking_company=carrier,
            tracking_url=supplier_data.get("trackingUrl") or f"https://t.17track.net/en#nums={tracking_number}",
        )
        fulfillment_id = None
        if isinstance(shopify_res, dict):
            fulfillment_id = (shopify_res.get("data") or {}).get("fulfillmentCreateV2", {}).get("fulfillment", {}).get("id")

        return result(
            "SUCCESS_FULFILLED",
            estimated_delivery_days=7,
            tracking_code=tracking_number,
            mock_tracking_code=tracking_number if self.dry_run else None,
            tracking_company=carrier,
            shopify_fulfillment_synced=bool(fulfillment_id),
            shopify_fulfillment_id=fulfillment_id,
        )
