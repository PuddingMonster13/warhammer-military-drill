"""Units Pass Through - diagnostic probe, version 2 (test pack).

    python probe.py check            offline checks: site bytes in the installed exe, caves decode, caves run natively
    python probe.py build <out_dir>  the same checks, then writes !units_pass_probe_debug.pack and units_pass_probe.lua

What the probe does in a battle:
  - counts how often the game's "these two models touch" routine runs, for which kind of pair (same unit / other
    friendly unit / enemy) and who calls it;
  - counts how often a model gets marked "blocked" by another model, and remembers the latest friendly pair;
  - counts friendly pairs at the routine that picks collision partners;
  - EXPERIMENTS, one mode per battle (battle 1 since the game was started = mode 0, battle 2 = mode 1, battle 3 =
    mode 2, battle 4 = mode 0 ...), switched by the battle script through the mode value in the table:
      mode 0  nothing is changed
      mode 1  a model walking into a model of ANOTHER FRIENDLY UNIT is not marked "blocked"
      mode 2  models of DIFFERENT FRIENDLY UNITS are not picked as collision partners at all
The battle script writes everything to units_pass_probe_log.txt in the game folder.
"""
import ctypes
import os
import re
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
import uid_pattern as skaven_cave  # the proven unique_ui_id pattern (from the Skaven mod)

VERSION = 2
PACK = '!units_pass_probe_debug.pack'
SCRIPT = 'units_pass_probe.lua'
BATTLE_PATH = 'script/battle/mod/' + SCRIPT
SIG_LEN = 32  # bytes compared at every site before anything is installed

# entity fields
ALLIANCE, UNIT = 0x80, 0x2e8

# table (game_alloc, zeroed)
MODE = 0x00
BATTLES = 0x04  # battles since the game was started (only the battle script touches it)
H_TOTAL, H_SAME, H_OTHER, H_ENEMY = 0x10, 0x14, 0x18, 0x1c
H_RET_SAME, H_RET_OTHER, H_RET_ENEMY = 0x20, 0x28, 0x30
O1_COUNT, O1_RET, O2_COUNT, O2_RET = 0x40, 0x48, 0x50, 0x58
B1, B2 = 0x60, 0x80  # blocks: +0 same unit, +4 other friendly unit, +8 enemy, +0xc not marked (mode 1), +0x10 A, +0x18 B
B_SAME, B_OTHER, B_ENEMY, B_SKIP, B_A, B_B = 0x00, 0x04, 0x08, 0x0c, 0x10, 0x18
S3, S4, S5 = 0xa0, 0xa4, 0xa8
G_SAME, G_OTHER, G_SKIP = 0xb0, 0xb4, 0xb8
TABLE_SIZE = 0x100
HEAD = b'\x48\xB8'  # mov rax, imm64 (the table address follows)
EXIT_MARK = b'\xE1\xE2\xE3\xE4\xE5\xE6\xE7\xE8'  # replaced by the battle script with the exit address

REG = {'rbx': 3, 'rsi': 6, 'rdi': 7}


def d32(v):
    return struct.pack('<i', v)


class Asm:
    def __init__(self):
        self.code = bytearray()
        self.fix = []
        self.labels = {}

    def emit(self, b):
        self.code.extend(b)

    def jump(self, opcode, label):  # 0x74 je, 0x75 jne, 0xEB jmp (all rel8)
        self.emit(bytes([opcode, 0]))
        self.fix.append((len(self.code) - 1, label))

    def label(self, name):
        self.labels[name] = len(self.code)

    def lock_inc(self, off):
        self.emit(b'\xF0\xFF\x80' + d32(off))  # lock inc dword [rax+off]

    def mov_rdx_from(self, reg, off):
        self.emit(b'\x48\x8B' + bytes([0x90 | REG[reg]]) + d32(off))  # mov rdx, [reg+off]

    def cmp_rdx_with(self, reg, off):
        self.emit(b'\x48\x3B' + bytes([0x90 | REG[reg]]) + d32(off))  # cmp rdx, [reg+off]

    def store(self, off, reg):
        self.emit(b'\x48\x89' + bytes([0x80 | REG[reg] << 3]) + d32(off))  # mov [rax+off], reg

    def cmp_mode(self, value):
        self.emit(b'\x83\xB8' + d32(MODE) + bytes([value]))  # cmp dword [rax+MODE], value

    def leave(self):
        self.emit(b'\x48\xB8' + EXIT_MARK + b'\xFF\xE0')  # mov rax, EXIT ; jmp rax

    def done(self):
        for pos, label in self.fix:
            rel = self.labels[label] - (pos + 1)
            assert -128 <= rel <= 127, label
            self.code[pos] = rel & 0xFF
        return bytes(self.code)


