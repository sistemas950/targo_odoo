import os
import xmlrpc.client
from fastapi import FastAPI, Body

app = FastAPI()

ODOO_URL = os.getenv("ODOO_URL")
ODOO_DB = os.getenv("ODOO_DB")
ODOO_USER = os.getenv("ODOO_USER")
ODOO_PASSWORD = os.getenv("ODOO_PASSWORD")


@app.get("/")
def home():
    return {
        "status": "ok",
        "service": "targo_odoo"
    }


@app.get("/test-odoo")
def test_odoo():
    common = xmlrpc.client.ServerProxy(f"{ODOO_URL}/xmlrpc/2/common")

    uid = common.authenticate(
        ODOO_DB,
        ODOO_USER,
        ODOO_PASSWORD,
        {}
    )

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


def extract_size_from_woo_item(item):
    """
    Intenta extraer la talla desde WooCommerce.
    Woo puede mandar la talla en meta_data con keys como:
    - Talla
    - talla
    - Size
    - pa_talla
    - attribute_talla
    """

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
    """
    Busca la variante correcta dentro de product.product.
    Primero intenta buscar por talla dentro del display_name.
    Si no encuentra, regresa la primera variante como respaldo.
    """

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

            # Busca coincidencias tipo:
            # Jacket 10 (M)
            # Jacket 10 / M
            # Jacket 10 - M
            if (
                f" {size}" in display_name
                or f"/ {size}" in display_name
                or f"({size})" in display_name
                or f"- {size}" in display_name
                or display_name.endswith(size)
            ):
                return variant["id"]

    # Respaldo: si no encuentra la talla, toma la primera variante
    return variants[0]["id"]


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

    # =========================
    # CONEXIÓN ODOO
    # =========================

    common = xmlrpc.client.ServerProxy(f"{ODOO_URL}/xmlrpc/2/common")

    uid = common.authenticate(
        ODOO_DB,
        ODOO_USER,
        ODOO_PASSWORD,
        {}
    )

    if not uid:
        return {
            "ok": False,
            "error": "No se pudo autenticar con Odoo"
        }

    models = xmlrpc.client.ServerProxy(f"{ODOO_URL}/xmlrpc/2/object")

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
        except:
            price = 0

        size = extract_size_from_woo_item(item)

        print("========== PRODUCTO WOO ==========")
        print("Nombre Woo:", product_name)
        print("Cantidad:", quantity)
        print("Precio:", price)
        print("Talla Woo:", size)
        print("Meta data Woo:", item.get("meta_data", []))

        # Si Woo manda algo como:
        # "Jacket 10 - Azul - M"
        # nos quedamos con la primera parte:
        # "Jacket 10"
        base_product_name = product_name.split(" - ")[0].strip()

        print("Nombre base para buscar en Odoo:", base_product_name)

        # Buscar producto padre en Odoo
        template_ids = models.execute_kw(
            ODOO_DB,
            uid,
            ODOO_PASSWORD,
            "product.template",
            "search",
            [[["name", "ilike", base_product_name]]],
            {"limit": 1}
        )

        print("Templates encontrados:", template_ids)

        if not template_ids:
            missing_products.append({
                "product_name": product_name,
                "reason": "No se encontró product.template"
            })
            continue

        product_template_id = template_ids[0]

        # Buscar variante correcta por talla
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

        print("Producto variante elegido:", product_id)

        # Crear línea de venta
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

    return {
        "ok": True,
        "order_id": order_id,
        "customer": customer_name,
        "created_lines": created_lines,
        "missing_products": missing_products
    }
