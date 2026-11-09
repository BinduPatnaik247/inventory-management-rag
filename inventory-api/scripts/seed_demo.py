"""Seed a small demo inventory through the same application service contract."""
from fastapi.testclient import TestClient
from app.main import create_app
from app.config import settings


def main():
    api_key, db_path = settings()
    with TestClient(create_app(db_path=db_path, api_key=api_key)) as client:
        headers = {"X-API-Key": api_key}
        warehouses = [
            {"code":"MAIN", "name":"Main Warehouse"},
            {"code":"NORTH", "name":"North Warehouse"},
        ]
        products = [
            {"sku":"NOTE-A5", "name":"A5 Notebook", "price_cents":549, "reorder_point":8},
            {"sku":"PEN-BLUE", "name":"Blue Gel Pen", "price_cents":199, "reorder_point":15},
            {"sku":"BOTTLE-1L", "name":"1L Water Bottle", "price_cents":1799, "reorder_point":4},
        ]
        for warehouse in warehouses:
            result = client.post('/api/warehouses', headers=headers,json=warehouse)
            if result.status_code not in (201,409):
                raise RuntimeError(result.text)
        for product in products:
            result = client.post('/api/products', headers=headers,json=product)
            if result.status_code not in (201,409):
                raise RuntimeError(result.text)
        available_warehouses = client.get('/api/warehouses', headers=headers).json()
        available_products = client.get('/api/products', headers=headers).json()
        quantities = {'NOTE-A5':30, 'PEN-BLUE':8, 'BOTTLE-1L':10}
        # Only seed products with zero stock, so rerunning doesn't duplicate inventory.
        stock = client.get('/api/stock', headers=headers).json()
        present = {(x['product_id'],x['warehouse_id']) for x in stock}
        main_id = next(x['id'] for x in available_warehouses if x['code']=='MAIN')
        for product in available_products:
            if product['sku'] in quantities and (product['id'],main_id) not in present:
                result = client.post('/api/stock/adjustments', headers=headers,json={
                    'product_id':product['id'], 'warehouse_id':main_id,
                    'delta':quantities[product['sku']], 'reason':'Demo seed',
                })
                if result.status_code != 201:
                    raise RuntimeError(result.text)
        print('Demo data ready. Open http://localhost:8000/ (API key is required in dashboard).')


if __name__ == '__main__':
    main()
