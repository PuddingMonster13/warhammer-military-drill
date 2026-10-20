"""Units Pass Through - the mod, TEST BUILD.

    python mod.py check            offline checks: site bytes in the installed exe, caves decode, caves run natively
    python mod.py build <out_dir>  the same checks, then writes !units_pass_through_debug.pack and the script

Option B "give way": when a model walks into a model of ANOTHER FRIENDLY UNIT that is standing idle,
  - the standing model takes (nearly) the whole push (cave 'pair_push'),
  - the walker is not marked "blocked" (caves 'blocked_a_by_b' / 'blocked_b_by_a'),
  - the standing models next to the walker play a STEP animation away from it: the cave 'pair_entry' (start of
    the routine the game runs for every two models that touch) calls the game's own routine that makes the
    neighbours of a model flinch, and the cave 'step_pick' inside that routine lets only models of units that
    give way react and swaps the flinch animation for the step animation.
    (0.2 made that call from the "blocked" caves; run 2 showed those places are reached far too rarely.)
The battle script keeps the list of units that give way; the caves only compare Unit pointers with that list.
"""
import ctypes
import os
import re
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import probe  # Asm, site checks, pack writer (shared with the probe)
from probe import Asm, d32, HEAD, EXIT_MARK

VERSION = '0.3'
PACK = '!units_pass_through_debug.pack'
SCRIPT = 'units_pass_through.lua'
BATTLE_PATH = 'script/battle/mod/' + SCRIPT

# first values (the script writes them into the table)
K_SMALL_VALUE = 0.02  # mass ratio used when the other model gives way
K_BIG_VALUE = 50.0  # mass ratio used when this model gives way
MAX_K_VALUE = 6.0  # a model does not shove aside a model more than this many times its own mass
CHANCE_VALUE = 0.3  # chance per standing neighbour and call to start its step animation (0 = no animation)
STEP_MASK_VALUE = 3  # the neighbours are looked at for every 4th touching pair only (must be 2^n - 1)

# the game's routine "the neighbours of this model flinch" and what it is expected to look like
FLINCH = 0x307da70  # (rcx = model, xmm1 = chance per neighbour)
FLINCH_FIRST, FLINCH_COUNT = 0x4df, 8  # FLINCH_STAND_LEFT / RIGHT / BACK / FORWARD, FLINCH_CR_LEFT / RIGHT / BACK / FORWARD
# what each of them becomes: STEP_LEFT / RIGHT / BACKWARD / FORWARD, COMBAT_READY_STEP_LEFT / RIGHT / BACK / FORWARD
STEP_SLOTS = [0x47, 0x48, 0x46, 0x45, 0x3a2, 0x3a3, 0x3a1, 0x3a0]

# entity fields; the same fields seen from a collision block (= entity + 0x68)
ALLIANCE, UNIT, MASS, INV_MASS = 0x80, 0x2e8, 0xb4, 0xb8
BLOCK_UNIT = UNIT - 0x68

# table (game_alloc, zeroed)
ENABLE = 0x00
K_SMALL, K_BIG, MAX_K, MIN_K = 0x08, 0x0c, 0x10, 0x14
STEP_ON = 0x18  # 1 while the cave 'pair_entry' is inside the flinch routine
CHANCE = 0x1c
C_B_GIVES, C_A_GIVES, C_FREE_1, C_FREE_2, C_STEPS = 0x20, 0x24, 0x28, 0x2c, 0x30
C_TOUCH = 0x34  # touching friendly pairs where exactly one unit gives way
STEP_MASK = 0x38
LIST = 0x40  # Unit pointers that give way, ended by 0
MAX_UNITS = 200
REMAP = 0x700  # FLINCH_COUNT dwords: the animation that replaces each flinch animation
TABLE_SIZE = 0x800
assert LIST + 8 * (MAX_UNITS + 1) <= REMAP and REMAP + 4 * FLINCH_COUNT <= TABLE_SIZE

CALL_MARK = b'\xC1\xC2\xC3\xC4\xC5\xC6\xC7\xC8'  # replaced by the battle script with the address of FLINCH
TABLE_MARK = b'\xD1\xD2\xD3\xD4\xD5\xD6\xD7\xD8'  # replaced by the battle script with the table address
RM = {'rbx': 3, 'rsi': 6, 'rdi': 7}


class Cave(Asm):
    def far(self, opcode, label):  # 0x84 je, 0x85 jne (rel32): for jumps over more than 127 bytes
        self.emit(bytes([0x0F, opcode]) + bytes(4))
        self.far_fix = getattr(self, 'far_fix', []) + [(len(self.code) - 4, label)]

    def done(self):
        for pos, label in getattr(self, 'far_fix', []):
            self.code[pos:pos + 4] = d32(self.labels[label] - (pos + 4))
        return Asm.done(self)


def lookup(a):
    """rcx = Unit of the first model, rdx = Unit of the second (different). Leaves r8d = 1 when only the first is
    in the list, 2 when only the second, 0 or 3 otherwise. r8 is pushed here and must be popped by the caller.
    Uses r10, r11; rax stays the table."""
    a.emit(b'\x41\x50')  # push r8
    a.emit(b'\x45\x31\xC0')  # xor r8d, r8d
    a.emit(b'\x4C\x8D\x90' + d32(LIST))  # lea r10, [rax+LIST]
    a.label('loop')
    a.emit(b'\x4D\x8B\x1A')  # mov r11, [r10]
    a.emit(b'\x4D\x85\xDB')  # test r11, r11
    a.jump(0x74, 'end')
    a.emit(b'\x49\x39\xCB')  # cmp r11, rcx
    a.jump(0x75, 'n1')
    a.emit(b'\x41\x83\xC8\x01')  # or r8d, 1
    a.label('n1')
    a.emit(b'\x49\x39\xD3')  # cmp r11, rdx
    a.jump(0x75, 'n2')
    a.emit(b'\x41\x83\xC8\x02')  # or r8d, 2
    a.label('n2')
    a.emit(b'\x49\x83\xC2\x08')  # add r10, 8
    a.jump(0xEB, 'loop')
    a.label('end')


