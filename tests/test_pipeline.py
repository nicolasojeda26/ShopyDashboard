"""
Unit and Integration tests for the Shopify Multi-Agent Pipeline.
"""

import json
import pytest
from tools.shopify_client import ShopifyClient
from tools.image_generator import ImageGeneratorTool
from agents.catalog_agent import CatalogAgent
from agents.art_agent import ArtAgent
from agents.liquid_coder_agent import LiquidCoderAgent
from agents.fulfillment_agent import FulfillmentAgent
from agents.orchestrator import DropshippingPipelineOrchestrator
from config import config
from tools.cj_client import CJClient


@pytest.fixture
def dashboard_auth(monkeypatch):
    import api.webhook_listener as listener
    from agents.inventory_sync_worker import InventorySyncWorker

    monkeypatch.setattr(config, "DASHBOARD_API_TOKEN", "test-dashboard-token")
    # The listener builds its agents from .env at import time; never let tests reach the live store or CJ.
    monkeypatch.setattr(listener, "fulfillment_agent", FulfillmentAgent(dry_run=True))
    monkeypatch.setattr(listener, "inventory_worker", InventorySyncWorker(dry_run=True))
    return {"Authorization": "Bearer test-dashboard-token"}


@pytest.fixture
def mock_shopify_client():
    return ShopifyClient(dry_run=True)


@pytest.fixture
def image_tool():
    return ImageGeneratorTool()


@pytest.fixture
def sample_payload():
    return {
        "title": "HOT SALE 2026 Dropshipping Portable Neck Fan Hands-Free Bladeless Neckband Cooler",
        "category": "Electronics",
        "product_type": "Neck Fan",
        "vendor": "CoolBreeze Co",
        "cost": 8.00,
        "features": [
            {"title": "360° Surround Airflow", "description": "Twin turbines provide full neck and face cooling."},
            {"title": "Bladeless Safe Design", "description": "Safe for long hair and children, completely pinch-free."},
            {"title": "Ultra-Quiet Operation", "description": "Operates under 25dB for peaceful work and travel."},
        ],
        "specs": {
            "Battery": "4000mAh Lithium",
            "Weight": "220g",
            "Speed Levels": "3 Adjustable Modes",
        },
        "hook": "Stay refreshingly cool anytime, anywhere without messy fan blades.",
    }


# -----------------------------------------------------------------------------
# 1. Tests for Connection Tool (ShopifyClient & OAuth)
# -----------------------------------------------------------------------------
def test_oauth_authorization_url(mock_shopify_client):
    auth_url = mock_shopify_client.get_authorization_url()
    assert "da6gne-6x.myshopify.com" in auth_url
    assert "client_id=" in auth_url
    for scope in config.SCOPES:
        assert scope in auth_url


@pytest.mark.asyncio
async def test_shopify_product_creation(mock_shopify_client):
    result = await mock_shopify_client.create_product({
        "title": "Smart Neck Fan Pro",
        "descriptionHtml": "<p>High performance cooling</p>",
    })
    product_data = result.get("data", {}).get("productCreate", {}).get("product", {})
    assert product_data.get("id") is not None
    assert product_data.get("title") == "Smart Neck Fan Pro"


@pytest.mark.asyncio
async def test_shopify_cdn_upload(mock_shopify_client):
    upload_res = await mock_shopify_client.upload_file_to_cdn(
        file_name="hero_banner.png",
        file_content=b"test_png_bytes",
        alt_text="Banner test",
    )
    assert "cdn.shopify.com" in upload_res["url"]
    assert upload_res["file_id"] != ""


# -----------------------------------------------------------------------------
# 2. Tests for Catalog Agent
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_catalog_agent_processing(mock_shopify_client, sample_payload):
    catalog_agent = CatalogAgent(shopify_client=mock_shopify_client)
    result = await catalog_agent.process_payload(sample_payload)

    # Check title optimization: spam keywords removed
    assert "HOT SALE" not in result.optimized_seo_title
    assert "Dropshipping" not in result.optimized_seo_title
    assert "Neck Fan" in result.optimized_seo_title

    # Check pricing markup
    assert result.suggested_price == 20.0  # 8.00 * 2.5
    assert result.compare_at_price > result.suggested_price

    # Check benefits
    assert len(result.benefits) == 3

    # Check HTML Description generated
    assert "<table" in result.description_html
    assert "Envío Rápido Asegurado" in result.description_html
    assert result.shopify_product_id is not None


