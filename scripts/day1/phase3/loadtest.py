#!/usr/bin/env python3
"""Phase 3 load tests: the measuring half of scripts/day1/phase3-models.sh (ATLAS_FRAMEWORK_REVIEW.md Section 17
Phase 3 steps 2-4; Section 21 V4, V10, V14b, V21, V22, V25; Section 4.2 rules 1-9; R19).

v0.4.0 (approved by the Principal 2026-10-16; register D-26 (c)): the Day 1 engines are gpt-oss-120b, its unchanged
abliterated twin, glm-5.3-flash and deepseek-v4-flash (S-34, D-42 (b)); one LLM seat is resident at a time (D-32 (a));
V10 records decode against the research projection and fails only below a per-seat floor the Principal sets (D-33
(c)); V21 is a single-residency proof (S-24); V22 covers every newer-architecture engine (S-26); V25 is the output
parity of the re-pin (S-02); the `quality` record (S-03) raises the KLD to-do.

Run as root by the driver. Stdout carries ONLY lines of the form
    RECORD<TAB><ID><TAB><pass|fail|deferred|info><TAB><message>
which the driver turns into verify.jsonl records with record_v, and (the `memory`, `quality` and `summarize`
subcommands, S52 / S-03 / D-33 (c))
    TODO<TAB><ID><TAB><title><TAB><detail>
which it raises with lib/common.sh todo_add; everything else goes to stderr (the unit's journal).
Exit status 0 means "measured and recorded" (a failed engine is a recorded fail, not an exit code); non-zero means an
infrastructure error the driver must stop on.

Subcommands (global options first, see main()):
    prepare                 unload every resident weight-bearing engine through the Arbiter (every answer granted:
                            its rule-5 check is the authority), poll the GTT counter ONCE against the earlier run's
                            baseline after the last one (a multi-resident counter is never attributed to a single
                            unload), settle, write baseline.json
    engine KEY [--previous PREV]
                            swap PREV out (unload + release check, credited to PREV) and KEY in through the Arbiter,
                            prove V4 from the journal, measure decode/prefill at 512 and 8k prompt tokens, register
                            the measured footprint with the Arbiter (rule 1), leave KEY resident. Every load also
                            records (S52, never a verdict) its context memory from the load log beside the Arbiter's
                            projection for the same profile, its host RAM after the load and after the tests, the
                            prompt-cache/checkpoint lines and whether context shift was switched off ("memory").
                            The Apex (kv_ladder) runs the engines.json ladder with a coherence prompt instead: f16
                            first, q8_0 only when f16 fails (S-42).
                            A result whose engine is NOT resident afterwards (not loaded, failed, the ladder) says
                            so in "resident_after": the driver reads it to know what the next swap releases.
    finish [--previous PREV]
                            unload the last resident engine and credit its release check
    summarize               print the V10 lines (one per engine, then the summary the gate reads), the V22 lines
                            (one per newer-architecture engine, then their summary) and the decode-floors to-do
                            while any seat has no floor (D-33 (c), D-47)
    residency --first KEY --second KEY
                            Section 17 step 3 (v0.4.0, S-24, D-32 (a)): the single-residency proof. FIRST is loaded
                            through the Arbiter, then SECOND: the Arbiter must evict FIRST (its unit stopped, the
                            ledger holds SECOND alone, the GTT delta is SECOND's alone within [0.85 x its weights,
                            the Arbiter's live budget]); two generation requests through the orchestrator to the
                            resident seat, the second proven to queue (V21). Slot save and restore is phased (S-35,
                            T-13). Then, with nothing resident, the real-engine refusal and Deep Think downgrade of
                            Section 21 V14 (V14b). A rule-5 leak fails V21 AND stops the phase (exit 2) after the
                            records are written, as step 02 does.
    parity                  S-02 (V25): the engines both llama.cpp builds load (the pin b11474 and the old pin
                            v0.4.1, built beside it by phase2/01-llama.sh) compared with llama-perplexity: the old
                            build writes the base logits of a fixed corpus, the new build scores them with
                            --kl-divergence, and both score a small deterministic task set (HellaSwag-format file
                            beside this script), flash attention on and off. Every seat is unloaded through the
                            Arbiter first; each run is followed by the rule-5 release poll.
    quality                 S-03: the KLD record of each Day 1 seat on the pin (perplexity of the fixed corpus and
                            the deterministic task set at the seat's own KV type), raised as the to-do kld-run: the
                            reference-based KLD run of T-03 needs reference files Day 1 does not pull.
    table                   the Section 17 step 4 table from the result files, plus every baseline_deviation
    memory                  S52 (end of Phase 3, step 04): the context memory, host-RAM caches and context-shift
                            records of every load (result files) plus the resident chat engines' current journals
                            (read-only), raised as the Principal's to-dos kv-estimates and cache-ram (TODO lines).
                            Records only: it never fails the phase (a parse miss is UNKNOWN)

Verdicts fixed by the baseline (the document wins over this file and over engines.json, CONVENTIONS.md preamble):
  * Section 21 V10 under D-33 option (c) (v0.4.0, S-25, B-04, B-06): load, generation at 512 and 8k tokens, swap and
    release gate the engine; the decode rate is recorded beside engines.json projected_decode_tok_s (a research
    projection, never measured on the PRO 495) and FAILS only below a non-null decode_floor_tok_s, the per-seat floor
    the Principal sets with the deep-tier floor (D-47). A null floor means record only, and `summarize` raises the
    to-do decode-floors. An entry that still carries a v0.3.5 expected_decode_tok_s band (the abliterated entry,
    left exactly as v0.3.5 has it) keeps the v0.3.5 rule: below the band's lower bound is a V10 fail, and it is
    left out of the to-do.
  * Section 23 S6 / an engines.json known_issue naming llama.cpp #20732 (Nemotron 3 Super, phased after Day 1): the
    8k prefill is followed by an alive check; a server that died is a V10 FAIL carrying the issue number in its
    message, never a hang and never a silent pass; alive with the request failed is a fail too. Every engine gets
    the alive check; only the message names the issue.
  * The Apex KV ladder (engines.json kv_ladder_rule, S-42, D-29 (a); replaces v0.3.5's descent to a q4_0 target):
    the HIGHEST-precision coherent rung that fits stands. f16 is loaded first at the entry's own profile; when it
    loads, proves its K/V lines and answers the coherence prompt it stands and nothing is written to overrides.json.
    q8_0 is tried only when f16 failed to load or was incoherent, and a coherent q8_0 is written to overrides.json.
    q4_0 is never a rung. No coherent rung: V4 and V22 deferred (R19), overrides cleared.
  * Section 21 V22 / R19 generalised (S-26, D-33 (c)): every engine whose support is newer than the rest has a
    deferrable row: the Apex (as before) and each engine whose min_llama_cpp_tag is newer than the parity build
    v0.4.1 (b10964), on Day 1 glm-5.3-flash (glm5-next, b11279). Its failure is recorded as V22 deferred, and its V4
    and V10 rows as deferred with the reason, so it never blocks the Phase 3 gate (its seats fall back to their
    D-40 fallback_engine). Each such engine gets a V22 line, then a V22 summary line, the one the gate reads.
  * Section 4.2 rule 3 for the requests this script sends straight to a llama-server port (POST /completion timing
    points, the coherence prompts, the solo timings): the driver stops every other path to a generation (the
    Sentinel/prune timers, atlas-celery-gpu, the atlas-openwebui container) for steps 02/03, and this script waits
    for the orchestrator's single generation slot to be free (GET /health "generating"/"generation_queue", api.py)
    before each such request and stops (Infra) when it is not free within GENERATION_IDLE_WAIT_S.

Result files: <results-dir>/<key>.json, one per engine (schema: EngineResult below), plus baseline.json,
residency.json (step 03), parity.json (step 02b, V25) and quality.json (step 02c, S-03). A result with "ok": true
AND "released": true makes the driver skip that engine on a re-run
(file-level resumability, CONVENTIONS.md §7.3; an engine saved while still resident is re-tested unless `prepare`
credited its release on the re-run). <results-dir>/pending-overrides.json journals every TEMPORARY overrides.json
change (ladder rungs, the V14b probe profile) before it is written; every subcommand
that touches the node restores and deletes it first, so an interrupted run never leaves a probe profile in production.
SIGTERM (`systemctl stop atlas-day1-phase3`) is turned into an exception in the main thread so every finally block runs.

Facts typed here and where they were VERIFIED (research/llama-cpp-vulkan.md, research/gguf-models.md, 2026-09-22;
the research files are cited through config/engines.json _meta where this checkout does not carry them; v0.4.0: every
llama.cpp fact below re-read in the source at the new pin b11474 = b9acf138 on 2026-10-16, line numbers at b11474):
  * GET /health is 503 while loading and 200 {"status":"ok"} once loaded (server README, server.cpp).
  * POST /completion and POST /v1/chat/completions carry a top-level "timings" object: cache_n, prompt_n, prompt_ms,
    prompt_per_second (prefill tok/s), predicted_n, predicted_ms, predicted_per_second (decode tok/s) (README).
  * POST /tokenize {"content": ...} -> {"tokens": [...]} (README).
  * The V4 proof line, one per attention sub-cache, in the unit's journal (research conflict h; llama-kv-cache.cpp):
        llama_kv_cache: size = ... K (q8_0): ... MiB, V (q8_0): ... MiB
    gpt-oss prints two (iswa), DeepSeek V4 four (dsv4), GLM-5.3-Flash two (glm5-next: the MLA attention cache, then
    the DSA indexer cache, src/llama-memory-hybrid-idx.cpp:68); hybrids also print
        llama_memory_recurrent: size = ... R (f32): ... S (f32): ...      (expected, never a failure)
    plus "llama_context: flash_attn = enabled"; hard-error lines that mean the request was refused (server exits):
    "Flash Attention not supported, set to disabled", "quantized V cache requires flash_attn",
    "does not support different K", "does not divide n_embd_head". No ^ anchor: --log-timestamps may prefix lines.
    The size and flash_attn lines are library INFO, which the pin logs as TRACE (common/log.cpp:535, since
    67b2b7f2f): they reach the journal only at -lv 4, which phase2/engine-env.py renders into every chat engine
    (--verbosity 4); at llama-server's default 3 the journal has none and V4 is unproven on every engine.
    Research conflict 11: llama.cpp errors out at context creation instead of falling back; the proof is the log line
    plus the process-alive/health check.
  * /props does not expose the KV cache types (README): when the journal has no proof line, V4 is a fail with reason.
  * GTT in use: /sys/class/drm/card*/device/mem_info_gtt_used, bytes (amdgpu_gtt_mgr.c); the Arbiter's counter.
  * Nemotron 3 Super: llama.cpp issue #20732, GPU memory fault at ~20k-token prompts on Vulkan/HIP (conflict f).
  * DeepSeek V4 and glm5-next (MLA): K and V types must be identical (src/llama-context.cpp:3907-3910), --parallel
    explicit (issue #26654), quantised KV on deepseek4 disputed (issues #25382, #26423; conflict b); kernel 7.x
    DeviceLost needs amdgpu.lockup_timeout (issue #25664).
  * --reasoning-format defaults to auto (VERIFIED, engines.json extra_args_rule): a thinking model's <think> block
    lands in message.reasoning_content and message.content holds only what follows it.
  # UNVERIFIED: the per-request "cache_prompt": false field of POST /completion — README field (not re-read in the
    research); a nonce in every prompt and a check that timings.cache_n == 0 make the measurement independent of it.
  # UNVERIFIED: that DeepSeek V4 Flash 0731's chat template emits a <think> block by default — the reviewer's reading
    of the 0731 template; handled by budget (1024/2048 tokens), finish_reason and one retry, never assumed.
    "chat_template_kwargs": {"thinking": false} is NOT sent (unverified for this template).

S52 Phase 3 items: records, never a verdict and never an Infra stop (a line that is not found is UNKNOWN). The log
lines, VERIFIED against llama.cpp b11474 (the pinned build, phase2/01-llama.sh LLAMA_CPP_COMMIT; re-read 2026-10-16,
every format string unchanged since b29c606, only the lines moved). llama-server logs with prefix and timestamps on
(common/common.cpp:394-395), "M.SS.mmm.uuu I " before a line, "W " before a warning, "D " before a debug line
(common/log.cpp:125-141; colours only on a tty, so the journal has none), so no pattern is anchored. Every library
line below (K/V, recurrent, DSV4, flash_attn, compute buffers, the mmproj's too: the server routes clip's log through
common_log_default_callback, tools/server/server-context.cpp:1275) is INFO, which the pin logs as TRACE
(common/log.cpp:535): at llama-server's default -lv 3 none reaches the journal, so phase2/engine-env.py renders every
chat engine with --verbosity 4 (exactly 4: see --fit below). Only the srv/slot INF and WRN lines print at -lv 3.
  * context memory, one line per K/V sub-cache (src/llama-kv-cache.cpp:301):
        "llama_kv_cache: size = %7.2f MiB (%6u cells, %3d layers, %2u/%u seqs), K (%s): %7.2f MiB, V (%s): %7.2f MiB"
    On gpt-oss "llama_kv_cache_iswa: creating non-SWA KV cache, size = %u cells" / "creating     SWA KV cache, ..."
    (src/llama-kv-cache-iswa.cpp:83, 100), on DeepSeek V4 those and "llama_kv_cache_dsv4: creating DSV4 CSA
    compressed|HCA compressed|lightning-indexer KV cache, size = %u cells" (src/llama-kv-cache-dsv4.cpp:1292-1308),
    on glm5-next "llama_memory_hybrid_idx: creating indexer KV cache, size = %u cells" (src/llama-memory-hybrid-idx.cpp:
    68, before the indexer's own size line; the MLA attention cache's line comes first, untagged), come just before
    the size line of the sub-cache they name. The hybrids' recurrent state (src/llama-memory-recurrent.cpp:140)
        "llama_memory_recurrent: size = %7.2f MiB (%6u cells, %3d layers, %2u seqs %2u rs_seq), R (%s): %7.2f MiB, ..."
    and DeepSeek V4's compressor states (src/llama-kv-cache-dsv4.cpp:1001)
        "llama_dsv4_comp_state: %s ratio = %u, state = %u x %u, streams = %u, rs_seq = %u, layers = %zu, size =
        %7.2f MiB" (one line in the log)
    are context memory too. Their sum is set beside the Arbiter's projection for the same profile (arbiter.py
    projected_footprint, run in the orchestrator's venv: see arbiter_projection).
  * compute buffers, per buffer type: "sched_reserve: %10s compute buffer size = %8.2f MiB" (src/llama-context.cpp:744;
    printed again whenever the scheduler re-reserves, so the largest per buffer is kept) and the mmproj's
    "reserve_compute_meta: ..." in the same format (tools/mtmd/clip.cpp:3822). --fit (on by default, common/common.h:
    481) builds a trial context first (common/fit.cpp:263, byte-identical to v0.4.1). Below -lv 5 its lines are demoted
    to debug and dropped (common/common.cpp:1267, common/fit.cpp:52), so at the rendered -lv 4 the journal has the
    served context's lines only; at -lv 5 or -v they keep INFO and print as "I " beside the real ones.
    parse_memory_lines therefore discards everything before --fit's closing TRACE line
    "common_fit_params: fitting params to free memory took" (common/fit.cpp:902, printed at every verbosity that
    shows the trial); the served context and the mmproj load after it (tools/server/server-context.cpp:1208, 1295),
    so nothing is counted twice.
  * "srv    load_model: initializing, n_slots = %d, n_ctx_slot = %d, kv_unified = '%s'" (tools/server/
    server-context.cpp:1383): the real slot window, logged AFTER the context-shift check, so a load log that has it
    covers that check.
  * context shift switched off (warnings, tools/server/server-context.cpp:1309 and 1321):
        "ctx_shift is not supported by multimodal, it will be disabled" / "... by this context, it will be disabled"
    Every chat engine starts with --context-shift (phase2/engine-env.py), so a load log with the slot line and no such
    warning means it stayed on. At b11474 only text-only glm5-next keeps it (RoPE type none); the glm5v mmproj turns it
    off on the Day 1 GLM unit, and deepseek4 (dsv4), gpt-oss (iSWA) and the qwen35 router (IMROPE) cannot shift.
  * host-RAM caches outside the Arbiter's budget: the prompt cache (--cache-ram, default 8192 MiB per server, idle slots
    copied into it on every new task: --cache-idle-slots on) and the context checkpoints (--ctx-checkpoints, default
    32 per slot, at least 8192 tokens apart) (common/common.h:634-638; flags common/arg.cpp:1700, 1718, 1734). B-33
    (measure 5, approved 2026-10-17): phase2/engine-env.py renders an explicit --cache-ram on every engine from
    engines.json cache_ram_mib, so the to-do cache-ram weighs the cap each env file's ARGS carry (8192 only where
    none is rendered) and is the Phase 3 measurement of whether to change it.
    Checkpoints are made only where the cache cannot roll back: sliding-window or recurrent layers
    (tools/server/server-context.cpp:3708-3721; glm5-next's KDA layers test as recurrent, so GLM makes them too,
    INFERRED). Their sizes are TRACE lines (verbosity 4): " - cache state: %zu prompts, %.3f MiB (limits: ...)"
    (tools/server/server-task.cpp:1905) and "created context checkpoint %d of %d (pos_min = ..., size = %.3f MiB)"
    (server-context.cpp:2573); llama-server's default verbosity is 3 (common/common.h:539; LOG_TRC common/log.h:122),
    which leaves only the warnings: an eviction from a full prompt cache (" - making room for prompt cache entry,
    removing oldest entry (size = %.3f MiB)", " - cache size limit reached, removing oldest entry (size = %.3f MiB)",
    server-task.cpp:1764, 1884; the token-limit and oversize forms at 1898 and 1743) and "erasing old context
    checkpoint (... size = %.3f MiB)" (server-context.cpp:2539). Both kinds are parsed; the TRACE lines are there
    because every chat engine runs at -lv 4 (phase2/engine-env.py), and a journal without them (an engine started
    before that render, a rotated journal) leaves the figure UNKNOWN. Phase 3 changes no engine setting, and it
    measures the RAM itself, read-only: VmRSS, VmHWM, RssAnon, RssFile and RssShmem of the unit's MainPID (systemctl
    show, /proc/<pid>/status) right after the load and after the tests. Both caches are heap (std::vector), so
    RssAnon's growth over the tests is their upper bound for that traffic (INFERRED).

CONTRACT with the orchestrator (orchestrator/src/atlas/api.py, another writer; read at fix-round time, every route
below exists there; CONVENTIONS.md §8 control path). Loopback only; admin routes take X-Atlas-Token when
ORCH_ADMIN_TOKEN_FILE is configured (--admin-token-file), else loopback is enough:
  * GET  {ORCH_URL}/health -> 200 when ready.
  * GET  {ORCH_URL}/arbiter/status -> 200 Arbiter.status(): gtt_used_bytes, budget_bytes, free_bytes,
         resident[{engine, projected_bytes, measured_bytes, ctx, parallel, kv_class, loading, unloading}], busy,
         generating {engine, task_id} | null, generation_queue [...], halted.
  * GET  {ORCH_URL}/health also carries "generating" (the holder of the single generation slot or null) and
         "generation_queue" (api.py health()); unauthenticated; the rule-3 idle check above reads it.
  * POST {ORCH_URL}/arbiter/load {"engine": KEY, "ctx": N, "parallel": N, "task_id": ...}
         -> 200 {"engine", "decision": "granted"|"queued"|"refused"|"error", "granted", "reason", "projected_bytes",
         "task_id"}. The Arbiter projects against the unit's env file (a ctx/parallel argument that disagrees with it
         is refused), plans LRU evictions (rule 3), waits load_wait_s behind a running generation (rules 4, 6), starts
         the unit through the sudoers path and returns granted only when it serves. 404 unknown engine, 503 halted
         after a rule-5 failure, 500 controller error. queued here means "still blocked after load_wait_s" (arbiter.py
         _plan_load: "arbiter busy: ...", "generation in progress on ...", "load of KEY already in progress"): the
         Arbiter's own wait IS the queue, so this script re-POSTs after QUEUED_RETRY_S (the ledger only tells it
         anything when another caller is loading this very engine, "already in progress", and then it is watched)
         until LOAD_TIMEOUT_S; refused is a real decision and fails the engine loudly.
  * POST {ORCH_URL}/arbiter/unload {"engine": KEY, "task_id": ...} -> the same shape; the rule-5 release check runs
         inside (503 when the counter did not drop: the Arbiter halts). Anything but 200/granted is a refusal: this
         script never stops a unit behind a serving Arbiter (Section 4.2, 9.3).
  * POST {ORCH_URL}/arbiter/register {"engine": KEY, "total_bytes": N, "task_id": ...} -> 200 (rule 1).
  * POST {ORCH_URL}/internal/v1/chat/completions {"model": <engine key>, "messages", "max_tokens", "stream": false}
         -> OpenAI shape; runs the internal pipeline through the Arbiter's load and generation lock (rules 3, 4);
         the llama-server "timings" object is passed through at the top level. /v1/chat/completions accepts engine
         keys too, but /internal is the route written for this script and is used here.
  * POST {ORCH_URL}/internal/deep-think/plan {"tier": "deep"|"standard"|"quick", "task_id"} -> {requested, granted,
         engines, required_bytes, budget_bytes, reason, resident_engine} (rule 8; the tier's largest engine is
         projected from its unit env file against the whole budget).
  There is NO systemctl fallback (fix round 3). Section 4.2 is a hard requirement ("every load and unload of any
  weight-bearing process passes" through the Arbiter) and Section 17 Phase 3 step 2 says "load through the Engine
  Arbiter": an orchestrator that is not serving is the missing Phase 2 step 2 prerequisite (CONVENTIONS.md §7.5) and an
  Infra error (§7.4). A unit that is active/activating but not yet 200 (Restart=on-failure, RestartSec=5, an
  ExecStartPre that waits up to 180 s for Redis) is waited for up to ORCH_WAIT_S, never worked around. When /health is
  200 but /arbiter/status or /arbiter/load|unload is missing, it REFUSES to run (Infra): engines are never controlled
  behind a serving orchestrator whose ledger would not see it. The admin header goes to /arbiter/* only; /internal/* is
  loopback-guarded and never sees the token.
"""

from __future__ import annotations

import argparse
import datetime as dt
import grp
import json
import os
import random
import re
import signal
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

GIB = 1024**3
GB = 10**9
# Section 4.2 rule 5: release confirmation belongs to the Arbiter (its own release_tolerance_bytes and
# release_timeout_s, not exposed by /arbiter/status). The two figures below are this script's MEASUREMENT window
# (the "released"/seconds columns of the Section 17 step 4 table), polled after EVERY unload and after every unit
# that went away on its own (a failed load, a server that died at 8k): the process exit is never trusted (rule 5).
# The Arbiter's granted/503 answer is the authority and both are reported.
RELEASE_TOLERANCE_BYTES = 2 * GIB
RELEASE_TIMEOUT_S = 120.0
LOAD_TIMEOUT_S = 960.0  # llama-server@.service TimeoutStartSec=900 plus margin
ARBITER_WAIT_S = 900.0  # api.py DEFAULT_LOAD_WAIT_S: how long /arbiter/load|unload may block before "queued"
STOP_TIMEOUT_S = 180.0  # TimeoutStopSec=120 plus margin
ORCH_UNIT = "atlas-orchestrator"
ORCH_WAIT_S = 300.0  # atlas-orchestrator.service: ExecStartPre waits up to 180 s for Redis, TimeoutStartSec=300
ORCH_TRANSIENT_STATES = ("active", "activating", "reloading", "deactivating")  # systemd ActiveState: wait, do not fail
PENDING_FILE = "pending-overrides.json"
ENGINE_ENV_GROUP = "atlas"  # engine-env.py installs root:atlas 640 (CONVENTIONS.md §2)
REQUEST_TIMEOUT_S = 1200.0  # an 8k prefill plus 128 decoded tokens on the slowest Day 1 seat (DeepSeek, ~7 tok/s)
PREFILL_SHORT = 512
PREFILL_LONG = 8192
N_PREDICT = 128
# Section 21 V10 under D-33 option (c) (v0.4.0; module docstring "Verdicts"): decode is recorded beside engines.json
# projected_decode_tok_s and fails only below a non-null decode_floor_tok_s (the Principal's per-seat floor, D-47).
QUEUED_RETRY_S = 15.0  # re-POST interval after an Arbiter "queued" answer (its own load_wait_s is the real queue)
SETTLE_DELTA_BYTES = 256 * 1024 * 1024  # two GTT readings 10 s apart closer than this = the counter has settled
GENERATION_IDLE_WAIT_S = 600.0  # Section 4.2 rule 3 belt: how long a direct request waits for the generation slot
# V21 (v0.4.0, S-24): the single-residency window for the GTT delta once the second seat is loaded and the first has
# been evicted (residency_bounds): at least RESIDENCY_LOW_FACTOR of the SECOND seat's weights (exact bytes, D-31), at
# most its weights plus the cache allowance plus allocator slack, CAPPED at the Arbiter's live budget. Any first seat
# left beside a 146-151 GiB second one would overflow the budget cap (D-32: no pair fits), so the cap is what proves
# one seat; the ledger and the first unit's state prove it a second way.
RESIDENCY_CACHE_BYTES = 28 * GB
RESIDENCY_SLACK_BYTES = 10 * GB  # allocator overhead on top of the caches before the delta is out of tolerance
RESIDENCY_LOW_FACTOR = 0.85  # less than this fraction of the weights on the counter = the seat is not resident
# Fallback only, when /arbiter/status carries no budget_bytes (the Arbiter has not measured its resident set, and then
# it grants nothing). 170e9 bytes = 158.3 GiB, the v0.3.4 "~170 GB" figure, ABOVE the ~156 GiB Section 4.1 expects since
# v0.4.0 (the Qwen3 pair on the CPU): not a stricter bound, and harmless only because it applies when the Arbiter
# grants nothing, so no load this script makes is measured against it.
DEFAULT_BUDGET_BYTES = 170 * GB
TASK_ID = "phase3-loadtest"
UNIT_PREFIX = "llama-server@"
P3_CLASSES = {"core", "apex"}  # S-34 (v0.4.0): the Day 1 LLM seats; the vision and cross-check classes are retired
ORCH_CHAT_PATH = "/internal/v1/chat/completions"
LLAMA_CHAT_PATH = "/v1/chat/completions"
ADMIN_TOKEN_HEADER = "X-Atlas-Token"
LOOPBACK_HOSTS = ("127.0.0.1", "localhost", "::1")
NEMOTRON_ISSUE_ID = "#20732"  # Section 23 S6: an engines.json known_issue that names it (Nemotron 3 Super, phased)
NEMOTRON_ISSUE = f"llama.cpp issue {NEMOTRON_ISSUE_ID} (Vulkan/HIP memory fault at ~20k-token prompts)"
DEVICELOST_ISSUE = "llama.cpp issue #25664 (kernel 7.x amdgpu lockup_timeout; Phase 1 GRUB line)"
DEEPSEEK_KV_ISSUES = "llama.cpp issues #25382/#26423"
COHERENCE_TOKENS_FACTUAL = 1024
COHERENCE_TOKENS_SUMMARY = 2048
COHERENCE_RETRY_FACTOR = 4
V14B_MAX_DOUBLINGS = 8
# S-26 / S-02: llama.cpp v0.4.1, the old pin built beside the new one for V25 only, is build b10964. An engine whose
# min_llama_cpp_tag is newer cannot load there: it is a "newer architecture" (V22 row, R19 generalised) and V25
# cannot cover it.
PARITY_PIN_BUILD = 10964
DEEP_THINK_GENERATOR = "glm-5.3-flash"  # atlas/arbiter.py DEEP_THINK_GENERATOR: the V14b probe of choice (both tiers)
DECODE_FLOOR_TODO_ID = "decode-floors"
KLD_TODO_ID = "kld-run"
# v0.4.0: the inputs an approved row leaves to the Principal, each built with its stated default and raised as a to-do
# (raise_input_todos, from summarize in step 02, beside decode-floors); only the Principal closes them.
CONTEXT_TARGETS_TODO_ID = "context-targets"  # D-16
TRIAL_TASK_SET_TODO_ID = "trial-task-set"  # D-46
SEAT_TEST_SETS_TODO_ID = "seat-test-sets"  # D-49
SEAT_PROFILES_TODO_ID = "seat-profiles"  # D-48, S-30, T-04
# S-02 (V25) and S-03: llama-perplexity runs. A 512-token window over 16 chunks of the fixed corpus (about 8k tokens,
# the domain profiles under config/domains, present on the node) keeps the base-logits file near 1-2 GB per engine; the
# deterministic task set is the HellaSwag-format file beside this script (6 lines per task: context, gold index, four
# endings; tools/perplexity/perplexity.cpp hellaswag_score, identical at both pins), scored by log-likelihood, so no
# sampling and no chat template is involved and both builds see byte-identical input. The file holds 24 commonsense
# tasks written for this project (no third-party data; six per gold index), no comments and no blank lines (the tool
# refuses a line count that is not a multiple of 6), and --hellaswag-tasks 1000 above the count takes every task. The
# tool ALWAYS shuffles them (perplexity.cpp: randomize_tasks = true unconditionally, std::mt19937 seeded 1, a
# uniform_int_distribution draw per task; VERIFIED at both pins), so the per-task answers are in that shuffled order,
# not file order: identical on both builds only because they share the node's toolchain (libstdc++ defines the
# distribution), which is all V25's comparison needs. The records say so (HS_ANSWERS_ORDER); never read them by line.
HS_ANSWERS_ORDER = "perplexity.cpp shuffle (std::mt19937 seed 1, the node's libstdc++), not file order"
PARITY_CTX = 512
PARITY_CHUNKS = 16
PARITY_TASK_CTX = 2048
PARITY_FA = ("on", "off")  # production renders -fa on (phase2/engine-env.py); off is the #28956 exposure check
PARITY_TOOL_TIMEOUT_S = 3600.0
# V25 thresholds at -fa on, proposed as INFERRED (no published build-to-build figure exists) and confirmed by the
# Principal on 2026-10-17 (doc S55): two builds scoring the SAME file should agree far closer than a KV quantisation
# does (q8_0 K/V is task-lossless at a mean KLD of 0.0018, register D-29), so a mean KLD above 0.01 or fewer than
# 98 % identical top-1 tokens, or fewer correct tasks on the new build, is a changed output. -fa off is recorded beside
# it, never a verdict: the old pin's non-FA Vulkan path carries the KV-view bug fixed in #28956, so a difference there
# is the old build's.
PARITY_MAX_MEAN_KLD = 0.01
PARITY_MIN_SAME_TOP_PCT = 98.0
PARITY_TASKS_FILE = Path(__file__).resolve().with_name("parity-tasks.txt")

