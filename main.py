import os
import xmlrpc.client
import requests
from fastapi import FastAPI, Body

app = FastAPI()

# =========================
# VARIABLES DE ENTORNO
# =========================

ODOO_URL = os.getenv("ODOO_URL")
ODOO_DB = os.getenv("ODOO_DB")
ODOO_USER = os.getenv("ODOO_USER")
ODOO_PASSWORD = os.getenv("ODOO_PASSWORD")

WOO_URL = os.getenv("WOO_URL")
WOO_CONSUMER_KEY = os.getenv("WOO_CONSUMER_KEY")
WOO_CONSUMER_SECRET = os.getenv("WOO_CONSUMER_SECRET")


@app.get("/")
def home():
    return {
        "status": "ok",
        "service": "targo_odoo"
    }


# =========================
# FUNCIONES AUXILIARES
# =========================

def get_odoo_connection():
    common = xmlrpc.client.ServerProxy(f"{ODOO_URL}/xmlrpc/2/common")

    uid = common.authenticate(
        ODOO_DB,
        ODOO_USER,
        ODOO_PASSWORD,
        {}
    )

    if not uid:
        return None, None

    models = xmlrpc.client.ServerProxy(f"{ODOO_URL}/xmlrpc/2/object")
    return uid, models


def extract_size_from_woo_item(item):
    meta_data = item.get("meta_data", [])

    for meta in meta_data:
        key = str(meta.get("key", "")).lower()
        value = str(meta.get("value", "")).strip()

        if not value:
            continue

        if (
            "talla" in key
            or "size" in key
            or "pa_talla" in key
            or "attribute" in key
        ):
            return value.upper()

    return ""


def find_correct_variant(models, uid, product_template_id, size):
    variants = models.execute_kw(
        ODOO_DB,
        uid,
        ODOO_PASSWORD,
        "product.product",
        "search_read",
        [[["product_tmpl_id", "=", product_template_id]]],
        {
            "fields": ["id", "display_name"],
            "limit": 100
        }
    )

    print("Variantes disponibles:", variants)
    print("Talla buscada:", size)

    if not variants:
        return False

    if size:
        for variant in variants:
            display_name = str(variant.get("display_name", "")).upper()

            if (
                f" {size}" in display_name
                or f"/ {size}" in display_name
                or f"({size})" in display_name
                or f"- {size}" in display_name
                or display_name.endswith(size)
            ):
                return variant["id"]

    return variants[0]["id"]


# =========================
# TEST ODOO
# =========================

@app.get("/test-odoo")
def test_odoo():
    uid, models = get_odoo_connection()

    if not uid:
        return {
            "ok": False,
            "message": "No se pudo autenticar con Odoo"
        }

    return {
        "ok": True,
        "uid": uid,
        "message": "Conexión correcta con Odoo"
    }


# =========================
# TEST WOOCOMMERCE
# =========================

@app.get("/test-woocommerce")
def test_woocommerce():

    if not WOO_URL or not WOO_CONSUMER_KEY or not WOO_CONSUMER_SECRET:
        return {
            "ok": False,
            "error": "Faltan variables de entorno de WooCommerce en Railway"
        }

    url = f"{WOO_URL}/wp-json/wc/v3/products"

    response = requests.get(
        url,
        auth=(WOO_CONSUMER_KEY, WOO_CONSUMER_SECRET),
        params={"per_page": 5}
    )

    try:
        response_data = response.json()
    except Exception:
        response_data = response.text

    return {
        "ok": response.status_code == 200,
        "status_code": response.status_code,
        "response": response_data
    }


# =========================
# TEST VARIANTES TARGO EN ODOO
# =========================

