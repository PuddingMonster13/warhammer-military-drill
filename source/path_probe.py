"""Units Pass Through - "path probe", version 4 (test pack).

    python path_probe.py check            offline checks
    python path_probe.py build <out_dir>  the same checks, then writes !units_path_probe_debug.pack and the script

Run 5 showed: the lanes cave in the formation routine 0x3284304 never shifted a place. Question now: WHICH routines
run while units just stand, and which ones send a displaced model back to its place?
  - the ring cave of the place probe at "walk to" 0x3071e04 (who is told to walk where),
  - a counting cave (calls + latest return address) at the first instruction of every routine on the known ways
    from "the unit knows the place" to "the model gets a move action",
  - a counter in the formation routine's loop (the spot where the lanes cave sat).
The battle script moves six models of standing units 4 m aside (as the place probe did) and logs what ran before
and after.
"""
import os
import re
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import probe
import place_probe as pp
from probe import HEAD

VERSION = 4
PACK = '!units_path_probe_debug.pack'
SCRIPT = 'units_path_probe.lua'
BATTLE_PATH = 'script/battle/mod/' + SCRIPT

SLOTS_AT = pp.RING_AT + pp.RING * pp.ENTRY  # after the ring: 0x10 bytes per counting cave (+0 calls, +8 return address)
ROUTINES = [  # name, rva of the routine's first instruction
    ('formation_routine', 0x3284304),
    ('order_to_model_390', 0x2f7a19c),  # model method slot 0x390: order -> move request
    ('order_to_model_b', 0x2f7a2b8),
    ('entity_3070b84', 0x3070b84),  # the other caller of slot 0x390
    ('entity_3117e8c', 0x3117e8c),  # another caller of slot 0x5b8
    ('start_move_570', 0x306a8fc),  # model method slot 0x570: starts a "move to" action
    ('start_move_5b8', 0x306ad00),  # model method slot 0x5b8: starts a "move to" action from a request
    ('caller_328c624', 0x328c624),  # callers of slot 0x570
    ('caller_2f737e0', 0x2f737e0),
    ('move_action_new', 0x32490d0),  # constructor of the "move to" action
]
LOOP_SITE = ('formation_loop', 0x32844f1)
TABLE_SIZE = SLOTS_AT + 0x10 * (len(ROUTINES) + 1)

SITES = [('walk_to', 0x3071e04, pp.cave_walk(), None, 'entry')]
for n, (name, rva) in enumerate(ROUTINES):
    SITES.append((name, rva, probe.cave_plain(SLOTS_AT + 0x10 * n, SLOTS_AT + 0x10 * n + 8), None, 'entry'))
SITES.append((LOOP_SITE[0], LOOP_SITE[1], probe.cave_count(SLOTS_AT + 0x10 * len(ROUTINES)), None, 'inner'))


def signatures(x):
    # what stands at each site is taken from the exe itself; the cave rules (5 bytes or more, no jump, no rip, no
    # jump landing inside) are checked by probe.signatures, and the script compares 32 bytes before installing
    expect = {}
    for name, rva, tail, leave, kind in SITES:
        f = x.func(rva)
        assert f, name
        ins = next(x.cs.disasm(x.read(rva, 16), rva))
        expect[name] = ['%s %s' % (ins.mnemonic, ins.op_str)]
    sigs = probe.signatures(x, SITES, expect)
    for name, rva, tail, leave, kind in SITES:  # a counting cave uses only rax: fine at a routine's first instruction;
        assert kind == 'entry' or name == 'formation_loop'  # in the loop rax is free too (checked for the lanes cave)
    return sigs


def lua_script(sigs):
    lua = open(os.path.join(HERE, 'path_probe.lua.tpl'), encoding='utf8').read()
    rows = []
    for n, ((name, rva, tail, leave, kind), sig) in enumerate(zip(SITES, sigs)):
        slot = -1 if name == 'walk_to' else SLOTS_AT + 0x10 * (n - 1)
        rows.append("\t{ name = '%s', hi = %d, lo = %d, sig = '%s', tail = '%s', slot = %d }," % (
            name, rva >> 16, rva & 0xFFFF, probe.lua_bytes(sig), probe.lua_bytes(tail), slot))
    values = {
        'VERSION': VERSION, 'SITES': '\n'.join(rows), 'HEAD': probe.lua_bytes(HEAD), 'TABLE_SIZE': TABLE_SIZE,
        'INDEX': pp.INDEX, 'RING_AT': pp.RING_AT, 'RING': pp.RING, 'ENTRY': pp.ENTRY,
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
    print('site bytes read from the installed exe: %d sites' % len(sigs))
    pp.check_decode(x)
    pp.native_test()
    for name, rva, tail, leave, kind in SITES[1:]:
        code = HEAD + b'\0' * 8 + tail
        end = 0
        for ins in x.cs.disasm(code, 0):
            end = ins.address + ins.size
            assert ins.mnemonic in ('movabs', 'lock inc', 'push', 'pop', 'mov'), ins.mnemonic
            dest = ins.op_str.split(',')[0]
            assert dest in ('rax', 'rcx') or dest.split('[')[-1].startswith('rax'), ins.op_str
        assert end == len(code), name
    print('counting caves decode')
    if len(sys.argv) > 2 and sys.argv[1] == 'build':
        lua = lua_script(sigs)
        out = sys.argv[2]
        probe.write_pack(os.path.join(out, PACK), {BATTLE_PATH: lua})
        with open(os.path.join(out, SCRIPT), 'wb') as f:
            f.write(lua)
        print('script: %d lines' % lua.count(b'\n'))


if __name__ == '__main__':
    main()
