# ATLAS v0.4.0 build record

Working notes written on 2026-10-16 while the v0.4.0 revision was built (S-40: the sources the scripts and the
document cite must be in the repository). They record what was read, not what was decided: where they and
`docs/ATLAS_FRAMEWORK_REVIEW.md` or the scripts disagree, the document and the scripts are the current state.

| File | What it holds | Cited as |
|---|---|---|
| `engine_data.md` | The v0.4.0 engine data: every Day 1 and phased file's bytes and sha256 from the Hugging Face tree API, the GGUF header facts (architecture, context, KV bytes per token), sampling, licences, first-support llama.cpp tags and the residency arithmetic, collected 2026-10-16 | "build record engine_data.md", with its section heading |
| `engine_data.json` | The same data in machine-readable form (the input the `engines.json` entries were written from) | "build record engine_data.json" |
| `pin_facts.md` | The llama.cpp facts the Day 1 scripts rely on, re-read at build tag b11474 against v0.4.1, with the script locations and the change each required | "build record pin_facts.md" |
| `answers.md` | The analysis behind the Principal's questions of 2026-10-16: RAM headroom (N-01, N-03; section 1), where and at which size the retrieval pair runs (D-14; section 2) and the router's size (P-10; section 3) | "build record answers.md §N" |

Notes on reading them:

- Paths beginning `<build session scratchpad>/` were the build session's own working copies (a llama.cpp clone at both
  pins, the register as JSON, the returned responses); they are not in the repository. The register and the returned
  workbook are `docs/ATLAS_ACTION_REGISTER.xlsx`; llama.cpp is public at the two commits named.
- Line numbers of repository files (`arbiter.py:541`, `docs/ATLAS_FRAMEWORK_REVIEW.md:231` and so on) are those of
  commit 1b93482, the v0.3.5 baseline the notes were written against.
- The notes were written before the code changed, so some proposals were refined in the build. Examples: V25 does
  not compare the Qwen3 embedder and reranker (they have no next-token logits; `loadtest.py` `parity_coverage`); the
  CPU pair is charged its weights in full plus its measured anonymous memory, never below its projection
  (`atlas/arbiter.py` `measure_host_companions`); the pair runs at a 4,096-token context (doc 5.3).
- Labels follow the document: VERIFIED (read in code or a primary page), INFERRED (derived, not measured),
  UNVERIFIED (not checked).