def cave_handler():
    """Entry of the pair routine: rcx = model A, rdx = model B. Changes only rax and the flags."""
    a = Asm()
    a.lock_inc(H_TOTAL)
    a.emit(b'\x41\x50')  # push r8
    a.emit(b'\x4C\x8B\x81' + d32(ALLIANCE))  # mov r8, [rcx+0x80]
    a.emit(b'\x4C\x3B\x82' + d32(ALLIANCE))  # cmp r8, [rdx+0x80]
    a.jump(0x75, 'enemy')
    a.emit(b'\x4C\x8B\x81' + d32(UNIT))  # mov r8, [rcx+0x2e8]
    a.emit(b'\x4C\x3B\x82' + d32(UNIT))  # cmp r8, [rdx+0x2e8]
    a.jump(0x74, 'same')
    for name, count, ret in (('other', H_OTHER, H_RET_OTHER), ('enemy', H_ENEMY, H_RET_ENEMY), ('same', H_SAME, H_RET_SAME)):
        if name != 'other':
            a.label(name)
        a.lock_inc(count)
        a.emit(b'\x4C\x8B\x44\x24\x08')  # mov r8, [rsp+8]      return address
        a.emit(b'\x4C\x89\x80' + d32(ret))  # mov [rax+ret], r8
        if name != 'same':
            a.jump(0xEB, 'done')
    a.label('done')
    a.emit(b'\x41\x58')  # pop r8
    return a.done()


def cave_plain(count, ret):
    """Entry of a routine: count the call and keep the return address. Changes only rax and the flags."""
    a = Asm()
    a.lock_inc(count)
    a.emit(b'\x51')  # push rcx
    a.emit(b'\x48\x8B\x4C\x24\x08')  # mov rcx, [rsp+8]
    a.emit(b'\x48\x89\x88' + d32(ret))  # mov [rax+ret], rcx
    a.emit(b'\x59')  # pop rcx
    return a.done()


def cave_block(base, mover, blocker):
    """Just before 'model <mover> is blocked by model <blocker>'. Free there: rax, rdx, flags."""
    a = Asm()
    a.mov_rdx_from(blocker, ALLIANCE)
    a.cmp_rdx_with(mover, ALLIANCE)
    a.jump(0x75, 'enemy')
    a.mov_rdx_from(blocker, UNIT)
    a.cmp_rdx_with(mover, UNIT)
    a.jump(0x74, 'same')
    a.lock_inc(base + B_OTHER)
    a.store(base + B_A, mover)
    a.store(base + B_B, blocker)
    a.cmp_mode(1)
    a.jump(0x75, 'done')
    a.lock_inc(base + B_SKIP)
    a.leave()
    a.label('enemy')
    a.lock_inc(base + B_ENEMY)
    a.jump(0xEB, 'done')
    a.label('same')
    a.lock_inc(base + B_SAME)
    a.label('done')
    return a.done()


def cave_count(off):
    a = Asm()
    a.lock_inc(off)
    return a.done()


