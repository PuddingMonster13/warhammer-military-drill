"""Units Pass Through - "diagnostic probe", version 7 (test pack).

    python diag_probe.py check | build <out_dir>

Runs NEXT TO the lanes test build 0.6 (it reads the lanes table, it changes nothing in the game) and answers in one
battle the four questions of 2026-10-14:
  1. a MARCHING unit: which routines give its models their places while it moves (to make it form columns);
  2. big monsters: their real size (0.6 read the K'daai Destroyer as an ordinary 0.6 m model);
  3. standing cavalry: why many of its models did not walk to the lane places they were sent to;
  4. artillery: what a moving war machine consists of (the lane has to fit the machine, not the crew).
Caves (all at a routine's first instruction, so all volatile registers except the arguments are free; each cave
saves r9 / r10 / r11 anyway, because some routines may take a 4th argument in r9):
  start_move 0x306a8fc (rcx = model, rdx = move request): ring of (return, model, 16 bytes of the request, stack words)
  walk_to    0x3071e04 (rcx = model, rdx = target)      : ring of (return, model, 0x20 bytes of the target)
  order_390  0x2f7a19c (rcx = model, rdx = order)       : ring of (return, model, stack words)
  formation  0x3284304 (the formation routine)          : count + last return address
  form_loop  0x32844f1 (one model in its loop)          : count
Addresses here are those of the analysed build (2026-10-12). Since build 25961564 (2026-10-19) every site is found
by byte pattern (sites.py), here and in the battle script; locate() puts the addresses of the installed exe in SITES.
"""
import ctypes
import os
import re
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import probe
import sites
from probe import Asm, d32, HEAD

VERSION = 7
PACK = '!units_diag_probe_debug.pack'
SCRIPT = 'units_diag_probe.lua'
BATTLE_PATH = 'script/battle/mod/' + SCRIPT
LANES_SITE = 0x327e49d  # cave 1 of the lanes build: the script finds the lanes table through it
IMAGE_SIZE = 0x3400000  # stack words inside [base, base + this) count as code addresses

WORDS = 24
RINGS = [  # name, rva, slots, what is kept besides return address and model
    ('start_move', 0x306a8fc, 1024, 'request+stack'),
    ('walk_to', 0x3071e04, 1024, 'target'),
    ('order_390', 0x2f7a19c, 1024, 'stack'),
]
COUNTERS = [('formation', 0x3284304, 'plain'), ('form_loop', 0x32844f1, 'count')]


def entry_size(kind):
    return 0x10 + (0x10 if kind == 'request+stack' else 0) + (0x20 if kind == 'target' else 0) + (8 * WORDS if 'stack' in kind else 0)


def stack_at(kind):
    return 0x20 if kind == 'request+stack' else 0x10


# table: +0x00.. one dword index per ring (8 bytes apart), +0x40.. counters (0x10 each: count, last return address),
# then the rings
INDEX_AT = {name: 8 * n for n, (name, rva, slots, kind) in enumerate(RINGS)}
COUNT_AT = {name: 0x40 + 0x10 * n for n, (name, rva, kind) in enumerate(COUNTERS)}
RING_AT = {}
_at = 0x100
for _name, _rva, _slots, _kind in RINGS:
    RING_AT[_name] = _at
    _at += _slots * entry_size(_kind)
TABLE_SIZE = _at


