"""Find instructions with a given memory displacement anywhere in the code (fast: byte search first).

python disp.py <hex disp> [<hex disp> ...] [-r lo hi]
Prints address, function, instruction. Uses the read-only helpers of the Skaven Shoot Through project.
"""
import struct, sys

sys.path.insert(0, __import__('os').path.dirname(__import__('os').path.dirname(__import__('os').path.abspath(__file__))))
from wh3tools.exe import Exe


def refs(x, d, lo=0, hi=1 << 40):
    needle = struct.pack('<I', d)
    tag = '+ 0x%x]' % d
    out = []
    done = set()
    for n, va, vs, ro, rs, c in x.secs:
        if not c & 0x20000000:
            continue
        data = x.m[ro:ro + rs]
        p = data.find(needle)
        while p != -1:
            r = va + p
            f = x.func(r)
            if f and f not in done and lo <= f[0] < hi:
                done.add(f)
                for i in x.cs.disasm(x.read(f[0], f[1] - f[0]), f[0]):
                    if tag in i.op_str:
                        out.append((i.address, f, '%s %s' % (i.mnemonic, i.op_str)))
            p = data.find(needle, p + 1)
    return sorted(out)


if __name__ == '__main__':
    x = Exe()
    a = sys.argv[1:]
    lo, hi = 0, 1 << 40
    if '-r' in a:
        k = a.index('-r')
        lo, hi = int(a[k + 1], 16), int(a[k + 2], 16)
        a = a[:k]
    for s in a:
        d = int(s, 16)
        print('disp 0x%x:' % d)
        for addr, f, t in refs(x, d, lo, hi):
            print('  %x  func %x-%x  %s' % (addr, f[0], f[1], t))
