import struct, sys
data = open(sys.argv[1], 'rb').read()
p = 0
def rd(fmt):
    global p
    v = struct.unpack_from('<' + fmt, data, p); p += struct.calcsize('<' + fmt); return v[0]
def rstr():
    global p
    n = rd('Q'); s = data[p:p+n].decode('utf-8', 'replace'); p += n; return s
SC = {0:'B',1:'b',2:'H',3:'h',4:'I',5:'i',6:'f',7:'?',10:'Q',11:'q',12:'d'}
def rval(t):
    global p
    if t in SC: return rd(SC[t])
    if t == 8: return rstr()
    if t == 9:
        at = rd('I'); n = rd('Q')
        if at == 8:
            for _ in range(n): rstr()
        else:
            p += n * struct.calcsize('<' + SC[at])
        return f'<array type {at} len {n}>'
    raise ValueError(t)
assert data[:4] == b'GGUF'; p = 4
ver = rd('I'); nt = rd('Q'); nkv = rd('Q')
print('version', ver, 'tensors', nt, 'kv', nkv)
try:
    for i in range(nkv):
        k = rstr(); t = rd('I'); v = rval(t)
        if not k.startswith('tokenizer.ggml.') or 'array' not in str(v):
            print(k, '=', v if len(str(v)) < 200 else str(v)[:200])
except Exception as e:
    print('stopped at byte', p, 'of', len(data), repr(e))
