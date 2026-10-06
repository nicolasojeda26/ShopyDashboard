"""
Inventory and Price Synchronization Worker (InventorySyncWorker).
Continuously monitors supplier stock levels and wholesale costs,
automatically updating Shopify variant pricing (2.5x margin) and
inventory quantities via GraphQL to prevent stockouts and protect margins.
"""

import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

from config import config
from tools.cj_client import CJClient
from tools.shopify_client import ShopifyClient

logger = logging.getLogger("InventorySyncWorker")

SYNC_HISTORY_FILE = Path("data/inventory_sync_history.json")


class SyncItemChange(BaseModel):
    sku: str
    product_title: str
    shopify_product_id: Optional[str] = None
    old_price: Optional[float] = None
    new_price: Optional[float] = None
    old_stock: Optional[int] = None
    new_stock: Optional[int] = None
    status: str = "UPDATED"
    notes: str = ""


class SyncReport(BaseModel):
    timestamp: str
    items_scanned: int
    items_updated: int
    price_adjustments: int
    stockouts_detected: int
    changes: List[SyncItemChange]
    duration_sec: float
    status: str = "SUCCESS"


class InventorySyncWorker:
    """
    Worker responsible for synchronizing inventory quantities and recalculating
    pricing rules based on real-time supplier fluctuations.
    """

    def __init__(
        self,
        shopify_client: Optional[ShopifyClient] = None,
        cj_client: Optional[CJClient] = None,
        dry_run: Optional[bool] = None,
        auto_reprice: Optional[bool] = None,
    ):
        self.shopify = shopify_client or ShopifyClient(dry_run=dry_run)
        self.cj_client = cj_client or CJClient()
        self.dry_run = dry_run if dry_run is not None else config.DRY_RUN
        # Live repricing is opt-in: cost x 2.5 ignores shipping, so it can undercut a hand-set price.
        self.auto_reprice = auto_reprice if auto_reprice is not None else config.SYNC_AUTO_REPRICE

    def _load_sync_history(self) -> List[Dict[str, Any]]:
        if SYNC_HISTORY_FILE.exists():
            try:
                with open(SYNC_HISTORY_FILE, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                return []
        return []

    def _save_sync_history(self, report_dict: Dict[str, Any]):
        try:
            history = self._load_sync_history()
            history.insert(0, report_dict)
            # Keep last 50 sync reports
            history = history[:50]
            SYNC_HISTORY_FILE.parent.mkdir(exist_ok=True)
            with open(SYNC_HISTORY_FILE, "w", encoding="utf-8") as f:
                json.dump(history, f, indent=2, default=str)
        except Exception as e:
            logger.warning(f"Could not save sync history: {e}")

    async def fetch_supplier_stock_and_cost(self, sku: str) -> Optional[Dict[str, Any]]:
        """
        Queries CJ for up-to-date warehouse stock and wholesale cost.
        Returns None in live mode when CJ cannot be reached, so nothing is changed on guesses.
        """
        if self.dry_run:
            # Deterministic simulation based on SKU
            return {
                "sku": sku,
                "available_stock": 120,
                "wholesale_cost": 8.50,
                "status": "IN_STOCK",
            }

        try:
            data = await self.cj_client.get_stock_and_cost(sku)
        except Exception as e:
            logger.warning(f"Could not read CJ stock/cost for SKU {sku}: {e}")
            return None
        data["status"] = "IN_STOCK" if data["available_stock"] > 0 else "OUT_OF_STOCK"
        return data

    async def sync_inventory_and_pricing(
        self,
        products: Optional[List[Dict[str, Any]]] = None,
    ) -> SyncReport:
        """
        Performs full synchronization against all tracked products.
        """
        import time
        start_time = time.time()
        logger.info(">>> Starting Inventory & Price Synchronization Cycle <<<")

        # Load products from catalog history if not provided
        if not products:
            from api.webhook_listener import load_catalog_history
            products = load_catalog_history()

        if not products and self.dry_run:
            # Fallback mock item
            products = [
                {
                    "optimized_seo_title": "Cinturón de Nylon para Halterofilia Pro Auto-Lock",
                    "shopify_product_id": "gid://shopify/Product/9794800484581",
                    "shopify_variant_id": "gid://shopify/ProductVariant/4455667788",
                    "inventory_item_id": "gid://shopify/InventoryItem/1122334455",
                    "suggested_price": 21.25,
                    "sku": "DS-CINTURON-01",
                }
            ]
        products = products or []

        location_id = await self.shopify.get_primary_location_id()
        changes: List[SyncItemChange] = []
        price_adjustments = 0
        stockouts_detected = 0

        for p in products:
            title = p.get("optimized_seo_title") or p.get("title", "Product")
            sku = p.get("sku")
            prod_id = p.get("shopify_product_id")
            variant_id = p.get("shopify_variant_id")
            inv_item_id = p.get("inventory_item_id")
            current_price = float(p.get("suggested_price") or 0)

            if not sku:
                changes.append(SyncItemChange(sku="", product_title=title, shopify_product_id=prod_id, status="SKIPPED", notes="Sin SKU del proveedor"))
                continue

            supplier_data = await self.fetch_supplier_stock_and_cost(sku)
            if supplier_data is None:
                changes.append(SyncItemChange(sku=sku, product_title=title, shopify_product_id=prod_id, status="SUPPLIER_UNAVAILABLE", notes="No se pudo consultar a CJ; no se modificó nada"))
                continue
            wholesale_cost = float(supplier_data["wholesale_cost"])
            available_stock = int(supplier_data["available_stock"])

            # Calculate target price maintaining 2.5x dropshipping margin
            expected_price = round(wholesale_cost * 2.5, 2)

            price_changed = abs(expected_price - current_price) > 0.05
            is_stockout = available_stock <= 0
            notes = []

            if price_changed or is_stockout:
                if price_changed:
                    price_adjustments += 1
                    if (self.dry_run or self.auto_reprice) and prod_id and variant_id:
                        logger.info(f"⚡ Price update for '{title}': ${current_price} -> ${expected_price}")
                        await self.shopify.update_variant_price(
                            product_id=prod_id,
                            variant_id=variant_id,
                            price=expected_price,
                            compare_at_price=None,
                        )
                        notes.append("Precio recalculado por variación de costo mayorista")
                    else:
                        notes.append(f"Precio sugerido ${expected_price} (no aplicado: SYNC_AUTO_REPRICE desactivado)")

                if is_stockout:
                    stockouts_detected += 1
                    if inv_item_id:
                        logger.warning(f"⚠ Stockout detected for '{title}'. Setting inventory to 0.")
                        await self.shopify.set_inventory_quantity(
                            inventory_item_id=inv_item_id,
                            location_id=location_id,
                            available_quantity=0,
                        )
                    notes.append("Agotado en depósito")

                changes.append(
                    SyncItemChange(
                        sku=sku,
                        product_title=title,
                        shopify_product_id=prod_id,
                        old_price=current_price,
                        new_price=expected_price if price_changed else current_price,
                        new_stock=available_stock,
                        status="STOCKOUT" if is_stockout else "PRICE_UPDATED",
                        notes="; ".join(notes),
                    )
                )

        duration = round(time.time() - start_time, 2)
        report = SyncReport(
            timestamp=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            items_scanned=len(products),
            items_updated=len(changes),
            price_adjustments=price_adjustments,
            stockouts_detected=stockouts_detected,
            changes=changes,
            duration_sec=duration,
            status="SUCCESS",
        )

        logger.info(f"✔ Synchronization finished in {duration}s. {len(changes)} products updated.")
        self._save_sync_history(report.model_dump())
        return report
