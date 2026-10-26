"""Write every route of the LiteLLM proxy app, after startup, as JSON to argv[1].

Run with the gateway's environment and CONFIG_FILE_PATH set, so startup registers the same
routes as the real gateway. LiteLLM's own GET /routes misses routes inside FastAPI's included
routers, so this walks them directly.
"""

import json
import sys

from fastapi.testclient import TestClient
from litellm.proxy.proxy_server import app


def flatten(routes, out: list) -> None:
    for r in routes:
        if type(r).__name__ == "_IncludedRouter":
            flatten(r.effective_candidates(), out)
            flatten(r.effective_low_priority_routes(), out)
            continue
        original = getattr(r, "original_route", r)
        kind = type(original).__name__
        methods = getattr(r, "methods", None) or getattr(original, "methods", None)
        if "WebSocket" in kind:
            out.append(["ws", r.path, []])
        elif kind == "Mount":
            out.append(["mount", r.path, ["GET"]])
        else:
            out.append(["http", r.path, sorted(methods or ["GET"])])


with TestClient(app):
    routes: list = []
    flatten(app.routes, routes)
with open(sys.argv[1], "w") as f:
    json.dump(routes, f)
