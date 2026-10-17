# Notes: D-49 (seat test sets, T-27) and D-46 (V4.1 trial task set, T-30)

Written 2026-10-17. Research only; no repository file edited. Labels: VERIFIED (read in a primary source, file:line or
URL), INFERRED (my arithmetic or judgement, shown), UNVERIFIED.
Scope: seats covered are GLM-5.3-Flash, DeepSeek-V4-Flash-0731, gpt-oss-120b and the router only.
Register file: `actions/register_final.json` (scratchpad): D-46 at L544, D-49 at L580, T-27 L2890, T-30 L2914, T-32 L2930.

## 1. What the repository already fixes (VERIFIED)

- Neither set has a defined format yet. `phase3/loadtest.py` only raises the to-dos: `TRIAL_TASK_SET_TODO_ID = "trial-task-set"` (L324), `SEAT_TEST_SETS_TODO_ID = "seat-test-sets"` (L325), texts at L2364-2373 ("Default built: no trial ... DeepSeek-V4-Flash-0731 stays the Apex"; "Default built: GLM-5.3-Flash on those seats, unproven"). No runner, scorer or schema reads either set. Only the Principal closes them (L322).
- The only task format the scripts run is the HellaSwag-style parity file (6 lines per task, log-likelihood scoring, "no sampling and no chat template is involved", L327-338). Wrong tool for these sets: it does not exercise generation, the chat template or reasoning, which is how the seats are used. So the format is free to choose.
- Deep Think already scores with a rubric of five named criteria, equal weights: correctness, completeness, risk, actionability, clarity (`orchestrator/src/atlas/deep_think.py` L77-83). Roles run at fixed temperatures: generator 0.8 (L72), adversary 0.15 (L73), deep-tier final synthesis 0.6 (L466). DeepSeek's server default is `--reasoning on --reasoning-effort high --temp 1.0 --top-p 1.0` (`config/engines.json` L157).
- Projected decode rates: gpt-oss-120b 57-60 (engines.json L51), GLM 12.5-14.0 (L119), DeepSeek-V4-Flash-0731 7 (L159) tok/s.
- The domain cards already list the Australian compliance sources for each domain: `config/domains/02` L8 (Spam Act, DNCR, ACL, Privacy Act), `03` L8, `06` L8 (Corporations Act, Fair Work Act, WHS Act, SOPA, DBP Act 2020 NSW, Owners Corporations Act 2006 Vic, SSMA 2015 NSW), `08` L8 (NCC, DDA, VPP/ResCode, AS 1170, AS 1428, NatHERS), `09` L7-9 (Purdue, IEC 62443, SOCI Act 2018, AEMO/NEM), `11` L8 (Classification Board, 2024 loot-box rules, ACL), `16` L9 (Fair Work, WHS, HVNL and CoR), `18` L9 (ATO residency, FIRB, ABF ATA Carnet, CASA, AMSA), `19` L8 (CASA, ACL, short-stay rules), `22` L8 (Privacy Act incl. statutory tort, Surveillance Devices Acts, TIA Act, SOCI). These are the drafting source list.
- Doc: 6.2 D-49 paragraph L457; 8.2 domains table L512-551; TF_OMEGA "Principal-triggered, Ren and Arthur jointly | Sensitive, dual sign-off" L603; 9.1 depth table L653-655; 15.5 trial row L949 and runtimes row L950; Section 22 open boxes L1313-1314.
- Doc Section 1: "No model inference runs anywhere but this node" (L54), "No Principal data leaves the node" (L55), Completion standard: "The only things asked of the Principal are decisions and approvals" (L64). This argues for ATLAS drafting and the Principal approving.
- Leak paths specific to ATLAS: the six Chroma collections (L724) and the nightly 04:15 task "that summarises each expired chat into the Vector Cortex and the graph" (L738). A test run made through Open WebUI chats would therefore copy items and references into memory. Tests must run through an API runner that is tagged and excluded from memory.
- Arsenal CR G10 (`research_notes/ATLAS specialist LLM arsenal/completeness_review.md` L206-218): "No category exists for aviation, logistics, AEC, SCADA, physical security or HR."
- Lucebox report: "Whether V4.1's five-point lead survives 2-bit compression has not been measured by anyone" (L349). The AA lead is "mostly in agentic terminal work and automation" (L335). Recommendation 1: "Promote it only if it beats 0731 on the Principal's own tasks ... perhaps 30-50" (L392).

## 2. External checks since 2026-10-15