@pytest.mark.asyncio
async def test_catalog_agent_llm_generation(mock_shopify_client, sample_payload, monkeypatch):
    import httpx

    catalog_agent = CatalogAgent(
        shopify_client=mock_shopify_client,
        llm_api_key="test_mock_gemini_key_123",
        llm_model="gemini-1.5-flash",
        llm_provider="gemini",
    )

    # Mock Gemini API response
    llm_mock_json = {
        "seo_title": "Pro Knee Sleeves 7mm Compression Brace",
        "persuasive_description": "Diseñadas para atletas de alto rendimiento y cross-training que buscan máxima estabilidad articular y superar sus marcas personales en sentadillas pesadas.",
        "benefits": [
            {"title": "Neopreno SCR 7mm Grado Médico", "description": "Compresión uniforme y calor terapéutico durante WODs intensos.", "icon": "⚡"},
            {"title": "Costura Reforzada Cuádruple", "description": "Resistencia extrema ante tensiones de cargas máximas sin desgarros.", "icon": "🛡️"},
            {"title": "Ajuste Anatómico 3D Antideslizante", "description": "Cero desplazamientos durante sentadillas profundas o levantamientos olímpicos.", "icon": "🔥"}
        ],
        "specs": {
            "Grosor": "7mm SCR Neoprene",
            "Disciplina": "Cross Training, Powerlifting, Weightlifting",
            "Ajuste": "Ergonómico Anatómico 3D",
            "Garantía": "12 Meses Oficial"
        }
    }

    async def mock_post(self, url, *args, **kwargs):
        return httpx.Response(
            status_code=200,
            json={
                "candidates": [
                    {
                        "content": {
                            "parts": [
                                {"text": json.dumps(llm_mock_json)}
                            ]
                        }
                    }
                ]
            },
            request=httpx.Request("POST", str(url))
        )

    monkeypatch.setattr(httpx.AsyncClient, "post", mock_post)

    result = await catalog_agent.process_payload(sample_payload)

    assert result.generated_by_llm is True
    assert result.optimized_seo_title == "Pro Knee Sleeves 7mm Compression Brace"
    assert len(result.optimized_seo_title) <= 60
    assert "cross-training" in result.hook
    assert len(result.benefits) == 3
    assert result.benefits[0].title == "Neopreno SCR 7mm Grado Médico"
    assert result.specs["Grosor"] == "7mm SCR Neoprene"
    assert "Envío Rápido Asegurado" in result.description_html


@pytest.mark.asyncio
async def test_catalog_agent_llm_fallback_on_error(mock_shopify_client, sample_payload, monkeypatch):
    import httpx
    from pathlib import Path

    catalog_agent = CatalogAgent(
        shopify_client=mock_shopify_client,
        llm_api_key="test_mock_gemini_key_123",
        llm_model="gemini-1.5-flash",
        llm_provider="gemini",
    )

    # Mock HTTP 429 Quota Exceeded error
    async def mock_post_429(self, url, *args, **kwargs):
        return httpx.Response(
            status_code=429,
            text='{"error": {"code": 429, "message": "Resource has been exhausted (e.g. check quota)."}}',
            request=httpx.Request("POST", str(url))
        )

    monkeypatch.setattr(httpx.AsyncClient, "post", mock_post_429)

    result = await catalog_agent.process_payload(sample_payload)

    # Verifies graceful fallback to rule-based engine
    assert result.generated_by_llm is False
    assert "HOT SALE" not in result.optimized_seo_title
    assert len(result.benefits) == 3
    assert result.shopify_product_id is not None

    # Verifies incident was logged to error_log.txt
    error_log_path = Path("error_log.txt")
    assert error_log_path.exists()
    log_content = error_log_path.read_text(encoding="utf-8")
    assert "LLM_QUOTA_EXCEEDED" in log_content


