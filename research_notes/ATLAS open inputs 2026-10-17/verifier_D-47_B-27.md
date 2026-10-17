# Verifier notes: D-47 (+D-33, T-06) and B-27, 2026-10-17

## D-47: what held up
- deep_think.py:72-73, :265, :332, :360, :406, :466; api.py:1407-1411; engines.json:14, :51/:53, :119/:121, :159/:161 re-read and confirmed.
- results.json re-fetched (4,450,920 B, 114 jobs, newest 2026-10-09). V4.1 gorgon-halo T=0: 14.8/14.7/14.5, 14.4/14.5/14.3, DSpark 16.3/18.4/16.9. Mainline Vulkan 0731 IQ2_XXS 12.6/12.2/11.1. Nothing newer than 2026-10-09.
- PR #28696 is still open: 2 approvals required, none given, latest comment 2026-10-10. Releases go up to b11706, with no deepseek/glm5 notes.
- d47_calc.py re-run: the tables reproduce.

## D-47: corrections
1. The 900 s figure is OrchestratorClient's read-timeout slack: 1,800 + 900 = 2,700 s (tasks/__init__.py:186-197, :221-225). The swap happens inside the slot (api.py:1403-1404, 1608-1609), so the 900 s covers swap, prefill and decode. With a healthy prefill, 3,072/(900 - swap - prefill) = 3.8-4.0 tok/s. At the 40 tok/s prefill floor it is 4.3-5.0 (vfy/verify_calc.py). The floor value of 4 holds, but the derivation changes. The 900 s is a design figure, not a hard stop.
2. Cross-checks run at 0.2 (approval.py:387), not at 0.8 or 0.15.
3. max_tokens counts reasoning tokens (lcpp server-context.cpp:486-492, :2029), and Deep Think keeps only delta.content (api.py:1612, :1418). At high effort the 3,072-token cap can truncate the synthesis. Lucebox's V4.1 answers ran into a 16K limit (lucebox_engine_memory_hierarchy.md:207-208).
4. D-61 allows production DSpark with V4-Flash only at a lower rung. T-06 tests DSpark at Q8, but the Q8 floor verdict should be taken with DSpark off.
5. Fitting V4.1 with DSpark into 156 GiB is UNVERIFIED: 151.76 GiB plus a 4.58-7.42 GiB drafter (Lucebox sizes).
6. api.py:1367 tells the Principal "15 to 30 min" (deep) and "5 to 8" (standard), which is optimistic.
7. BF16 mat-vec has Vulkan pipelines at the pin (ggml-vulkan.cpp:2938, :3024), so there is no BF16 CPU-fallback concern.

## B-27: what held up
- Script, squid and firewall lines confirmed. tcp_recv_bufsize defaults to 0 (cf.data.pre:9327-9331) and is absent from the template.
- GMKtec page, tymscar and computingforgeeks re-fetched.
- r8169 v7.0: VER_80 rev 0x04 maps to RTL_DASH_25_BP (rtl_get_dash_type), so the DASH variant is handled. rtl8127a-1.fw is in linux-firmware WHENCE.
- My two single-stream samples ran at 92-96 MB/s (CloudFront ORD58, cache Miss).

## B-27: corrections
- The node is in Australia (doc L62). HF lists only US and EU CDN hosts, and every sample so far comes from a US datacenter, so 400 Mbps is a placeholder. Measure on the Principal's own line before Phase 3.
- tymscar: the 1.7-2.1 Gbps figure is an estimate. The measurements were 1.62 Gbps with 6 streams and 400 Mbps single-stream before the fix.
- The HF forum "no throttling" reply is from a poster not identified as staff.
- DOWNLOAD_MBPS=100 also appears in lib/common.sh:1072 and CONVENTIONS.md:82. The atlas.env.example:84 comment is stale.
- docker-egress-rules.sh also binds to LAN_IFACE. Services bind to LAN_IP (01-preflight.sh:159), so reserve the same IP for the new MAC and take Wi-Fi off the subnet.
