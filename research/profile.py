"""One line per function: size and how often it touches the entity fields that matter for collisions.

python profile.py <rva> [<rva> ...]
Fields: a0 radius, 9c height, 98 ?, 88/90 position, e0/e8 velocity, b4 mass, 80 alliance, 2e8 Unit, 74 kind.
"""
import re, sys

sys.path.insert(0, __import__('os').path.dirname(__import__('os').path.dirname(__import__('os').path.abspath(__file__))))
from wh3tools.exe import Exe

x = Exe()
KEYS = ['0xa0', '0x9c', '0x98', '0x88', '0x90', '0xe0', '0xe8', '0xb4', '0x80', '0x2e8', '0x74']
print('func              size ' + ' '.join('%5s' % k[2:] for k in KEYS) + '  sqrt calls kvrules')
for s in sys.argv[1:]:
    b, e = x.func(int(s, 16))
    n = dict.fromkeys(KEYS, 0)
    sq = calls = 0
    kv = set()
    for i in x.cs.disasm(x.read(b, e - b), b):
        t = i.op_str
        for k in KEYS:
            if ('+ %s]' % k) in t:
                n[k] += 1
        if i.mnemonic.startswith('sqrt'):
            sq += 1
        if i.mnemonic == 'call':
            calls += 1
        m = re.search(r'\[rax \+ (0x[0-9a-f]{4})\]', t)
        if m and i.mnemonic in ('movss', 'mulss', 'comiss', 'addss', 'subss', 'divss', 'ucomiss', 'cmp', 'mov', 'maxss', 'minss'):
            kv.add(m.group(1))
    print('%x-%x %5x ' % (b, e, e - b) + ' '.join('%5d' % n[k] for k in KEYS) + '  %4d %5d %s' % (sq, calls, ' '.join(sorted(kv))))
