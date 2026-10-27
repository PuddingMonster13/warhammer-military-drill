"""Units Pass Through - where the lanes build and the probes find the game code (byte PATTERNS, no fixed addresses).

    python sites.py      every pattern against the installed Warhammer3.exe: one match each, and where it is

Written on 2026-10-19 after game build 25961564 moved every routine (lanes 0.6 and probe 7 had fixed RVAs of the
2026-10-12 exe). The routines themselves were unchanged: same size, same instructions apart from addresses
(compared in listings of the game's code, which are not published).

Pattern rules (as in source/cave.py of https://github.com/PuddingMonster13/skaven-lore-firendly-friendly-fire): `??` only for call targets and data addresses; opcodes,
registers, stack slots, struct offsets and every jump distance are fixed, so a match means the control flow is the
analysed one. '|' marks the wanted address. A site with call=True is the target of the E8 call at the mark (for a
routine that has byte-identical twins and can only be told apart by who calls it).
The battle scripts get the same patterns (lua_sites) and check the bytes at each site with read_original.
"""
import os
import re
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from wh3tools.exe import Exe, parse  # noqa: E402

PATTERNS = {
    # lanes cave 1: in the loop of the routine that hands every model its place, at `mov r8, r13 / mov rdx, r12 /
    # mov rcx, rbx / call <hand the place>` (the wait was just put to [rsp+0x28]); the loop ends with `jb` back
    'place_send': '''89 44 24 28  0F 28 DF  66 89 54 24 20 |
        4D 8B C5  49 8B D4  48 8B CB  E8 ?? ?? ?? ??  84 C0  74 02  FF C5  FF C7
        49 83 C7 30  49 83 C6 08  3B FE  0F 82 EF FE FF FF''',
    # lanes cave 2: the 5 s test of the "keep your places" state object (rbx = C; C+0x88 = time of the last run)
    'recheck': '''8B 05 ?? ?? ?? ??  F3 48 0F 2A C0  F3 0F 59 C8 |
        F3 0F 10 83 88 00 00 00  F3 0F 58 05 ?? ?? ?? ??  F3 0F 59 0D ?? ?? ?? ??  0F 2F C8  76 12
        48 8B 03  48 8B CB  48 83 C4 20  5B  48 FF A0 C8 00 00 00''',
    # lanes cave 3: the model's own tick (rdi = the model), right after a call, at the station gate +0x88a / +0x884
    'station': '''48 8B CF  E8 ?? ?? ?? ??  84 C0  0F 85 A4 04 00 00 |
        44 38 BF 8A 08 00 00  0F 85 47 02 00 00  48 8B 47 58  48 8B 88 40 04 00 00
        8B 87 84 08 00 00  39 81 98 00 00 00  0F 82 2A 02 00 00''',
    # the game's "here is your station" (model, station record of 0x38 bytes): cave 3 calls it
    'set_station': '''| 40 53  48 81 EC 90 00 00 00  48 8B 41 58  48 8B D9  48 8B 88 40 04 00 00  48 8B 89 50 01 00 00
        E8 ?? ?? ?? ??  84 C0  74 37''',
    # lanes cave 4 (0.7): the return address of the call that hands a MARCHING model its next point to walk to
    # (a target a few metres ahead on its way, built in the caller's stack frame at [rbp-0x31]; found with probe 7,
    # run 11: return 0x325b395 in build 25961564). Not a cave site: cave 4 at walk_to compares its return address.
    'carrot_ret': '''48 8D 55 CF  89 45 CB  E8 ?? ?? ?? ?? |  83 8B 98 00 00 00 01  4C 8D 9C 24 D0 00 00 00''',
    # lanes cave 5 (0.85e): the per-entity collision pass 0x316ff04 calls the pair routine for each other entity B in the cells of A and, when it
    # returns 1, the pair handler `call [B.vtable + 0xb8]` (sets the blocked byte). The mark is the `mov rax, [rdi+0x58]` that starts the call setup
    # (r15 = A entity, r14 = B entity, both after the 0x200000 redirect); the `jne` after the pair routine skips the handler: its target
    # (mark + 0x43, the end of the vcall) is where a pair is skipped to
    'ghost_pass': '''4C 8B 75 D8 | 48 8B 47 58  4D 8B CF  4C 89 74 24 30  4D 8B C4  48 8B D3  48 8B 88 ?? ?? ?? ??
        48 8B 45 E0  48 89 44 24 28  4C 89 6C 24 20  48 8B 49 ??  E8 ?? ?? ?? ??  3C 01  75 0F
        49 8B 06  49 8B D7  49 8B CE  FF 90 ?? ?? ?? ??''',
    # probe 7 ------------------------------------------------------------------------------------------------------
    # "start move" (model slot 0x570): about 20 wrappers have the same bytes, so it is found through its only
    # direct caller, the override of a derived class that calls the base first
    # (a twin override with the same layout calls another wrapper; the two differ in the jmp distance)
    'start_move': '''48 8B D7  48 8B CB | E8 ?? ?? ?? ??  8B 83 64 0B 00 00  48 8B BB 68 0B 00 00  48 8D 34 C7
        E9 8C 00 00 00  48 8B 1F  48 8B 83 08 03 00 00''',
    # "walk to" (rcx = model, rdx = target of 0x20 bytes)
    'walk_to': '''| 48 89 5C 24 08  48 89 74 24 10  57  48 83 EC 20  49 8B F8  48 8B F2  48 8B D9  E8 ?? ?? ?? ??
        48 8B 8B 28 08 00 00  48 85 C9  74 05''',
    # model slot 0x390: order -> move request
    'order_390': '''| 48 89 5C 24 08  48 89 74 24 10  57  48 81 EC E0 00 00 00  48 8B FA  48 8B D9  E8 ?? ?? ?? ??
        84 C0  0F 84 D6 00 00 00''',
    # the formation routine (entry) and one model in its loop (before `lea rdx, [rbp+0xa0] / mov rcx, r13 / call`)
    'formation': '''| 48 8B C4  88 50 10  55 53 56 57  41 54 41 55 41 56 41 57  48 8D A8 48 FE FF FF  48 81 EC 78 02 00 00
        8B 51 48  48 8B F9  48 8B 49 08  45 33 C0''',
    # the morale block of the Unit object (leadership, run 48): the natives is_routing (`add rcx, B`) and is_wavering
    # (`cmp dword [rcx+S], 5`); the mark is the 4-byte displacement itself, read at load (so these are `??` data, and
    # the check S == B + 0x40 tells whether it is the analysed block). Current leadership = int at Unit* + B + 0x48.
    'is_routing': '''48 85 C0 74 1F 48 8B 4F 08 48 81 C1 | ?? ?? ?? ??  E8 ?? ?? ?? ??  8A D0 48 8B CB  E8 ?? ?? ?? ??
        B8 01 00 00 00 48 8B 5C 24 30''',
    'is_wavering': '''48 85 C0 74 1B 48 8B 4F 08 83 B9 | ?? ?? ?? ??  05 48 8B C8 0F 94 C2  E8 ?? ?? ?? ??  B8 01 00 00 00''',
    'form_loop': '''4C 03 AD 90 00 00 00  48 8B 48 28  8B 47 5C  89 44 24 68  48 8B 1C D1 |
        48 8D 95 A0 00 00 00  49 8B CD  E8 ?? ?? ?? ??  4C 8D 4D A8  4C 8B C3  48 8D 55 80  48 8B CF''',
}
CALL = {'start_move'}  # the site is the target of the call at the mark