def cave_push():
    """Friend branch of the push routine, in front of the kind rules. r14 = collision block of A, rbx = of B,
    xmm2 = B.mass / A.mass. Free: rax, rcx, rdx, r10, r11, flags (r8b and r9b are in use: r8 is saved)."""
    a = Cave()
    a.emit(b'\x83\xB8' + d32(ENABLE) + b'\x00')  # cmp dword [rax+ENABLE], 0
    a.far(0x84, 'done')
    a.emit(b'\x49\x8B\x8E' + d32(BLOCK_UNIT))  # mov rcx, [r14+0x280]     A's unit
    a.emit(b'\x48\x8B\x93' + d32(BLOCK_UNIT))  # mov rdx, [rbx+0x280]     B's unit
    a.emit(b'\x48\x39\xD1')  # cmp rcx, rdx
    a.far(0x84, 'done')
    lookup(a)
    a.emit(b'\x41\x83\xF8\x02')  # cmp r8d, 2                             only B's unit gives way
    a.jump(0x75, 'try_a')
    a.emit(b'\x0F\x2F\x90' + d32(MAX_K))  # comiss xmm2, [rax+MAX_K]
    a.jump(0x77, 'out')  # ja: B is too heavy to be shoved aside
    a.emit(b'\xF3\x0F\x5D\x90' + d32(K_SMALL))  # minss xmm2, [rax+K_SMALL]
    a.lock_inc(C_B_GIVES)
    a.jump(0xEB, 'leave')
    a.label('try_a')
    a.emit(b'\x41\x83\xF8\x01')  # cmp r8d, 1                             only A's unit gives way
    a.jump(0x75, 'out')
    a.emit(b'\x0F\x2F\x90' + d32(MIN_K))  # comiss xmm2, [rax+MIN_K]
    a.jump(0x72, 'out')  # jb: A is too heavy to be shoved aside
    a.emit(b'\xF3\x0F\x5F\x90' + d32(K_BIG))  # maxss xmm2, [rax+K_BIG]
    a.lock_inc(C_A_GIVES)
    a.label('leave')
    a.emit(b'\x41\x58')  # pop r8
    a.leave()  # past the kind rules
    a.label('out')
    a.emit(b'\x41\x58')  # pop r8
    a.label('done')
    return a.done()


def cave_entry():
    """First instruction of the pair routine: rcx and rdx = the two models that touch. Free: rax, r8-r11, xmm0-5,
    flags; rcx and rdx must come out unchanged. When they are friends of different units and exactly one unit
    gives way, the other model is the walker: every (mask + 1)th time the flinch routine is called for it."""
    a = Cave()
    a.emit(b'\x83\xB8' + d32(ENABLE) + b'\x00')  # cmp dword [rax+ENABLE], 0
    a.far(0x84, 'done')
    a.emit(b'\x83\xB8' + d32(CHANCE) + b'\x00')  # cmp dword [rax+CHANCE], 0     animation switched off?
    a.far(0x84, 'done')
    a.emit(b'\x4C\x8B\x89' + d32(ALLIANCE))  # mov r9, [rcx+0x80]
    a.emit(b'\x4C\x3B\x8A' + d32(ALLIANCE))  # cmp r9, [rdx+0x80]
    a.far(0x85, 'done')  # not friends
    a.emit(b'\x48\x83\xEC\x38')  # sub rsp, 0x38      room for a called routine, the stack stays aligned
    a.emit(b'\x48\x89\x4C\x24\x20')  # mov [rsp+0x20], rcx
    a.emit(b'\x48\x89\x54\x24\x28')  # mov [rsp+0x28], rdx
    a.emit(b'\x48\x8B\x89' + d32(UNIT))  # mov rcx, [rcx+0x2e8]
    a.emit(b'\x48\x8B\x92' + d32(UNIT))  # mov rdx, [rdx+0x2e8]
    a.emit(b'\x48\x39\xD1')  # cmp rcx, rdx
    a.far(0x84, 'restore')
    lookup(a)
    a.emit(b'\x45\x89\xC1')  # mov r9d, r8d
    a.emit(b'\x41\x58')  # pop r8
    a.emit(b'\x41\x83\xF9\x01')  # cmp r9d, 1          only the first model's unit gives way
    a.jump(0x74, 'second_walks')
    a.emit(b'\x41\x83\xF9\x02')  # cmp r9d, 2          only the second model's unit gives way
    a.jump(0x75, 'restore')
    a.emit(b'\x48\x8B\x4C\x24\x20')  # mov rcx, [rsp+0x20]     the first model is the walker
    a.jump(0xEB, 'go')
    a.label('second_walks')
    a.emit(b'\x48\x8B\x4C\x24\x28')  # mov rcx, [rsp+0x28]     the second model is the walker
    a.label('go')
    a.lock_inc(C_TOUCH)
    a.emit(b'\x44\x8B\x88' + d32(C_TOUCH))  # mov r9d, [rax+C_TOUCH]
    a.emit(b'\x44\x23\x88' + d32(STEP_MASK))  # and r9d, [rax+STEP_MASK]
    a.jump(0x75, 'restore')
    a.emit(b'\xC7\x80' + d32(STEP_ON) + d32(1))  # mov dword [rax+STEP_ON], 1
    a.emit(b'\xF3\x0F\x10\x88' + d32(CHANCE))  # movss xmm1, [rax+CHANCE]
    a.emit(b'\x48\xB8' + CALL_MARK + b'\xFF\xD0')  # mov rax, FLINCH ; call rax
    a.emit(b'\x48\xB8' + TABLE_MARK)  # mov rax, TABLE
    a.emit(b'\xC7\x80' + d32(STEP_ON) + d32(0))  # mov dword [rax+STEP_ON], 0
    a.label('restore')
    a.emit(b'\x48\x8B\x4C\x24\x20')  # mov rcx, [rsp+0x20]
    a.emit(b'\x48\x8B\x54\x24\x28')  # mov rdx, [rsp+0x28]
    a.emit(b'\x48\x83\xC4\x38')  # add rsp, 0x38
    a.label('done')
    return a.done()


