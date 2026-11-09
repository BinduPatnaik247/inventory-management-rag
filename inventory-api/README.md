# Smart Inventory API + dashboard

A real, runnable **Python inventory management starter** for a small business. This is a **different use case** from the website-support chatbot project.

## Features

- Create products (SKU, name, price in integer cents, reorder threshold) and warehouses.
- Track inventory per product and warehouse with an **append-only stock movement log**.
- Adjust stock and transfer between warehouses using atomic SQLite transactions.
- Fulfill orders only when inventory is available. Reject overselling, including concurrent checkouts.
- Retry orders safely using an optional idempotency key; reject reuse with different details.
- Cancel an order and replenish stock **once**.
- Low-stock reports include product/warehouse combinations with **zero stock**.
- Overview reports show available units, fulfilled orders, and fulfilled revenue (gross sales, not profit).
- Basic interactive browser dashboard at `/` and OpenAPI docs at `/docs`.
- API-key authentication for `/api/*`; public `/health` for readiness checks.

## Start locally

Requires **Python 3.10+**. From the `inventory-api` directory:

```bash
python -m venv .venv
source .venv/bin/activate         # Windows PowerShell: .venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
cp .env.example .env              # Windows PowerShell: Copy-Item .env.example .env
```

In `.env`, change `INVENTORY_API_KEY` to a unique long random value. Generate one with:

```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

> `.env` is optional for local-only testing: without it, the development key is `local-dev-key-change-me`. **Never expose the default key to the internet.** API-key auth is for demos; production needs user accounts, roles and stronger controls.

Start the server:

```bash
python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

Visit `http://127.0.0.1:8000/` and enter the configured API key. `http://127.0.0.1:8000/docs` lists every endpoint. The database is created automatically in `data/inventory.sqlite3`.

Optional sample data:

```bash
python -m scripts.seed_demo
```

## Run all tests

```bash
python -m pytest -q
```

All tests use isolated temporary SQLite databases; demo data is not required. Concurrent tests verify checkouts do not oversell the same item.

## Example API requests

```bash
curl -H 'X-API-Key: local-dev-key-change-me' \
     -H 'Content-Type: application/json' \
     -d '{"code":"MAIN","name":"Main warehouse"}' \
     http://127.0.0.1:8000/api/warehouses

curl -H 'X-API-Key: local-dev-key-change-me' \
     -H 'Content-Type: application/json' \
     -d '{"sku":"PEN-001","name":"Blue pen","price_cents":150,"reorder_point":5}' \
     http://127.0.0.1:8000/api/products
```

Use your own key if changed. Use product and warehouse IDs returned by the API for adjustments, transfers, and orders.

## REST endpoint summary

| Method | Path | Description |
| --- | --- | --- |
| GET | `/health` | Health check |
| GET | `/` | Browser dashboard |
| POST / GET | `/api/products` | Create/list products |
| GET / PATCH | `/api/products/{id}` | Get/edit product |
| POST / GET | `/api/warehouses` | Create/list warehouses |
| GET | `/api/stock` | View recorded stock balances |
| POST | `/api/stock/adjustments` | Positive/negative adjustments |
| POST | `/api/stock/transfers` | Atomic transfer |
| GET | `/api/stock/movements` | Inventory audit trail |
| POST / GET | `/api/orders` | Fulfill/list orders |
| GET | `/api/orders/{id}` | Order details and price snapshots |
| POST | `/api/orders/{id}/cancel` | Cancel once, restore stock |
| GET | `/api/reports/low-stock` | Threshold-based replenishment suggestions |
| GET | `/api/reports/overview` | Simple inventory KPIs |

## Design decisions and limits

- Prices are integer cents to avoid floating-point money errors. This starter uses USD presentation in the dashboard, but does **not** implement currencies/taxes/payments.
- On-hand inventory decreases immediately when an order is created; there is no cart, reservation, partial fulfillment, return, supplier purchase order, or shipping process yet.
- This is a **single-business** local-first application using SQLite; API keys are global rather than per-user.
- The UI has basic forms for creating products/warehouses, adjustments, and one-line orders. API supports orders with multiple lines, transfers, cancellation, and reports.
- The stock ledger is immutable through the supplied API, but SQLite admins can still edit the database directly; use proper permissions and audit controls for production.
- `.env` values are loaded on process startup; restart the server after changing configuration.
- No external AI service is needed. A future forecasting/replenishment model could be added as a large project enhancement.

## Suggested future work

Role-based accounts, suppliers and purchase orders, lot/expiry tracking, barcode scanners, returns, reservations, forecasting, alert delivery, Postgres migrations, multi-business data isolation, and end-to-end browser tests. Each can be built and evaluated across multiple development turns.