def find(x, name):
    """RVA of a site in the installed exe; fails unless its pattern matches exactly once."""
    pattern, mark = parse(PATTERNS[name])
    hits = x.find(pattern, 2)
    assert len(hits) == 1, '%s: %d matches in the installed exe, expected 1' % (name, len(hits))
    at = hits[0] + (mark or 0)
    if name in CALL:
        assert x.read(at, 1) == b'\xE8', name
        at += 5 + struct.unpack('<i', bytes(x.read(at + 1, 4)))[0]
    return at


def call_target(x, rva):
    """Target of the E8 call at rva."""
    assert x.read(rva, 1) == b'\xE8', hex(rva)
    return rva + 5 + struct.unpack('<i', bytes(x.read(rva + 1, 4)))[0]


def expect_bytes(x, at):
    """The bytes a battle script compares (read_original) at a site: whole instructions from the site up to the first
    call, jump, return or rip-relative one (those hold addresses that move between builds), at most 32 bytes.
    At least 5, so they cover what a cave replaces."""
    out = b''
    for i in x.cs.disasm(bytes(x.read(at, 48)), at):
        if i.mnemonic.startswith('j') or i.mnemonic in ('call', 'ret') or 'rip' in i.op_str or len(out) + i.size > 32:
            break
        out += bytes(i.bytes)
    assert len(out) >= 5, hex(at)
    return out


def lua_bytes(b):
    return ''.join('\\%03d' % v for v in b)


def lua_site(x, name, **extra):
    """One Lua table { name, pattern, mark, call, expect, <extra> } for find_site() of sites.inc.lua; extra values
    are bytes (written as Lua strings)."""
    pattern, mark = parse(PATTERNS[name])
    fields = "name = '%s', pattern = '%s', mark = %d, call = %s, expect = '%s'" % (
        name, pattern, mark or 0, 'true' if name in CALL else 'false', lua_bytes(expect_bytes(x, find(x, name))))
    for key, value in extra.items():
        fields += ", %s = '%s'" % (key, lua_bytes(value))
    return '{ %s }' % fields


def lua_find_site():
    """The Lua function find_site(mr, site) (sites.inc.lua), the same in every script of this project."""
    return open(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'sites.inc.lua'), encoding='utf8').read().rstrip('\n')


class View:
    """The instructions of a routine found by pattern, addressed as in the build the analysis was made on
    (2026-10-12), so the checks keep the addresses of the old listings and notes. Jump targets inside the routine and
    calls to the routines named in `names` ({new rva: old rva}) are shown with their old addresses too."""

    def __init__(self, x, rva, old_start, old_end=None, names=None):
        f = x.func(rva)
        assert f, hex(rva)
        if old_end is not None:
            assert f[1] - f[0] == old_end - old_start, 'the routine at %x changed its size' % f[0]
        self.start, self.end, self.delta = f[0], f[1], f[0] - old_start
        self.text = {}
        for i in x.cs.disasm(bytes(x.read(f[0], f[1] - f[0])), f[0]):
            ops = i.op_str
            if i.mnemonic.startswith('j') or i.mnemonic == 'call':
                if re.fullmatch(r'0x[0-9a-f]+', ops):
                    t = int(ops, 16)
                    if f[0] <= t < f[1]:
                        ops = hex(t - self.delta)
                    elif names and t in names:
                        ops = hex(names[t])
            self.text[i.address - self.delta] = ('%s %s' % (i.mnemonic, ops)).strip()

    def __getitem__(self, old):
        return self.text[old]

    def new(self, old):
        return old + self.delta

    def items(self):
        return self.text.items()


if __name__ == '__main__':
    x = Exe()
    print('%s (%d bytes)' % (x.path, os.path.getsize(x.path)))
    for name in PATTERNS:
        at = find(x, name)
        f = x.func(at)
        print('  %-12s %x  in function %x-%x' % (name, at, f[0], f[1]))
