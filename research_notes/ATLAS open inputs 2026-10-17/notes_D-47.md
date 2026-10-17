# D-47 (with D-33 (c), V10, T-06): decode floors, the deep-tier floor, and the V4.1 trial's sampling

Research only, 2026-10-17. No repository file was edited. Labels: VERIFIED (read in a primary source, file:line or URL),
INFERRED (my arithmetic or judgement, shown), UNVERIFIED. Arithmetic: `rec/d47_calc.py` (this folder).
Scope: gpt-oss-120b, GLM-5.3-Flash, DeepSeek-V4-Flash-0731, the router; the V4.1 trial.

## 1. The recommendation in one paragraph

Set each seat a fixed **usability floor** in tok/s, stated at an 8k-token context. It is checked against the slower of
the two Phase 3 points (512 and 8k), and **it alone fails V10**. Floors: gpt-oss-120b **25**, GLM-5.3-Flash **6**,
DeepSeek-V4-Flash-0731 **4**, router **20** (checked at V10a). The **deep-tier floor is 4 tok/s at 8k**. It applies to
whichever engine holds the Apex synthesis role, 0731 now and V4.1 in the trial, measured at the slowest of the node's
own sampling settings. A **review line at 70% of the projection** is recorded with a to-do and never fails: the
projections have never been measured on this chip, and failing on them would bring back the band rule D-33 removed.
Prefill gets a floor too, at the 8k point: **GLM 40 tok/s** (the long-document seat), DeepSeek 40, gpt-oss-120b 200.
Speed floors cannot tell "the whole model fell to the CPU" from "slow GPU" on this unified-memory chip (§4). So V10
should also check placement directly: all layers offloaded, the model buffer on Vulkan0, a GTT delta of at least
0.85 × the weights, and the graph-splits line recorded. Trial sampling: greedy for comparison with every published
figure, plus the temperatures the node actually sends DeepSeek: **0.15** (Arthur and the adversary scoring), **0.6**
(deep synthesis) and 1.0 (server default). Each is run with DSpark off and on. Under this floor V4.1's ~14–16 tok/s
clears 4 by 3.5× and 0731's projected 7 clears it by 1.75×. Speed therefore decides nothing in the trial, and T-30's
blind scores decide it.

## 2. What the code does today (VERIFIED)

- V10 timing points: `PREFILL_SHORT = 512`, `PREFILL_LONG = 8192`, `N_PREDICT = 128` (scripts/day1/phase3/loadtest.py:278-280).
  Each point is a raw `POST /completion` at `temperature: 0` with `cache_prompt: False` (loadtest.py:1551), using a
  random-word prompt (loadtest.py:1529-1546).
- The floor verdict uses **only the 512-token decode**: `check_band` reads `res.decode_tps_512` (loadtest.py:1795) and
  runs right after the 512 point, before the 8k point is measured (loadtest.py:1870-1883). A null `decode_floor_tok_s`
  is recorded only. A value below a non-null floor is a V10 fail "no tolerance" (loadtest.py:1812-1814).
  The 8k decode and prefill are recorded but never judged (loadtest.py:1933-1934).
- The to-do text tells the Principal to set "decode_floor_tok_s (tok/s at 512 tokens)" (loadtest.py:2331-2335).
- The consequence of a fail differs by seat. gpt-oss-120b's V10 fail blocks the Phase 3 gate. GLM (newer architecture)
  and the Apex defer through V22, "its seats fall back to their D-40 fallback_engine" (loadtest.py:84-86, 1851-1865;
  doc L1080 gate `V4 V10 V14b V21 V25 -- V22`). **A false floor fail on GLM or DeepSeek therefore sends their seats to
  gpt-oss-120b.** That is a quality loss, so these floors must never fail a healthy file.
- engines.json: `projected_decode_tok_s` gpt-oss [57, 60] (L51), GLM [12.5, 14.0] (L119), DeepSeek [7, 7] (L159).
  `decode_floor_tok_s` is null on all three (L53, L121, L161). The router entry has neither field (L170 ff.).
  `_meta.decode_rule` is at L14.
- Router: V10a sends one chat request with `max_tokens: 16, temperature: 0` and prints `predicted_per_second`
  (scripts/day1/verify/v10a-router-resident.sh:43, 64-67). A rate taken over a 1–3-token "OK" reply is noise.
