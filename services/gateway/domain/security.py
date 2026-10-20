import hashlib
import hmac
import secrets
import time

import jwt
from fastapi import HTTPException


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def password_hash(password):
    salt = secrets.token_hex(16)
    key = hashlib.scrypt(password.encode(), salt=salt.encode(), n=16384, r=8, p=1)
    return f"scrypt${salt}${key.hex()}"


def password_matches(password, stored):
    try:
        _, salt, expected = stored.split("$")
        actual = hashlib.scrypt(password.encode(), salt=salt.encode(), n=16384, r=8, p=1).hex()
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False


def service_token(secret, audience, **claims):
    return jwt.encode(
        {
            **claims,
            "aud": audience,
            "iss": "dayzero-gateway",
            "exp": int(time.time()) + 120,
        },
        secret,
        algorithm="HS256",
    )


def verify_service(secret, token, audience):
    try:
        return jwt.decode(
            token,
            secret,
            algorithms=["HS256"],
            audience=audience,
            issuer="dayzero-gateway",
            options={"require": ["exp", "aud", "iss"]},
        )
    except jwt.PyJWTError:
        raise HTTPException(401, "서비스 인증이 필요합니다.") from None