def cave_free(counter, mover, blocker):
    """Just before 'model <mover> is blocked by model <blocker>' (entities), in the pair routine.
    Free: rax, rcx, rdx, r8-r11, xmm0, flags."""
    a = Cave()
    a.emit(b'\x83\xB8' + d32(ENABLE) + b'\x00')  # cmp dword [rax+ENABLE], 0
    a.far(0x84, 'done')
    a.emit(b'\x48\x8B' + bytes([0x90 | RM[blocker]]) + d32(ALLIANCE))  # mov rdx, [blocker+0x80]
    a.emit(b'\x48\x3B' + bytes([0x90 | RM[mover]]) + d32(ALLIANCE))  # cmp rdx, [mover+0x80]
    a.far(0x85, 'done')  # not friends
    a.emit(b'\x48\x8B' + bytes([0x88 | RM[mover]]) + d32(UNIT))  # mov rcx, [mover+0x2e8]
    a.emit(b'\x48\x8B' + bytes([0x90 | RM[blocker]]) + d32(UNIT))  # mov rdx, [blocker+0x2e8]
    a.emit(b'\x48\x39\xD1')  # cmp rcx, rdx
    a.far(0x84, 'done')
    lookup(a)
    a.emit(b'\x41\x83\xF8\x02')  # cmp r8d, 2                             only the blocker's unit gives way
    a.jump(0x75, 'out')
    a.emit(b'\xF3\x0F\x10' + bytes([0x80 | RM[blocker]]) + d32(MASS))  # movss xmm0, [blocker+0xb4]
    a.emit(b'\xF3\x0F\x59' + bytes([0x80 | RM[mover]]) + d32(INV_MASS))  # mulss xmm0, [mover+0xb8]
    a.emit(b'\x0F\x2F\x80' + d32(MAX_K))  # comiss xmm0, [rax+MAX_K]
    a.jump(0x77, 'out')  # ja: the blocker is too heavy
    a.lock_inc(counter)
    a.emit(b'\x41\x58')  # pop r8
    a.leave()  # the routine's own "not blocked" path
    a.label('out')
    a.emit(b'\x41\x58')  # pop r8
    a.label('done')
    return a.done()


def cave_step():
    """Inside the flinch routine, where the animation for one neighbour is chosen: rdi = the model in the middle,
    rbx = the neighbour, r9d = the flinch animation. Free: rax, rcx, rdx, r8, r10, r11, flags.
    Only while one of our caves made the call: neighbours of units that do not give way are skipped, the others
    get the step animation instead."""
    a = Cave()
    a.emit(b'\x83\xB8' + d32(STEP_ON) + b'\x00')  # cmp dword [rax+STEP_ON], 0
    a.far(0x84, 'done')  # the game's own call: nothing changes
    a.emit(b'\x48\x8B\x8F' + d32(UNIT))  # mov rcx, [rdi+0x2e8]           the walker's unit
    a.emit(b'\x48\x8B\x93' + d32(UNIT))  # mov rdx, [rbx+0x2e8]           the neighbour's unit
    a.emit(b'\x48\x39\xD1')  # cmp rcx, rdx
    a.jump(0x74, 'skip')
    lookup(a)
    a.emit(b'\x41\x83\xF8\x02')  # cmp r8d, 2                             only the neighbour's unit gives way
    a.emit(b'\x41\x58')  # pop r8 (the flags stay)
    a.jump(0x75, 'skip')
    a.emit(b'\xF3\x0F\x10\x83' + d32(MASS))  # movss xmm0, [rbx+0xb4]     the neighbour's mass
    a.emit(b'\xF3\x0F\x59\x87' + d32(INV_MASS))  # mulss xmm0, [rdi+0xb8]  / the walker's mass
    a.emit(b'\x0F\x2F\x80' + d32(MAX_K))  # comiss xmm0, [rax+MAX_K]
    a.jump(0x77, 'skip')  # ja: too heavy to be shoved aside, so it does not step either
    a.emit(b'\x41\x8D\x89' + d32(-FLINCH_FIRST))  # lea ecx, [r9-0x4df]
    a.emit(b'\x83\xF9' + bytes([FLINCH_COUNT - 1]))  # cmp ecx, 7
    a.jump(0x77, 'skip')  # not one of the flinch animations: no animation at all
    a.emit(b'\x44\x8B\x8C\x88' + d32(REMAP))  # mov r9d, [rax+rcx*4+REMAP]
    a.lock_inc(C_STEPS)
    a.jump(0xEB, 'done')
    a.label('skip')
    a.leave()  # next neighbour
    a.label('done')
    return a.done()