@app.get("/test-odoo-variants-targo")
def test_odoo_variants_targo():

    uid, models = get_odoo_connection()

    if not uid:
        return {
            "ok": False,
            "error": "No se pudo autenticar con Odoo"
        }

    website_ids = models.execute_kw(
        ODOO_DB,
        uid,
        ODOO_PASSWORD,
        "website",
        "search",
        [[["name", "ilike", "Targo"]]],
        {"limit": 1}
    )

    if not website_ids:
        return {
            "ok": False,
            "error": "No se encontró el website Targo en Odoo"
        }

    website_id = website_ids[0]

    variants = models.execute_kw(
        ODOO_DB,
        uid,
        ODOO_PASSWORD,
        "product.product",
        "search_read",
        [[
            ["default_code", "!=", False],
            ["product_tmpl_id.website_id", "=", website_id]
        ]],
        {
            "fields": [
                "id",
                "display_name",
                "default_code",
                "qty_available",
                "virtual_available",
                "product_tmpl_id"
            ],
            "limit": 100
        }
    )

    return {
        "ok": True,
        "website_id": website_id,
        "count": len(variants),
        "variants": variants
    }

@app.get("/test-odoo-products-targo")
def test_odoo_products_targo():

    uid, models = get_odoo_connection()

    if not uid:
        return {
            "ok": False,
            "error": "No se pudo autenticar con Odoo"
        }

    website_ids = models.execute_kw(
        ODOO_DB,
        uid,
        ODOO_PASSWORD,
        "website",
        "search",
        [[["name", "ilike", "Targo"]]],
        {"limit": 1}
    )

    if not website_ids:
        return {
            "ok": False,
            "error": "No se encontró website Targo en Odoo"
        }

    website_id = website_ids[0]

    products = models.execute_kw(
        ODOO_DB,
        uid,
        ODOO_PASSWORD,
        "product.template",
        "search_read",
        [[["website_id", "=", website_id]]],
        {
            "fields": [
                "id",
                "name",
                "list_price",
                "website_id",
                "description_sale",
                "product_variant_ids"
            ],
            "limit": 50
        }
    )

    return {
        "ok": True,
        "website_id": website_id,
        "count": len(products),
        "products": products
    }


# =========================
# CREAR ORDEN DESDE WOOCOMMERCE
# =========================

