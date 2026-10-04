"""
title: A.T.L.A.S. Router
author: ATLAS Day 1 (scripts/day1/orchestrator/src/atlas/openwebui_filter.py)
version: 0.2.0
description: Thin relay into the orchestrator (Section 7.1): forwards the override prefix and the vault flag.
"""

# Open WebUI Filter function (Section 7.1, 12.1; phase2/03-openwebui.sh installs it via POST /api/v1/functions/create).
#
# "It lives in the orchestrator; the Open WebUI Filter is a thin relay into it." This file therefore does NOT route,
# classify, inject prompts or touch memory. It does exactly three things in inlet() and nothing in outlet():
#   1. reads the leading override prefix of the current user message ("[REN]", "[ARTHUR:LONG]", "[DEEP THINK: ...]",
#      "[LOG STRIKE: ...]", "[VAULT]", ... the set is config/router-rules.json, owned by the orchestrator; the filter
#      forwards the bracketed token verbatim and never interprets it);
#   2. sets the "vault" flag when "[VAULT]" appears in ANY user message of the chat (Section 10.5: vault-tagged for
#      the life of the session). The whole history Open WebUI resends with every turn is scanned (the same rule as
#      atlas.api.history_has_vault_prefix and atlas.tasks.retention.is_vault_chat: the token anywhere in the text,
#      fix round 2, fail-closed), so the flag survives a container restart or a reboot; the in-process set is only an
#      accelerator (fix round: the last-message-only rule lost the tag after a restart);
#   3. forwards both, plus the chat id as the session id and the user's id, as TOP-LEVEL body fields the orchestrator
#      understands (atlas_override, atlas_vault, atlas_session, atlas_user).
# VERIFIED against Open WebUI v0.11.4 (fix-round review of routers/openai.py, main.py, utils/middleware.py): top-level
# body keys survive to the backend (`payload = {**form_data}`, no key whitelist); `body["metadata"]` is Open WebUI's
# own dict and is popped before the backend call, so the earlier metadata mirror never reached the orchestrator and
# is gone. The orchestrator also reads the X-OpenWebUI-Chat-Id header (ENABLE_FORWARD_USER_INFO_HEADERS=true in the
# compose file, another writer) as a second channel for the session id.
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


def _text_of(content: Any) -> str:
    if isinstance(content, list):  # multimodal: text parts only
        return " ".join(str(p.get("text", "")) for p in content if isinstance(p, dict))
    return str(content or "")


class Filter:
    class Valves(BaseModel):
        ORCHESTRATOR_URL: str = Field(
            default="http://127.0.0.1:8800",
            description="The orchestrator (informational: Open WebUI reaches it through "
            "OPENAI_API_BASE_URL; kept so the admin sees where prompts go).",
        )
        priority: int = Field(default=0, description="Filter order (0 = first).")

    def __init__(self) -> None:
        self.valves = self.Valves()
        # Chats that have seen [VAULT] (Section 10.5): an accelerator only; the history scan is the rule.
        self._vault_chats: set[str] = set()

    # --- helpers -----------------------------------------------------------------------------------------------------

    @staticmethod
    def _last_user_message(body: dict[str, Any]) -> str:
        for m in reversed(body.get("messages") or []):
            if isinstance(m, dict) and m.get("role") == "user":
                return _text_of(m.get("content"))
        return ""

    @staticmethod
    def history_has_vault(body: dict[str, Any]) -> bool:
        """True when [VAULT] appears in any user message of the chat (the orchestrator's rule, fail-closed)."""
        for m in body.get("messages") or []:
            if isinstance(m, dict) and m.get("role") == "user" and VAULT_TOKEN in _text_of(m.get("content")).upper():
                return True
        return False

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
        vault = self.history_has_vault(body) or (prefix is not None and prefix.upper() == VAULT_TOKEN)
        if vault and chat_id:
            self._vault_chats.add(chat_id)
        vault = vault or bool(chat_id and chat_id in self._vault_chats)
        body.update(
            {
                "atlas_override": prefix,
                "atlas_vault": vault,
                "atlas_session": chat_id or None,
                "atlas_user": str((__user__ or {}).get("id") or (__user__ or {}).get("email") or ""),
            }
        )
        return body

    async def outlet(self, body: dict[str, Any], __user__: dict[str, Any] | None = None) -> dict[str, Any]:
        # Thin relay: nothing is appended, rewritten or stored on the way back (Section 7.1).
        return body