# name, rva of the first replaced instruction, cave code after 'mov rax, TABLE', rva the cave leaves to, kind
SITES = [
    ('pair_push', 0x316b860, cave_push(), 0x316b88f, 'inner'),
    ('blocked_a_by_b', 0x3066ee3, cave_free(C_FREE_1, 'rdi', 'rsi'), 0x3066ef6, 'inner'),
    ('blocked_b_by_a', 0x3066f63, cave_free(C_FREE_2, 'rsi', 'rdi'), 0x306703d, 'inner'),
    ('step_pick', 0x307de3f, cave_step(), 0x307de7c, 'inner'),
    ('pair_entry', 0x3066c34, cave_entry(), None, 'entry'),
]
EXPECT = {
    'pair_entry': probe.EXPECT['pair_handler'],
    'pair_push': ['mov eax, dword ptr [rbx + 0xc]', 'cmp eax, dword ptr [r14 + 0xc]', 'jge 0x316b87d'],
    'blocked_a_by_b': probe.EXPECT['blocked_a_by_b'],
    'blocked_b_by_a': probe.EXPECT['blocked_b_by_a'],
    'step_pick': ['mov rcx, qword ptr [rdi + 0x308]', 'movzx edx, r9w', 'call 0x1482074', 'cmp al, 1', 'jne 0x307de7c'],
}
# bytes outside the sites that must be the analysed ones too: the start of the routine the caves call
EXTRA = [('flinch_routine', FLINCH)]
ALLOWED = ('movabs', 'lock inc', 'push', 'pop', 'mov', 'cmp', 'je', 'jne', 'jmp', 'ja', 'jb', 'xor', 'lea', 'test', 'or',
           'add', 'sub', 'and', 'comiss', 'minss', 'maxss', 'movss', 'mulss', 'call')
# pushes and pops of r8 per cave (one pop on each way out that comes after the push)
STACK = {'pair_push': (1, 2), 'blocked_a_by_b': (1, 2), 'blocked_b_by_a': (1, 2), 'step_pick': (1, 1), 'pair_entry': (1, 1)}


def listing(x, rva):
    f = x.func(rva)
    return f, {i.address: '%s %s' % (i.mnemonic, i.op_str) for i in x.cs.disasm(x.read(f[0], f[1] - f[0]), f[0])}


def signatures(x):
    sigs = probe.signatures(x, SITES, EXPECT)
    # what the analysis of the push routine relies on, checked against the exe
    f, text = listing(x, 0x316b860)
    assert f == (0x316afd4, 0x316bdcc), f
    assert text[0x316b6cd] == 'movss xmm2, dword ptr [r14 + 0x50]' and text[0x316b6d6] == 'mulss xmm2, dword ptr [rbx + 0x4c]'
    assert text[0x316b705] == 'cmp r9b, 1' and text[0x316b709] == 'jne 0x316b860'
    assert text[0x316b88f] == 'xorps xmm9, xmm9' and text[0x316b893].startswith('comiss xmm8, dword ptr [rip')
    assert text[0x316b927] == 'test r8b, r8b' and text[0x316bacf] == 'test r9b, r9b'
    assert text[0x316b8ea] == 'mov ecx, dword ptr [r14 + 8]' and text[0x316b8ee] == 'mov edx, dword ptr [rbx + 0xc]'
    # the pair routine: both "blocked" places are followed by code that reloads every register a call may change
    f, text = listing(x, 0x3066ee3)
    assert f[0] == 0x3066c34 and text[0x3066ef6] == 'cmp bpl, r15b' and text[0x306703d] == 'cmp bl, r15b'
    assert text[0x3066c4c] == 'sub rsp, 0x40', 'the stack frame the call from the caves relies on'
    # the flinch routine: arguments, the eight animations in the analysed order, the neighbour loop
    f, text = listing(x, FLINCH)
    assert f == (FLINCH, 0x307dec4), f
    assert text[0x307da8a] == 'mov rdi, rcx' and text[0x307da9b] == 'movaps xmm12, xmm1'
    assert text[0x307db62] == 'mov ecx, esi' and text[0x307db64] == 'mov rbx, qword ptr [rax + rcx*8]'
    picks = {0x307de39: 0x4e1, 0x307de31: 0x4e0, 0x307de29: 0x4df, 0x307ddee: 0x4e2,
             0x307de21: 0x4e5, 0x307de19: 0x4e4, 0x307de11: 0x4e3, 0x307de09: 0x4e6}
    for at, slot in picks.items():
        assert text[at] == 'mov r9d, 0x%x' % slot, (hex(at), text[at])
    assert sorted(picks.values()) == list(range(FLINCH_FIRST, FLINCH_FIRST + FLINCH_COUNT))
    assert text[0x307de6a] == 'movzx edx, r9w' and text[0x307de77] == 'call 0x307ccf0' and text[0x307de7c] == 'inc esi'
    # the animation names behind the numbers (table of 0x20-byte records: id, name pointer, ...)
    base = 0x36d4560 - 0x654 * 0x20

    def slot_name(slot):
        idv, namep = struct.unpack('<QQ', x.read(base + slot * 0x20, 16))
        assert idv == slot
        return x.cstr(namep - x.base, 80)

    want = [('FLINCH_STAND_LEFT', 'STEP_LEFT'), ('FLINCH_STAND_RIGHT', 'STEP_RIGHT'), ('FLINCH_STAND_BACK', 'STEP_BACKWARD'),
            ('FLINCH_STAND_FORWARD', 'STEP_FORWARD'), ('FLINCH_CR_LEFT', 'COMBAT_READY_STEP_LEFT'),
            ('FLINCH_CR_RIGHT', 'COMBAT_READY_STEP_RIGHT'), ('FLINCH_CR_BACK', 'COMBAT_READY_STEP_BACK'),
            ('FLINCH_CR_FORWARD', 'COMBAT_READY_STEP_FORWARD')]
    for n, (flinch, step) in enumerate(want):
        assert slot_name(FLINCH_FIRST + n) == flinch and slot_name(STEP_SLOTS[n]) == step, (n, slot_name(FLINCH_FIRST + n), slot_name(STEP_SLOTS[n]))
    extra = [bytes(x.read(rva, probe.SIG_LEN)) for name, rva in EXTRA]
    return sigs, extra