def cave_gate():
    """In the routine that picks collision partners: rdi = this model, rbx = the other. Free: rax, rdx, flags."""
    a = Asm()
    a.mov_rdx_from('rbx', ALLIANCE)
    a.cmp_rdx_with('rdi', ALLIANCE)
    a.jump(0x75, 'done')
    a.mov_rdx_from('rbx', UNIT)
    a.cmp_rdx_with('rdi', UNIT)
    a.jump(0x74, 'same')
    a.lock_inc(G_OTHER)
    a.cmp_mode(2)
    a.jump(0x75, 'done')
    a.lock_inc(G_SKIP)
    a.leave()
    a.label('same')
    a.lock_inc(G_SAME)
    a.label('done')
    return a.done()


# name, rva of the first replaced instruction, cave code after 'mov rax, TABLE', rva the cave may leave to (or None),
# registers the test loads (rcx, rdx, r8 -> these), what must be true at the site
SITES = [
    ('pair_handler', 0x3066c34, cave_handler(), None, 'entry'),
    ('pair_override_1', 0x31162cc, cave_plain(O1_COUNT, O1_RET), None, 'entry'),
    ('pair_override_2', 0x31163d4, cave_plain(O2_COUNT, O2_RET), None, 'entry'),
    ('blocked_a_by_b', 0x3066ee3, cave_block(B1, 'rdi', 'rsi'), 0x3066ef6, 'inner'),
    ('blocked_b_by_a', 0x3066f63, cave_block(B2, 'rsi', 'rdi'), 0x306703d, 'inner'),
    ('blocked_site_3', 0x306707b, cave_count(S3), None, 'inner'),
    ('blocked_site_4', 0x3067091, cave_count(S4), None, 'inner'),
    ('blocked_site_5', 0x3067101, cave_count(S5), None, 'inner'),
    ('partner_gate', 0x3062892, cave_gate(), 0x3062938, 'inner'),
]
# the instructions each cave is expected to sit in front of (checked against the exe)
EXPECT = {
    'pair_handler': ['mov rax, rsp'],
    'pair_override_1': ['mov qword ptr [rsp + 8], rbx'],
    'pair_override_2': ['mov qword ptr [rsp + 8], rbx'],
    'blocked_a_by_b': ['test dword ptr [rdi + 0x188], r13d', 'jne 0x3066ef3', 'mov byte ptr [rdi + 0x1b0], r15b'],
    'blocked_b_by_a': ['test dword ptr [rsi + 0x188], r13d', 'jne 0x3066f73', 'mov byte ptr [rsi + 0x1b0], r15b'],
    'blocked_site_3': ['mov byte ptr [rdi + 0x1b0], r15b'],
    'blocked_site_4': ['mov byte ptr [rsi + 0x1b0], r15b'],
    'blocked_site_5': ['mov byte ptr [rdi + 0x1b0], r15b'],
    'partner_gate': ['and ecx, dword ptr [rdi + 0x6c]', 'mov rax, qword ptr [rbx + 0x80]', 'cmp qword ptr [rdi + 0x80], rax'],
}


def exe():
    sys.path.insert(0, os.path.dirname(HERE))
    from wh3tools.exe import Exe  # the shared read-only reader (has find() for the patterns of sites.py)
    return Exe()


