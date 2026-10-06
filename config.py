"""
Configuration management for the Shopify Multi-Agent Pipeline.
"""

import os
from pathlib import Path
from typing import List
from dotenv import load_dotenv

# Load environment variables from .env file
BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")


class ShopifyConfig:
    CLIENT_ID: str = os.getenv("SHOPIFY_CLIENT_ID", "")
    CLIENT_SECRET: str = os.getenv("SHOPIFY_CLIENT_SECRET", "")
    SHOP_DOMAIN: str = os.getenv("SHOPIFY_SHOP_DOMAIN", "da6gne-6x.myshopify.com")
    API_VERSION: str = os.getenv("SHOPIFY_API_VERSION", "2024-10")
    ACCESS_TOKEN: str = os.getenv("SHOPIFY_ACCESS_TOKEN", "")
    DASHBOARD_API_TOKEN: str = os.getenv("DASHBOARD_API_TOKEN", "")
    REDIRECT_URI: str = os.getenv("SHOPIFY_REDIRECT_URI", "https://localhost:8000/auth/callback")
    DRY_RUN: bool = os.getenv("DRY_RUN", "false").lower() in ("true", "1", "yes")

    # Dropshipping Supplier API Configuration
    SUPPLIER_API_KEY: str = os.getenv("SUPPLIER_API_KEY", "")
    SUPPLIER_API_BASE_URL: str = os.getenv("SUPPLIER_API_BASE_URL", "https://developers.cjdropshipping.com/api2.0/v1")
    SUPPLIER_NAME: str = os.getenv("SUPPLIER_NAME", "CJ_DROPSHIPPING")
    # Off by default: when the store is connected through CJ's own Shopify app, CJ already imports
    # every order, and creating it here too would ship it twice.
    SUPPLIER_AUTO_ORDER: bool = os.getenv("SUPPLIER_AUTO_ORDER", "false").lower() in ("true", "1", "yes")
    SUPPLIER_LOGISTIC_NAME: str = os.getenv("SUPPLIER_LOGISTIC_NAME", "CJPacket Ordinary")
    SUPPLIER_FROM_COUNTRY: str = os.getenv("SUPPLIER_FROM_COUNTRY", "CN")
    # CJ createOrderV2 payType: 2 = pay from CJ wallet balance, 3 = create the order only (pay it in CJ)
    SUPPLIER_PAY_TYPE: int = int(os.getenv("SUPPLIER_PAY_TYPE", "3"))
    # Inventory sync only reports suggested prices unless this is on (cost x 2.5 ignores shipping)
    SYNC_AUTO_REPRICE: bool = os.getenv("SYNC_AUTO_REPRICE", "false").lower() in ("true", "1", "yes")

    # LLM AI Copywriting Configuration (Gemini / OpenAI)
    LLM_API_KEY: str = os.getenv("LLM_API_KEY", "")
    LLM_MODEL: str = os.getenv("LLM_MODEL", "gemini-1.5-flash")
    LLM_PROVIDER: str = os.getenv("LLM_PROVIDER", "gemini")

    # Image Generation AI Configuration (DALL-E 3, Pollinations, Stability)
    IMAGE_GEN_API_KEY: str = os.getenv("IMAGE_GEN_API_KEY", "")
    IMAGE_GEN_PROVIDER: str = os.getenv("IMAGE_GEN_PROVIDER", "pollinations")
    IMAGE_GEN_MODEL: str = os.getenv("IMAGE_GEN_MODEL", "dall-e-3")

    # Required OAuth scopes
    SCOPES: List[str] = [
        "read_products",
        "write_products",
        "read_themes",
        "write_themes",
        "read_files",
        "write_files",
        "read_inventory",
        "write_inventory",
    ]

    @classmethod
    def get_scopes_string(cls) -> str:
        return ",".join(cls.SCOPES)

    @classmethod
    def get_graphql_url(cls) -> str:
        domain = cls.SHOP_DOMAIN.replace("https://", "").replace("http://", "").strip("/")
        return f"https://{domain}/admin/api/{cls.API_VERSION}/graphql.json"

    @classmethod
    def get_rest_url(cls, endpoint: str) -> str:
        domain = cls.SHOP_DOMAIN.replace("https://", "").replace("http://", "").strip("/")
        clean_endpoint = endpoint.lstrip("/")
        return f"https://{domain}/admin/api/{cls.API_VERSION}/{clean_endpoint}"


config = ShopifyConfig()