def check_decode(x):
    for name, rva, tail, leave, kind in SITES:
        calls = name == 'pair_entry'
        assert tail.count(EXIT_MARK) == (0 if leave is None else 1), name
        assert tail.count(CALL_MARK) == tail.count(TABLE_MARK) == (1 if calls else 0), name
        code = HEAD + b'\0' * 8 + tail
        # r8 is pushed once and popped on each way out; the frame of 'pair_entry' is opened and closed once;
        # nothing of r8 is on the stack when a cave calls or leaves
        end = depth = pushes = pops = frame = 0
        for ins in x.cs.disasm(code, 0):
            end = ins.address + ins.size
            if ins.mnemonic in ('push', 'pop'):
                assert ins.op_str == 'r8', ins.op_str
                pushes += ins.mnemonic == 'push'
                pops += ins.mnemonic == 'pop'
                depth = 1 if ins.mnemonic == 'push' else 0
            assert ins.mnemonic in ALLOWED, (name, ins.mnemonic)
            if ins.mnemonic in ('sub', 'add') and ins.op_str.startswith('rsp'):
                assert calls and ins.op_str == 'rsp, 0x38' and depth == 0
                frame += 1 if ins.mnemonic == 'sub' else -1
                assert frame in (0, 1)
            if ins.mnemonic == 'call':
                assert calls and ins.op_str == 'rax' and depth == 0 and frame == 1
            if ins.mnemonic.startswith('j') and ins.op_str != 'rax':
                assert 0 <= int(ins.op_str, 16) <= len(code), 'jump out of the cave'
            if ins.mnemonic == 'jmp' and ins.op_str == 'rax':
                assert depth == 0 and not calls, 'leaves with something on the stack'
            dest = ins.op_str.split(',')[0]
            if ins.mnemonic in ('mov', 'movabs', 'xor', 'or', 'and', 'add', 'sub', 'lea', 'minss', 'maxss', 'movss', 'mulss') and '[' not in dest:
                assert dest in ('rax', 'rcx', 'ecx', 'rdx', 'rsp', 'r9', 'r10', 'r11', 'r8d', 'r9d', 'xmm0', 'xmm1', 'xmm2'), (name, ins.op_str)
                if dest == 'r8d':
                    assert depth == 1, 'r8 changed outside its push / pop'
                assert dest != 'xmm2' or name == 'pair_push'
                assert dest not in ('r9', 'r9d') or name in ('step_pick', 'pair_entry')
                assert dest != 'xmm1' or calls
                assert dest not in ('rcx', 'rdx') or not calls or frame == 1, 'the arguments are changed outside the frame'
            if '[' in dest:
                assert ins.mnemonic in ('lock inc', 'cmp', 'comiss', 'mov'), 'memory write: ' + ins.op_str
                if ins.mnemonic == 'lock inc':
                    assert dest.split('[')[1].startswith('rax'), 'write outside the table: ' + ins.op_str
                if ins.mnemonic == 'mov':
                    assert calls and dest in ('dword ptr [rax + 0x%x]' % STEP_ON, 'qword ptr [rsp + 0x20]', 'qword ptr [rsp + 0x28]'), ins.op_str
        assert end == len(code), '%s: cave does not decode to its end' % name
        assert depth == 0 and frame == 0 and (pushes, pops) == STACK[name], (name, pushes, pops)
    print('caves decode: %s bytes' % [len(HEAD) + 8 + len(t) for n, r, t, l, k in SITES])


def float_bits(v):
    return struct.unpack('<I', struct.pack('<f', v))[0]


