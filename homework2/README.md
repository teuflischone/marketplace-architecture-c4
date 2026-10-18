# ДЗ №2. API маркетплейса

По теме это продолжение первой работы. Там я спроектировал архитектуру всего маркетплейса и сделал только `/health` у Catalog Service. Вторая работа запускается отдельно и не использует код первой. Здесь я реализовал API товаров и заказов в одном приложении с PostgreSQL. Контракт лежит в `src/main/resources/openapi/marketplace.yaml`.

## Запуск

Нужен Docker Compose. Команды запуска из папки `homework2`.

```bash
docker compose up --build -d
docker compose logs -f api
```

API работает по адресу `http://localhost:8081`. Swagger UI открыт по адресу `http://localhost:8081/docs`, исходный контракт по адресу `http://localhost:8081/openapi.yaml`. PostgreSQL доступен на порту `5433`.

Быстрая проверка API.

```bash
./scripts/demo.sh
```

Скрипт регистрирует продавца и покупателя, создаёт товар и заказ, отменяет заказ, проверяет остаток и выполняет `SELECT` из таблиц. Данные в скрипте каждый раз новые.

Остановить контейнеры.

```bash
docker compose down
```

Для полного сброса учебной БД можно выполнить `docker compose down -v`.

## Схема реализации второй ДЗ

В первой ДЗ C4-диаграмма показывает план системы с отдельными сервисами. Ниже показано то, что действительно запускается во второй ДЗ. Здесь одно приложение FastAPI и одна база PostgreSQL. Это позволяет менять остатки и заказ в одной транзакции.

```mermaid
flowchart LR
    client["Покупатель или продавец"] -->|HTTP и JWT| api["FastAPI<br/>правила работы"]
    spec["OpenAPI<br/>marketplace.yaml"] --> generator["generate.py"]
    generator --> generated["DTO и маршруты"]
    generated --> api
    api --> orm["SQLAlchemy"]
    orm --> db[("PostgreSQL")]
    flyway["Flyway<br/>миграции"] --> db
```

Схема показывает путь запроса, генерацию маршрутов из OpenAPI и сохранение данных.

- `marketplace.yaml` содержит схемы запросов и ответов, ограничения, роли и возможные ошибки.
- `scripts/generate.py` при сборке создаёт `app/generated/models.py` с Pydantic DTO и `app/generated/routes.py` с обработчиками HTTP. Они вызывают `service.execute`. Сгенерированные файлы не хранятся в Git.
- `app/service.py` содержит правила доступа и бизнес-логику.
- `app/db.py` содержит ORM-модели. Flyway создаёт таблицы миграцией `V1__create_tables.sql`, а `V2__native_enums.sql` задаёт типы ENUM для статусов и ролей.
- `app/main.py` запускает приложение, формирует ошибки и пишет JSON-лог каждого запроса.

После установки зависимостей генерацию можно запустить без Docker.

```bash
python3 -m pip install -r requirements.txt
python3 scripts/generate.py
```

## Основные правила

Покупатель может оформлять и менять свои заказы, продавец управляет только своими товарами. Администратор создаётся при старте из `ADMIN_EMAIL` и `ADMIN_PASSWORD` (по умолчанию `admin@example.com` / `admin12345` для локальной демонстрации). Регистрировать администратора через API нельзя. Access token действует 20 минут, refresh token действует 14 дней. Секрет JWT задаётся переменной `JWT_SECRET`.

При создании заказа сначала проверяются лимит частоты, активный заказ, товары и остатки. Затем в одной транзакции уменьшается `stock`, сохраняются цены позиций и заказ. При обновлении старые остатки возвращаются, новые резервируются, сумма пересчитывается. При отмене остатки и использование промокода возвращаются. Строка пользователя блокируется на время изменения заказа, а товары блокируются в одинаковом порядке, чтобы параллельные запросы не потратили один остаток два раза.

Для процентного промокода со значением больше 70% применяется скидка 100%. Скидка в остальных случаях округляется до копеек. При обновлении заказа, если сумма стала меньше минимальной для промокода, промокод снимается.

`ORDER_LIMIT_MINUTES` по умолчанию равен `1`. Для быстрой проверки нескольких обновлений подряд можно запустить с `ORDER_LIMIT_MINUTES=0 docker compose up --build -d`.

## Небольшие примеры

```bash
curl -X POST http://localhost:8081/auth/register \
  -H 'Content-Type: application/json' \
  -d '{"email":"buyer@example.com","password":"password123","role":"USER"}'

curl -X POST http://localhost:8081/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"email":"buyer@example.com","password":"password123"}'
```

Из ответа входа взять `access_token` и передать его как `Authorization: Bearer <token>` во все остальные запросы. Остальные запросы удобно выполнять в Swagger UI.

Проверка таблиц вручную.

```bash
docker compose exec db psql -U marketplace -d marketplace -c 'SELECT id, name, stock, status FROM products;'
docker compose exec db psql -U marketplace -d marketplace -c 'SELECT id, user_id, status, total_amount FROM orders;'
docker compose exec db psql -U marketplace -d marketplace -c 'SELECT order_id, product_id, quantity, price_at_order FROM order_items;'
docker compose exec db psql -U marketplace -d marketplace -c 'SELECT code, current_uses FROM promo_codes;'
```
