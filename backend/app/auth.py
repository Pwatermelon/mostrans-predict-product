"""Демо-авторизация: диспетчер и водители (HMAC-токен)."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from dataclasses import dataclass
from typing import Any, Literal

from fastapi import Cookie, Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.config import settings

Role = Literal["dispatcher", "driver"]

# Демо-учётки для жюри (пароль один — demo)
DISPATCHER_LOGIN = "dispatcher"
DEFAULT_PASSWORD = "demo"
# Водители: driver-1000 … driver-1011 → ТС-1000 … ТС-1011
DRIVER_ID_MIN = 1000
DRIVER_ID_MAX = 1011

_bearer = HTTPBearer(auto_error=False)


@dataclass(frozen=True)
class User:
    login: str
    role: Role
    vehicle_id: str | None = None
    display_name: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "login": self.login,
            "role": self.role,
            "vehicle_id": self.vehicle_id,
            "display_name": self.display_name or self.login,
        }


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(s: str) -> bytes:
    pad = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s + pad)


def authenticate(login: str, password: str) -> User | None:
    """Проверка логина/пароля. Возвращает User или None."""
    login = (login or "").strip()
    password = password or ""
    if password != DEFAULT_PASSWORD:
        return None
    if login.lower() == DISPATCHER_LOGIN:
        return User(
            login=DISPATCHER_LOGIN,
            role="dispatcher",
            display_name="Диспетчер",
        )
    # driver-1000 / driver1000 / ТС-1000
    vid: str | None = None
    low = login.lower().replace(" ", "")
    if low.startswith("driver-") or low.startswith("driver"):
        digits = "".join(c for c in low if c.isdigit())
        if digits.isdigit():
            n = int(digits)
            if DRIVER_ID_MIN <= n <= DRIVER_ID_MAX:
                vid = f"ТС-{n}"
    elif login.upper().startswith("ТС-") or login.upper().startswith("TC-"):
        digits = "".join(c for c in login if c.isdigit())
        if digits.isdigit():
            n = int(digits)
            if DRIVER_ID_MIN <= n <= DRIVER_ID_MAX:
                vid = f"ТС-{n}"
    if vid:
        return User(
            login=f"driver-{vid.split('-')[1]}",
            role="driver",
            vehicle_id=vid,
            display_name=f"Водитель {vid}",
        )
    return None


def issue_token(user: User, ttl_sec: int | None = None) -> str:
    """Подписанный токен: payload.b64.sig."""
    ttl = ttl_sec if ttl_sec is not None else settings.auth_token_ttl_sec
    payload = {
        "login": user.login,
        "role": user.role,
        "vehicle_id": user.vehicle_id,
        "display_name": user.display_name,
        "exp": int(time.time()) + ttl,
    }
    body = _b64url(json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    sig = _b64url(
        hmac.new(
            settings.auth_secret.encode("utf-8"),
            body.encode("ascii"),
            hashlib.sha256,
        ).digest()
    )
    return f"{body}.{sig}"


def parse_token(token: str) -> User | None:
    try:
        body, sig = token.split(".", 1)
        expect = _b64url(
            hmac.new(
                settings.auth_secret.encode("utf-8"),
                body.encode("ascii"),
                hashlib.sha256,
            ).digest()
        )
        if not hmac.compare_digest(sig, expect):
            return None
        payload = json.loads(_b64url_decode(body))
        if int(payload.get("exp", 0)) < time.time():
            return None
        role = payload.get("role")
        if role not in ("dispatcher", "driver"):
            return None
        return User(
            login=str(payload["login"]),
            role=role,
            vehicle_id=payload.get("vehicle_id"),
            display_name=str(payload.get("display_name") or payload["login"]),
        )
    except Exception:
        return None


def demo_accounts() -> list[dict[str, str]]:
    """Список демо-учёток для UI/жюри."""
    rows = [
        {
            "login": DISPATCHER_LOGIN,
            "password": DEFAULT_PASSWORD,
            "role": "dispatcher",
            "note": "пульт и статистика",
        }
    ]
    for n in range(DRIVER_ID_MIN, DRIVER_ID_MIN + 3):
        rows.append(
            {
                "login": f"driver-{n}",
                "password": DEFAULT_PASSWORD,
                "role": "driver",
                "note": f"кабинет ТС-{n}",
            }
        )
    rows.append(
        {
            "login": f"driver-{DRIVER_ID_MIN}+…+{DRIVER_ID_MAX}",
            "password": DEFAULT_PASSWORD,
            "role": "driver",
            "note": "все ТС эмулятора",
        }
    )
    return rows


async def current_user_optional(
    request: Request,
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
    mt_token: str | None = Cookie(default=None),
) -> User | None:
    token = None
    if creds and creds.credentials:
        token = creds.credentials
    elif mt_token:
        token = mt_token
    else:
        # query ?token= для простых ссылок (опционально)
        token = request.query_params.get("token")
    if not token:
        return None
    return parse_token(token)


async def require_user(user: User | None = Depends(current_user_optional)) -> User:
    if not user:
        raise HTTPException(status_code=401, detail="Требуется авторизация")
    return user


async def require_dispatcher(user: User = Depends(require_user)) -> User:
    if user.role != "dispatcher":
        raise HTTPException(status_code=403, detail="Только для диспетчера")
    return user


def ensure_driver_access(user: User, vehicle_id: str) -> None:
    """Диспетчер — любой ТС; водитель — только своё."""
    if user.role == "dispatcher":
        return
    if user.role == "driver" and user.vehicle_id == vehicle_id:
        return
    raise HTTPException(status_code=403, detail="Нет доступа к этому ТС")
