G = 2**30
f16, q8 = 192_512, 192_512 * 34 // 64  # 102,272
W = {"intern-s1 Q5_K_S": 161_963_285_952, "baichuan i1-Q5_K_S": 161_881_426_496,
     "intern-s1 Q4_K_M": 142_654_184_800, "baichuan official Q4_K_M": 143_782_829_376}
MM = {"none": 0, "mmproj Q8_0 (official)": 5_972_727_424, "mmproj f16": 11_221_608_064}
CB = 2.0
for B in (155.9, 156.0, 156.9):
    print(f"\n=== budget {B} GiB, compute allowance {CB} GiB")
    for k, w in W.items():
        room = B - w / G - CB
        print(f"{k:28s} weights {w/G:7.2f}  room {room:5.2f} GiB -> f16 {int(room*G/f16):>7,d} tok, q8_0 {int(room*G/q8):>7,d} tok")
print()
for ctx in (16_384, 17_408, 24_576, 28_672, 32_768, 40_960, 65_536):
    print(f"ctx {ctx:>6,d}: f16 {ctx*f16/G:5.3f} GiB  q8_0 {ctx*q8/G:5.3f} GiB")
print()
def tot(w, ctx, per, mm=0, extra=0.0):
    return w / G + ctx * per / G + CB + mm / G + extra
for k, w in W.items():
    for ctx, per, nm in ((32_768, q8, "q8_0"), (40_960, q8, "q8_0"), (28_672, q8, "q8_0"), (16_384, f16, "f16"), (40_960, f16, "f16")):
        print(f"{k:28s} {ctx:>6,d} {nm:5s} total {tot(w, ctx, per):7.2f} GiB")
print()
w = W["intern-s1 Q4_K_M"]
for mmk, mm in MM.items():
    for ctx, per, nm in ((40_960, f16, "f16"), (40_960, q8, "q8_0")):
        print(f"intern-s1 Q4_K_M + {mmk:24s} {ctx:,d} {nm}: {tot(w, ctx, per, mm, 1.0 if mm else 0):7.2f} GiB (incl. 1.0 GiB vision compute INFERRED)")
print("intern-s1 Q5_K_S + mmproj Q8_0, no KV:", round((W['intern-s1 Q5_K_S'] + MM['mmproj Q8_0 (official)'])/G + CB, 2))
print("image tokens per figure: 13 tiles x 256 =", 13 * 256)
