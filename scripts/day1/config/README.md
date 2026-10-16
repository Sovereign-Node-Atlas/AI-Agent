# config/ — schema notes for the presets, personas, voice casting and router rules

These notes exist so the orchestrator writer and the `config/engines.json` writer use the same spellings and
honour the same rules. Section numbers refer to `docs/ATLAS_FRAMEWORK_REVIEW.md`; engine and persona keys are
the canonical set in `scripts/day1/CONVENTIONS.md` §8.

## task-forces.json (Section 8.3, 8.4, 8.5)

- `domain_cards` never names a Tier C card (8.2: 11, 13, 26, 28, 30, 33, 34, 35, 36). A preset's default card
  list is a speculative load and 8.4 rule 4 forbids loading Tier C speculatively. A Tier C card reaches a
  director only on an explicit match (a request that names the field, or the director's retrieval tool, 8.4
  rule 5); several `why` strings name the Tier C card that was deliberately left out.
- `triggers` are lowercase keywords for the detection layer (7.2 rule 3). Short generic tokens were replaced by
  phrases; the remaining three-letter entries are distinctive acronyms (m&a, whs, sla, aec, bim, tpm, hsm, vpn,
  seo, jet). Match on whole tokens or phrases, not raw substrings. No trigger contains a 7.2 rule-1 hard keyword,
  because the hard rule routes to Arthur before task-force detection runs. Eleanor's classifier catches what the
  keywords miss (7.2 rule 2).
- Extra keys beyond the brief: `hemisphere` (the 8.1 hemisphere tag on every task force) and, on TF_OMEGA only,
  `engine` (`deepseek-v4-flash`, the Apex engine, 5.1) and `trigger: "principal-only"`. `notes` (v0.4.0, S-29,
  D-37) names the phased engine a preset will carry once it is pulled (Ling-3.0-flash-Fin on TF_GAMMA, TF_DELTA and
  TF_EPSILON, Qwen3-Coder-Next on TF_MU, Baichuan-M3 and Intern-S1 on TF_UPSILON); an `engine` field is set only
  for a Day 1 engine, and it must be in the dispatching director's binding. Unknown keys must be ignored by the
  loader; these are informational.

## personas/<name>.md

- Front matter is YAML; `#` lines are comments and record provenance. `speaks_externally_tier` on `ren.md` and
  `arthur.md` is an inference (6.2 gives tiers for directors only), marked as such in the file, to be confirmed
  with the Principal.
- `default_engine` / `override_engines` / `fallback_engine` use the CONVENTIONS §8 keys of the v0.4.0 Day 1 set
  (`glm-5.3-flash`, `deepseek-v4-flash`, `gpt-oss-120b`, `gpt-oss-120b-abliterated`, `router-qwen3.5-4b`), spelled
  exactly as in `config/engines.json` (5.1, 5.3); the loader refuses any other key. `fallback_engine` (D-40 option
  (a)) records the engine that carries the seat if its default fails or defers in Phase 3: the arsenal's runner-up
  where that is a Day 1 engine, else `gpt-oss-120b`, the floor (the planned runner-up is named in a comment and in
  engines.json `phased`). It is never picked at runtime. Eleanor's `resident_model` is the router her
  classification runs on; her persona work uses `default_engine` (P-10).
- Body length is 250-450 words excluding front matter.

## voice-casting.json (Section 14.3)

- Extra keys beyond the brief: `key` (the CONVENTIONS §8 persona key, so the orchestrator can join this file to
  `personas/<key>.md` and `reference_recordings`) and `kokoro_note` (only on Gideon, whose 14.3 row is conditional).
- No two personas share a `kokoro_primary`. Gideon's is `null` pending the V7 listening test: 14.3 gives him
  bm_george only if Arthur takes bm_daniel, and otherwise "am_onyx reassigned", but Arthur holds bm_george and
  Ren holds am_onyx. His alternate is bm_george and his separation path is the Chatterbox clone from
  `reference_recordings.gideon`.

## router-rules.json (Section 7.1, 7.2, 9.1)

- `overrides` maps a message prefix to an action. Matching rule: the message must start with the key, matching is
  case-sensitive, and the longest matching key wins, so `[DEEP THINK:DEEP] ...` resolves to `deep-think:deep`
  while `[DEEP THINK: problem]` resolves to `deep-think` (classifier-chosen depth, 9.1). Keys that end in `:`
  (`[DEEP THINK:`, `[LOG STRIKE:`) carry a payload up to the closing `]`; the other keys are complete tokens.
- Keys beyond the brief, all intended and all fixed in configuration per 7.2 rule 4:
  `[ARTHUR:UNCENSORED]` -> `arthur-abliterated` (6.1 C6 addendum); `routes.arthur-abliterated` ->
  `gpt-oss-120b-abliterated`; `routes.deep-think:deep` -> `deepseek-v4-flash` (9.1 deep tier on the Apex engine);
  `[ARTHUR:LONG]` -> `arthur-long` -> `glm-5.3-flash` (v0.4.0, S-30, D-38: v0.3.5's `arthur-qwen` name is refused);
  `hard_keyword_route` (`arthur`, 7.2 rule 1); `classifier_engine` (`router-qwen3.5-4b`, 5.3, 7.2 rule 2).
- `routes.ren` -> `glm-5.3-flash` and `routes.arthur` -> `deepseek-v4-flash` (v0.4.0, S-30). `long_document_tokens`
  stays 24000, the value set for a 32K slot, until Phase 3 has measured the per-engine slot profile (D-38, D-48):
  a to-do, not a decision.
- `hard_keywords` ends with `FAMILY_NAMES_PLACEHOLDER`; the orchestrator replaces it with the names listed in
  `/etc/atlas/atlas.env`. Every hard-keyword hit and every override is logged (7.2 rule 5).
