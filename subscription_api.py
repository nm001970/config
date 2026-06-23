import os
import base64

from fastapi import FastAPI, HTTPException, Response

from database.repositories.public_config_repository import (
    get_public_configs_for_subscription
)
from database.repositories.bot_user_repository import (
    get_user_by_subscription_token
)


DEBUG_SUB_RAW = os.getenv("DEBUG_SUB_RAW", "false").lower() == "true"

SUBSCRIPTION_CONFIG_LIMIT = int(os.getenv("SUBSCRIPTION_CONFIG_LIMIT", "10"))

app = FastAPI(
    docs_url=None,
    redoc_url=None,
    openapi_url=None
)


def build_text_response(content: str, status_code: int = 200):
    return Response(
        content=content,
        media_type="text/plain; charset=utf-8",
        status_code=status_code,
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
            "Pragma": "no-cache",
            "X-Content-Type-Options": "nosniff"
        }
    )


def get_active_subscription_user(token: str):
    user = get_user_by_subscription_token(token)

    if not user:
        raise HTTPException(
            status_code=404,
            detail="Subscription not found"
        )

    if not user["is_active"]:
        raise HTTPException(
            status_code=403,
            detail="Subscription expired"
        )

    return user


def get_subscription_raw_text(limit: int = 20):
    lines = get_public_configs_for_subscription(limit=limit)

    if not lines:
        raise HTTPException(
            status_code=503,
            detail="No healthy config available"
        )

    return "\n".join(lines)


@app.get("/")
def root():
    return {
        "status": "ok"
    }


@app.get("/sub/{token}")
def get_subscription(token: str):
    get_active_subscription_user(token)

    raw_text = get_subscription_raw_text(limit=SUBSCRIPTION_CONFIG_LIMIT)

    encoded = base64.b64encode(
        raw_text.encode("utf-8")
    ).decode("utf-8")

    return build_text_response(encoded)


@app.get("/sub-raw/{token}")
def get_subscription_raw(token: str):
    if not DEBUG_SUB_RAW:
        raise HTTPException(
            status_code=404,
            detail="Not found"
        )

    get_active_subscription_user(token)
    raw_text = get_subscription_raw_text(limit=SUBSCRIPTION_CONFIG_LIMIT)

    return build_text_response(raw_text)