@app.post("/create-order")
async def create_order(data: dict = Body(...)):

    print("========== PEDIDO RECIBIDO ==========")
    print(data)

    billing = data.get("billing", {})
    line_items = data.get("line_items", [])

    customer_name = f"{billing.get('first_name', '')} {billing.get('last_name', '')}".strip()
    phone = billing.get("phone", "")
    email = billing.get("email", "")
    order_number = str(data.get("id"))

    if not customer_name:
        customer_name = "Cliente WooCommerce"

    uid, models = get_odoo_connection()

    if not uid:
        return {
            "ok": False,
            "error": "No se pudo autenticar con Odoo"
        }

    # =========================
    # EVITAR ÓRDENES DUPLICADAS
    # =========================

    existing_order_ids = models.execute_kw(
        ODOO_DB,
        uid,
        ODOO_PASSWORD,
        "sale.order",
        "search",
        [[["client_order_ref", "=", order_number]]],
        {"limit": 1}
    )

    if existing_order_ids:
        return {
            "ok": True,
            "message": "La orden ya existía en Odoo, no se duplicó",
            "order_id": existing_order_ids[0]
        }

    # =========================
    # 1. BUSCAR O CREAR CLIENTE
    # =========================

    partner_id = False

    if email:
        partner_ids = models.execute_kw(
            ODOO_DB,
            uid,
            ODOO_PASSWORD,
            "res.partner",
            "search",
            [[["email", "=", email]]],
            {"limit": 1}
        )

        if partner_ids:
            partner_id = partner_ids[0]

    if not partner_id:
        partner_id = models.execute_kw(
            ODOO_DB,
            uid,
            ODOO_PASSWORD,
            "res.partner",
            "create",
            [{
                "name": customer_name,
                "email": email,
                "phone": phone
            }]
        )

    # =========================
    # 2. BUSCAR WEBSITE TARGO
    # =========================

    website_ids = models.execute_kw(
        ODOO_DB,
        uid,
        ODOO_PASSWORD,
        "website",
        "search",
        [[["name", "ilike", "Targo"]]],
        {"limit": 1}
    )

    website_id = website_ids[0] if website_ids else False

    # =========================
    # 3. CREAR SALE ORDER
    # =========================

    order_vals = {
        "partner_id": partner_id,
        "client_order_ref": order_number
    }

    if website_id:
        order_vals["website_id"] = website_id

    order_id = models.execute_kw(
        ODOO_DB,
        uid,
        ODOO_PASSWORD,
        "sale.order",
        "create",
        [order_vals]
    )

    created_lines = []
    missing_products = []

    # =========================
    # 4. AGREGAR PRODUCTOS
    # =========================

    for item in line_items:

        product_name = item.get("name", "").strip()
        quantity = item.get("quantity", 1)

        try:
            price = float(item.get("price", 0))
        except Exception:
            price = 0

        size = extract_size_from_woo_item(item)

        print("========== PRODUCTO WOO ==========")
        print("Nombre Woo:", product_name)
        print("Cantidad:", quantity)
        print("Precio:", price)
        print("Talla Woo:", size)
        print("Meta data Woo:", item.get("meta_data", []))

        base_product_name = product_name.split(" - ")[0].strip()

        template_ids = models.execute_kw(
            ODOO_DB,
            uid,
            ODOO_PASSWORD,
            "product.template",
            "search",
            [[["name", "ilike", base_product_name]]],
            {"limit": 1}
        )

        if not template_ids:
            missing_products.append({
                "product_name": product_name,
                "reason": "No se encontró product.template"
            })
            continue

        product_template_id = template_ids[0]

        product_id = find_correct_variant(
            models,
            uid,
            product_template_id,
            size
        )

        if not product_id:
            missing_products.append({
                "product_name": product_name,
                "reason": "No se encontró variante product.product"
            })
            continue

        line_id = models.execute_kw(
            ODOO_DB,
            uid,
            ODOO_PASSWORD,
            "sale.order.line",
            "create",
            [{
                "order_id": order_id,
                "product_id": product_id,
                "product_uom_qty": quantity,
                "price_unit": price
            }]
        )

        created_lines.append({
            "product_name": product_name,
            "base_product_name": base_product_name,
            "size": size,
            "product_id": product_id,
            "line_id": line_id,
            "quantity": quantity,
            "price": price
        })

    # =========================
    # 5. CONFIRMAR ORDEN EN ODOO
    # =========================

    confirmed = False
    confirm_error = None

    if created_lines:
        try:
            models.execute_kw(
                ODOO_DB,
                uid,
                ODOO_PASSWORD,
                "sale.order",
                "action_confirm",
                [[order_id]]
            )
            confirmed = True
        except Exception as e:
            confirm_error = str(e)

    return {
        "ok": True,
        "order_id": order_id,
        "customer": customer_name,
        "created_lines": created_lines,
        "missing_products": missing_products,
        "confirmed": confirmed,
        "confirm_error": confirm_error
    }


# =========================
# TEST VARIANTES DE WOOCOMMERCE
# =========================

@app.get("/test-woocommerce-variations")
def test_woocommerce_variations():

    product_id = 3298

    url = f"{WOO_URL}/wp-json/wc/v3/products/{product_id}/variations"

    response = requests.get(
        url,
        auth=(WOO_CONSUMER_KEY, WOO_CONSUMER_SECRET),
        params={"per_page": 100}
    )

    try:
        response_data = response.json()
    except Exception:
        response_data = response.text

    return {
        "ok": response.status_code == 200,
        "status_code": response.status_code,
        "product_id": product_id,
        "variations": response_data
    }


# =========================
# SINCRONIZAR STOCK ODOO → WOOCOMMERCE
# =========================

