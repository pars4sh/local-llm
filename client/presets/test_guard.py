# Checks the installed preset guard (D-017): loads it with its stored policy inside the Open
# WebUI container and feeds it requests that ask for more than each preset may have.
#
# Usage: docker exec -i local-llm-open-webui-1 python3 - < client/presets/test_guard.py
import json
import sqlite3
import types

db = sqlite3.connect("/app/backend/data/webui.db")
content, valves = db.execute(
    "select content, valves from function where id='preset_guard'"
).fetchone()
mod = types.ModuleType("g")
exec(content, mod.__dict__)
f = mod.Filter()
f.valves = f.Valves(**json.loads(valves))
pol = json.loads(f.valves.policy)
web = pol["web_tools"]
priv_kb = pol["presets"]["private"]["knowledge_ids"][0]
res_kb = pol["presets"]["research"]["knowledge_ids"][0]


def body(model, **kw):
    b = {
        "model": model,
        "metadata": {
            "tool_ids": kw.get("tools", []),
            "tool_servers": [{"url": "x"}],
            "files": kw.get("files", []),
        },
        "tool_ids": kw.get("tools", []),
        "terminal_id": "sandbox",
        "features": {
            "web_search": True,
            "code_interpreter": True,
            "image_generation": True,
        },
        "files": kw.get("files", []),
    }
    return b


ok = True


def check(name, cond):
    global ok
    ok &= cond
    print(("PASS " if cond else "FAIL ") + name)


b = f.inlet(
    body(
        "research",
        tools=web,
        files=[
            {"type": "collection", "id": priv_kb},
            {"type": "collection", "id": res_kb},
            {"type": "file", "id": "f1"},
        ],
    ),
    {"id": "research"},
)
check("research: terminal dropped", "terminal_id" not in b)
check("research: code interpreter off", b["features"]["code_interpreter"] is False)
check("research: web search kept", b["features"]["web_search"] is True)
check(
    "research: its web tools kept",
    sorted(b["metadata"]["tool_ids"]) == sorted(pol["presets"]["research"]["tool_ids"]),
)
check(
    "research: Private KB dropped, own KB and file kept",
    [x["id"] for x in b["files"]] == [res_kb, "f1"],
)
check("research: browser tool servers dropped", b["metadata"]["tool_servers"] is None)
for p in ("code", "private", "quick", "figures"):
    b = f.inlet(body(p, tools=web), {"id": p})
    check(f"{p}: no web tools", b["metadata"]["tool_ids"] == [])
    check(f"{p}: web search off", b["features"]["web_search"] is False)
    check(f"{p}: code interpreter off", b["features"]["code_interpreter"] is False)
    check(
        f"{p}: terminal {'kept' if p == 'code' else 'dropped'}",
        ("terminal_id" in b) == (p == "code"),
    )
b = f.inlet(body("qwen-main", tools=web), {"id": "qwen-main"})
check(
    "bare model reading the web: terminal dropped",
    "terminal_id" not in b and b["features"]["code_interpreter"] is False,
)
b = f.inlet(
    {"model": "qwen-main", "metadata": {}, "terminal_id": "sandbox", "features": {}},
    {"id": "qwen-main"},
)
check(
    "bare model not reading the web: terminal kept", b.get("terminal_id") == "sandbox"
)
f.valves.policy = ""
try:
    f.inlet(body("research"), {"id": "research"})
    check("empty policy fails closed", False)
except Exception:
    check("empty policy fails closed", True)
print("ALL PASS" if ok else "SOME FAILED")
raise SystemExit(0 if ok else 1)
