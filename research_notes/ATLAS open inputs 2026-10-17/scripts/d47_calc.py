# D-47 arithmetic (INFERRED). Decode-only times; prefill and swaps separate.
def mmss(s):
    m, s = divmod(round(s), 60)
    return f"{m}:{s:02d}"
rates = [3, 4, 5, 6, 7, 8, 11, 12.5, 14, 16, 20, 30, 45, 57]
print("tok/s | 500 tok | 1,500 tok | 2,000 tok | 2,500 tok | 3,072 tok (deep-tier max_tokens)")
for r in rates:
    print(r, "|", " | ".join(mmss(n / r) for n in (500, 1500, 2000, 2500, 3072)))

# Deep Think token budgets from deep_think.py (max_tokens): standard 2048 per GLM call, scoring 1024 per DeepSeek call
# deep: 3072 per GLM call; GLM calls 3 + 1 refine + 3 + 1 refine = 8; DeepSeek 6 scorings x 1024 + synthesis 3072
def deep_run(g, d, frac=1.0, swaps=4, swap_s=70, prefill_s=300):
    glm = 8 * 3072 * frac / g
    ds = (6 * 1024 + 3072) * frac / d
    return glm, ds, glm + ds + swaps * swap_s + prefill_s
def std_run(g, d, frac=1.0, swaps=2, swap_s=70, prefill_s=120):
    glm = 4 * 2048 * frac / g
    ds = 3 * 1024 * frac / d
    return glm, ds, glm + ds + swaps * swap_s + prefill_s
print()
print("Deep tier, GLM g / DeepSeek d tok/s, max tokens (frac 1.0) and half (0.5): GLM decode, DS decode, total wall (min)")
for g, d in [(13, 7), (12.5, 6.8), (6, 4), (8, 5), (13, 4), (13, 14.5), (13, 16.3)]:
    for f in (1.0, 0.5):
        a, b, t = deep_run(g, d, f)
        print(f"g={g} d={d} frac={f}: GLM {a/60:.1f} min, DS {b/60:.1f} min, total {t/60:.1f} min")
print()
print("Standard tier")
for g, d in [(13, 7), (6, 4), (13, 4), (13, 14.5)]:
    for f in (1.0, 0.5):
        a, b, t = std_run(g, d, f)
        print(f"g={g} d={d} frac={f}: GLM {a/60:.1f} min, DS {b/60:.1f} min, total {t/60:.1f} min")

# Prefill model r(d) = r0/(1+d/D), D ~ 19,000 (fit: Gorgon GLM 256.4@2k-chunk depth0 -> 95.9@32k; DS IQ2 mainline 224.5 -> 154.3@8k -> 82.2@32k)
D_gorgon = 32768 / (256.4 / 95.9 - 1)
D_ds8 = 8192 / (224.5 / 154.3 - 1)
D_ds32 = 32768 / (224.5 / 82.2 - 1)
print()
print(f"fitted D: GLM Gorgon {D_gorgon:.0f}, DS 8k {D_ds8:.0f}, DS 32k {D_ds32:.0f}")
D = 19000
def prefill_time(N, r8):
    r0 = r8 * (1 + 8192 / (2 * D))  # the loadtest 8k point averages over 0..8k
    return (N + N * N / (2 * D)) / r0
for r8 in (30, 40, 50, 100, 150):
    print(f"r8={r8}: 24k {mmss(prefill_time(24000, r8))}, 32k {mmss(prefill_time(32768, r8))}, 64k {mmss(prefill_time(65536, r8))}, 8k scoring prompt 6k {mmss(prefill_time(6000, r8))}")

# 900 s one-generation budget (tasks/__init__.py DEFAULT_TIMEOUT_SLACK_S): decode needed for 3,072 tokens after a prefill
for pre_s in (60, 120, 160):
    print(f"3072 tok in 900 s after {pre_s} s prefill needs {3072/(900-pre_s):.2f} tok/s")

# CPU-fallback decode ceilings at ~108 GB/s effective CPU bandwidth (Llama-2-7B Q4_0 CPU 28.65 tok/s, #13565)
cpu_bw = 28.65 * (3.83 - 0.07)
print(f"CPU effective GB/s ~{cpu_bw:.0f}")
for name, gb, proj in [("gpt-oss-120b", 3.75, 57), ("GLM UD-IQ4_XS", 13.10, 12.5), ("DeepSeek UD-Q8_K_XL", 17.085, 6.8), ("router 4B Q8_0", 4.48, 43)]:
    c = cpu_bw / gb
    print(f"{name}: CPU ceiling {c:.1f} tok/s = {c/proj*100:.0f}% of projection low {proj}")
# router projection
for eff in (0.70, 0.80):
    print(f"router 4.48 GB at {eff:.0%} of 273: {273*eff/4.48:.1f} tok/s")
