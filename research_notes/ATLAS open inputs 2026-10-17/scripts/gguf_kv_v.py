import struct,sys
def rd(f,fmt): n=struct.calcsize(fmt); return struct.unpack('<'+fmt,f.read(n))
def rs(f): (n,)=rd(f,'Q'); return f.read(n).decode('utf-8','replace')
T={0:'B',1:'b',2:'H',3:'h',4:'I',5:'i',6:'f',7:'?',10:'Q',11:'q',12:'d'}
def val(f,t):
    if t==8: return rs(f)
    if t==9:
        (at,)=rd(f,'I'); (n,)=rd(f,'Q')
        if at==8: [rs(f) for _ in range(n)]; return f'<str array {n}>'
        f.read(n*struct.calcsize(T[at])); return f'<array {n}>'
    return rd(f,T[t])[0]
f=open(sys.argv[1],'rb'); assert f.read(4)==b'GGUF'; ver,=rd(f,'I'); nt,nk=rd(f,'QQ')
for _ in range(nk):
    k=rs(f); (t,)=rd(f,'I'); v=val(f,t)
    if any(x in k for x in ('context_length','block_count','head_count','key_length','value_length','rope','imatrix','general.name','file_type','quantize')): print(k,'=',v)
