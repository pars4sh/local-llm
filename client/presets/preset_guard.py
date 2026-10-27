"""
title: Preset guard
description: Keeps reading tools and acting tools apart (D-008, D-017). Installed and configured by client/presets/apply.py; edit the repository copy, not this one.
"""

# An Open WebUI global inlet filter. It runs before Open WebUI resolves tools, the terminal and
# the feature toggles of a request, so whatever the browser asks for, a preset gets only what
# client/presets/presets.toml grants it:
# - tool servers outside the preset's list are dropped,
# - the terminal is dropped unless the preset has it,
# - web search and the code interpreter are switched off unless the preset has them,
# - knowledge bases attached in the chat (#) are dropped unless the preset lists them,
# - tool servers sent directly by the browser are dropped.
# Models that are not presets (the bare roster models) keep their tools, except that a request
# that reads the web loses the terminal and the code interpreter.
#
# The filter fails closed: if the policy can't be read, it raises and the request fails.

import json

from pydantic import BaseModel, Field


class Filter:
    class Valves(BaseModel):
        policy: str = Field(
            default="", description="JSON written by client/presets/apply.py"
        )

    def __init__(self):
        self.valves = self.Valves()

    def inlet(
        self,
        body: dict,
        __model__: dict | None = None,
        __metadata__: dict | None = None,
    ) -> dict:
        policy = json.loads(self.valves.policy)
        metadata = body.get("metadata") or __metadata__ or {}
        model_id = (__model__ or {}).get("id") or body.get("model", "")
        preset = policy["presets"].get(model_id)
        web_tools = set(policy["web_tools"])

        features = body.get("features") or {}
        tool_ids = list(metadata.get("tool_ids") or body.get("tool_ids") or [])

        if preset is None:
            reads_web = features.get("web_search") or any(
                t in web_tools for t in tool_ids
            )
            if reads_web:
                body.pop("terminal_id", None)
                features["code_interpreter"] = False
                metadata["tool_servers"] = None
            body["features"] = features
            return body

        allowed = set(preset["tool_ids"])
        tool_ids = [t for t in tool_ids if t in allowed]
        metadata["tool_ids"] = tool_ids
        body["tool_ids"] = tool_ids
        metadata["tool_servers"] = None
        if not preset["terminal"]:
            body.pop("terminal_id", None)
        for feature in ("web_search", "code_interpreter", "image_generation"):
            if not preset[feature]:
                features[feature] = False
        body["features"] = features

        knowledge = set(preset["knowledge_ids"])
        for holder in (body, metadata):
            files = holder.get("files")
            if files:
                holder["files"] = [
                    f
                    for f in files
                    if not (
                        isinstance(f, dict)
                        and f.get("type") == "collection"
                        and f.get("id") not in knowledge
                    )
                ]
        return body