def signatures(x, sites=None, expect=None):
    """Reads the site bytes from the installed exe and checks that a cave can sit there."""
    out = []
    EXPECT = expect or globals()['EXPECT']
    for name, rva, tail, leave, kind in (sites or SITES):
        f = x.func(rva)
        assert f, name
        if kind == 'entry':
            assert f[0] == rva, '%s: %x is not the start of a function' % (name, rva)
        ins = list(x.cs.disasm(x.read(f[0], f[1] - f[0]), f[0]))
        starts = {i.address: n for n, i in enumerate(ins)}
        assert rva in starts, '%s: not at an instruction boundary' % name
        if leave is not None:
            assert leave in starts and f[0] <= leave < f[1], '%s: exit is not an instruction of the same function' % name
        n = starts[rva]
        texts = [re.sub(r'rip \+ 0x[0-9a-f]+', 'rip + ADDR', '%s %s' % (i.mnemonic, i.op_str)) for i in ins[n:n + len(EXPECT[name])]]  # data addresses move between builds
        assert texts == EXPECT[name], (name, texts)
        # the cave replaces the first instructions (5 bytes or more): none of them may be a jump, call or return,
        # none may use rip, and no jump of the function may land inside the replaced bytes
        size = 0
        while size < 5:
            i = ins[n]
            assert not (i.mnemonic.startswith('j') or i.mnemonic in ('call', 'ret')), (name, i.mnemonic)
            assert 'rip' not in i.op_str, (name, i.op_str)
            size += i.size
            n += 1
        for i in ins:
            if i.mnemonic.startswith('j') and re.fullmatch(r'0x[0-9a-f]+', i.op_str):
                t = int(i.op_str, 16)
                assert not (rva < t < rva + size), '%s: a jump lands inside the replaced bytes (%x)' % (name, t)
        sig = bytes(x.read(rva, SIG_LEN))
        assert len(sig) == SIG_LEN
        out.append(sig)
    return out


def check_decode(x):
    for name, rva, tail, leave, kind in SITES:
        assert (EXIT_MARK in tail) == (leave is not None), name
        assert tail.count(EXIT_MARK) <= 1
        code = HEAD + b'\0' * 8 + tail
        end = depth = 0
        for ins in x.cs.disasm(code, 0):
            end = ins.address + ins.size
            depth += ins.mnemonic == 'push'
            depth -= ins.mnemonic == 'pop'
            assert ins.mnemonic in ('movabs', 'lock inc', 'push', 'pop', 'mov', 'cmp', 'je', 'jne', 'jmp'), (name, ins.mnemonic)
            if ins.mnemonic in ('je', 'jne') or (ins.mnemonic == 'jmp' and ins.op_str != 'rax'):
                assert int(ins.op_str, 16) <= len(code), 'jump out of the cave'
            dest = ins.op_str.split(',')[0]
            if ins.mnemonic in ('mov', 'movabs') and '[' not in dest:
                assert dest in ('rax', 'rcx', 'rdx', 'r8'), (name, dest)
                if kind == 'entry':
                    assert dest == 'rax' or depth == 1, (name, ins.op_str)  # rcx / r8 only between push and pop
                else:
                    assert dest in ('rax', 'rdx'), (name, dest)
            if '[' in dest and ins.mnemonic in ('mov', 'lock inc'):
                assert dest.split('[')[1].startswith('rax'), 'write outside the table: ' + ins.op_str
        assert end == len(code), '%s: cave does not decode to its end' % name
        assert depth == 0
    print('caves decode: %s bytes' % [len(HEAD) + 8 + len(t) for n, r, t, l, k in SITES])