- **llama.cpp PR #28696** (https://github.com/ggml-org/llama.cpp/pull/28696), fetched 2026-10-17: still **Open**. "At least 2 approving reviews are required to merge"; pwilkin, CISC and ggerganov show as "Awaiting requested review". Tested on CPU and CUDA; Metal "genuinely unexercised"; Vulkan and HIP not mentioned. ~16K context cap, because the two-level candidate mask is not implemented. Last force-push Sep 22; latest comment Oct 10, a CUDA MMQ crash fixed on master by #29953 that needs a rebase. No successor PR in ggml-org. A PR search for "deepseek v4.1" (https://github.com/ggml-org/llama.cpp/pulls?q=is%3Apr+deepseek+v4.1) shows only #28696 open and #29008 merged (a V3.2/V4 chat parser fix). VERIFIED. Conclusion: no mainline route, so D-51 (a) stands.
- **No new DeepSeek model since V4.1.** HF API (https://huggingface.co/api/models?author=deepseek-ai&sort=createdAt&direction=-1) lists V4.1-Flash (2026-09-10) as the newest. VERIFIED.
- **V4.1 reference repo changed after 2026-10-15** (https://huggingface.co/api/models/deepseek-ai/DeepSeek-V4.1-Flash/commits/main). VERIFIED. None of these commits change the weights:
  - 2026-10-12 `8b3cec70`: "Restore each indexer's K cache on incomplete compression steps (#12)". In `inference/model.py`, `shared_attn.index_k = self.k_cache` now runs on every step, not only when a compression group completes.
  - 2026-10-12 `4dd75a8b`: `act_quant` is applied to the attention output before the `wo_a` einsum, and tilelang is bumped 0.1.8 to 0.1.12.
  - 2026-10-16 `314113f8`: an encoding fix that keeps current-turn reasoning across tool results and the order of tool images.
- **DwarfStar** (https://github.com/antirez/ds4/commits/main): the newest visible commit is 2026-10-07. Whether it mirrors the 10-12 reference fixes is UNVERIFIED. If it does not, the trial could score an implementation bug, not the model. Its 2026-09-19 commit "Add fresh official V4.1 Flash continuations and router quality comparisons" provides a parity check that can be run first.
- kyuz0 toolbox (https://github.com/kyuz0/strix-halo-ds4-toolbox): the `ds4.1f-rocm10.0` image "tracks antirez/ds4:main", which is a moving target, so the trial must pin an image digest (as D-52 and 15.5 already require). VERIFIED.
- No new board for the D-49 domains was found. The nearest precedent is the **Allens AI Australian law benchmark**: 30 questions across 10 practice areas. Each answer is marked out of 10: "a maximum of 5 marks for the substance", "a maximum of 3 marks for correct references to cases, laws or guidance", "a maximum of 2 marks for the clarity" (https://allens.com.au/insights-news/explore/2024/the-allens-ai-australian-law-benchmark/our-approach). Each question is asked three times single-shot. Allens monitors "the intrusion of international legal explanations and principles into Australian law answers" (https://www.allens.com.au/insights-news/explore/2025/the-allens-ai-australian-law-benchmark-2025/appendix-our-methodology-in-detail/). VERIFIED. Its tested models are hosted ones, none of the node's.
- Source access (VERIFIED):
  - AustLII robots.txt: `Content-Signal: search=yes,ai-train=no,use=reference` (https://www.austlii.edu.au/robots.txt). Its copyright page returned 403. A search snippet says it blocks automated access for AI uses (UNVERIFIED).
  - NSW Caselaw robots.txt disallows `/decision` (https://www.caselaw.nsw.gov.au/robots.txt).
  - legislation.gov.au allows crawling with Crawl-delay 10.
  - Rule that follows: draft by reading individual pages by hand and citing pinpoints. Never scrape case law.
- IEC 62443-3-3:2013 costs CHF 380 per part (https://webstore.iec.ch/en/publication/7033). It cannot be a drafting source unless the Principal holds a licence. NIST SP 800-82 Rev. 3 is free (Sept 2023), with a planning note of 2026-09-21 pointing to the initial public draft of Rev. 4 (https://csrc.nist.gov/pubs/sp/800/82/r3/final). VERIFIED.
- Game classification: paid loot boxes carry a minimum of M and simulated gambling R 18+, for decisions from 22 Sep 2024 (F2023L01424 explanatory statement). UNVERIFIED here: search summary only.
- Judge-bias literature (VERIFIED, abstracts):
  - Zheng et al. 2023 names "position, verbosity, and self-enhancement biases" and reports strong judges "achieving over 80% agreement, the same level of agreement between humans" (https://arxiv.org/abs/2306.05685).
  - Panickssery et al. 2024: "an LLM evaluator scores its own outputs higher than others'", linearly with self-recognition (https://arxiv.org/abs/2404.13076).
  - HealthBench uses conversation-specific rubrics by 262 physicians, 48,562 criteria (https://arxiv.org/abs/2505.08775). This is the precedent for per-item rubrics.
  - Quantised reasoning: "lower bit-widths introduce significant accuracy risks", and task difficulty is a determinant (https://arxiv.org/abs/2504.04823).
- Canary strings: the BIG-bench task files "contain a 'canary' string ... to prevent benchmark tasks from leaking into web-scraped training data" (https://raw.githubusercontent.com/google/BIG-bench/main/README.md L144, L236). VERIFIED.

## 3. One format for both sets (recommendation)

A JSONL file with one task per line, hashed when frozen. Fields:
`id, set (seat-test-sets|trial-task-set), version, canary, domain, seat, task_force, cards[], category, source_kind (public|private), drafted_by, checked_by, checked_on, as_at, prompt, context[] (title, text, source_url, compilation/version), reference {answer, must_include[{point, cite}], fatal[]}, objective {type exact|numeric|flaws|none, answer, tolerance}, scoring (5-3-2+fatal | pairwise+deep-think-rubric), holdout (bool)`.

- Scale for D-49: Allens-style 10 points (substance 0-5, sources/grounding 0-3, clarity and actionability 0-2), plus a binary FATAL flag that sets the item to 0. Fatal means:
  - an invented authority or citation;
  - foreign law applied as Australian;
  - unsafe or unlawful advice;
  - a wrong figure that changes the decision.
  - "Usable" = score >= 7 and no fatal.
- Scale for D-46: a pairwise verdict (A, B or tie) guided by the five deep_think.py criteria, plus the fatal flag and the objective checks.
- The Principal's scoring surface is a blinded workbook like the action register they already return (Section 22 records the returned workbooks). No new UI is needed.

## 4. Blind scoring on the node (both sets)

1. **Freeze.** Record the set's sha256 in the ledger before any run. Keep the file outside every Chroma/LightRAG path; it is never ingested.
2. **Run.** An API runner (not Open WebUI) dispatches each task straight to the seat through the Arbiter:
   - It bypasses the router (routing accuracy is P-10's own set) and reads no memory except the task's own `context`.
   - Each run is tagged `eval` so the retention summaries, scars and ledger summaries skip it.
   - Each engine runs in one batch (one load per engine), with the production persona, domain cards and sampling.
3. **Blind.** Strip reasoning blocks and any self-identification, and normalise the markdown. Give random per-task labels from a seed. The key file is root-only or kept in the vault, with its sha256 in the ledger, so the labels are committed before scoring.
4. **Judges.**
   - D-49: each answer is graded against its reference by the two Day 1 engines that did not write it. GLM, DeepSeek and gpt-oss are three families, so no engine ever grades its own answer.
   - D-46: GLM-5.3-Flash and gpt-oss-120b judge. Both are neutral to the two DeepSeek arms. Each pair is judged in both orders (four verdicts per task); a split or order-inconsistent verdict counts as a tie and is escalated.
   - Objective items (numbers, planted flaws, exact facts) are checked against the key, and the numbers are computed by the code interpreter.
5. **Principal.** The Principal grades blind:
   - every escalation (judges >= 3 points apart or one fatal flag on D-49; splits on D-46);
   - plus a random calibration sample (10% of D-49 answers; 10 of the 40 D-46 tasks).
   - If agreement with the judges falls below 80% (same verdict, or within 1 point with the same fatal flag), the Principal grades that domain, or the whole trial, in full. The 80% figure is the human-human level in Zheng et al.
6. **Unblind** only after all scores are committed. The report gives a table per domain and per engine.

## 5. D-49 starter outline (100 tasks, two waves)

Task-type codes: APPLY (fresh fact pattern), GROUNDED (open book from a supplied extract), PRECISE (closed-book exact rule or number, including abstention items; numbers computed), PRODUCE (the deliverable itself), TRAP (false premise, wrong jurisdiction, or a request outside the posture). Each domain has at least one TRAP. At least 30% of items should come from the Principal's real matters, redacted and drafted on the node. 20% of each domain is held out from any prompt or card tuning.

| Wave | Domain (seat) | n | APPLY | GROUNDED | PRECISE | PRODUCE | TRAP | Draft from (public) |
|---|---|---|---|---|---|---|---|---|
| 1 | AU law (Gideon; domain 6; TF_ETA/THETA/KAPPA/IOTA/ZETA) | 16 | 6 | 3 | 3 | 2 | 2 | legislation.gov.au (Corporations Act 2001, CCA 2010 Sch 2 ACL, Privacy Act 1988), legislation.nsw.gov.au (SSMA 2015, DBP Act 2020, SOPA 1999), legislation.vic.gov.au (OC Act 2006, SOPA 2002); cases read by hand from court sites |
| 1 | HR (Gideon as CHRO; domain 6) | 10 | 4 | 2 | 2 | 1 | 1 | Fair Work Act 2009 and NES, modern awards (FWC), FWO guidance, model WHS Regs (psychosocial), SDA positive duty |
| 1 | Aviation (Victor; TF_PHI; domains 18, 19) | 12 | 5 | 2 | 3 | 1 | 1 | CASR 1998 Parts 61/91/119/135/101 and Part 91 MOS (legislation.gov.au), AIP (Airservices), CASA guidance; FAA/EU261 secondary |
| 1 | Physical security / OSINT (Alaric; TF_TAU; domain 22), defensive only | 12 | 5 | 2 | 2 | 2 | 1 | Privacy Act and APPs, statutory privacy tort, Surveillance Devices Act 2007 NSW / 1999 Vic, TIA Act 1979, Security Industry Act 1997 NSW / Private Security Act 2004 Vic |
| 2 | Logistics (Eleanor with Silas, domain 16; Victor, domain 18) | 10 | 3 | 1 | 4 | 1 | 1 | HVNL and CoR (NHVR), ADG Code (NTC), Customs Act / ABF (ATA Carnet); OR items with computed answers |
| 2 | AEC (domain 8; TF_LAMBDA) | 10 | 3 | 3 | 2 | 1 | 1 | NCC (ABCB, free), Premises Standards 2010, VPP/ResCode, NatHERS; AS 1170/1428 only if the Principal holds copies |
| 2 | SCADA (domain 9), defensive only | 8 | 3 | 2 | 1 | 1 | 1 | NIST SP 800-82r3, ASD/ACSC OT guidance, SOCI Act 2018 and CIRMP Rules 2023, Modbus/DNP3/OPC UA public specs; IEC 62443 only if licensed |
| 2 | Growth marketing (Helena; domain 2; TF_PI) | 10 | 3 | 0 | 3 | 3 | 1 | Spam Act 2003, DNCR Act 2006, ACL ss 18/29, ACCC guidance, AANA Code; computed CAC/LTV/attribution |
| 2 | Customer success (Helena; domain 3) | 6 | 2 | 0 | 1 | 2 | 1 | ACL consumer guarantees (ACCC), APPs 11-13; computed NRR/churn |
| 2 | Game design (Helena with Valerie; domain 11, tier C) | 6 | 1 | 0 | 2 | 2 | 1 | Computer Games Classification Guidelines 2023, ACL in-game purchases; computed drop-rate EV and economy figures |
| | **Total** | **100** | 35 | 15 | 23 | 16 | 11 | |

- How the counts were set (INFERRED): tier A, sensitive or safety domains get 12-16 items, tier B 8-10, tier C 6. Wave 1 = the seats T-27 settles in D-10 (Gideon for law and HR, Victor, Alaric) = 50 items.
- Statistics (INFERRED, Wilson 95%):
  - n=12 with 10/12 usable gives 0.55-0.95; n=16 with 13/16 (81%) gives about 0.57-0.93.
  - So a single domain only screens for large failures. The pooled 100 tasks carry the engine comparison.
- Pre-registered bars (INFERRED, adjustable by the Principal):
  - GLM keeps a seat if its usable rate is >= 80% with at most 1 fatal item in the domain.
  - The seat's default moves only if another Day 1 engine beats GLM by >= 3 usable items in that domain and the neutral judge agrees.
  - If no engine reaches 80%, the domain is marked "unproven-failed": its output goes through a mandatory verifier pass at a raised approval tier, and the domain goes on the watch-list for a specialist.
- Run time (INFERRED, ~3,000 output tokens per answer):
  - Generation: GLM 100×3000/13.25 = 6.3 h; DeepSeek 100×3000/7 = 11.9 h; gpt-oss 100×3000/58.5 = 1.4 h; 19.6 h in all.
  - Judging: 600 judge calls × 600 tokens = 7.8 h.
  - About 28 h of engine time, or about 3 overnight batches (1.5 per wave).
- Principal effort (INFERRED): about 12 h in all, spread across the waves:
  - a 15-minute brief per domain (2.5 h);
  - checking 100 drafts at about 3.5 min each (5.8 h);
  - calibration of 30 answers (2 h);
  - about 30 escalations (2 h).
- Who drafts:
  - Public-source items: drafted off-node by a research session (Claude, which is not a contestant), with pinpoint citations (section and compilation number), so checking is "does s X say this", not legal judgement.
  - Private items: drafted on the node from the Principal's briefs. Record `drafted_by` and report each engine's score on items it did not draft.

## 6. D-46 starter outline (40 tasks)

Why 40 (INFERRED, exact binomial, one-sided sign test, alpha 0.05, ties excluded):

| n | Wins needed | Power if true preference is 0.60 | 0.65 | 0.70 | 0.75 |
|---|---|---|---|---|---|
| 30 | >= 20 (p = 0.049) | 0.29 | 0.51 | 0.73 | 0.89 |
| 40 | >= 26 (p = 0.040) | 0.32 | 0.57 | 0.81 | 0.95 |
| 50 | >= 32 (p = 0.033) | 0.34 | 0.62 | 0.86 | 0.97 |

40 is the smallest size in the approved range with about 80% power for a 70:30 preference. No size in 30-50 can detect a 60:40 edge, and such an edge would not justify swapping a bit-exact engine for a 2-bit file in a ROCm container.

| Category | n | What it tests | Scoring |
|---|---|---|---|
| A. TF_OMEGA apex synthesis | 16 | A dossier of 3-5 fresh director memos with planted conflicts leads to the Apex decision brief. Covers legal+capital, estate+health, crisis (PR+legal), security incident, capital deployment | Pairwise by the judges, with the 5 criteria and the fatal flag |
| B. Deep Think adversary | 10 | Three frozen trajectories (generated once on GLM at 0.8, as Standard does) containing 1-3 planted flaws: a wrong figure, a missing legal constraint, a hidden worst case. The engine returns the rubric JSON at temperature 0.15 | Objective: flaws caught, the right winner picked, valid JSON |
| C. Deep-tier final synthesis | 6 | Refined winner + critique + third opinion leads to the final synthesis, at temperature 0.6 | Pairwise |
| D. Long dossier, about 32K tokens | 4 | Exact details across a long bundle plus reasoning over them (the 2-bit risk) | Exact facts against the key, then pairwise |
| E. Exact multi-step computation | 4 | Quantitative chains, with the answer computed by Silas's interpreter | Exact or numeric tolerance |

- Drafting:
  - A and C are private TF_OMEGA matters, Sensitive with dual sign-off, so they are drafted only on the node, on GLM, which is neutral to both arms. They start from 8-10 one-paragraph real situations the Principal writes.
  - B, D and E can be drafted from public material, sharing sources but never items with D-49.
  - Every item is new; none is taken from AA, HLE, GPQA, Terminal-Bench or any public set.
- Run settings:
  - Production sampling per role: A at the server default 1.0 / top-p 1.0 with reasoning effort high, B at 0.15, C at 0.6.
  - V4.1 runs with reasoning on at the equivalent effort. The HF chat template accepts `reasoning_effort` (commit 2cba9e4, 2026-10-01). DwarfStar's levels are UNVERIFIED.
  - The same output cap for both arms, with truncations recorded.
  - Quality is scored only under production sampling. Greedy is for T-31 speed only.
  - One sample per task; the 40 tasks are the replication.
- Run time (INFERRED, ~6,000 tokens each): 0731 40×6000/7 = 9.5 h; V4.1 40×6000/13.8 = 4.8 h; judges (160 calls) about 1 h. Two overnight runs.
- Pre-registered decision rule. Promote V4.1 only if all four hold:
  - (1) wins >= 26 of 40 decided pairs (or the table threshold for the number decided), with ties at or below 25%; above that, add 10 tasks rather than decide;
  - (2) no more fatal errors than 0731;
  - (3) no worse than 0731 on any objective category (B, D, E counts);
  - (4) meets the D-47 floor in T-31.
  - Otherwise 0731 stays.
  - "Promise" for the T-32 Lucebox stage = at least 55% preference with no fatal excess. T-32 reuses the same set, re-blinded.
- Principal effort (INFERRED): about 6.5 h:
  - 1.5 h writing the situations;
  - 2 h checking 40 drafts;
  - about 3 h blind-scoring 10 calibration tasks plus about 8 splits.
- Timing:
  - Keep T-30 after Day 1 as approved and do not wait for #28696 (still open).
  - Build the set now, so the trial never waits on it.
  - Run the 0731 arm as soon as Day 1 passes, since it needs nothing from the trial build. Run the V4.1 arm after T-29.
  - Before the V4.1 arm, pin a DwarfStar commit and image digest, run DwarfStar's official-continuation check, and confirm it covers the DeepSeek reference fixes of 2026-10-12 and the encoding of 2026-10-16 (or record that they do not apply).