def native_test():
    """Runs every cave inside this Python process with fake models and checks the table, the path and the values."""
    k32 = ctypes.windll.kernel32
    k32.VirtualAlloc.restype = ctypes.c_void_p
    k32.VirtualAlloc.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_uint32, ctypes.c_uint32]
    table = (ctypes.c_ubyte * TABLE_SIZE)()
    tbl = ctypes.addressof(table)
    c = lambda off: struct.unpack_from('<I', table, off)[0]
    A = ctypes.addressof
    checks = 0
    U1, U2, U3 = 0x7001000, 0x7002000, 0x7003000
    # what the fake flinch routine was called with: model, chance bits, STEP_ON, number of calls, stack pointer
    seen = (ctypes.c_uint64 * 5)()

    def model(alliance, unit, mass):
        m = (ctypes.c_ubyte * 0x400)()
        struct.pack_into('<Q', m, ALLIANCE, alliance)
        struct.pack_into('<Q', m, UNIT, unit)
        struct.pack_into('<ff', m, MASS, mass, 1.0 / mass)
        return m

    def fill(enable, units, chance=CHANCE_VALUE, step_on=0, mask=0, touched=0):
        ctypes.memset(tbl, 0, TABLE_SIZE)
        struct.pack_into('<I', table, ENABLE, enable)
        struct.pack_into('<4f', table, K_SMALL, K_SMALL_VALUE, K_BIG_VALUE, MAX_K_VALUE, 1.0 / MAX_K_VALUE)
        struct.pack_into('<If', table, STEP_ON, step_on, chance)
        struct.pack_into('<II', table, C_TOUCH, touched, mask)
        struct.pack_into('<%dQ' % (len(units) + 1), table, LIST, *units, 0)
        struct.pack_into('<%dI' % FLINCH_COUNT, table, REMAP, *STEP_SLOTS)
        for i in range(5):
            seen[i] = 0

    def alloc(code):
        mem = k32.VirtualAlloc(None, 0x1000, 0x3000, 0x40)
        assert mem
        ctypes.memmove(mem, code, len(code))
        return mem

    q = lambda v: struct.pack('<Q', v)
    # fake flinch routine: notes rcx, xmm1, the STEP_ON value, its stack pointer and counts the call, writes into
    # the room a called routine may use above its return address, then destroys every register a real routine may
    fake = alloc(bytes.fromhex('4889c8') + b'\x48\xA3' + q(A(seen)) +  # mov rax, rcx ; mov [seen+0], rax
                 bytes.fromhex('66480f7ec8') + b'\x48\xA3' + q(A(seen) + 8) +  # movq rax, xmm1 ; mov [seen+8], rax
                 b'\x48\xA1' + q(tbl + STEP_ON) + b'\x48\xA3' + q(A(seen) + 16) +  # mov rax, [STEP_ON] ; mov [seen+16], rax
                 b'\x48\xA1' + q(A(seen) + 24) + bytes.fromhex('48ffc0') + b'\x48\xA3' + q(A(seen) + 24) +  # calls += 1
                 bytes.fromhex('4889e0') + b'\x48\xA3' + q(A(seen) + 32) +  # mov rax, rsp ; mov [seen+32], rax
                 bytes.fromhex('48c7442408ffffffff' '48c7442410ffffffff' '48c7442418ffffffff' '48c7442420ffffffff') +
                 bytes.fromhex('31c9' '31d2' '4531c0' '4531c9' '4531d2' '4531db' '0f57c0' '0f57c9' '0f57d2' '31c0' 'c3'))

    def build(tail, kind):
        if kind == 'push':
            # f(rcx = A block, rdx = B block, r8 = float* k): r14 = A, rbx = B, xmm2 = *k, r8 = r9 = marker values;
            # afterwards *k = xmm2; returns 1 (cave ran to its end) or 2 (left), plus 0x10 / 0x20 when r8 / r9 changed
            pre = bytes.fromhex('4156' '53' '4989ce' '4889d3' 'f3410f1010' '4150'
                                '49b8' '1122334455667788' '49b9' '8877665544332211')
            post = bytes.fromhex('b801000000' 'eb05' 'b802000000'
                                 '49ba' '1122334455667788' '4d39d0' '7403' '83c810'
                                 '49ba' '8877665544332211' '4d39d1' '7403' '83c820'
                                 '4158' 'f3410f1110' '5b' '415e' 'c3')
        elif kind == 'free':
            # f(rcx -> rdi, rdx -> rsi): returns 1 or 2 as above
            pre = bytes.fromhex('57' '56' '4889cf' '4889d6')
            post = bytes.fromhex('b801000000' 'eb05' 'b802000000' '5e' '5f' 'c3')
        elif kind == 'step':
            # f(rcx -> rdi, rdx -> rbx, r8 -> r9): returns the path (1 / 2) * 0x10000 + r9d
            pre = bytes.fromhex('57' '53' '4889cf' '4889d3' '4d89c1')
            post = bytes.fromhex('b801000000' 'eb05' 'b802000000' 'c1e010' '4409c8' '5b' '5f' 'c3')
        else:
            # the cave as the first thing of a routine f(rcx, rdx): returns rcx ^ rdx as they are after the cave
            pre = b''
            post = bytes.fromhex('4889c8' '4831d0' 'c3')
        body = HEAD + q(tbl) + tail
        mem = alloc(b'\0')
        leave_at = mem + len(pre) + len(body) + 7
        stub = pre + body.replace(EXIT_MARK, q(leave_at)).replace(CALL_MARK, q(fake)).replace(TABLE_MARK, q(tbl)) + post
        ctypes.memmove(mem, stub, len(stub))
        return ctypes.CFUNCTYPE(ctypes.c_uint64, ctypes.c_uint64, ctypes.c_uint64, ctypes.c_void_p)(mem)

    near = lambda x, y: abs(x - y) < 1e-6 * max(1.0, abs(y))
    for name, rva, tail, leave, kind in SITES:
        if name == 'pair_push':
            run = build(tail, 'push')

            def push(enable, units, unit_a, unit_b, k):
                fill(enable, units)
                a, b = model(0x10, unit_a, 100.0), model(0x10, unit_b, 100.0)
                value = ctypes.c_float(k)
                got = run(A(a) + 0x68, A(b) + 0x68, ctypes.addressof(value))  # the routine works on collision blocks
                assert got in (1, 2), 'r8 / r9 changed: %x' % got
                return got, value.value, c(C_B_GIVES), c(C_A_GIVES)

            cases = [
                # enable, list, A's unit, B's unit, k in -> path, k out, counted B gives, counted A gives
                (1, [U2], U1, U2, 1.0, 2, K_SMALL_VALUE, 1, 0),  # B stands, A walks: B gives way
                (1, [U3, U2], U1, U2, 1.0, 2, K_SMALL_VALUE, 1, 0),  # ... not the first unit of the list
                (1, [U2], U1, U2, 0.01, 2, 0.01, 1, 0),  # B is much lighter anyway: k is not raised
                (1, [U2], U1, U2, 6.0, 2, K_SMALL_VALUE, 1, 0),  # B six times heavier: still shoved
                (1, [U2], U1, U2, 6.5, 1, 6.5, 0, 0),  # B too heavy: the game's own rule
                (1, [U1], U1, U2, 1.0, 2, K_BIG_VALUE, 0, 1),  # A stands, B walks: A gives way
                (1, [U1], U1, U2, 80.0, 2, 80.0, 0, 1),  # A much lighter anyway
                (1, [U1], U1, U2, 0.1, 1, 0.1, 0, 0),  # A too heavy (1 / 0.1 = 10 > 6)
                (1, [U1, U2], U1, U2, 1.0, 1, 1.0, 0, 0),  # both stand: the game's own rule
                (1, [U3], U1, U2, 1.0, 1, 1.0, 0, 0),  # both walk
                (1, [], U1, U2, 1.0, 1, 1.0, 0, 0),  # empty list
                (1, [U1], U1, U1, 1.0, 1, 1.0, 0, 0),  # same unit
                (1, [U1], 0, 0, 1.0, 1, 1.0, 0, 0),  # models without a unit
                (1, [U2], 0, U2, 1.0, 2, K_SMALL_VALUE, 1, 0),  # a model without a unit walks into a standing unit
                (0, [U2], U1, U2, 1.0, 1, 1.0, 0, 0),  # mod switched off
            ]
            for enable, units, ua, ub, k, path, k_out, nb, na in cases:
                got = push(enable, units, ua, ub, k)
                assert got[0] == path and near(got[1], k_out) and got[2:] == (nb, na), ((enable, units, ua, ub, k), got)
                assert seen[3] == 0 and c(STEP_ON) == 0
                checks += 5
            got = push(1, [U3 + i for i in range(MAX_UNITS - 1)] + [U2], U1, U2, 1.0)  # a full list
            assert got[0] == 2 and near(got[1], K_SMALL_VALUE)
            checks += 2
        elif name.startswith('blocked_'):
            run = build(tail, 'free')
            counter = C_FREE_1 if name == 'blocked_a_by_b' else C_FREE_2
            walker = model(0x10, U1, 100.0)
            cases = [
                # enable, list, the blocker -> path, counted
                (1, [U2], model(0x10, U2, 100.0), 2, 1),  # the blocker stands and gives way: not blocked
                (1, [U2], model(0x10, U2, 600.0), 2, 1),  # six times heavier: still gives way
                (1, [U2], model(0x10, U2, 650.0), 1, 0),  # too heavy: the game's own rule
                (1, [U2], model(0x20, U2, 100.0), 1, 0),  # an enemy
                (1, [U1], model(0x10, U2, 100.0), 1, 0),  # the walker's own unit is the one that stands
                (1, [U1, U2], model(0x10, U2, 100.0), 1, 0),  # both stand
                (1, [], model(0x10, U2, 100.0), 1, 0),
                (1, [U1], model(0x10, U1, 100.0), 1, 0),  # same unit
                (0, [U2], model(0x10, U2, 100.0), 1, 0),  # mod switched off
            ]
            for enable, units, blocker, path, counted in cases:
                fill(enable, units)
                got = run(A(walker), A(blocker), None) if name == 'blocked_a_by_b' else run(A(blocker), A(walker), None)
                assert (got, c(counter), seen[3]) == (path, counted, 0), (name, enable, units, got, c(counter))
                assert sum(struct.unpack_from('<6I', table, C_B_GIVES)) == counted, 'another counter moved'
                checks += 4
        elif name == 'step_pick':
            run = build(tail, 'step')
            walker, standing, same, other = model(0x10, U1, 100.0), model(0x10, U2, 100.0), model(0x10, U1, 100.0), model(0x10, U3, 100.0)
            heavy, limit = model(0x10, U2, 650.0), model(0x10, U2, 600.0)
            for n in range(FLINCH_COUNT):  # our call, the neighbour's unit gives way: step instead of flinch
                fill(1, [U2], step_on=1)
                assert run(A(walker), A(standing), FLINCH_FIRST + n) == 0x10000 + STEP_SLOTS[n] and c(C_STEPS) == 1
                checks += 2
            fill(1, [U2], step_on=1)
            assert run(A(walker), A(limit), 0x4e1) == 0x10000 + 0x46 and c(C_STEPS) == 1  # six times heavier: steps
            checks += 1
            cases = [
                # our call?, list, neighbour, animation in -> path, animation out
                (0, [U2], standing, 0x4e1, 1, 0x4e1),  # the game's own call: untouched
                (0, [], other, 0x4df, 1, 0x4df),
                (1, [U2], other, 0x4e1, 2, 0x4e1),  # neighbour of a unit that does not give way: skipped
                (1, [U2], same, 0x4e1, 2, 0x4e1),  # neighbour of the walker's own unit: skipped
                (1, [U1, U2], standing, 0x4e1, 2, 0x4e1),  # the walker's unit stands too: skipped
                (1, [U2], heavy, 0x4e1, 2, 0x4e1),  # too heavy for this walker: skipped
                (1, [U2], standing, 0x4de, 2, 0x4de),  # not a flinch animation: skipped
                (1, [U2], standing, 0x4e7, 2, 0x4e7),
            ]
            for step_on, units, neighbour, slot, path, out in cases:
                fill(1, units, step_on=step_on)
                assert run(A(walker), A(neighbour), slot) == path * 0x10000 + out and c(C_STEPS) == 0, (step_on, units, slot)
                checks += 2
        else:
            run = build(tail, 'entry')
            m1, m2, m1b, enemy = model(0x10, U1, 100.0), model(0x10, U2, 100.0), model(0x10, U1, 100.0), model(0x20, U2, 100.0)
            cases = [
                # enable, list, chance, mask, pairs seen before, first, second -> counted, the walker handed over
                (1, [U2], CHANCE_VALUE, 0, 0, m1, m2, 1, m1),  # the second stands: the first is the walker
                (1, [U1], CHANCE_VALUE, 0, 0, m1, m2, 1, m2),  # the first stands: the second is the walker
                (1, [U3, U2], CHANCE_VALUE, 0, 5, m1, m2, 1, m1),
                (1, [U2], CHANCE_VALUE, 3, 3, m1, m2, 1, m1),  # every 4th pair: this is the 4th
                (1, [U2], CHANCE_VALUE, 3, 0, m1, m2, 1, None),  # the 1st: counted, no call
                (1, [U2], CHANCE_VALUE, 3, 1, m1, m2, 1, None),
                (1, [U1, U2], CHANCE_VALUE, 0, 0, m1, m2, 0, None),  # both stand
                (1, [U3], CHANCE_VALUE, 0, 0, m1, m2, 0, None),  # both walk
                (1, [], CHANCE_VALUE, 0, 0, m1, m2, 0, None),
                (1, [U1], CHANCE_VALUE, 0, 0, m1, m1b, 0, None),  # same unit
                (1, [U2], CHANCE_VALUE, 0, 0, m1, enemy, 0, None),  # enemies
                (1, [U2], 0.0, 0, 0, m1, m2, 0, None),  # animation switched off
                (0, [U2], CHANCE_VALUE, 0, 0, m1, m2, 0, None),  # mod switched off
            ]
            for enable, units, chance, mask, before, first, second, counted, walker in cases:
                fill(enable, units, chance, mask=mask, touched=before)
                assert run(A(first), A(second), None) == A(first) ^ A(second), 'the two arguments must come through unchanged'
                assert c(C_TOUCH) == before + counted and seen[3] == (walker is not None), (enable, units, mask, before, c(C_TOUCH), seen[3])
                assert c(STEP_ON) == 0, 'the mark of our call must be gone afterwards'
                assert sum(struct.unpack_from('<5I', table, C_B_GIVES)) == 0, 'another counter moved'
                if walker is not None:  # the walker in rcx, the chance in xmm1, the mark up, the stack as a routine expects it
                    assert seen[0] == A(walker) and seen[1] & 0xFFFFFFFF == float_bits(chance) and seen[2] & 0xFFFFFFFF == 1
                    assert seen[4] % 16 == 8, 'stack not aligned at the call'
                checks += 5
    print('native test: %d checks passed' % checks)


