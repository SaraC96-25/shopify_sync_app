
import os
import time
import requests

class ShopifyClient:
    def __init__(self, shop_domain=None, access_token=None, api_version=None):
        self.shop_domain = shop_domain or os.getenv("SHOPIFY_SHOP_DOMAIN")
        self.access_token = access_token or os.getenv("SHOPIFY_ACCESS_TOKEN")
        self.api_version = api_version or os.getenv("SHOPIFY_API_VERSION", "2024-10")
        if not self.shop_domain or not self.access_token:
            raise ValueError("Configurare SHOPIFY_SHOP_DOMAIN e SHOPIFY_ACCESS_TOKEN nelle secrets/env.")

        self.base = f"https://{self.shop_domain}/admin/api/{self.api_version}"
        self.headers = {
            "X-Shopify-Access-Token": self.access_token,
            "Content-Type": "application/json",
        }

    # --- helpers ---
    def _get(self, path, params=None):
        r = requests.get(self.base + path, headers=self.headers, params=params, timeout=30)
        r.raise_for_status()
        return r.json()

    def _post(self, path, payload):
        r = requests.post(self.base + path, headers=self.headers, json=payload, timeout=60)
        r.raise_for_status()
        return r.json()

    def _put(self, path, payload):
        r = requests.put(self.base + path, headers=self.headers, json=payload, timeout=60)
        r.raise_for_status()
        return r.json()

    # --- core methods ---
    def find_product_by_title(self, title):
        # NB: Shopify search is limited; we filter client-side.
        res = self._get("/products.json", params={"title": title, "limit": 50})
        for p in res.get("products", []):
            if p.get("title") == title:
                return p
        return None

    def create_or_update_product(self, title, body_html="", vendor="", product_type="", tags=None, options=None):
        """Ensure product exists with given options. Returns product dict."""
        existing = self.find_product_by_title(title)
        if existing:
            prod_id = existing["id"]
            # Update options if necessary
            payload = {"product": {"id": prod_id}}
            if body_html:
                payload["product"]["body_html"] = body_html
            if vendor:
                payload["product"]["vendor"] = vendor
            if product_type:
                payload["product"]["product_type"] = product_type
            if tags is not None:
                payload["product"]["tags"] = ", ".join(tags) if isinstance(tags, (list, tuple)) else str(tags)
            if options:
                payload["product"]["options"] = [{"name": o} if isinstance(o, str) else o for o in options]
            return self._put(f"/products/{prod_id}.json", payload)["product"]
        else:
            payload = {"product": {
                "title": title,
                "body_html": body_html,
                "vendor": vendor,
                "product_type": product_type,
                "tags": ", ".join(tags) if isinstance(tags, (list, tuple)) else (tags or ""),
                "options": [{"name": o} if isinstance(o, str) else o for o in (options or [])],
            }}
            return self._post("/products.json", payload)["product"]

    def list_variants(self, product_id):
        res = self._get(f"/products/{product_id}/variants.json", params={"limit": 250})
        return res.get("variants", [])

    def create_variant(self, product_id, price, option1, option2, sku=None, inventory_management="shopify"):
        payload = {"variant": {
            "price": str(price),
            "option1": str(option1),
            "option2": str(option2),
            "sku": sku,
            "inventory_management": inventory_management,
        }}
        return self._post(f"/products/{product_id}/variants.json", payload)["variant"]

    def update_variant_price(self, variant_id, price):
        payload = {"variant": {"id": variant_id, "price": str(price)}}
        return self._put(f"/variants/{variant_id}.json", payload)["variant"]
