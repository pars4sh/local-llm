"""The gateway's ASGI app: LiteLLM's proxy app behind a route allowlist.

LiteLLM serves hundreds of routes, and dozens answer without a key (admin UI, SSO,
onboarding, /public/* catalogues that fetch from the internet, MCP, skills, OpenAPI docs).
With custom auth, LiteLLM also skips its own admin-route checks, so a client key could reach
admin routes. This gateway only needs inference and a liveness probe, so only those reach
LiteLLM. Every other HTTP route gets 404 and every WebSocket is refused, whatever the key.

Run by cluster/launch.py as `uvicorn local_llm_app:app` with CONFIG_FILE_PATH set; LiteLLM's
startup reads the config from there. Auth itself is in local_llm_gateway.py.
"""

from __future__ import annotations

import re

from litellm.proxy.proxy_server import app as litellm_app

# Inference routes (each needs a key) and the liveness probe (open).
ALLOWED = re.compile(
    r"/(?:v1/)?(?:chat/completions|completions|embeddings|rerank|models(?:/[^/]+)?)"
    r"|/v2/rerank"
    r"|/health/liveliness"
)
_NOT_FOUND = b'{"error": {"message": "not found", "type": "not_found"}}'
# Marks 404s from the allowlist, as opposed to LiteLLM's own (e.g. an unknown model).
BLOCKED_HEADER = "x-local-llm"


class RouteAllowlist:
    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        kind = scope["type"]
        if kind == "lifespan" or (kind == "http" and ALLOWED.fullmatch(scope["path"])):
            await self.app(scope, receive, send)
        elif kind == "websocket":
            await send({"type": "websocket.close", "code": 1008})
        else:
            await send(
                {
                    "type": "http.response.start",
                    "status": 404,
                    "headers": [
                        (b"content-type", b"application/json"),
                        (b"content-length", str(len(_NOT_FOUND)).encode()),
                        (BLOCKED_HEADER.encode(), b"route-not-allowed"),
                    ],
                }
            )
            await send({"type": "http.response.body", "body": _NOT_FOUND})


app = RouteAllowlist(litellm_app)
