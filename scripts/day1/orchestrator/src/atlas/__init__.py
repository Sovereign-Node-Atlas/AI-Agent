"""A.T.L.A.S. orchestrator package (docs/ATLAS_FRAMEWORK_REVIEW.md Section 17 Phase 2 step 2).

Modules written against scripts/day1/CONVENTIONS.md:
    config    settings and the config/ tree (engines, router rules, task forces, personas, domain cards)
    engines   EngineSpec helpers, the llama-server HTTP client, the systemd engine controller (§8 control path)
    arbiter   the Engine Arbiter, Section 4.2 rules 1-9
    ledger    the SQLite task ledger (tasks, arbiter/routing decisions, approvals, strikes, sentinel pulses)
    admin     the atlas-admin CLI
"""

from __future__ import annotations

__version__ = "0.1.0"

__all__ = ["__version__"]