- The footprint is a GTT delta registered with the Arbiter, with **no lower-bound check** in V10
  (loadtest.py:1770-1781). The 0.85 × weights lower bound exists only in V21's two-seat residency proof
  (loadtest.py:293, `RESIDENCY_LOW_FACTOR = 0.85`).
- At the pin, the load log carries `offloaded %d/%d layers to GPU` (lcpp src/llama-model.cpp:1936),
  `%12s model buffer size` (:1942) and `graph: nodes = …, splits = …` (src/llama-context.cpp:758), all at INFO, which
  the units log at `--verbosity 4` (loadtest.py:117-119 docstring). The lcpp checkout is at b9acf138, 2026-10-07.

### The sampling the node actually sends DeepSeek (VERIFIED; corrects the register's premise)
- engines.json gives the DeepSeek server `--temp 1.0 --top-p 1.0` (extra_args). That is only the default for a request
  that carries no temperature.
- api.py:1407-1411: when a request has no temperature, the persona's is used. arthur.md:19 says `low-not-zero`, which
  maps to **0.15**. ren.md:19 says **0.8**. No other persona sets one.
- deep_think.py:72-73: generator 0.8, adversary **0.15**. Deep synthesis on DeepSeek runs at **0.6**
  (deep_think.py:466).
- So Arthur's chats, the strong cross-checks Arthur runs, and Deep Think scoring all run DeepSeek at 0.15. Ren's
  cross-checks on DeepSeek run at 0.8, the deep synthesis at 0.6, and 1.0 applies only to persona-less calls. The
  register says "the node samples DeepSeek at temperature 1.0" (D-47 why; Lucebox report L374). That is the server
  default, not what the Apex roles use.

### Token budgets that set "usable" (VERIFIED)
- deep_think.py: standard tier calls with 2,048 max tokens, the deep tier with 3,072, and adversary scoring with 1,024
  per trajectory (deep_think.py:255-265, 332, 360, 406). Deep tier = 8 GLM calls × 3,072 plus 6 DeepSeek scorings ×
  1,024, plus 1 DeepSeek synthesis × 3,072 (deep_think.py:400-470).
- The node's own budget for "one generation" is `DEFAULT_TIMEOUT_SLACK_S = 900.0`, on top of the 1,800 s slot wait
  (orchestrator/src/atlas/tasks/__init__.py:186-197).
- Reasoning: DeepSeek runs `--reasoning on --reasoning-effort high` (engines.json extra_args). GLM's template
  `reasoning_effort` defaults to max (engines.json notes). gpt-oss runs at high effort. `max_tokens` counts reasoning
  tokens, so the times below are for **generated** tokens, reasoning included.

## 3. The speed evidence per seat

| Seat | Projection (depth 0) | Measured kin | Depth effect | Source |
|---|---|---|---|---|
| gpt-oss-120b, **F16 trunk** (Day 1) | engines.json says 57–60, but that projection was computed for the ggml-org **Q8_0-trunk** file (3.594 GB/token, 57.3–60.4). The F16 trunk reads about 4.99 GB/token, giving **≈ 41–44 tok/s** (INFERRED, §3a) | MXFP4 / Q8_0-trunk file on 395 Vulkan: 53.8–56.61 | 53.7 → 49.1 at 8k → 40.6 at 32k (−9% at 8k) | single_engine_performance.md:20-21, 70, 108; strix_halo_capacity_performance.md:93, 124 (VERIFIED) |
| GLM-5.3-Flash UD-IQ4_XS | 12.5–13.8 (13.1 GB/token × 60–66% of 273) | PRO 495 Vulkan, 3.84 bpw Gorgon, `-fa 1`: 17.4 at depth 0, 15.0 at 32k; "plain llama.cpp master runs the file at the same decode speed" | −14% at 32k; UD-IQ1_S 17.63 → 15.46 at 4k; "about 6 by 128k" UNVERIFIED | cafonez card (fetched 2026-10-17); single_engine_performance.md:29-30, 96; strix_halo…:95 |
| DeepSeek-V4-Flash-0731 UD-Q8_K_XL | 6.8–7.2 (17.085 GB/token × 42.6–44.9% mainline); 10.6 at fork efficiency | Mainline Vulkan IQ2_XXS 12.6 → 12.2 at 8k → 11.1 at 32k; IQ3_XXS b10488 11.94 | −3% at 8k, −12% at 32k | single_engine_performance.md:33-35, 51, 88, 92; local-llm-benchmarks results.json (fetched 2026-10-17) |
| Router Qwen3.5-4B Q8_0 | 45–50 (INFERRED; answers.md:280); 42.7–48.8 = 273 × 70–80% ÷ 4.48 GB (INFERRED) | Qwen3-8B Q8_0 25.65 on Strix Halo | — | answers.md:280 |
| DeepSeek V4.1 Flash (trial) | — | PRO 495, DwarfStar ROCm 10.0, antirez Q2, **temperature 0**: 14.8 / 14.7 / 14.5 at 0 / 8k / 32k; 14.4 / 14.5 / 14.3 with SWA replay; DSpark 16.3 / 18.4 / 16.9 | flat to 64k (Framework 14.0 / 13.8 / 13.8) | local-llm-benchmarks results.json, gorgon-halo rows 2026-09-30 (VERIFIED, fetched 2026-10-17); lucebox_applicability_and_verification.md:22 |

