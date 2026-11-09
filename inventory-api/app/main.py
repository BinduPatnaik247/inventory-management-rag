"""FastAPI application for a practical single-business inventory starter."""
import hashlib
import json
import sqlite3
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.responses import FileResponse
from .config import settings
from .database import connect, initialize, to_dict, transaction
from .schemas import Adjustment, OrderCreate, ProductCreate, ProductUpdate, Transfer, WarehouseCreate

ROOT = Path(__file__).resolve().parent.parent


def fail_if_missing(db: sqlite3.Connection, table: str, row_id: int) -> None:
    if not db.execute(f"SELECT 1 FROM {table} WHERE id=?", (row_id,)).fetchone():
        raise HTTPException(status_code=404, detail=f"{table.rstrip('s').capitalize()} {row_id} not found")


def change_stock(db: sqlite3.Connection, product_id: int, warehouse_id: int,
                 delta: int, reason: str, reference: str | None = None) -> int:
    """Atomic balance update and append-only movement ledger inside a transaction."""
    fail_if_missing(db, "products", product_id)
    fail_if_missing(db, "warehouses", warehouse_id)
    db.execute("INSERT OR IGNORE INTO stock (product_id, warehouse_id, quantity) VALUES (?,?,0)",
               (product_id, warehouse_id))
    changed = db.execute(
        "UPDATE stock SET quantity=quantity+? WHERE product_id=? AND warehouse_id=? AND quantity+?>=0",
        (delta, product_id, warehouse_id, delta),
    )
    if changed.rowcount == 0:
        raise HTTPException(status_code=409, detail="Insufficient stock")
    db.execute("INSERT INTO stock_movements (product_id,warehouse_id,delta,reason,reference) VALUES (?,?,?,?,?)",
               (product_id, warehouse_id, delta, reason, reference))
    return db.execute("SELECT quantity FROM stock WHERE product_id=? AND warehouse_id=?",
                      (product_id, warehouse_id)).fetchone()["quantity"]