KV_LINE_RE = re.compile(r"llama_kv_cache: size = .*K \((\w+)\): .*V \((\w+)\):")
RECURRENT_RE = re.compile(r"llama_memory_recurrent: size = .*R \((\w+)\): .*S \((\w+)\):")
FA_RE = re.compile(r"llama_context: flash_attn\s*= (\w+)")
KV_REFUSED = (
    "Flash Attention not supported, set to disabled",
    "quantized V cache requires flash_attn",
    "does not support different K",
    "does not divide n_embd_head",
)
EXTERNAL_STOP_MARKS = ("Received SIGTERM", "Stopping ", "Deactivated successfully", "Stopped ")

# S52 Phase 3 items (module docstring: the pinned format strings these follow, file and line). Records only.
MIB = 1024**2
DEBUG_LINE_RE = re.compile(r"^\s*\d+\.\d{2}\.\d{3}\.\d{3} D ")  # common/log.cpp:125-141: a debug line
# common/fit.cpp:902, LOG_TRC after every --fit run: the K/V and compute lines before it were the trial context's
# (printed at -lv 5 and -v, where common/common.cpp:1267 keeps them at INFO), never the served one's.
FIT_END_RE = re.compile(r"common_fit_params: fitting params to free memory took")
KV_SIZE_RE = re.compile(r"llama_kv_cache: size = +([0-9.]+) MiB \( *(\d+) cells, +(\d+) layers, +(\d+)/(\d+) seqs\), "
                        r"K \((\w+)\): +[0-9.]+ MiB, V \((\w+)\): +[0-9.]+ MiB")
ISWA_PART_RE = re.compile(r"llama_kv_cache_iswa: creating +(non-SWA|SWA) KV cache, size = \d+ cells")
DSV4_PART_RE = re.compile(r"llama_kv_cache_dsv4: creating DSV4 (.+?) KV cache, size = \d+ cells")
IDX_PART_RE = re.compile(r"llama_memory_hybrid_idx: creating indexer KV cache, size = \d+ cells")  # glm5-next
RECURRENT_SIZE_RE = re.compile(r"llama_memory_recurrent: size = +([0-9.]+) MiB \( *(\d+) cells, +(\d+) layers")
DSV4_STATE_RE = re.compile(r"llama_dsv4_comp_state: (\w+) ratio = \d+, state = \d+ x \d+, streams = (\d+), "
                           r"rs_seq = \d+, layers = \d+, size = +([0-9.]+) MiB")
COMPUTE_RE = re.compile(r"(sched_reserve|reserve_compute_meta): +(\S+) compute buffer size = +([0-9.]+) MiB")
SLOTS_RE = re.compile(r"initializing, n_slots = (\d+), n_ctx_slot = (\d+), kv_unified = '(\w+)'")
CTX_SHIFT_OFF_RE = re.compile(r"ctx_shift is not supported by (this context|multimodal), it will be disabled")
PCACHE_EVICT_RE = re.compile(r" - (?:making room for prompt cache entry|cache size limit reached|cache token limit "
                             r"\(\d+, est: \d+\) reached), removing oldest entry \(size = ([0-9.]+) MiB\)")
PCACHE_SKIP_RE = re.compile(r" - prompt state size ([0-9.]+) MiB exceeds cache size limit ([0-9.]+) MiB, skipping")
PCACHE_REDUCED_RE = re.compile(r" - cache size limit reduced to ([0-9.]+) MiB")
PCACHE_STATE_RE = re.compile(r" - cache state: (\d+) prompts, ([0-9.]+) MiB \(limits: ([0-9.]+) MiB")  # TRACE
CKPT_ERASED_RE = re.compile(r"erasing old context checkpoint \(pos_min = -?\d+, pos_max = -?\d+, n_tokens = -?\d+, "
                            r"size = ([0-9.]+) MiB\)")
CKPT_CREATED_RE = re.compile(r"created context checkpoint (\d+) of (\d+) \(pos_min = -?\d+, pos_max = -?\d+, "
                             r"n_tokens = -?\d+, size = ([0-9.]+) MiB\)")  # TRACE
# llama-server's defaults at the pin (common/common.h:634-638 at b11474): the figures the cache-ram to-do weighs. The
# 8192 MiB cache applies only to a server whose ARGS carry no --cache-ram (B-33: engine-env.py renders one everywhere).
LLAMA_CACHE_RAM_DEFAULT_MIB = 8192
CACHE_RAM_FLAGS = ("--cache-ram", "-cram")  # common/arg.cpp:1719 at b11474
LLAMA_CTX_CHECKPOINTS_DEFAULT = 32
LLAMA_CHECKPOINT_MIN_STEP = 8192
PROC_STATUS_FIELDS = ("VmRSS", "VmHWM", "RssAnon", "RssFile", "RssShmem")
PROJECTION_TIMEOUT_S = 60.0
# Runs in the orchestrator's venv (the atlas package the live Arbiter imports; phase2/02-orchestrator.sh leaves both
# trees root:atlas without group or other write, so running it as root runs root-owned code). Nothing measured is
# registered with this throw-away Arbiter, so projected_footprint returns its ESTIMATE for the profile given: the
# figure the kv-estimates to-do weighs against the log. No controller and no probe are touched by a projection.
PROJECTION_SNIPPET = """
import json, sys
from pathlib import Path
from atlas.arbiter import KV_BYTES_PER_TOKEN_F16, KV_CLASS_FACTOR, Arbiter
from atlas.config import load_engines
cfg, key, ctx, parallel, kv = Path(sys.argv[1]), sys.argv[2], int(sys.argv[3]), int(sys.argv[4]), sys.argv[5]
engines = load_engines(cfg)
fp = Arbiter(engines, None, None).projected_footprint(key, ctx, parallel, kv)
spec = engines[key]
per_token = spec.kv_bytes_per_token_f16
if per_token is None:
    per_token = KV_BYTES_PER_TOKEN_F16.get(key)
print(json.dumps({"weights_bytes": fp.weights_bytes, "kv_bytes": fp.kv_bytes, "total_bytes": fp.total_bytes,
                  "measured": fp.measured, "per_token_f16": per_token, "kv_factor": KV_CLASS_FACTOR.get(kv),
                  "arbiter_class": spec.arbiter_class}))
"""

WORDS = (
    "atlas harbour ledger granite meadow copper lantern orbit velvet marble quartz ember willow falcon cinder timber "
    "saddle beacon canyon anchor prairie thistle summit glacier compass vellum saffron pewter juniper tundra barley "
    "mortar plaster gable rafter lintel keystone terrace paddock furrow hedgerow estuary fjord delta moraine scree "
    "basalt schist gneiss feldspar mica pumice obsidian gypsum shale loam silt clay peat humus lichen sphagnum sedge "
    "heron kestrel osprey plover curlew wren linnet finch grouse teal widgeon gannet petrel shearwater kittiwake "
    "clarinet oboe viola cello timpani marimba celesta bassoon piccolo cornet euphonium tuba harp lute zither "
    "ledgerline quaver crotchet minim semibreve cadence coda fugue motet canon rondo sonata toccata partita chaconne"
).split()


# --- logging and records ----------------------------------------------------------------------------------------------


def log(msg: str) -> None:
    ts = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"{ts} [phase3] loadtest {msg}", file=sys.stderr, flush=True)


def record(vid: str, result: str, msg: str) -> None:
    """One verify record for the driver (record_v). Tabs and newlines would break the line protocol."""
    clean = " ".join(str(msg).split())
    print(f"RECORD\t{vid}\t{result}\t{clean}", flush=True)


def now_iso() -> str:
    return dt.datetime.now(dt.UTC).isoformat(timespec="seconds")


def short(obj: Any, n: int = 300) -> str:
    text = obj if isinstance(obj, str) else json.dumps(obj, default=str)
    return " ".join(text.split())[:n]


class Infra(RuntimeError):
    """An infrastructure error: the driver stops the phase (exit non-zero)."""


class Terminated(Infra):
    """SIGTERM (`systemctl stop atlas-day1-phase3`, the documented way to abort): raised in the main thread by the
    handler installed in main() so every `finally` (override restores, pending journal) runs before exit 143."""


def _on_sigterm(signum: int, _frame: Any) -> None:
    raise Terminated(f"signal {signum} (SIGTERM, e.g. systemctl stop atlas-day1-phase3): stopping, restoring temporary "
                     "overrides; re-run to resume")


# --- HTTP (loopback only, never through the allowlist proxy) ----------------------------------------------------------

_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


class HttpFail(RuntimeError):
    def __init__(self, msg: str, status: int = 0, body: str = "") -> None:
        super().__init__(msg)
        self.status = status
        self.body = body