def cave_ring(name):
    """Routine entry: rcx = model, rdx = second argument. Keeps return address, model and what the ring wants.
    Saves r9 / r10 / r11; changes rax (the table) and the flags only."""
    _, rva, slots, kind = next(r for r in RINGS if r[0] == name)
    size = entry_size(kind)
    assert slots & (slots - 1) == 0
    a = Asm()
    a.emit(b'\x41\x51\x41\x52\x41\x53')  # push r9; push r10; push r11       the return address is now at [rsp+0x18]
    a.emit(b'\x41\xB9\x01\x00\x00\x00')  # mov r9d, 1
    a.emit(b'\xF0\x44\x0F\xC1\x88' + d32(INDEX_AT[name]))  # lock xadd [rax+index], r9d
    a.emit(b'\x41\x81\xE1' + d32(slots - 1))  # and r9d, slots - 1
    a.emit(b'\x4D\x69\xC9' + d32(size))  # imul r9, r9, size
    a.emit(b'\x4E\x8D\x8C\x08' + d32(RING_AT[name]))  # lea r9, [rax+r9+ring]
    a.emit(b'\x4C\x8B\x54\x24\x18')  # mov r10, [rsp+0x18]                  return address
    a.emit(b'\x4D\x89\x11')  # mov [r9], r10
    a.emit(b'\x49\x89\x49\x08')  # mov [r9+8], rcx                         the model
    if kind == 'request+stack':
        for i in range(2):
            a.emit(b'\x4C\x8B\x52' + bytes([8 * i]))  # mov r10, [rdx+8*i]
            a.emit(b'\x4D\x89\x51' + bytes([0x10 + 8 * i]))  # mov [r9+0x10+8*i], r10
    if kind == 'target':
        for i in range(4):
            a.emit(b'\x4C\x8B\x52' + bytes([8 * i]))  # mov r10, [rdx+8*i]
            a.emit(b'\x4D\x89\x51' + bytes([0x10 + 8 * i]))  # mov [r9+0x10+8*i], r10
    if 'stack' in kind:
        a.emit(b'\x45\x31\xDB')  # xor r11d, r11d
        a.label('loop')
        a.emit(b'\x4E\x8B\x54\xDC\x20')  # mov r10, [rsp+r11*8+0x20]          the words above the return address
        a.emit(b'\x4F\x89\x54\xD9' + bytes([stack_at(kind)]))  # mov [r9+r11*8+stack], r10
        a.emit(b'\x49\xFF\xC3')  # inc r11
        a.emit(b'\x49\x83\xFB' + bytes([WORDS]))  # cmp r11, WORDS
        a.jump(0x72, 'loop')  # jb
    a.emit(b'\x41\x5B\x41\x5A\x41\x59')  # pop r11; pop r10; pop r9
    return a.done()


SITES = [(name, rva, cave_ring(name), None, 'entry') for name, rva, slots, kind in RINGS]
for _name, _rva, _kind in COUNTERS:
    if _kind == 'plain':
        SITES.append((_name, _rva, probe.cave_plain(COUNT_AT[_name], COUNT_AT[_name] + 8), None, 'entry'))
    else:
        SITES.append((_name, _rva, probe.cave_count(COUNT_AT[_name]), None, 'inner'))
EXPECT = {
    'start_move': ['mov qword ptr [rsp + 8], rbx', 'push rdi'],
    'walk_to': ['mov qword ptr [rsp + 8], rbx', 'mov qword ptr [rsp + 0x10], rsi', 'push rdi'],
    'order_390': ['mov qword ptr [rsp + 8], rbx', 'mov qword ptr [rsp + 0x10], rsi', 'push rdi', 'sub rsp, 0xe0', 'mov rdi, rdx', 'mov rbx, rcx'],
    'formation': ['mov rax, rsp', 'mov byte ptr [rax + 0x10], dl'],
    'form_loop': ['lea rdx, [rbp + 0xa0]', 'mov rcx, r13', 'call 0x311d0cc'],
}
READ_RECORD = 0x311d0cc  # what form_loop calls (analysed build)


def locate(x):
    """Puts the addresses of the installed exe (found by pattern, each must match once) into SITES."""
    SITES[:] = [(name, sites.find(x, name), tail, leave, kind) for name, rva, tail, leave, kind in SITES]
    return {name: rva for name, rva, tail, leave, kind in SITES}