### 3a. gpt-oss-120b F16-trunk bytes per token (INFERRED, from VERIFIED inputs)
- The ggml-org file keeps attention, router, embeddings and output at Q8_0. The F16 file keeps them at 16 bits
  (verification_named_120b_class_and_uncensored.md:26, GGUF headers).
- Q8_0-trunk file: 3.594 GB/token, of which 1.69 GB is non-routed (single_engine_performance.md:70). Routed per token
  = 1.904 GB.
- F16 trunk: attention 36 × (2880 × 5120 + 4096 × 2880) = 0.955 B parameters × 2 B = 1.91 GB, plus the output head
  201,088 × 2,880 = 0.579 B × 2 B = 1.16 GB, so 3.07 GB non-routed. Check: 61.07 GB experts + 1.91 + 2 × 1.16 =
  65.30 GB against the 65.37 GB file (engines.json files[]).
- Active ≈ 1.904 + 3.07 = **4.97–4.99 GB/token**. At the measured 75.5–79.5% of 273 GB/s that is **41.3–43.7 tok/s**.
  At 8k (−9%) it is ≈ 37.6–39.8.
- Consequence: engines.json [57, 60] and doc §2 L103 / §5.1 "57–60 projected for the Day 1 file" describe the other
  file. D-59 (b) bought the F16 trunk for precision, as "the higher-precision choice, not a measured gain". This is its
  speed cost (≈ −25%). That is acceptable under quality first, but the projection should say so before a review line
  is computed from it.

## 4. What a floor can and cannot catch on this chip

Failure magnitudes (INFERRED, from VERIFIED kin):
- **An op on the CPU that copies weights** (Mistral-Small-4 `ffn_down_exps` on stock Vulkan): 0.82 against 35.3 tok/s,
  a 40× slowdown (strix_halo_capacity_performance.md:97, 181). Any floor catches this.
- **An activation-only op on the CPU** (graph splits; T-04's open question for GLM's hyper-connection or
  GATED_DELTA_NET ops): roughly 1.2–2× slower, from 0.1–0.3 ms per split × 90–180 splits per token against GLM's
  ~77 ms per token (INFERRED, unmeasured). A floor at half the projection catches only the worst case. The graph-splits
  line and the review line catch the rest.
