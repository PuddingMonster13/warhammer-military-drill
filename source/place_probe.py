"""Units Pass Through - "place probe", version 3 (test pack).

    python place_probe.py check            offline checks: site bytes in the installed exe, cave decodes and runs
    python place_probe.py build <out_dir>  the same checks, then writes !units_place_probe_debug.pack and the script

Question it answers: where does the game keep the place in the formation a standing model belongs to?
  - one cave at the first instruction of the routine "walk to this position" (0x3071e04: rcx = model, rdx = target,
    0x20 bytes): every call is noted in a ring (who called, which model, the target);
  - the battle script moves a few models of standing units of the player's side 4 m aside, watches which caller
    sends them back and where to, searches the model and its unit for the numbers of the place, and tries whether a
    changed place makes the model walk there.
"""
import ctypes
import os
import re
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import probe
from probe import Asm, d32, HEAD

VERSION = 3
PACK = '!units_place_probe_debug.pack'
SCRIPT = 'units_place_probe.lua'
BATTLE_PATH = 'script/battle/mod/' + SCRIPT

# table (game_alloc, zeroed): +0 number of calls so far, +0x40 ring of RING entries of ENTRY bytes:
# +0 return address, +8 model, +0x10 the 0x20 bytes of the target
INDEX = 0x00
RING_AT = 0x40
RING = 512
ENTRY = 0x30
TABLE_SIZE = RING_AT + RING * ENTRY
assert RING & (RING - 1) == 0


def cave_walk():
    """First instruction of 'walk to this position': rcx = model, rdx = target, r8 = settings.
    Changes only rax, r9, r10 and the flags (all free at a routine's first instruction)."""
    a = Asm()
    a.emit(b'\x41\xB9\x01\x00\x00\x00')  # mov r9d, 1
    a.emit(b'\xF0\x44\x0F\xC1\x88' + d32(INDEX))  # lock xadd [rax+INDEX], r9d     r9d = number of this call
    a.emit(b'\x41\x81\xE1' + d32(RING - 1))  # and r9d, RING - 1
    a.emit(b'\x4F\x8D\x0C\x49')  # lea r9, [r9+r9*2]
    a.emit(b'\x49\xC1\xE1\x04')  # shl r9, 4                                      * 0x30
    a.emit(b'\x4E\x8D\x4C\x08' + bytes([RING_AT]))  # lea r9, [rax+r9+RING_AT]
    a.emit(b'\x4C\x8B\x14\x24')  # mov r10, [rsp]                                 return address
    a.emit(b'\x4D\x89\x11')  # mov [r9], r10
    a.emit(b'\x49\x89\x49\x08')  # mov [r9+8], rcx                                the model
    for i in range(4):  # the target, 0x20 bytes
        a.emit(b'\x4C\x8B\x52' + bytes([8 * i]))  # mov r10, [rdx+8*i]
        a.emit(b'\x4D\x89\x51' + bytes([0x10 + 8 * i]))  # mov [r9+0x10+8*i], r10
    return a.done()


assert ENTRY == 0x30 and RING_AT < 0x80
SITES = [('walk_to', 0x3071e04, cave_walk(), None, 'entry')]
EXPECT = {'walk_to': ['mov qword ptr [rsp + 8], rbx', 'mov qword ptr [rsp + 0x10], rsi', 'push rdi']}


def signatures(x):
    sigs = probe.signatures(x, SITES, EXPECT)
    f = x.func(0x3071e04)
    text = {i.address: '%s %s' % (i.mnemonic, i.op_str) for i in x.cs.disasm(x.read(f[0], f[1] - f[0]), f[0])}
    # the routine copies 0x20 bytes from rdx (through rsi) into the model: so rdx points to 0x20 readable bytes
    assert text[0x3071e16] == 'mov rsi, rdx' and text[0x3071e32] == 'movups xmm0, xmmword ptr [rsi]'
    assert text[0x3071e3c] == 'movups xmm1, xmmword ptr [rsi + 0x10]' and text[0x3071e35] == 'movups xmmword ptr [rbx + 0x7f8], xmm0'
    return sigs


