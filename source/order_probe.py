"""Units Pass Through - "order probe", version 5 (test pack).

    python order_probe.py check            offline checks
    python order_probe.py build <out_dir>  the same checks, then writes !units_order_probe_debug.pack and the script

Question: does a standing model keep an order object (class vtable 0x3905e18) with its place at +0x40, and does
the model walk to a changed place when that order is edited?
One cave at the first instruction of the order's step routine 0x2f6b170 (rcx = the order) notes every order that
is stepped (order, return address) in a ring. The battle script does the rest.
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

VERSION = 5
PACK = '!units_order_probe_debug.pack'
SCRIPT = 'units_order_probe.lua'
BATTLE_PATH = 'script/battle/mod/' + SCRIPT

ORDER_VTABLE = 0x3905e18
STEP = 0x2f6b170
# table: +0 number of steps so far, +0x40 ring of RING entries of 0x10 bytes: +0 order, +8 return address
INDEX, RING_AT, RING, ENTRY = 0x00, 0x40, 4096, 0x10
TABLE_SIZE = RING_AT + RING * ENTRY


def cave_step():
    """First instruction of the order's step: rcx = the order. Changes only rax, r9, r10 and the flags."""
    a = Asm()
    a.emit(b'\x41\xB9\x01\x00\x00\x00')  # mov r9d, 1
    a.emit(b'\xF0\x44\x0F\xC1\x88' + d32(INDEX))  # lock xadd [rax+INDEX], r9d
    a.emit(b'\x41\x81\xE1' + d32(RING - 1))  # and r9d, RING - 1
    a.emit(b'\x49\xC1\xE1\x04')  # shl r9, 4
    a.emit(b'\x4E\x8D\x4C\x08' + bytes([RING_AT]))  # lea r9, [rax+r9+RING_AT]
    a.emit(b'\x49\x89\x09')  # mov [r9], rcx
    a.emit(b'\x4C\x8B\x14\x24')  # mov r10, [rsp]
    a.emit(b'\x4D\x89\x51\x08')  # mov [r9+8], r10
    return a.done()


SITES = [('order_step', STEP, cave_step(), None, 'entry')]
EXPECT = {'order_step': ['push rbx', 'sub rsp, 0x20', 'mov rax, qword ptr [rcx + 0x30]']}


def signatures(x):
    sigs = probe.signatures(x, SITES, EXPECT)
    f = x.func(STEP)
    text = {i.address: '%s %s' % (i.mnemonic, i.op_str) for i in x.cs.disasm(x.read(f[0], f[1] - f[0]), f[0])}
    # the step waits for the battle tick to reach order +0xa0, and the model hangs at [[order+0x10]+8]
    assert text[0x2f6b184] == 'mov eax, dword ptr [rcx + 0xa0]' and text[0x2f6b18a] == 'cmp dword ptr [rdx + 0x98], eax'
    assert text[0x2f6b192] == 'mov rax, qword ptr [rcx + 0x10]' and text[0x2f6b196] == 'mov rcx, qword ptr [rax + 8]'
    assert text[0x2f6b1db] == 'call 0x2f79ff0'
    # "send my model to my place" reads the place at order +0x40
    f = x.func(0x2f79ff0)
    text = {i.address: '%s %s' % (i.mnemonic, i.op_str) for i in x.cs.disasm(x.read(f[0], f[1] - f[0]), f[0])}
    assert text[0x2f7a025] == 'lea r10, [rcx + 0x40]' and text[0x2f7a034] == 'call 0x311d0cc'
    # the class: vtable slot 1 is the thunk that ends in the step
    assert struct.unpack('<Q', x.read(ORDER_VTABLE + 8, 8))[0] - x.base == 0x2f737d0
    return sigs


def native_test():
    k32 = ctypes.windll.kernel32
    k32.VirtualAlloc.restype = ctypes.c_void_p
    k32.VirtualAlloc.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_uint32, ctypes.c_uint32]
    table = (ctypes.c_ubyte * (TABLE_SIZE + 0x40))()
    tbl = ctypes.addressof(table)
    mem = k32.VirtualAlloc(None, 0x1000, 0x3000, 0x40)
    stub = HEAD + struct.pack('<Q', tbl) + SITES[0][2] + bytes.fromhex('4889c8' '4831d0' '4c31c0' 'c3')  # returns rcx ^ rdx ^ r8
    ctypes.memmove(mem, stub, len(stub))
    run = ctypes.CFUNCTYPE(ctypes.c_uint64, ctypes.c_uint64, ctypes.c_uint64, ctypes.c_uint64)(mem)
    for n in range(RING + 3):
        assert run(0x5000 + n, 0x22, 0x33) == (0x5000 + n) ^ 0x22 ^ 0x33
        at = RING_AT + (n % RING) * ENTRY
        order, ret = struct.unpack_from('<QQ', table, at)
        assert order == 0x5000 + n and ret != 0 and struct.unpack_from('<I', table, INDEX)[0] == n + 1
    assert bytes(table[TABLE_SIZE:]) == b'\0' * 0x40 and bytes(table[4:RING_AT]) == b'\0' * (RING_AT - 4)
    print('native test: %d calls checked' % (RING + 3))


def lua_script(sigs):
    lua = open(os.path.join(HERE, 'order_probe.lua.tpl'), encoding='utf8').read()
    name, rva, tail, leave, kind = SITES[0]
    values = {
        'VERSION': VERSION, 'HI': rva >> 16, 'LO': rva & 0xFFFF, 'SIG': probe.lua_bytes(sigs[0]), 'TAIL': probe.lua_bytes(tail),
        'HEAD': probe.lua_bytes(HEAD), 'TABLE_SIZE': TABLE_SIZE, 'INDEX': INDEX, 'RING_AT': RING_AT, 'RING': RING, 'ENTRY': ENTRY,
        'VT_HI': ORDER_VTABLE >> 16, 'VT_LO': ORDER_VTABLE & 0xFFFF,
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
    code = HEAD + b'\0' * 8 + SITES[0][2]
    end = 0
    for ins in x.cs.disasm(code, 0):
        end = ins.address + ins.size
        assert ins.mnemonic in ('movabs', 'mov', 'lock xadd', 'and', 'lea', 'shl'), ins.mnemonic
        dest = ins.op_str.split(',')[0]
        assert dest in ('rax', 'r9', 'r9d', 'r10') or dest.split('[')[-1].startswith(('rax', 'r9')), ins.op_str
    assert end == len(code)
    print('cave decodes: %d bytes' % len(code))
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