- **The whole model on the CPU** (no Vulkan device, or a wrong `-ngl`): CPU decode is bandwidth-bound at about
  108 GB/s effective (Llama-2-7B Q4_0 on the CPU 28.65 tok/s × 3.76 GB; strix_halo…:115, issue #13565). Ceilings:
  gpt-oss F16 21.7 tok/s (≈ 52% of 41.5), GLM 8.2 (66% of 12.5), **DeepSeek 6.3 (93% of 6.8)**, router 24 (≈ 53%).
  **No sane speed floor separates a CPU-resident DeepSeek from a healthy mainline-Vulkan one.** Placement must be
  checked directly (§6 change 3).
- **A driver regression, thermal or power limits, a wrong `-ub`, or flash attention off at depth**: 10–50%. A fixed
  floor catches only the large ones. The 70% review line and the Day 1 baseline catch the rest (§6).
- **A wrong KV type**: no reliable speed signature (q8_0 against f16 changes decode by a few percent). V4's proof line
  is the check (doc L1076), not the floor.

Hence the split. **Placement checks catch misconfiguration. The absolute floor catches catastrophic faults and enforces
usability. The review line catches drift and asks the Principal.** None of them picks a model on speed.

## 5. The floors, with human time

Rule used (INFERRED): floor ≈ half of the pessimistic healthy figure at 8k, rounded, then checked against the seat's
usability minimum (its job's time budget). Where the two differ, the higher wins, but only if it stays below the
pessimistic healthy figure with margin.

| Seat | Pessimistic healthy at 8k | Usability anchor | **Floor at 8k (fails V10)** | Review line: 70% of projection low, at 512 (to-do, no fail) | **Prefill floor at 8k (fails V10)** |
|---|---|---|---|---|---|
| gpt-oss-120b (F16 trunk) | ≈ 37.6 (§3a) | fast utility and the fallback of every seat; must stay well ahead of GLM | **25** (66% of 37.6; above its 21.7 CPU ceiling) | 29 (on a corrected projection of 41–44) | **200** (kin 747.7 incremental at depth 8192, strix_halo…:93) |
| GLM-5.3-Flash | ≈ 11.5 (12.5 × 0.92) | default seat for about 7 personas and the Deep Think generator; a standard tier at the floor stays under ~40 min worst case | **6** | 8.75 | **40** (long documents: §5b) |
| DeepSeek-V4-Flash-0731 (Apex) | ≈ 6.2–6.5 (6.8 × 0.95) | a 3,072-token deep synthesis must fit the node's 900 s one-generation budget: 3,072 ÷ (900 − 120 s prefill) = **3.94 tok/s** | **4** (= the deep-tier floor) | 4.8 | **40** (a 6k scoring prompt in 2:23 at the floor) |
| Router Qwen3.5-4B | ≈ 40 | runs before every request: an 80-token verdict (200 max) in ≤ 4 s (≤ 10 s) | **20** (V10a, ≥ 64 generated tokens) | 31 | none (V10a has no prefill point; record only) |

Margins against the pessimistic healthy figure at 8k: gpt-oss 34%, GLM 48%, DeepSeek 36–38%, router 50% (INFERRED).
Only gpt-oss's fail blocks the Phase 3 gate, so its margin rests on its being the best-measured engine (8+ stock-Vulkan
runs at 51.7–56.6 tok/s for the sister file).

### 5a. Generated-token times (decode only; add prefill and, for each swap, 50–85 s) (INFERRED, d47_calc.py)

| tok/s | 500 tok | 1,500 tok | 2,000 tok | 2,500 tok | 3,072 tok (deep-tier max) |
|---|---|---|---|---|---|
| 4 (DeepSeek floor / deep-tier floor) | 2:05 | 6:15 | 8:20 | 10:25 | 12:48 |
| 5 | 1:40 | 5:00 | 6:40 | 8:20 | 10:14 |
| 6 (GLM floor) | 1:23 | 4:10 | 5:33 | 6:57 | 8:32 |
| 7 (DeepSeek projection) | 1:11 | 3:34 | 4:46 | 5:57 | 7:19 |
| 11 (DeepSeek UD-Q4_K_XL projection) | 0:45 | 2:16 | 3:02 | 3:47 | 4:39 |
| 12.5 (GLM projection low) | 0:40 | 2:00 | 2:40 | 3:20 | 4:06 |
| 14 (V4.1, DwarfStar, greedy) | 0:36 | 1:47 | 2:23 | 2:59 | 3:39 |
| 16 (V4.1 with DSpark) | 0:31 | 1:34 | 2:05 | 2:36 | 3:12 |
| 20 (router floor) | 0:25 | 1:15 | 1:40 | 2:05 | 2:34 |
| 25 (gpt-oss floor) | 0:20 | 1:00 | 1:20 | 1:40 | 2:03 |
| 42 (gpt-oss F16-trunk projection) | 0:12 | 0:36 | 0:48 | 1:00 | 1:13 |

Whole runs at the max_tokens of deep_think.py, with 70 s per swap and a few minutes of prefill (INFERRED):
- Deep tier at the projections (GLM 13 / DeepSeek 7): **63 min** if every call uses its full budget, **36 min** at half.
  At the floors (6 / 4): 116 / 63 min. With V4.1 as the Apex (13 / 14.5): 52 / 31 min.
- Standard tier at the projections: 22 / 13 min. At the floors: 40 / 22 min.
- So the doc's "15 to 30 min" for the deep tier (9.1 table) is already optimistic at the projections if answers run
  near max_tokens. That is a separate to-do for T-14 / the deep-tier timings, not part of this item.

### 5b. Prefill and the long-document route (INFERRED)
Depth model r(d) = r0 / (1 + d/D), D ≈ 19,000. It fits the Gorgon GLM card (256.4 → 95.9 at 32k, D = 19,579) and
DeepSeek on mainline (224.5 → 154.3 at 8k → 82.2 at 32k, D = 18,006 / 18,929). The loadtest's 8k point averages from
0 to 8k, so r0 = 1.216 × r8. Time to read N tokens = (N + N²/2D) / r0.
- r8 = 40 (the floor): a 24k document (the long-document trigger) takes 13:25, 32k takes 20:55, 64k takes 61 min.
- r8 = 100: 5:22 / 8:22 / 24:29. r8 = 150: 3:35 / 5:35 / 16:19.
- Below 40, a 24k document needs over a quarter of an hour before GLM writes a word, and an ordinary 4–8k persona
  prompt costs 1–3 min before the first token of every new conversation. That is unusable for the default resident
  seat. Healthy GLM on master is "lower" than the card's 256.4 at 2k (card), but kin runs of 96–131 at depth
  (strix_halo…:95; DwarfStar 100 at 8k) put the floor at about 1/3 of them.
- **Yes, prefill should have a floor**, and GLM's matters most. Prefill is compute-bound, so it is also where a lost
  GPU path shows first on the seats whose decode is close to the CPU's (VERIFIED for kin: CPU pp 304 against Vulkan
  881 on 7B; strix_halo…:115).

