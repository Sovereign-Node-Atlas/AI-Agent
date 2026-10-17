# B-27 — Internet bandwidth figure for the Phase 3 download (working notes, 2026-10-17)

Principal's input (relayed request): "my internet speed connection is 2000Mbps, the speed will be limitd to the 10Gbps LAN port or the Wifi 7 Connection".

## Recommendation (one)

Record **2,000 Mbps** as the line figure, and set the planning figure **DOWNLOAD_MBPS = 400** (in place of 100).
400 Mbps is what the pull path as written (one curl stream, one file at a time, sha256 after each file) is likely to
reach. It is not what the line can carry. Connect the node **by cable** from the 10 GbE port (the one not marked DASH) to a router port of
**2.5 GbE or faster**. Decide this **before Phase 1**, because Phase 1 ties the firewall, DNS fence and service bindings
to that interface. Replace 400 with the first "Mbit/s effective" figure Phase 3 logs. No script change is needed for Day 1. For the
842 GB phased pull, parallel shard downloads (about 4 streams) are worth doing later (recommendation only).

## 1. The pull path (what actually runs)

- Phase 3 step 01 loops over the four Day 1 engines **one after another** — VERIFIED `scripts/day1/phase3-models.sh:682-690`
  (`for key in "${P3_KEYS[@]}"` → `pull_engine_files "$key"`).
- `pull_engine_files` downloads each wanted file **one after another** with `hf_download_public` — VERIFIED
  `scripts/day1/phase2/04-memory.sh:303`, call at `:378`; `hf_download_public` at `:293-300` (proxy_env, token-less).
- `hf_download` is **plain curl, one connection per file**: `curl -L -C - --fail -sS --retry 5 --retry-delay 10
  --connect-timeout 30 -w '%{http_code}' -o "$part" ... "$url"` — VERIFIED `scripts/day1/lib/common.sh:773`. No
  segmenting, no parallel files, no hf_xet, no huggingface_hub.
- Checksum: after each file, `sha256sum` of the whole `.part` file (serial, it blocks the next file) — VERIFIED
  `lib/common.sh:734` (`_sha256_of`), `:788`. On a re-run every complete file is re-hashed before it is skipped —
  VERIFIED `lib/common.sh:744-751`.
- `HF_HUB_ENABLE_HF_TRANSFER=0` is exported by proxy_env — VERIFIED `lib/common.sh:707`. **It has no effect on this
  path**, because only huggingface_hub reads it and Phase 3 uses curl. huggingface_hub ≥ 1.0 ignores it anyway: "The `HF_HUB_ENABLE_HF_TRANSFER`
  environment variable is therefore ignored. Use HF_XET_HIGH_PERFORMANCE instead" — VERIFIED
  https://huggingface.co/docs/huggingface_hub/main/en/concepts/migration
- hf_xet: not used by Phase 3 (curl). Phase 4 containers force `HF_HUB_DISABLE_XET=1` because hf_xet's proxy handling
  is UNVERIFIED — VERIFIED `scripts/day1/docker/rocm-base/Dockerfile:27-30, 94`.
- Redirect target: `/resolve/main/<file>` answers 302 to `https://us.aws.cdn.hf.co/xet-bridge-us/...` (CloudFront
  edge serving the Xet-reconstructed file) — VERIFIED by `curl -I` from this session, 2026-10-17. The allowlist
  carries `huggingface.co` and `.hf.co` — VERIFIED `scripts/day1/config/allowlist.txt:87-88`. HF says
  suffix allowlisting of `hf.co` and `huggingface.co` covers every storage/CDN host — VERIFIED
  https://huggingface.co/docs/hub/en/models-downloading ("Downloading behind a proxy or firewall").
- ETA printed by the script: `_p3_eta` uses `DOWNLOAD_MBPS` (default 100) — VERIFIED `phase3-models.sh:528-533, 679`;
  `DOWNLOAD_MBPS=100` "Planning figure ... Not a rate limit" — VERIFIED `config/atlas.env.example:84-85`. The script
  logs the measured rate per engine ("Mbit/s effective", which includes the hash time) — VERIFIED `phase3-models.sh:704`.

### Squid (127.0.0.1:3128)
- No SSL bump (CONNECT tunnel only), no cache, no delay pools — VERIFIED `config/squid.conf.tmpl:6, 26, 44` (no
  `delay_pools` line in the file).