def http(method: str, url: str, body: dict[str, Any] | None = None, timeout: float = 30.0,
         headers: dict[str, str] | None = None) -> tuple[int, Any]:
    """Returns (status, parsed JSON or text). Raises HttpFail on transport errors; HTTP errors are returned."""
    data = None
    hdrs = {"Accept": "application/json", **(headers or {})}
    if body is not None:
        data = json.dumps(body).encode()
        hdrs["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, method=method, headers=hdrs)
    try:
        with _OPENER.open(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", "replace")
            status = resp.status
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", "replace") if exc.fp else ""
        status = exc.code
    except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as exc:
        raise HttpFail(f"{method} {url}: {type(exc).__name__}: {exc}") from exc
    try:
        return status, json.loads(raw) if raw else None
    except json.JSONDecodeError:
        return status, raw


def health_code(port: int) -> int:
    try:
        status, _ = http("GET", f"http://127.0.0.1:{port}/health", timeout=5)
    except HttpFail:
        return 0
    return status


def require_loopback(url: str) -> None:
    """CONVENTIONS.md §8: the orchestrator binds 127.0.0.1; engine control and generation never leave the host."""
    host = urllib.parse.urlparse(url).hostname
    if host not in LOOPBACK_HOSTS:
        raise Infra(f"--orch-url must be loopback (CONVENTIONS.md §8), got {url!r}")


# --- sysfs GTT counter (llama-cpp-vulkan.md §5.2) ---------------------------------------------------------------------


def drm_root() -> Path:
    """ATLAS_DRM_ROOT is a test-only hook (CONVENTIONS.md §4: node paths are overridable for tests). On the node
    (ATLAS_ETC is the real /etc/atlas) it is refused: the GTT counter is the rule-5 measurement behind every
    'released' verdict and the V21 proof, and a fake tree must never reach it through the environment."""
    override = os.environ.get("ATLAS_DRM_ROOT")
    # Canonical compare: "/etc/atlas/" or "/etc//atlas" still point at the real config (fix round 3, minor).
    if override and os.path.realpath(os.environ.get("ATLAS_ETC", "/etc/atlas")) == "/etc/atlas":
        raise Infra("ATLAS_DRM_ROOT is a test-only hook; unset it on the node (ATLAS_ETC is /etc/atlas)")
    return Path(override or "/sys/class/drm")


def gpu_device_dir() -> Path:
    root = drm_root()
    for c in sorted(root.glob("card*")):
        if not re.fullmatch(r"card\d+", c.name):
            continue
        vendor = c / "device" / "vendor"
        try:
            if vendor.read_text().strip() == "0x1002":
                return c / "device"
        except OSError:
            continue
    raise Infra(f"no AMD GPU (vendor 0x1002) under {root}")


def gtt_used_bytes() -> int:
    return int((gpu_device_dir() / "mem_info_gtt_used").read_text().strip())


def gtt_total_bytes() -> int:
    return int((gpu_device_dir() / "mem_info_gtt_total").read_text().strip())


def fmt_gb(b: float) -> str:
    return f"{b / GB:.1f} GB"


# --- systemd helpers --------------------------------------------------------------------------------------------------


def run(cmd: list[str], timeout: float = 60.0) -> subprocess.CompletedProcess[str]:
    """A timed-out command comes back as exit 124 with the reason in stderr (callers treat non-zero as a recorded
    failure), never as an uncaught TimeoutExpired traceback that would stop the whole phase."""
    try:
        return subprocess.run(cmd, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=timeout,
                              check=False)
    except subprocess.TimeoutExpired as exc:
        out = exc.stdout.decode("utf-8", "replace") if isinstance(exc.stdout, bytes) else (exc.stdout or "")
        return subprocess.CompletedProcess(cmd, 124, out, f"{cmd[0]} timed out after {timeout:.0f}s")


def orchestrator_state() -> str:
    """systemd ActiveState of atlas-orchestrator.service ("unknown" when systemctl itself fails)."""
    p = run(["systemctl", "show", "-p", "ActiveState", "--value", ORCH_UNIT])
    return p.stdout.strip() or "unknown"


def unit_of(key: str) -> str:
    return f"{UNIT_PREFIX}{key}"


def unit_active(key: str) -> bool:
    return run(["systemctl", "is-active", "--quiet", unit_of(key)]).returncode == 0


def unit_invocation_id(key: str) -> str:
    p = run(["systemctl", "show", "-p", "InvocationID", "--value", unit_of(key)])
    return p.stdout.strip()


def journal_lines(key: str, invocation_id: str, since: str) -> list[str]:
    """Log lines of the current invocation of llama-server@KEY (stderr goes to the journal)."""
    lines: list[str] = []
    if invocation_id:
        p = run(["journalctl", "--no-pager", "-o", "cat", f"_SYSTEMD_INVOCATION_ID={invocation_id}"], timeout=120)
        lines = p.stdout.splitlines()
    if not lines:
        p = run(["journalctl", "--no-pager", "-o", "cat", "-u", unit_of(key), "--since", since], timeout=120)
        lines = p.stdout.splitlines()
    return lines


def journal_tail(key: str, n: int = 40) -> str:
    p = run(["journalctl", "--no-pager", "-o", "cat", "-u", unit_of(key), "-n", str(n)], timeout=60)
    return p.stdout.strip()


def stopped_externally(tail: str) -> bool:
    """True when the journal tail shows systemd stopping the unit (an orchestrator restart stops every unit it does
    not own, api.py startup) rather than the server dying on its own: recorded as 'stopped externally', never as a
    GPU fault, and still a failure so the engine is re-tested on the next run."""
    return any(mark in tail for mark in EXTERNAL_STOP_MARKS)


# --- configuration ----------------------------------------------------------------------------------------------------


def read_env(path: Path) -> dict[str, str]:
    """KEY=value / KEY='value' lines as written by phase2/engine-env.py."""
    out: dict[str, str] = {}
    if not path.is_file():
        raise Infra(f"{path} missing: phase2/engine-env.py has not rendered this engine (Phase 2 step 1)")
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        v = v.strip()
        if len(v) >= 2 and v[0] == v[-1] and v[0] in "'\"":
            v = v[1:-1]
        out[k.strip()] = v
    return out


def read_admin_token(path: str | None) -> str:
    """ORCH_ADMIN_TOKEN_FILE (api.py): `ORCH_ADMIN_TOKEN=...` or the bare token. Never logged."""
    if not path:
        return ""
    p = Path(path)
    if not p.is_file():
        raise Infra(f"--admin-token-file {path} is not readable; the orchestrator's admin routes need it")
    for raw in p.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("ORCH_ADMIN_TOKEN="):
            line = line.split("=", 1)[1].strip().strip("'\"")
        return line
    raise Infra(f"--admin-token-file {path} is empty")


@dataclass
class Ctx:
    engines_path: Path
    env_dir: Path
    results_dir: Path
    orch_url: str
    engine_env: Path
    models_dir: Path
    slots_dir: Path
    port_base: int
    overrides: Path
    admin_token: str = ""
    atlas_python: str = ""  # the orchestrator venv's python: the Arbiter's projection for the S52 memory record
    engines: dict[str, dict[str, Any]] = field(default_factory=dict)
    order: list[str] = field(default_factory=list)

    def load_engines(self) -> None:
        data = json.loads(self.engines_path.read_text(encoding="utf-8"))
        for e in data["engines"]:
            self.engines[e["key"]] = e
            self.order.append(e["key"])

    def spec(self, key: str) -> dict[str, Any]:
        if key not in self.engines:
            raise Infra(f"{key} is not in {self.engines_path}")
        return self.engines[key]

    def env(self, key: str) -> dict[str, str]:
        return read_env(self.env_dir / f"{key}.env")

    def port(self, key: str) -> int:
        env = self.env(key)
        try:
            return int(env["LLAMA_ARG_PORT"])
        except (KeyError, ValueError) as exc:
            raise Infra(f"{key}.env has no LLAMA_ARG_PORT") from exc

    def render(self, key: str, set_ov: Sequence[tuple[str, str]] = (), clear_ov: Sequence[str] = ()) -> dict[str, str]:
        """Write overrides through engine-env.py (its contract) and re-render this engine's env file."""
        cmd = [sys.executable, str(self.engine_env), "--engines", str(self.engines_path), "--out", str(self.env_dir),
               "--models-dir", str(self.models_dir), "--slots-dir", str(self.slots_dir),
               "--port-base", str(self.port_base), "--overrides", str(self.overrides), "--key", key]
        for fld, val in set_ov:
            cmd += ["--set-override", key, fld, val]
        for fld in clear_ov:
            cmd += ["--clear-override", key, fld]
        p = run(cmd, timeout=120)
        if p.returncode != 0:
            raise Infra(f"engine-env.py failed for {key}: {(p.stderr or p.stdout).strip()[:600]}")
        path = self.env_dir / f"{key}.env"
        # Owner/mode belt, failing loudly like the driver's _p3_render_envs (rule §7.4): engine-env.py's install()
        # swallows a failed chown because the tests run unprivileged; on the node (root) it must hold.
        try:
            os.chmod(path, 0o640)
        except OSError as exc:
            raise Infra(f"could not set mode 640 on {path}: {exc}") from exc
        if os.geteuid() == 0:
            try:
                want_gid = grp.getgrnam(ENGINE_ENV_GROUP).gr_gid
            except KeyError as exc:
                raise Infra(f"group {ENGINE_ENV_GROUP!r} does not exist (Phase 1 creates it)") from exc
            st = path.stat()
            if (st.st_uid, st.st_gid) != (0, want_gid):
                try:
                    os.chown(path, 0, want_gid)
                except OSError as exc:
                    raise Infra(f"could not set root:{ENGINE_ENV_GROUP} on {path}: {exc}") from exc
        return self.env(key)

    def override_of(self, key: str, fld: str) -> Any:
        """The current overrides.json value of one field (None when absent), so a temporary override can be restored."""
        if not self.overrides.is_file():
            return None
        try:
            data = json.loads(self.overrides.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise Infra(f"{self.overrides} is not valid JSON: {exc}") from exc
        return (data.get(key) or {}).get(fld)

    def result_path(self, key: str) -> Path:
        return self.results_dir / f"{key}.json"

    def core_keys(self) -> list[str]:
        return [k for k in self.order if self.engines[k].get("arbiter_class") in P3_CLASSES]


# --- results ----------------------------------------------------------------------------------------------------------


@dataclass
class EngineResult:
    key: str
    arbiter_class: str = ""
    control_mode: str = ""
    kv_requested: str = ""
    kv_applied: str = ""
    kv_proof_ok: bool = False
    kv_proof_msg: str = ""
    load_ok: bool = False
    load_error: str = ""
    load_s: float | None = None
    swap_s: float | None = None
    swap_note: str = ""
    generated: bool = False
    decode_tps_512: float | None = None
    decode_tps_8k: float | None = None
    prefill_tps_512: float | None = None
    prefill_tps_8k: float | None = None
    prefill_8k_note: str = ""
    crashed_at_8k: bool = False
    known_issue_note: str = ""  # the Section 23 S6 note with the issue number (#20732) recorded BESIDE the V10 fail
    stopped_externally: bool = False
    band_note: str = ""
    band_fail: str = ""
    footprint_bytes: int | None = None
    footprint_note: str = ""
    released: bool | None = None
    release_s: float | None = None
    release_note: str = ""
    warnings: list[str] = field(default_factory=list)
    ladder: list[dict[str, Any]] = field(default_factory=list)
    memory: list[dict[str, Any]] = field(default_factory=list)  # S52: one record per load (per rung on the ladder)
    baseline_deviation: str = ""
    v22_result: str = ""
    v22_msg: str = ""
    resident_after: bool = False
    ok: bool = False
    tested_at: str = ""

    def warn(self, msg: str) -> None:
        if msg not in self.warnings:
            self.warnings.append(msg)

    def save(self, ctx: Ctx) -> None:
        ctx.results_dir.mkdir(parents=True, exist_ok=True)
        p = ctx.result_path(self.key)
        p.with_suffix(".tmp").write_text(json.dumps(asdict(self), indent=2) + "\n", encoding="utf-8")
        os.replace(p.with_suffix(".tmp"), p)


def load_result(ctx: Ctx, key: str) -> EngineResult | None:
    p = ctx.result_path(key)
    if not p.is_file():
        return None
    data = json.loads(p.read_text(encoding="utf-8"))
    res = EngineResult(key=key)
    for k, v in data.items():
        if hasattr(res, k):
            setattr(res, k, v)
    return res


def read_baseline_optional(ctx: Ctx) -> int | None:
    p = ctx.results_dir / "baseline.json"
    if not p.is_file():
        return None
    return int(json.loads(p.read_text(encoding="utf-8"))["gtt_used_bytes"])


def read_baseline(ctx: Ctx) -> int:
    b = read_baseline_optional(ctx)
    if b is None:
        raise Infra(f"{ctx.results_dir / 'baseline.json'} missing: run `loadtest.py prepare` first (the driver does)")
    return b


# --- temporary overrides journal (CONVENTIONS.md §7.3 resumable, §7.4) ------------------------------------------------


def pending_path(ctx: Ctx) -> Path:
    return ctx.results_dir / PENDING_FILE


def pending_read(ctx: Ctx) -> list[dict[str, Any]]:
    p = pending_path(ctx)
    if not p.is_file():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise Infra(f"{p} is not valid JSON: {exc}; the temporary overrides it journals must be restored by hand "
                    f"(engine-env.py --clear-override) before re-running") from exc
    return data if isinstance(data, list) else []


def pending_write(ctx: Ctx, entries: list[dict[str, Any]]) -> None:
    p = pending_path(ctx)
    if not entries:
        p.unlink(missing_ok=True)
        return
    ctx.results_dir.mkdir(parents=True, exist_ok=True)
    p.with_suffix(".tmp").write_text(json.dumps(entries, indent=2) + "\n", encoding="utf-8")
    os.replace(p.with_suffix(".tmp"), p)


def pending_begin(ctx: Ctx, key: str, fields: Sequence[str], why: str, clear_always: Sequence[str] = (),
                  ) -> dict[str, Any]:
    """Journal a TEMPORARY overrides.json change BEFORE it is written: the prior value of every field (restored on
    end), or "clear" for the fields in clear_always (never a legitimate persistent override). A SIGKILL, OOM or power
    cut between the render and its restore then cannot leave a probe profile (a 256x ctx_size, an f16 ladder rung) in
    a production unit env file: pending_recover() restores it at the start of the next invocation. (S-22, v0.4.0: the
    co-resident profile and its overrides field are gone, so nothing here journals or clears `coresident`.)"""
    entry: dict[str, Any] = {"id": f"{key}:{why}:{now_iso()}:{random.randrange(10**6)}", "key": key, "why": why,
                             "set": {}, "clear": []}
    for f in fields:
        prior = None if f in clear_always else ctx.override_of(key, f)
        if prior is None:
            entry["clear"].append(f)
        else:
            entry["set"][f] = prior
    entries = pending_read(ctx)
    entries.append(entry)
    pending_write(ctx, entries)
    return entry


def pending_restore(ctx: Ctx, entry: dict[str, Any]) -> dict[str, str]:
    set_ov = [(f, str(v)) for f, v in (entry.get("set") or {}).items()]
    return ctx.render(str(entry["key"]), set_ov, list(entry.get("clear") or []))


def pending_end(ctx: Ctx, entry: dict[str, Any]) -> None:
    entries = [e for e in pending_read(ctx) if e.get("id") != entry.get("id")]
    pending_write(ctx, entries)


def pending_recover(ctx: Ctx) -> None:
    """Called first by every subcommand that touches the node: restore what an interrupted run left behind, loudly."""
    entries = pending_read(ctx)
    if not entries:
        return
    for entry in reversed(entries):
        log(f"RECOVERY: {entry.get('key')} still carries the temporary override of an interrupted run "
            f"({entry.get('why')}; id {entry.get('id')}): restoring set={entry.get('set')} clear={entry.get('clear')}")
        env = pending_restore(ctx, entry)
        log(f"RECOVERY: {entry.get('key')} env restored (kv {env.get('ATLAS_KV_TYPE')}, ctx "
            f"{env.get('ATLAS_CTX_SIZE')}, parallel {env.get('ATLAS_PARALLEL')})")
    pending_path(ctx).unlink(missing_ok=True)


# --- engine control: the orchestrator's Arbiter API (no fallback) -----------------------------------------------------


class Control:
    """Every load and unload goes through the Arbiter (Section 4.2, a hard requirement: "every load and unload of any
    weight-bearing process passes" through it; Section 17 Phase 3 step 2 "load through the Engine Arbiter"). There is
    NO systemctl fallback (fix round 3): an orchestrator that is not serving is the missing Phase 2 step 2 prerequisite
    (CONVENTIONS.md §7.5) and an Infra error (§7.4), never a reason to start units behind the ledger's back (api.py's
    startup would stop them as unowned, and V4/V10 would be recorded from loads the Arbiter never saw). A unit that is
    active/activating but not yet 200 (Restart=on-failure, RestartSec=5, an ExecStartPre that waits for Redis) is
    waited for up to ORCH_WAIT_S. A serving orchestrator whose Arbiter API is missing is an Infra error too."""

    mode = "orchestrator"  # the only mode; kept as a field because every result file and the step 4 table print it

    def __init__(self, orch_url: str, admin_token: str = "") -> None:
        self.orch_url = orch_url.rstrip("/")
        self.last_decision: dict[str, Any] = {}
        self.headers = {ADMIN_TOKEN_HEADER: admin_token} if admin_token else {}
        self._probe()

    # -- probe --
    def _health(self) -> tuple[int, str]:
        try:
            code, body = http("GET", f"{self.orch_url}/health", timeout=5)
        except HttpFail as exc:
            return 0, str(exc)
        return code, short(body, 160)

    def _probe(self) -> None:
        code, detail = self._health()
        state = orchestrator_state()
        if code != 200 and state in ORCH_TRANSIENT_STATES:
            log(f"control: {ORCH_UNIT} is {state} and GET /health answered {code or 'nothing'} ({detail}); waiting up "
                f"to {ORCH_WAIT_S:.0f}s for it to serve (Restart=on-failure; ExecStartPre waits for Redis)")
            deadline = time.monotonic() + ORCH_WAIT_S
            while code != 200 and time.monotonic() < deadline:
                time.sleep(5)
                code, detail = self._health()
                state = orchestrator_state()
                if state not in ORCH_TRANSIENT_STATES:
                    break
        if code != 200:
            raise Infra(f"{ORCH_UNIT} is not serving (unit {state}; GET {self.orch_url}/health -> "
                        f"{code or 'unreachable'}: {detail}): Phase 2 step 2 prerequisite (CONVENTIONS.md §7.5). Every "
                        "load passes through the Engine Arbiter (Section 4.2); there is no systemctl fallback. "
                        f"systemctl start {ORCH_UNIT}, then re-run")
        code, body = self._get("/arbiter/status")
        if code in (401, 403):
            raise Infra(f"GET /arbiter/status answered {code}: the orchestrator's admin routes need X-Atlas-Token "
                        "(pass --admin-token-file, the ORCH_ADMIN_TOKEN_FILE of orchestrator.env)")
        if code != 200 or not isinstance(body, dict):
            raise Infra(f"orchestrator {self.orch_url} is serving (/health 200) but GET /arbiter/status answered "
                        f"{code}: refusing to control engines behind it (Section 4.2); implement /arbiter/* (api.py)")
        for route in ("/arbiter/load", "/arbiter/unload", "/arbiter/register"):
            # FastAPI answers 405 for a GET on a POST-only route that exists and 404 when the route is absent.
            rc, _ = self._get(route)
            if rc == 404:
                raise Infra(f"orchestrator {self.orch_url} is serving without POST {route} (api.py); V10/V14b/V21 "
                            "cannot be proved behind it: implement the route")
        halted = body.get("halted")
        if halted:
            raise Infra(f"the Arbiter is halted ({halted}): every load is refused until a human looks (Section 4.2 "
                        f"rule 5); restart {ORCH_UNIT}.service after checking the GTT counter")
        log(f"control: orchestrator Arbiter API at {self.orch_url} (unit {state}; resident: "
            f"{[r.get('engine') for r in body.get('resident', [])]}, budget {fmt_gb(body.get('budget_bytes') or 0)})")

    def _hdr(self, path: str) -> dict[str, str]:
        """The admin credential goes to /arbiter/* only; /internal/* is loopback-guarded (api.py) and never needs it."""
        return self.headers if path.startswith("/arbiter/") else {}

    def _get(self, path: str) -> tuple[int, Any]:
        try:
            return http("GET", f"{self.orch_url}{path}", timeout=10, headers=self._hdr(path))
        except HttpFail as exc:
            raise Infra(f"GET {path} failed while the orchestrator answered /health: {exc}") from exc

    def post(self, path: str, body: dict[str, Any], timeout: float) -> tuple[int, Any]:
        """A POST to an admin/internal route; transport errors surface as HttpFail for the caller to record."""
        return http("POST", f"{self.orch_url}{path}", body, timeout=timeout, headers=self._hdr(path))

    def status(self) -> dict[str, Any] | None:
        try:
            code, body = http("GET", f"{self.orch_url}/arbiter/status", timeout=10, headers=self.headers)
        except HttpFail:
            return None
        return body if code == 200 and isinstance(body, dict) else None

    def resident_keys(self) -> list[str]:
        st = self.status() or {}
        return [str(r.get("engine")) for r in st.get("resident", [])
                if isinstance(r, dict) and not r.get("loading") and not r.get("unloading")]

    def _wait_resident(self, key: str, deadline: float) -> bool:
        """Rule 4/6: a queued load waits its turn; the ledger says when the engine serves."""
        while time.monotonic() < deadline:
            if key in self.resident_keys():
                return True
            time.sleep(5)
        return False

    # -- load --
    def load(self, key: str, ctx_size: int, parallel: int, port: int) -> float:
        """Load through the Arbiter and wait for /health 200. Returns the load time in seconds. Raises RuntimeError with
        the reason on failure (the caller records it); Infra only for broken tooling."""
        t0 = time.monotonic()
        body = {"engine": key, "ctx": ctx_size, "parallel": parallel, "task_id": TASK_ID}
        deadline = t0 + ARBITER_WAIT_S + LOAD_TIMEOUT_S
        while True:
            try:
                code, resp = self.post("/arbiter/load", body, timeout=ARBITER_WAIT_S + LOAD_TIMEOUT_S + 60)
            except HttpFail as exc:
                raise RuntimeError(f"POST /arbiter/load {key} failed: {exc} (no systemctl fallback: Section 4.2)"
                                   ) from exc
            if code == 503:
                raise RuntimeError(f"Arbiter halted, {key} not loaded: HTTP 503 {short(resp)} (Section 4.2 rule 5)")
            if code != 200 or not isinstance(resp, dict):
                raise RuntimeError(f"Arbiter did not load {key}: HTTP {code} {short(resp)}")
            self.last_decision = resp
            decision = resp.get("decision")
            reason = resp.get("reason", "")
            if decision == "granted":
                log(f"control: Arbiter granted {key}: {reason} "
                    f"(projected {fmt_gb(resp.get('projected_bytes') or 0)})")
                break
            if decision == "queued":
                # Rules 4 and 6: never a failure. api.py blocks load_wait_s inside the Arbiter's own queue and answers
                # queued as "retry later" (arbiter.py _plan_load: busy, a generating victim); nothing in the ledger
                # changes until the retry, so the retry re-enters that queue after a short sleep. Only "load of KEY
                # already in progress" (another caller loading this very engine) is a ledger wait.
                if time.monotonic() > deadline:
                    raise RuntimeError(f"Arbiter kept {key} queued for {deadline - t0:.0f}s: {reason}")
                if "already in progress" in reason:
                    log(f"control: Arbiter queued {key}: {reason}; watching the ledger for it to serve")
                    if self._wait_resident(key, min(deadline, time.monotonic() + 300)):
                        break
                else:
                    log(f"control: Arbiter queued {key}: {reason}; asking again in {QUEUED_RETRY_S:.0f}s")
                    time.sleep(QUEUED_RETRY_S)
                continue
            raise RuntimeError(f"Arbiter {decision or 'answered without a decision'} for {key}: {reason} "
                               f"({short(resp, 200)})")
        health_deadline = time.monotonic() + LOAD_TIMEOUT_S
        while time.monotonic() < health_deadline:
            if health_code(port) == 200:
                secs = time.monotonic() - t0
                log(f"control: {key} healthy on 127.0.0.1:{port} after {secs:.1f}s (via the Arbiter)")
                return secs
            if not unit_active(key) and time.monotonic() - t0 > 10:
                raise RuntimeError(f"{unit_of(key)} is not active after the grant; journal: "
                                   f"{journal_tail(key, 20)[-800:]}")
            time.sleep(2)
        raise RuntimeError(f"{key} did not answer /health 200 within {LOAD_TIMEOUT_S:.0f}s")

    # -- unload --
    def unload(self, key: str) -> str:
        """Unload through the Arbiter (its rule-5 check inside). Returns the Arbiter's reason. Raises RuntimeError on
        any answer but granted: a refusal means the engine is busy (Section 9.3: never swapped out from under a
        session) and this script never stops a unit behind a serving Arbiter."""
        try:
            code, resp = self.post("/arbiter/unload", {"engine": key, "task_id": TASK_ID},
                                   timeout=ARBITER_WAIT_S + STOP_TIMEOUT_S + 60)
        except HttpFail as exc:
            raise RuntimeError(f"POST /arbiter/unload {key} failed: {exc}") from exc
        if code == 503:
            raise RuntimeError(f"Arbiter halted after unloading {key}: HTTP 503 {short(resp)} (Section 4.2 rule "
                               f"5: the GTT counter did not drop; restart {ORCH_UNIT}.service after looking)")
        if code != 200 or not isinstance(resp, dict):
            raise RuntimeError(f"Arbiter refused to unload {key}: HTTP {code} {short(resp, 200)}")
        self.last_decision = resp
        if resp.get("decision") != "granted":
            raise RuntimeError(f"Arbiter did not unload {key}: {resp.get('decision')}: {resp.get('reason', '')}")
        reason = str(resp.get("reason", ""))
        if unit_active(key) and "not resident" in reason:
            raise RuntimeError(f"{unit_of(key)} is active but the Arbiter does not list it ({reason}); a unit "
                               "started outside the Arbiter is never stopped behind it: stop it by hand or "
                               f"restart {ORCH_UNIT}.service (api.py startup stops unowned units)")
        log(f"control: Arbiter unloaded {key}: {reason}")
        return reason

    # -- register (rule 1) --
    def register(self, key: str, total_bytes: int) -> str:
        """POST /arbiter/register; returns "" on success or the reason it was not registered (the caller warns)."""
        try:
            code, resp = self.post("/arbiter/register", {"engine": key, "total_bytes": int(total_bytes),
                                                         "task_id": TASK_ID}, timeout=30)
        except HttpFail as exc:
            return f"POST /arbiter/register failed: {exc}"
        if code != 200:
            return f"POST /arbiter/register answered {code}: {short(resp, 200)}"
        return ""


def wait_release(baseline: int, label: str) -> tuple[bool, float, str]:
    """The measurement window of Section 4.2 rule 5 (see RELEASE_TOLERANCE_BYTES): poll the GTT counter until it
    is back within tolerance of the baseline."""
    t0 = time.monotonic()
    last = gtt_used_bytes()
    while True:
        last = gtt_used_bytes()
        if last <= baseline + RELEASE_TOLERANCE_BYTES:
            secs = time.monotonic() - t0
            note = f"GTT {fmt_gb(last)} (baseline {fmt_gb(baseline)}) after {secs:.0f}s"
            log(f"release {label}: {note}")
            return True, secs, note
        if time.monotonic() - t0 > RELEASE_TIMEOUT_S:
            note = (f"GTT still {fmt_gb(last)} after {RELEASE_TIMEOUT_S:.0f}s, baseline {fmt_gb(baseline)} "
                    f"+ {RELEASE_TOLERANCE_BYTES / GIB:.0f} GiB tolerance")
            log(f"release {label}: FAIL {note}")
            return False, time.monotonic() - t0, note
        time.sleep(2)


def settle_counter() -> int:
    """The GTT counter once it stopped moving: two readings 10 s apart within SETTLE_DELTA_BYTES (or RELEASE_TIMEOUT_S
    elapsed), so a baseline is never taken mid-release."""
    deadline = time.monotonic() + RELEASE_TIMEOUT_S
    prev = gtt_used_bytes()
    while True:
        time.sleep(10)
        cur = gtt_used_bytes()
        if abs(cur - prev) < SETTLE_DELTA_BYTES or time.monotonic() > deadline:
            return cur
        prev = cur


def clear_residents(ctl: Control, keys: Sequence[str], baseline: int | None, who: str,
                    ) -> tuple[bool, float, str, int]:
    """Unload several resident engines through the Arbiter and measure the release ONCE, against BASELINE, after the
    last one: with two engines resident the counter cannot be back at the baseline after the first unload, so a
    per-engine poll against it would report a leak that does not exist (fix round, major). Every /arbiter/unload must
    answer granted (its own rule-5 check is the authority; a refusal or 503 is an Infra error here: nothing is stopped
    behind the Arbiter). When the measurement window against BASELINE fails although every unload was granted, the
    baseline is treated as stale (the resident set grew since it was taken: voice containers, a desktop session, a
    Phase 2 model) and the settled counter is reported instead of a false leak.
    Returns (window_ok, seconds, note, settled_bytes); window_ok is False only in that stale-baseline case."""
    t0 = time.monotonic()
    reasons: list[str] = []
    for k in keys:
        log(f"{who}: {k} is resident; unloading it through the Arbiter")
        try:
            reasons.append(f"{k}: {ctl.unload(k)}")
        except RuntimeError as exc:
            raise Infra(f"{who}: cannot clear the resident engines: {exc}") from exc
    if not keys:
        return True, 0.0, "nothing was resident", settle_counter()
    label = "+".join(keys)
    if baseline is None:
        settled = settle_counter()
        return True, time.monotonic() - t0, (f"Arbiter release check passed for {label} ({'; '.join(reasons)}); no "
                                             f"earlier baseline to poll against; settled at {fmt_gb(settled)}"), settled
    ok, secs, note = wait_release(baseline, label)
    settled = settle_counter()
    if ok:
        return True, secs, f"{note} (measured once after {label}); Arbiter: {'; '.join(reasons)}", settled
    log(f"{who}: the counter did not come back to the earlier baseline {fmt_gb(baseline)} but the Arbiter's own "
        f"release check passed for {label}: treating the baseline as stale (settled at {fmt_gb(settled)})")
    return False, secs, (f"Arbiter release check passed for {label} ({'; '.join(reasons)}); stale baseline "
                         f"{fmt_gb(baseline)} vs settled {fmt_gb(settled)} ({note})"), settled


def require_generation_idle(orch_url: str, label: str) -> None:
    """Section 4.2 rule 3 ("exactly one may generate at any moment ... everywhere") for a request this script sends
    straight to a llama-server port, which the Arbiter's generation lock cannot see. The driver has stopped every
    other path to a generation (timers, atlas-celery-gpu, the Open WebUI container); this belt waits for the
    orchestrator's single generation slot to be free (GET /health "generating" / "generation_queue", api.py) and
    stops the phase when it is not within GENERATION_IDLE_WAIT_S: someone is using the assistant beside the tests."""
    deadline = time.monotonic() + GENERATION_IDLE_WAIT_S
    said = False
    while True:
        try:
            code, body = http("GET", f"{orch_url.rstrip('/')}/health", timeout=10)
        except HttpFail as exc:
            raise Infra(f"{label}: GET /health failed while checking the generation slot (rule 3): {exc}") from exc
        if code != 200 or not isinstance(body, dict):
            raise Infra(f"{label}: GET /health answered {code} while checking the generation slot (rule 3): "
                        f"{short(body, 200)}")
        gen, queue = body.get("generating"), body.get("generation_queue") or []
        if not gen and not queue:
            return
        if time.monotonic() > deadline:
            raise Infra(f"{label}: the orchestrator is still generating ({short(gen, 120)}; queue {short(queue, 120)}) "
                        f"after {GENERATION_IDLE_WAIT_S:.0f}s: something uses the assistant during the load tests "
                        "(Section 4.2 rule 3); stop it and re-run")
        if not said:
            log(f"{label}: waiting for the orchestrator's generation slot ({short(gen, 120)}) before a direct request "
                "(Section 4.2 rule 3)")
            said = True
        time.sleep(5)


# --- V4 proof ---------------------------------------------------------------------------------------------------------


def evaluate_kv_lines(key: str, expected: str, proof_lines: int, lines: list[str], build: str = "",
                      ) -> tuple[bool, str, str]:
    """Pure part of the V4 proof: (ok, message, applied type) from the journal lines of the current invocation."""
    kv = [m for m in (KV_LINE_RE.search(ln) for ln in lines) if m]
    rec = [m for m in (RECURRENT_RE.search(ln) for ln in lines) if m]
    fa = [m.group(1) for m in (FA_RE.search(ln) for ln in lines) if m]
    refused = [s for s in KV_REFUSED if any(s in ln for ln in lines)]
    if not kv:
        why = ("journal of the current invocation has no 'llama_kv_cache: size =' line (TRACE at the pin: printed "
               "only at -lv 4, which phase2/engine-env.py renders; a unit started before that render has none)"
               if lines else "journal of the current invocation is empty (journalctl access?)")
        return False, f"{key}: V4 unproven: {why}{build}" + (f"; refused: {refused}" if refused else ""), ""
    types = sorted({(m.group(1), m.group(2)) for m in kv})
    applied = types[0][0] if len(types) == 1 and types[0][0] == types[0][1] else ",".join(f"{k}/{v}" for k, v in types)
    ok = all(k == expected and v == expected for k, v in types) and not refused
    parts = [f"{len(kv)} llama_kv_cache line(s) K/V {applied}"]
    if proof_lines and len(kv) != proof_lines:
        parts.append(f"expected {proof_lines} line(s)")
    if fa:
        parts.append(f"flash_attn={fa[-1]}")
        if fa[-1] != "enabled":
            ok = False
    else:
        parts.append("no flash_attn line")
        ok = False
    if rec:
        parts.append(f"recurrent R/S {rec[-1].group(1)}/{rec[-1].group(2)} (expected f32, unaffected)")
    if refused:
        parts.append(f"refused: {refused}")
    verdict = f"{expected} applied" if ok else f"{expected} requested, NOT proven"
    return ok, f"{key}: {verdict} ({'; '.join(parts)})", applied


def prove_kv(key: str, expected: str, proof_lines: int, invocation_id: str, since: str, port: int,
             ) -> tuple[bool, str, str]:
    """(ok, message, applied type). Reads the journal of the current invocation; /props only for the reason text.
    journald commits stderr lines asynchronously, so a warm re-load can answer /health before the proof line is
    readable: three reads two seconds apart before the verdict."""
    lines: list[str] = []
    for attempt in range(3):
        lines = journal_lines(key, invocation_id, since)
        if any(KV_LINE_RE.search(ln) for ln in lines) or any(s in ln for s in KV_REFUSED for ln in lines):
            break
        if attempt < 2:
            time.sleep(2)
    build = ""
    if not any(KV_LINE_RE.search(ln) for ln in lines):
        try:
            code, props = http("GET", f"http://127.0.0.1:{port}/props", timeout=10)
            if code == 200 and isinstance(props, dict):
                build = f"; /props build_info={props.get('build_info')} (no cache-type field exists there)"
        except HttpFail:
            pass
    return evaluate_kv_lines(key, expected, proof_lines, lines, build)


# --- S52 Phase 3 items: context memory, host-RAM caches, context shift (records, never a verdict) ---------------------


def _fnum(text: str) -> float:
    return round(float(text), 3)


def _max_opt(a: float | None, b: float) -> float:
    return b if a is None else max(a, b)


def parse_memory_lines(lines: Sequence[str]) -> dict[str, Any]:
    """Pure: what one load's log says about its memory (module docstring, S52; MiB as llama-server prints them, None
    when the line was not logged). A K/V size line belongs to the sub-cache announced just before it (iSWA, DSV4),
    else to the one full cache. --fit's trial context logs its own K/V, recurrent, compressor and compute lines as
    "I " at -lv 5 or -v (module docstring), so everything before its closing "common_fit_params: fitting params to
    free memory took" line is discarded and nothing counts twice; debug ("D ") lines are skipped."""
    parts: list[dict[str, Any]] = []
    recurrent: list[dict[str, Any]] = []
    states: list[dict[str, Any]] = []
    compute: dict[str, float] = {}
    mmproj: dict[str, float] = {}
    pcache: dict[str, Any] = {"evicted": 0, "evicted_max_mib": None, "oversize_skipped": 0, "limit_reduced_mib": None,
                              "state_max_mib": None, "prompts_max": None}
    ckpt: dict[str, Any] = {"erased": 0, "size_max_mib": None, "created_max": None, "limit": None}
    out: dict[str, Any] = {"n_slots": None, "n_ctx_slot": None, "kv_unified": None, "ctx_shift_off": ""}
    tag = ""
    for ln in lines:
        if DEBUG_LINE_RE.search(ln):
            continue
        if FIT_END_RE.search(ln):  # what came before was --fit's trial context (FIT_END_RE)
            parts, recurrent, states, compute, tag = [], [], [], {}, ""
        elif m := ISWA_PART_RE.search(ln):
            tag = m.group(1)
        elif m := DSV4_PART_RE.search(ln):
            tag = f"DSV4 {m.group(1)}"
        elif IDX_PART_RE.search(ln):
            tag = "indexer"  # glm5-next's DSA indexer cache (module docstring); the MLA attention line before is "full"
        elif m := KV_SIZE_RE.search(ln):
            parts.append({"part": tag or "full", "mib": _fnum(m.group(1)), "cells": int(m.group(2)),
                          "layers": int(m.group(3)), "seqs": int(m.group(4)), "streams": int(m.group(5)),
                          "k": m.group(6), "v": m.group(7)})
            tag = ""
        elif m := RECURRENT_SIZE_RE.search(ln):
            recurrent.append({"mib": _fnum(m.group(1)), "cells": int(m.group(2)), "layers": int(m.group(3))})
        elif m := DSV4_STATE_RE.search(ln):
            states.append({"name": m.group(1), "streams": int(m.group(2)), "mib": _fnum(m.group(3))})
        elif m := COMPUTE_RE.search(ln):
            into = compute if m.group(1) == "sched_reserve" else mmproj
            into[m.group(2)] = max(into.get(m.group(2), 0.0), _fnum(m.group(3)))  # the largest reservation stands
        elif m := SLOTS_RE.search(ln):
            out["n_slots"], out["n_ctx_slot"], out["kv_unified"] = int(m.group(1)), int(m.group(2)), m.group(3)
        elif m := CTX_SHIFT_OFF_RE.search(ln):
            out["ctx_shift_off"] = f"not supported by {m.group(1)}"
        elif m := PCACHE_EVICT_RE.search(ln):
            pcache["evicted"] += 1
            pcache["evicted_max_mib"] = _max_opt(pcache["evicted_max_mib"], _fnum(m.group(1)))
        elif m := PCACHE_SKIP_RE.search(ln):
            pcache["oversize_skipped"] += 1
        elif m := PCACHE_REDUCED_RE.search(ln):
            pcache["limit_reduced_mib"] = _fnum(m.group(1))
        elif m := PCACHE_STATE_RE.search(ln):
            pcache["state_max_mib"] = _max_opt(pcache["state_max_mib"], _fnum(m.group(2)))
            pcache["prompts_max"] = max(pcache["prompts_max"] or 0, int(m.group(1)))
        elif m := CKPT_ERASED_RE.search(ln):
            ckpt["erased"] += 1
            ckpt["size_max_mib"] = _max_opt(ckpt["size_max_mib"], _fnum(m.group(1)))
        elif m := CKPT_CREATED_RE.search(ln):
            ckpt["created_max"] = max(ckpt["created_max"] or 0, int(m.group(1)))
            ckpt["limit"] = int(m.group(2))
            ckpt["size_max_mib"] = _max_opt(ckpt["size_max_mib"], _fnum(m.group(3)))
    kv = round(sum(p["mib"] for p in parts), 2) if parts else None
    rec_mib = round(sum(r["mib"] for r in recurrent), 2) if recurrent else None
    state_mib = round(sum(s["mib"] for s in states), 2) if states else None
    # One slot's share of what a checkpoint holds (the parts that cannot be rolled back: sliding-window cells,
    # recurrent state, compressor state; tools/server/server-context.cpp:3465-3473), INFERRED from the sizes above.
    per_slot = (sum(p["mib"] / max(p["streams"], 1) for p in parts if p["part"] == "SWA")
                + sum(r["mib"] / max(r["cells"], 1) for r in recurrent)
                + sum(s["mib"] / max(s["streams"], 1) for s in states))
    out.update({
        "kv_mib": kv, "kv_lines": len(parts), "kv_parts": parts,
        "swa_mib": round(sum(p["mib"] for p in parts if p["part"] == "SWA"), 2) if parts else None,
        "recurrent_mib": rec_mib, "dsv4_state_mib": state_mib,
        "context_mib": None if kv is None else round(kv + (rec_mib or 0.0) + (state_mib or 0.0), 2),
        "compute_mib": compute, "mmproj_compute_mib": mmproj,
        "checkpoint_state_mib": round(per_slot, 3) if per_slot > 0 else None,
        "prompt_cache": pcache, "checkpoints": ckpt,
    })
    return out


def read_proc_status(pid: int, proc_root: Path = Path("/proc")) -> dict[str, Any]:
    """The host RAM of one process, read-only: /proc/<pid>/status VmRSS, VmHWM, RssAnon, RssFile, RssShmem (kB there,
    bytes here) and comm. {"pid", "why"} when it cannot be read."""
    out: dict[str, Any] = {"pid": pid}
    try:
        text = (proc_root / str(pid) / "status").read_text(encoding="ascii", errors="replace")
    except OSError as exc:
        return {**out, "why": f"/proc/{pid}/status: {exc.strerror or exc}"}
    for line in text.splitlines():
        name, _, value = line.partition(":")
        if name == "Name":
            out["comm"] = value.strip()
        elif name in PROC_STATUS_FIELDS and value.split() and value.split()[0].isdigit():
            out[name] = int(value.split()[0]) * 1024
    if "VmRSS" not in out:
        out["why"] = f"/proc/{pid}/status has no VmRSS (a zombie or a kernel thread)"
    return out


def host_memory(key: str, proc_root: Path = Path("/proc")) -> dict[str, Any]:
    """S52 (b): the host RAM of llama-server@KEY's main process now (systemctl show MainPID, then /proc; read-only).
    A measurement: never raises, a failure is {"why"}."""
    try:
        p = run(["systemctl", "show", "-p", "MainPID", "--value", unit_of(key)])
        pid = int(p.stdout.strip() or "0")
    except (OSError, ValueError) as exc:
        return {"why": f"systemctl show -p MainPID {unit_of(key)}: {exc}"}
    if pid <= 0:
        return {"why": f"{unit_of(key)} has no main process (MainPID {pid})"}
    return read_proc_status(pid, proc_root)


def arbiter_projection(ctx: Ctx, key: str, ctx_size: int, parallel: int, kv: str,
                       ) -> tuple[dict[str, Any] | None, str]:
    """The Arbiter's projection for exactly this profile, from its own code (PROJECTION_SNIPPET in the orchestrator's
    venv, `python -I -B`: no PYTHONPATH, no cwd on sys.path, no .pyc written as root), never a copy of its figures here.
    (None, why) when it cannot be had: the record then says UNKNOWN."""
    py = ctx.atlas_python
    if not py or not os.access(py, os.X_OK):
        return None, f"no orchestrator python at {py or '(none given)'} (--atlas-python)"
    try:
        p = run([py, "-I", "-B", "-c", PROJECTION_SNIPPET, str(ctx.engines_path.parent), key, str(ctx_size),
                 str(parallel), kv], timeout=PROJECTION_TIMEOUT_S)
    except OSError as exc:
        return None, f"{py}: {exc}"
    if p.returncode != 0:
        return None, f"projection failed (exit {p.returncode}): {short((p.stderr or p.stdout).strip()[-400:], 300)}"
    try:
        data = json.loads(p.stdout.strip().splitlines()[-1])
    except (json.JSONDecodeError, IndexError) as exc:
        return None, f"projection printed no JSON ({exc}): {short(p.stdout, 200)}"
    return (data, "") if isinstance(data, dict) else (None, f"projection printed {short(data, 120)}")


def rendered_cache_ram_mib(env: dict[str, str]) -> int | None:
    """Pure: the prompt-cache cap the engine's env file renders (the last --cache-ram or -cram in ARGS, in either the
    `--flag N` or the `--flag=N` spelling; phase2/engine-env.py from engines.json cache_ram_mib, B-33). A negative
    value is returned as it is: llama-server reads it as no size limit (tools/server/server-task.h:654 at b11474), and
    engine-env.py refuses it, so only a hand-edited or out-of-date env file carries one. None when ARGS carry none, or
    not a whole number (an env file rendered before B-33): the server then runs at llama-server's default,
    LLAMA_CACHE_RAM_DEFAULT_MIB."""
    value: str | None = None
    args = env.get("ARGS", "").split()
    for i, tok in enumerate(args):
        flag, eq, rest = tok.partition("=")
        if flag in CACHE_RAM_FLAGS:
            value = rest if eq else (args[i + 1] if i + 1 < len(args) else None)
    return int(value) if value is not None and re.fullmatch(r"-?[0-9]+", value) else None


def memory_record(env: dict[str, str], parsed: dict[str, Any], projection: dict[str, Any] | None, proj_why: str,
                  host_load: dict[str, Any], host_after: dict[str, Any], label: str = "") -> dict[str, Any]:
    """Pure: one load's S52 record (results file "memory"): the profile the unit ran, what its log says (a), the
    Arbiter's projection for that profile, the host RAM right after the load and after the tests (b), and whether
    context shift stayed on (c). Everything not measured is listed under "unknown" with its reason."""
    unknown: list[str] = []
    requested = "--context-shift" in env.get("ARGS", "").split()
    if parsed.get("ctx_shift_off"):
        shift = "disabled"
    elif not requested:
        shift = "not requested"
    elif parsed.get("n_ctx_slot") is not None:
        shift = "kept"  # the slot line comes after the check (module docstring), and no warning came before it
    else:
        shift = "UNKNOWN"
        unknown.append("context shift: the load log has no slot line, so it does not cover the check")
    pc, ck = parsed.get("prompt_cache") or {}, parsed.get("checkpoints") or {}
    if parsed.get("kv_mib") is None:
        unknown.append("context memory: no 'llama_kv_cache: size =' line in the load log (TRACE at the pin: printed "
                       "only at the -lv 4 phase2/engine-env.py renders)")
        if parsed.get("checkpoint_state_mib") is None:  # not measured, which is not "none made" (cache_suggestion)
            unknown.append("checkpoints: one slot's state UNKNOWN (no K/V, recurrent or compressor size line in the "
                           "load log); erasures or creations are logged, so the engine makes them"
                           if ck.get("erased") or ck.get("created_max") else
                           "checkpoints: UNKNOWN (no K/V, recurrent or compressor size line in the load log, so "
                           "whether the engine makes them, and their size, is not known)")
    if projection is None:
        unknown.append(f"projection: {proj_why}")
    for when, h in (("after the load", host_load), ("after the tests", host_after)):
        if "VmRSS" not in h:
            unknown.append(f"host RAM {when}: {h.get('why', 'not read')}")
    growth = None
    if "RssAnon" in host_load and "RssAnon" in host_after and host_load.get("pid") == host_after.get("pid"):
        growth = host_after["RssAnon"] - host_load["RssAnon"]
    elif "RssAnon" in host_load and "RssAnon" in host_after:
        unknown.append("host RAM growth: the main process changed between the two readings (a restart)")
    if pc.get("state_max_mib") is None:
        unknown.append("prompt-cache size: no ' - cache state:' line in the log (TRACE, printed at the -lv 4 "
                       "phase2/engine-env.py renders); RssAnon growth is the measured bound")
    if parsed.get("checkpoint_state_mib") is not None and ck.get("size_max_mib") is None:
        unknown.append("checkpoint size: no 'created context checkpoint' (TRACE, -lv 4) or 'erasing' line in the log; "
                       "INFERRED from the sliding-window/recurrent/compressor sizes instead")
    return {
        "label": label,
        "profile": {"ctx": int(env.get("ATLAS_CTX_SIZE") or 0), "parallel": int(env.get("ATLAS_PARALLEL") or 1),
                    "kv": env.get("ATLAS_KV_TYPE", ""), "cache_ram_mib": rendered_cache_ram_mib(env)},
        "measured": parsed,
        "projected": projection,
        "ctx_shift": shift,
        "ctx_shift_why": parsed.get("ctx_shift_off") or "",
        "ctx_shift_requested": requested,
        "host_after_load": host_load,
        "host_after_tests": host_after,
        "anon_growth_bytes": growth,
        "unknown": unknown,
        "taken_at": now_iso(),
    }


def _gib(mib: float | None) -> str:
    return "UNKNOWN" if mib is None else f"{mib / 1024:.2f}"


def _rss(h: dict[str, Any], fld: str = "VmRSS") -> str:
    return f"{h[fld] / GIB:.2f}" if fld in h else "UNKNOWN"


def memory_line(rec: dict[str, Any]) -> str:
    """The one log line per load (S52): measured beside projected, host RAM, caches, context shift."""
    if rec.get("error"):
        return f"memory (S52){' ' + rec['label'] if rec.get('label') else ''}: not recorded ({rec['error'][:200]})"
    m, prof, proj = rec["measured"], rec["profile"], rec.get("projected") or {}
    extra = [f"SWA {_gib(m['swa_mib'])}" if m.get("swa_mib") else "",
             f"recurrent {_gib(m['recurrent_mib'])}" if m.get("recurrent_mib") is not None else "",
             f"compressor {_gib(m['dsv4_state_mib'])}" if m.get("dsv4_state_mib") is not None else ""]
    kvb = proj.get("kv_bytes")
    pc, ck = m["prompt_cache"], m["checkpoints"]
    growth = rec.get("anon_growth_bytes")
    resident = rec.get("label") == "resident"
    versus = ("resident, never charged" if resident
              else f"vs {'UNKNOWN' if kvb is None else f'{kvb / GIB:.2f}'} GiB projected")
    host = (f"host RSS {_rss(rec['host_after_tests'])} GiB now" if resident
            else f"host RSS {_rss(rec['host_after_load'])} GiB after the load, {_rss(rec['host_after_tests'])} after "
            f"the tests (anon {'UNKNOWN' if growth is None else f'{growth / MIB:+.0f} MiB'})")
    return (f"memory (S52){' ' + rec['label'] if rec.get('label') else ''}: {prof['ctx']} x {prof['parallel']} "
            f"{prof['kv']}; context {_gib(m['context_mib'])} GiB measured ({m['kv_lines']} K/V line(s)"
            + "".join(f", {e}" for e in extra if e) + f") {versus}; compute "
            f"{_gib(sum(m['compute_mib'].values()) if m['compute_mib'] else None)} GiB; slot window "
            f"{m['n_ctx_slot'] or 'UNKNOWN'}; context shift {rec['ctx_shift']}"
            + (f" ({rec['ctx_shift_why']})" if rec.get("ctx_shift_why") else "")
            + f"; {host}; prompt cache {pc['evicted']} eviction(s), checkpoints {ck['erased']} erased")


def take_memory_record(ctx: Ctx, res: EngineResult, key: str, env: dict[str, str], inv: str, since: str,
                       host_load: dict[str, Any], label: str = "") -> None:
    """S52 Phase 3 items (a)-(c) for one load, after its tests while the unit is still up: the load's journal (the
    invocation read for V4), the host RAM now, and the Arbiter's projection for the profile the env file gave. One
    record into res.memory and one log line. Never fails the engine or the phase (a measurement, not a verdict):
    whatever goes wrong is recorded with its reason; only SIGTERM still stops the run."""
    try:
        lines = journal_lines(key, inv, since)
        host_after = host_memory(key)
        projection, why = arbiter_projection(ctx, key, int(env.get("ATLAS_CTX_SIZE") or 0),
                                             int(env.get("ATLAS_PARALLEL") or 1), env.get("ATLAS_KV_TYPE", ""))
        rec = memory_record(env, parse_memory_lines(lines), projection, why, host_load, host_after, label)
        if not lines:
            rec["unknown"].insert(0, "the load's journal is empty (journalctl access?)")
    except Terminated:
        raise
    except Exception as exc:  # S52: a record, never a verdict; broken tooling is UNKNOWN, not a failed engine
        rec = {"label": label, "error": f"{type(exc).__name__}: {exc}", "unknown": [f"record not taken: {exc}"],
               "taken_at": now_iso()}
    res.memory.append(rec)
    log(f"{key}: {memory_line(rec)}")


# --- generation and measurement ---------------------------------------------------------------------------------------


def tokenize_count(port: int, text: str) -> int:
    code, body = http("POST", f"http://127.0.0.1:{port}/tokenize", {"content": text}, timeout=120)
    if code != 200 or not isinstance(body, dict) or "tokens" not in body:
        raise RuntimeError(f"/tokenize answered {code}: {str(body)[:200]}")
    return len(body["tokens"])


def make_prompt(port: int, target_tokens: int, nonce: str) -> tuple[str, int]:
    """A prompt of about target_tokens tokens: random words (never cached) sized by /tokenize in a few rounds."""
    rng = random.Random(nonce)
    words = [rng.choice(WORDS) for _ in range(int(target_tokens * 0.8) + 8)]
    text = f"{nonce} " + " ".join(words)
    n = tokenize_count(port, text)
    for _ in range(4):
        if abs(n - target_tokens) <= max(8, target_tokens // 50):
            break
        ratio = target_tokens / max(n, 1)
        want = max(4, int(len(words) * ratio))
        if want > len(words):
            words += [rng.choice(WORDS) for _ in range(want - len(words))]
        else:
            words = words[:want]
        text = f"{nonce} " + " ".join(words)
        n = tokenize_count(port, text)
    return text + "\n\nContinue this list of words:", n


def completion(port: int, prompt: str, n_predict: int) -> dict[str, Any]:
    """POST /completion; returns {"content", "timings", "error"}. Never raises on HTTP errors (the caller decides)."""
    body = {"prompt": prompt, "n_predict": n_predict, "temperature": 0, "cache_prompt": False, "stream": False}
    try:
        code, resp = http("POST", f"http://127.0.0.1:{port}/completion", body, timeout=REQUEST_TIMEOUT_S)
    except HttpFail as exc:
        return {"content": "", "timings": {}, "error": str(exc)}
    if code != 200 or not isinstance(resp, dict):
        return {"content": "", "timings": {}, "error": f"HTTP {code}: {str(resp)[:300]}"}
    return {"content": resp.get("content") or "", "timings": resp.get("timings") or {}, "error": ""}


def chat(url: str, model: str, messages: list[dict[str, str]], max_tokens: int, timeout: float = REQUEST_TIMEOUT_S,
         path: str = LLAMA_CHAT_PATH, headers: dict[str, str] | None = None) -> dict[str, Any]:
    """POST an OpenAI chat request: to a llama-server directly (path /v1/chat/completions, the model field is
    ignored) or to the orchestrator (path /internal/v1/chat/completions, model = engine key, Arbiter lock)."""
    body = {"model": model, "messages": messages, "max_tokens": max_tokens, "temperature": 0, "stream": False}
    t_send = time.monotonic()
    base = {"content": "", "reasoning": "", "finish": "", "timings": {}, "error": "", "t_send": t_send, "code": 0}
    try:
        code, resp = http("POST", f"{url}{path}", body, timeout=timeout, headers=headers)
    except HttpFail as exc:
        return {**base, "error": str(exc), "t_recv": time.monotonic()}
    t_recv = time.monotonic()
    if code != 200 or not isinstance(resp, dict):
        return {**base, "error": f"HTTP {code}: {str(resp)[:300]}", "t_recv": t_recv, "code": code}
    content, reasoning, finish = "", "", ""
    try:
        choice = resp["choices"][0]
        msg = choice.get("message") or {}
        content = msg.get("content") or ""
        reasoning = msg.get("reasoning_content") or ""
        finish = choice.get("finish_reason") or ""
    except (KeyError, IndexError, TypeError, AttributeError):
        pass
    return {**base, "content": content, "reasoning": reasoning, "finish": finish,
            "timings": resp.get("timings") or {}, "t_recv": t_recv, "code": code}


def measure_point(port: int, target: int, label: str) -> tuple[dict[str, Any], str]:
    """One timing point: prefill tok/s at ~target prompt tokens and decode tok/s for N_PREDICT tokens after it."""
    nonce = f"p3-{label}-{random.randrange(10**9)}"
    prompt, n = make_prompt(port, target, nonce)
    r = completion(port, prompt, N_PREDICT)
    if r["error"]:
        return r, f"{label}: request failed: {r['error'][:200]}"
    t = r["timings"]
    note = (f"{label}: prompt_n={t.get('prompt_n')} (target {target}, tokenized {n}) cache_n={t.get('cache_n')} "
            f"prefill {t.get('prompt_per_second', 0):.1f} tok/s, predicted_n={t.get('predicted_n')} "
            f"decode {t.get('predicted_per_second', 0):.1f} tok/s")
    if t.get("cache_n"):
        note += " (WARNING cache_n>0: prefill figure includes cached tokens)"
    return r, note


COHERENCE_PASSAGE = (
    "The water cycle describes how water moves between the oceans, the atmosphere and the land. Heat from the sun "
    "evaporates water from the sea surface, lakes and rivers, and plants release vapour through transpiration. The "
    "vapour rises, cools and condenses into clouds. When the droplets grow heavy enough they fall as rain, snow or "
    "hail. Some of that precipitation runs off into streams and rivers, some soaks into the soil to recharge "
    "groundwater, and some is stored for centuries in glaciers and ice sheets. Rivers carry the runoff back to the "
    "ocean, closing the loop. The cycle moves enormous amounts of energy: evaporation stores heat as latent energy, "
    "and condensation releases it, which drives storms and shapes regional climates. Human activity changes the "
    "cycle by paving land, pumping aquifers, building dams and warming the atmosphere, which holds more vapour and "
    "makes heavy rainfall more intense."
)
COHERENCE_KEYWORDS = ("water", "evaporat", "cloud", "rain", "ocean", "river", "vapour", "condens", "groundwater", "ice")


def chat_with_thinking_budget(port: int, model: str, messages: list[dict[str, str]], max_tokens: int,
                              ) -> tuple[dict[str, Any], str]:
    """One chat turn against a llama-server with a reasoning model in mind: when the answer is empty because the
    thinking budget ran out (finish_reason "length"), retry once with a larger budget rather than call it incoherent.
    Returns (response, note)."""
    r = chat(f"http://127.0.0.1:{port}", model, messages, max_tokens)
    if r["error"]:
        return r, ""
    if not r["content"].strip() and r["finish"] == "length":
        bigger = max_tokens * COHERENCE_RETRY_FACTOR
        r2 = chat(f"http://127.0.0.1:{port}", model, messages, bigger)
        if r2["error"]:
            return r2, ""
        return r2, (f"thinking budget of {max_tokens} tokens exhausted (reasoning {len(r['reasoning'])} chars); "
                    f"retried at {bigger}")
    return r, ""


def text_quality(a2: str) -> tuple[bool, str, dict[str, Any]]:
    """Garbage/repetition heuristics on a summary; (ok, reason, stats)."""
    words = re.findall(r"[A-Za-z']+", a2)
    stats: dict[str, Any] = {"words": len(words)}
    if len(words) < 20:
        return False, f"summary too short/empty ({len(words)} words): {a2[:80]!r}", stats
    non_ascii = sum(1 for ch in a2 if ord(ch) > 127) / max(len(a2), 1)
    if non_ascii > 0.15:
        return False, f"summary is mojibake ({non_ascii:.0%} non-ASCII): {a2[:80]!r}", stats
    uniq = len({w.lower() for w in words}) / len(words)
    stats["uniq"] = uniq
    if uniq < 0.3:
        return False, f"summary is repetitive (unique-word ratio {uniq:.2f}): {a2[:80]!r}", stats
    grams = [" ".join(words[i:i + 4]).lower() for i in range(len(words) - 3)]
    if grams and max(grams.count(g) for g in set(grams)) > 4:
        return False, f"summary repeats a 4-gram more than 4 times: {a2[:80]!r}", stats
    hits = sum(1 for k in COHERENCE_KEYWORDS if k in a2.lower())
    stats["hits"] = hits
    if hits < 3:
        return False, f"summary is off-topic ({hits} of {len(COHERENCE_KEYWORDS)} keywords): {a2[:80]!r}", stats
    return True, "", stats


def coherence_check(port: int, model: str) -> tuple[bool, str]:
    """A short factual question with a known answer plus a ~120-word summary; garbage or repetition fails. A reasoning
    model spends tokens in reasoning_content first, so the budgets are generous, an exhausted budget is retried once,
    and the quality checks fall back to reasoning_content when content is empty."""
    notes: list[str] = []
    r1, note = chat_with_thinking_budget(
        port, model, [{"role": "user", "content": "What is the capital of France? Answer with the city name only."}],
        COHERENCE_TOKENS_FACTUAL)
    if note:
        notes.append(note)
    if r1["error"]:
        return False, f"factual question failed: {r1['error'][:200]}"
    a1 = r1["content"].strip()
    if not a1:
        return False, (f"factual question produced no answer (finish={r1['finish'] or '?'}, reasoning "
                       f"{len(r1['reasoning'])} chars: {r1['reasoning'][:60]!r})")
    if "paris" not in a1.lower():
        return False, f"factual question wrong/garbled: {a1[:80]!r}"
    r2, note = chat_with_thinking_budget(
        port, model,
        [{"role": "user", "content": f"Summarise the following passage in about 120 words:\n\n{COHERENCE_PASSAGE}"}],
        COHERENCE_TOKENS_SUMMARY)
    if note:
        notes.append(note)
    if r2["error"]:
        return False, f"summary request failed: {r2['error'][:200]}"
    a2 = r2["content"].strip()
    source = "content"
    if not a2 and r2["reasoning"].strip():
        a2, source = r2["reasoning"].strip(), "reasoning_content (content empty)"
    ok, why, stats = text_quality(a2)
    if not ok:
        return False, why + (f" [{source}]" if source != "content" else "") + ("; " + "; ".join(notes) if notes else "")
    return True, (f"'{a1[:20]}' + {stats['words']}-word summary ({source}), {stats['hits']} keywords, unique ratio "
                  f"{stats['uniq']:.2f}" + ("; " + "; ".join(notes) if notes else ""))


# --- per-engine test --------------------------------------------------------------------------------------------------


def finalize_control(res: EngineResult, ctl: Control) -> None:
    """Record how the engine was controlled at save time (the step 4 table prints it)."""
    res.control_mode = ctl.mode


def credit_release(ctx: Ctx, key: str, ok: bool, secs: float, note: str) -> None:
    """The release check of the engine being swapped out belongs to that engine's result (V10 'released')."""
    res = load_result(ctx, key)
    if res is None:
        res = EngineResult(key=key, arbiter_class=ctx.spec(key).get("arbiter_class", ""))
    res.released, res.release_s, res.release_note, res.resident_after = ok, round(secs, 1), note, False
    if not ok:
        res.ok = False
    res.save(ctx)


def unload_and_release(ctl: Control, key: str, baseline: int) -> tuple[bool, float, str]:
    """Unload through Control and confirm the memory came back. A refusal is recorded as a failed release with the
    Arbiter's reason; nothing is stopped behind it."""
    try:
        reason = ctl.unload(key)
    except RuntimeError as exc:
        log(f"release {key}: FAIL {exc}")
        return False, 0.0, f"unload refused/failed: {exc}"
    ok, secs, note = wait_release(baseline, key)
    return ok, secs, f"{note}; Arbiter: {reason}"


def release_or_poll(ctl: Control, key: str, baseline: int) -> tuple[bool, float, str]:
    """Section 4.2 rule 5, always: an engine that is resident (unit active or in the ledger) is unloaded through the
    Arbiter and the counter polled; an engine whose unit already went away (a failed load, a server that died at 8k)
    still gets the counter polled, because the process exit is never trusted."""
    if unit_active(key) or key in ctl.resident_keys():
        return unload_and_release(ctl, key, baseline)
    ok, secs, note = wait_release(baseline, key)
    return ok, secs, f"unit not active, counter polled anyway: {note}"


def leak_message(ctx: Ctx, key: str, note: str) -> str:
    return (f"{key} did not release its memory ({note}): the phase stops here, as the Arbiter halts (Section 4.2 rule "
            f"5); recorded in {ctx.result_path(key)}. Check the GTT counter and the llama-server@ units, restart "
            f"{ORCH_UNIT}.service, then re-run (the engine is re-tested)")


def swap_out(ctx: Ctx, ctl: Control, previous: str | None, baseline: int) -> tuple[float, str]:
    """Unload the previous engine and confirm its memory came back, crediting the check to PREVIOUS. Returns (seconds,
    note). A release that fails is an Infra error: no further load (rule 5)."""
    t0 = time.monotonic()
    if not previous:
        return 0.0, "no previous engine"
    log(f"swap: releasing {previous}")
    ok, secs, note = release_or_poll(ctl, previous, baseline)
    credit_release(ctx, previous, ok, secs, note)
    if not ok:
        raise Infra(leak_message(ctx, previous, note))
    return time.monotonic() - t0, f"released {previous} ({note})"


def stop_and_release(ctx: Ctx, ctl: Control, key: str, baseline: int, res: EngineResult) -> None:
    """Release KEY (unload through the Arbiter, or poll the counter when its unit is already gone) into res."""
    ok, secs, note = release_or_poll(ctl, key, baseline)
    res.released, res.release_s, res.release_note = ok, round(secs, 1), note
    res.resident_after = unit_active(key) if not ok else False


def halt_if_leaked(ctx: Ctx, res: EngineResult) -> None:
    """Called after the result is saved and its records printed: a failed release stops the phase (rule 5)."""
    if res.released is False:
        raise Infra(leak_message(ctx, res.key, res.release_note))


def register_footprint(ctl: Control, res: EngineResult, baseline: int) -> None:
    """Section 4.2 rule 1: the measured footprint (GTT delta at the rendered ctx/parallel) replaces the Arbiter's
    estimate. Measured while the engine is resident, right after the timing points."""
    delta = max(0, gtt_used_bytes() - baseline)
    res.footprint_bytes = delta
    why = ctl.register(res.key, delta)
    if why:
        res.footprint_note = f"measured {fmt_gb(delta)}, NOT registered: {why}"
        res.warn(res.footprint_note)
    else:
        res.footprint_note = f"measured {fmt_gb(delta)}, registered with the Arbiter"
    log(f"{res.key}: footprint {res.footprint_note}")


def _band_text(band: Any) -> str:
    lo, hi = band[0], band[1]
    return f"{lo}" if lo == hi else f"{lo}-{hi}"


def check_band(res: EngineResult, spec: dict[str, Any]) -> None:
    """Section 21 V10 under D-33 option (c) (module docstring "Verdicts"): the decode rate at 512 tokens is recorded
    beside the research projection (projected_decode_tok_s) and is a V10 fail only below a non-null
    decode_floor_tok_s, the per-seat floor the Principal sets (D-47). With no floor it is a record, never a verdict.
    An entry that still carries a v0.3.5 expected_decode_tok_s band (the unchanged abliterated entry is the only one)
    keeps the v0.3.5 rule: its band's lower bound is the floor, as before this revision."""
    d = res.decode_tps_512
    if d is None:
        return
    floor = spec.get("decode_floor_tok_s")
    band = spec.get("expected_decode_tok_s")
    proj = spec.get("projected_decode_tok_s")
    if floor is None and band:
        lo, hi = float(band[0]), float(band[1])
        if d < lo:
            res.band_fail = (f"decode {d} tok/s below the expected band {_band_text(band)} tok/s (the entry's v0.3.5 "
                             "band, its rule unchanged; V10 fail)")
        elif d > hi:
            res.band_note = f"decode {d} tok/s above the expected band {_band_text(band)} tok/s (informational)"
        else:
            res.band_note = f"decode {d} tok/s within the expected band {_band_text(band)} tok/s"
        return
    versus = f" vs {_band_text(proj)} tok/s projected" if proj else " (no projection recorded)"
    if floor is not None and d < float(floor):
        # V10 fail, no tolerance: the Principal's floor is the verdict (D-33 (c), D-47).
        res.band_fail = f"decode {d} tok/s below the seat's floor {floor} tok/s{versus} (D-33 (c); V10 fail)"
    elif floor is not None:
        res.band_note = f"decode {d} tok/s at or above the floor {floor} tok/s{versus}"
    else:
        res.band_note = (f"decode {d} tok/s{versus}; no floor set "
                         "(D-33 (c): recorded, not a verdict; to-do decode-floors)")


def build_number(tag: Any) -> int | None:
    """'b11279' -> 11279; anything else (None, a v-tag) -> None."""
    m = re.fullmatch(r"b(\d+)", str(tag or "").strip())
    return int(m.group(1)) if m else None


def is_newer_arch(spec: dict[str, Any]) -> bool:
    """S-26: support newer than the old pin v0.4.1 (b10964), so the engine cannot load there (module docstring)."""
    n = build_number(spec.get("min_llama_cpp_tag"))
    return n is not None and n > PARITY_PIN_BUILD


def is_v22_engine(spec: dict[str, Any]) -> bool:
    """The engines with a deferrable V22 row (R19 generalised, S-26): the Apex and every newer architecture."""
    return spec.get("arbiter_class") == "apex" or is_newer_arch(spec)


def v4_verdict(spec: dict[str, Any], ok: bool, msg: str) -> tuple[str, str]:
    """V4 for one engine: a failure of a newer-architecture engine defers (R19 generalised, S-26: it never blocks the
    Phase 3 gate; V22 carries it), every other failure is a fail."""
    if ok:
        return "pass", msg
    if is_newer_arch(spec):
        return "deferred", f"{msg} [newer architecture ({spec.get('arch') or '?'}, first build "  \
            f"{spec.get('min_llama_cpp_tag')}): deferred without blocking, see V22; R19 generalised, S-26]"
    return "fail", msg


def set_v22(res: EngineResult, spec: dict[str, Any]) -> None:
    """The V22 row of a newer-architecture engine tested by test_engine (the Apex's comes from its ladder): pass when
    it loaded, generated and released, else deferred with the reason (R19 generalised, S-26)."""
    if not is_newer_arch(spec) or spec.get("kv_ladder"):
        return
    head = (f"{res.key} ({spec.get('arch') or '?'}, first build {spec.get('min_llama_cpp_tag')}; "
            f"pin {PARITY_PIN_BUILD} cannot load it)")
    if res.ok and res.kv_proof_ok:
        res.v22_result = "pass"
        res.v22_msg = (f"{head}: loads, proves {res.kv_applied or res.kv_requested} KV, generates at 512 and 8k "
                       f"(decode {fmt_num(res.decode_tps_512)} tok/s) and releases its memory")
        return
    why = (res.load_error or res.band_fail or (res.release_note if res.released is False else "")
           or ("V4 not proven: " + res.kv_proof_msg if res.load_ok and not res.kv_proof_ok else "") or "not ok")
    res.v22_result = "deferred"
    res.v22_msg = f"{head}: deferred without blocking the phase (R19 generalised, S-26): {why[:300]}"


def run_measurements(ctx: Ctx, res: EngineResult, key: str, port: int, env: dict[str, str]) -> None:
    spec = ctx.spec(key)
    require_generation_idle(ctx.orch_url, f"{key} 512-token point")  # rule 3: a direct request to the port
    r, note = measure_point(port, PREFILL_SHORT, "512")
    log(f"{key}: {note}")
    if r["error"]:
        res.load_error = f"512-token request failed: {r['error'][:200]}"
        return
    t = r["timings"]
    res.prefill_tps_512 = round(float(t.get("prompt_per_second") or 0), 2)
    res.decode_tps_512 = round(float(t.get("predicted_per_second") or 0), 2)
    res.generated = bool(t.get("predicted_n")) and bool(r["content"].strip())
    if not res.generated:
        res.load_error = f"no generation at 512 tokens (predicted_n={t.get('predicted_n')}, content empty)"
        return
    check_band(res, spec)
    ctx_size = int(env.get("ATLAS_CTX_SIZE", spec["ctx_size"]))
    parallel = int(env.get("ATLAS_PARALLEL", spec.get("parallel", 1)))
    per_slot = ctx_size // max(parallel, 1)
    if per_slot < PREFILL_LONG + N_PREDICT + 256:
        res.prefill_8k_note = f"skipped: {per_slot} tokens per slot ({ctx_size}/{parallel}) cannot hold an 8k prompt"
        log(f"{key}: 8k point {res.prefill_8k_note}")
        return
    require_generation_idle(ctx.orch_url, f"{key} 8k point")
    r, note = measure_point(port, PREFILL_LONG, "8k")
    log(f"{key}: {note}")
    # Section 23 S6 (an engines.json known_issue naming #20732; Nemotron 3 Super's, phased after Day 1): the 8k
    # prefill test checks the server is still alive afterwards (never a hang) and records the issue number. The
    # verdict is engines.json's: "pass with the '#20732 checked, server alive' note when /health answers, fail when it
    # does not". Section 21 V10 wants load,
    # generate, swap and release at 8k as at 512: a server that is dead after 8k has not shown that, and its footprint
    # is never registered (rule 1), so for every engine a death at 8k is a V10 FAIL; Nemotron's carries the issue
    # number in the message (the S6 "warning with the issue number" is recorded beside the fail, not instead of it).
    known = NEMOTRON_ISSUE_ID in str(spec.get("known_issue") or "")
    if r["error"]:
        time.sleep(3)
        alive = unit_active(key) and health_code(port) == 200
        if not alive:
            tail_full = journal_tail(key, 30)
            tail = " | ".join(tail_full.splitlines()[-3:])[-300:]
            if stopped_externally(tail_full):
                res.stopped_externally = True
                res.prefill_8k_note = "stopped externally"
                res.load_error = (f"server stopped externally during the 8k prefill (systemd stop, e.g. an "
                                  f"atlas-orchestrator restart); journal: {tail}")
            else:
                res.crashed_at_8k = True
                lost = f" [{DEVICELOST_ISSUE}]" if "ErrorDeviceLost" in tail_full else ""
                if known:
                    res.prefill_8k_note = f"crashed ({NEMOTRON_ISSUE_ID} checked, server dead): V10 fail"
                    res.known_issue_note = (f"8k prefill crashed the server: {NEMOTRON_ISSUE}; Section 23 S6: the "
                                            "alive check ran and the server was found dead, not hung (the 512 point "
                                            "passed); engines.json known_issue: fail when /health does not answer")
                    res.warn(res.known_issue_note)
                    res.load_error = (f"8k prefill crashed the server ({NEMOTRON_ISSUE}; S6 alive check: dead); "
                                      f"journal: {tail}{lost}")
                    log(f"{key}: FAIL {res.load_error}")
                else:
                    res.prefill_8k_note = "crashed"
                    res.load_error = f"8k prefill crashed the server (died during the prefill); journal: {tail}{lost}"
        else:
            res.prefill_8k_note = f"request failed but server alive: {r['error'][:160]}"
            res.load_error = f"8k prefill request failed: {r['error'][:200]}"
        return
    t = r["timings"]
    res.prefill_tps_8k = round(float(t.get("prompt_per_second") or 0), 2)
    res.decode_tps_8k = round(float(t.get("predicted_per_second") or 0), 2)
    if known:
        res.prefill_8k_note = f"{NEMOTRON_ISSUE_ID} checked, server alive"  # engines.json known_issue wording


def test_engine(ctx: Ctx, key: str, previous: str | None) -> None:
    spec = ctx.spec(key)
    ctl = Control(ctx.orch_url, ctx.admin_token)
    pending_recover(ctx)
    baseline = read_baseline(ctx)
    res = EngineResult(key=key, arbiter_class=spec.get("arbiter_class", ""), control_mode=ctl.mode,
                       tested_at=now_iso(), baseline_deviation=str(spec.get("baseline_deviation") or ""))
    if spec.get("kv_ladder"):
        ladder_engine(ctx, ctl, key, previous, baseline, res)
        return
    # Step 2 measures the unit as production runs it: engines.json's profile or whatever overrides.json says (S-22:
    # there is no co-resident profile any more). The footprint registered here (rule 1) is charged by the Arbiter at
    # the profile it was measured at only (arbiter.py measured_profile).
    env = ctx.render(key)
    res.kv_requested = env.get("ATLAS_KV_TYPE", spec.get("kv_class", ""))
    # The previous engine is released BEFORE the model-file check: an engine that never loads must not leave the
    # earlier one resident with the driver believing this one is (its --previous would then name the wrong engine and
    # the next release would be polled against the empty baseline with the real engine still loaded).
    t_swap = time.monotonic()
    _, res.swap_note = swap_out(ctx, ctl, previous, baseline)
    if env.get("ATLAS_MODEL_PRESENT") != "1":
        res.load_error = (f"ATLAS_MODEL_PRESENT=0 in {key}.env: model file {env.get('ATLAS_MODEL_FILE')} "
                          "not found (step 01)")
        res.resident_after = False
        finalize_control(res, ctl)
        set_v22(res, spec)
        res.save(ctx)
        record("V4", *v4_verdict(spec, False, f"{key}: not loaded ({res.load_error})"))
        return
    port = int(env["LLAMA_ARG_PORT"])
    proof_lines = int(env.get("ATLAS_KV_PROOF_LINES", spec.get("kv_proof_lines", 0)) or 0)
    since = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        res.load_s = round(ctl.load(key, int(env["ATLAS_CTX_SIZE"]), int(env["ATLAS_PARALLEL"]), port), 1)
    except RuntimeError as exc:
        res.load_error = str(exc)
        if "ErrorDeviceLost" in res.load_error:
            res.load_error += f" [{DEVICELOST_ISSUE}]"
        log(f"{key}: LOAD FAILED: {res.load_error}")
        stop_and_release(ctx, ctl, key, baseline, res)  # rule 5: the counter is polled even when the unit is gone
        finalize_control(res, ctl)
        set_v22(res, spec)
        res.save(ctx)
        record("V4", *v4_verdict(spec, False, f"{key}: not loaded ({res.load_error[:300]})"))
        halt_if_leaked(ctx, res)
        return
    res.load_ok = True
    res.swap_s = round(time.monotonic() - t_swap, 1)
    if previous:
        log(f"{key}: swap from {previous} took {res.swap_s}s ({res.swap_note}; load {res.load_s}s)")
    inv = unit_invocation_id(key)
    res.kv_proof_ok, res.kv_proof_msg, res.kv_applied = prove_kv(key, res.kv_requested, proof_lines, inv, since, port)
    log(f"{key}: V4 {'PASS' if res.kv_proof_ok else 'FAIL'}: {res.kv_proof_msg}")
    record("V4", *v4_verdict(spec, res.kv_proof_ok, res.kv_proof_msg))
    host_load = host_memory(key)  # S52 (b): before any request, so the tests' cache growth can be told apart
    run_measurements(ctx, res, key, port, env)
    take_memory_record(ctx, res, key, env, inv, since, host_load)  # S52 (a)-(c): a record, never a verdict
    if not res.load_error and not res.crashed_at_8k:
        register_footprint(ctl, res, baseline)
        res.resident_after = True  # the next engine's swap (or `finish`) measures this one's release
    else:
        if res.crashed_at_8k and not res.load_error:
            res.footprint_note = "not measured: the server died at 8k (see warnings)"
        stop_and_release(ctx, ctl, key, baseline, res)
    res.ok = (res.load_ok and res.generated and not res.load_error and not res.band_fail
              and not res.crashed_at_8k and res.released is not False)
    finalize_control(res, ctl)
    set_v22(res, spec)
    res.save(ctx)
    log(f"{key}: result ok={res.ok} decode512={res.decode_tps_512} decode8k={res.decode_tps_8k} "
        f"prefill512={res.prefill_tps_512} prefill8k={res.prefill_tps_8k} warnings={res.warnings}")
    halt_if_leaked(ctx, res)


# --- the Apex KV ladder (engines.json kv_ladder_rule; S-42, D-29 (a); Section 4.3; R19) -------------------------------


@dataclass
class LadderVerdict:
    winner: str
    v4_result: str
    v4_msg: str
    v22_result: str
    v22_msg: str
    set_ov: list[tuple[str, str]]
    clear_ov: list[str]
    note: str
    deviation: str
    summary: str


def rung_verdict(r: dict[str, Any]) -> str:
    if r.get("coherent") and r.get("kv_proof_ok"):
        return "coherent" + (" (measurement failed)" if r.get("measure_error") else "")
    if r.get("coherent"):
        return "coherent but KV unproven"
    return "incoherent" if r.get("load_ok") else "load failed"


def decide_ladder(key: str, ladder: Sequence[str], rungs: Sequence[dict[str, Any]], base_kv: str,
                  stop_reason: str, released: bool | None, release_note: str,
                  decode_512: float | None, prefill_512: float | None, quant: str = "") -> LadderVerdict:
    """Pure decision of the ladder (engines.json kv_ladder_rule under S-42 / D-29 (a); Section 21 V4/V22; R19). The
    rungs are tried in list order, f16 then q8_0, and the FIRST rung that loaded, printed its proof lines and answered
    coherently stands (the highest precision that fits; ladder_engine stops there, so a later rung is never tried
    after a winner):
      * the entry's own kv_class (f16) -> V4 pass, overrides cleared;
      * q8_0 -> V4 pass with the note that f16 failed to load or was incoherent, kv_type written to overrides;
      * no coherent rung at all -> V4 and V22 deferred with the reason (R19 keeps the phase unblocked);
      * a winner whose measurement failed (request error, death at 8k) is still the winner (a measurement failure is
        not incoherence) but V22 records deferred with that reason (R19).
    q4_0 is never a rung (D-29 (a)). A rung the loop never reached is listed as "not tried"."""
    summary = ", ".join(f"{r['kv']}={rung_verdict(r)}" for r in rungs)
    untried = [kv for kv in ladder if kv not in {r["kv"] for r in rungs}]
    if untried:
        summary += ", " + ", ".join(f"{kv}=not tried" for kv in untried)
    winners = [r for r in rungs if r.get("coherent") and r.get("kv_proof_ok")]
    if not winners:
        reason = stop_reason or f"no coherent rung ({summary})"
        return LadderVerdict(
            winner="", v4_result="deferred",
            v4_msg=f"{key}: no KV type proven coherent, V22 deferred (R19): {reason}",
            v22_result="deferred",
            v22_msg=f"{key}: deferred without blocking the phase (R19): {reason}; ladder: {summary}",
            set_ov=[], clear_ov=["kv_type", "ctx_size"], note="overrides cleared", deviation="", summary=summary)
    wrung = winners[0]  # rungs are in list order: the first winner is the highest-precision coherent rung
    winner = str(wrung["kv"])
    above = [r for r in rungs if ladder.index(r["kv"]) < ladder.index(winner)]
    detail = ", ".join(f"{r['kv']}={rung_verdict(r)}" for r in above)
    measure_error = str(wrung.get("measure_error") or "")
    ok = released is not False and not measure_error
    perf = f"decode {decode_512} tok/s at 512, prefill {prefill_512} tok/s"
    if winner == base_kv:
        note = "overrides cleared"
        v = LadderVerdict(winner, "pass",
                          f"{key}: {winner} applied and coherent, the highest-precision rung (S-42; ladder: {summary}; "
                          "overrides cleared)", "pass" if ok else "deferred", "", [], ["kv_type", "ctx_size"],
                          note, "", summary)
    else:
        note = f"{winner} written to overrides.json"
        v = LadderVerdict(winner, "pass",
                          f"{key}: {winner} applied because the {base_kv} rung did not stand ({detail or 'not tried'}; "
                          f"{DEEPSEEK_KV_ISSUES}); {note} (S-42; ladder: {summary})", "pass" if ok else "deferred", "",
                          [("kv_type", winner)],
                          ["ctx_size"], note, "", summary)
    v.v22_msg = (f"{key}{' ' + quant if quant else ''} loads and generates coherently with {winner} KV ({perf}; "
                 f"{v.note})"
                 + (f"; deferred: measurement failed at {winner}: {measure_error[:200]} (R19)" if measure_error else "")
                 + (f"; deferred: {release_note} (R19)" if released is False else ""))
    return v


LADDER_RUNG_FIELDS = ("decode_tps_512", "decode_tps_8k", "prefill_tps_512", "prefill_tps_8k", "prefill_8k_note",
                      "crashed_at_8k", "footprint_bytes", "footprint_note", "band_fail", "band_note")


def ladder_engine(ctx: Ctx, ctl: Control, key: str, previous: str | None, baseline: int, res: EngineResult) -> None:
    """The Apex (engines.json kv_ladder_rule, S-42, D-29 (a); Section 4.3): the HIGHEST-precision coherent rung that
    fits stands. f16 is loaded first at the entry's own ctx_size and parallel, proved (V4 lines) and asked the
    coherence prompt; when it stands, nothing more is loaded. q8_0 is tried only when f16 failed to load (refused
    type, OOM, DeviceLost) or was incoherent or unproven; q4_0 is never a rung. A coherent rung whose measurement fails
    (request error, death at 8k) is not incoherence: it still stands, with V22 deferred (R19). Only a missing model
    file or a rule-5 leak ends the ladder early (the leak stops the phase, Infra, after the result is saved). Every
    rung's overrides are journalled (pending-overrides.json) so an interrupted ladder cannot leave a probe rung
    behind."""
    spec = ctx.spec(key)
    ladder: list[str] = list(spec["kv_ladder"])
    base_kv = spec.get("kv_class", ladder[0] if ladder else "f16")
    proof_lines = int(spec.get("kv_proof_lines", 0) or 0)
    res.kv_requested = f"ladder {'->'.join(ladder)}"
    t_swap = time.monotonic()
    _, res.swap_note = swap_out(ctx, ctl, previous, baseline)
    swap_measured = False
    stop_reason = ""
    pending = pending_begin(ctx, key, ["kv_type", "ctx_size"], "Apex KV ladder rungs")
    for kv in ladder:
        rung: dict[str, Any] = {"kv": kv, "load_ok": False, "coherent": False, "kv_proof_ok": False, "note": ""}
        res.ladder.append(rung)
        env = ctx.render(key, [("kv_type", kv)], ["ctx_size"])
        if env.get("ATLAS_MODEL_PRESENT") != "1":
            stop_reason = f"model file {env.get('ATLAS_MODEL_FILE')} not found (step 01)"
            rung["note"] = stop_reason
            break
        port = int(env["LLAMA_ARG_PORT"])
        since = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        log(f"{key}: ladder rung {kv} (ctx {env.get('ATLAS_CTX_SIZE')}, parallel {env.get('ATLAS_PARALLEL')} explicit)")
        try:
            rung["load_s"] = round(ctl.load(key, int(env["ATLAS_CTX_SIZE"]), int(env["ATLAS_PARALLEL"]), port), 1)
        except RuntimeError as exc:
            rung["note"] = f"load failed: {exc}"
            if "ErrorDeviceLost" in str(exc):
                rung["note"] += f" [{DEVICELOST_ISSUE}]"
            log(f"{key}: rung {kv} {rung['note']}; the next rung is tried (S-42: q8_0 only after f16 fails)")
            stop_and_release(ctx, ctl, key, baseline, res)  # rule 5: polled even when the unit never came up
            rung["released"] = res.released
            if res.released is False:
                stop_reason = f"memory not released after the failed {kv} rung: {res.release_note}"
                break
            continue
        rung["load_ok"] = True
        res.load_ok = True
        if not swap_measured:
            res.swap_s = round(time.monotonic() - t_swap, 1)
            res.load_s = rung["load_s"]
            swap_measured = True
        inv = unit_invocation_id(key)
        ok, msg, applied = prove_kv(key, kv, proof_lines, inv, since, port)
        rung["kv_proof_ok"], rung["kv_proof_msg"], rung["applied"] = ok, msg, applied
        log(f"{key}: rung {kv} proof {'ok' if ok else 'NOT ok'}: {msg}")
        host_load = host_memory(key)  # S52 (b), before the rung's first request
        require_generation_idle(ctx.orch_url, f"{key} rung {kv} coherence prompt")  # rule 3: direct to the port
        coherent, detail = coherence_check(port, key)
        rung["coherent"], rung["coherence"] = coherent, detail
        log(f"{key}: rung {kv} coherence {'ok' if coherent else 'FAIL'}: {detail}")
        won = coherent and ok
        if won:
            # Per-rung measurement into res (reset first so a rung never inherits the one before), copied into the rung.
            res.load_error, res.band_fail, res.band_note, res.crashed_at_8k, res.prefill_8k_note = "", "", "", False, ""
            res.footprint_bytes, res.footprint_note = None, ""
            run_measurements(ctx, res, key, port, env)
            if res.load_error:
                rung["measure_error"] = res.load_error  # a dead or failing server is never registered (rule 1)
            else:
                register_footprint(ctl, res, baseline)
            rung.update({f: getattr(res, f) for f in LADDER_RUNG_FIELDS})
        # S52 (a)-(c) for every rung that loaded, labelled with its KV type: each rung is its own profile.
        take_memory_record(ctx, res, key, env, inv, since, host_load, label=kv)
        stop_and_release(ctx, ctl, key, baseline, res)
        rung["released"] = res.released
        if res.released is False:
            stop_reason = f"memory not released after the {kv} rung: {res.release_note}"
            break
        if won:
            log(f"{key}: rung {kv} coherent and proven: it stands (S-42: the highest-precision coherent rung)"
                + (f"; its measurement failed ({rung['measure_error'][:160]}): V22 deferred (R19)"
                   if rung.get("measure_error") else ""))
            break
        log(f"{key}: rung {kv} {rung_verdict(rung)}; the next rung is tried (S-42)")
    standing = next((r for r in res.ladder if r.get("coherent") and r.get("kv_proof_ok")), {})
    v = decide_ladder(key, ladder, res.ladder, base_kv, stop_reason, res.released, res.release_note,
                      standing.get("decode_tps_512"), standing.get("prefill_tps_512"), str(spec.get("quant") or ""))
    ctx.render(key, v.set_ov, v.clear_ov)
    pending_end(ctx, pending)
    # The result's figures are the winner's (the rung that stands).
    wrung = standing if v.winner else None
    for f in LADDER_RUNG_FIELDS:
        setattr(res, f, wrung.get(f) if wrung else EngineResult.__dataclass_fields__[f].default)
    res.load_error = ""
    res.kv_applied = v.winner
    res.kv_proof_ok = v.v4_result == "pass"
    res.kv_proof_msg = v.v4_msg
    res.generated = bool(v.winner)
    res.v22_result, res.v22_msg = v.v22_result, v.v22_msg
    if v.deviation:
        res.baseline_deviation = (res.baseline_deviation + " | " if res.baseline_deviation else "") + v.deviation
    res.ok = (bool(v.winner) and res.released is not False and not res.band_fail
              and not (wrung or {}).get("measure_error"))
    res.resident_after = False
    finalize_control(res, ctl)
    res.save(ctx)
    record("V4", v.v4_result, v.v4_msg)
    log(f"{key}: ladder result: {res.v22_result}: {res.v22_msg}")
    halt_if_leaked(ctx, res)


# --- prepare / finish / summarize / table -----------------------------------------------------------------------------


def cmd_prepare(ctx: Ctx, _args: argparse.Namespace) -> int:
    ctl = Control(ctx.orch_url, ctx.admin_token)
    pending_recover(ctx)
    prev_baseline = read_baseline_optional(ctx)  # an earlier run's baseline: a resident engine's release is credited
    active = [k for k in ctx.core_keys() if unit_active(k)]
    ledger = ctl.resident_keys()
    foreign = [k for k in active if k not in ledger]
    if foreign:
        raise Infra(f"{foreign} active but not in the Arbiter's ledger (resident: {ledger}); a unit the Arbiter "
                    f"does not own is never stopped behind it: restart {ORCH_UNIT}.service (its startup "
                    "stops unowned units) or stop the unit by hand")
    to_unload = [k for k in ctx.core_keys() if k in ledger or k in active]
    # Every resident engine is unloaded through the Arbiter (each answer granted: its rule-5 check is the authority)
    # and the counter is polled ONCE against the earlier run's baseline after the last one: with two engines resident
    # (an interrupted step 03) a per-engine poll against the empty baseline would report a leak that does not exist.
    # A window that fails although every unload was granted means the earlier baseline is stale (the resident set
    # grew since); the fresh baseline below is what the tests use, and no false leak stops the phase.
    window_ok, secs, note, cur = clear_residents(ctl, to_unload, prev_baseline, "prepare")
    for k in to_unload:
        res = load_result(ctx, k)
        if res is not None and res.resident_after:
            # File-level resumability (CONVENTIONS.md §7.3): the engine passed in an interrupted run and was left
            # resident for the next swap to measure; this IS that measurement, credited so its V10 line can pass. With
            # several engines resident the measurement is cumulative and the note says so (never attributed to one).
            how = "cumulative after " + "+".join(to_unload) if len(to_unload) > 1 else "baseline of the earlier run"
            window = "window ok" if window_ok else "Arbiter release check passed, earlier baseline stale"
            credit_release(ctx, k, True, secs, f"credited by prepare on a re-run ({how}; {window}): {note}")
    total = gtt_total_bytes()
    log(f"prepare: baseline GTT used {fmt_gb(cur)} of {fmt_gb(total)} (residents only; control via {ctl.mode})")
    if cur > 40 * GB:
        log(f"prepare: WARNING baseline {fmt_gb(cur)} is far above the resident set on the GTT counter (v0.4.0: the "
            "router, about 5 GB; the Qwen3 embedder/reranker pair runs on the CPU, D-14 (e)); something "
            "weight-bearing is still loaded outside the llama-server@ units")
    ctx.results_dir.mkdir(parents=True, exist_ok=True)
    (ctx.results_dir / "baseline.json").write_text(json.dumps({
        "gtt_used_bytes": cur, "gtt_total_bytes": total, "measured_at": now_iso(), "control_mode": ctl.mode,
        "unloaded_first": to_unload}, indent=2) + "\n", encoding="utf-8")
    return 0


def cmd_engine(ctx: Ctx, args: argparse.Namespace) -> int:
    test_engine(ctx, args.key, args.previous)
    return 0


def cmd_finish(ctx: Ctx, args: argparse.Namespace) -> int:
    ctl = Control(ctx.orch_url, ctx.admin_token)
    pending_recover(ctx)
    baseline = read_baseline(ctx)
    prev = args.previous
    if prev:
        log(f"finish: releasing the last engine {prev}")
        ok, secs, note = release_or_poll(ctl, prev, baseline)
        credit_release(ctx, prev, ok, secs, note)
        if not ok:
            raise Infra(leak_message(ctx, prev, note))
    for k in ctx.core_keys():
        if unit_active(k) or k in ctl.resident_keys():
            log(f"finish: {k} still resident (unexpected); unloading it")
            ok, secs, note = unload_and_release(ctl, k, baseline)
            credit_release(ctx, k, ok, secs, note)
            if not ok:
                raise Infra(leak_message(ctx, k, note))
    return 0


def fmt_num(v: float | None) -> str:
    return "-" if v is None else f"{v:.1f}"


def v10_line(res: EngineResult, spec: dict[str, Any]) -> tuple[str, str]:
    """Section 21 V10 per engine (D-33 option (c)): load, generate (512 and 8k), swap, release; decode recorded beside
    the projection and failing only below the seat's floor (check_band)."""
    floor = spec.get("decode_floor_tok_s")
    band = spec.get("expected_decode_tok_s")
    proj = spec.get("projected_decode_tok_s")
    if floor is None and band:
        ref = f"expected band {_band_text(band)}, v0.3.5 rule"
    else:
        ref = (f"projected {_band_text(proj)}" if proj else "no projection") + (f", floor {floor}" if floor is not None
                                                                                else ", no floor")
    if not res.load_ok:
        return "fail", f"{res.key}: load FAILED ({res.load_error[:200]}) [control {res.control_mode or '?'}]"
    parts = [f"{res.key}: load ok (KV {res.kv_applied or res.kv_requested}, control {res.control_mode or '?'})",
             f"decode {fmt_num(res.decode_tps_512)}/{fmt_num(res.decode_tps_8k)} tok/s at 512/8k ({ref})",
             f"prefill {fmt_num(res.prefill_tps_512)}/{fmt_num(res.prefill_tps_8k)} tok/s",
             f"swap {fmt_num(res.swap_s)}s",
             f"footprint {fmt_gb(res.footprint_bytes) if res.footprint_bytes is not None else '-'}",
             f"released {'yes' if res.released else ('NO' if res.released is False else '?')}"]
    if res.prefill_8k_note:
        parts.append(f"8k: {res.prefill_8k_note[:120]}")
    if res.band_fail:
        parts.append(f"FLOOR FAIL: {res.band_fail}")
    if res.warnings:
        parts.append("warn: " + " | ".join(w[:160] for w in res.warnings))
    if res.load_error:
        parts.append(f"error: {res.load_error[:200]}")
    # A death at 8k fails the engine; where engines.json's known_issue names #20732 (Nemotron 3 Super, phased) the
    # issue number rides in the message (known_issue_note, Section 23 S6).
    if res.crashed_at_8k and res.known_issue_note and NEMOTRON_ISSUE_ID not in "; ".join(parts):
        parts.append(f"S6: {res.known_issue_note[:160]}")
    ok = (res.generated and not res.load_error and not res.crashed_at_8k and not res.stopped_externally
          and not res.band_fail and res.released is True)
    return ("pass" if ok else "fail"), "; ".join(parts)


DECODE_FLOOR_TODO_TITLE = ("Set a decode floor per LLM seat (D-33 (c), D-47): until you do, Phase 3 records each "
                           "seat's decode rate beside its projection and never fails it on speed")


def raise_decode_floor_todo(ctx: Ctx) -> None:
    """D-33 (c) / D-47: the floors are the Principal's input. While any Phase 3 seat has a null decode_floor_tok_s the
    to-do decode-floors carries every seat's measured decode beside its projection (re-raised by each summarize)."""
    items: list[str] = []
    for key in ctx.core_keys():
        spec = ctx.spec(key)
        if spec.get("decode_floor_tok_s") is not None or spec.get("expected_decode_tok_s"):
            continue  # a floor set, or a v0.3.5 band kept with its rule (check_band): a verdict exists already
        res = load_result(ctx, key)
        proj = spec.get("projected_decode_tok_s")
        measured = (f"{fmt_num(res.decode_tps_512)}/{fmt_num(res.decode_tps_8k)} tok/s at 512/8k"
                    if res is not None and res.decode_tps_512 is not None else "not measured")
        items.append(f"{key}: {measured}, projected {_band_text(proj) if proj else 'none'} tok/s")
    if items:
        todo(DECODE_FLOOR_TODO_ID, DECODE_FLOOR_TODO_TITLE,
             "Measured in Phase 3 step 02: " + " | ".join(items) + ". Set decode_floor_tok_s (tok/s at 512 tokens) on "
             "each engines.json entry you want gated, together with the deep-tier floor of D-47; below it V10 fails. "
             "Null (the default) is record only. Phase 3 changed no engine setting.")


def _router_rules(ctx: Ctx) -> dict[str, Any]:
    try:
        data = json.loads((ctx.engines_path.parent / "router-rules.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _profile_text(ctx: Ctx, key: str) -> str:
    if key not in ctx.engines:
        return f"{key}: not in engines.json"
    spec = ctx.spec(key)
    ctx_size, parallel = int(spec.get("ctx_size") or 0), max(1, int(spec.get("parallel") or 1))
    return (f"{key} {spec.get('kv_class', '?')} at {parallel} x {ctx_size // parallel:,} tokens "
            f"(--ctx-size {ctx_size:,} --parallel {parallel}, no --kv-unified)")


def raise_input_todos(ctx: Ctx) -> None:
    """v0.4.0: the stated defaults of the approved rows that leave an input to the Principal (doc 4.3, 5.1, 9.1,
    Section 22), each a to-do on the live list so `atlas-day1.sh status` shows it; re-raised by every summarize, closed
    only by the Principal. The defaults are read from the shipped config where it holds them."""
    todo(CONTEXT_TARGETS_TODO_ID,
         "State a context target for the phased medical and science seats (D-16)",
         "Default built: Q5_K_S (Baichuan-M3 150.76 GiB, Intern-S1 150.84 GiB), about 23K tokens after a 9 GiB "
         "allowance at the register's ~164 GiB budget; at the ~156 GiB Section 4.1 now expects they leave about 3 GiB "
         "for context, short of that default (doc 4.1, phased seats). Q4_K_M (133.91 / 132.86 GiB) is the rung for a "
         "longer context. Neither seat is pulled on Day 1 (D-26 (c)); the answer is needed before their pull.")
    todo(TRIAL_TASK_SET_TODO_ID,
         "Supply the 30 to 50 blind-scored tasks for the DeepSeek V4.1 Flash trial (D-46 (b), T-30)",
         "Default built: no trial (evaluation-only, after Day 1, outside the routed roster); DeepSeek-V4-Flash-0731 "
         "stays the Apex. The trial needs the Principal's private Deep Think and TF_OMEGA-style tasks, scored blind "
         "by the Principal or a director, and the deep-tier speed floor of D-47 (to-do decode-floors) (doc 5.1, 9.1).")
    todo(SEAT_TEST_SETS_TODO_ID,
         "Supply test sets for the seats no benchmark covers (D-49 (a), T-27)",
         "Default built: GLM-5.3-Flash on those seats, unproven: Australian law, HR, aviation, logistics, AEC, SCADA, "
         "game design, growth marketing, customer success and physical security / OSINT (Victor, Alaric and the "
         "domains of 8.2). Their quality can only be settled on the Principal's own work (doc 5.1, Section 22).")
    rules = _router_rules(ctx)
    trigger = rules.get("long_document_tokens", "UNKNOWN (router-rules.json unreadable)")
    todo(SEAT_PROFILES_TODO_ID,
         "Set each seat's slot and context profile from the Phase 3 measurements (D-48, S-30, T-04)",
         "Defaults built: " + _profile_text(ctx, "deepseek-v4-flash") + " (its native 1,048,576 needs 6.72 GiB of f16 "
         "KV the budget does not have beside its weights); " + _profile_text(ctx, "glm-5.3-flash") + " (T-04 settles "
         f"its cache type; D-48 (b) --kv-unified or (c) one large slot); the long-document trigger {trigger} tokens "
         "(router-rules.json long_document_tokens, D-38, S-30). While GLM's slot is smaller than DeepSeek's, the "
         "trigger keeps Arthur's long documents on DeepSeek (atlas/router.py). See the to-do kv-estimates for the "
         "measured context memory (doc 4.3). Phase 3 changed no engine setting.")


def cmd_summarize(ctx: Ctx, _args: argparse.Namespace) -> int:
    failed: list[str] = []
    warned: list[str] = []
    missing: list[str] = []
    off_floor: list[str] = []
    v22_lines: list[str] = []
    v22_results: list[str] = []
    for key in ctx.core_keys():
        spec = ctx.spec(key)
        res = load_result(ctx, key)
        deferrable = is_v22_engine(spec)
        if res is None:
            if deferrable:
                record("V10", "deferred", f"{key}: no load-test result [newer architecture or Apex: see V22]")
                record("V22", "deferred", f"{key}: no result recorded (R19)")
                v22_lines.append(f"{key}=deferred")
                v22_results.append("deferred")
            else:
                missing.append(key)
                record("V10", "fail", f"{key}: no load-test result (step 02 did not reach it)")
            continue
        result, msg = v10_line(res, spec)
        if deferrable:
            # R19 generalised (S-26): an engine with its own V22 row never fails the V10 summary; its V10 line is a
            # record (pass or deferred) and V22 carries the verdict, deferrable.
            record("V10", "pass" if result == "pass" else "deferred", msg + " [see V22]")
            v22 = res.v22_result or "deferred"
            record("V22", v22, res.v22_msg or f"{key}: no V22 result recorded (R19)")
            v22_lines.append(f"{key}={v22}")
            v22_results.append(v22)
            continue
        record("V10", result, msg)
        if result != "pass":
            failed.append(key)
        elif res.warnings:
            warned.append(key)
        if res.band_fail:
            off_floor.append(key)
    tested = [k for k in ctx.core_keys() if not is_v22_engine(ctx.spec(k))]
    if failed or missing:
        record("V10", "fail", f"summary: {len(failed) + len(missing)} of {len(tested)} engines failed "
               f"({', '.join(failed + missing)}); below their floor: {', '.join(off_floor) or 'none'}; "
               f"warns: {', '.join(warned) or 'none'}; V22 engines: {', '.join(v22_lines) or '-'}")
    else:
        record("V10", "pass", f"summary: all {len(tested)} engines load, generate, swap and release memory at their "
               f"fixed quantisations; decode recorded beside the projections (D-33 (c)); warns: "
               f"{', '.join(warned) or 'none'}; V22 engines: {', '.join(v22_lines) or '-'}")
    if v22_lines:
        # R19 generalised: deferrable, never a fail; the gate reads this line (the latest V22).
        verdict = "pass" if all(r == "pass" for r in v22_results) else "deferred"
        record("V22", verdict, f"summary: {', '.join(v22_lines)} (newer architectures and the Apex, R19 "
               "generalised, S-26: a deferred engine never blocks the phase; its seats use their D-40 fallback)")
    raise_decode_floor_todo(ctx)
    raise_input_todos(ctx)
    return 0


def cmd_table(ctx: Ctx, _args: argparse.Namespace) -> int:
    cols = ("engine", "load", "KV type", "decode 512/8k", "prefill 512/8k", "swap s", "footprint", "released",
            "control", "notes")
    rows: list[tuple[str, ...]] = []
    deviations: list[tuple[str, str]] = []
    for key in ctx.core_keys():
        spec = ctx.spec(key)
        res = load_result(ctx, key)
        if spec.get("baseline_deviation"):
            deviations.append((key, str(spec["baseline_deviation"])))
        if res is None:
            rows.append((key, "-", "-", "-", "-", "-", "-", "-", "-", "not tested"))
            continue
        if res.baseline_deviation and res.baseline_deviation != str(spec.get("baseline_deviation") or ""):
            deviations.append((key, res.baseline_deviation))
        notes = []
        if is_v22_engine(spec):
            notes.append(f"V22 {res.v22_result or '?'}")
        if res.band_fail:
            notes.append("BELOW FLOOR")
        if res.warnings:
            notes.append("warn")
        if res.prefill_8k_note:
            notes.append(f"8k {res.prefill_8k_note.split(':')[0]}")
        if res.known_issue_note:
            notes.append(f"S6 {NEMOTRON_ISSUE_ID} (fail)")
        if res.load_error:
            notes.append(res.load_error[:40])
        rows.append((key, "ok" if res.load_ok else "FAIL", res.kv_applied or res.kv_requested or "-",
                     f"{fmt_num(res.decode_tps_512)}/{fmt_num(res.decode_tps_8k)}",
                     f"{fmt_num(res.prefill_tps_512)}/{fmt_num(res.prefill_tps_8k)}", fmt_num(res.swap_s),
                     fmt_gb(res.footprint_bytes) if res.footprint_bytes is not None else "-",
                     "yes" if res.released else ("NO" if res.released is False else "?"),
                     res.control_mode or "?", "; ".join(notes) or "-"))
    widths = [max(len(str(r[i])) for r in [cols, *rows]) for i in range(len(cols))]
    for r in [cols, *rows]:
        print("  ".join(str(c).ljust(w) for c, w in zip(r, widths, strict=True)), file=sys.stderr)
    for name, label in (("residency.json", "single residency"), ("parity.json", "output parity (V25)"),
                        ("quality.json", "KLD record (S-03)")):
        f = ctx.results_dir / name
        if f.is_file():
            d = json.loads(f.read_text(encoding="utf-8"))
            print(f"{label}: {d.get('summary', '-')}", file=sys.stderr)
    # engines.json baseline_deviation_rule: every deviation from a closed value is printed here, never only in the file.
    print("Baseline deviations (engines.json baseline_deviation_rule):" if deviations else
          "Baseline deviations: none", file=sys.stderr)
    for key, text in deviations:
        print(f"  {key}: {text}", file=sys.stderr)
    return 0


# --- S52 Phase 3 items: the Principal's two to-dos at the end of Phase 3 (step 04) -----------------------------------

KV_TODO_ID = "kv-estimates"
CACHE_TODO_ID = "cache-ram"
CHAT_MODES = ("chat", "vision")  # engines.json modes started with --context-shift (phase2/engine-env.py CHAT_MODES)


def todo(tid: str, title: str, detail: str) -> None:
    """One to-do for the driver (lib/common.sh todo_add). Tabs and newlines would break the line protocol."""
    print(f"TODO\t{tid}\t{' '.join(title.split())}\t{' '.join(detail.split())}", flush=True)


def _ceil_to(x: float, step: int) -> int:
    return int(-(-x // step) * step)


def cache_suggestion(rec: dict[str, Any]) -> dict[str, Any]:
    """INFERRED, for the Principal to weigh (S52 (b)): a --cache-ram that holds a RAM copy of every slot at full
    length (the engine's measured context memory rounded up to 256 MiB, never above the 8192 MiB default; the cap now
    rendered kept when the context memory is UNKNOWN), and, where checkpoints are made, an explicit --ctx-checkpoints
    of one per --checkpoint-min-step across a full slot plus the one near the prompt's end. Worst cases: the cache
    limit plus every slot full of checkpoints of one slot's sliding-window, recurrent and compressor state.

    B-33 (measure 5, approved 2026-10-17): the Day 1 caps are set (engines.json cache_ram_mib, rendered as --cache-ram
    by phase2/engine-env.py), so the "rendered" worst case uses the cap the env file renders (profile cache_ram_mib,
    rendered_cache_ram_mib) with the default 32 checkpoints per slot; only a record with no rendered cap (an env file
    from before B-33) falls back to llama-server's 8192 MiB default, which the server then runs with. A rendered -1
    (no limit, cache_ram_rendered_mib kept negative; cap_unbounded) is counted at that default, so its "rendered"
    worst case is a lower bound, and the suggestion where the context memory is UNKNOWN is the default, not -1.

    "Not measured" is not "not made" (S52 fix round): with no K/V, recurrent or compressor size line in the log
    (checkpoints_known False: a journal without the -lv 4 lines, rotated or empty) whether the engine makes checkpoints,
    and their size, is UNKNOWN, so --ctx-checkpoints is suggested anyway (it does nothing on an engine that makes none)
    and the worst cases are lower bounds that leave the checkpoints out. ctx_checkpoints None means none are made: K/V
    lines were parsed and none of them is sliding-window, recurrent or compressor state."""
    m, prof = rec.get("measured") or {}, rec.get("profile") or {}
    parallel = max(int(prof.get("parallel") or 1), 1)
    window = m.get("n_ctx_slot") or int(prof.get("ctx") or 0) // parallel
    ctx_mib = m.get("context_mib")
    rendered = prof.get("cache_ram_mib")
    cap = LLAMA_CACHE_RAM_DEFAULT_MIB if rendered is None or int(rendered) < 0 else int(rendered)
    cache_ram = (cap if ctx_mib is None
                 else max(256, min(LLAMA_CACHE_RAM_DEFAULT_MIB, _ceil_to(ctx_mib, 256))))
    n = min(LLAMA_CTX_CHECKPOINTS_DEFAULT, -(-window // LLAMA_CHECKPOINT_MIN_STEP) + 1) if window else \
        LLAMA_CTX_CHECKPOINTS_DEFAULT
    ckpt = m.get("checkpoint_state_mib")
    base = {"cache_ram_mib": cache_ram, "cache_ram_rendered_mib": rendered}
    if ckpt is None and m.get("kv_mib") is None:
        return {**base, "ctx_checkpoints": n, "checkpoints_known": False,
                "worst_rendered_mib": cap, "worst_suggested_mib": cache_ram}
    if ckpt is None:
        return {**base, "ctx_checkpoints": None, "checkpoints_known": True,
                "worst_rendered_mib": cap, "worst_suggested_mib": cache_ram}
    return {**base, "ctx_checkpoints": n, "checkpoints_known": True,
            "worst_rendered_mib": round(cap + LLAMA_CTX_CHECKPOINTS_DEFAULT * parallel * ckpt),
            "worst_suggested_mib": round(cache_ram + n * parallel * ckpt)}


def cap_unbounded(sug: dict[str, Any]) -> bool:
    """True when the env file renders a negative --cache-ram: no size limit, so the rendered worst case is a lower
    bound (cache_suggestion)."""
    rendered = sug.get("cache_ram_rendered_mib")
    return rendered is not None and int(rendered) < 0


def resident_memory_record(ctx: Ctx, key: str) -> dict[str, Any]:
    """S52 (c) and (b) for a resident chat engine Phase 3 never loads (the router, Phase 2): its current invocation's
    journal and its host RAM now, read-only. No projection: resident models are never charged (Section 4.1)."""
    try:
        env = ctx.env(key)
        if not unit_active(key):
            return {"label": "resident", "error": f"{unit_of(key)} is not active", "unknown": ["not running"],
                    "taken_at": now_iso()}
        inv = unit_invocation_id(key)
        lines: list[str] = []
        if inv:
            p = run(["journalctl", "--no-pager", "-o", "cat", f"_SYSTEMD_INVOCATION_ID={inv}"], timeout=120)
            lines = p.stdout.splitlines()
        rec = memory_record(env, parse_memory_lines(lines), None,
                            "resident: never charged against the engine budget (Section 4.1)",
                            {"why": "not read: Phase 3 does not load the resident engines"}, host_memory(key),
                            "resident")
        if not lines:
            rec["unknown"].insert(0, f"the journal of its current invocation ({inv or 'none'}) is empty (rotated?)")
        return rec
    except Terminated:
        raise
    except Exception as exc:  # S52: a record, never a verdict
        return {"label": "resident", "error": f"{type(exc).__name__}: {exc}", "unknown": [str(exc)],
                "taken_at": now_iso()}


MemoryEntry = tuple[str, str, dict[str, Any], int | None]  # (engine, role, record, GTT footprint registered)


def memory_entries(ctx: Ctx) -> list[MemoryEntry]:
    """Every load's record from the result files (a DeepSeek rung is its own entry), then the resident chat engines."""
    out: list[MemoryEntry] = []
    for key in ctx.core_keys():
        res = load_result(ctx, key)
        if res is None:
            out.append((key, "not tested", {}, None))
            continue
        if not res.memory:
            out.append((key, "no record: the load failed, or the result predates S52", {}, None))
            continue
        for rec in res.memory:
            label = str(rec.get("label") or "")
            rung = next((r for r in res.ladder if r.get("kv") == label), None) if label else None
            fp = (rung or {}).get("footprint_bytes") if label else res.footprint_bytes
            role = ""
            if label:
                role = f"ladder rung {label}" + (", the rung that stands" if label == res.kv_applied else "")
            out.append((key, role, rec, fp))
    for key in ctx.order:
        spec = ctx.spec(key)
        if spec.get("arbiter_class") == "resident" and spec.get("mode", "chat") in CHAT_MODES:
            out.append((key, "resident, not loaded by Phase 3: current journal and RSS, read-only",
                        resident_memory_record(ctx, key), None))
    return out


def _head(key: str, role: str) -> str:
    return f"{key} ({role})" if role else key


def kv_item(key: str, role: str, rec: dict[str, Any], footprint: int | None) -> str:
    """One engine (or rung) in the kv-estimates to-do: measured context memory beside the projection."""
    if not rec or rec.get("error"):
        return f"{_head(key, role)}: UNKNOWN" + (f" (record not taken: {rec['error'][:160]})" if rec else "")
    m, prof, proj = rec["measured"], rec["profile"], rec.get("projected")
    text = (f"{_head(key, role)} {prof['ctx']} x {prof['parallel']} {prof['kv']}: context "
            f"{_gib(m['context_mib'])} GiB measured")
    if m.get("kv_mib") is not None:
        bits = [f"K/V {_gib(m['kv_mib'])} GiB in {m['kv_lines']} line(s)"]
        if m.get("swa_mib"):
            bits.append(f"of which sliding-window {_gib(m['swa_mib'])}")
        if m.get("recurrent_mib") is not None:
            bits.append(f"recurrent state {_gib(m['recurrent_mib'])}")
        if m.get("dsv4_state_mib") is not None:
            bits.append(f"compressor state {_gib(m['dsv4_state_mib'])}")
        text += f" ({', '.join(bits)})"
    else:
        text += " (no K/V size line in the load log: TRACE at the pin, printed only at -lv 4)"
    factor = (proj or {}).get("kv_factor")
    if proj is not None:
        text += (f" vs {proj['kv_bytes'] / GIB:.2f} GiB projected ({proj.get('per_token_f16')} B/token at f16 x "
                 f"{factor})")
    elif rec.get("label") == "resident":
        text += " (resident: never charged, Section 4.1)"
    else:
        why = next((u for u in rec.get("unknown", []) if u.startswith("projection")), "projection: ?")
        text += f" vs projection UNKNOWN ({why})"
    if m.get("context_mib") and factor and prof["ctx"]:
        text += f"; implied {m['context_mib'] * MIB / (prof['ctx'] * factor):,.0f} B/token at f16 at this profile"
    if m.get("compute_mib"):
        text += f"; compute buffers {_gib(sum(m['compute_mib'].values()))} GiB"
    if m.get("mmproj_compute_mib"):
        text += f" (+ mmproj {_gib(sum(m['mmproj_compute_mib'].values()))})"
    if footprint is not None:
        text += f"; GTT footprint {fmt_gb(footprint)} registered (rule 1)"
    return text


def cache_item(key: str, role: str, rec: dict[str, Any]) -> tuple[str, dict[str, Any] | None]:
    """One engine in the cache-ram to-do: its host RAM, what the log says of its caches, and the suggestion."""
    if not rec or rec.get("error"):
        return f"{_head(key, role)}: UNKNOWN" + (f" (record not taken: {rec['error'][:160]})" if rec else ""), None
    m, hl, ht = rec["measured"], rec["host_after_load"], rec["host_after_tests"]
    growth = rec.get("anon_growth_bytes")
    if rec.get("label") == "resident":
        text = f"{_head(key, role)}: RSS {_rss(ht)} GiB now (RssAnon {_rss(ht, 'RssAnon')})"
    else:
        text = (f"{_head(key, role)}: RSS {_rss(hl)} GiB after the load, {_rss(ht)} after the tests (RssAnon "
                f"{_rss(hl, 'RssAnon')} -> {_rss(ht, 'RssAnon')}, "
                f"{'UNKNOWN' if growth is None else f'{growth / MIB:+.0f} MiB'})")
    pc, ck = m["prompt_cache"], m["checkpoints"]
    if pc["evicted"]:
        text += f"; prompt cache: {pc['evicted']} eviction(s) logged, largest entry {pc['evicted_max_mib']} MiB"
    else:
        text += "; prompt cache: no eviction logged (below its limit)"
    if pc.get("state_max_mib") is not None:
        text += f", {pc['state_max_mib']} MiB in {pc['prompts_max']} prompt(s) at most (TRACE)"
    if pc.get("oversize_skipped"):
        text += f", {pc['oversize_skipped']} prompt(s) larger than the limit skipped"
    sug = cache_suggestion(rec)
    # B-33: the cap the env file renders (cache_suggestion), or llama-server's default where none is rendered; a
    # negative one is no limit, counted at the default, so its worst case is a lower bound.
    rendered, unbounded = sug["cache_ram_rendered_mib"], cap_unbounded(sug)
    if rendered is None:
        cap = f"no --cache-ram rendered (llama-server's default, {LLAMA_CACHE_RAM_DEFAULT_MIB} MiB)"
    elif unbounded:
        cap = f"--cache-ram {rendered} rendered (no limit; counted at {LLAMA_CACHE_RAM_DEFAULT_MIB} MiB, a lower bound)"
    else:
        cap = f"--cache-ram {rendered} rendered"
    upto = "at least" if unbounded else "up to"
    if not sug["checkpoints_known"]:  # not measured, which is not "none made" (cache_suggestion)
        if ck.get("erased") or ck.get("created_max"):
            made = f", {ck['created_max']} of {ck['limit']} made at most" if ck.get("created_max") else ""
            text += (f"; checkpoints: {ck['erased']} erased{made} logged, so it makes them, but one slot's state is "
                     "UNKNOWN (no K/V, recurrent or compressor size line in the load log)")
        else:
            text += ("; checkpoints: UNKNOWN (no K/V, recurrent or compressor size line in the load log, so whether "
                     "this engine makes them, and their size, is not known)")
        text += f"; {cap} (at least {sug['worst_rendered_mib']} MiB)"
        flags = f"--cache-ram {sug['cache_ram_mib']} --ctx-checkpoints {sug['ctx_checkpoints']}"
        return f"{text}; suggested {flags} (at least {sug['worst_suggested_mib']} MiB, checkpoints UNKNOWN)", sug
    if sug["ctx_checkpoints"] is None:
        text += f"; checkpoints: none made (its cache rolls back); {cap} ({upto} {sug['worst_rendered_mib']} MiB)"
        flags = f"--cache-ram {sug['cache_ram_mib']}"
    else:
        made = f", {ck['created_max']} of {ck['limit']} made at most (TRACE)" if ck.get("created_max") else ""
        text += (f"; checkpoints: {ck['erased']} erased{made}, one slot's state about "
                 f"{m['checkpoint_state_mib']:.1f} MiB (INFERRED), so {cap} and the default "
                 f"{LLAMA_CTX_CHECKPOINTS_DEFAULT} checkpoints per slot allow {upto} {sug['worst_rendered_mib']} MiB")
        flags = f"--cache-ram {sug['cache_ram_mib']} --ctx-checkpoints {sug['ctx_checkpoints']}"
    return f"{text}; suggested {flags} (up to {sug['worst_suggested_mib']} MiB)", sug


def is_weight_bearing(key: str, entries: Sequence[MemoryEntry]) -> bool:
    """True for an engine the Arbiter charges, False for a resident one (memory_entries' role)."""
    return not any(k == key and role.startswith("resident") for k, role, _, _ in entries)


KV_TODO_TITLE = ("Phase 3 measured each engine's context memory: compare it with the Arbiter's estimates and decide "
                 "whether to correct them (S52)")
CACHE_TODO_TITLE = ("The Day 1 prompt-cache caps are set (--cache-ram from engines.json cache_ram_mib, B-33): Phase 3 "
                    "measured the host RAM they and the checkpoints hold outside the Arbiter's budget; decide whether "
                    "to change a cap or set --ctx-checkpoints (S52)")


def arbiter_max_resident(ctx: Ctx) -> int:
    """The LLM-seat residency limit the Arbiter runs with (/arbiter/status max_resident, D-32), read once and never
    fatal: 1 (the D-32 (a) default) when the orchestrator cannot be asked or gives no usable figure."""
    headers = {ADMIN_TOKEN_HEADER: ctx.admin_token} if ctx.admin_token else {}
    try:
        code, body = http("GET", f"{ctx.orch_url.rstrip('/')}/arbiter/status", timeout=10, headers=headers)
    except (HttpFail, OSError):
        return 1
    value = body.get("max_resident") if code == 200 and isinstance(body, dict) else None
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 1 else 1


def raise_memory_todos(entries: Sequence[MemoryEntry], note: str = "", max_resident: int = 1) -> None:
    """Pure apart from the two TODO lines: kv-estimates and cache-ram with the measured figures in their detail.
    `max_resident` is the Arbiter's LLM-seat limit (D-32 (a): 1): the worst case sums that many of the largest seats,
    since an evicted server's prompt cache and checkpoints go with its process."""
    lead = f"{note}. " if note else ""
    kv_items = " | ".join(kv_item(k, role, rec, fp) for k, role, rec, fp in entries) or "no record"
    todo(KV_TODO_ID, KV_TODO_TITLE,
         lead + "Measured from each load's own log (the llama_kv_cache, llama_memory_recurrent and "
         "llama_dsv4_comp_state 'size =' lines of llama.cpp b11474) beside the Arbiter's projection for the same "
         "profile (arbiter.py projected_footprint, estimate): " + kv_items + ". The estimates are "
         "orchestrator/src/atlas/arbiter.py KV_BYTES_PER_TOKEN_F16 (an engines.json kv_bytes_per_token_f16 overrides "
         "one engine's) and err high on purpose (an out-of-memory backstop). The GTT footprint Phase 3 registered "
         "(POST /arbiter/register, Section 4.2 rule 1) replaces the projection only at the profile it was measured at "
         "and only until the orchestrator restarts: the Arbiter keeps it in memory (arbiter.py Arbiter.measured) and "
         "nothing replays it or re-measures an ordinary load (VERIFIED in the source). After any restart or reboot "
         "the estimates govern every load and eviction, at every profile (another context size, a KV-ladder "
         "rung, the Deep Think plans), so a corrected figure changes real Arbiter decisions. The implied figure is "
         "the measured context memory over ctx x the K/V type's size factor; recurrent state, sliding-window cells "
         "and compressor state are per slot and do not scale with ctx, so it holds at the measured profile only. "
         "B-03 (approved 2026-10-16): these measurements are adopted into the estimates for every kept engine before "
         "any long context is used. Updating the figures is the Principal's call; Phase 3 changed nothing.")
    by_engine: dict[str, tuple[str, dict[str, Any] | None]] = {}
    for k, role, rec, _ in entries:
        if k not in by_engine or "the rung that stands" in role:  # one per engine: the ladder's standing rung
            by_engine[k] = cache_item(k, role, rec)
    # (worst at the rendered caps, worst at the suggested values, checkpoints UNKNOWN: then both are lower bounds)
    seats = max(1, int(max_resident))
    heavy = sorted((s["worst_rendered_mib"], s["worst_suggested_mib"], not s["checkpoints_known"])
                   for k, (_, s) in by_engine.items() if s and is_weight_bearing(k, entries))[-seats:]
    light = [(s["worst_rendered_mib"], s["worst_suggested_mib"], not s["checkpoints_known"])
             for k, (_, s) in by_engine.items() if s and not is_weight_bearing(k, entries)]
    total = ""
    if heavy:
        d, s = (sum(w[i] for w in heavy + light) / 1024 for i in (0, 1))
        # every record, not only the summed ones: an UNKNOWN engine ranked out of the largest may be larger; a cap of
        # -1 (no limit, counted at the default) makes the rendered total a lower bound, not the suggested one
        unknown = any(s and not s["checkpoints_known"] for _, s in by_engine.values())
        unbounded = any(s and cap_unbounded(s) for _, s in by_engine.values())
        bound, least = "at worst at least" if unknown or unbounded else "up to", "at least " if unknown else ""
        which = "The largest LLM seat" if seats == 1 else f"The {seats} largest LLM seats"
        ranked = "the one ranked here" if seats == 1 else f"the {seats} ranked here"
        rule = ("Section 4.2 rule 3, D-32: one LLM seat resident; an evicted server's prompt cache and checkpoints "
                "go with its process" if seats == 1 else
                f"Section 4.2 rule 3: the Arbiter's limit is {seats} LLM seats (ATLAS_ARBITER_MAX_RESIDENT; D-32 (a) "
                "is 1)")
        total = (f" {which} plus the resident chat engine(s) could hold {bound} {d:.1f} GiB of host "
                 f"RAM at the caps now rendered (llama-server's 8192 MiB default where none is) and {least}{s:.1f} "
                 "GiB at the suggested values"
                 + (" (checkpoints UNKNOWN for at least one engine, whose checkpoints are left out of the sum "
                    f"and which may be larger than {ranked})" if unknown else "")
                 + (" (a negative --cache-ram, no limit, rendered for at least one engine, whose cache is counted "
                    f"at the {LLAMA_CACHE_RAM_DEFAULT_MIB} MiB default and which may be larger than {ranked})"
                    if unbounded else "")
                 + ", against the host reserve (16 GiB, S44) that also carries about 12 GB of CPU-side services "
                 f"({rule}).")
    todo(CACHE_TODO_ID, CACHE_TODO_TITLE,
         lead + "Each chat llama-server keeps a prompt cache in host RAM up to its --cache-ram (B-33, approved "
         "2026-10-17: the Day 1 caps are set, rendered by phase2/engine-env.py from engines.json cache_ram_mib, and "
         "each item below names the one its env file renders; llama-server's default without the flag is 8192 MiB "
         "per server; idle slots are copied into it at every new task, --cache-idle-slots on) and, where its cache "
         "cannot roll back (sliding-window or recurrent layers), up to 32 context checkpoints per slot "
         "(--ctx-checkpoints). Neither is on the GTT counter or in the Arbiter's grants; only the host reserve covers "
         "them. This is the Phase 3 measurement of whether to change the caps. Measured in "
         "Phase 3 (a few test requests, not normal use; the cache and checkpoint sizes are TRACE lines, logged at "
         "the -lv 4 phase2/engine-env.py renders, and RssAnon's growth over the tests is the measured bound): "
         + (" | ".join(text for text, _ in by_engine.values()) or "no record") + "." + total + " Suggested values, "
         "INFERRED: --cache-ram = the engine's measured context memory rounded up to 256 MiB, at most the 8192 "
         "default (a RAM copy of every slot; the rendered cap kept where the context memory is UNKNOWN); "
         "--ctx-checkpoints = the slot window / 8192 + 1 (one per --checkpoint-min-step across a full slot), where "
         "checkpoints are made or UNKNOWN (it does nothing on an engine that makes none). A changed cap goes in "
         "engines.json cache_ram_mib (extra_args and env refuse --cache-ram: _meta cache_ram_rule) and "
         "--ctx-checkpoints in extra_args (\"--ctx-checkpoints\", \"<N>\"), then phase2/engine-env.py re-renders; "
         "changing them is the Principal's call, Phase 3 changed no engine setting.")


def cmd_memory(ctx: Ctx, _args: argparse.Namespace) -> int:
    """S52 Phase 3 items at the end of Phase 3 (step 04). Never fails the phase and always raises both to-dos: a
    broken record is UNKNOWN in its detail, and unreadable records or a to-do that cannot be built still leave the
    two to-dos with the reason (SIGTERM still stops the run)."""
    note = ""
    try:
        entries = memory_entries(ctx)
    except Terminated:
        raise
    except Exception as exc:  # S52: records, never a verdict
        entries, note = [], f"Figures UNKNOWN: the result files could not be read ({type(exc).__name__}: {exc})"
        log(f"memory: {note}")
    for key, role, rec, _ in entries:
        if role.startswith("resident"):
            log(f"{key}: {memory_line(rec)}")
    try:
        raise_memory_todos(entries, note, arbiter_max_resident(ctx))
    except Terminated:
        raise
    except Exception as exc:  # S52: the to-dos go out with the reason rather than not at all
        why = (f"Figures UNKNOWN: the to-do could not be built ({type(exc).__name__}: {exc}); see "
               f"{ctx.results_dir}/<engine>.json, field memory")
        log(f"memory: {why}")
        todo(KV_TODO_ID, KV_TODO_TITLE, why)
        todo(CACHE_TODO_ID, CACHE_TODO_TITLE, why)
    return 0


# --- single residency (Section 17 step 3; V21, S-24) and the real-engine Arbiter proof (V14b) -----------------------


def queue_proof(a: dict[str, Any], b: dict[str, Any], solo_second_s: float) -> tuple[bool, str]:
    """Pure V21 arithmetic: did the second request start only after the first finished? With timings passed through,
    the second's busy time (prompt_ms + predicted_ms) is subtracted from its receive time to find when it started;
    without timings, its wall clock must cover the first's wall clock plus most of its own solo duration."""
    ta, tb = a.get("timings") or {}, b.get("timings") or {}
    wall_a, wall_b = a["t_recv"] - a["t_send"], b["t_recv"] - b["t_send"]
    if tb.get("prompt_ms") is not None and tb.get("predicted_ms") is not None:
        busy_b = (float(tb["prompt_ms"]) + float(tb["predicted_ms"])) / 1000.0
        start_b = b["t_recv"] - busy_b
        gap = start_b - a["t_recv"]
        ok = gap >= -1.0
        busy_a = (float(ta.get("prompt_ms", 0)) + float(ta.get("predicted_ms", 0))) / 1000.0 if ta else None
        proof = (f"timings: second started {gap:+.1f}s relative to the first's completion "
                 f"(second busy {busy_b:.1f}s of {wall_b:.1f}s wall; first {wall_a:.1f}s wall"
                 + (f", first busy {busy_a:.1f}s" if busy_a is not None else "") + ")")
        return ok, proof
    ok = wall_b >= wall_a + 0.8 * solo_second_s - 1.0
    proof = (f"wall-clock (no timings passed through): second took {wall_b:.1f}s vs {solo_second_s:.1f}s solo while "
             f"the first took {wall_a:.1f}s")
    return ok, proof


def residency_bounds(weights_bytes: int, budget_bytes: int) -> tuple[int, int]:
    """Pure V21 window for the GTT delta of the one resident seat (S-24, v0.4.0): at least RESIDENCY_LOW_FACTOR of its
    weights (less means it is not really resident), at most the weights plus the cache allowance and allocator slack,
    CAPPED at the live engine budget: a second seat left beside a 146-151 GiB one overflows the Arbiter's budget, so
    the cap is the memory half of the single-residency proof (D-32 (a))."""
    low = int(RESIDENCY_LOW_FACTOR * weights_bytes)
    high = min(weights_bytes + RESIDENCY_CACHE_BYTES + RESIDENCY_SLACK_BYTES, int(budget_bytes))
    return low, high


def pick_probe_key(ctx: Ctx, exclude: Sequence[str]) -> str:
    """The engine whose unit profile is rendered over budget for the V14b proof: the Deep Think generator
    glm-5.3-flash by preference (v0.4.0, S-43: it is in both the standard and the deep tier, so one over-budget
    profile makes both not fit), else the first non-Apex core engine not excluded. The unchanged abliterated entry is
    never a probe (its env file is left exactly as v0.3.5 renders it)."""
    cands = [k for k in ctx.core_keys() if k not in exclude and ctx.spec(k).get("arbiter_class") != "apex"
             and not k.endswith("-abliterated")]
    if DEEP_THINK_GENERATOR in cands:
        return DEEP_THINK_GENERATOR
    if not cands:
        raise Infra("no engine left for the V14b over-budget proof (engines.json lists no non-Apex seat?)")
    return cands[0]


def arbiter_refusal_proof(ctx: Ctx, ctl: Control, exclude: Sequence[str], out: dict[str, Any],
                          loaded: list[str]) -> tuple[bool, str]:
    """Section 21 V14, real engines: the Arbiter refuses an over-budget load and downgrades a Deep Think depth when
    the projected footprint exceeds budget. On a healthy node every engine fits the ~156 GiB budget alone (Section
    4.1) and the Arbiter EVICTS to make room rather than refusing, so an honest over-budget projection is produced
    the way the Arbiter itself reads it: the probe engine's unit env file (phase2/engine-env.py, root) is rendered
    with a ctx_size override doubled until /internal/deep-think/plan says the standard tier no longer fits; then
    (a) the deep tier is planned and must be downgraded, (b) POST /arbiter/load of that engine at that profile must be
    refused. The override is journalled in pending-overrides.json BEFORE the first render and restored in a finally
    block (SIGTERM is an exception here, so `systemctl stop` runs it; a SIGKILL/power cut is repaired by
    pending_recover() on the next invocation); nothing is loaded. The Arbiter's KV model (arbiter.py) scales with ctx,
    which is why doubling reaches over budget in a few steps. /internal/deep-think/plan is loopback-guarded and never
    sees the admin token (Control._hdr)."""
    key = pick_probe_key(ctx, exclude)
    spec = ctx.spec(key)
    prior_ctx = ctx.override_of(key, "ctx_size")
    base_ctx = int(prior_ctx or spec["ctx_size"])
    out["v14b_probe_engine"] = key
    out["v14b_prior_ctx_override"] = prior_ctx

    def plan(tier: str) -> dict[str, Any]:
        code, resp = ctl.post("/internal/deep-think/plan", {"tier": tier, "task_id": TASK_ID}, timeout=60)
        if code != 200 or not isinstance(resp, dict):
            raise RuntimeError(f"POST /internal/deep-think/plan {tier} answered {code}: {short(resp, 200)}")
        return resp

    pending = pending_begin(ctx, key, ["ctx_size"], "V14b over-budget probe profile")
    try:
        before = plan("deep")
        out["v14b_plan_deep_before"] = before
        log(f"residency/V14b: deep-think plan before the proof: granted={before.get('granted')} "
            f"({before.get('reason')}; required {fmt_gb(before.get('required_bytes') or 0)}, "
            f"budget {fmt_gb(before.get('budget_bytes') or 0)})")
        ctx_over = base_ctx
        env: dict[str, str] = {}
        over = False
        for _ in range(V14B_MAX_DOUBLINGS):
            ctx_over *= 2
            env = ctx.render(key, [("ctx_size", str(ctx_over))], [])
            p = plan("standard")
            if p.get("granted") != "standard":
                over = True
                break
            log(f"residency/V14b: {key} at ctx {ctx_over} still fits the standard tier "
                f"(required {fmt_gb(p.get('required_bytes') or 0)} of budget {fmt_gb(p.get('budget_bytes') or 0)})")
        if not over:
            return False, (f"could not render {key} over budget within {V14B_MAX_DOUBLINGS} doublings of ctx_size "
                           f"(last {ctx_over}); the Arbiter's projection does not scale with ctx as expected")
        # (a) rule 8: the deep tier must be downgraded now that its adversary engine does not fit.
        deep = plan("deep")
        out["v14b_plan_deep_over"] = deep
        downgraded = deep.get("granted") != "deep" and "downgrad" in str(deep.get("reason", "")).lower()
        a_msg = (f"deep-think deep -> {deep.get('granted')} ({deep.get('reason')}; budget "
                 f"{fmt_gb(deep.get('budget_bytes') or 0)})")
        # (b) rule 2: the load itself must be refused, not evicted around.
        try:
            code, resp = ctl.post("/arbiter/load", {"engine": key, "ctx": int(env["ATLAS_CTX_SIZE"]),
                                                    "parallel": int(env["ATLAS_PARALLEL"]), "task_id": TASK_ID},
                                  timeout=120)
        except HttpFail as exc:
            return False, f"{a_msg}; POST /arbiter/load {key} failed: {exc}"
        out["v14b_load_decision"] = resp if isinstance(resp, dict) else {"http": code, "body": short(resp)}
        if code != 200 or not isinstance(resp, dict):
            return False, f"{a_msg}; POST /arbiter/load {key} answered HTTP {code} {short(resp, 200)}"
        decision, reason = resp.get("decision"), str(resp.get("reason", ""))
        if decision == "granted":
            loaded.append(key)  # it really loaded: cleanup must unload it, and the proof failed
            return False, f"{a_msg}; the Arbiter GRANTED the over-budget load of {key} ({reason})"
        refused = decision == "refused" and "budget" in reason.lower()
        b_msg = (f"load of {key} at ctx {env['ATLAS_CTX_SIZE']} x {env['ATLAS_PARALLEL']} slots "
                 f"(projected {fmt_gb(resp.get('projected_bytes') or 0)}) -> {decision}: {reason}")
        ok = downgraded and refused
        msg = (f"{'refused' if refused else 'NOT refused'}: {b_msg}; "
               f"{'downgraded' if downgraded else 'NOT downgraded'}: {a_msg}; "
               "probe profile rendered through engine-env.py for the proof and restored")
        return ok, msg
    finally:
        env_back = pending_restore(ctx, pending)
        pending_end(ctx, pending)
        log(f"residency/V14b: {key} env restored (ctx {env_back.get('ATLAS_CTX_SIZE')}, "
            f"parallel {env_back.get('ATLAS_PARALLEL')})")


def engine_weights_bytes(spec: dict[str, Any]) -> int:
    """The weights the Arbiter budgets (D-31): the exact sum of files[].bytes when every file has a positive one
    (as config._exact_bytes requires), else footprint_gb read as GiB, as config.EngineSpec.footprint_bytes and the
    v03b floor read it (the unchanged abliterated entry, enumerated files)."""
    files = spec.get("files") or []
    if files and all(_positive_int(f.get("bytes")) for f in files):
        return sum(int(f["bytes"]) for f in files)
    return int(float(spec.get("footprint_gb") or 0) * GIB)


def _positive_int(value: Any) -> bool:
    try:
        return int(value) > 0
    except (TypeError, ValueError):
        return False


def cmd_residency(ctx: Ctx, args: argparse.Namespace) -> int:
    """Section 17 step 3 as v0.4.0 restates it (S-24; D-32 option (a): one LLM seat resident at a time; replaces the
    v0.3.5 two-residency test): FIRST is loaded through the Arbiter, then SECOND, and the Arbiter must evict FIRST to
    make room (rule 3 at limit 1, rule 7 for every seat): the Arbiter runs at limit 1 (/arbiter/status max_resident),
    its grant of SECOND names FIRST among the seats it evicted (/arbiter/load `evicted`), the ledger holds SECOND alone,
    FIRST's unit is stopped and the GTT delta is SECOND's alone (residency_bounds). No Day 1 pair of non-abliterated
    seats fits the budget together (gpt-oss-120b 68.3 + GLM-5.3-Flash 155.1 GiB against about 156 GiB), so with the
    shipped pair the budget alone would force this eviction at limit 2 too: the seat limit itself is proven by V14a
    (test_one_seat_limit_evicts_the_resident_seat); V21 proves the real swap at that limit. Then two generation
    requests through the orchestrator to the resident seat; the second must start after the first finished (rules 3,
    4). Slot save and restore across the
    swap is phased after Day 1 (S-35, T-13), so V21 says so instead of testing it. Then, with nothing resident, the
    real-engine refusal and Deep Think downgrade of Section 21 V14 (V14b)."""
    first, second = args.first, args.second
    ctx.spec(first)
    sspec = ctx.spec(second)
    ctl = Control(ctx.orch_url, ctx.admin_token)
    pending_recover(ctx)
    saved_baseline = read_baseline(ctx)
    out: dict[str, Any] = {"first": first, "second": second, "control_mode": ctl.mode, "tested_at": now_iso()}
    # Whatever is resident goes through the Arbiter first and the counter is polled once against the saved baseline
    # after the last unload (clear_residents). The test's own baseline is the SETTLED counter measured now.
    ledger = ctl.resident_keys()
    resident = [k for k in ctx.core_keys() if unit_active(k) or k in ledger]
    _, _, clear_note, baseline = clear_residents(ctl, resident, saved_baseline, "residency")
    out["cleared_first"] = {"engines": resident, "note": clear_note}
    out["baseline_bytes"] = baseline
    out["saved_baseline_bytes"] = saved_baseline
    if abs(baseline - saved_baseline) > RELEASE_TOLERANCE_BYTES:
        log(f"residency: baseline for this test {fmt_gb(baseline)} (settled now) differs from baseline.json "
            f"{fmt_gb(saved_baseline)}: the resident set changed since step 02; the settled figure is used")
    st = ctl.status() or {}
    budget = int(st.get("budget_bytes") or 0) or DEFAULT_BUDGET_BYTES
    out["max_resident"] = st.get("max_resident")
    loaded: list[str] = []

    def cleanup() -> list[str]:
        # Every engine of this test still resident is unloaded through the Arbiter and the counter must come back to
        # the baseline (Section 4.2 rule 5).
        problems: list[str] = []
        for k in [k for k in loaded if unit_active(k) or k in ctl.resident_keys()]:
            ok, _, note = unload_and_release(ctl, k, baseline)
            out[f"released_{k}"] = ok
            out[f"release_note_{k}"] = note
            if not ok:
                problems.append(f"{k} did not release after the single-residency test ({note})")
        return problems

    def finish(v21: tuple[str, str]) -> int:
        problems = cleanup()
        if problems:
            # Section 4.2 rule 5 is part of the residency proof itself: a leak fails V21, never a stale V10.
            v21 = ("fail", f"{v21[1]}; release: {' | '.join(problems)}")
            v14b = ("fail", "not attempted: memory still held after the residency test")
        else:
            # V14b with nothing resident: the probe engine is rendered over budget and must be refused, the deep
            # tier downgraded (arbiter_refusal_proof); nothing is loaded.
            try:
                ok, msg = arbiter_refusal_proof(ctx, ctl, (), out, loaded)
            except (RuntimeError, Infra) as exc:
                ok, msg = False, f"proof aborted: {exc}"
            log(f"residency: V14b {'PASS' if ok else 'FAIL'}: {msg}")
            v14b = ("pass" if ok else "fail", msg)
            more = cleanup()  # a granted over-budget probe load (the proof failed) is unloaded too
            if more:
                problems = more
                v14b = ("fail", f"{v14b[1]}; release: {' | '.join(more)}")
        out["v21"] = v21
        out["v14b"] = v14b
        out["control_mode"] = ctl.mode
        out["summary"] = f"V21 {v21[0]}: {v21[1][:200]} | V14b {v14b[0]}: {v14b[1][:200]}"
        (ctx.results_dir / "residency.json").write_text(json.dumps(out, indent=2, default=str) + "\n",
                                                         encoding="utf-8")
        record("V21", *v21)
        record("V14b", *v14b)
        if problems:
            # Rule 5, as in step 02 (halt_if_leaked): the records are written, then the phase STOPS, so the driver
            # does not mark step 03 done and resume background generation with memory still held.
            raise Infra(f"single-residency cleanup: {' | '.join(problems)}: the phase stops here, as the Arbiter halts "
                        f"(Section 4.2 rule 5); recorded in {ctx.results_dir / 'residency.json'}. Check the GTT "
                        f"counter and the llama-server@ units, restart {ORCH_UNIT}.service, then re-run step 03")
        return 0

    fenv = ctx.render(first)
    senv = ctx.render(second)
    for key, env in ((first, fenv), (second, senv)):
        if env.get("ATLAS_MODEL_PRESENT") != "1":
            return finish(("fail", f"{key}: model file not present ({env.get('ATLAS_MODEL_FILE')})"))
    secs: dict[str, float] = {}
    for key, env in ((first, fenv), (second, senv)):
        try:
            secs[key] = ctl.load(key, int(env["ATLAS_CTX_SIZE"]), int(env["ATLAS_PARALLEL"]),
                                 int(env["LLAMA_ARG_PORT"]))
        except RuntimeError as exc:
            out[f"load_error_{key}"] = str(exc)
            if unit_active(key) or key in ctl.resident_keys():
                loaded.append(key)  # unload it in cleanup even if half-loaded
            return finish(("fail", f"{key} failed to load: {str(exc)[:200]}"))
        loaded.append(key)
        out[f"decision_{key}"] = ctl.last_decision
        time.sleep(3)
        log(f"residency: {key} loaded in {secs[key]:.0f}s (ctx {env['ATLAS_CTX_SIZE']}, parallel "
            f"{env['ATLAS_PARALLEL']}); GTT used {fmt_gb(gtt_used_bytes())}; Arbiter: "
            f"{short(ctl.last_decision.get('reason', ''), 200)}")
    # Rule 3 at limit 1 / rule 7 (D-32 (a)): the grant of SECOND must have evicted FIRST, by the Arbiter's own account
    # (/arbiter/load `evicted`), at limit 1. No Day 1 non-abliterated pair fits the budget together, so the eviction
    # alone does not prove the limit (V14a does); a limit other than 1 is a V21 failure (D-32 (a)).
    ledger = [k for k in ctl.resident_keys() if k in ctx.core_keys()]
    first_gone = first not in ledger and not unit_active(first)
    decision_second = out.get(f"decision_{second}") or {}
    evicted = [str(k) for k in (decision_second.get("evicted") or [])]
    limit_ok = out["max_resident"] == 1
    single = ledger == [second] and first_gone and first in evicted
    out["ledger_after_second"] = ledger
    out["evicted_by_second"] = evicted
    out["limit_ok"] = limit_ok
    reason = str(decision_second.get("reason", ""))
    one_msg = (f"loading {second} evicted {first} (ledger {ledger}; Arbiter evicted {evicted}: {short(reason, 160)}; "
               f"limit {out['max_resident'] if out['max_resident'] is not None else '?'})" if single else
               f"NOT single residency after loading {second}: ledger {ledger}, {first} unit "
               f"{'still active' if unit_active(first) else 'stopped'}, Arbiter evicted {evicted} "
               f"(Arbiter: {short(reason, 160)})")
    if not limit_ok:
        one_msg += f"; limit is {out['max_resident']}, D-32 (a) expects 1 (ATLAS_ARBITER_MAX_RESIDENT)"
    time.sleep(5)
    used = gtt_used_bytes()
    delta = used - baseline
    weights = engine_weights_bytes(sspec)
    low, high = residency_bounds(weights, budget)
    memory_ok = low <= delta <= high
    mem_msg = (f"GTT delta {fmt_gb(delta)} for {second} alone (weights {fmt_gb(weights)} + its caches; accepted "
               f"{fmt_gb(low)}-{fmt_gb(high)}, the upper bound capped at the Arbiter's budget {fmt_gb(budget)}; used "
               f"{fmt_gb(used)}, baseline {fmt_gb(baseline)})")
    out.update({"gtt_used_bytes": used, "gtt_delta_bytes": delta, "weights_bytes": weights, "budget_bytes": budget,
                "accepted_low_bytes": low, "accepted_high_bytes": high, "single": single, "memory_ok": memory_ok})
    log(f"residency: {'OK' if single and memory_ok else 'NOT PROVEN'}: {one_msg}; {mem_msg}")

    # Solo timing of the resident seat (direct to its port) for the wall-clock reference; nothing else generates
    # meanwhile (the driver stops the timers, atlas-celery-gpu and the Open WebUI container around this step).
    sport = int(senv["LLAMA_ARG_PORT"])
    q_a = [{"role": "user", "content": "Write a 250-word essay about the history of lighthouses."}]
    q_b = [{"role": "user", "content": "Describe, in about 150 words, how a suspension bridge carries its load."}]
    require_generation_idle(ctx.orch_url, f"residency solo timing {second}")
    solo = chat(f"http://127.0.0.1:{sport}", second, q_b, 160)
    if solo["error"]:
        return finish(("fail", f"{one_msg}; {mem_msg}; solo generation failed: {solo['error'][:160]!r}"))
    d_solo = solo["t_recv"] - solo["t_send"]
    log(f"residency: solo duration of the second request's prompt on {second}: {d_solo:.1f}s")

    # Two requests at once THROUGH THE ORCHESTRATOR to the one resident seat; the second must run only after the
    # first finished (Section 4.2 rules 3, 4: one generation at a time).
    results: dict[str, dict[str, Any]] = {}
    seen_queue: list[str] = []
    stop_poll = threading.Event()

    def poll() -> None:
        while not stop_poll.is_set():
            st2 = ctl.status()
            if st2 and st2.get("generation_queue"):
                seen_queue.append(json.dumps(st2["generation_queue"])[:80])
            time.sleep(0.5)

    def fire(name: str, msgs: list[dict[str, str]], max_tokens: int) -> None:
        results[name] = chat(ctx.orch_url, second, msgs, max_tokens, path=ORCH_CHAT_PATH)

    poller = threading.Thread(target=poll, daemon=True)
    poller.start()
    th_a = threading.Thread(target=fire, args=("first", q_a, 256))
    th_b = threading.Thread(target=fire, args=("second", q_b, 160))
    th_a.start()
    time.sleep(0.5)
    th_b.start()
    th_a.join()
    th_b.join()
    stop_poll.set()
    poller.join(timeout=2)
    a, b = results["first"], results["second"]
    out["concurrent"] = {"first": {k: v for k, v in a.items() if k not in ("content", "reasoning")},
                         "second": {k: v for k, v in b.items() if k not in ("content", "reasoning")},
                         "queue_seen": seen_queue[:5]}
    if a["error"] or b["error"]:
        why = (f"orchestrator chat failed: first={a['error'][:140]!r} second={b['error'][:140]!r} "
               f"(contract: POST {ORCH_CHAT_PATH} with model = engine key, see loadtest.py header)")
        return finish(("fail", f"{one_msg}; {mem_msg}; {why}"))
    ok, proof = queue_proof(a, b, d_solo)
    if seen_queue:
        proof += f"; Arbiter status showed a generation queue ({seen_queue[0]})"
    log(f"residency: queueing {'PROVEN' if ok else 'NOT proven'}: {proof}")
    verb = "queued behind" if ok else "did NOT wait for"
    passed = ok and single and memory_ok and limit_ok
    return finish(("pass" if passed else "fail",
                   f"{one_msg}; {mem_msg}; the second generation request to {second} {verb} the first; {proof}; "
                   "slot save and restore across the swap: phased after Day 1 (S-35, T-13), not tested"
                   + ("" if memory_ok else "; memory out of tolerance")))


# --- S-02 output parity of the re-pin (V25) and S-03 KLD record ----------------------------------------------------
#
# Both run llama-perplexity, a measuring tool, not an engine unit: every LLM seat is first unloaded through the Arbiter
# (clear_residents, so its ledger is empty and the counter settles), background generation is stopped by the driver
# around the step, the orchestrator's generation slot must be idle before each run, and the GTT counter is polled back
# to the settled baseline after each run (Section 4.2 rule 5; a leak stops the phase after the records are written).
# The resident companions stay up (the router on the iGPU, the Qwen3 pair on the CPU: never charged). Outputs (VERIFIED
# at both pins, tools/perplexity/perplexity.cpp): "Final estimate: PPL = %.4lf +/- %.5lf" (:647), "Mean    KLD:
# %10.6lf ± %10.6lf" (:1926), "Same top p: %6.3lf ± %5.3lf %%" (:1982) and one "%zu\t%3.8lf%%\t[...]" line per
# HellaSwag task with the running acc_norm (:988).

PPL_RE = re.compile(r"Final estimate: PPL = ([0-9.]+) \+/- ([0-9.]+)")
KLD_MEAN_RE = re.compile(r"Mean +KLD: +([0-9.eE+-]+) ± +([0-9.eE+-]+)")
SAME_TOP_RE = re.compile(r"Same top p: +([0-9.]+) ± +([0-9.]+) %")
PPL_Q_RE = re.compile(r"Mean PPL\(Q\) +: +([0-9.]+)")
PPL_BASE_RE = re.compile(r"Mean PPL\(base\) +: +([0-9.]+)")
HS_LINE_RE = re.compile(r"^(\d+)\t([0-9.]+)%\t\[")


def parse_ppl(text: str) -> float | None:
    m = PPL_RE.search(text)
    return float(m.group(1)) if m else None


def parse_kld(text: str) -> dict[str, float] | None:
    """The KLD statistics of one `--kl-divergence` run; None when the run printed none (it failed)."""
    mean, top = KLD_MEAN_RE.search(text), SAME_TOP_RE.search(text)
    if not mean or not top:
        return None
    out = {"mean_kld": float(mean.group(1)), "mean_kld_unc": float(mean.group(2)),
           "same_top_pct": float(top.group(1)), "same_top_unc": float(top.group(2))}
    for name, rx in (("ppl_q", PPL_Q_RE), ("ppl_base", PPL_BASE_RE)):
        if m := rx.search(text):
            out[name] = float(m.group(1))
    return out


def parse_hellaswag(text: str) -> dict[str, Any] | None:
    """Per-task correctness from the running acc_norm lines (task i: round(acc_i * i) - round(acc_(i-1) * (i-1))),
    in the order the tool scored them: every task is taken (PARITY_TASKS_FILE), but always shuffled with std::mt19937
    seeded 1 (HS_ANSWERS_ORDER, recorded beside the answers), the same order on both builds of one toolchain, never
    file order. None when no task line was printed or the numbering skips."""
    accs: list[float] = []
    for ln in text.splitlines():
        if m := HS_LINE_RE.match(ln.strip("\r")):
            if int(m.group(1)) != len(accs) + 1:
                return None
            accs.append(float(m.group(2)))
    if not accs:
        return None
    correct, prev = [], 0
    for i, acc in enumerate(accs, start=1):
        now = round(acc * i / 100.0)
        correct.append(now - prev == 1)
        prev = now
    return {"n": len(accs), "correct": sum(correct), "acc_pct": accs[-1], "answers": correct,
            "answers_order": HS_ANSWERS_ORDER}


def changed_answers(a: dict[str, Any] | None, b: dict[str, Any] | None) -> int | None:
    if not a or not b or a["n"] != b["n"]:
        return None
    return sum(1 for x, y in zip(a["answers"], b["answers"], strict=True) if x != y)


def parity_engine_verdict(key: str, runs: dict[str, dict[str, Any]]) -> tuple[str, str]:
    """Pure V25 verdict for one engine (module constants; -fa on decides, -fa off is recorded): (result, message).
    The old build failing to run it is deferred (nothing to compare); the new build failing, a KLD above
    PARITY_MAX_MEAN_KLD, fewer than PARITY_MIN_SAME_TOP_PCT identical top-1 tokens or fewer correct tasks is a fail."""
    parts: list[str] = []
    verdict = "pass"
    for fa in PARITY_FA:
        r = runs.get(fa) or {}
        kld, old_t, new_t = r.get("kld"), r.get("old_tasks"), r.get("new_tasks")
        errors = r.get("errors") or []
        bits = []
        if kld:
            bits.append(f"KLD {kld['mean_kld']:.6f}, top-1 same {kld['same_top_pct']:.2f}%")
        if old_t and new_t:
            ch = changed_answers(old_t, new_t)
            bits.append(f"tasks {old_t['correct']}->{new_t['correct']} of {new_t['n']}"
                        + (f", {ch} changed" if ch is not None else ""))
        if errors:
            bits.append("errors: " + "; ".join(e[:160] for e in errors))
        parts.append(f"-fa {fa}: " + (", ".join(bits) or "not run"))
        if fa != "on":
            continue
        if r.get("old_failed"):
            verdict = "deferred"
            continue
        if r.get("new_failed") or not kld or not new_t or not old_t:
            verdict = "fail"
            continue
        if (kld["mean_kld"] > PARITY_MAX_MEAN_KLD or kld["same_top_pct"] < PARITY_MIN_SAME_TOP_PCT
                or new_t["correct"] < old_t["correct"]):
            verdict = "fail"
    return verdict, (f"{key}: b11474 vs v0.4.1 {'within' if verdict == 'pass' else 'NOT within'} parity at -fa on "
                     f"(mean KLD <= {PARITY_MAX_MEAN_KLD}, top-1 >= {PARITY_MIN_SAME_TOP_PCT}%, no fewer correct "
                     f"tasks; -fa off recorded only): " + " | ".join(parts))


def parity_coverage(ctx: Ctx) -> tuple[list[str], list[str]]:
    """(covered keys, notes on the others): chat engines both pins load (min_llama_cpp_tag not newer than v0.4.1),
    LLM seats and the resident router alike. The embedder and reranker have no next-token logits to compare; the
    unchanged abliterated entry is left as v0.3.5 has it (the uncensored seat is the Principal's to configure)."""
    covered, notes = [], []
    for key in ctx.order:
        spec = ctx.spec(key)
        mode = spec.get("mode", "chat")
        if mode not in CHAT_MODES:
            notes.append(f"{key}: not compared ({mode}: no next-token logits; T-15 checks it at the pin)")
        elif key.endswith("-abliterated"):
            notes.append(f"{key}: not compared (unchanged by this revision; the Principal's to configure)")
        elif is_newer_arch(spec):
            notes.append(f"{key}: not comparable (first build {spec.get('min_llama_cpp_tag')}, v0.4.1 cannot load it; "
                         "T-03 and T-04 cover it)")
        elif spec.get("arbiter_class") in P3_CLASSES or spec.get("arbiter_class") == "resident":
            covered.append(key)
    return covered, notes


def build_corpus(ctx: Ctx, work: Path) -> Path:
    """The fixed corpus: the domain profiles shipped with the scripts (config/domains/*.md), concatenated in name
    order, so both builds and every engine read byte-identical text on the node with no download."""
    files = sorted((ctx.engines_path.parent / "domains").glob("*.md"))
    if not files:
        raise Infra(f"no corpus: {ctx.engines_path.parent / 'domains'} has no *.md (CONVENTIONS.md §1 layout)")
    out = work / "corpus.txt"
    out.write_text("\n\n".join(f.read_text(encoding="utf-8") for f in files), encoding="utf-8")
    return out


def run_tool(cmd: list[str], work: Path, timeout: float = PARITY_TOOL_TIMEOUT_S) -> tuple[int, str]:
    """One llama-perplexity run with a scrubbed environment (no proxy, no HF variables, the RADV cache in the work
    directory); returns (exit code, stdout+stderr)."""
    env = {"PATH": "/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin", "HOME": str(work),
           "XDG_CACHE_HOME": str(work / "cache"), "NO_PROXY": "*", "no_proxy": "*", "LC_ALL": "C.UTF-8"}
    try:
        p = subprocess.run(cmd, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=timeout,
                           check=False, env=env, cwd=str(work))
    except subprocess.TimeoutExpired:
        return 124, f"{cmd[0]} timed out after {timeout:.0f}s"
    except OSError as exc:
        return 127, f"{cmd[0]}: {exc}"
    return p.returncode, (p.stdout or "") + "\n" + (p.stderr or "")


class Measured:
    """The guard around every direct tool run (section comment): generation idle before, release polled after."""

    def __init__(self, ctx: Ctx, baseline: int, out: dict[str, Any]) -> None:
        self.ctx, self.baseline, self.out = ctx, baseline, out
        self.leaks: list[str] = []

    def run(self, label: str, cmd: list[str], work: Path) -> tuple[int, str]:
        require_generation_idle(self.ctx.orch_url, label)
        log(f"{label}: {' '.join(cmd)[:300]}")
        rc, text = run_tool(cmd, work)
        ok, _secs, note = wait_release(self.baseline, label)
        if not ok:
            self.leaks.append(f"{label}: {note}")
        self.out.setdefault("runs", []).append({"label": label, "rc": rc, "release": note,
                                                "tail": " ".join(text.split())[-400:]})
        return rc, text


def _ppl_args(model: str, fa: str, kv: str = "") -> list[str]:
    args = ["-m", model, "-ngl", "all", "-fa", fa]
    if kv and kv != "none":
        args += ["--cache-type-k", kv, "--cache-type-v", kv]
    return args


def _clear_for_tools(ctx: Ctx, ctl: Control, who: str) -> int:
    """Every LLM seat unloaded through the Arbiter; returns the settled counter (the tools' baseline)."""
    ledger = ctl.resident_keys()
    resident = [k for k in ctx.core_keys() if unit_active(k) or k in ledger]
    _, _, note, settled = clear_residents(ctl, resident, read_baseline_optional(ctx), who)
    log(f"{who}: every LLM seat unloaded through the Arbiter ({note}); baseline {fmt_gb(settled)}")
    return settled


def cmd_parity(ctx: Ctx, args: argparse.Namespace) -> int:
    """S-02 (V25): see the section comment and parity_engine_verdict."""
    ctl = Control(ctx.orch_url, ctx.admin_token)
    pending_recover(ctx)
    ctx.results_dir.mkdir(parents=True, exist_ok=True)
    out: dict[str, Any] = {"tested_at": now_iso(), "new": args.perplexity, "old": args.parity_perplexity}
    covered, notes = parity_coverage(ctx)
    out["covered"], out["not_covered"] = covered, notes
    missing = [b for b in (args.perplexity, args.parity_perplexity) if not os.access(b, os.X_OK)]
    if missing:
        msg = (f"summary: the llama-perplexity build(s) {missing} are missing (phase2/01-llama.sh builds both: "
               f"sudo {os.environ.get('ATLAS_ENTRY', './atlas-day1.sh')} phase2 --force 01)")
        (ctx.results_dir / "parity.json").write_text(json.dumps({**out, "summary": msg}, indent=2) + "\n",
                                                      encoding="utf-8")
        record("V25", "fail", msg)
        return 0
    baseline = _clear_for_tools(ctx, ctl, "parity")
    work = ctx.results_dir / "parity-work"
    work.mkdir(parents=True, exist_ok=True)
    corpus = build_corpus(ctx, work)
    guard = Measured(ctx, baseline, out)
    verdicts: dict[str, str] = {}
    out["engines"] = {}
    try:
        for key in covered:
            env = ctx.env(key)
            model = env.get("ATLAS_MODEL_FILE", "")
            if env.get("ATLAS_MODEL_PRESENT") != "1":
                verdicts[key] = "deferred"
                record("V25", "deferred", f"{key}: model file not present ({model})")
                continue
            runs: dict[str, dict[str, Any]] = {}
            for fa in PARITY_FA:
                r: dict[str, Any] = {"errors": []}
                base = work / f"{key}-fa{fa}.kld"
                rc, text = guard.run(f"parity {key} v0.4.1 -fa {fa} base logits", [
                    args.parity_perplexity, *_ppl_args(model, fa), "-f", str(corpus), "-c", str(PARITY_CTX),
                    "-b", str(PARITY_CTX), "--chunks", str(PARITY_CHUNKS), "--kl-divergence-base", str(base)], work)
                r["old_ppl"] = parse_ppl(text)
                if rc != 0 or not base.is_file():
                    r["old_failed"] = True
                    r["errors"].append(f"v0.4.1 base run exit {rc}: {' '.join(text.split())[-200:]}")
                else:
                    rc, text = guard.run(f"parity {key} b11474 -fa {fa} KLD", [
                        args.perplexity, *_ppl_args(model, fa), "-c", str(PARITY_CTX), "-b", str(PARITY_CTX),
                        "--kl-divergence-base", str(base), "--kl-divergence"], work)
                    r["kld"] = parse_kld(text)
                    if rc != 0 or r["kld"] is None:
                        r["new_failed"] = True
                        r["errors"].append(f"b11474 KLD run exit {rc}: {' '.join(text.split())[-200:]}")
                base.unlink(missing_ok=True)
                for which, binary in (("old", args.parity_perplexity), ("new", args.perplexity)):
                    rc, text = guard.run(f"parity {key} {which} build -fa {fa} task set", [
                        binary, *_ppl_args(model, fa), "-f", str(PARITY_TASKS_FILE), "--hellaswag",
                        "--hellaswag-tasks", "1000", "-c", str(PARITY_TASK_CTX)], work)
                    tasks = parse_hellaswag(text)
                    r[f"{which}_tasks"] = tasks
                    if rc != 0 or tasks is None:
                        r[f"{which}_failed"] = True
                        r["errors"].append(f"{which} task run exit {rc}: {' '.join(text.split())[-160:]}")
                runs[fa] = r
            result, msg = parity_engine_verdict(key, runs)
            out["engines"][key] = {"runs": runs, "result": result, "msg": msg}
            verdicts[key] = result
            record("V25", result, msg)
    finally:
        out["leaks"] = guard.leaks
    results = set(verdicts.values())
    summary_result = "fail" if "fail" in results or guard.leaks else ("pass" if results == {"pass"} else "deferred")
    out["summary"] = (f"summary: {', '.join(f'{k}={v}' for k, v in verdicts.items()) or 'nothing compared'}; "
                      + "; ".join(notes) + (f"; LEAK: {' | '.join(guard.leaks)}" if guard.leaks else ""))
    (ctx.results_dir / "parity.json").write_text(json.dumps(out, indent=2, default=str) + "\n", encoding="utf-8")
    record("V25", summary_result, out["summary"])
    if guard.leaks:
        raise Infra(f"parity: memory not released after a llama-perplexity run ({' | '.join(guard.leaks)}): the phase "
                    "stops here (Section 4.2 rule 5); check the GTT counter, then re-run step 02b")
    return 0


KLD_REFERENCE_NOTE = {
    "glm-5.3-flash": ("UD-IQ4_XS is lossy (mean KLD 0.117 on Unsloth's own harness); its Q8_0 reference (317 GiB) "
                      "exceeds memory, so T-03 measures a task delta against published full-precision scores or "
                      "streamed base logits"),
    "deepseek-v4-flash": ("UD-Q8_K_XL is bit-exact to the official weights (KLD about 0 by construction); T-03 scores "
                          "the q8_0 KV rung against f16 on the same file"),
    "gpt-oss-120b": ("the F16-trunk GGUF with native MXFP4 experts is the highest-precision file; no higher reference "
                     "exists to pull"),
}
KLD_TODO_TITLE = ("KLD run (T-03): score each Day 1 seat against a reference with llama-perplexity --kl-divergence on "
                  "Wikitext-2 plus a domain corpus (S-03)")


def cmd_quality(ctx: Ctx, args: argparse.Namespace) -> int:
    """S-03 (a record with a to-do, as S52 did for kv-estimates): each Day 1 LLM seat (core/apex chat or vision, the
    unchanged abliterated entry excepted) is scored by the pin's llama-perplexity at its own KV type with -fa on: the
    perplexity of the fixed corpus and the deterministic task set, the figures a later reference run (T-03) compares
    against. Raised as the to-do kld-run with the figures and each seat's reference status; never a verdict, never an
    Infra stop for a measurement (a rule-5 leak still stops the phase)."""
    ctl = Control(ctx.orch_url, ctx.admin_token)
    pending_recover(ctx)
    ctx.results_dir.mkdir(parents=True, exist_ok=True)
    out: dict[str, Any] = {"tested_at": now_iso(), "tool": args.perplexity, "engines": {}}
    items: list[str] = []
    if not os.access(args.perplexity, os.X_OK):
        items.append(f"not measured: {args.perplexity} is missing (phase2/01-llama.sh S-01 builds it)")
    else:
        baseline = _clear_for_tools(ctx, ctl, "quality")
        work = ctx.results_dir / "quality-work"
        work.mkdir(parents=True, exist_ok=True)
        corpus = build_corpus(ctx, work)
        guard = Measured(ctx, baseline, out)
        for key in ctx.core_keys():
            spec = ctx.spec(key)
            if key.endswith("-abliterated") or spec.get("mode", "chat") not in CHAT_MODES:
                continue
            env = ctx.env(key)
            kv = env.get("ATLAS_KV_TYPE", spec.get("kv_class", ""))
            rec: dict[str, Any] = {"kv": kv}
            if env.get("ATLAS_MODEL_PRESENT") != "1":
                rec["error"] = f"model file not present ({env.get('ATLAS_MODEL_FILE')})"
            else:
                model = env["ATLAS_MODEL_FILE"]
                rc, text = guard.run(f"quality {key} perplexity", [
                    args.perplexity, *_ppl_args(model, "on", kv), "-f", str(corpus), "-c", str(PARITY_CTX),
                    "-b", str(PARITY_CTX), "--chunks", str(PARITY_CHUNKS)], work)
                rec["ppl"] = parse_ppl(text) if rc == 0 else None
                rc, text = guard.run(f"quality {key} task set", [
                    args.perplexity, *_ppl_args(model, "on", kv), "-f", str(PARITY_TASKS_FILE), "--hellaswag",
                    "--hellaswag-tasks", "1000", "-c", str(PARITY_TASK_CTX)], work)
                rec["tasks"] = parse_hellaswag(text) if rc == 0 else None
                if rec["ppl"] is None or rec["tasks"] is None:
                    rec["error"] = f"a run printed no result (exit {rc}: {' '.join(text.split())[-160:]})"
            out["engines"][key] = rec
            t = rec.get("tasks")
            items.append(f"{key} ({kv} KV, -fa on): corpus PPL "
                         f"{'UNKNOWN' if rec.get('ppl') is None else round(rec['ppl'], 4)}, tasks "
                         f"{'UNKNOWN' if not t else str(t['correct']) + ' of ' + str(t['n'])}"
                         + (f" ({rec['error'][:160]})" if rec.get("error") else "")
                         + f"; reference: {KLD_REFERENCE_NOTE.get(key, 'none recorded')}")
        out["leaks"] = guard.leaks
    out["summary"] = " | ".join(items) or "no seat measured"
    (ctx.results_dir / "quality.json").write_text(json.dumps(out, indent=2, default=str) + "\n", encoding="utf-8")
    todo(KLD_TODO_ID, KLD_TODO_TITLE,
         "Measured in Phase 3 step 02c on the pin b11474 (the fixed corpus of config/domains, 512-token windows; the "
         "deterministic task set phase3/parity-tasks.txt): " + out["summary"] + ". These are the figures a reference "
         "run compares against. The KLD itself needs a reference file per seat that Day 1 does not pull (T-03, D-02: "
         "GLM's seats stay provisional until it runs); the changed-answer count is taken against the same task set. "
         "With it, fix each seat's reasoning setting (D-30 (b): a fixed setting per seat, chosen after the refusal and "
         "KLD tests; provisional Day 1 settings: DeepSeek-V4-Flash-0731 reasoning on at high effort, gpt-oss-120b "
         "reasoning_effort high, GLM-5.3-Flash its template default; engines.json extra_args and env). "
         "Phase 3 changed no engine setting; only the Principal closes this to-do.")
    if out.get("leaks"):
        raise Infra(f"quality: memory not released after a llama-perplexity run ({' | '.join(out['leaks'])}): the "
                    "phase stops here (Section 4.2 rule 5); check the GTT counter, then re-run step 02c")
    return 0


# --- main -------------------------------------------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--engines", required=True, help="config/engines.json")
    ap.add_argument("--env-dir", required=True, help="$ATLAS_ETC/engines")
    ap.add_argument("--results-dir", required=True, help="$ATLAS_STATE/phase3/results")
    ap.add_argument("--orch-url", required=True, help="http://127.0.0.1:$ORCH_PORT (loopback only)")
    ap.add_argument("--engine-env", required=True, help="phase2/engine-env.py")
    ap.add_argument("--models-dir", required=True)
    ap.add_argument("--slots-dir", required=True)
    ap.add_argument("--port-base", type=int, required=True)
    ap.add_argument("--overrides", required=True, help="$ATLAS_ETC/engines/overrides.json")
    ap.add_argument("--admin-token-file", default=None,
                    help="ORCH_ADMIN_TOKEN_FILE of orchestrator.env when configured (X-Atlas-Token for /arbiter/*)")
    ap.add_argument("--atlas-python", default=str(Path(os.environ.get("ATLAS_OPT", "/opt/atlas")) / "venv/bin/python"),
                    help="$ATLAS_OPT/venv/bin/python: runs the Arbiter's own projection for the S52 memory record")
    ap.add_argument("--perplexity", default="/usr/local/bin/llama-perplexity",
                    help="the pin's llama-perplexity (phase2/01-llama.sh, S-01)")
    parity_tool = Path(os.environ.get("ATLAS_OPT", "/opt/atlas")) / "llama-parity/dist/bin/llama-perplexity"
    ap.add_argument("--parity-perplexity", default=str(parity_tool),
                    help="the old pin v0.4.1's llama-perplexity, built beside it for V25 only (S-02)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("prepare").set_defaults(fn=cmd_prepare)
    p = sub.add_parser("engine")
    p.add_argument("key")
    p.add_argument("--previous", default=None)
    p.set_defaults(fn=cmd_engine)
    p = sub.add_parser("finish")
    p.add_argument("--previous", default=None)
    p.set_defaults(fn=cmd_finish)
    sub.add_parser("summarize").set_defaults(fn=cmd_summarize)
    sub.add_parser("table").set_defaults(fn=cmd_table)
    sub.add_parser("memory").set_defaults(fn=cmd_memory)
    p = sub.add_parser("residency")
    p.add_argument("--first", required=True, help="the seat loaded first, which the second must evict (S-24)")
    p.add_argument("--second", required=True)
    p.set_defaults(fn=cmd_residency)
    sub.add_parser("parity").set_defaults(fn=cmd_parity)
    sub.add_parser("quality").set_defaults(fn=cmd_quality)
    args = ap.parse_args(argv)
    # `systemctl stop atlas-day1-phase3` sends SIGTERM to the whole cgroup: turn it into an exception in the main
    # thread so every finally block (override restores, pending journal) runs before exit (CONVENTIONS.md §7.3, §7.4).
    signal.signal(signal.SIGTERM, _on_sigterm)
    try:
        require_loopback(args.orch_url)
        ctx = Ctx(engines_path=Path(args.engines), env_dir=Path(args.env_dir), results_dir=Path(args.results_dir),
                  orch_url=args.orch_url, engine_env=Path(args.engine_env), models_dir=Path(args.models_dir),
                  slots_dir=Path(args.slots_dir), port_base=args.port_base, overrides=Path(args.overrides),
                  admin_token=read_admin_token(args.admin_token_file), atlas_python=args.atlas_python)
        log(f"{args.cmd}: orchestrator {args.orch_url}, GTT counter under {drm_root()}"
            + (" (admin token loaded)" if ctx.admin_token else ""))
        ctx.load_engines()
        return int(args.fn(ctx, args))
    except Terminated as exc:
        log(f"TERMINATED: {exc}")
        return 143
    except Infra as exc:
        log(f"FATAL: {exc}")
        return 2


if __name__ == "__main__":
    sys.exit(main())