# -----------------------------------------------------------------------------
# 3. Tests for Art Agent
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_art_agent_generation_and_upload(mock_shopify_client, image_tool, sample_payload):
    catalog_agent = CatalogAgent(shopify_client=mock_shopify_client)
    product = await catalog_agent.process_payload(sample_payload)

    art_agent = ArtAgent(shopify_client=mock_shopify_client, image_tool=image_tool)
    art_output = await art_agent.generate_and_upload_assets(product)

    assert len(art_output.assets) == 3
    assert art_output.primary_cdn_url.startswith("https://cdn.shopify.com")
    for asset in art_output.assets:
        assert asset.shopify_file_id is not None
        assert "prompt" in asset.model_dump()


# -----------------------------------------------------------------------------
# 4. Tests for Liquid Coder Agent
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_liquid_coder_agent(mock_shopify_client, image_tool, sample_payload):
    catalog_agent = CatalogAgent(shopify_client=mock_shopify_client)
    product = await catalog_agent.process_payload(sample_payload)

    art_agent = ArtAgent(shopify_client=mock_shopify_client, image_tool=image_tool)
    art_output = await art_agent.generate_and_upload_assets(product)

    liquid_agent = LiquidCoderAgent(shopify_client=mock_shopify_client)
    frontend_output = await liquid_agent.inject_theme_enhancements(product, art_output)

    assert frontend_output.status == "success"
    assert frontend_output.liquid_snippet_key.startswith("snippets/")
    assert frontend_output.template_json_key == "templates/product.json"
    assert len(frontend_output.modified_assets) >= 2


# -----------------------------------------------------------------------------
# 5. Integration Test: Full Multi-Agent Orchestrator Pipeline
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_full_pipeline_orchestrator(mock_shopify_client, image_tool, sample_payload):
    orchestrator = DropshippingPipelineOrchestrator(
        shopify_client=mock_shopify_client,
        image_tool=image_tool,
    )
    report = await orchestrator.run(sample_payload)

    assert report.status == "COMPLETED_SUCCESSFULLY"
    assert report.shop_domain == "da6gne-6x.myshopify.com"
    assert report.catalog_result.optimized_seo_title != ""
    assert len(report.art_result.assets) >= 3
    assert len(report.frontend_result.modified_assets) >= 2
    assert report.execution_time_sec >= 0


# -----------------------------------------------------------------------------
# 6. Tests for Fulfillment Agent & Supplier REST API Integration
# -----------------------------------------------------------------------------
@pytest.fixture
def sample_shopify_order():
    return {
        "id": 998811223344,
        "order_number": 1005,
        "name": "#1005",
        "email": "customer.test@example.com",
        "customer": {
            "first_name": "Carlos",
            "last_name": "Santana",
            "phone": "+1 305-555-1234",
        },
        "shipping_address": {
            "first_name": "Carlos",
            "last_name": "Santana",
            "address1": "742 Evergreen Terrace",
            "address2": "Apt 4B",
            "city": "Springfield",
            "province": "Oregon",
            "province_code": "OR",
            "zip": "97477",
            "country": "United States",
            "country_code": "US",
            "phone": "+1 305-555-1234",
        },
        "line_items": [
            {
                "id": 112233,
                "variant_id": 445566,
                "title": "Smart Neck Fan Pro",
                "sku": "DS-NECK-FAN-01",
                "quantity": 2,
                "price": "39.99",
            }
        ],
        "total_price": "79.98",
        "currency": "USD",
    }