@app.get("/sync-stock-from-odoo")
def sync_stock_from_odoo():

    uid, models = get_odoo_connection()

    if not uid:
        return {
            "ok": False,
            "error": "No se pudo autenticar con Odoo"
        }

    if not WOO_URL or not WOO_CONSUMER_KEY or not WOO_CONSUMER_SECRET:
        return {
            "ok": False,
            "error": "Faltan variables de WooCommerce"
        }

    website_ids = models.execute_kw(
        ODOO_DB,
        uid,
        ODOO_PASSWORD,
        "website",
        "search",
        [[["name", "ilike", "Targo"]]],
        {"limit": 1}
    )

    if not website_ids:
        return {
            "ok": False,
            "error": "No se encontró website Targo en Odoo"
        }

    website_id = website_ids[0]

    odoo_variants = models.execute_kw(
        ODOO_DB,
        uid,
        ODOO_PASSWORD,
        "product.product",
        "search_read",
        [[
            ["default_code", "!=", False],
            ["product_tmpl_id.website_id", "=", website_id]
        ]],
        {
            "fields": [
                "id",
                "display_name",
                "default_code",
                "qty_available",
                "virtual_available",
                "product_tmpl_id"
            ],
            "limit": 500
        }
    )

    woo_sku_map = {}
    page = 1

    while True:

        products_url = f"{WOO_URL}/wp-json/wc/v3/products"

        products_response = requests.get(
            products_url,
            auth=(WOO_CONSUMER_KEY, WOO_CONSUMER_SECRET),
            params={
                "per_page": 100,
                "page": page,
                "type": "variable"
            }
        )

        if products_response.status_code != 200:
            return {
                "ok": False,
                "error": "Error leyendo productos de WooCommerce",
                "status_code": products_response.status_code,
                "response": products_response.text
            }

        products = products_response.json()

        if not products:
            break

        for product in products:

            product_id = product.get("id")
            product_name = product.get("name")

            variations_url = f"{WOO_URL}/wp-json/wc/v3/products/{product_id}/variations"

            variations_response = requests.get(
                variations_url,
                auth=(WOO_CONSUMER_KEY, WOO_CONSUMER_SECRET),
                params={"per_page": 100}
            )

            if variations_response.status_code != 200:
                continue

            variations = variations_response.json()

            for variation in variations:

                sku = str(variation.get("sku", "")).strip()

                if not sku:
                    continue

                woo_sku_map[sku] = {
                    "product_id": product_id,
                    "product_name": product_name,
                    "variation_id": variation.get("id"),
                    "variation_name": variation.get("name"),
                    "current_stock": variation.get("stock_quantity")
                }

        page += 1

    updated = []
    not_found_in_woo = []
    errors = []

    for variant in odoo_variants:

        sku = str(variant.get("default_code", "")).strip()

        if not sku:
            continue

        # Usamos virtual_available para reflejar stock disponible considerando pedidos confirmados/reservas.
        stock = variant.get("virtual_available", 0)

        try:
            stock = int(stock)
        except Exception:
            stock = 0

        if stock < 0:
            stock = 0

        woo_match = woo_sku_map.get(sku)

        if not woo_match:
            not_found_in_woo.append({
                "sku": sku,
                "odoo_product": variant.get("display_name"),
                "odoo_stock": stock
            })
            continue

        product_id = woo_match["product_id"]
        variation_id = woo_match["variation_id"]

        update_url = f"{WOO_URL}/wp-json/wc/v3/products/{product_id}/variations/{variation_id}"

        payload = {
            "manage_stock": True,
            "stock_quantity": stock,
            "stock_status": "instock" if stock > 0 else "outofstock"
        }

        update_response = requests.put(
            update_url,
            auth=(WOO_CONSUMER_KEY, WOO_CONSUMER_SECRET),
            json=payload
        )

        try:
            update_data = update_response.json()
        except Exception:
            update_data = update_response.text

        if update_response.status_code in [200, 201]:

            updated.append({
                "sku": sku,
                "odoo_product": variant.get("display_name"),
                "woo_product": woo_match.get("product_name"),
                "woo_variation": woo_match.get("variation_name"),
                "old_stock_woo": woo_match.get("current_stock"),
                "new_stock": stock,
                "product_id": product_id,
                "variation_id": variation_id
            })

        else:

            errors.append({
                "sku": sku,
                "product_id": product_id,
                "variation_id": variation_id,
                "status_code": update_response.status_code,
                "response": update_data
            })

    return {
        "ok": True,
        "website_id": website_id,
        "odoo_variants_count": len(odoo_variants),
        "woo_variations_with_sku_count": len(woo_sku_map),
        "updated_count": len(updated),
        "not_found_count": len(not_found_in_woo),
        "error_count": len(errors),
        "updated": updated,
        "not_found_in_woo": not_found_in_woo,
        "errors": errors
    }


