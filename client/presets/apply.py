#!/usr/bin/env python3
"""Make Open WebUI match client/presets/presets.toml.

Creates or updates, by name or id, never deleting anything:
- the preset-guard filter (global, active) and its policy,
- the knowledge bases, uploading PDFs from KNOWLEDGE_DIR that a base doesn't have yet,
- the presets (Workspace models),
- the slash prompts,
- the folders and automations,
- and hides the embedding and reranking models from the model picker.

Usage: client/presets/apply.py [--run AUTOMATION_NAME]
  --run   also trigger that automation once, now (to test it).

Needs Python >= 3.11 and the stack running (client/compose.sh up -d) with an admin account. It
authenticates with a 15-minute token for the first admin, minted inside the Open WebUI container,
so no API key is needed and the secret key never leaves the container.
"""

import argparse
import json
import mimetypes
import os
import re
import subprocess
import sys
import time
import tomllib
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
COMPOSE = REPO / "client" / "compose.sh"
GUARD_ID = "preset_guard"

# Open WebUI 0.12 built-in tool groups (meta.builtinTools). Groups not listed in a preset's
# `builtins` are switched off for it.
BUILTIN_GROUPS = [
    "time", "user_input", "files", "knowledge", "chats", "subagents", "memory", "web_search",
    "image_generation", "code_interpreter", "notes", "channels", "skills", "tasks",
    "automations", "calendar", "notifications",
]  # fmt: skip

# Runs inside the Open WebUI container. The claims match a sign-in session token; the session
# stamp ties it to the account, so changing the admin password invalidates it like any session.
TOKEN_SCRIPT = """
import sqlite3
from datetime import timedelta
from open_webui.utils.auth import create_token
db = sqlite3.connect("/app/backend/data/webui.db")
row = db.execute(
    "SELECT u.id, a.session_stamp FROM user u JOIN auth a ON a.id = u.id"
    " WHERE u.role = 'admin' ORDER BY u.created_at LIMIT 1"
).fetchone()
if not row:
    raise SystemExit("no admin account yet: create one at the Open WebUI login page first")
claims = {"id": row[0], "typ": "session", "session_stamp": row[1]}
print(create_token(claims, timedelta(minutes=15)))
"""


def die(msg: str) -> None:
    print(f"apply: {msg}", file=sys.stderr)
    sys.exit(1)


def read_env(path: Path) -> dict[str, str]:
    env = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            env[key.strip()] = value.strip().strip("'\"")
    return env


def compose(*args: str) -> str:
    return subprocess.run(
        [str(COMPOSE), *args], check=True, capture_output=True, text=True
    ).stdout


class Api:
    def __init__(self, base: str, token: str):
        self.base = base
        self.token = token

    def call(
        self,
        method: str,
        path: str,
        body=None,
        *,
        data=None,
        content_type=None,
        ok404=False,
    ):
        headers = {"Authorization": f"Bearer {self.token}"}
        if body is not None:
            data = json.dumps(body).encode()
            content_type = "application/json"
        if content_type:
            headers["Content-Type"] = content_type
        req = urllib.request.Request(
            self.base + path, data=data, headers=headers, method=method
        )
        try:
            with urllib.request.urlopen(req, timeout=900) as resp:
                raw = resp.read()
        except urllib.error.HTTPError as e:
            if ok404 and e.code in (401, 404):
                return None
            die(
                f"{method} {path}: HTTP {e.code}: {e.read().decode(errors='replace')[:300]}"
            )
        return json.loads(raw) if raw else None

    def upload(self, path: Path) -> dict:
        boundary = uuid.uuid4().hex
        ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        data = b"".join(
            [
                f'--{boundary}\r\nContent-Disposition: form-data; name="file"; '
                f'filename="{path.name}"\r\nContent-Type: {ctype}\r\n\r\n'.encode(),
                path.read_bytes(),
                f"\r\n--{boundary}--\r\n".encode(),
            ]
        )
        return self.call(
            "POST",
            "/api/v1/files/?process=true&process_in_background=false",
            data=data,
            content_type=f"multipart/form-data; boundary={boundary}",
        )


