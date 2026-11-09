from concurrent.futures import ThreadPoolExecutor
import pytest
from fastapi.testclient import TestClient
from app.main import create_app


@pytest.fixture
def client(tmp_path):
    app = create_app(db_path=str(tmp_path / "inventory.sqlite3"), api_key="test-secret")
    with TestClient(app) as client:
        client.headers.update({"X-API-Key": "test-secret"})
        yield client


@pytest.fixture
def setup(client):
    p = client.post("/api/products", json={
        "sku": "CUP-001", "name": "Reusable Cup", "price_cents": 1250, "reorder_point": 3,
    }).json()
    a = client.post("/api/warehouses", json={"code": "MAIN", "name": "Main warehouse"}).json()
    b = client.post("/api/warehouses", json={"code": "WEST", "name": "West warehouse"}).json()
    return p, a, b


def restock(client, p, w, qty=10):
    return client.post("/api/stock/adjustments", json={
        "product_id": p["id"], "warehouse_id": w["id"],
        "delta": qty, "reason": "Opening stock",
    })


def create_order(client, p, w, qty=1, key=None):
    data = {"customer_name": "Jane", "lines": [{
        "product_id": p["id"], "warehouse_id": w["id"], "quantity": qty,
    }]}
    if key: data["idempotency_key"] = key
    return client.post("/api/orders", json=data)
