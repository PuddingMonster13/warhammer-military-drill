"""Read-only analysis of Warhammer3.exe for memreader Plus mods (never writes to the exe).

    python -m wh3tools.exe info
    python -m wh3tools.exe dis <rva> [<end rva>]              disassemble the function holding rva (or a range)
    python -m wh3tools.exe find "<pattern>" [--max 20]        memreader pattern ('48 8B ?? 05', '|' allowed) -> RVAs
    python -m wh3tools.exe strings <text> [--max 20]          RVAs of ASCII strings containing text
    python -m wh3tools.exe compare <old listing> <new listing>    after a game patch: same code apart from addresses?

Listings for `compare` are saved `dis` outputs (keep one per build in the project's re\\ folder, named with the date:
f_<name>_exe_<YYYY-MM-DD>.txt). Addresses are RVAs (image base 0x140000000).
Pattern syntax = memreader Plus find_pattern: hex bytes and ?? wildcards separated by spaces; '|' marks the
address you want (removed, its byte offset returned). Comments after ';' and line breaks are allowed in parse().
"""
import bisect
import mmap
import os
import re
import struct
import sys

from . import paths


class Exe:
    def __init__(self, path=None):
        import capstone
        import pefile
        path = path or os.environ.get('WARHAMMER3_EXE', paths.EXE)
        self.path = path
        self.pe = pefile.PE(path, fast_load=True)
        self.base = self.pe.OPTIONAL_HEADER.ImageBase
        self.f = open(path, 'rb')
        self.m = mmap.mmap(self.f.fileno(), 0, access=mmap.ACCESS_READ)
        self.secs = [(s.Name.rstrip(b'\0').decode('latin1'), s.VirtualAddress, s.Misc_VirtualSize,
                      s.PointerToRawData, s.SizeOfRawData, s.Characteristics) for s in self.pe.sections]
        d = self.pe.OPTIONAL_HEADER.DATA_DIRECTORY[3]  # exception directory = .pdata: function ranges
        raw = self.read(d.VirtualAddress, d.Size)
        self.funcs = sorted(f for f in (struct.unpack_from('<III', raw, i * 12) for i in range(len(raw) // 12)) if f[0])
        self.starts = [f[0] for f in self.funcs]
        self.cs = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_64)

    def off(self, rva):
        for n, va, vs, ro, rs, c in self.secs:
            if va <= rva < va + max(vs, rs):
                return None if rva - va >= rs else ro + rva - va
        return None

    def rva(self, off):
        for n, va, vs, ro, rs, c in self.secs:
            if ro <= off < ro + rs:
                return va + off - ro
        return None

    def sec(self, rva):
        for n, va, vs, ro, rs, c in self.secs:
            if va <= rva < va + max(vs, rs):
                return n
        return None

    def code_sections(self):
        """[(rva, bytes)] of the executable sections (this exe calls its code section .xcode, not .text)."""
        out = [(va, self.m[ro:ro + rs]) for n, va, vs, ro, rs, c in self.secs if c & 0x20000000]
        if not out:
            raise KeyError('no executable section')
        return out

    def read(self, rva, n):
        o = self.off(rva)
        return b'' if o is None else self.m[o:o + n]

    def func(self, rva):
        """(begin, end) of the .pdata entry holding rva (a function may have several chunks)."""
        i = bisect.bisect_right(self.starts, rva) - 1
        if i < 0:
            return None
        b, e, u = self.funcs[i]
        return (b, e) if b <= rva < e else None

    def cstr(self, rva, limit=120):
        b = self.read(rva, limit)
        e = b.find(b'\0') if b else -1
        if e <= 3:
            return None
        s = b[:e]
        return s.decode() if all(32 <= c < 127 for c in s) else None

    def dis(self, begin, end, out=sys.stdout, marks=()):
        code = self.read(begin, end - begin)
        for ins in self.cs.disasm(code, begin):
            note = ''
            mm = re.search(r'rip ([+-]) (0x[0-9a-f]+)', ins.op_str)
            if mm:
                t = ins.address + ins.size + (int(mm.group(2), 16) if mm.group(1) == '+' else -int(mm.group(2), 16))
                s = self.cstr(t)
                note = '  ; -> %x' % t + (' "%s"' % s if s else '') + ' [%s]' % self.sec(t)
            mark = '>>' if ins.address in marks else '  '
            out.write('%s %8x  %-22s %-7s %s%s\n' % (mark, ins.address, ins.bytes.hex(), ins.mnemonic, ins.op_str, note))

    def find(self, pattern, limit=20):
        """RVAs where a memreader pattern matches in the executable sections (pattern without '|')."""
        rx = to_regex(pattern)
        out = []
        for va, code in self.code_sections():
            for m in rx.finditer(code):
                out.append(va + m.start())
                if len(out) >= limit:
                    return out
        return out


def parse(src):
    """Pattern source (lines, '; comments', a '|' line or token for the wanted address) -> (pattern, mark offset)."""
    toks, mark = [], None
    for line in src.splitlines():
        line = line.split(';')[0].strip()
        for t in line.split():
            if t == '|':
                mark = len(toks)
            else:
                toks.append(t.upper())
    for t in toks:
        assert t == '??' or re.fullmatch('[0-9A-F]{2}', t), t
    assert toks and toks[0] != '??' and len(toks) <= 256, 'a pattern starts with a fixed byte and has at most 256 bytes'
    return ' '.join(toks), mark


def to_regex(pattern):
    return re.compile(b''.join(b'.' if t == '??' else re.escape(bytes([int(t, 16)])) for t in pattern.split()), re.S)


def lua_sites(sites, x=None, expect_len=3):
    """Lua lines for --SITES-- of memreader_locate.inc.lua. sites = [(name, pattern source with '|'), ...].
    Checks against the installed exe that each pattern matches exactly once and takes the expected bytes at the mark
    from it, so the build fails right away when the game changed."""
    from .build import lua_bytes
    x = x or Exe()
    lines = []
    for name, src in sites:
        pattern, mark = parse(src)
        hits = x.find(pattern, 2)
        assert len(hits) == 1, '%s: %d matches in the installed exe, expected 1' % (name, len(hits))
        at = hits[0] + (mark or 0)
        lines.append("\t{ name = '%s', pattern = '%s', mark = %d, expect = '%s' }," % (
            name, pattern, mark or 0, lua_bytes(x.read(at, expect_len))))
    return '\n'.join(lines)


def normalise_listing(line):
    """A `dis` line without what moves between builds: address, bytes, call/jump targets and data addresses
    (numbers >= 0x10000). Struct offsets, registers, opcodes stay."""
    m = re.match(r'^(>>|  ) +[0-9a-f]+  [0-9a-f]+ +(\S+) *(.*?)(  ;.*)?$', line.rstrip('\n'))
    if not m:
        return None
    mnem, ops = m.group(2), m.group(3)
    ops = re.sub(r'0x[0-9a-f]{5,}', 'ADDR', ops)
    if mnem.startswith(('call', 'j')):
        ops = re.sub(r'\b[0-9a-f]{5,}\b', 'ADDR', ops)
    return mnem + ' ' + ops


def compare(old_path, new_path):
    old = [x for x in (normalise_listing(l) for l in open(old_path, encoding='utf8')) if x]
    new = [x for x in (normalise_listing(l) for l in open(new_path, encoding='utf8')) if x]
    diffs = [(i, a, b) for i, (a, b) in enumerate(zip(old, new)) if a != b]
    print('%d instructions old, %d new; %d differ (addresses ignored)' % (len(old), len(new), len(diffs) + abs(len(old) - len(new))))
    for i, a, b in diffs[:30]:
        print('  #%d  old: %s\n       new: %s' % (i, a, b))
    same = not diffs and len(old) == len(new)
    print('SAME CODE' if same else 'CODE CHANGED: redo the analysis of this function before trusting the mod')
    return same


def _main(argv):
    lim = int(argv[argv.index('--max') + 1]) if '--max' in argv else 20
    if argv[:1] == ['compare'] and len(argv) == 3:
        return 0 if compare(argv[1], argv[2]) else 2
    x = Exe()
    if argv[:1] == ['info']:
        print('%s  %d bytes  image base 0x%x  %d functions' % (x.path, os.path.getsize(x.path), x.base, len(x.funcs)))
        for s in x.secs:
            print('  %-8s rva 0x%08x size 0x%08x' % (s[0], s[1], s[2]))
    elif argv[:1] == ['dis'] and len(argv) >= 2:
        a = int(argv[1], 16)
        if len(argv) > 2 and not argv[2].startswith('--'):
            b = int(argv[2], 16)
        else:
            r = x.func(a)
            if not r:
                raise SystemExit('no function holds %x' % a)
            a, b = r
        print('range %x-%x' % (a, b))
        x.dis(a, b, marks=(int(argv[1], 16),))
    elif argv[:1] == ['find'] and len(argv) >= 2:
        pattern, mark = parse(argv[1])
        hits = x.find(pattern, lim)
        print('%d match(es)%s' % (len(hits), ' (stopped at --max)' if len(hits) >= lim else ''))
        for h in hits:
            print('  %x%s  in function %s' % (h, '' if mark is None else ' -> mark %x' % (h + mark), '%x-%x' % x.func(h) if x.func(h) else '?'))
    elif argv[:1] == ['strings'] and len(argv) >= 2:
        needle = argv[1].encode()
        shown = 0
        for m in re.finditer(re.escape(needle), x.m):
            start = x.m.rfind(b'\0', 0, m.start()) + 1
            r = x.rva(start)
            if r is None:
                continue
            print('  %x [%s] "%s"' % (r, x.sec(r), x.m[start:x.m.find(b'\0', m.start())].decode('latin1')[:100]))
            shown += 1
            if shown >= lim:
                break
    else:
        print(__doc__)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(_main(sys.argv[1:]))
