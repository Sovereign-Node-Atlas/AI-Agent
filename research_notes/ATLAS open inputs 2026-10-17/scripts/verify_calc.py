# Verifier arithmetic (INFERRED)
D=19000
def pre_t(N, r8):
    r0 = r8*(1+8192/(2*D)); return (N+N*N/(2*D))/r0
print("900 s slack anchor for a 3,072-token synthesis, swap inside the slot:")
for swap in (50,70,85):
    for N,r8 in ((6000,150),(8000,150),(6000,40),(8000,40)):
        p=pre_t(N,r8); need=3072/(900-swap-p)
        print(f" swap {swap}s prompt {N} @prefill8k {r8}: prefill {p:.0f}s -> needs {need:.2f} tok/s")
print("synthesis wall time at decode 4 tok/s:")
for swap in (50,85):
    for N,r8 in ((8000,150),(8000,40)):
        print(f" swap {swap} prompt {N} pf {r8}: {swap+pre_t(N,r8)+3072/4:.0f} s")
print("reasoning-length scenario (DeepSeek high effort):")
for toks in (3072,8192,16384):
    for r in (4,7,14):
        print(f" {toks} tok at {r} tok/s: {toks/r/60:.1f} min")
# margins
for h in (6.2,6.46,6.5):
    print(f"floor 4 vs healthy {h}: margin {(1-4/h)*100:.0f}%")
print("0.7*6.8 =",0.7*6.8)
# single stream window limit (B-27)
for rmem_mb in (6,32):
    for rtt in (0.02,0.04,0.16,0.18):
        for frac in (0.5,0.75):
            print(f" rmem {rmem_mb} MiB x{frac} / RTT {rtt*1000:.0f} ms -> {rmem_mb*2**20*frac/rtt*8/1e6:.0f} Mbit/s")
# Times at 200 Mbps
for gb in (448,842,1900):
    s=gb*1e9*8/200e6; print(f"{gb} GB at 200 Mbps: {s/3600:.1f} h")