def check_decode(x):
    name, rva, tail, leave, kind = SITES[0]
    code = HEAD + b'\0' * 8 + tail
    end = 0
    for ins in x.cs.disasm(code, 0):
        end = ins.address + ins.size
        assert ins.mnemonic in ('movabs', 'mov', 'lock xadd', 'and', 'lea', 'shl'), ins.mnemonic
        dest = ins.op_str.split(',')[0]
        if '[' in dest:
            assert dest.split('[')[1].startswith(('rax', 'r9')), 'write outside the table: ' + ins.op_str
        else:
            assert dest in ('rax', 'r9', 'r9d', 'r10'), ins.op_str
    assert end == len(code)
    print('cave decodes: %d bytes' % len(code))


def native_test():
    k32 = ctypes.windll.kernel32
    k32.VirtualAlloc.restype = ctypes.c_void_p
    k32.VirtualAlloc.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_uint32, ctypes.c_uint32]
    table = (ctypes.c_ubyte * (TABLE_SIZE + 0x40))()
    tbl = ctypes.addressof(table)
    mem = k32.VirtualAlloc(None, 0x1000, 0x3000, 0x40)
    # the cave as the first thing of a routine f(rcx, rdx, r8): returns rcx ^ rdx ^ r8 as they are after the cave
    stub = HEAD + struct.pack('<Q', tbl) + SITES[0][2] + bytes.fromhex('4889c8' '4831d0' '4c31c0' 'c3')
    ctypes.memmove(mem, stub, len(stub))
    run = ctypes.CFUNCTYPE(ctypes.c_uint64, ctypes.c_uint64, ctypes.c_void_p, ctypes.c_uint64)(mem)
    target = (ctypes.c_ubyte * 0x20)()
    checks = 0
    for n in range(RING + 5):  # more calls than the ring holds: it wraps, nothing is written outside
        struct.pack_into('<8f', target, 0, n + 0.5, 1.0, 2.0, 3.0, 4.0, 5.0, 6.0, n + 7.5)
        model = 0x10000 + n
        assert run(model, ctypes.addressof(target), 0x77) == model ^ ctypes.addressof(target) ^ 0x77
        assert struct.unpack_from('<I', table, INDEX)[0] == n + 1
        at = RING_AT + (n % RING) * ENTRY
        ret, got = struct.unpack_from('<QQ', table, at)
        assert ret != 0 and got == model and bytes(table[at + 0x10:at + 0x30]) == bytes(target)
        checks += 3
    assert bytes(table[TABLE_SIZE:]) == b'\0' * 0x40 and bytes(table[4:RING_AT]) == b'\0' * (RING_AT - 4)
    print('native test: %d checks passed' % (checks + 1))


def lua_script(sigs):
    lua = open(os.path.join(HERE, 'place_probe.lua.tpl'), encoding='utf8').read()
    name, rva, tail, leave, kind = SITES[0]
    values = {
        'VERSION': VERSION, 'HI': rva >> 16, 'LO': rva & 0xFFFF, 'SIG': probe.lua_bytes(sigs[0]), 'TAIL': probe.lua_bytes(tail),
        'HEAD': probe.lua_bytes(HEAD), 'TABLE_SIZE': TABLE_SIZE, 'INDEX': INDEX, 'RING_AT': RING_AT, 'RING': RING, 'ENTRY': ENTRY,
        'P_UID': probe.skaven_cave.P_UID, 'UID_OFF': probe.skaven_cave.UID_OFF,
    }
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
        lua = lua_script(sigs)
        out = sys.argv[2]
        probe.write_pack(os.path.join(out, PACK), {BATTLE_PATH: lua})
        with open(os.path.join(out, SCRIPT), 'wb') as f:
            f.write(lua)
        print('script: %d lines' % lua.count(b'\n'))


if __name__ == '__main__':
    main()
