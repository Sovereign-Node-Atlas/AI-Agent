"""A.T.L.A.S. orchestrator package (docs/ATLAS_FRAMEWORK_REVIEW.md Section 17 Phase 2 step 2).

Modules written against scripts/day1/CONVENTIONS.md:
    config    settings and the config/ tree (engines, router rules, task forces, personas, domain cards)
    engines   EngineSpec helpers, the llama-server HTTP client, the systemd engine controller (§8 control path)
    arbiter   the Engine Arbiter, Section 4.2 rules 1-9
    ledger    the SQLite task ledger (tasks, arbiter/routing decisions, approvals, strikes, sentinel pulses)
    admin     the atlas-admin CLI
"""

from __future__ import annotations

import os

# Rule §7.1 (no telemetry from any installed component): chromadb-client posts a PostHog event on Client() unless
# ANONYMIZED_TELEMETRY=false, and huggingface_hub / several CLIs honour the other two. phase2/02-orchestrator.sh
# writes the same keys into orchestrator.env for the units; this covers every console script (atlas-admin, atlas-api,
# atlas-orchestrator) and every test run started without that file. The package FORCES the opt-out: the rule is
# absolute, so no unit drop-in, shell profile or stray `export ANONYMIZED_TELEMETRY=true` may re-enable a beacon
# (squid would deny it, but the attempt itself is contrary to §7.1).
TELEMETRY_OPT_OUT: dict[str, str] = {
    "ANONYMIZED_TELEMETRY": "false",
    "HF_HUB_DISABLE_TELEMETRY": "1",
    "DO_NOT_TRACK": "1",
}
os.environ.update(TELEMETRY_OPT_OUT)

__version__ = "0.1.0"

__all__ = ["TELEMETRY_OPT_OUT", "__version__"]
