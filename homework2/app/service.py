import hashlib
import hmac
import os
from datetime import timedelta
from decimal import Decimal, ROUND_HALF_UP
from uuid import UUID

import jwt
from fastapi import Request
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.db import Order, OrderItem, Product, PromoCode, SessionLocal, User, UserOperation, now

JWT_SECRET = os.getenv("JWT_SECRET", "change-this-for-real-use")
LIMIT_MINUTES = int(os.getenv("ORDER_LIMIT_MINUTES", "1"))
MONEY = Decimal("0.01")


class ApiError(Exception):
    def __init__(self, status, code, message, details=None):
        self.status = status
        self.code = code
        self.message = message
        self.details = details


def fail(status, code, message, details=None):
    raise ApiError(status, code, message, details)


def password_hash(password):
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 200_000)
    return salt.hex() + ":" + digest.hex()


def password_ok(password, stored):
    salt, expected = stored.split(":")
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), 200_000)
    return hmac.compare_digest(digest, bytes.fromhex(expected))


def make_token(user, token_type, minutes):
    return jwt.encode({"sub": str(user.id), "role": user.role, "typ": token_type, "exp": now() + timedelta(minutes=minutes)}, JWT_SECRET, algorithm="HS256")


def tokens(user):
    return {"access_token": make_token(user, "access", 20), "refresh_token": make_token(user, "refresh", 14 * 24 * 60), "token_type": "bearer"}


def decode_token(token, token_type):
    try:
        claims = jwt.decode(token, JWT_SECRET, algorithms=["HS256"], options={"require": ["sub", "exp", "typ", "role"]})
        if claims["typ"] != token_type:
            raise jwt.InvalidTokenError()
        return claims
    except jwt.ExpiredSignatureError:
        code = "TOKEN_EXPIRED" if token_type == "access" else "REFRESH_TOKEN_INVALID"
        fail(401, code, "Срок действия токена истёк")
    except jwt.InvalidTokenError:
        code = "TOKEN_INVALID" if token_type == "access" else "REFRESH_TOKEN_INVALID"
        fail(401, code, "Неверный токен")


def require_user(request, session):
    header = request.headers.get("Authorization", "")
    if not header.startswith("Bearer "):
        fail(401, "TOKEN_INVALID", "Нужен access token")
    claims = decode_token(header[7:], "access")
    try:
        user_id = UUID(claims["sub"])
    except ValueError:
        fail(401, "TOKEN_INVALID", "Неверный токен")
    user = session.get(User, user_id)
    if user is None or user.role != claims["role"]:
        fail(401, "TOKEN_INVALID", "Пользователь не найден")
    request.state.user_id = str(user.id)
    return user


def require_role(user, *allowed):
    if user.role not in allowed:
        fail(403, "ACCESS_DENIED", "Недостаточно прав")


def product_data(product):
    return {key: getattr(product, key) for key in ("id", "name", "description", "price", "stock", "category", "status", "seller_id", "created_at", "updated_at")}


def order_data(order):
    return {"id": order.id, "user_id": order.user_id, "status": order.status,
            "promo_code": order.promo.code if order.promo else None,
            "total_amount": order.total_amount, "discount_amount": order.discount_amount,
            "created_at": order.created_at, "updated_at": order.updated_at,
            "items": [{"product_id": item.product_id, "quantity": item.quantity, "price_at_order": item.price_at_order} for item in order.items]}


def promo_data(promo):
    return {key: getattr(promo, key) for key in ("id", "code", "discount_type", "discount_value", "min_order_amount", "max_uses", "current_uses", "active")}


def get_product(session, product_id, lock=False):
    stmt = select(Product).where(Product.id == product_id)
    if lock:
        stmt = stmt.with_for_update()
    product = session.scalar(stmt)
    if product is None:
        fail(404, "PRODUCT_NOT_FOUND", "Товар не найден")
    return product


def get_order(session, order_id, user, lock=False):
    stmt = select(Order).where(Order.id == order_id)
    if lock:
        stmt = stmt.with_for_update()
    order = session.scalar(stmt)
    if order is None:
        fail(404, "ORDER_NOT_FOUND", "Заказ не найден")
    if user.role != "ADMIN" and order.user_id != user.id:
        fail(403, "ORDER_OWNERSHIP_VIOLATION", "Это заказ другого пользователя")
    return order


def check_limit(session, user_id, operation):
    if LIMIT_MINUTES <= 0:
        return
    latest = session.scalar(select(UserOperation).where(UserOperation.user_id == user_id, UserOperation.operation_type == operation).order_by(UserOperation.created_at.desc()).limit(1))
    if latest and now() - latest.created_at < timedelta(minutes=LIMIT_MINUTES):
        fail(429, "ORDER_LIMIT_EXCEEDED", "Слишком частая операция с заказом")


def lock_user(session, user_id):
    session.scalar(select(User).where(User.id == user_id).with_for_update())


def aggregate_items(items):
    quantities = {}
    for item in items:
        quantities[item.product_id] = quantities.get(item.product_id, 0) + item.quantity
    if any(value > 999 for value in quantities.values()):
        fail(400, "VALIDATION_ERROR", "Суммарное количество товара не может быть больше 999", {"fields": [{"field": "items", "message": "quantity > 999"}]})
    return quantities


