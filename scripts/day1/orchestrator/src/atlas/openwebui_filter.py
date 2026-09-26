"""
title: A.T.L.A.S. Router
author: ATLAS Day 1 (scripts/day1/orchestrator/src/atlas/openwebui_filter.py)
version: 0.1.0
description: Thin relay into the orchestrator (Section 7.1): forwards the override prefix and the vault flag.
"""

# Open WebUI Filter function (Section 7.1, 12.1; phase2/03-openwebui.sh installs it via POST /api/v1/functions/create).
#
# "It lives in the orchestrator; the Open WebUI Filter is a thin relay into it." This file therefore does NOT route,
# classify, inject prompts or touch memory. It does exactly three things in inlet() and nothing in outlet():
#   1. reads the leading override prefix of the current user message ("[REN]", "[ARTHUR:LONG]", "[DEEP THINK: ...]",
#      "[LOG STRIKE: ...]", "[VAULT]", ... the set is config/router-rules.json, owned by the orchestrator; the filter
#      forwards the bracketed token verbatim and never interprets it);
#   2. keeps the "vault" session flag: once "[VAULT]" has been seen in a chat, every later request of that chat carries
#      atlas_vault=true (Section 10.5: vault-tagged for the life of the session);
#   3. forwards both, plus the chat id as the session id and the user's id, as body fields the orchestrator understands
#      (atlas_override, atlas_vault, atlas_session, atlas_user) and mirrors them under body["metadata"]["atlas"].
# The body fields travel to POST /v1/chat/completions of the OpenAI-compatible backend (the orchestrator). UNVERIFIED:
# that Open WebUI 0.11.4 forwards unknown top-level body keys to the backend unchanged; the metadata mirror is the
# fallback the orchestrator also reads. Both are logged by the orchestrator, so a dropped field shows up on Day 1.
#
# Frontmatter rule (services-tools.md §1.4, VERIFIED utils/plugin.py): no `requirements:` line, nothing is pip-installed
# at load time (the node is offline). The class must be named `Filter` (type detection is hasattr(module, "Filter")).
# No `from atlas ...` import: Open WebUI executes this file's content on its own, outside the atlas package.

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, Field

# A leading bracketed token, e.g. "[REN]", "[ARTHUR:LONG]", "[DEEP THINK: the problem]", "[LOG STRIKE: name]".
# Only the token up to and including the first ']' is the prefix; the orchestrator parses what follows a colon.
_PREFIX_RE = re.compile(r"^\s*(\[[A-Z][A-Z0-9 _:.-]*(?::[^\]]*)?\])", re.IGNORECASE)
VAULT_TOKEN = "[VAULT]"


class Filter:
    class Valves(BaseModel):
        ORCHESTRATOR_URL: str = Field(
            default="http://127.0.0.1:8800",
            description="The orchestrator (informational: Open WebUI reaches it through "
            "OPENAI_API_BASE_URL; kept so the admin sees where prompts go).",
        )
        FORWARD_METADATA_MIRROR: bool = Field(
            default=True, description="Also mirror the fields under body.metadata.atlas."
        )
        priority: int = Field(default=0, description="Filter order (0 = first).")

    def __init__(self) -> None:
        self.valves = self.Valves()
        # Chats that have seen [VAULT] (Section 10.5). Per process; the orchestrator keeps its own copy too.
        self._vault_chats: set[str] = set()

    # --- helpers -----------------------------------------------------------------------------------------------------

    @staticmethod
    def _last_user_message(body: dict[str, Any]) -> str:
        for m in reversed(body.get("messages") or []):
            if isinstance(m, dict) and m.get("role") == "user":
                c = m.get("content")
                if isinstance(c, list):  # multimodal: text parts only
                    return " ".join(str(p.get("text", "")) for p in c if isinstance(p, dict))
                return str(c or "")
        return ""

    @staticmethod
    def read_prefix(text: str) -> str | None:
        m = _PREFIX_RE.match(text or "")
        return m.group(1).strip() if m else None

    @staticmethod
    def _chat_id(body: dict[str, Any], metadata: dict[str, Any] | None) -> str:
        meta = body.get("metadata") if isinstance(body.get("metadata"), dict) else {}
        for src in (metadata or {}, meta, body):
            cid = src.get("chat_id") or src.get("session_id")
            if cid:
                return str(cid)
        return ""

    # --- hooks -------------------------------------------------------------------------------------------------------

    async def inlet(
        self,
        body: dict[str, Any],
        __user__: dict[str, Any] | None = None,
        __metadata__: dict[str, Any] | None = None,
        __event_emitter__: Any = None,
    ) -> dict[str, Any]:
        text = self._last_user_message(body)
        prefix = self.read_prefix(text)
        chat_id = self._chat_id(body, __metadata__)
        if prefix and prefix.upper() == VAULT_TOKEN and chat_id:
            self._vault_chats.add(chat_id)
        vault = bool(chat_id and chat_id in self._vault_chats) or (prefix is not None and prefix.upper() == VAULT_TOKEN)
        fields: dict[str, Any] = {
            "atlas_override": prefix,
            "atlas_vault": vault,
            "atlas_session": chat_id or None,
            "atlas_user": str((__user__ or {}).get("id") or (__user__ or {}).get("email") or ""),
        }
        body.update(fields)
        if self.valves.FORWARD_METADATA_MIRROR:
            meta = body.get("metadata") if isinstance(body.get("metadata"), dict) else {}
            meta["atlas"] = dict(fields)
            body["metadata"] = meta
        return body

    async def outlet(self, body: dict[str, Any], __user__: dict[str, Any] | None = None) -> dict[str, Any]:
        # Thin relay: nothing is appended, rewritten or stored on the way back (Section 7.1).
        return body
