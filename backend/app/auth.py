"""
Validates Telegram WebApp `initData` on every request.

Without this, anyone could open devtools and send `user_id=<someone_else>` in
the request body and drain that person's diamonds / trigger their Stars
cashout. The signature proves the request really came from Telegram for the
user it claims to be.

Docs: https://core.telegram.org/bots/webapps#validating-data-received-via-the-mini-app
"""
import hashlib
import hmac
import json
import time
from urllib.parse import parse_qsl

from fastapi import Header, HTTPException

from .config import BOT_TOKEN

MAX_AGE_SECONDS = 24 * 60 * 60  # reject stale initData


def _validate(init_data: str) -> dict:
    try:
        pairs = dict(parse_qsl(init_data, strict_parsing=True))
    except ValueError:
        raise HTTPException(401, "malformed initData")

    received_hash = pairs.pop("hash", None)
    if not received_hash:
        raise HTTPException(401, "missing hash")

    data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(pairs.items()))
    secret_key = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
    computed_hash = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()

    if not hmac.compare_digest(computed_hash, received_hash):
        raise HTTPException(401, "invalid initData signature")

    auth_date = int(pairs.get("auth_date", 0))
    if time.time() - auth_date > MAX_AGE_SECONDS:
        raise HTTPException(401, "initData expired")

    user = json.loads(pairs["user"])
    return user


async def current_user(x_telegram_init_data: str = Header(...)) -> dict:
    """FastAPI dependency — use as: user = Depends(current_user)."""
    return _validate(x_telegram_init_data)