def lock_products(session, product_ids):
    ids = sorted(product_ids, key=str)
    products = session.scalars(select(Product).where(Product.id.in_(ids)).order_by(Product.id).with_for_update()).all()
    found = {product.id: product for product in products}
    for product_id in ids:
        if product_id not in found:
            fail(404, "PRODUCT_NOT_FOUND", "Товар не найден", {"product_id": str(product_id)})
    return found


def validate_items(products, quantities):
    missing = []
    for product_id, quantity in quantities.items():
        product = products[product_id]
        if product.status != "ACTIVE":
            fail(409, "PRODUCT_INACTIVE", "Товар не активен", {"product_id": str(product_id)})
        if product.stock < quantity:
            missing.append({"product_id": str(product_id), "requested": quantity, "available": product.stock})
    if missing:
        fail(409, "INSUFFICIENT_STOCK", "Недостаточно товара", {"products": missing})


def calculate_discount(promo, subtotal):
    if promo.discount_type == "PERCENTAGE":
        if promo.discount_value > 70:
            return Decimal("0")
        return (subtotal * promo.discount_value / 100).quantize(MONEY, rounding=ROUND_HALF_UP)
    return min(promo.discount_value, subtotal)


def check_promo(promo, subtotal, check_uses=True):
    current = now()
    if promo is None or not promo.active or not promo.valid_from <= current <= promo.valid_until or (check_uses and promo.current_uses >= promo.max_uses):
        fail(422, "PROMO_CODE_INVALID", "Промокод недоступен")
    if subtotal < promo.min_order_amount:
        fail(422, "PROMO_CODE_MIN_AMOUNT", "Сумма заказа ниже минимума промокода")