def order_details(db: sqlite3.Connection, order_id: int) -> dict:
    row = db.execute("SELECT * FROM orders WHERE id=?", (order_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Order not found")
    order = dict(row)
    order.pop("request_hash", None)
    order["lines"] = [dict(item) for item in db.execute("""
        SELECT oi.product_id, p.sku, oi.warehouse_id, oi.quantity, oi.unit_price_cents
        FROM order_items oi JOIN products p ON p.id=oi.product_id
        WHERE oi.order_id=? ORDER BY oi.id
    """, (order_id,)).fetchall()]
    return order


def create_app(db_path: str | None = None, api_key: str | None = None) -> FastAPI:
    env_key, env_db = settings()
    db_path = db_path if db_path is not None else env_db
    api_key = api_key if api_key is not None else env_key

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        initialize(db_path)
        yield

    app = FastAPI(title="Smart Inventory API", version="0.1.0", lifespan=lifespan,
                  description="Single-business inventory, warehouse stock and order fulfillment starter.")

    def auth(x_api_key: str | None = Header(default=None)) -> None:
        import hmac
        if not x_api_key or not hmac.compare_digest(x_api_key, api_key):
            raise HTTPException(status_code=401, detail="Invalid X-API-Key")

    @app.get("/health")
    def health():
        return {"status": "ok", "service": "smart-inventory"}

    @app.get("/", include_in_schema=False)
    def dashboard():
        return FileResponse(ROOT / "static" / "index.html")

    @app.get("/static/dashboard.js", include_in_schema=False)
    def dashboard_js():
        return FileResponse(ROOT / "static" / "dashboard.js", media_type="text/javascript")

    @app.post("/api/warehouses", dependencies=[Depends(auth)], status_code=201)
    def create_warehouse(request: WarehouseCreate):
        try:
            with transaction(db_path) as db:
                cursor = db.execute("INSERT INTO warehouses (code,name) VALUES (?,?)",
                                    (request.code.upper(), request.name.strip()))
                return to_dict(db.execute("SELECT * FROM warehouses WHERE id=?", (cursor.lastrowid,)).fetchone())
        except sqlite3.IntegrityError as exc:
            raise HTTPException(status_code=409, detail="Warehouse code already exists") from exc

    @app.get("/api/warehouses", dependencies=[Depends(auth)])
    def list_warehouses():
        db = connect(db_path)
        try:
            return [dict(x) for x in db.execute("SELECT * FROM warehouses ORDER BY id")]
        finally:
            db.close()

    @app.post("/api/products", dependencies=[Depends(auth)], status_code=201)
    def create_product(request: ProductCreate):
        try:
            with transaction(db_path) as db:
                cursor = db.execute("INSERT INTO products (sku,name,price_cents,reorder_point) VALUES (?,?,?,?)",
                                    (request.sku.upper(), request.name.strip(), request.price_cents, request.reorder_point))
                return to_dict(db.execute("SELECT * FROM products WHERE id=?", (cursor.lastrowid,)).fetchone())
        except sqlite3.IntegrityError as exc:
            raise HTTPException(status_code=409, detail="SKU already exists") from exc

    @app.get("/api/products", dependencies=[Depends(auth)])
    def list_products(search: str = Query(default="", max_length=100), include_inactive: bool = False):
        db = connect(db_path)
        try:
            term = "%" + search.replace("%", "\\%").replace("_", "\\_") + "%"
            return [dict(x) for x in db.execute("""
                SELECT * FROM products WHERE (? OR active=1)
                AND (sku LIKE ? ESCAPE '\\' OR name LIKE ? ESCAPE '\\') ORDER BY id
            """, (int(include_inactive), term, term))]
        finally:
            db.close()

    @app.get("/api/products/{product_id}", dependencies=[Depends(auth)])
    def get_product(product_id: int):
        db = connect(db_path)
        try:
            fail_if_missing(db, "products", product_id)
            return to_dict(db.execute("SELECT * FROM products WHERE id=?", (product_id,)).fetchone())
        finally:
            db.close()

    @app.patch("/api/products/{product_id}", dependencies=[Depends(auth)])
    def update_product(product_id: int, request: ProductUpdate):
        patch = request.model_dump(exclude_unset=True)
        if "active" in patch:
            patch["active"] = int(patch["active"])
        with transaction(db_path) as db:
            fail_if_missing(db, "products", product_id)
            columns = ", ".join(f"{key}=?" for key in patch)
            db.execute(f"UPDATE products SET {columns} WHERE id=?", (*patch.values(), product_id))
            return to_dict(db.execute("SELECT * FROM products WHERE id=?", (product_id,)).fetchone())

    @app.get("/api/stock", dependencies=[Depends(auth)])
    def stock(warehouse_id: int | None = Query(default=None, gt=0)):
        db = connect(db_path)
        try:
            return [dict(r) for r in db.execute("""
                SELECT s.product_id,p.sku,p.name,s.warehouse_id,w.code AS warehouse_code,s.quantity
                FROM stock s JOIN products p ON p.id=s.product_id
                JOIN warehouses w ON w.id=s.warehouse_id
                WHERE (? IS NULL OR s.warehouse_id=?) ORDER BY p.sku,w.code
            """, (warehouse_id, warehouse_id))]
        finally:
            db.close()

    @app.post("/api/stock/adjustments", dependencies=[Depends(auth)], status_code=201)
    def adjust_stock(request: Adjustment):
        with transaction(db_path) as db:
            quantity = change_stock(db, request.product_id, request.warehouse_id,
                                    request.delta, "adjustment: " + request.reason.strip())
            return {"product_id": request.product_id, "warehouse_id": request.warehouse_id,
                    "quantity": quantity, "delta": request.delta}

    @app.post("/api/stock/transfers", dependencies=[Depends(auth)], status_code=201)
    def transfer_stock(request: Transfer):
        with transaction(db_path) as db:
            ref = f"transfer:{request.product_id}:{request.from_warehouse_id}:{request.to_warehouse_id}"
            source_left = change_stock(db, request.product_id, request.from_warehouse_id,
                                       -request.quantity, "transfer_out", ref)
            dest_left = change_stock(db, request.product_id, request.to_warehouse_id,
                                     request.quantity, "transfer_in", ref)
            return {"source_quantity": source_left, "destination_quantity": dest_left,
                    "transferred": request.quantity}

    @app.get("/api/stock/movements", dependencies=[Depends(auth)])
    def movement_history(product_id: int | None = Query(default=None, gt=0),
                         limit: int = Query(default=100, ge=1, le=500)):
        db = connect(db_path)
        try:
            return [dict(r) for r in db.execute("""
                SELECT * FROM stock_movements WHERE (? IS NULL OR product_id=?)
                ORDER BY id DESC LIMIT ?
            """, (product_id, product_id, limit))]
        finally:
            db.close()

    @app.post("/api/orders", dependencies=[Depends(auth)], status_code=201)
    def create_order(request: OrderCreate):
        normalized = request.model_dump(exclude={"idempotency_key"})
        # Ignore line order for idempotent retries, while retaining duplicate lines as distinct.
        normalized["lines"] = sorted(normalized["lines"],
                                     key=lambda r: (r["product_id"], r["warehouse_id"], r["quantity"]))
        signature = hashlib.sha256(json.dumps(normalized, sort_keys=True).encode()).hexdigest()
        with transaction(db_path) as db:
            if request.idempotency_key:
                previous = db.execute("SELECT id,request_hash FROM orders WHERE idempotency_key=?",
                                      (request.idempotency_key,)).fetchone()
                if previous:
                    if previous["request_hash"] != signature:
                        raise HTTPException(status_code=409, detail="Idempotency key reused with different order")
                    return order_details(db, previous["id"])

            total = 0
            entries = []
            for line in request.lines:
                product = db.execute("SELECT * FROM products WHERE id=?", (line.product_id,)).fetchone()
                if not product:
                    raise HTTPException(status_code=404, detail=f"Product {line.product_id} not found")
                if not product["active"]:
                    raise HTTPException(status_code=409, detail=f"Product {line.product_id} is inactive")
                # All changes are made in one BEGIN IMMEDIATE transaction.
                change_stock(db, line.product_id, line.warehouse_id,
                             -line.quantity, "order_fulfilled", request.idempotency_key)
                total += product["price_cents"] * line.quantity
                entries.append((line.product_id, line.warehouse_id, line.quantity, product["price_cents"]))
            cursor = db.execute("""
                INSERT INTO orders (customer_name,total_cents,idempotency_key,request_hash)
                VALUES (?,?,?,?)
            """, (request.customer_name.strip(), total, request.idempotency_key, signature))
            for product_id, warehouse_id, qty, price in entries:
                db.execute("""
                    INSERT INTO order_items (order_id,product_id,warehouse_id,quantity,unit_price_cents)
                    VALUES (?,?,?,?,?)
                """, (cursor.lastrowid, product_id, warehouse_id, qty, price))
            # Bind the stock movement ledger to the final order ID (including without an idempotency key).
            db.execute("""
                UPDATE stock_movements SET reference=?
                WHERE id IN (SELECT id FROM stock_movements WHERE reason='order_fulfilled'
                    AND reference IS ? ORDER BY id DESC LIMIT ?)
            """, (f"order:{cursor.lastrowid}", request.idempotency_key, len(request.lines)))
            return order_details(db, cursor.lastrowid)

    @app.get("/api/orders", dependencies=[Depends(auth)])
    def list_orders(limit: int = Query(default=100, ge=1, le=500)):
        db = connect(db_path)
        try:
            return [dict(r) for r in db.execute("""
                SELECT id,customer_name,status,total_cents,idempotency_key,created_at
                FROM orders ORDER BY id DESC LIMIT ?
            """, (limit,))]
        finally:
            db.close()

    @app.get("/api/orders/{order_id}", dependencies=[Depends(auth)])
    def get_order(order_id: int):
        db = connect(db_path)
        try:
            return order_details(db, order_id)
        finally:
            db.close()

    @app.post("/api/orders/{order_id}/cancel", dependencies=[Depends(auth)])
    def cancel_order(order_id: int):
        with transaction(db_path) as db:
            order = order_details(db, order_id)
            if order["status"] == "cancelled":
                return order
            for line in order["lines"]:
                change_stock(db, line["product_id"], line["warehouse_id"],
                             line["quantity"], "order_cancelled", f"order:{order_id}")
            db.execute("UPDATE orders SET status='cancelled' WHERE id=?", (order_id,))
            return order_details(db, order_id)

    @app.get("/api/reports/low-stock", dependencies=[Depends(auth)])
    def low_stock(warehouse_id: int | None = Query(default=None, gt=0)):
        db = connect(db_path)
        try:
            return [dict(r) for r in db.execute("""
                SELECT p.id AS product_id,p.sku,p.name,p.reorder_point,w.id AS warehouse_id,
                       w.code AS warehouse_code,COALESCE(s.quantity,0) AS quantity
                FROM products p CROSS JOIN warehouses w
                LEFT JOIN stock s ON s.product_id=p.id AND s.warehouse_id=w.id
                WHERE p.active=1 AND COALESCE(s.quantity,0)<=p.reorder_point
                  AND (? IS NULL OR w.id=?)
                ORDER BY p.sku,w.code
            """, (warehouse_id, warehouse_id))]
        finally:
            db.close()

    @app.get("/api/reports/overview", dependencies=[Depends(auth)])
    def overview():
        db = connect(db_path)
        try:
            count = lambda sql: db.execute(sql).fetchone()[0]
            return {
                "products": count("SELECT COUNT(*) FROM products WHERE active=1"),
                "warehouses": count("SELECT COUNT(*) FROM warehouses"),
                "units_in_stock": count("SELECT COALESCE(SUM(quantity),0) FROM stock"),
                "orders_fulfilled": count("SELECT COUNT(*) FROM orders WHERE status='fulfilled'"),
                "revenue_cents": count("SELECT COALESCE(SUM(total_cents),0) FROM orders WHERE status='fulfilled'"),
                "low_stock_locations": count("""
                    SELECT COUNT(*) FROM products p CROSS JOIN warehouses w
                    LEFT JOIN stock s ON s.product_id=p.id AND s.warehouse_id=w.id
                    WHERE p.active=1 AND COALESCE(s.quantity,0)<=p.reorder_point
                """),
            }
        finally:
            db.close()

    return app


app = create_app()
