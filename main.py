import os
import xmlrpc.client
from fastapi import FastAPI, Request, Body

app = FastAPI()

app = FastAPI()

ODOO_URL = os.getenv("ODOO_URL")
ODOO_DB = os.getenv("ODOO_DB")
ODOO_USER = os.getenv("ODOO_USER")
ODOO_PASSWORD = os.getenv("ODOO_PASSWORD")

@app.get("/")
def home():
    return {"status": "ok", "service": "targo_odoo"}

@app.get("/test-odoo")
def test_odoo():
    common = xmlrpc.client.ServerProxy(f"{ODOO_URL}/xmlrpc/2/common")
    uid = common.authenticate(ODOO_DB, ODOO_USER, ODOO_PASSWORD, {})

    if not uid:
        return {"ok": False, "message": "No se pudo autenticar con Odoo"}

    return {"ok": True, "uid": uid, "message": "Conexión correcta con Odoo"}


@app.post("/create-order")
async def create_order(data: dict = Body(...)):

    billing = data.get("billing", {})
    line_items = data.get("line_items", [])

    customer_name = f"{billing.get('first_name', '')} {billing.get('last_name', '')}"
    phone = billing.get("phone", "")
    email = billing.get("email", "")
    order_number = str(data.get("id"))

    # conexión odoo
    common = xmlrpc.client.ServerProxy(f"{ODOO_URL}/xmlrpc/2/common")
    uid = common.authenticate(ODOO_DB, ODOO_USER, ODOO_PASSWORD, {})
    models = xmlrpc.client.ServerProxy(f"{ODOO_URL}/xmlrpc/2/object")

    # crear cliente
    partner_ids = models.execute_kw(
        ODOO_DB, uid, ODOO_PASSWORD,
        'res.partner', 'search',
        [[['email', '=', email]]]
    )

    if partner_ids:
        partner_id = partner_ids[0]
    else:
        partner_id = models.execute_kw(
            ODOO_DB, uid, ODOO_PASSWORD,
            'res.partner', 'create',
            [[{
                'name': customer_name,
                'email': email,
                'phone': phone
            }]]
        )

    # website
    website_ids = models.execute_kw(
        ODOO_DB, uid, ODOO_PASSWORD,
        'website', 'search',
        [[['name', 'ilike', 'Targo']]],
        {'limit': 1}
    )

    website_id = website_ids[0] if website_ids else False

    # crear orden
    order_vals = {
        'partner_id': partner_id,
        'client_order_ref': order_number
    }

    if website_id:
        order_vals['website_id'] = website_id

    order_id = models.execute_kw(
        ODOO_DB, uid, ODOO_PASSWORD,
        'sale.order', 'create',
        [order_vals]
    )

    # =========================
    # AGREGAR PRODUCTOS
    # =========================

    for item in line_items:

        product_name = item.get("name")
        quantity = item.get("quantity", 1)
        price = float(item.get("price", 0))

        product_ids = models.execute_kw(
            ODOO_DB, uid, ODOO_PASSWORD,
            'product.product', 'search',
            [[['name', 'ilike', product_name]]],
            {'limit': 1}
        )

        if not product_ids:
            continue

        product_id = product_ids[0]

        models.execute_kw(
            ODOO_DB, uid, ODOO_PASSWORD,
            'sale.order.line', 'create',
            [[{
                'order_id': order_id,
                'product_id': product_id,
                'product_uom_qty': quantity,
                'price_unit': price
            }]]
        )

    return {
        "ok": True,
        "order_id": order_id
    }
