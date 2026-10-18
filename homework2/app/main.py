import json
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import UUID, uuid4

import yaml
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, PlainTextResponse
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.db import SessionLocal, User, now
from app.generated.routes import router
from app.service import ApiError, decode_token, password_hash

SPEC_PATH = Path(__file__).resolve().parents[1] / "src/main/resources/openapi/marketplace.yaml"


@asynccontextmanager
async def lifespan(app):
    email = os.getenv("ADMIN_EMAIL", "admin@example.com").lower()
    password = os.getenv("ADMIN_PASSWORD", "admin12345")
    with SessionLocal.begin() as session:
        if session.scalar(select(User.id).where(User.email == email)) is None:
            session.add(User(email=email, password_hash=password_hash(password), role="ADMIN"))
    yield


app = FastAPI(title="Marketplace homework API", lifespan=lifespan)
app.include_router(router)
app.openapi = lambda: yaml.safe_load(SPEC_PATH.read_text())


@app.get("/health", include_in_schema=False)
def health():
    return {"status": "ok", "service": "marketplace"}


@app.get("/openapi.yaml", include_in_schema=False)
def openapi_yaml():
    return PlainTextResponse(SPEC_PATH.read_text(), media_type="application/yaml")


@app.exception_handler(ApiError)
async def api_error_handler(request, exc):
    payload = {"error_code": exc.code, "message": exc.message}
    if exc.details is not None:
        payload["details"] = exc.details
    return JSONResponse(payload, status_code=exc.status)


@app.exception_handler(RequestValidationError)
async def validation_error_handler(request, exc):
    fields = [{"field": ".".join(str(part) for part in err["loc"] if part != "body"), "message": err["msg"]} for err in exc.errors()]
    return JSONResponse({"error_code": "VALIDATION_ERROR", "message": "Неверные данные запроса", "details": {"fields": fields}}, status_code=400)


@app.exception_handler(IntegrityError)
async def integrity_error_handler(request, exc):
    constraint = getattr(getattr(exc.orig, "diag", None), "constraint_name", None)
    field = {"users_email_key": "email", "promo_codes_code_key": "code"}.get(constraint, "database")
    return JSONResponse({"error_code": "VALIDATION_ERROR", "message": "Данные нарушают ограничение базы", "details": {"fields": [{"field": field, "message": "database constraint"}]}}, status_code=400)


def hide_passwords(value):
    if isinstance(value, dict):
        return {key: "***" if "password" in key.lower() or "token" in key.lower() else hide_passwords(item) for key, item in value.items()}
    if isinstance(value, list):
        return [hide_passwords(item) for item in value]
    return value


@app.middleware("http")
async def log_request(request: Request, call_next):
    request_id = str(uuid4())
    started = time.perf_counter()
    body = None
    if request.method in {"POST", "PUT", "DELETE"}:
        raw = await request.body()
        if raw:
            try:
                body = hide_passwords(json.loads(raw))
            except (UnicodeDecodeError, json.JSONDecodeError):
                body = "<not JSON>"
    status_code = 500
    try:
        path = request.url.path
        if any(path == root or path.startswith(root + "/") for root in ("/products", "/orders", "/promo-codes")):
            try:
                header = request.headers.get("Authorization", "")
                if not header.startswith("Bearer "):
                    raise ApiError(401, "TOKEN_INVALID", "Нужен access token")
                claims = decode_token(header[7:], "access")
                user_id = UUID(claims["sub"])
                with SessionLocal() as session:
                    user = session.get(User, user_id)
                    if user is None or user.role != claims["role"]:
                        raise ApiError(401, "TOKEN_INVALID", "Пользователь не найден")
                request.state.user_id = str(user_id)
            except (ValueError, TypeError):
                error = ApiError(401, "TOKEN_INVALID", "Неверный токен")
            except ApiError as exc:
                error = exc
            else:
                error = None
            if error is not None:
                status_code = error.status
                response = JSONResponse({"error_code": error.code, "message": error.message}, status_code=error.status)
                response.headers["X-Request-Id"] = request_id
                return response
        response = await call_next(request)
        status_code = response.status_code
        response.headers["X-Request-Id"] = request_id
        return response
    finally:
        event = {"request_id": request_id, "method": request.method, "endpoint": request.url.path,
                 "status_code": status_code, "duration_ms": round((time.perf_counter() - started) * 1000, 2),
                 "user_id": getattr(request.state, "user_id", None), "timestamp": now().isoformat()}
        if request.method in {"POST", "PUT", "DELETE"}:
            event["body"] = body
        print(json.dumps(event, ensure_ascii=False, default=str), flush=True)