def stack_settings() -> tuple[list[str], str]:
    """The MCP server ids and the terminal id, from the rendered compose config."""
    config = json.loads(compose("config", "--format", "json"))
    env = config["services"]["open-webui"]["environment"]
    servers = [
        c["info"]["id"]
        for c in json.loads(env["TOOL_SERVER_CONNECTIONS"])
        if c.get("type") == "mcp"
    ]
    terminal = json.loads(env["TERMINAL_SERVER_CONNECTIONS"])[0]["id"]
    return servers, terminal


def admin_token() -> str:
    container = compose("ps", "-q", "open-webui").strip()
    if not container:
        die("Open WebUI isn't running (client/compose.sh up -d)")
    out = subprocess.run(
        ["docker", "exec", "-i", container, "python3", "-"],
        input=TOKEN_SCRIPT,
        capture_output=True,
        text=True,
    )
    if out.returncode:
        die(f"could not mint a token: {out.stderr.strip().splitlines()[-1]}")
    return out.stdout.strip().splitlines()[-1]


# Some PDFs map their digits to private font glyphs, which text extraction turns into characters
# from U+0376-U+037F ("0.90" comes out as "Ͷ.ͿͶ"). Real papers hardly use that range, so a few
# of them mean the numbers are unreadable; full-page OCR in Docling reads them correctly.
GARBLED = re.compile("[\u0376-\u037f]")

OCR_SCRIPT = """
import sys, requests
pdf = sys.stdin.buffer.read()
r = requests.post(
    sys.argv[1] + "/v1/convert/file",
    files={"files": ("paper.pdf", pdf, "application/pdf")},
    data={"force_ocr": "true", "to_formats": "md"},
    timeout=1800,
)
r.raise_for_status()
sys.stdout.write(r.json()["document"]["md_content"])
"""


def reextract_if_garbled(api: Api, file_id: str, pdf: Path) -> None:
    content = api.call("GET", f"/api/v1/files/{file_id}/data/content")["content"]
    if len(GARBLED.findall(content)) < 5:
        return
    t0 = time.time()
    container = compose("ps", "-q", "open-webui").strip()
    docling = json.loads(compose("config", "--format", "json"))["services"][
        "open-webui"
    ]["environment"]["DOCLING_SERVER_URL"]
    out = subprocess.run(
        ["docker", "exec", "-i", container, "python3", "-c", OCR_SCRIPT, docling],
        input=pdf.read_bytes(),
        capture_output=True,
    )
    if out.returncode:
        print(f"knowledge: {pdf.name}: OCR re-extraction failed, kept the garbled text")
        return
    text = out.stdout.decode()
    if len(GARBLED.findall(text)) >= len(GARBLED.findall(content)):
        print(f"knowledge: {pdf.name}: OCR didn't help, kept the original text")
        return
    api.call("POST", f"/api/v1/files/{file_id}/data/content/update", {"content": text})
    print(
        f"knowledge: {pdf.name}: garbled digits, re-extracted with OCR ({time.time() - t0:.0f} s)"
    )


def apply_knowledge(
    api: Api, bases: list[dict], kb_dir: Path | None
) -> dict[str, dict]:
    existing = {}
    page = 1
    while True:
        res = api.call("GET", f"/api/v1/knowledge/?page={page}")
        existing.update({k["name"]: k for k in res["items"]})
        if len(existing) >= res["total"] or not res["items"]:
            break
        page += 1

    result = {}
    for base in bases:
        kb = existing.get(base["name"])
        form = {"name": base["name"], "description": base["description"]}
        if kb is None:
            kb = api.call("POST", "/api/v1/knowledge/create", form)
            print(f"knowledge: created {base['name']}")
        elif kb.get("description") != base["description"]:
            api.call("POST", f"/api/v1/knowledge/{kb['id']}/update", form)
        result[base["name"]] = kb

        folder = kb_dir / base["folder"] if kb_dir else None
        pdfs = sorted(folder.glob("*.pdf")) if folder and folder.is_dir() else []
        if not pdfs:
            print(
                f"knowledge: {base['name']}: no PDFs in {folder or 'KNOWLEDGE_DIR (unset)'}"
            )
            continue
        files = api.call("GET", f"/api/v1/knowledge/{kb['id']}/files")
        have = {
            (f.get("meta") or {}).get("name") or f.get("filename"): f["id"]
            for f in files.get("items", [])
        }
        for pdf in pdfs:
            file_id = have.get(pdf.name)
            if file_id is None:
                t0 = time.time()
                uploaded = api.upload(pdf)
                status = ((uploaded.get("data") or {}).get("status")) or "?"
                if status == "failed":
                    print(
                        f"knowledge: {base['name']}: {pdf.name}: extraction failed, skipped"
                    )
                    continue
                file_id = uploaded["id"]
                api.call(
                    "POST",
                    f"/api/v1/knowledge/{kb['id']}/file/add",
                    {"file_id": file_id},
                )
                print(
                    f"knowledge: {base['name']}: added {pdf.name} ({time.time() - t0:.0f} s)"
                )
            reextract_if_garbled(api, file_id, pdf)
    return result