@pytest.mark.asyncio
async def test_fulfillment_agent_payload_mapping(mock_shopify_client, sample_shopify_order):
    agent = FulfillmentAgent(shopify_client=mock_shopify_client, dry_run=True)
    order_details = agent.extract_order_details(sample_shopify_order)

    assert order_details["order_id"] == "998811223344"
    assert order_details["customer_name"] == "Carlos Santana"
    assert order_details["shipping_address"].zip == "97477"
    assert len(order_details["line_items"]) == 1
    assert order_details["line_items"][0].sku == "DS-NECK-FAN-01"
    assert agent.validate_order(order_details) == []

    supplier_payload = agent.map_to_supplier_payload(order_details)
    assert supplier_payload["orderNumber"] == "SHOPIFY-1005-998811223344"
    assert supplier_payload["shippingCustomerName"] == "Carlos Santana"
    assert supplier_payload["shippingAddress"] == "742 Evergreen Terrace"
    assert supplier_payload["shippingAddress2"] == "Apt 4B"
    assert supplier_payload["shippingCountryCode"] == "US"
    assert supplier_payload["shippingCountry"] == "United States"
    assert supplier_payload["logisticName"] == config.SUPPLIER_LOGISTIC_NAME
    assert supplier_payload["fromCountryCode"] == "CN"
    assert supplier_payload["payType"] == config.SUPPLIER_PAY_TYPE
    assert supplier_payload["products"] == [{"sku": "DS-NECK-FAN-01", "quantity": 2, "storeLineItemId": "112233"}]


def test_fulfillment_agent_does_not_invent_missing_data(mock_shopify_client, sample_shopify_order):
    agent = FulfillmentAgent(shopify_client=mock_shopify_client, dry_run=True)
    sample_shopify_order["shipping_address"] = None
    sample_shopify_order["line_items"][0]["sku"] = None
    order_details = agent.extract_order_details(sample_shopify_order)

    assert order_details["shipping_address"].address1 == ""
    assert order_details["shipping_address"].city == ""
    problems = agent.validate_order(order_details)
    assert "missing shipping address1" in problems
    assert any("has no SKU" in p for p in problems)


@pytest.mark.asyncio
async def test_fulfillment_agent_process_order_success(mock_shopify_client, sample_shopify_order):
    agent = FulfillmentAgent(shopify_client=mock_shopify_client, dry_run=True)
    result = await agent.process_order_webhook(sample_shopify_order)

    assert result.status == "SUCCESS_FULFILLED"
    assert result.shopify_order_id == "998811223344"
    assert result.tracking_code is not None
    assert result.tracking_code.startswith("CJ")
    assert result.shopify_fulfillment_synced is True


@pytest.mark.asyncio
async def test_fulfillment_agent_skips_when_auto_order_disabled(mock_shopify_client, sample_shopify_order, monkeypatch):
    import httpx

    async def fail_request(self, *args, **kwargs):
        raise AssertionError("CJ must not be called when SUPPLIER_AUTO_ORDER is off")

    monkeypatch.setattr(httpx.AsyncClient, "request", fail_request)
    agent = FulfillmentAgent(
        cj_client=CJClient(api_key="test_key"),
        shopify_client=mock_shopify_client,
        dry_run=False,
        auto_order=False,
    )
    result = await agent.process_order_webhook(sample_shopify_order)

    assert result.status == "SKIPPED_AUTO_ORDER_DISABLED"
    assert result.tracking_code is None
    assert result.shopify_fulfillment_synced is False


@pytest.mark.asyncio
async def test_fulfillment_agent_real_http_post_mocked(mock_shopify_client, sample_shopify_order, monkeypatch, tmp_path):
    import httpx

    # Create agent in live HTTP mode with configured API key
    agent = FulfillmentAgent(
        cj_client=CJClient(api_key="test_cj_api_key_123", base_url="https://cj.test/api2.0/v1", token_cache=tmp_path / "token.json"),
        shopify_client=mock_shopify_client,
        dry_run=False,
        auto_order=True,
    )

    captured = []

    async def mock_request(self, method, url, *args, **kwargs):
        captured.append({"url": str(url), "headers": kwargs.get("headers", {}), "json": kwargs.get("json", {})})
        if str(url).endswith("/authentication/getAccessToken"):
            body = {"code": 200, "result": True, "data": {"accessToken": "exchanged_access_token"}}
        else:
            body = {
                "code": 200,
                "result": True,
                "data": {"orderId": "CJ-ORDER-42", "orderNumber": kwargs["json"]["orderNumber"], "orderStatus": "CREATED"},
            }
        return httpx.Response(status_code=200, json=body, request=httpx.Request(method, str(url)))

    monkeypatch.setattr(httpx.AsyncClient, "request", mock_request)

    result = await agent.process_order_webhook(sample_shopify_order)

    auth_call, order_call = captured
    assert auth_call["url"] == "https://cj.test/api2.0/v1/authentication/getAccessToken"
    assert auth_call["json"] == {"apiKey": "test_cj_api_key_123"}
    assert order_call["url"] == "https://cj.test/api2.0/v1/shopping/order/createOrderV2"
    assert order_call["headers"]["CJ-Access-Token"] == "exchanged_access_token"
    assert order_call["json"]["shippingCustomerName"] == "Carlos Santana"
    # CJ assigns tracking later: the order must not be marked fulfilled with a made-up number
    assert result.status == "SUPPLIER_ORDER_CREATED"
    assert result.supplier_po_number == "CJ-ORDER-42"
    assert result.tracking_code is None
    assert result.shopify_fulfillment_synced is False


