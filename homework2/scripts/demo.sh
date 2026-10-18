#!/usr/bin/env bash
set -euo pipefail
BASE="${BASE:-http://localhost:8081}"
SELLER="seller$(python3 -c 'import uuid; print(uuid.uuid4().hex[:12])')@example.com"
BUYER="buyer$(python3 -c 'import uuid; print(uuid.uuid4().hex[:12])')@example.com"
curl -fsS -X POST "$BASE/auth/register" -H 'Content-Type: application/json' -d "{\"email\":\"$SELLER\",\"password\":\"password123\",\"role\":\"SELLER\"}" > /dev/null
curl -fsS -X POST "$BASE/auth/register" -H 'Content-Type: application/json' -d "{\"email\":\"$BUYER\",\"password\":\"password123\",\"role\":\"USER\"}" > /dev/null
SELLER_TOKEN="$(curl -fsS -X POST "$BASE/auth/login" -H 'Content-Type: application/json' -d "{\"email\":\"$SELLER\",\"password\":\"password123\"}" | python3 -c 'import json,sys; print(json.load(sys.stdin)["access_token"])')"
BUYER_TOKEN="$(curl -fsS -X POST "$BASE/auth/login" -H 'Content-Type: application/json' -d "{\"email\":\"$BUYER\",\"password\":\"password123\"}" | python3 -c 'import json,sys; print(json.load(sys.stdin)["access_token"])')"
PRODUCT="$(curl -fsS -X POST "$BASE/products" -H "Authorization: Bearer $SELLER_TOKEN" -H 'Content-Type: application/json' -d '{"name":"Кружка","price":350.00,"stock":5,"category":"Посуда","status":"ACTIVE"}')"
PRODUCT_ID="$(printf '%s' "$PRODUCT" | python3 -c 'import json,sys; print(json.load(sys.stdin)["id"])')"
ORDER="$(curl -fsS -X POST "$BASE/orders" -H "Authorization: Bearer $BUYER_TOKEN" -H 'Content-Type: application/json' -d "{\"items\":[{\"product_id\":\"$PRODUCT_ID\",\"quantity\":2}]}")"
ORDER_ID="$(printf '%s' "$ORDER" | python3 -c 'import json,sys; print(json.load(sys.stdin)["id"])')"
BUYER_ID="$(printf '%s' "$ORDER" | python3 -c 'import json,sys; print(json.load(sys.stdin)["user_id"])')"
curl -fsS "$BASE/products/$PRODUCT_ID" -H "Authorization: Bearer $BUYER_TOKEN" | python3 -m json.tool --no-ensure-ascii
printf '\nЗаказ после создания:\n'
printf '%s' "$ORDER" | python3 -m json.tool --no-ensure-ascii
printf '\nЗаказ после отмены:\n'
curl -fsS -X POST "$BASE/orders/$ORDER_ID/cancel" -H "Authorization: Bearer $BUYER_TOKEN" | python3 -m json.tool --no-ensure-ascii
printf '\nПроверка остатка:\n'
curl -fsS "$BASE/products/$PRODUCT_ID" -H "Authorization: Bearer $BUYER_TOKEN" | python3 -m json.tool --no-ensure-ascii
printf '\nТаблицы БД:\n'
docker compose exec -T db psql -U marketplace -d marketplace -c "SELECT id, name, stock, status FROM products WHERE id = '$PRODUCT_ID'; SELECT id, user_id, status, total_amount FROM orders WHERE id = '$ORDER_ID'; SELECT order_id, product_id, quantity, price_at_order FROM order_items WHERE order_id = '$ORDER_ID'; SELECT user_id, operation_type, created_at FROM user_operations WHERE user_id = '$BUYER_ID';"