def preset_meta(p: dict, kbs: dict[str, dict], terminal_id: str) -> dict:
    meta = {
        "description": p["description"],
        "capabilities": {
            "vision": p["vision"],
            "file_upload": True,
            "file_context": True,
            "web_search": p["web_search"],
            "terminal": p["terminal"],
            "code_interpreter": False,
            "image_generation": False,
            "memory": False,
            "builtin_tools": True,
            "citations": True,
            "status_updates": True,
            "usage": True,
        },
        "builtinTools": {g: g in p["builtins"] for g in BUILTIN_GROUPS},
        "toolIds": [f"server:mcp:{t}" for t in p["tools"]],
        "defaultFeatureIds": ["web_search"] if p["web_search"] else [],
        "knowledge": [
            {
                "id": kbs[n]["id"],
                "name": n,
                "description": kbs[n]["description"],
                "type": "collection",
            }
            for n in p["knowledge"]
        ],
        "tags": [{"name": "preset"}],
    }
    if p["terminal"]:
        meta["terminalId"] = terminal_id
    return meta


def apply_presets(
    api: Api, presets: list[dict], roles: dict[str, str], kbs: dict, terminal_id: str
) -> None:
    for p in presets:
        params = {
            "system": (HERE / "system" / p["system"]).read_text().strip(),
            "function_calling": "native",
        }
        if "thinking" in p:
            params["custom_params"] = {
                "chat_template_kwargs": {"enable_thinking": p["thinking"]}
            }
        form = {
            "id": p["id"],
            "base_model_id": roles[p["base"]],
            "name": p["name"],
            "meta": preset_meta(p, kbs, terminal_id),
            "params": params,
            "is_active": True,
        }
        if api.call("GET", f"/api/v1/models/model?id={p['id']}", ok404=True):
            api.call("POST", f"/api/v1/models/model/update?id={p['id']}", form)
            print(f"preset: updated {p['name']}")
        else:
            api.call("POST", "/api/v1/models/create", form)
            print(f"preset: created {p['name']}")


def hide_models(api: Api, model_ids: list[str]) -> None:
    """Keep the embedding and reranking models out of the model picker."""
    for mid in model_ids:
        form = {
            "id": mid,
            "base_model_id": None,
            "name": mid,
            "meta": {},
            "params": {},
            "is_active": False,
        }
        if api.call("GET", f"/api/v1/models/model?id={mid}", ok404=True):
            api.call("POST", f"/api/v1/models/model/update?id={mid}", form)
        else:
            api.call("POST", "/api/v1/models/create", form)


def apply_guard(api: Api, presets: list[dict], servers: list[str], kbs: dict) -> None:
    policy = {
        "web_tools": [f"server:mcp:{s}" for s in servers],
        "presets": {
            p["id"]: {
                "tool_ids": [f"server:mcp:{t}" for t in p["tools"]],
                "terminal": p["terminal"],
                "web_search": p["web_search"],
                "code_interpreter": False,
                "image_generation": False,
                "knowledge_ids": [kbs[n]["id"] for n in p["knowledge"]],
            }
            for p in presets
        },
    }
    form = {
        "id": GUARD_ID,
        "name": "Preset guard",
        "content": (HERE / "preset_guard.py").read_text(),
        "meta": {
            "description": "Keeps reading and acting tools in separate presets (D-017)."
        },
    }
    fn = api.call("GET", f"/api/v1/functions/id/{GUARD_ID}", ok404=True)
    if fn is None:
        fn = api.call("POST", "/api/v1/functions/create", form)
        print("guard: created")
    else:
        api.call("POST", f"/api/v1/functions/id/{GUARD_ID}/update", form)
    api.call(
        "POST",
        f"/api/v1/functions/id/{GUARD_ID}/valves/update",
        {"policy": json.dumps(policy)},
    )
    fn = api.call("GET", f"/api/v1/functions/id/{GUARD_ID}")
    if not fn["is_active"]:
        api.call("POST", f"/api/v1/functions/id/{GUARD_ID}/toggle")
    if not fn["is_global"]:
        api.call("POST", f"/api/v1/functions/id/{GUARD_ID}/toggle/global")
    print("guard: active, global")