## 6. Which number fails the gate, and why

- **The absolute floor fails V10** (for GLM and the Apex, a V22 deferral). It is the Principal's own usability
  statement in tok/s at 8k and does not depend on any projection being right.
- **The fraction of projection never fails.** Below 70% of the projection's low end, the V10 line says so and the
  to-do asks the Principal. The projections are other machines' measurements scaled by arithmetic (doc L111). One of
  them, gpt-oss's, is computed for a different file (§3a). Failing on a fraction would recreate the v0.3.5 band rule
  that D-33 (c) withdrew.
- **After Day 1**, record each seat's measured 512 and 8k figures in engines.json as its node baseline. Later pins and
  driver updates (V25-style re-pin runs) warn below 0.8 × baseline, as a finding about the pin, and never fail. The
  absolute floors stay as they are.

## 7. The deep-tier floor and the V4.1 trial

- **Deep-tier floor: 4 tok/s decode at 8k**, measured by the same protocol (8k prompt, at least 128 generated tokens).
  It is applied to the **slowest of the node's production settings** for the Apex roles: 0.15 and 0.6, plus 1.0 as
  the server default, each with DSpark off and on.
- **Sampling the trial measures**:
  - (a) **Greedy (T = 0)**, so the trial compares with Framework, local-llm-benchmarks and Lucebox, which are all
    temperature 0 (local-llm-benchmarks `sampling.temperature 0.0`; lucebox_applicability…:39).
  - (b) **T = 0.15** (Arthur and adversary scoring), **T = 0.6** (deep synthesis) and **T = 1.0 / top-p 1.0** (vendor
    default, D-30), each with DSpark off and on, at 8k and 32k.
  - Without a drafter, temperature should not move decode by more than a few percent: the sampling cost is about
    1 ms against 60–70 ms per token (INFERRED). Its effect is through DSpark acceptance, which falls as temperature
    rises, so greedy DSpark figures overstate the node's.
  - The **quality** scoring of T-30 runs at the production settings (0.15 scoring, 0.6 synthesis), never greedy,
    because that is what the Principal will get.
- **What the V4.1 result would mean**: about 14–16 tok/s (UNVERIFIED on this node; 14.3–14.8 without DSpark and
  16.3–18.4 with it, measured on a PRO 495 at T = 0). That clears the floor by 3.5–4× and 0731's projected 7 by 2×.
  The floor binds neither engine, so the trial is decided **only by the blind scores**. If V4.1 scores lower, its speed
  cannot save it. If it scores higher, the 2.5-bit quant (KL 0.26) has earned its place on quality. Speed is a
  tie-break only if the scores are level within the test's noise, and even then D-46's costs (a ROCm container, build
  allowlist hosts) belong to D-46, not here.
- If 0731 UD-Q8_K_XL measures **below 4** on Day 1 (T-06), the remedy is UD-Q4_K_XL from the existing relief order
  (D-11 (b): about 11 projected, KLD 0.0102, 96.28% same top token; lucebox_applicability…:181). V4.1 is not the
  remedy: its speed counts for nothing until it wins T-30.

## 8. Checked since 2026-10-15 (web, 2026-10-17)
- llama.cpp releases run to **b11706** (2026-10-17). The release listing names no deepseek4, dsv4, V4.1 or glm5 change
  (https://github.com/ggml-org/llama.cpp/releases, fetched 2026-10-17; listing page only, VERIFIED as far as that
  page goes). The pin stays b11474. Nothing found changes the projections.