def signatures(x):
    found = locate(x)
    loop_call = sites.call_target(x, found['form_loop'] + 10)
    expect = dict(EXPECT, form_loop=EXPECT['form_loop'][:2] + ['call 0x%x' % loop_call])
    probe.signatures(x, SITES, expect)
    sigs = [sites.expect_bytes(x, rva) for name, rva, tail, leave, kind in SITES]
    # start move: rdx -> request read at once (16 bytes are there); walk to: copies 0x20 bytes from rdx
    text = sites.View(x, found['start_move'], 0x306a8fc, 0x306a937)
    assert text.start == found['start_move'] and text[0x306a90d] == 'mov rdi, rdx'
    text = sites.View(x, found['walk_to'], 0x3071e04)
    assert text.start == found['walk_to']
    assert text[0x3071e16] == 'mov rsi, rdx' and text[0x3071e32] == 'movups xmm0, xmmword ptr [rsi]' and text[0x3071e3c] == 'movups xmm1, xmmword ptr [rsi + 0x10]'
    assert x.func(found['order_390'])[0] == found['order_390']
    # the formation loop site is the one lanes 0.4 used (rax free there)
    text = sites.View(x, found['form_loop'], 0x3284304, 0x3284df3, names={loop_call: READ_RECORD})
    assert text.start == found['formation'] and text.new(0x32844f1) == found['form_loop'] and text[0x32844f1 + 10] == 'call 0x311d0cc'
    # the lanes cave 1 site, read only
    text = sites.View(x, sites.find(x, 'place_send'), 0x327e358, 0x327e531)
    assert text.new(LANES_SITE) == sites.find(x, 'place_send')
    return sigs


def check_decode(x):
    for name, rva, tail, leave, kind in SITES:
        code = HEAD + b'\0' * 8 + tail
        end = depth = 0
        for ins in x.cs.disasm(code, 0):
            end = ins.address + ins.size
            line = '%s %s' % (ins.mnemonic, ins.op_str)
            depth += ins.mnemonic == 'push'
            depth -= ins.mnemonic == 'pop'
            assert ins.mnemonic in ('movabs', 'push', 'pop', 'mov', 'lock xadd', 'and', 'imul', 'lea', 'xor', 'inc', 'cmp', 'jb', 'lock inc'), line
            dest = ins.op_str.split(',')[0]
            if ins.mnemonic in ('push', 'pop', 'cmp', 'jb'):
                continue
            if '[' in dest:
                assert dest.split('[')[-1].startswith(('rax', 'r9')), line
            else:
                assert dest in ('rax', 'r9', 'r9d', 'r10', 'r11', 'r11d', 'rcx'), line
                assert dest != 'rcx' or depth == 1, line  # cave_plain: rcx is pushed / popped around its use
        assert end == len(code) and depth == 0, name
    print('caves decode: %d' % len(SITES))