@pytest.mark.asyncio
async def test_fulfillment_agent_error_handling_and_logging(mock_shopify_client, sample_shopify_order, monkeypatch, tmp_path):
    import httpx
    from pathlib import Path

    (tmp_path / "token.json").write_text(
        json.dumps({"access_token": "cached", "api_key_tail": "_token"[-6:], "expires_at": 9999999999}), encoding="utf-8"
    )
    agent = FulfillmentAgent(
        cj_client=CJClient(api_key="test_token", base_url="https://cj.test/api2.0/v1", token_cache=tmp_path / "token.json"),
        shopify_client=mock_shopify_client,
        dry_run=False,
        auto_order=True,
    )

    # Test HTTP 400 (Out of stock)
    async def mock_post_400(self, method, url, *args, **kwargs):
        return httpx.Response(
            status_code=400,
            text='{"code": 400, "message": "Product SKU DS-NECK-FAN-01 is out of stock in warehouse"}',
            request=httpx.Request(method, str(url))
        )

    monkeypatch.setattr(httpx.AsyncClient, "request", mock_post_400)
    result_400 = await agent.process_order_webhook(sample_shopify_order)

    assert result_400.status == "FAILED_SUPPLIER_ERROR_400"
    assert result_400.shopify_fulfillment_synced is False
    assert "HTTP 400" in result_400.error_message

    # Test HTTP 402 (Insufficient balance)
    async def mock_post_402(self, method, url, *args, **kwargs):
        return httpx.Response(
            status_code=402,
            text='{"code": 402, "message": "Insufficient funds in supplier wallet. Please recharge."}',
            request=httpx.Request(method, str(url))
        )

    monkeypatch.setattr(httpx.AsyncClient, "request", mock_post_402)
    result_402 = await agent.process_order_webhook(sample_shopify_order)

    assert result_402.status == "FAILED_SUPPLIER_ERROR_402"
    assert "HTTP 402" in result_402.error_message

    # CJ also reports business errors as HTTP 200 with result=false
    async def mock_result_false(self, method, url, *args, **kwargs):
        return httpx.Response(
            status_code=200,
            json={"code": 1603001, "result": False, "message": "Order number already exists"},
            request=httpx.Request(method, str(url))
        )

    monkeypatch.setattr(httpx.AsyncClient, "request", mock_result_false)
    result_dup = await agent.process_order_webhook(sample_shopify_order)

    assert result_dup.status == "FAILED_SUPPLIER_ERROR_1603001"
    assert "already exists" in result_dup.error_message

    # Verify error_log.txt exists and contains recorded error events
    error_log_path = Path("error_log.txt")
    assert error_log_path.exists()
    log_content = error_log_path.read_text(encoding="utf-8")
    assert "OUT_OF_STOCK_OR_BAD_REQUEST" in log_content
    assert "INSUFFICIENT_FUNDS" in log_content


