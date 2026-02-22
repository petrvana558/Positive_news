import os
import bcrypt
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
from fastapi import Request, HTTPException
from fastapi.responses import RedirectResponse

SESSION_COOKIE = "pz_session"
SESSION_MAX_AGE = 60 * 60 * 8  # 8 hodin

_serializer: URLSafeTimedSerializer | None = None


def _get_serializer() -> URLSafeTimedSerializer:
    global _serializer
    if _serializer is None:
        secret = os.environ.get("SECRET_KEY", "fallback-secret-change-me")
        _serializer = URLSafeTimedSerializer(secret)
    return _serializer


def hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode(), bcrypt.gensalt()).decode()


def check_password(plain: str, hashed: str) -> bool:
    return bcrypt.checkpw(plain.encode(), hashed.encode())


def create_session_token() -> str:
    return _get_serializer().dumps({"authenticated": True})


def verify_session_token(token: str) -> bool:
    try:
        data = _get_serializer().loads(token, max_age=SESSION_MAX_AGE)
        return data.get("authenticated") is True
    except (BadSignature, SignatureExpired):
        return False


def is_authenticated(request: Request) -> bool:
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        return False
    return verify_session_token(token)


def require_auth(request: Request):
    """Dependency pro FastAPI – přesměruje na login pokud nepřihlášen."""
    if not is_authenticated(request):
        raise HTTPException(status_code=307, headers={"Location": "/admin/login"})


def get_admin_password_hash() -> str:
    """Vrátí hash hesla z env proměnné."""
    plain = os.environ.get("ADMIN_PASSWORD", "admin123")
    return hash_password(plain)


# Uchováme hash v paměti při startu (aby se nehashovalo při každém requestu)
_admin_hash: str | None = None


def verify_admin_password(plain: str) -> bool:
    global _admin_hash
    if _admin_hash is None:
        _admin_hash = get_admin_password_hash()
    plain_from_env = os.environ.get("ADMIN_PASSWORD", "admin123")
    return plain == plain_from_env