def native_test():
    """Runs every cave inside this Python process with fake models and checks the table and the path taken."""
    k32 = ctypes.windll.kernel32
    k32.VirtualAlloc.restype = ctypes.c_void_p
    k32.VirtualAlloc.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_uint32, ctypes.c_uint32]
    table = (ctypes.c_ubyte * TABLE_SIZE)()
    tbl = ctypes.addressof(table)
    q = lambda off: struct.unpack_from('<Q', table, off)[0]
    c = lambda off: struct.unpack_from('<I', table, off)[0]
    checks = 0

    def model(alliance, unit):
        m = (ctypes.c_ubyte * 0x400)()
        struct.pack_into('<Q', m, ALLIANCE, alliance)
        struct.pack_into('<Q', m, UNIT, unit)
        return m

    m_a = model(0x1000, 0x7001)
    pairs = {'same': model(0x1000, 0x7001), 'other': model(0x1000, 0x7002), 'enemy': model(0x2000, 0x7003)}
    A = ctypes.addressof
    three = [ctypes.c_uint64] * 3

    def build(tail, entry):
        mem = k32.VirtualAlloc(None, 0x1000, 0x3000, 0x40)
        assert mem
        if entry:  # returns rcx ^ rdx ^ r8 computed after the cave: the arguments must come through unchanged
            stub = HEAD + struct.pack('<Q', tbl) + tail + bytes.fromhex('4889c8' '4831d0' '4c31c0' 'c3')
        else:  # rcx -> rdi, rdx -> rsi, r8 -> rbx; returns 1 when the cave runs to its end, 2 when it leaves
            pre = bytes.fromhex('57' '56' '53' '4889cf' '4889d6' '4c89c3')
            body = HEAD + struct.pack('<Q', tbl) + tail
            post = bytes.fromhex('b801000000' 'eb05' 'b802000000' '5b' '5e' '5f' 'c3')
            leave_at = mem + len(pre) + len(body) + 7
            stub = pre + body.replace(EXIT_MARK, struct.pack('<Q', leave_at)) + post
        ctypes.memmove(mem, stub, len(stub))
        return ctypes.CFUNCTYPE(ctypes.c_uint64, *three)(mem)

    def others_untouched(mine):
        snap = bytes(table)
        for lo, hi in mine:
            snap = snap[:lo] + b'\0' * (hi - lo) + snap[hi:]
        return snap == b'\0' * TABLE_SIZE

    for name, rva, tail, leave, kind in SITES:
        run = build(tail, kind == 'entry')
        ctypes.memset(tbl, 0, TABLE_SIZE)
        if name == 'pair_handler':
            for n, (which, count, ret) in enumerate((('same', H_SAME, H_RET_SAME), ('other', H_OTHER, H_RET_OTHER), ('enemy', H_ENEMY, H_RET_ENEMY))):
                b = pairs[which]
                assert run(A(m_a), A(b), 0x3333) == A(m_a) ^ A(b) ^ 0x3333
                assert c(H_TOTAL) == n + 1 and c(count) == 1 and q(ret) != 0
                checks += 4
            assert others_untouched([(0x10, 0x38)])
        elif name.startswith('pair_override'):
            count, ret = (O1_COUNT, O1_RET) if name.endswith('1') else (O2_COUNT, O2_RET)
            assert run(0x1111, 0x2222, 0x3333) == 0x1111 ^ 0x2222 ^ 0x3333
            assert c(count) == 1 and q(ret) != 0 and others_untouched([(count, count + 4), (ret, ret + 8)])
            checks += 4
        elif name.startswith('blocked_') and leave is not None:
            base = B1 if name == 'blocked_a_by_b' else B2
            for mode in (0, 1, 2):
                for which in ('same', 'other', 'enemy'):
                    ctypes.memset(tbl, 0, TABLE_SIZE)
                    struct.pack_into('<I', table, MODE, mode)
                    b = pairs[which]
                    # the mover is rdi at the first site and rsi at the second
                    got = run(A(m_a), A(b), 0) if name == 'blocked_a_by_b' else run(A(b), A(m_a), 0)
                    left = mode == 1 and which == 'other'
                    assert got == (2 if left else 1), (name, mode, which, got)
                    assert c(base + B_SAME) == (which == 'same') and c(base + B_OTHER) == (which == 'other')
                    assert c(base + B_ENEMY) == (which == 'enemy') and c(base + B_SKIP) == left
                    if which == 'other':
                        assert q(base + B_A) == A(m_a) and q(base + B_B) == A(b)
                    else:
                        assert q(base + B_A) == 0 and q(base + B_B) == 0
                    assert others_untouched([(MODE, MODE + 4), (base, base + 0x20)])
                    checks += 6
        elif name.startswith('blocked_site'):
            off = {'3': S3, '4': S4, '5': S5}[name[-1]]
            assert run(1, 2, 3) == 1 and run(1, 2, 3) == 1 and c(off) == 2 and others_untouched([(off, off + 4)])
            checks += 3
        elif name == 'partner_gate':
            for mode in (0, 1, 2):
                for which in ('same', 'other', 'enemy'):
                    ctypes.memset(tbl, 0, TABLE_SIZE)
                    struct.pack_into('<I', table, MODE, mode)
                    got = run(A(m_a), 0, A(pairs[which]))  # rdi = this, rbx = other
                    left = mode == 2 and which == 'other'
                    assert got == (2 if left else 1), (mode, which, got)
                    assert c(G_SAME) == (which == 'same') and c(G_OTHER) == (which == 'other') and c(G_SKIP) == left
                    assert others_untouched([(MODE, MODE + 4), (G_SAME, G_SKIP + 4)])
                    checks += 4
        else:
            raise AssertionError(name)
    print('native test: %d checks passed' % checks)