- Tunnel relay buffer = `SQUID_TCP_SO_RCVBUF` — VERIFIED https://raw.githubusercontent.com/squid-cache/squid/master/src/tunnel.cc
  L485. This is the OS default SO_RCVBUF, capped at 64 KiB at build time (fallback 16 KiB) — VERIFIED
  https://raw.githubusercontent.com/squid-cache/squid/master/acinclude/os-deps.m4 L477-482. It does not cap the
  kernel TCP window, which still autotunes.
- INFERRED: at 250 MB/s a 64 KiB relay is about 3,800 read/write cycles per second on one core. That is a few per cent of a Zen 5 core,
  **not a bottleneck** at 2 Gbps.

### Firewall coupling (why the interface must be chosen before Phase 1)
- `LAN_IFACE` = "the interface of the default route" when blank — VERIFIED `config/atlas.env.example:15-16`.
- ufw is default-deny outbound — VERIFIED `phase1/04-system.sh:618`. Squid's 80/443 egress is owner-matched and **not**
  interface-bound — VERIFIED `:682`. DNS from dnsmasq is allowed only `-o $lan` to the LAN resolvers, and port 53 on
  `$lan` is dropped for everyone else — VERIFIED `:694-698`. The "LAN" outbound allow is `on "$lan"` only — VERIFIED `:656`.
- INFERRED: if a cable is added after Phase 1 and comes up, it gets the lower route metric and the LAN route. dnsmasq's
  queries to the router would then leave by the cable, match no allow rule and be dropped. Resolution would fail and
  the pull would die at the tree API. The fix would be to re-run Phase 1 step 4 with LAN_IFACE set. If the cable is not in netplan, nothing
  happens and nothing speeds up.
- The router DHCP reservation (and so the UDP 51820 forward) is per MAC, "Wi-Fi or cable" — VERIFIED
  `docs/ATLAS_FRAMEWORK_REVIEW.md:1328`. A switch of interface moves the reservation to the RTL8127's MAC.
- The doc records the node as "Wi-Fi only by Principal's choice" — VERIFIED `docs/ATLAS_FRAMEWORK_REVIEW.md:91`. The
  cable recommendation changes that choice, so it is the Principal's call. R9 (10GbE driver) is marked "Not applicable" (`:1225`).

### Disk path
- LUKS2 data volume, sector 4096 — VERIFIED `phase1/02-luks.sh:310`. dm-crypt throughput measured elsewhere at
  1.67-5.9 GB/s — VERIFIED (excerpt) `research_notes/ATLAS specialist LLM arsenal/single_engine_performance.md:245`.
  INFERRED: writing at ≤ 0.25 GB/s is far below that. **Not a bottleneck.**

## 2. The link

- Node: "Wi-Fi 7 (RZ717 / MT7925) • Theoretical Peak Speed: Up to 2.9Gbps"; "2 × 10GbE RJ45 Ports • 1 × RTL8127AT + 1 ×
  RTL8127AP • The RJ45 Port Next to DC IN Supports DASH" — VERIFIED GMKtec product page (curl, 2026-10-17)
  https://www.gmktec.com/products/gmktec-evo-x5-pro-amd-ryzen-ai-max-pro-495-ai-mini-pc
- RZ717 "supports a channel width of up to 160MHz" (no 320 MHz) — VERIFIED https://frame.work/products/amd-rz717-wi-fi-7
- Linux 7.0 r8169 carries the RTL8127A family (`{ 0x7cf, 0x6c9, RTL_GIGA_MAC_VER_80, "RTL8127A", ... }`, PCI 0x8127) —
  VERIFIED https://raw.githubusercontent.com/torvalds/linux/v7.0/drivers/net/ethernet/realtek/r8169_main.c L105-106,
  L243. Ubuntu 26.04 (7.0) supports the RJ45 card. In-tree r8169 has a single RX queue (RSS not merged), measured 3.76-8.2
  Gbit/s without RSS. That is still above 2 Gbps. Secondary source:
  https://computingforgeeks.com/rtl8127-10gbe-linux-proxmox/. Whether the "AP" (DASH) variant matches the same XID is
  UNVERIFIED, which is one more reason to use the non-DASH port.