def lua_script(sigs, extra):
    lua = open(os.path.join(HERE, 'mod.lua.tpl'), encoding='utf8').read()
    rows = []
    for (name, rva, tail, leave, kind), sig in zip(SITES, sigs):
        rows.append("\t{ name = '%s', hi = %d, lo = %d, sig = '%s', tail = '%s', exit_at = %d, exit_hi = %d, exit_lo = %d, call_at = %d, table_at = %d }," % (
            name, rva >> 16, rva & 0xFFFF, probe.lua_bytes(sig), probe.lua_bytes(tail), tail.find(EXIT_MARK), (leave or 0) >> 16, (leave or 0) & 0xFFFF,
            tail.find(CALL_MARK), tail.find(TABLE_MARK)))
    more = ["\t{ name = '%s', hi = %d, lo = %d, sig = '%s' }," % (name, rva >> 16, rva & 0xFFFF, probe.lua_bytes(sig)) for (name, rva), sig in zip(EXTRA, extra)]
    values = {
        'VERSION': VERSION, 'SITES': '\n'.join(rows), 'EXTRA': '\n'.join(more), 'TABLE_SIZE': TABLE_SIZE, 'HEAD': probe.lua_bytes(HEAD),
        'ENABLE': ENABLE, 'K_SMALL': K_SMALL, 'K_BIG': K_BIG, 'MAX_K': MAX_K, 'MIN_K': MIN_K, 'STEP_ON': STEP_ON, 'CHANCE': CHANCE,
        'K_SMALL_BITS': float_bits(K_SMALL_VALUE), 'K_BIG_BITS': float_bits(K_BIG_VALUE),
        'MAX_K_BITS': float_bits(MAX_K_VALUE), 'MIN_K_BITS': float_bits(1.0 / MAX_K_VALUE), 'CHANCE_BITS': float_bits(CHANCE_VALUE),
        'K_TEXT': 'mass ratio %s / %s, heaviest shoved model %s times the walker, step animation chance %s for every %s. touching pair' % (K_SMALL_VALUE, K_BIG_VALUE, MAX_K_VALUE, CHANCE_VALUE, STEP_MASK_VALUE + 1),
        'C_B_GIVES': C_B_GIVES, 'C_A_GIVES': C_A_GIVES, 'C_FREE_1': C_FREE_1, 'C_FREE_2': C_FREE_2, 'C_STEPS': C_STEPS, 'C_TOUCH': C_TOUCH,
        'STEP_MASK': STEP_MASK, 'STEP_MASK_VALUE': STEP_MASK_VALUE,
        'LIST': LIST, 'MAX_UNITS': MAX_UNITS, 'REMAP': REMAP, 'STEP_SLOTS': ', '.join(str(v) for v in STEP_SLOTS),
        'FLINCH_HI': FLINCH >> 16, 'FLINCH_LO': FLINCH & 0xFFFF,
        'P_UID': probe.skaven_cave.P_UID, 'UID_OFF': probe.skaven_cave.UID_OFF,
    }
    for name, value in values.items():
        assert ('--%s--' % name) in lua, name
        lua = lua.replace('--%s--' % name, str(value))
    left = re.findall(r'--[A-Z_0-9]+--', lua)
    assert not left, left
    return lua.encode('utf8')


def main():
    x = probe.exe()
    sigs, extra = signatures(x)
    print('site bytes read from the installed exe: %d sites, animation names as expected' % len(sigs))
    check_decode(x)
    native_test()
    if len(sys.argv) > 2 and sys.argv[1] == 'build':
        lua = lua_script(sigs, extra)
        out = sys.argv[2]
        probe.write_pack(os.path.join(out, PACK), {BATTLE_PATH: lua})
        with open(os.path.join(out, SCRIPT), 'wb') as f:
            f.write(lua)
        print('script: %d lines, version %s' % (lua.count(b'\n'), VERSION))


if __name__ == '__main__':
    main()