def execute(operation, request: Request, **kwargs):
    with SessionLocal.begin() as session:
        body = kwargs.get("body")
        if operation == "register":
            if session.scalar(select(User.id).where(User.email == str(body.email).lower())):
                fail(400, "VALIDATION_ERROR", "Email уже занят", {"fields": [{"field": "email", "message": "already exists"}]})
            user = User(email=str(body.email).lower(), password_hash=password_hash(body.password), role=body.role)
            session.add(user)
            session.flush()
            return {"id": user.id, "email": user.email, "role": user.role}
        if operation == "login":
            user = session.scalar(select(User).where(User.email == str(body.email).lower()))
            if user is None or not password_ok(body.password, user.password_hash):
                fail(400, "VALIDATION_ERROR", "Неверный email или пароль")
            return tokens(user)
        if operation == "refresh":
            claims = decode_token(body.refresh_token, "refresh")
            try:
                user = session.get(User, UUID(claims["sub"]))
            except ValueError:
                user = None
            if user is None or user.role != claims["role"]:
                fail(401, "REFRESH_TOKEN_INVALID", "Неверный refresh token")
            return tokens(user)

        user = require_user(request, session)
        if operation == "list_products":
            stmt = select(Product)
            count = select(func.count(Product.id))
            if kwargs["status"] is not None:
                stmt = stmt.where(Product.status == kwargs["status"])
                count = count.where(Product.status == kwargs["status"])
            if kwargs["category"] is not None:
                stmt = stmt.where(Product.category == kwargs["category"])
                count = count.where(Product.category == kwargs["category"])
            total = session.scalar(count)
            rows = session.scalars(stmt.order_by(Product.created_at, Product.id).offset(kwargs["page"] * kwargs["size"]).limit(kwargs["size"])).all()
            return {"items": [product_data(p) for p in rows], "totalElements": total, "page": kwargs["page"], "size": kwargs["size"]}
        if operation == "get_product":
            return product_data(get_product(session, kwargs["id"]))
        if operation == "create_product":
            require_role(user, "SELLER", "ADMIN")
            product = Product(**body.model_dump(), seller_id=user.id)
            session.add(product)
            session.flush()
            return product_data(product)
        if operation in ("update_product", "delete_product"):
            require_role(user, "SELLER", "ADMIN")
            product = get_product(session, kwargs["id"], lock=True)
            if user.role == "SELLER" and product.seller_id != user.id:
                fail(403, "ACCESS_DENIED", "Чужой товар")
            if operation == "delete_product":
                if product.status != "ARCHIVED":
                    product.status = "ARCHIVED"
                    product.updated_at = now()
                    session.flush()
                return None
            changes = body.model_dump()
            if any(getattr(product, key) != value for key, value in changes.items()):
                for key, value in changes.items():
                    setattr(product, key, value)
                product.updated_at = now()
                session.flush()
            return product_data(product)
        if operation == "create_promo":
            require_role(user, "SELLER", "ADMIN")
            if body.valid_until <= body.valid_from:
                fail(400, "VALIDATION_ERROR", "Конец действия должен быть позже начала", {"fields": [{"field": "valid_until", "message": "must be after valid_from"}]})
            if session.scalar(select(PromoCode.id).where(PromoCode.code == body.code)):
                fail(400, "VALIDATION_ERROR", "Промокод уже существует", {"fields": [{"field": "code", "message": "already exists"}]})
            promo = PromoCode(**body.model_dump(), current_uses=0, active=True)
            session.add(promo)
            session.flush()
            return promo_data(promo)
        if operation == "create_order":
            require_role(user, "USER", "ADMIN")
            lock_user(session, user.id)
            check_limit(session, user.id, "CREATE_ORDER")
            if session.scalar(select(Order.id).where(Order.user_id == user.id, Order.status.in_(("CREATED", "PAYMENT_PENDING"))).limit(1)):
                fail(409, "ORDER_HAS_ACTIVE", "У пользователя уже есть активный заказ")
            quantities = aggregate_items(body.items)
            products = lock_products(session, quantities)
            validate_items(products, quantities)
            for product_id, quantity in quantities.items():
                products[product_id].stock -= quantity
            subtotal = sum((products[item.product_id].price * item.quantity for item in body.items), Decimal("0"))
            promo = None
            discount = Decimal("0")
            if body.promo_code:
                promo = session.scalar(select(PromoCode).where(PromoCode.code == body.promo_code).with_for_update())
                check_promo(promo, subtotal)
                discount = calculate_discount(promo, subtotal)
                promo.current_uses += 1
            order = Order(user_id=user.id, status="CREATED", promo=promo, total_amount=subtotal - discount, discount_amount=discount)
            order.items = [OrderItem(product_id=item.product_id, quantity=item.quantity, price_at_order=products[item.product_id].price) for item in body.items]
            session.add_all([order, UserOperation(user_id=user.id, operation_type="CREATE_ORDER")])
            session.flush()
            return order_data(order)
        if operation in ("get_order", "update_order", "cancel_order", "change_order_status"):
            require_role(user, "USER", "ADMIN")
            if operation == "change_order_status":
                require_role(user, "ADMIN")
            if operation != "get_order":
                owner_id = session.scalar(select(Order.user_id).where(Order.id == kwargs["id"]))
                if owner_id is None:
                    fail(404, "ORDER_NOT_FOUND", "Заказ не найден")
                if user.role != "ADMIN" and owner_id != user.id:
                    fail(403, "ORDER_OWNERSHIP_VIOLATION", "Это заказ другого пользователя")
                lock_user(session, owner_id)
            order = get_order(session, kwargs["id"], user, lock=operation != "get_order")
            if operation == "get_order":
                return order_data(order)
            if operation == "change_order_status":
                transitions = {"CREATED": "PAYMENT_PENDING", "PAYMENT_PENDING": "PAID", "PAID": "SHIPPED", "SHIPPED": "COMPLETED"}
                if transitions.get(order.status) != body.status:
                    fail(409, "INVALID_STATE_TRANSITION", "Недопустимый переход состояния")
                order.status = body.status
                order.updated_at = now()
                session.flush()
                return order_data(order)
            if operation == "cancel_order":
                if order.status not in ("CREATED", "PAYMENT_PENDING"):
                    fail(409, "INVALID_STATE_TRANSITION", "Этот заказ нельзя отменить")
                products = lock_products(session, {item.product_id for item in order.items})
                for item in order.items:
                    products[item.product_id].stock += item.quantity
                if order.promo_code_id:
                    promo = session.scalar(select(PromoCode).where(PromoCode.id == order.promo_code_id).with_for_update())
                    promo.current_uses -= 1
                order.status = "CANCELED"
                order.updated_at = now()
                session.flush()
                return order_data(order)
            if order.status != "CREATED":
                fail(409, "INVALID_STATE_TRANSITION", "Изменять можно только заказ CREATED")
            check_limit(session, order.user_id, "UPDATE_ORDER")
            quantities = aggregate_items(body.items)
            all_ids = set(quantities) | {item.product_id for item in order.items}
            products = lock_products(session, all_ids)
            for item in order.items:
                products[item.product_id].stock += item.quantity
            validate_items(products, quantities)
            for product_id, quantity in quantities.items():
                products[product_id].stock -= quantity
            subtotal = sum((products[item.product_id].price * item.quantity for item in body.items), Decimal("0"))
            discount = Decimal("0")
            if order.promo_code_id:
                promo = session.scalar(select(PromoCode).where(PromoCode.id == order.promo_code_id).with_for_update())
                try:
                    check_promo(promo, subtotal, check_uses=False)
                    discount = calculate_discount(promo, subtotal)
                except ApiError as exc:
                    if exc.code != "PROMO_CODE_MIN_AMOUNT":
                        raise
                    promo.current_uses -= 1
                    order.promo = None
            order.items.clear()
            session.flush()
            order.items = [OrderItem(product_id=item.product_id, quantity=item.quantity, price_at_order=products[item.product_id].price) for item in body.items]
            order.discount_amount = discount
            order.total_amount = subtotal - discount
            order.updated_at = now()
            session.add(UserOperation(user_id=order.user_id, operation_type="UPDATE_ORDER"))
            session.flush()
            return order_data(order)
    raise RuntimeError(f"Unknown operation: {operation}")