@pytest.mark.asyncio
async def test_shopify_graphql_fulfillment_mutation(mock_shopify_client):
    res = await mock_shopify_client.create_order_fulfillment(
        order_id="998811223344",
        tracking_number="CJ8877665544YQ",
        tracking_company="CJ_DROPSHIPPING",
        tracking_url="https://t.17track.net/en#nums=CJ8877665544YQ",
    )
    fulfillment = res.get("data", {}).get("fulfillmentCreateV2", {}).get("fulfillment", {})
    assert fulfillment.get("id") is not None
    assert fulfillment.get("status") == "SUCCESS"


# -----------------------------------------------------------------------------
# 7. Tests for FastAPI Web Dashboard Endpoints
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_dashboard_api_endpoints(dashboard_auth):
    from httpx import ASGITransport, AsyncClient
    from api.webhook_listener import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test", headers=dashboard_auth) as ac:
        # Test GET / (HTML Dashboard)
        res_root = await ac.get("/")
        assert res_root.status_code == 200

        # Test GET /api/stats
        res_stats = await ac.get("/api/stats")
        assert res_stats.status_code == 200
        stats_data = res_stats.json()
        assert "shop_domain" in stats_data
        assert "products_count" in stats_data

        # Test GET /api/orders
        res_orders = await ac.get("/api/orders")
        assert res_orders.status_code == 200
        assert isinstance(res_orders.json(), list)

        # Test GET /api/products
        res_products = await ac.get("/api/products")
        assert res_products.status_code == 200
        assert isinstance(res_products.json(), list)

        # Test GET /api/logs
        res_logs = await ac.get("/api/logs")
        assert res_logs.status_code == 200
        assert "logs" in res_logs.json()

        # Test POST /api/webhook/simulate
        res_sim = await ac.post("/api/webhook/simulate")
        assert res_sim.status_code == 200
        sim_data = res_sim.json()
        assert sim_data.get("success") is True
        assert "fulfillment" in sim_data

        # Test POST /api/pipeline/run (dry-run mode)
        res_pipe = await ac.post(
            "/api/pipeline/run",
            json={"input_file": "data/test_llm.csv", "live": False}
        )
        assert res_pipe.status_code == 200
        pipe_data = res_pipe.json()
        assert pipe_data.get("success") is True
        assert pipe_data.get("processed_count") >= 1


# -----------------------------------------------------------------------------
# 8. Tests for Inventory & Price Sync Worker (InventorySyncWorker)
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_inventory_sync_worker_price_and_stockout(mock_shopify_client, monkeypatch):
    from agents.inventory_sync_worker import InventorySyncWorker

    worker = InventorySyncWorker(shopify_client=mock_shopify_client, dry_run=True)

    test_products = [
        {
            "optimized_seo_title": "Cinturón de Nylon para Halterofilia Pro",
            "shopify_product_id": "gid://shopify/Product/9794800484581",
            "suggested_price": 20.00,  # Old price
            "sku": "DS-CINTURON-01",
        }
    ]

    # Mock supplier query returning updated cost (12.00 -> 30.00 expected price)
    async def mock_supplier_stock(sku):
        return {
            "sku": sku,
            "available_stock": 85,
            "wholesale_cost": 12.00,
            "status": "IN_STOCK",
        }

    monkeypatch.setattr(worker, "fetch_supplier_stock_and_cost", mock_supplier_stock)

    report = await worker.sync_inventory_and_pricing(test_products)

    assert report.status == "SUCCESS"
    assert report.items_scanned == 1
    assert report.price_adjustments == 1
    assert report.changes[0].new_price == 30.00  # 12.00 * 2.5


@pytest.mark.asyncio
async def test_inventory_sync_api_endpoints(dashboard_auth):
    from httpx import ASGITransport, AsyncClient
    from api.webhook_listener import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test", headers=dashboard_auth) as ac:
        # Test POST /api/inventory/sync
        res_sync = await ac.post("/api/inventory/sync")
        assert res_sync.status_code == 200
        sync_data = res_sync.json()
        assert sync_data.get("success") is True
        assert "report" in sync_data

        # Test GET /api/inventory/sync/history
        res_hist = await ac.get("/api/inventory/sync/history")
        assert res_hist.status_code == 200
        assert isinstance(res_hist.json(), list)
