from concurrent.futures import ThreadPoolExecutor
import sqlite3
from tests.conftest import restock, create_order


def test_health_no_key(client):
    client.headers.clear()
    assert client.get('/health').json()['status'] == 'ok'


def test_protected_endpoint_requires_key(client):
    assert client.get('/api/products', headers={'X-API-Key': 'wrong'}).status_code == 401
    client.headers.clear()
    assert client.get('/api/warehouses').status_code == 401


def test_docs_available(client):
    assert client.get('/openapi.json').status_code == 200
    assert client.get('/').status_code == 200
    assert client.get('/static/dashboard.js').status_code == 200


def test_new_product_and_warehouse(setup, client):
    p, a, b = setup
    assert p['sku'] == 'CUP-001'
    assert len(client.get('/api/warehouses').json()) == 2
    assert client.get(f"/api/products/{p['id']}").json()['name'] == 'Reusable Cup'


def test_duplicate_sku_case_insensitive(setup, client):
    response = client.post('/api/products', json={'sku': 'cup-001', 'name': 'Copy', 'price_cents': 0})
    assert response.status_code == 409


def test_duplicate_warehouse_code(setup, client):
    assert client.post('/api/warehouses', json={'code': 'main', 'name': 'Other'}).status_code == 409


def test_product_validation(client):
    assert client.post('/api/products', json={'sku': 'A', 'name':'A', 'price_cents':-1}).status_code == 422
    assert client.post('/api/products', json={'sku': 'GOOD', 'name':'A', 'price_cents':-1}).status_code == 422


def test_update_product(setup, client):
    p, _, _ = setup
    updated = client.patch(f"/api/products/{p['id']}", json={'price_cents': 1500, 'reorder_point': 2})
    assert updated.status_code == 200
    assert updated.json()['price_cents'] == 1500
    assert client.patch(f"/api/products/{p['id']}", json={}).status_code == 422
    assert client.patch(f"/api/products/{p['id']}", json={'name': None}).status_code == 422


def test_missing_product(client):
    assert client.get('/api/products/9999').status_code == 404
    assert client.patch('/api/products/9999', json={'name': 'nope'}).status_code == 404


def test_product_search(setup, client):
    assert len(client.get('/api/products?search=Reusable').json()) == 1
    assert len(client.get('/api/products?search=missing').json()) == 0
    assert len(client.get('/api/products?search=%25').json()) == 0  # literal % not wildcard


def test_low_stock_includes_zero_quantity(setup, client):
    p, a, b = setup
    alerts = client.get('/api/reports/low-stock').json()
    assert {(x['product_id'], x['warehouse_id']) for x in alerts} == {(p['id'],a['id']),(p['id'],b['id'])}
    assert all(x['quantity'] == 0 for x in alerts)


def test_adjustment_and_stock_movement(setup, client):
    p, a, _ = setup
    assert restock(client, p, a, 8).status_code == 201
    assert client.get('/api/stock').json()[0]['quantity'] == 8
    movement = client.get('/api/stock/movements').json()[0]
    assert movement['delta'] == 8
    assert 'Opening' in movement['reason']


def test_cannot_adjust_below_zero(setup, client):
    p, a, _ = setup
    assert restock(client,p,a,3).status_code == 201
    err = client.post('/api/stock/adjustments',json={'product_id':p['id'],'warehouse_id':a['id'],
                                                     'delta':-4,'reason':'lost units'})
    assert err.status_code == 409
    assert client.get('/api/stock').json()[0]['quantity'] == 3
    assert len(client.get('/api/stock/movements').json()) == 1


def test_zero_stock_adjustment_rejected(setup, client):
    p, a, _ = setup
    assert client.post('/api/stock/adjustments',json={'product_id':p['id'],'warehouse_id':a['id'],
                                                     'delta':0,'reason':'nothing'}).status_code == 422


def test_transfer_is_atomic(setup, client):
    p,a,b = setup
    restock(client,p,a,10)
    res = client.post('/api/stock/transfers',json={'product_id':p['id'],'from_warehouse_id':a['id'],
                                                   'to_warehouse_id':b['id'],'quantity':6})
    assert res.status_code == 201
    quantities = {x['warehouse_id']:x['quantity'] for x in client.get('/api/stock').json()}
    assert quantities == {a['id']:4,b['id']:6}
    assert len(client.get('/api/stock/movements').json()) == 3


def test_failed_transfer_does_not_write(setup, client):
    p,a,b = setup
    restock(client,p,a,2)
    err = client.post('/api/stock/transfers',json={'product_id':p['id'],'from_warehouse_id':a['id'],
                                                   'to_warehouse_id':b['id'],'quantity':3})
    assert err.status_code == 409
    stock = client.get('/api/stock').json()
    assert len(stock) == 1 and stock[0]['quantity'] == 2
    assert len(client.get('/api/stock/movements').json()) == 1


def test_same_warehouse_transfer_rejected(setup, client):
    p,a,b = setup
    assert client.post('/api/stock/transfers',json={'product_id':p['id'],'from_warehouse_id':a['id'],
                    'to_warehouse_id':a['id'],'quantity':1}).status_code == 422


