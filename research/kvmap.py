"""Map kv rule names to their field offset in the rules object (value = field + 0x60).

python kvmap.py [regex]      prints "offset value_offset name" for matching names
The registration function is 0x57a640..0x57c18e: lea rcx,[rsi+off] ... lea rdx,[name] ; call 0x57a42c
"""
import re, sys

sys.path.insert(0, __import__('os').path.dirname(__import__('os').path.dirname(__import__('os').path.abspath(__file__))))
from wh3tools.exe import Exe


def kvmap(x):
    out = {}
    last = None
    b, e = 0x57a640, 0x57c18e
    for i in x.cs.disasm(x.read(b, e - b), b):
        t = i.op_str
        m = re.match(r'rcx, \[r\w+ \+ (0x[0-9a-f]+)\]$', t)
        if i.mnemonic == 'lea' and m:
            last = int(m.group(1), 16)
            continue
        if i.mnemonic == 'mov' and re.match(r'rcx, r\w+$', t):
            last = 0
            continue
        m = re.match(r'rdx, \[rip ([+-]) (0x[0-9a-f]+)\]', t)
        if i.mnemonic == 'lea' and m and last is not None:
            tgt = i.address + i.size + (int(m.group(2), 16) if m.group(1) == '+' else -int(m.group(2), 16))
            s = x.cstr(tgt)
            if s:
                out[s] = last
            last = None
    return out


if __name__ == '__main__':
    x = Exe()
    pat = re.compile(sys.argv[1] if len(sys.argv) > 1 else '.', re.I)
    for s, o in sorted(kvmap(x).items(), key=lambda p: p[1]):
        if pat.search(s):
            print('%5x %5x %s' % (o, o + 0x60, s))
