'use strict';
let apiKey = '';
const byId = id => document.getElementById(id);
const dollars = cents => `$${(cents / 100).toFixed(2)}`;
function showStatus(text, error = false) {
  const el = byId('status');
  el.textContent = text;
  el.className = error ? 'error' : 'ok';
}
async function request(path, opts = {}) {
  const response = await fetch(path, {
    ...opts,
    headers: {'X-API-Key': apiKey, 'Content-Type': 'application/json', ...(opts.headers || {})}
  });
  let body;
  try { body = await response.json(); } catch { body = {}; }
  if (!response.ok) {
    const detail = Array.isArray(body.detail) ? JSON.stringify(body.detail) : (body.detail || response.statusText);
    throw new Error(`${response.status}: ${detail}`);
  }
  return body;
}
function fillRows(tableId, rows) {
  const target = byId(tableId);
  target.replaceChildren();
  for (const values of rows) {
    const tr = document.createElement('tr');
    for (const value of values) {
      const td = document.createElement('td');
      td.textContent = String(value);
      tr.appendChild(td);
    }
    target.appendChild(tr);
  }
}
function fillSelect(id, records, label) {
  const el = byId(id);
  el.replaceChildren();
  for (const record of records) {
    const option = document.createElement('option');
    option.value = record.id;
    option.textContent = label(record);
    el.appendChild(option);
  }
}
async function refresh() {
  const [overview, products, warehouses, stocks, alerts, orders] = await Promise.all([
    request('/api/reports/overview'), request('/api/products'), request('/api/warehouses'),
    request('/api/stock'), request('/api/reports/low-stock'), request('/api/orders')
  ]);
  byId('metrics').replaceChildren();
  for (const [label, value] of [
    ['Products', overview.products], ['Warehouses', overview.warehouses],
    ['Units available', overview.units_in_stock], ['Low-stock locations', overview.low_stock_locations],
    ['Fulfilled orders', overview.orders_fulfilled], ['Fulfilled order value', dollars(overview.revenue_cents)]
  ]) {
    const card = document.createElement('div');
    card.className = 'card';
    const title = document.createElement('div'); title.textContent = label;
    const number = document.createElement('div'); number.className = 'metrics'; number.textContent = String(value);
    card.append(title, number);
    byId('metrics').appendChild(card);
  }
  fillSelect('adjustProduct', products, p => `${p.sku} · ${p.name}`);
  fillSelect('orderProduct', products, p => `${p.sku} · ${dollars(p.price_cents)}`);
  fillSelect('adjustWarehouse', warehouses, w => `${w.code} · ${w.name}`);
  fillSelect('orderWarehouse', warehouses, w => `${w.code} · ${w.name}`);
  fillRows('stocks', stocks.map(x => [x.sku, x.warehouse_code, x.quantity]));
  fillRows('alerts', alerts.map(x => [x.sku, x.warehouse_code, x.quantity, x.reorder_point]));
  fillRows('orders', orders.map(x => [x.id, x.customer_name, x.status, dollars(x.total_cents), x.created_at]));
}
byId('connect').onclick = async () => {
  apiKey = byId('apiKey').value.trim() || 'local-dev-key-change-me';
  try { await refresh(); showStatus('Connected. Inventory loaded.'); }
  catch (error) { showStatus(error.message, true); }
};
for (const [id, path, prepare] of [
  ['warehouseForm', '/api/warehouses', form => ({code:form.code.value, name:form.name.value})],
  ['productForm', '/api/products', form => ({sku:form.sku.value,name:form.name.value,
    price_cents:Math.round(Number(form.price.value)*100),reorder_point:Number(form.reorder_point.value)})],
  ['adjustForm', '/api/stock/adjustments', form => ({product_id:Number(form.product_id.value),
    warehouse_id:Number(form.warehouse_id.value),delta:Number(form.delta.value),reason:form.reason.value})],
  ['orderForm', '/api/orders', form => ({customer_name:form.customer_name.value,
    lines:[{product_id:Number(form.product_id.value),warehouse_id:Number(form.warehouse_id.value),
    quantity:Number(form.quantity.value)}]})],
]) {
  byId(id).addEventListener('submit', async event => {
    event.preventDefault();
    try {
      await request(path, {method:'POST', body:JSON.stringify(prepare(event.currentTarget.elements))});
      await refresh(); showStatus('Saved successfully.');
    } catch(error) { showStatus(error.message, true); }
  });
}
