# ATLAS open inputs: recommendations, 2026-10-17

Research record for the Principal's question of 2026-10-17: recommendations for the inputs the approved
action register left open (D-47 with D-33 and T-06, D-16, D-19, D-60, C-12, D-49, D-46) and the B-27 download plan
(the Principal reported a 2,000 Mbps line, reached through the node's 10 GbE port or Wi-Fi 7, and router ports of
2.5 GbE). Research only: nothing in the baseline or the scripts changes until the Principal answers.

Method: one research agent per item (two items shared one agent: D-60 with C-12, and D-49 with D-46). Each pair was
then checked by an adversarial verifier who re-read every cited file and page and re-ran the arithmetic. Claims carry
VERIFIED / INFERRED / UNVERIFIED markers.

| File | Contents |
|---|---|
| `recommendations.json` | The verified recommendations, one item per register id, with reasons, evidence, risks, build changes and what would change each one |
| `notes_D-47.md`, `notes_B-27.md`, `notes_D-16.md`, `notes_D-19.md`, `notes_COMP.md` (D-60, C-12), `notes_SETS.md` (D-49, D-46) | Researchers' working notes |
| `verifier_D-47_B-27.md`, `verify_D-16_D-19.md`, `notes_verify_COMP_SETS.md` | Verifiers' notes and corrections |
| `scripts/` | The calculation and GGUF-header scripts the figures came from (paths inside them point at the session scratchpad) |

Summary of the recommendations (details and sources in `recommendations.json`):

- D-47: deep-tier floor 4 tok/s decode at 8k context; the V4.1 trial measures greedy and the node's own temperatures.
- D-33: per-seat decode floors at 8k: gpt-oss-120b 25, GLM-5.3-Flash 6, DeepSeek-V4-Flash-0731 4, router 20 tok/s;
  prefill floors 200 / 40 / 40; a 70%-of-projection review line that raises a to-do and never fails.
- B-27: record 2,000 Mbps; plan 400 Mbps for the pull as written (one stream per file) until a 60-second
  single-stream test on the line measures it; connect by cable (the non-DASH 10 GbE port to a 2.5 GbE router port),
  decided before Phase 1; parallel shard downloads before the phased pull.
- D-16: both seats at Q5_K_S with a q8_0 KV cache and one 32,768-token slot; Baichuan-M3 on the imatrix i1-Q5_K_S file.
- D-19: option (c), no dedicated coder seat; GLM-5.3-Flash codes, settled against DeepSeek-V4-Flash-0731 on the
  Principal's own coding tasks in T-03.
- D-60: option (b), drop EmbeddingGemma 2.
- C-12: Rad-DINO stays; MedGemma 1.5 4B joins as an on-demand imaging tool only if the Principal accepts the HAI-DEF
  terms and a node test shows it beats GLM-5.3-Flash's own vision.
- D-49: 100 private tasks in two waves of 50, drafted by ATLAS and approved by the Principal, scored blind.
- D-46: 40 private trial tasks with a promotion rule fixed before unblinding; PR #28696 still open on 2026-10-17.
