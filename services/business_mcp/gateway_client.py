import os
import time

import httpx
import jwt
from mcp.server.fastmcp.exceptions import ToolError


def verified_context(ctx):
    secret = os.environ["AUTH_SECRET"]
    request = ctx.request_context.request
    token = request.headers.get("authorization", "").removeprefix("Bearer ") if request else ""
    try:
        claims = jwt.decode(
            token,
            secret,
            algorithms=["HS256"],
            audience="business-mcp",
            issuer="dayzero-gateway",
            options={"require": ["exp", "aud", "iss"]},
        )
    except jwt.PyJWTError:
        raise ToolError('{"code":"ACCESS_DENIED","retryable":false}') from None
    if (
        claims.get("actor_role") not in ("colleague", "leader", "orchestrator", "observer")
        or claims.get("scope") != "business"
    ):
        raise ToolError('{"code":"ACCESS_DENIED","retryable":false}')
    return claims


async def call_gateway(ctx, name, arguments):
    claims = verified_context(ctx)
    claims.update(aud="gateway", exp=int(time.time()) + 60)
    token = jwt.encode(claims, os.environ["AUTH_SECRET"], algorithm="HS256")
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.post(
                os.getenv("GATEWAY_URL", "http://127.0.0.1:8000") + "/internal/tool",
                headers={"Authorization": f"Bearer {token}"},
                json={"tool": name, "arguments": arguments},
            )
        if response.is_error:
            code = {
                401: "ACCESS_DENIED",
                403: "ACCESS_DENIED",
                404: "NOT_FOUND",
                409: "VERSION_CONFLICT",
                422: "INVALID_INPUT",
            }.get(response.status_code, "UPSTREAM_ERROR")
            raise ToolError(f'{{"code":"{code}","retryable":false}}')
        return response.json()
    except httpx.TimeoutException:
        raise ToolError('{"code":"TIMEOUT","retryable":true}') from None
    except httpx.HTTPError:
        raise ToolError('{"code":"UPSTREAM_UNAVAILABLE","retryable":true}') from None