def apply_prompts(api: Api, prompts: list[dict]) -> None:
    existing = {
        p["command"].lstrip("/"): p for p in api.call("GET", "/api/v1/prompts/")
    }
    for p in prompts:
        form = {
            "command": p["command"],
            "name": p["name"],
            "content": (HERE / "prompts" / f"{p['command']}.md").read_text().strip(),
        }
        if p["command"] in existing:
            api.call(
                "POST",
                f"/api/v1/prompts/id/{existing[p['command']]['id']}/update",
                form,
            )
        else:
            api.call("POST", "/api/v1/prompts/create", form)
            print(f"prompt: created /{p['command']}")


def folder_id(api: Api, name: str) -> str:
    for f in api.call("GET", "/api/v1/folders/"):
        if f["name"] == name and not f.get("parent_id"):
            return f["id"]
    print(f"folder: created {name}")
    return api.call("POST", "/api/v1/folders/", {"name": name})["id"]


def apply_automations(api: Api, automations: list[dict], run: str | None) -> None:
    res = api.call("GET", "/api/v1/automations/list")
    items = res if isinstance(res, list) else res.get("items", [])
    existing = {a["name"]: a for a in items}
    for a in automations:
        form = {
            "name": a["name"],
            "folder_id": folder_id(api, a["folder"]),
            "data": {
                "prompt": (HERE / "automations" / a["prompt"]).read_text().strip(),
                "model_id": a["preset"],
                "rrule": a["rrule"],
            },
            "is_active": True,
        }
        if a["name"] in existing:
            auto = api.call(
                "POST", f"/api/v1/automations/{existing[a['name']]['id']}/update", form
            )
        else:
            auto = api.call("POST", "/api/v1/automations/create", form)
            print(f"automation: created {a['name']}")
        nxt = (auto.get("next_runs") or [None])[0]
        when = (
            time.strftime("%Y-%m-%d %H:%M", time.localtime(nxt / 1e9)) if nxt else "?"
        )
        print(f"automation: {a['name']}: next run {when}")
        if run == a["name"]:
            api.call("POST", f"/api/v1/automations/{auto['id']}/run")
            print(f"automation: {a['name']}: started once")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--run", metavar="AUTOMATION_NAME", help="trigger this automation once now"
    )
    args = parser.parse_args()

    cfg = tomllib.loads((HERE / "presets.toml").read_text())
    env = read_env(REPO / ".env")
    sys.path.insert(0, str(REPO / "cluster"))
    import roster  # noqa: E402  (cluster/roster.py, stdlib only)

    client = tomllib.loads(roster.roster_path().read_text())["client"]
    servers, terminal_id = stack_settings()
    for p in cfg["preset"]:
        unknown = set(p["tools"]) - set(servers)
        if unknown:
            die(
                f"preset {p['id']}: unknown tool servers {sorted(unknown)}; see client/compose.yaml"
            )
        if p["base"] not in client:
            die(f"preset {p['id']}: base must be one of {sorted(client)}")

    kb_dir = (
        Path(os.path.expanduser(env["KNOWLEDGE_DIR"]))
        if env.get("KNOWLEDGE_DIR")
        else None
    )
    api = Api(f"http://127.0.0.1:{env.get('OPENWEBUI_PORT') or '3000'}", admin_token())
    kbs = apply_knowledge(api, cfg["knowledge"], kb_dir)
    apply_guard(api, cfg["preset"], servers, kbs)
    apply_presets(api, cfg["preset"], client, kbs, terminal_id)
    hide_models(api, [client["embedding_model"], client["reranking_model"]])
    apply_prompts(api, cfg["prompt"])
    apply_automations(api, cfg.get("automation", []), args.run)


if __name__ == "__main__":
    main()