- **PR #28696** (DeepSeek V4.1, `DeepseekV41ForCausalLM`) is **still open**: 2 approvals are required and none is
  given; the three requested reviewers are pending. Its context is capped at about 16K, it mentions only CUDA and Metal,
  its latest comment is from 2026-10-10 and fork commits reference it on 2026-10-13
  (https://github.com/ggml-org/llama.cpp/pull/28696, VERIFIED). The trial path is still DwarfStar or ROCm.
- local-llm-benchmarks results.json (https://local-llm-benchmarks.dev/data/results.json, fetched 2026-10-17): newest
  job 2026-10-09. There is **no llama.cpp Vulkan run of 0731 UD-Q8_K_XL or GLM UD-IQ4_XS** anywhere. The only PRO 495
  rows are GLM (DwarfStar), V4.1 (DwarfStar) and MiMo (VERIFIED).
- cafonez GLM Gorgon card: unchanged at 17.4 / 15.0 decode with `-fa 1`, and master runs the same decode
  (https://huggingface.co/cafonez/GLM-5.3-Flash-Gorgon-GGUF/raw/main/README.md, VERIFIED).
- A search for a GLM UD-IQ4_XS or DeepSeek UD-Q8_K_XL measurement on Strix Halo or a PRO 495 found none
  (unsloth.ai/docs GLM-5.3-Flash, search excerpt).

## 9. Build changes if approved
1. engines.json: set `decode_floor_tok_s` to 25 / 6 / 4 (gpt-oss / GLM / DeepSeek). Add `prefill_floor_tok_s`
   200 / 40 / 40. Correct gpt-oss `projected_decode_tok_s` to [41, 44] with the §3a source. For the router, add
   `projected_decode_tok_s` [45, 50] (INFERRED) and `decode_floor_tok_s` 20. Rewrite `_meta.decode_rule` (L14).
2. loadtest.py:
   - Run `check_band` after the 8k point, on min(decode 512, decode 8k), or on the 512 figure when the 8k point is
     skipped.
   - Add the prefill floor on `prefill_tps_8k`.
   - Give a floor verdict only when predicted_n ≥ 64, otherwise re-measure once.
   - Add the 70% review line to the V10 message and to the decode-floors to-do (or a new `decode-review` to-do).
   - Change the to-do text from "tok/s at 512 tokens" to "at 8k (the slower of the two points)".
3. loadtest.py placement checks in V10, as the CPU-fallback detector (§4):
   - Fail when `offloaded N/M layers` has N < M, or when the GTT delta is below 0.85 × the file bytes (the existing
     `RESIDENCY_LOW_FACTOR`).
   - Record the `model buffer size` lines per buffer type and the `graph … splits` line (a record, not a verdict).
4. verify/v10a-router-resident.sh: add a `/completion` timing request (128 tokens, temperature 0, as loadtest does) and
   fail V10a below 20 tok/s. Today's 16-token chat figure is noise.
5. Doc: Section 1 L70, §2 L103/L111, §5.1 L352, §9.1 L663, §17 L1076/L1080, §21 V10 L1266 (floors set, which number
   fails, the review line, the gpt-oss projection), and §9.1 for the deep-tier floor and the trial's sampling list.
6. The T-30 trial harness (phased) measures with the §7 protocol and applies the 4 tok/s floor to the slowest
   production setting.

## 10. What would change my mind
- A Day 1 figure for a healthy seat within ~20% of its floor (for example GLM at 7 at 8k with clean placement lines).
  The floor would then be too close to a working engine, and I would lower it, not fail the seat.
- The Principal saying a deep run of over an hour is acceptable. The DeepSeek floor could then drop to 3, but only
  together with a larger `DEFAULT_TIMEOUT_SLACK_S`.
- The Principal wanting speed to choose DeepSeek's rung (Q8 against Q4) in advance. That needs a floor near 6, which I
  advise against: it makes an unmeasured projection pick the file.
- Evidence that CPU-side decode on this APU is far below 108 GB/s for MXFP4 or IQ4_XS kernels. The floors would then
  also catch whole-CPU fallback, and the placement checks would matter less, though they would still be cheaper and
  surer.
- A merged DSV4 Vulkan speed-up (fork-level efficiency puts Q8 at ~10.6) makes the floors less binding but does not
  change them.
