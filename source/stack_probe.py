"""Units Pass Through - "stack probe", version 6 (test pack).

    python stack_probe.py check | build <out_dir>

Question: which chain of game routines sends a displaced INFANTRY model back to its place?
One cave at the first instruction of "start move" 0x306a8fc (rcx = model, rdx = move request). For every call it
notes the model, the first 16 bytes of the request (the target) and 63 words of the stack: the battle script
picks the words that point into the game's code = the routines the call came through.
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

VERSION = 6
PACK = '!units_stack_probe_debug.pack'
SCRIPT = 'units_stack_probe.lua'
BATTLE_PATH = 'script/battle/mod/' + SCRIPT

START_MOVE = 0x306a8fc
INDEX, RING_AT, RING, WORDS = 0x00, 0x40, 256, 63
ENTRY = 0x18 + 8 * WORDS  # +0 model, +8 request bytes 0..15, +0x18 the stack words
TABLE_SIZE = RING_AT + RING * ENTRY
IMAGE_SIZE = 0x3400000  # the script treats stack words inside [base, base + this) as code addresses (the game's own code)


def cave_start():
    """First instruction of "start move": rcx = model, rdx = request. Changes only rax, r9, r10, r11, flags."""
    a = Asm()
    a.emit(b'\x41\xB9\x01\x00\x00\x00')  # mov r9d, 1
    a.emit(b'\xF0\x44\x0F\xC1\x88' + d32(INDEX))  # lock xadd [rax+INDEX], r9d
    a.emit(b'\x41\x81\xE1' + d32(RING - 1))  # and r9d, RING - 1
    a.emit(b'\x4D\x69\xC9' + d32(ENTRY))  # imul r9, r9, ENTRY
    a.emit(b'\x4E\x8D\x4C\x08' + bytes([RING_AT]))  # lea r9, [rax+r9+RING_AT]
    a.emit(b'\x49\x89\x09')  # mov [r9], rcx
    a.emit(b'\x4C\x8B\x12')  # mov r10, [rdx]
    a.emit(b'\x4D\x89\x51\x08')  # mov [r9+8], r10
    a.emit(b'\x4C\x8B\x52\x08')  # mov r10, [rdx+8]
    a.emit(b'\x4D\x89\x51\x10')  # mov [r9+0x10], r10
    a.emit(b'\x45\x31\xDB')  # xor r11d, r11d
    a.label('loop')
    a.emit(b'\x4E\x8B\x14\xDC')  # mov r10, [rsp+r11*8]
    a.emit(b'\x4F\x89\x54\xD9\x18')  # mov [r9+r11*8+0x18], r10
    a.emit(b'\x49\xFF\xC3')  # inc r11
    a.emit(b'\x49\x83\xFB' + bytes([WORDS]))  # cmp r11, WORDS
    a.jump(0x72, 'loop')  # jb
    return a.done()


SITES = [('start_move', START_MOVE, cave_start(), None, 'entry')]
EXPECT = {'start_move': ['mov qword ptr [rsp + 8], rbx']}


def signatures(x):
    sigs = probe.signatures(x, SITES, EXPECT)
    f = x.func(0x32490d0)  # the constructor the routine calls reads the request it was given (rdx -> r8): a real pointer
    assert f and x.func(START_MOVE) == (0x306a8fc, 0x306a937)
    assert START_MOVE < IMAGE_SIZE
    return sigs


def native_test():
    k32 = ctypes.windll.kernel32
    k32.VirtualAlloc.restype = ctypes.c_void_p
    k32.VirtualAlloc.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_uint32, ctypes.c_uint32]
    table = (ctypes.c_ubyte * (TABLE_SIZE + 0x40))()
    tbl = ctypes.addressof(table)
    mem = k32.VirtualAlloc(None, 0x1000, 0x3000, 0x40)
    stub = HEAD + struct.pack('<Q', tbl) + SITES[0][2] + bytes.fromhex('4889c8' '4831d0' 'c3')  # returns rcx ^ rdx
    ctypes.memmove(mem, stub, len(stub))
    run = ctypes.CFUNCTYPE(ctypes.c_uint64, ctypes.c_uint64, ctypes.c_void_p)(mem)
    request = (ctypes.c_ubyte * 0x20)()
    for n in range(RING + 3):
        struct.pack_into('<4f', request, 0, n + 0.5, 1.0, 2.0, 3.0)
        assert run(0x7000 + n, ctypes.addressof(request)) == (0x7000 + n) ^ ctypes.addressof(request)
        at = RING_AT + (n % RING) * ENTRY
        assert struct.unpack_from('<Q', table, at)[0] == 0x7000 + n and bytes(table[at + 8:at + 0x18]) == bytes(request[:16])
        assert struct.unpack_from('<Q', table, at + 0x18)[0] != 0, 'first stack word = the return address'
        assert struct.unpack_from('<I', table, INDEX)[0] == n + 1
    assert bytes(table[TABLE_SIZE:]) == b'\0' * 0x40 and bytes(table[4:RING_AT]) == b'\0' * (RING_AT - 4)
    print('native test: %d calls checked' % (RING + 3))


def lua_script(sigs):
    lua = open(os.path.join(HERE, 'stack_probe.lua.tpl'), encoding='utf8').read()
    name, rva, tail, leave, kind = SITES[0]
    values = {
        'VERSION': VERSION, 'HI': rva >> 16, 'LO': rva & 0xFFFF, 'SIG': probe.lua_bytes(sigs[0]), 'TAIL': probe.lua_bytes(tail),
        'HEAD': probe.lua_bytes(HEAD), 'TABLE_SIZE': TABLE_SIZE, 'INDEX': INDEX, 'RING_AT': RING_AT, 'RING': RING, 'ENTRY': ENTRY,
        'WORDS': WORDS, 'IMAGE_HI': IMAGE_SIZE >> 16,
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
        assert ins.mnemonic in ('movabs', 'mov', 'lock xadd', 'and', 'lea', 'imul', 'xor', 'inc', 'cmp', 'jb'), ins.mnemonic
        dest = ins.op_str.split(',')[0]
        if ins.mnemonic not in ('cmp', 'jb'):
            assert dest in ('rax', 'r9', 'r9d', 'r10', 'r11', 'r11d') or dest.split('[')[-1].startswith(('rax', 'r9')), ins.op_str
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
