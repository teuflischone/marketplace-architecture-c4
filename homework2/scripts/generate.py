from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
SPEC = ROOT / "src/main/resources/openapi/marketplace.yaml"
OUT = ROOT / "app/generated"
source = yaml.safe_load(SPEC.read_text())
OUT.mkdir(exist_ok=True)


def type_name(schema, strict=False):
    if "$ref" in schema:
        return schema["$ref"].split("/")[-1]
    if "enum" in schema:
        return "Literal[" + ", ".join(repr(x) for x in schema["enum"]) + "]"
    kind = schema.get("type")
    if kind == "array":
        return f"list[{type_name(schema['items'], strict)}]"
    if kind == "integer":
        return "StrictInt" if strict else "int"
    if kind == "number":
        return "DecimalNumber"
    if kind == "boolean":
        return "StrictBool" if strict else "bool"
    if kind == "string":
        return {"uuid": "UUID", "date-time": "datetime", "email": "EmailStr"}.get(schema.get("format"), "str")
    return "dict[str, Any]"


def field_line(name, schema, required):
    typ = type_name(schema, strict=True)
    nullable = schema.get("nullable", False)
    optional = not required or nullable
    if optional:
        typ += " | None"
    args = ["..." if required else "None"]
    for source_key, target in (("minLength", "min_length"), ("maxLength", "max_length"), ("minimum", "ge"), ("maximum", "le"), ("minItems", "min_length"), ("maxItems", "max_length"), ("pattern", "pattern")):
        if source_key in schema:
            args.append(f"{target}={schema[source_key]!r}")
    return f"    {name}: {typ} = Field({', '.join(args)})"


lines = [
    "# Generated from src/main/resources/openapi/marketplace.yaml. Do not edit.",
    "from __future__ import annotations",
    "from datetime import datetime",
    "from decimal import Decimal",
    "from typing import Annotated, Any, Literal",
    "from uuid import UUID",
    "from pydantic import BaseModel, BeforeValidator, ConfigDict, EmailStr, Field, PlainSerializer, StrictBool, StrictInt",
    "def numeric_input(value):",
    "    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):",
    "        raise ValueError('must be a JSON number')",
    "    return value",
    "DecimalNumber = Annotated[Decimal, BeforeValidator(numeric_input), PlainSerializer(float, return_type=float, when_used='json')]",
]
for name, schema in source["components"]["schemas"].items():
    if schema.get("type") != "object":
        lines.append(f"{name} = {type_name(schema)}")
        continue
    lines += [f"class {name}(BaseModel):", "    model_config = ConfigDict(extra='forbid')"]
    required = set(schema.get("required", []))
    for field, definition in schema.get("properties", {}).items():
        lines.append(field_line(field, definition, field in required))
    lines.append("")
(OUT / "models.py").write_text("\n".join(lines) + "\n")

routes = [
    "# Generated from src/main/resources/openapi/marketplace.yaml. Do not edit.",
    "from __future__ import annotations",
    "from typing import Literal",
    "from uuid import UUID",
    "from fastapi import APIRouter, Body, Query, Request",
    "from app.generated import models",
    "from app import service",
    "router = APIRouter()",
]
for path, path_item in source["paths"].items():
    shared = path_item.get("parameters", [])
    for method, operation in path_item.items():
        if method not in {"get", "post", "put", "delete", "patch"}:
            continue
        name = operation["operationId"]
        success_code = next(int(k) for k in operation["responses"] if k.startswith("2"))
        response = operation["responses"][str(success_code)]
        response_schema = response.get("content", {}).get("application/json", {}).get("schema")
        response_type = "models." + response_schema["$ref"].split("/")[-1] if response_schema else "None"
        routes.append(f"@router.{method}({path!r}, status_code={success_code}, response_model={response_type}, operation_id={name!r})")
        params = ["request: Request"]
        args = []
        for param in shared + operation.get("parameters", []):
            key = param["name"]
            schema = param["schema"]
            typ = type_name(schema)
            if "$ref" in schema:
                typ = "models." + typ
            if param["in"] == "path":
                params.append(f"{key}: {typ}")
            else:
                field_args = [f"{schema.get('default')!r}"]
                for src, dst in (("minimum", "ge"), ("maximum", "le")):
                    if src in schema:
                        field_args.append(f"{dst}={schema[src]}")
                params.append(f"{key}: {typ} | None = Query({', '.join(field_args)})" if "default" not in schema else f"{key}: {typ} = Query({', '.join(field_args)})")
            args.append(f"{key}={key}")
        body_schema = operation.get("requestBody", {}).get("content", {}).get("application/json", {}).get("schema")
        if body_schema:
            body_type = body_schema["$ref"].split("/")[-1]
            params.append(f"body: models.{body_type} = Body(...)")
            args.append("body=body")
        routes.append(f"def {name}({', '.join(params)}):")
        routes.append(f"    return service.execute({name!r}, request, {', '.join(args)})" if args else f"    return service.execute({name!r}, request)")
        routes.append("")
(OUT / "routes.py").write_text("\n".join(routes) + "\n")
(OUT / "__init__.py").write_text("")
print("Generated app/generated/models.py and routes.py")
