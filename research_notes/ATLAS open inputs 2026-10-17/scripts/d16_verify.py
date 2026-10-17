# Verifier arithmetic for D-16: llama-perplexity host buffers at the pin (tools/perplexity/perplexity.cpp:510-525, 1738-1743)
G = 2**30
n_vocab = 151_936            # qwen3moe vocab (Intern-S1/Baichuan inherit Qwen3's; logits size used by the researcher)
nv = 2 * ((n_vocab + 1) // 2) + 4
w = {"intern-s1 Q5_K_S": 161_963_285_952, "baichuan i1-Q5_K_S": 161_881_426_496}
f16 = 192_512
def host(n_ctx, n_batch=2048, mode="base"):
    out_buf = min(n_batch, n_ctx) * n_vocab * 4                      # context output buffer (n_outputs_max = n_batch)
    logits_touched = (n_ctx - n_ctx // 2) * n_vocab * 4 if n_ctx > n_batch else 0   # second-half logits kept per chunk
    if mode == "base":
        lp = n_ctx * nv * 2                                          # log_probs.resize(n_ctx * nv) uint16, zero-filled
    else:
        lp = (n_ctx - 1 - n_ctx // 2) * nv * 2                       # log_probs_uint16 in kl_divergence()
    return (out_buf + logits_touched + lp) / G
for n_ctx in (4096, 8192, 16384):
    for mode in ("base", "kld"):
        h = host(n_ctx, mode=mode)
        for k, b in w.items():
            tot = b / G + n_ctx * f16 / G + 2.0 + h
            print(f"-c {n_ctx:>6} {mode:4s} host buffers {h:5.2f} GiB  {k:20s} weights+f16 KV+2.0+host = {tot:7.2f} GiB")
print()
for n_ctx, nb in ((4096, 512), (8192, 512)):
    for mode in ("base", "kld"):
        h = host(n_ctx, n_batch=nb, mode=mode)
        for k, b in w.items():
            print(f"-c {n_ctx} -b {nb} {mode:4s} host {h:5.2f} GiB  {k:20s} total {b/G + n_ctx*f16/G + 2.0 + h:7.2f} GiB")
# server-side long-context A/B (no full-vocab logits kept): 1 x 16,384 at f16 and at q8_0
for k, b in w.items():
    print(k, "server 16,384 f16", round(b/G + 16384*f16/G + 2.0, 2), "q8_0", round(b/G + 16384*f16*34/64/G + 2.0, 2))
# GLM wall-clock per coding task from AA output tokens per task (SciCode): GLM 27,041; Coder-Next 1,055; V4-Flash 31,296
for name, toks, lo, hi in (("GLM UD-IQ4_XS", 27_041, 12.0, 17.4), ("Coder-Next Q8_0", 1_055, 37, 44), ("V4-Flash UD-Q8_K_XL", 31_296, 7, 7)):
    print(f"{name:20s} {toks:>6,d} tok -> {toks/hi/60:5.1f}-{toks/lo/60:5.1f} min")