# =========================
# SINCRONIZAR PRODUCTOS ODOO → WOOCOMMERCE
# Crea productos variables y variantes si no existen
# =========================

def extract_size_from_odoo_variant(display_name, sku):
    """
    Intenta obtener la talla desde el nombre de variante o desde el SKU.
    Ejemplos:
    - Jacket 10 / M
    - Jacket 10 (Talla: M)
    - JACKET10-M
    """

    display_name = str(display_name or "").strip()
    sku = str(sku or "").strip()

    possible_sizes = ["XXL", "XL", "XS", "S", "M", "L"]

    upper_name = display_name.upper()
    upper_sku = sku.upper()

    for size in possible_sizes:
        if f"/ {size}" in upper_name:
            return size
        if f": {size}" in upper_name:
            return size
        if f"({size})" in upper_name:
            return size
        if upper_name.endswith(f" {size}"):
            return size
        if upper_sku.endswith(f"-{size}"):
            return size

    return ""


@app.get("/sync-products-from-odoo")
def sync_products_from_odoo():

    uid, models = get_odoo_connection()

    if not uid:
        return {
            "ok": False,
            "error": "No se pudo autenticar con Odoo"
        }

    if not WOO_URL or not WOO_CONSUMER_KEY or not WOO_CONSUMER_SECRET:
        return {
            "ok": False,
            "error": "Faltan variables de WooCommerce"
        }

    # =========================
    # 1. BUSCAR WEBSITE TARGO
    # =========================

    website_ids = models.execute_kw(
        ODOO_DB,
        uid,
        ODOO_PASSWORD,
        "website",
        "search",
        [[["name", "ilike", "Targo"]]],
        {"limit": 1}
    )

    if not website_ids:
        return {
            "ok": False,
            "error": "No se encontró website Targo en Odoo"
        }

    website_id = website_ids[0]

    # =========================
    # 2. LEER PRODUCTOS PADRE DE ODOO
    # =========================

    odoo_products = models.execute_kw(
        ODOO_DB,
        uid,
        ODOO_PASSWORD,
        "product.template",
        "search_read",
        [[["website_id", "=", website_id]]],
        {
            "fields": [
                "id",
                "name",
                "list_price",
                "description_sale",
                "product_variant_ids"
            ],
            "limit": 200
        }
    )

    # =========================
    # 3. LEER PRODUCTOS EXISTENTES EN WOOCOMMERCE
    # =========================

    woo_products_by_name = {}
    woo_variations_by_sku = {}

    page = 1

    while True:

        products_response = requests.get(
            f"{WOO_URL}/wp-json/wc/v3/products",
            auth=(WOO_CONSUMER_KEY, WOO_CONSUMER_SECRET),
            params={
                "per_page": 100,
                "page": page
            }
        )

        if products_response.status_code != 200:
            return {
                "ok": False,
                "error": "Error leyendo productos de WooCommerce",
                "status_code": products_response.status_code,
                "response": products_response.text
            }

        woo_products = products_response.json()

        if not woo_products:
            break

        for woo_product in woo_products:

            woo_product_id = woo_product.get("id")
            woo_product_name = str(woo_product.get("name", "")).strip()

            if woo_product_name:
                woo_products_by_name[woo_product_name.lower()] = woo_product

            if woo_product.get("type") == "variable":

                variations_response = requests.get(
                    f"{WOO_URL}/wp-json/wc/v3/products/{woo_product_id}/variations",
                    auth=(WOO_CONSUMER_KEY, WOO_CONSUMER_SECRET),
                    params={"per_page": 100}
                )

                if variations_response.status_code == 200:

                    variations = variations_response.json()

                    for variation in variations:
                        sku = str(variation.get("sku", "")).strip()

                        if sku:
                            woo_variations_by_sku[sku] = {
                                "product_id": woo_product_id,
                                "variation_id": variation.get("id"),
                                "variation": variation
                            }

        page += 1

    # =========================
    # 4. CREAR / ACTUALIZAR PRODUCTOS
    # =========================

    created_products = []
    updated_products = []
    created_variations = []
    updated_variations = []
    skipped_variations = []
    errors = []

    for product in odoo_products:

        template_id = product.get("id")
        product_name = str(product.get("name", "")).strip()
        list_price = product.get("list_price", 0) or 0
        description = product.get("description_sale") or ""
        variant_ids = product.get("product_variant_ids", [])

        if not product_name or not variant_ids:
            continue

        # Leer variantes de ese producto en Odoo
        odoo_variants = models.execute_kw(
            ODOO_DB,
            uid,
            ODOO_PASSWORD,
            "product.product",
            "search_read",
            [[["id", "in", variant_ids]]],
            {
                "fields": [
                    "id",
                    "display_name",
                    "default_code",
                    "virtual_available",
                    "qty_available"
                ],
                "limit": 100
            }
        )

        sizes = []

        for variant in odoo_variants:
            sku = str(variant.get("default_code", "")).strip()
            display_name = str(variant.get("display_name", "")).strip()
            size = extract_size_from_odoo_variant(display_name, sku)

            if sku and size and size not in sizes:
                sizes.append(size)

        if not sizes:
            skipped_variations.append({
                "product": product_name,
                "reason": "No se encontraron tallas válidas en variantes de Odoo"
            })
            continue

        # Orden recomendado de tallas
        size_order = ["XS", "S", "M", "L", "XL", "XXL"]
        sizes = sorted(sizes, key=lambda x: size_order.index(x) if x in size_order else 999)

        # Buscar producto padre en Woo por nombre
        woo_product = woo_products_by_name.get(product_name.lower())

        if woo_product:
            woo_product_id = woo_product.get("id")

            # Actualizar producto padre
            update_product_payload = {
                "name": product_name,
                "type": "variable",
                "description": description,
                "short_description": description,
                "attributes": [
                    {
                        "name": "Talla",
                        "visible": True,
                        "variation": True,
                        "options": sizes
                    }
                ]
            }

            update_product_response = requests.put(
                f"{WOO_URL}/wp-json/wc/v3/products/{woo_product_id}",
                auth=(WOO_CONSUMER_KEY, WOO_CONSUMER_SECRET),
                json=update_product_payload
            )

            if update_product_response.status_code in [200, 201]:
                updated_products.append({
                    "product_name": product_name,
                    "woo_product_id": woo_product_id
                })
            else:
                errors.append({
                    "product_name": product_name,
                    "step": "update_parent_product",
                    "status_code": update_product_response.status_code,
                    "response": update_product_response.text
                })
                continue

        else:
            # Crear producto padre variable en Woo
            create_product_payload = {
                "name": product_name,
                "type": "variable",
                "status": "publish",
                "description": description,
                "short_description": description,
                "attributes": [
                    {
                        "name": "Talla",
                        "visible": True,
                        "variation": True,
                        "options": sizes
                    }
                ]
            }

            create_product_response = requests.post(
                f"{WOO_URL}/wp-json/wc/v3/products",
                auth=(WOO_CONSUMER_KEY, WOO_CONSUMER_SECRET),
                json=create_product_payload
            )

            try:
                create_product_data = create_product_response.json()
            except Exception:
                create_product_data = create_product_response.text

            if create_product_response.status_code not in [200, 201]:
                errors.append({
                    "product_name": product_name,
                    "step": "create_parent_product",
                    "status_code": create_product_response.status_code,
                    "response": create_product_data
                })
                continue

            woo_product_id = create_product_data.get("id")

            created_products.append({
                "product_name": product_name,
                "woo_product_id": woo_product_id
            })

        # =========================
        # 5. CREAR / ACTUALIZAR VARIANTES
        # =========================

        for variant in odoo_variants:

            sku = str(variant.get("default_code", "")).strip()
            display_name = str(variant.get("display_name", "")).strip()
            size = extract_size_from_odoo_variant(display_name, sku)

            if not sku or not size:
                skipped_variations.append({
                    "product": product_name,
                    "variant": display_name,
                    "sku": sku,
                    "reason": "Variante sin SKU o sin talla detectada"
                })
                continue

            stock = variant.get("virtual_available", 0)

            try:
                stock = int(stock)
            except Exception:
                stock = 0

            if stock < 0:
                stock = 0

            variation_payload = {
                "regular_price": str(list_price),
                "sku": sku,
                "manage_stock": True,
                "stock_quantity": stock,
                "stock_status": "instock" if stock > 0 else "outofstock",
                "attributes": [
                    {
                        "name": "Talla",
                        "option": size
                    }
                ]
            }

            if sku in woo_variations_by_sku:

                existing = woo_variations_by_sku[sku]
                existing_product_id = existing["product_id"]
                existing_variation_id = existing["variation_id"]

                update_variation_response = requests.put(
                    f"{WOO_URL}/wp-json/wc/v3/products/{existing_product_id}/variations/{existing_variation_id}",
                    auth=(WOO_CONSUMER_KEY, WOO_CONSUMER_SECRET),
                    json=variation_payload
                )

                if update_variation_response.status_code in [200, 201]:
                    updated_variations.append({
                        "product_name": product_name,
                        "sku": sku,
                        "size": size,
                        "stock": stock,
                        "woo_product_id": existing_product_id,
                        "woo_variation_id": existing_variation_id
                    })
                else:
                    errors.append({
                        "product_name": product_name,
                        "sku": sku,
                        "step": "update_variation",
                        "status_code": update_variation_response.status_code,
                        "response": update_variation_response.text
                    })

            else:

                create_variation_response = requests.post(
                    f"{WOO_URL}/wp-json/wc/v3/products/{woo_product_id}/variations",
                    auth=(WOO_CONSUMER_KEY, WOO_CONSUMER_SECRET),
                    json=variation_payload
                )

                try:
                    create_variation_data = create_variation_response.json()
                except Exception:
                    create_variation_data = create_variation_response.text

                if create_variation_response.status_code in [200, 201]:
                    new_variation_id = create_variation_data.get("id")

                    woo_variations_by_sku[sku] = {
                        "product_id": woo_product_id,
                        "variation_id": new_variation_id,
                        "variation": create_variation_data
                    }

                    created_variations.append({
                        "product_name": product_name,
                        "sku": sku,
                        "size": size,
                        "stock": stock,
                        "woo_product_id": woo_product_id,
                        "woo_variation_id": new_variation_id
                    })
                else:
                    errors.append({
                        "product_name": product_name,
                        "sku": sku,
                        "step": "create_variation",
                        "status_code": create_variation_response.status_code,
                        "response": create_variation_data
                    })

    return {
        "ok": True,
        "website_id": website_id,
        "odoo_products_count": len(odoo_products),
        "created_products_count": len(created_products),
        "updated_products_count": len(updated_products),
        "created_variations_count": len(created_variations),
        "updated_variations_count": len(updated_variations),
        "skipped_variations_count": len(skipped_variations),
        "error_count": len(errors),
        "created_products": created_products,
        "updated_products": updated_products,
        "created_variations": created_variations,
        "updated_variations": updated_variations,
        "skipped_variations": skipped_variations,
        "errors": errors
    }