- Wired goodput: about 950 Mbps through a 1 Gbps switch, and about 2.3 Gbps to a 2.5 Gbps port (iperf3) — VERIFIED
  https://blog.tymscar.com/posts/wifi7speedhunt/ (2025-11-01). Arithmetic: 1448/1538 bytes per frame = 94.1 % → 941 Mbps
  on 1 GbE, 2,353 Mbps on 2.5 GbE, about 1,880 Mbps on a 2,000 Mbps service (INFERRED).
- Wi-Fi 7 at 2×2 160 MHz: after the fix, 1.62 Gbit/s with 6 streams (`-P 6 -R`) and 1.6-1.7 Gbps in daily use. The first single test was 400
  Mbps at 1 ft, and the reviewer quoted gives 1,635 Mbps average and 1,890 Mbps peak on 6 GHz. "a 2×2 client on 160 MHz typically
  reaches about 1.7–2.1 Gbps" — VERIFIED same tymscar page (iPhone N1 client, not MT7925). A search snippet of an Arch bug says the MT7925 on Linux
  "usually get 650-750 Mbit/s ... wifi <-> lan" and drops to about 200 on one kernel — UNVERIFIED (Anubis blocked
  the fetch) https://gitlab.archlinux.org/archlinux/packaging/packages/linux/-/issues/112
- INFERRED: Wi-Fi 7 on this node cannot carry the full 2 Gbps. It reaches 1.6-1.9 Gbps at best, close to the router on a
  clean 160 MHz channel, and less through walls. The 10 GbE port is limited by the **router port**: a 1 GbE port caps the
  node at about 941 Mbps, and a 2.5/10 GbE port passes the whole 2 Gbps service.

## 3. Hugging Face per-connection throughput

- HF: "There's no manual bandwidth throttling applied to individual repositories" (forum, 2026-09-19). Speeds differ by
  repo and path (2 vs 30 MB/s in that thread) — VERIFIED https://discuss.huggingface.co/t/bandwidth-reduced-for-my-repository/179113
- hf_xet "starting conservatively (1 stream) and scaling up to 64 concurrent streams as bandwidth permits" — VERIFIED
  https://huggingface.co/docs/hub/en/models-downloading . In other words, HF's own client relies on many streams and not on one.
- hf_transfer exists "to go beyond ~500MB/s on very high bandwidth network" — VERIFIED https://github.com/huggingface/hf_transfer
- Rate limits are request counts (anonymous 3,000 resolver requests per 5 min), not bandwidth — VERIFIED
  https://huggingface.co/docs/hub/en/rate-limits . Four parallel files are far inside that limit.
- **Measured in this session** (cloud sandbox behind a TLS-intercepting agent proxy, so it is NOT the Principal's line; curl
  8.5, 15-25 s samples of the Day 1 files): single stream 24.8, 26.2, 26.9, 27.6, 35.5, 62.8, 64.4, 90.4, 101.1, 103.7,
  116.7 MB/s. The median is **62.8 MB/s ≈ 500 Mbps** and the range is 200-930 Mbps. Parallel range requests on one file: 4 streams 130.6 MB/s,
  8 streams 265.8 MB/s (about 33 MB/s each, which scales linearly). INFERRED: one curl stream per file is the limiter. The
  proxy, the disk and the line are not.
- INFERRED single-stream TCP ceiling from the receive window: Linux tcp_rmem max 6 MB gives about 2.4 Gbps at 20 ms RTT and
  about 1.2 Gbps at 40 ms. The window is therefore not the first limit. The CDN per-connection rate and loss (Wi-Fi especially) are.

## 4. Checksum speed

- On 26.04, `sha256sum` is uutils (rust-coreutils 0.8.0); GNU is `gnusha256sum`. On a 100 MiB file, uutils took 772.4 ms
  (≈136 MB/s) and GNU 332.7 ms (≈315 MB/s), so GNU was 2.32× faster. The CPU is not stated — VERIFIED
  https://computingforgeeks.com/ubuntu-2604-rust-coreutils-guide/
- RustCrypto sha2: "use `x86-sha` ... when the required target features are detected at runtime" (SHA-NI) — VERIFIED
  https://github.com/RustCrypto/hashes/tree/master/sha2 . Zen 5 has SHA-NI (INFERRED from the architecture).
- Measured in this session: GNU sha256sum 9.4 (libcrypto) on a Xeon **without** SHA-NI: 2 GB in 5.85 s = 0.34 GB/s.
- INFERRED for the node: probably ≥ 1 GB/s with SHA-NI, but UNVERIFIED. Range used below: 2.0 / 1.0 / 0.34 / 0.136 GB/s.

