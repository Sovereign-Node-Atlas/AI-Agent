# config/tiktoken — LightRAG's tokenizer table (Phase 2 step 4)

`phase2/04-memory.sh` seeds tiktoken's `cl100k_base` table into `$ATLAS_SRV/data/graph/tiktoken` so LightRAG (D7,
Section 10.2) can index without ever contacting the network at run time (Section 12.5; `HF_HUB_OFFLINE`-style offline
operation for the graph layer). The step looks for the table in this order and uses the first copy whose sha256 matches
the pin below:

1. `scripts/day1/config/tiktoken/cl100k_base.tiktoken` (this directory, the vendored copy);
2. `/srv/atlas/staging/inbox/cl100k_base.tiktoken` (the Principal's drop directory, CONVENTIONS.md §2);
3. a one-time download through the allowlist proxy from the URL below (`openaipublic.blob.core.windows.net` is in
   `config/allowlist.txt` as an ADDITION to the Section 12.5 enumeration, pending the Principal: the allowlist comment
   says so and a Section 23 row recording it is requested of the document's editor; striking that line returns to the
   fix-round-3 position, offline delivery through paths 1 and 2 only, and the step then defers with the inbox remedy).

Whichever path succeeds, `LIGHTRAG_TIKTOKEN_CACHED=1` in `/etc/atlas/memory.env` is the normal outcome; `0` (the seed
deferred) happens only when every path failed, and the step then prints the exact remedy.

## The file

| Item | Value |
|---|---|
| URL | `https://openaipublic.blob.core.windows.net/encodings/cl100k_base.tiktoken` |
| sha256 | `223921b76ee99bde995b7ff738513eef100fb51d18c93597a113bcffe865b2a7` |
| Status | VERIFIED: the hash is the `expected_hash` tiktoken itself pins for `cl100k_base` in `tiktoken_ext/openai_public.py` (read 2026-10-04 from the repository's `main` branch through the review sandbox's proxy, and from the tiktoken 0.14.0 sdist in the earlier fix round); tiktoken re-checks it on every load |
| Size | ~1.7 MB, one public BPE rank table, no Principal data |

`SHA256SUMS` beside this file carries the same hash in `sha256sum -c` form.

## Vendoring the copy

The review sandbox that wrote this directory could not fetch the file (HTTP 403 from its own proxy), so the table is
NOT committed yet. To vendor it, on any machine with internet access:

```bash
curl -fsSL -o cl100k_base.tiktoken https://openaipublic.blob.core.windows.net/encodings/cl100k_base.tiktoken
sha256sum -c SHA256SUMS          # must print: cl100k_base.tiktoken: OK
```

and commit `cl100k_base.tiktoken` into this directory. Until then path 2 or 3 above delivers it on the node; the step
verifies the sha256 whichever path the file came by and ignores (loudly) a copy that does not match.
