"""
Shopify Webhook Listener, Auto-Fulfillment Service & Web Dashboard API.
Listens for orders/create webhooks, verifies HMAC signatures, triggers FulfillmentAgent,
and serves the real-time Multi-Agent Dropshipping Dashboard SPA.
"""

import base64
import hashlib
import hmac
import json
import logging
import os
import secrets
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import Body, FastAPI, Header, HTTPException, Request, Response, status
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from rich.console import Console
from rich.table import Table

from config import config
from agents.fulfillment_agent import FulfillmentAgent
from agents.inventory_sync_worker import InventorySyncWorker
from agents.orchestrator import DropshippingPipelineOrchestrator
from tools.shopify_client import ShopifyClient
from utils.csv_parser import parse_csv

# Ensure UTF-8 logging on Windows
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

console = Console(force_terminal=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("WebhookListener")

app = FastAPI(
    title="Shopify Dropshipping Auto-Fulfillment & Dashboard API",
    version="2.0.0",
    description="Real-time multi-agent dropshipping pipeline dashboard and automated fulfillment service.",
)


@app.middleware("http")
async def protect_dashboard_api(request: Request, call_next):
    """Require a private bearer token for dashboard and administrative APIs."""
    if request.url.path.startswith("/api/"):
        expected_token = config.DASHBOARD_API_TOKEN
        if not expected_token:
            return JSONResponse(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                content={"detail": "Dashboard API is disabled until DASHBOARD_API_TOKEN is configured."},
            )
        supplied = request.headers.get("Authorization", "")
        scheme, _, token = supplied.partition(" ")
        if scheme.lower() != "bearer" or not secrets.compare_digest(token, expected_token):
            return JSONResponse(
                status_code=status.HTTP_401_UNAUTHORIZED,
                content={"detail": "Valid dashboard bearer token required."},
                headers={"WWW-Authenticate": "Bearer"},
            )
    return await call_next(request)

# Static files mount
STATIC_DIR = Path(__file__).resolve().parent / "static"
if not STATIC_DIR.exists():
    STATIC_DIR.mkdir(parents=True, exist_ok=True)

app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

fulfillment_agent = FulfillmentAgent()
inventory_worker = InventorySyncWorker()

# Persistence paths
DATA_DIR = Path("data")
DATA_DIR.mkdir(exist_ok=True)
ORDERS_HISTORY_FILE = DATA_DIR / "orders_history.json"
CATALOG_HISTORY_FILE = DATA_DIR / "catalog_history.json"


def load_orders_history() -> List[Dict[str, Any]]:
    if ORDERS_HISTORY_FILE.exists():
        try:
            with open(ORDERS_HISTORY_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return []
    return []


def save_orders_history(orders: List[Dict[str, Any]]):
    try:
        with open(ORDERS_HISTORY_FILE, "w", encoding="utf-8") as f:
            json.dump(orders, f, indent=2, default=str)
    except Exception as e:
        logger.warning(f"Could not save orders history: {e}")


def load_catalog_history() -> List[Dict[str, Any]]:
    if CATALOG_HISTORY_FILE.exists():
        try:
            with open(CATALOG_HISTORY_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return []
    # Fallback to results.json if exists
    results_path = Path("results.json")
    if results_path.exists():
        try:
            with open(results_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                items = data if isinstance(data, list) else [data]
                return [
                    {
                        "original_title": item.get("catalog_result", {}).get("original_title"),
                        "optimized_seo_title": item.get("catalog_result", {}).get("optimized_seo_title"),
                        "suggested_price": item.get("catalog_result", {}).get("suggested_price"),
                        "compare_at_price": item.get("catalog_result", {}).get("compare_at_price"),
                        "product_type": item.get("catalog_result", {}).get("product_type"),
                        "shopify_product_id": item.get("catalog_result", {}).get("shopify_product_id"),
                        "hook": item.get("catalog_result", {}).get("hook"),
                        "primary_cdn_url": item.get("art_result", {}).get("primary_cdn_url"),
                    }
                    for item in items
                    if "catalog_result" in item
                ]
        except Exception:
            return []
    return []


def save_catalog_history(products: List[Dict[str, Any]]):
    try:
        with open(CATALOG_HISTORY_FILE, "w", encoding="utf-8") as f:
            json.dump(products, f, indent=2, default=str)
    except Exception as e:
        logger.warning(f"Could not save catalog history: {e}")


def verify_shopify_hmac(raw_body: bytes, hmac_header: str, secret: str) -> bool:
    """
    Verifies the Shopify HMAC-SHA256 signature against the raw request body.
    """
    if not hmac_header or not secret:
        return False
    try:
        digest = hmac.new(
            secret.encode("utf-8"),
            raw_body,
            hashlib.sha256,
        ).digest()
        computed_hmac = base64.b64encode(digest).decode("utf-8")
        return hmac.compare_digest(computed_hmac, hmac_header)
    except Exception as e:
        logger.error(f"HMAC verification failed with error: {e}")
        return False


# -----------------------------------------------------------------------------
# Web Dashboard Endpoints
# -----------------------------------------------------------------------------

@app.get("/")
async def root():
    index_path = STATIC_DIR / "index.html"
    if index_path.exists():
        return FileResponse(str(index_path))
    return {
        "service": "Shopify Auto-Fulfillment Webhook Service",
        "status": "online",
        "shop_domain": config.SHOP_DOMAIN,
        "endpoint": "/webhook/shopify/order",
    }


@app.get("/health")
async def health_check():
    return {"status": "healthy", "service": "shopify-webhook-listener"}


@app.get("/api/stats")
async def get_dashboard_stats():
    orders = load_orders_history()
    products = load_catalog_history()
    synced_tracking = sum(1 for o in orders if o.get("shopify_fulfillment_synced"))
    sync_history = inventory_worker._load_sync_history()
    last_sync = sync_history[0].get("timestamp") if sync_history else "No realizado aún"

    return {
        "shop_domain": config.SHOP_DOMAIN,
        "client_id": config.CLIENT_ID,
        "dry_run": config.DRY_RUN,
        "products_count": len(products),
        "orders_count": len(orders),
        "tracking_synced_count": synced_tracking,
        "last_sync_timestamp": last_sync,
        "total_sync_count": len(sync_history),
        "status": "ONLINE",
    }


@app.get("/api/orders")
async def get_orders_list():
    return load_orders_history()


@app.get("/api/products")
async def get_products_list():
    return load_catalog_history()


@app.get("/api/inventory/sync/history")
async def get_inventory_sync_history():
    return inventory_worker._load_sync_history()


@app.post("/api/inventory/sync")
async def trigger_inventory_and_price_sync():
    """
    Triggers an immediate background synchronization cycle across all catalog products.
    """
    products = load_catalog_history()
    report = await inventory_worker.sync_inventory_and_pricing(products)
    return {
        "success": True,
        "message": f"Sincronización completada. {report.items_updated} productos actualizados.",
        "report": report.model_dump(),
    }


@app.get("/api/logs")
async def get_system_logs():
    log_file = Path("error_log.txt")
    if log_file.exists():
        try:
            content = log_file.read_text(encoding="utf-8")
            lines = content.strip().splitlines()
            # Return last 50 lines
            recent_logs = "\n".join(lines[-50:]) if lines else "No hay logs registrados."
            return {"logs": recent_logs}
        except Exception as e:
            return {"logs": f"Error al leer error_log.txt: {e}"}
    return {"logs": "El archivo error_log.txt no contiene incidentes registrados. Sistema 100% operativo."}


class PipelineRunRequest(BaseModel):
    input_file: str = "data/catalogo_proveedor.csv"
    live: bool = False


@app.post("/api/pipeline/run")
async def trigger_pipeline_execution(payload: PipelineRunRequest = Body(...)):
    """
    Executes the multi-agent pipeline on the specified input CSV.
    """
    input_path = Path(payload.input_file)
    if not input_path.exists():
        raise HTTPException(status_code=400, detail=f"Archivo '{payload.input_file}' no encontrado.")

    is_dry_run = not payload.live
    client = ShopifyClient(dry_run=is_dry_run)
    orchestrator = DropshippingPipelineOrchestrator(shopify_client=client)

    if str(input_path).endswith(".csv"):
        items = parse_csv(str(input_path))
    else:
        with open(input_path, "r", encoding="utf-8") as f:
            data = json.load(f)
            items = data if isinstance(data, list) else [data]

    start_time = time.time()
    successful_reports = []

    for item in items:
        try:
            report = await orchestrator.run(item)
            successful_reports.append(report)
        except Exception as e:
            logger.error(f"Error executing item in pipeline API: {e}")

    total_duration = round(time.time() - start_time, 2)

    # Update catalog history
    existing_catalog = load_catalog_history()
    for r in successful_reports:
        prod_entry = {
            "original_title": r.catalog_result.original_title,
            "optimized_seo_title": r.catalog_result.optimized_seo_title,
            "suggested_price": r.catalog_result.suggested_price,
            "compare_at_price": r.catalog_result.compare_at_price,
            "product_type": r.catalog_result.product_type,
            "shopify_product_id": r.catalog_result.shopify_product_id,
            "hook": r.catalog_result.hook,
            "primary_cdn_url": r.art_result.primary_cdn_url,
            "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        # Avoid duplicate titles
        existing_catalog = [p for p in existing_catalog if p.get("optimized_seo_title") != prod_entry["optimized_seo_title"]]
        existing_catalog.insert(0, prod_entry)

    save_catalog_history(existing_catalog)

    return {
        "success": True,
        "processed_count": len(successful_reports),
        "total_duration_sec": total_duration,
        "reports": [r.model_dump() for r in successful_reports],
    }


@app.post("/api/webhook/simulate")
async def simulate_webhook_purchase():
    """
    Simulates an incoming signed Shopify orders/create webhook and triggers auto-fulfillment.
    """
    sample_order_id = int(time.time() * 1000)
    order_num = 1000 + (sample_order_id % 9000)

    sample_order_payload = {
        "id": sample_order_id,
        "order_number": order_num,
        "name": f"#{order_num}",
        "email": f"athlete.{uuid.uuid4().hex[:4]}@crossfit-wod.com",
        "created_at": datetime.now().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "customer": {
            "first_name": "Valentina",
            "last_name": "Rossi",
            "phone": "+1 305-555-4821",
        },
        "shipping_address": {
            "first_name": "Valentina",
            "last_name": "Rossi",
            "address1": "780 Brickell Ave",
            "address2": "Suite 1204",
            "city": "Miami",
            "province": "Florida",
            "province_code": "FL",
            "zip": "33131",
            "country": "United States",
            "country_code": "US",
            "phone": "+1 305-555-4821",
        },
        "line_items": [
            {
                "id": 998811,
                "variant_id": 776655,
                "title": "Cinturón de Nylon para Halterofilia Pro Auto-Lock",
                "sku": "DS-CINTURON-01",
                "quantity": 1,
                "price": "21.25",
            }
        ],
        "total_price": "21.25",
        "currency": "USD",
    }

    result = await fulfillment_agent.process_order_webhook(sample_order_payload)

    # Store in history
    history = load_orders_history()
    history.insert(0, result.model_dump())
    save_orders_history(history)

    return {
        "success": True,
        "message": "Simulated purchase webhook dispatched and auto-fulfilled successfully.",
        "fulfillment": result.model_dump(),
    }


# -----------------------------------------------------------------------------
# Shopify Live Webhook Listener
# -----------------------------------------------------------------------------

@app.post("/webhook/shopify/order", status_code=status.HTTP_200_OK)
async def handle_shopify_order_webhook(
    request: Request,
    x_shopify_hmac_sha256: str = Header(None, alias="X-Shopify-Hmac-Sha256"),
    x_shopify_topic: str = Header(None, alias="X-Shopify-Topic"),
    x_shopify_shop_domain: str = Header(None, alias="X-Shopify-Shop-Domain"),
):
    """
    Receives and processes Shopify orders/create webhooks.
    Validates HMAC signature using SHOPIFY_CLIENT_SECRET.
    """
    raw_body = await request.body()

    logger.info(f"Incoming Webhook: Topic='{x_shopify_topic}', Shop='{x_shopify_shop_domain}'")

    # 1. HMAC Signature Verification
    client_secret = config.CLIENT_SECRET
    if not client_secret:
        logger.error("SHOPIFY_CLIENT_SECRET is not configured.")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Server configuration error: SHOPIFY_CLIENT_SECRET missing.",
        )

    is_valid_hmac = False
    if x_shopify_hmac_sha256:
        is_valid_hmac = verify_shopify_hmac(raw_body, x_shopify_hmac_sha256, client_secret)

    if not is_valid_hmac:
        logger.warning(f"❌ Invalid Shopify HMAC signature received: {x_shopify_hmac_sha256}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Unauthorized: Invalid Shopify HMAC-SHA256 signature.",
        )

    # 2. Ingest Payload
    try:
        payload = json.loads(raw_body.decode("utf-8")) if raw_body else {}
    except Exception as e:
        logger.error(f"Failed to parse JSON body: {e}")
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Malformed JSON body")

    # 3. Process via FulfillmentAgent
    result = await fulfillment_agent.process_order_webhook(payload)

    # Store in history
    history = load_orders_history()
    history.insert(0, result.model_dump())
    save_orders_history(history)

    # 4. Rich Terminal Output
    table_style = "bold green" if not result.status.startswith("FAILED") else "bold red"
    table = Table(title=f"📦 Auto-Fulfillment: Orden {result.shopify_order_number}", show_header=True, header_style=table_style)
    table.add_column("Campo", style="cyan", width=24)
    table.add_column("Detalle", style="white")
    table.add_row("Shopify Order ID", result.shopify_order_id)
    table.add_row("Cliente", result.customer_name)
    table.add_row("Destino", result.shipping_destination)
    table.add_row("Proveedor PO #", f"[bold yellow]{result.supplier_po_number}[/bold yellow]")
    table.add_row("Tracking Code", f"[bold green]{result.tracking_code or result.mock_tracking_code or 'N/A'}[/bold green]")
    table.add_row("Carrier", result.tracking_company)
    table.add_row("Shopify Sync", "✔ Sincronizado" if result.shopify_fulfillment_synced else "❌ No sincronizado")
    table.add_row("Items Despachados", str(len(result.items_fulfilled)))
    table.add_row("Estado", f"[{'bold green' if not result.status.startswith('FAILED') else 'bold red'}]{result.status}[/]")
    if result.error_message:
        table.add_row("Error", f"[bold red]{result.error_message}[/bold red]")

    console.print(table)

    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content={
            "success": True,
            "message": "Order successfully ingested and dispatched to dropshipping supplier.",
            "fulfillment": result.model_dump(),
        },
    )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("api.webhook_listener:app", host="0.0.0.0", port=8000, reload=True)