## 5. Times (INFERRED; decimal GB; durations = bytes × 8 / rate)

| Set | 2,000 Mbps line | ~1,880 Mbps best goodput | 1 GbE cap 941 Mbps | **400 Mbps (as written)** | old 100 Mbps |
|---|---|---|---|---|---|
| Day 1 Phase 3 pull, 448 GB (D-42 (b), doc :927) | 30 min | 32 min | 1 h 03 | **2 h 29** | 9 h 57 |
| Phased engines, 842 GB (doc :930) | 56 min | 1 h 00 | 1 h 59 | **4 h 41** | 18 h 43 |
| Whole arsenal, 1.9 TB (D-41, doc :934) | 2 h 07 | 2 h 15 | 4 h 29 | **10 h 33** | 42 h 13 |

Serial checksum time, added in full to every column except 400 Mbps, which already allows for it:

| Set | 2 GB/s | 1 GB/s | 0.34 GB/s | 0.136 GB/s |
|---|---|---|---|---|
| 448 GB | 4 min | 7 min | 22 min | 55 min |
| 842 GB | 7 min | 14 min | 41 min | 1 h 43 |
| 1.9 TB | 16 min | 32 min | 1 h 33 | 3 h 53 |

The 400 figure follows from the median single stream of 62.8 MB/s plus a serial hash. Effective rate = 1/(1/62.8 + 1/hash):
487 Mbps at 2 GB/s, 473 at 1 GB/s, 424 at 0.34 GB/s, 344 at 0.136 GB/s. 400 sits slightly below the middle of that range, as a planning figure should.
Phase 2's 13 GB of companions take about 4-5 min at 400 Mbps.

## 6. Does any script setting hold the pull far below line rate?

- **Yes, one does: a single curl stream, with files and engines in sequence** (common.sh:773, 04-memory.sh:378, phase3-models.sh:682).
  That holds the pull to roughly 20-50 % of a 2 Gbps line.
- No for the squid proxy (no bump, no cache, no delay pools, 64 KiB relay).
- No for hf_transfer=0 (not read by curl, and ignored by huggingface_hub ≥ 1.0).
- No for LUKS.
- Partly for the serial sha256 (4-55 min on Day 1, depending on the node's hash speed).
- Recommended changes (recommendation only, none needed for Day 1):
  1. Later (before the phased pull): let `pull_engine_files` run up to 4 `hf_download_public` calls at once, one per
     shard. Each keeps its own .part, resume, size check and sha256, and all of them stay on curl through squid. INFERRED gain: the line rate on a ≥2.5 GbE cable,
     842 GB in about 1 h instead of about 4.7 h, with the hashes overlapping the other downloads.
  2. In Phase 1 or 3, time `sha256sum`, `gnusha256sum` and `openssl dgst -sha256` on a 10 GB file, and have `_sha256_of` use the fastest.
  3. Do not turn on hf_xet: its proxy handling is UNVERIFIED and the repo deliberately disables it (Dockerfile:27-30).

## 7. What to record

- B-27 / Section 22 / workbook: "Internet: 2,000 Mbps (Principal, 2026-10-17); node on cable, 10 GbE port (non-DASH) →
  router port ≥ 2.5 GbE; planning figure DOWNLOAD_MBPS=400 (single-stream pull as written) until Phase 3 logs its first
  measured Mbit/s effective".
- 15.4 / 17 durations: about 2.5 h for the Day 1 pull (was about 10 h), about 4.7 h for the phased engines (was about 19 h), and about 10.5 h for
  the whole arsenal (was about 42 h). Wired, with parallel shards: about 0.5 h, 1 h and 2.2 h.

## 8. What would change it

- A measured rate on the node: either the first "Mbit/s effective" log line, or a 60 s curl of one shard through squid in Phase 1. If it is well above or
  below 400, record the measured figure.
- The router has only 1 GbE ports. This changes nothing for the single-stream pull (cap 941 > about 500), but it caps any
  parallel change at about 941 Mbps.
- The Principal keeps Wi-Fi. The 400 figure holds only near the router on a 160 MHz channel. Through walls, plan 200 Mbps and
  measure; R20 (Wi-Fi stability) stays at Medium.
- The node's sha256sum is slower than about 0.3 GB/s. Then lower the planning figure to about 350 Mbps, or switch to gnusha256sum.