def lua_bytes(b):
    return ''.join('\\%03d' % v for v in b)


def lua_script(sigs):
    lua = open(os.path.join(HERE, 'probe.lua.tpl'), encoding='utf8').read()
    rows = []
    for (name, rva, tail, leave, kind), sig in zip(SITES, sigs):
        at = tail.find(EXIT_MARK)
        rows.append("\t{ name = '%s', hi = %d, lo = %d, sig = '%s', tail = '%s', exit_at = %d, exit_hi = %d, exit_lo = %d }," % (
            name, rva >> 16, rva & 0xFFFF, lua_bytes(sig), lua_bytes(tail), at, (leave or 0) >> 16, (leave or 0) & 0xFFFF))
    values = {
        'VERSION': VERSION, 'SITES': '\n'.join(rows), 'TABLE_SIZE': TABLE_SIZE, 'HEAD': lua_bytes(HEAD), 'MODE': MODE, 'BATTLES': BATTLES,
        'H_TOTAL': H_TOTAL, 'H_SAME': H_SAME, 'H_OTHER': H_OTHER, 'H_ENEMY': H_ENEMY,
        'H_RET_SAME': H_RET_SAME, 'H_RET_OTHER': H_RET_OTHER, 'H_RET_ENEMY': H_RET_ENEMY,
        'O1_COUNT': O1_COUNT, 'O1_RET': O1_RET, 'O2_COUNT': O2_COUNT, 'O2_RET': O2_RET,
        'B1': B1, 'B2': B2, 'B_SAME': B_SAME, 'B_OTHER': B_OTHER, 'B_ENEMY': B_ENEMY, 'B_SKIP': B_SKIP, 'B_A': B_A, 'B_B': B_B,
        'S3': S3, 'S4': S4, 'S5': S5, 'G_SAME': G_SAME, 'G_OTHER': G_OTHER, 'G_SKIP': G_SKIP,
        'P_UID': skaven_cave.P_UID, 'UID_OFF': skaven_cave.UID_OFF,
    }
    for name, value in values.items():
        assert ('--%s--' % name) in lua, name
        lua = lua.replace('--%s--' % name, str(value))
    left = re.findall(r'--[A-Z_0-9]+--', lua)
    assert not left, left
    return lua.encode('utf8')


def write_pack(path, files):
    idx = b''
    data = b''
    for name in sorted(files):
        idx += struct.pack('<I', len(files[name])) + b'\0' + name.replace('/', chr(92)).encode('utf8') + b'\0'
        data += files[name]
    with open(path, 'wb') as f:
        f.write(b'PFH5' + struct.pack('<6I', 3, 0, 0, len(files), len(idx), 0) + idx + data)
    print('%s  %d bytes  %s' % (os.path.basename(path), os.path.getsize(path), ', '.join(sorted(files))))


def main():
    x = exe()
    sigs = signatures(x)
    print('site bytes read from the installed exe: %d sites' % len(sigs))
    check_decode(x)
    native_test()
    if len(sys.argv) > 2 and sys.argv[1] == 'build':
        lua = lua_script(sigs)
        out = sys.argv[2]
        write_pack(os.path.join(out, PACK), {BATTLE_PATH: lua})
        with open(os.path.join(out, SCRIPT), 'wb') as f:
            f.write(lua)
        print('script: %d lines' % lua.count(b'\n'))


if __name__ == '__main__':
    main()