def native_test():
    """Every ring cave in a stub that passes model / argument and r9 / r10 / r11 / r8 values that must survive."""
    k32 = ctypes.windll.kernel32
    k32.VirtualAlloc.restype = ctypes.c_void_p
    k32.VirtualAlloc.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_uint32, ctypes.c_uint32]
    table = (ctypes.c_ubyte * (TABLE_SIZE + 0x40))()
    tbl = ctypes.addressof(table)
    mem = k32.VirtualAlloc(None, 0x1000 * len(RINGS), 0x3000, 0x40)
    checks = 0
    arg = (ctypes.c_ubyte * 0x40)()
    for i in range(0x40):
        arg[i] = i + 1
    out = (ctypes.c_uint64 * 4)()
    for n, (name, rva, slots, kind) in enumerate(RINGS):
        tail = SITES[n][2]
        # f(rcx, rdx, r8 = out): sets r9 / r10 / r11 to marks, runs the cave from a call (like the game), stores them
        body = HEAD + struct.pack('<Q', tbl) + tail + bytes.fromhex('c3')
        at = mem + 0x1000 * n
        stub = (bytes.fromhex('49b9') + struct.pack('<Q', 0x9999) + bytes.fromhex('49ba') + struct.pack('<Q', 0xaaaa) + bytes.fromhex('49bb') + struct.pack('<Q', 0xbbbb)
                + bytes.fromhex('6a77' '6a66' 'e806000000' '4883c410' 'eb' + '%02x' % (len(body))) + body
                + bytes.fromhex('4d8908' '4d895008' '4d895810' 'c3'))
        ctypes.memmove(at, stub, len(stub))
        run = ctypes.CFUNCTYPE(None, ctypes.c_uint64, ctypes.c_void_p, ctypes.c_void_p)(at)
        size = entry_size(kind)
        for call in range(slots + 3):
            run(0x7000 + call, ctypes.addressof(arg), ctypes.addressof(out))
            assert tuple(out[:3]) == (0x9999, 0xaaaa, 0xbbbb), 'r9 / r10 / r11 survive'
            e = tbl + RING_AT[name] + (call % slots) * size
            ret, model = struct.unpack('<QQ', bytes(table[e - tbl:e - tbl + 16]))
            assert model == 0x7000 + call and at < ret < at + len(stub), (hex(ret), hex(at))
            if kind == 'request+stack':
                assert bytes(table[e - tbl + 0x10:e - tbl + 0x20]) == bytes(arg[:0x10])
            if kind == 'target':
                assert bytes(table[e - tbl + 0x10:e - tbl + 0x30]) == bytes(arg[:0x20])
            if 'stack' in kind:
                words = struct.unpack_from('<%dQ' % WORDS, table, e - tbl + stack_at(kind))
                assert words[:2] == (0x66, 0x77), words[:3]  # the two words pushed before the call
            assert struct.unpack_from('<I', table, INDEX_AT[name])[0] == call + 1
            checks += 1
    assert bytes(table[TABLE_SIZE:]) == bytes(0x40)
    print('native test: %d calls checked' % checks)


def lua_script(x, sigs):
    lua = open(os.path.join(HERE, 'diag_probe.lua.tpl'), encoding='utf8').read()
    for (name, rva, tail, leave, kind), sig in zip(SITES, sigs):
        assert sites.expect_bytes(x, rva) == sig, name
    rings = ',\n'.join("\t%s = { index = %d, at = %d, slots = %d, size = %d, stack = %s, extra = '%s' }" % (
        name, INDEX_AT[name], RING_AT[name], slots, entry_size(kind), stack_at(kind) if 'stack' in kind else 'nil', kind) for name, rva, slots, kind in RINGS)
    counters = ',\n'.join("\t%s = %d" % (name, COUNT_AT[name]) for name, rva, kind in COUNTERS)
    values = {'VERSION': VERSION, 'FIND_SITE': sites.lua_find_site(),
              'SITES': ',\n'.join('\t' + sites.lua_site(x, name, tail=tail) for name, rva, tail, leave, kind in SITES),
              'RINGS': rings, 'COUNTERS': counters, 'WORDS': WORDS, 'HEAD': probe.lua_bytes(HEAD),
              'TABLE_SIZE': TABLE_SIZE, 'IMAGE_HI': IMAGE_SIZE >> 16, 'LANES_SITE': sites.lua_site(x, 'place_send'),
              'P_UID': probe.skaven_cave.P_UID, 'UID_OFF': probe.skaven_cave.UID_OFF}
    for key, value in values.items():
        assert ('--%s--' % key) in lua, key
        lua = lua.replace('--%s--' % key, str(value))
    left = re.findall(r'--[A-Z_0-9]+--', lua)
    assert not left, left
    return lua.encode('utf8')


def main():
    x = probe.exe()
    sigs = signatures(x)
    print('site bytes read from the installed exe')
    check_decode(x)
    native_test()
    if len(sys.argv) > 2 and sys.argv[1] == 'build':
        lua = lua_script(x, sigs)
        out = sys.argv[2]
        probe.write_pack(os.path.join(out, PACK), {BATTLE_PATH: lua})
        with open(os.path.join(out, SCRIPT), 'wb') as f:
            f.write(lua)
        print('script: %d lines, %d bytes of table' % (lua.count(b'\n'), TABLE_SIZE))


if __name__ == '__main__':
    main()