def test_order_fulfills_and_decrements_stock(setup, client):
    p,a,_ = setup
    restock(client,p,a)
    result = create_order(client,p,a,3)
    assert result.status_code == 201
    assert result.json()['total_cents'] == 3750
    assert client.get('/api/stock').json()[0]['quantity'] == 7
    assert client.get('/api/reports/overview').json()['revenue_cents'] == 3750
    assert client.get('/api/orders').json()[0]['status'] == 'fulfilled'


def test_order_rejected_when_insufficient_stock(setup, client):
    p,a,_ = setup
    restock(client,p,a,2)
    assert create_order(client,p,a,3).status_code == 409
    assert len(client.get('/api/orders').json()) == 0
    assert client.get('/api/stock').json()[0]['quantity'] == 2


def test_failed_multiline_order_rolls_back(setup, client):
    p,a,b = setup
    restock(client,p,a,5)
    result=client.post('/api/orders',json={'customer_name':'Customer', 'lines':[
        {'product_id':p['id'],'warehouse_id':a['id'],'quantity':3},
        {'product_id':p['id'],'warehouse_id':b['id'],'quantity':3}]})
    assert result.status_code == 409
    assert client.get('/api/stock').json()[0]['quantity'] == 5
    assert len(client.get('/api/orders').json()) == 0
    assert len(client.get('/api/stock/movements').json()) == 1


def test_duplicate_order_key_reuses_same_order(setup, client):
    p,a,_ = setup
    restock(client,p,a,10)
    first = create_order(client,p,a,2,key='checkout-0001')
    second= create_order(client,p,a,2,key='checkout-0001')
    assert first.status_code == second.status_code == 201
    assert first.json()['id'] == second.json()['id']
    assert client.get('/api/stock').json()[0]['quantity'] == 8
    assert len(client.get('/api/orders').json()) == 1


def test_idempotency_key_conflicting_body(setup, client):
    p,a,_ = setup
    restock(client,p,a,10)
    assert create_order(client,p,a,2,key='checkout-0002').status_code == 201
    assert create_order(client,p,a,3,key='checkout-0002').status_code == 409
    assert client.get('/api/stock').json()[0]['quantity'] == 8


def test_cancel_order_restocks_once(setup, client):
    p,a,_ = setup
    restock(client,p,a,5)
    order = create_order(client,p,a,4).json()
    one=client.post(f"/api/orders/{order['id']}/cancel")
    two=client.post(f"/api/orders/{order['id']}/cancel")
    assert one.status_code == two.status_code == 200
    assert one.json()['status'] == two.json()['status'] == 'cancelled'
    assert client.get('/api/stock').json()[0]['quantity'] == 5
    assert client.get('/api/reports/overview').json()['revenue_cents'] == 0


def test_order_price_is_snapshot(setup, client):
    p,a,_ = setup
    restock(client,p,a)
    order=create_order(client,p,a,2).json()
    client.patch(f"/api/products/{p['id']}",json={'price_cents':9999})
    assert client.get(f"/api/orders/{order['id']}").json()['total_cents'] == 2500
    assert client.get(f"/api/orders/{order['id']}").json()['lines'][0]['unit_price_cents'] == 1250


def test_inactive_product_cannot_be_ordered(setup, client):
    p,a,_ = setup
    restock(client,p,a)
    client.patch(f"/api/products/{p['id']}",json={'active':False})
    assert create_order(client,p,a).status_code == 409
    assert len(client.get('/api/products').json()) == 0
    assert len(client.get('/api/products?include_inactive=true').json()) == 1


def test_unknown_warehouse_stock_adjustment(setup,client):
    p,a,_ = setup
    assert client.post('/api/stock/adjustments',json={'product_id':p['id'],
           'warehouse_id':9999,'delta':1,'reason':'receive'}).status_code == 404


def test_order_movement_reference(setup,client):
    p,a,_=setup
    restock(client,p,a)
    order=create_order(client,p,a,2).json()
    rows=client.get('/api/stock/movements').json()
    assert rows[0]['reason'] == 'order_fulfilled'
    assert rows[0]['reference'] == f"order:{order['id']}"


def test_low_stock_warehouse_filter(setup,client):
    p,a,b=setup
    assert len(client.get(f"/api/reports/low-stock?warehouse_id={a['id']}").json()) == 1
    restock(client,p,a,5)
    assert len(client.get(f"/api/reports/low-stock?warehouse_id={a['id']}").json()) == 0
    assert len(client.get('/api/reports/low-stock').json()) == 1


def test_concurrent_orders_cannot_oversell(setup,client):
    p,a,_=setup
    restock(client,p,a,1)
    def buy(n):
        return create_order(client,p,a,1,key=f'concurrent-order-{n:03d}').status_code
    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(buy,range(8)))
    assert results.count(201) == 1
    assert results.count(409) == 7
    assert client.get('/api/stock').json()[0]['quantity'] == 0
    assert client.get('/api/reports/overview').json()['orders_fulfilled'] == 1
