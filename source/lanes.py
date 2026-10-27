"""Military Drill (working title "Units Pass Through") - the mod: lanes, columns, caves.

    python lanes.py check                      offline checks: site bytes in the installed exe, caves decode and run,
                                               caves match caves_listing.asm
    python lanes.py listing                    writes caves_listing.asm (the disassembly of the caves)
    python lanes.py build <out_dir>            the same checks, then writes the TEST build !units_lanes_debug.pack
                                               (full log units_lanes_log.txt, CREW / MISSED diagnostics) and its script
    python lanes.py build <out_dir> --release  the same checks, then writes the RELEASE pack military_drill.pack (no
                                               log file; problems only in the game's lua_mod_log.txt) and its script
    python lanes.py build <out_dir> --narrow-off  the TEST build with TUNE.NARROW = false (085h comparison: big walkers
                                               take the ordinary lanes / columns path); not with --release

What it does: a STANDING unit that a friendly MOVING unit is about to walk through opens lanes: its models are
sent to places shifted sideways in blocks, so that empty lanes appear between the blocks; when the other unit has
passed, the places go back and the unit closes up. Nobody is pushed, no collision value is touched.

How (found with the stack probe, run 8, and the first lanes run, run 9):
A standing unit has a "keep your places" state object C. Its routine 0x327e358 walks the unit's table of places
([C+0x78], 0x30 bytes per model) and hands every model its place (0x3279824); a model that is not there gets a
"station" (0x306c308(model, station record) -> model +0x858), and the model's own tick (0x3070e38) walks it there.
The game only hands places out when it notices models out of place, so three caves are needed:
  cave 1 "place_send" (0x327e49d, in the loop of 0x327e358, r13 = the place record, r12 = the model, rbx = C):
    keeps a copy of the last record of every listed unit (the "template": facing and kind of a place in this
    unit), and for a unit with open lanes hands over a shifted COPY of the record (the game's table is never
    written):
        s = (x - ox) * ax + (z - oz) * az        how far the place is from the lane origin, across the lanes
        n = round(s / P), limited to -N .. N     which block it belongs to
        place += ((n + bias) * L + G * (s - S0)) * (ax, az)
                                                 blocks move apart by L = the lane width; G <= 0 closes the unit up
                                                 towards the walker's line (S0) so that it does not push into a
                                                 unit standing next to it
    It also shortens the random wait the game gives every model before it starts to walk.
    085i: before the formula the cave looks the model up in the unit's table of places (E_PLACES, E_NPLACES records of
    16 bytes: model pointer, x, z; at most 256): a hit puts that x / z into the copy (script-computed layout of the
    blocks: extra ranks); no record, no table or more than 256 records = the formula above (counters PLACE_HIT / PLACE_MISS).
  cave 2 "recheck" (0x32711ca, the 5 s test of C, rbx = C): when the script marked the unit ("dirty"), the places
    are handed out again at once instead of up to 5 s later.
  cave 3 "station" (0x3070ec0, in the model's own tick, rdi = the model): the battle script leaves "jobs" (model,
    station record) for the models of a unit; when a model with a job ticks, the cave calls the game's own
    0x306c308 for it. This is what makes the lanes open BEFORE the other unit arrives.
  cave 4 "walk_to" (0.7; entry of the game's "walk to" 0x3071e04, rcx = model, rdx = target of 0x20 bytes): a
    MARCHING model gets a point a few metres ahead on its way several times a second (probe 7, run 11: always from
    the call whose return address the pattern 'carrot_ret' finds; the target is built in the caller's stack frame).
    Only for those calls, and only for a unit the script marked as passing through lanes, the cave moves the point
    sideways into the nearest lane while it lies in the crossing zone: the walking unit forms columns.
        t = (x - ox) * bx + (z - oz) * bz        along its way; only t0 <= t <= t1
        s = (x - ox) * ax + (z - oz) * az        across; k = round(s / Q - 0.5) limited to kl .. kh
        c = (k + 0.5) * Q                         the middle of lane k (Q = block + lane)
        s' = c + (s - c) * q                      files of the strip squeezed into the lane
  cave 5 "ghost_pass" (0.85e; the per-entity collision pass, r15 = entity A, r14 = entity B, just before the pair test): two entities
    of the same alliance but of DIFFERENT units, one of them in the script's ghost list (ethereal units), are not tested at all: no push in
    either direction, no "blocked" byte. Everything else (enemies, the same unit, units not listed) runs as ever.
Addresses in this file are those of the build the analysis was made on (2026-10-12). Since build 25961564
(2026-10-19) the code is found by byte patterns (sites.py), in this script and in the battle script; the checks
below read the routines found that way, addressed as in the analysed build (sites.View).
"""
import ctypes
import os
import re
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import probe
import mod  # Cave (assembler with long jumps)
import sites
import unitkinds
sys.path.insert(0, os.path.dirname(HERE))
from wh3tools import nasm  # new cave code is written as NASM source (2026-10-19)
from probe import d32, HEAD

VERSION = '0.85'
PACK = '!units_lanes_debug.pack'
SCRIPT = 'units_lanes.lua'
BATTLE_PATH = 'script/battle/mod/' + SCRIPT
RELEASE_PACK = 'military_drill.pack'
RELEASE_SCRIPT = 'military_drill.lua'
RELEASE_BATTLE_PATH = 'script/battle/mod/' + RELEASE_SCRIPT
MCT_PATH = 'script/mct/settings/military_drill.lua'  # read by the Mod Configuration Tool when it is installed


def expendable_units():
    """Main unit keys (unit:type() in battle) of the vanilla units with the attribute 'expendable'."""
    sys.path.insert(0, os.path.dirname(HERE))
    from wh3tools import db
    groups = {}
    for r in db.table('unit_attributes_to_groups_junctions'):
        groups.setdefault(r['attribute_group'], set()).add(r['attribute'])
    land = {r['key']: r for r in db.table('land_units')}
    keys = []
    for m in db.table('main_units'):
        l = land.get(m['land_unit'])
        if l and 'expendable' in groups.get(l.get('attribute_group') or '', set()):
            keys.append(m['unit'])
    assert 30 < len(keys) < 200, len(keys)
    return sorted(keys)

# in the analysed build (2026-10-12): the cave sites and the routines the checks read (start, end)
PLACE_SEND, RECHECK, STATION = 0x327e49d, 0x32711ca, 0x3070ec0
SET_STATION_FN = 0x306c308  # (model, station record of 0x38 bytes)
F_PLACE, F_HAND, F_RECHECK, F_TICK, F_SET_STATION = (0x327e358, 0x327e531), (0x3279824, 0x3279bfd), (0x3271148, None), (0x3070e38, 0x307163c), (0x306c308, 0x306c3b1)
F_READ_RECORD = 0x311d0cc
UNIT = 0x2e8  # model -> its Unit
REC_X, REC_Z, REC_OBJECT, REC_SIZE = 0x00, 0x08, 0x18, 0x30  # place record: x, (y), z; +0x18 an object the place hangs on (then leave it)
WAIT_ARG = 0x28  # [rsp+0x28] at the call = the longest random wait (battle ticks) before the model starts to walk
C_GROUP, C_KIND, C_HAS_KIND, C_LAST = 0x08, 0x5c, 0x63, 0x88  # state object: +8 model group, +0x5c / +0x63 a fixed kind, +0x88 time of the last run
G_COUNT, G_LIST = 0x24, 0x28  # model group: count and list of the models
LONG_AGO = 0xC9742400  # -1,000,000.0

# table (game_alloc, zeroed)
ENABLE = 0x00
COUNT = 0x08  # places shifted so far
REACHED = 0x10  # cave 1 ran
HUNG = 0x18  # places left alone because they hang on an object
CHECKS = 0x20  # cave 2 ran
FORCED = 0x28  # cave 2 made a unit hand out its places again
SET_STATION = 0x30  # address of the game's 0x306c308 (written by the script)
POOL = 0x38  # the job memory of this game session (written by the script)
JOBS_DONE = 0x40  # cave 3 gave a model its station
CARROT_RET = 0x48  # absolute return address of the marching call (written by the script)
CARROTS = 0x50  # cave 4 moved a point into a lane
CARROT_SEEN = 0x58  # cave 4 saw a marching point of a listed unit
HALF = 0x60  # the float 0.5 (written by the script)
CARROTS_NEG = 0x70  # cave 4 pulled a point into a lane with k < 0 (run 20: which side do the walkers' points go to?)
PLACE_HIT = 0x74  # cave 1 handed a model its place from the unit's table (085i)
PLACE_MISS = 0x78  # cave 1 found no entry for a model of a unit that has a table: the old formula
LIST = 0x80  # entries, ended by Unit* = 0
ENTRY = 0x108
# entry: +0 Unit*, lane numbers, +0x30 the shifted copy, +0x60 the template, then kind of C, template counter, jobs
E_OX, E_OZ, E_AX, E_AZ, E_P, E_L, E_N, E_DIRTY, E_WAIT, E_BIAS, E_COPY = 0x08, 0x0c, 0x10, 0x14, 0x18, 0x1c, 0x20, 0x24, 0x28, 0x2c, 0x30
E_TPL, E_C5C, E_C63, E_SEQ, E_PENDING, E_JOBS, E_NJOBS = 0x60, 0x90, 0x94, 0x98, 0x9c, 0xa0, 0xa8
# the same unit as a WALKER (cave 4): Q = 0 means off
E_WQ, E_WOX, E_WOZ, E_WAX, E_WAZ, E_WBX, E_WBZ, E_WT0, E_WT1, E_WC, E_WKL, E_WKH = 0xb0, 0xb4, 0xb8, 0xbc, 0xc0, 0xc4, 0xc8, 0xcc, 0xd0, 0xd4, 0xd8, 0xdc
E_WSL, E_WSH = 0xe0, 0xe4  # across the way: points outside the standing unit (after its blocks stepped aside) are free
MAX_UNITS = 120
E_G, E_S0 = 0xe8, 0xec  # cave 1: closing up (G <= 0) around the walker's line S0 (room left for the neighbours)
E_WLAG, E_WMIN = 0xf0, 0xf4  # cave 4: a point pulled d sideways also comes LAG * |d| nearer (not under MIN ahead)
E_PLACES, E_NPLACES = 0xf8, 0x100  # cave 1 (085i): the unit's table of places (pointer, E_NPLACES records of PLACE_REC bytes: model pointer, x, z); 0 records = off
PLACE_REC, PLACE_CAP = 16, 256  # a record; the cave looks at 256 records at most (more than that counts as no table)
PLACE_POOL_SIZE = MAX_UNITS * PLACE_CAP * PLACE_REC
TABLE_SIZE = 0x8000
# quick ways out: cave 3 runs in every model's tick and cave 4 in every marching call of the battle; while nothing is to
# do, they leave after one compare instead of searching the list of units
WALKERS = 0x64  # how many walkers have their columns on (written by the script); cave 4 does nothing while 0
JOBS_ANY = 0x6c  # 1 while the script has left jobs (written by the script); cave 3 does nothing while 0
JOB, JOB_RECORD, CAP = 0x40, 0x08, 256  # a job: +0 model (0 = taken), +8 the station record (0x38 bytes); CAP jobs per unit
POOL_SIZE = MAX_UNITS * CAP * JOB + PLACE_POOL_SIZE  # 085i: the jobs, then the per-model place tables (the script: pool + MAX_UNITS * CAP * JOB)
# cave 5 (ghost_pass), after the entry list (which ends at 0x7d48)
GHOSTS = 0x7d80  # dword: how many Unit* of the ghost list count (written by the script; 0 = the cave does nothing)
GHOST_SKIPS = 0x7d88  # dword: pairs skipped (the script reads and resets it)
GHOST_TO = 0x7d90  # qword: absolute address the cave jumps to for a skipped pair (written by the script: the site + GHOST_SKIP_OFF)
GHOST_LIST = 0x7da0  # GHOST_MAX x Unit* (qword)
GHOST_MAX = 32
GHOST_SKIP_OFF = 0x43  # the skip target (the end of the `call [B.vtable + 0xb8]`) from the site; checked against the `jne` in signatures()
ALLIANCE, ENTITY_UNIT = 0x80, 0x2e8  # entity: alliance pointer (two entities are friends when equal), the Unit
assert GHOSTS >= LIST + ENTRY * (MAX_UNITS + 1) and GHOST_LIST + 8 * GHOST_MAX <= TABLE_SIZE and GHOST_SKIPS == GHOSTS + 8 and GHOST_TO == GHOSTS + 0x10
# the header fields 0x00 - 0x7f of the table must not overlap (085i review: a qword at 0x68 once ate JOBS_ANY at 0x6c)
_HEADER = [(ENABLE, 4), (COUNT, 4), (REACHED, 4), (HUNG, 4), (CHECKS, 4), (FORCED, 4), (SET_STATION, 8), (POOL, 8), (JOBS_DONE, 4), (CARROT_RET, 8), (CARROTS, 4), (CARROT_SEEN, 4),
           (HALF, 4), (WALKERS, 4), (JOBS_ANY, 4), (CARROTS_NEG, 4), (PLACE_HIT, 4), (PLACE_MISS, 4)]
_SPANS = sorted(_HEADER)
assert all(a + n <= b for (a, n), (b, _) in zip(_SPANS, _SPANS[1:])) and _SPANS[-1][0] + _SPANS[-1][1] <= LIST, 'header fields overlap'
assert LIST + ENTRY * (MAX_UNITS + 1) <= TABLE_SIZE and E_COPY + REC_SIZE == E_TPL and E_TPL + REC_SIZE == E_C5C and E_NPLACES + 8 == ENTRY


def find_unit(a):
    """rcx = Unit. Leaves r10 = its entry, or jumps to 'done'. Uses r11."""
    a.emit(b'\x48\x85\xC9')  # test rcx, rcx
    a.far(0x84, 'done')
    a.emit(b'\x4C\x8D\x90' + d32(LIST))  # lea r10, [rax+LIST]
    a.label('loop')
    a.emit(b'\x4D\x8B\x1A')  # mov r11, [r10]
    a.emit(b'\x4D\x85\xDB')  # test r11, r11
    a.far(0x84, 'done')
    a.emit(b'\x49\x39\xCB')  # cmp r11, rcx
    a.jump(0x74, 'found')
    a.emit(b'\x49\x81\xC2' + d32(ENTRY))  # add r10, ENTRY
    a.jump(0xEB, 'loop')
    a.label('found')


# cave 1 (085i): the per-model place table of the unit (r10 = its entry, r12 = the model, rax = the table). Leaves rdx = the record of the model in the
# table, or 0 (no table, too many records, no record for this model: counted as a miss when the unit has a table). The loop is bounded by PLACE_CAP.
PLACE_LOOKUP_SOURCE = '''
    xor edx, edx
    mov r8, [r10+E_PLACES]
    test r8, r8
    jz place_none
    mov ecx, [r10+E_NPLACES]
    test ecx, ecx
    jz place_none
    cmp ecx, PLACE_CAP
    ja place_none
place_scan:
    cmp [r8], r12
    je place_found
    add r8, PLACE_REC
    dec ecx
    jnz place_scan
    lock inc dword [rax+PLACE_MISS]
    jmp place_none
place_found:
    mov rdx, r8
place_none:
'''
PLACE_LOOKUP_CODE = nasm.assemble(PLACE_LOOKUP_SOURCE, {'E_PLACES': E_PLACES, 'E_NPLACES': E_NPLACES, 'PLACE_CAP': PLACE_CAP, 'PLACE_REC': PLACE_REC, 'PLACE_MISS': PLACE_MISS})

# the hit: the record of the model in the copy, x and z from the table (rdx = the record)
PLACE_HIT_SOURCE = '''
    movups xmm0, [r13]
    movups [r10+E_COPY], xmm0
    movups xmm0, [r13+0x10]
    movups [r10+E_COPY+0x10], xmm0
    movups xmm0, [r13+0x20]
    movups [r10+E_COPY+0x20], xmm0
    mov ecx, [rdx+8]
    mov [r10+E_COPY+REC_X], ecx
    mov ecx, [rdx+12]
    mov [r10+E_COPY+REC_Z], ecx
    lea r13, [r10+E_COPY]
    lock inc dword [rax+PLACE_HIT]
    lock inc dword [rax+COUNT]
'''
PLACE_HIT_CODE = nasm.assemble(PLACE_HIT_SOURCE, {'E_COPY': E_COPY, 'REC_X': REC_X, 'REC_Z': REC_Z, 'PLACE_HIT': PLACE_HIT, 'COUNT': COUNT})


def cave_place():
    """In the loop of 0x327e358, before `mov r8, r13 / mov rdx, r12`: r13 = place record of this model (in the
    game's table: read only), r12 = the model, rbx = C, [rsp+0x28] = the random wait handed to 0x3279824.
    Free: rax, rcx, rdx, r8-r11, xmm0-xmm2, xmm4, xmm5, flags. xmm3 is an argument of the call: untouched.
    r13 is loaded anew at the top of every pass, so it may point to the copy when the cave ends."""
    a = mod.Cave()
    a.lock_inc(REACHED)
    a.emit(b'\x49\x8B\x8C\x24' + d32(UNIT))  # mov rcx, [r12+0x2e8]
    find_unit(a)
    for part in (0x00, 0x10, 0x20):  # the template: the last record handed out in this unit
        a.emit(b'\x41\x0F\x10\x45' + bytes([part]))  # movups xmm0, [r13+part]
        a.emit(b'\x41\x0F\x11\x82' + d32(E_TPL + part))  # movups [r10+tpl+part], xmm0
    a.emit(b'\x8B\x4B' + bytes([C_KIND]))  # mov ecx, [rbx+0x5c]
    a.emit(b'\x41\x89\x8A' + d32(E_C5C))  # mov [r10+c5c], ecx
    a.emit(b'\x0F\xB6\x4B' + bytes([C_HAS_KIND]))  # movzx ecx, byte [rbx+0x63]
    a.emit(b'\x41\x89\x8A' + d32(E_C63))  # mov [r10+c63], ecx
    a.emit(b'\x41\xFF\x82' + d32(E_SEQ))  # inc dword [r10+seq]
    a.emit(b'\x83\xB8' + d32(ENABLE) + b'\x00')  # cmp dword [rax+ENABLE], 0
    a.far(0x84, 'done')
    a.emit(b'\xF3\x41\x0F\x10\x62' + bytes([E_L]))  # movss xmm4, [r10+L]
    a.emit(b'\x0F\x57\xC0')  # xorps xmm0, xmm0
    a.emit(b'\x0F\x2E\xE0')  # ucomiss xmm4, xmm0
    a.far(0x84, 'done')  # lanes closed
    a.emit(b'\x49\x83\x7D' + bytes([REC_OBJECT]) + b'\x00')  # cmp qword [r13+0x18], 0
    a.far(0x85, 'hung')  # the place hangs on an object (a wall, a platform): leave it
    a.emit(b'\x41\x8B\x4A' + bytes([E_WAIT]))  # mov ecx, [r10+wait]
    a.emit(b'\x39\x4C\x24' + bytes([WAIT_ARG]))  # cmp [rsp+0x28], ecx
    a.jump(0x76, 'waited')  # jbe: the game's wait is already that short
    a.emit(b'\x89\x4C\x24' + bytes([WAIT_ARG]))  # mov [rsp+0x28], ecx
    a.label('waited')
    a.emit(PLACE_LOOKUP_CODE)  # 085i: rdx = the model's record in the unit's table of places, or 0 (NASM source above)
    a.emit(b'\x48\x85\xD2')  # test rdx, rdx
    a.far(0x85, 'table_hit')  # jne: the table names this model's place
    a.emit(b'\xF3\x41\x0F\x10\x4D' + bytes([REC_X]))  # movss xmm1, [r13+x]
    a.emit(b'\xF3\x41\x0F\x5C\x4A' + bytes([E_OX]))  # subss xmm1, [r10+ox]
    a.emit(b'\xF3\x41\x0F\x10\x55' + bytes([REC_Z]))  # movss xmm2, [r13+z]
    a.emit(b'\xF3\x41\x0F\x5C\x52' + bytes([E_OZ]))  # subss xmm2, [r10+oz]
    a.emit(b'\xF3\x41\x0F\x59\x4A' + bytes([E_AX]))  # mulss xmm1, [r10+ax]
    a.emit(b'\xF3\x41\x0F\x59\x52' + bytes([E_AZ]))  # mulss xmm2, [r10+az]
    a.emit(b'\xF3\x0F\x58\xCA')  # addss xmm1, xmm2                         s
    a.emit(b'\x0F\x28\xC1')  # movaps xmm0, xmm1                        (xmm0 is free until the copy below)
    a.emit(b'\xF3\x41\x0F\x5C\x82' + d32(E_S0))  # subss xmm0, [r10+S0]            s - S0
    a.emit(b'\xF3\x41\x0F\x59\x82' + d32(E_G))  # mulss xmm0, [r10+G]              closing up: G (s - S0)
    a.emit(b'\xF3\x41\x0F\x5E\x4A' + bytes([E_P]))  # divss xmm1, [r10+P]
    a.emit(b'\xF3\x0F\x2D\xC9')  # cvtss2si ecx, xmm1                       n = round(s / P)
    a.emit(b'\x81\xF9\x00\x00\x00\x80')  # cmp ecx, 0x80000000              not a number that fits
    a.far(0x84, 'done')
    a.emit(b'\x45\x8B\x5A' + bytes([E_N]))  # mov r11d, [r10+N]
    a.emit(b'\x44\x39\xD9')  # cmp ecx, r11d
    a.emit(b'\x41\x0F\x4F\xCB')  # cmovg ecx, r11d
    a.emit(b'\x41\xF7\xDB')  # neg r11d
    a.emit(b'\x44\x39\xD9')  # cmp ecx, r11d
    a.emit(b'\x41\x0F\x4C\xCB')  # cmovl ecx, r11d
    a.emit(b'\xF3\x0F\x2A\xC9')  # cvtsi2ss xmm1, ecx
    a.emit(b'\xF3\x41\x0F\x58\x4A' + bytes([E_BIAS]))  # addss xmm1, [r10+bias]
    a.emit(b'\xF3\x0F\x59\xCC')  # mulss xmm1, xmm4                         (n + bias) * L
    a.emit(b'\xF3\x0F\x58\xC8')  # addss xmm1, xmm0                         shift = (n + bias) * L + G (s - S0)
    for part in (0x00, 0x10, 0x20):  # the record goes to this unit's own copy
        a.emit(b'\x41\x0F\x10\x45' + bytes([part]))  # movups xmm0, [r13+part]
        a.emit(b'\x41\x0F\x11\x42' + bytes([E_COPY + part]))  # movups [r10+copy+part], xmm0
    a.emit(b'\x4D\x8D\x6A' + bytes([E_COPY]))  # lea r13, [r10+copy]         from here on r13 = the copy
    a.emit(b'\x0F\x28\xD1')  # movaps xmm2, xmm1
    a.emit(b'\xF3\x41\x0F\x59\x52' + bytes([E_AX]))  # mulss xmm2, [r10+ax]
    a.emit(b'\xF3\x41\x0F\x58\x55' + bytes([REC_X]))  # addss xmm2, [r13+x]
    a.emit(b'\xF3\x41\x0F\x11\x55' + bytes([REC_X]))  # movss [r13+x], xmm2
    a.emit(b'\xF3\x41\x0F\x59\x4A' + bytes([E_AZ]))  # mulss xmm1, [r10+az]
    a.emit(b'\xF3\x41\x0F\x58\x4D' + bytes([REC_Z]))  # addss xmm1, [r13+z]
    a.emit(b'\xF3\x41\x0F\x11\x4D' + bytes([REC_Z]))  # movss [r13+z], xmm1
    a.lock_inc(COUNT)
    a.jump(0xEB, 'done')
    a.label('hung')
    a.lock_inc(HUNG)
    a.jump(0xEB, 'done')
    a.label('table_hit')
    a.emit(PLACE_HIT_CODE)  # the copy with x and z from the table (NASM source above)
    a.label('done')
    return a.done()


def cave_recheck():
    """At the 5 s test of 0x3271148, before `movss xmm0, [rbx+0x88]`: rbx = the state object C.
    Free: rax, rcx, rdx, r8-r11, flags. No xmm register is touched (xmm1 holds the time)."""
    a = mod.Cave()
    a.lock_inc(CHECKS)
    a.emit(b'\x83\xB8' + d32(ENABLE) + b'\x00')  # cmp dword [rax+ENABLE], 0
    a.far(0x84, 'done')
    a.emit(b'\x48\x8B\x4B' + bytes([C_GROUP]))  # mov rcx, [rbx+8]             the model group
    a.emit(b'\x48\x85\xC9')  # test rcx, rcx
    a.far(0x84, 'done')
    a.emit(b'\x83\x79' + bytes([G_COUNT]) + b'\x00')  # cmp dword [rcx+0x24], 0
    a.far(0x84, 'done')
    a.emit(b'\x48\x8B\x49' + bytes([G_LIST]))  # mov rcx, [rcx+0x28]           the list of its models
    a.emit(b'\x48\x85\xC9')  # test rcx, rcx
    a.far(0x84, 'done')
    a.emit(b'\x48\x8B\x09')  # mov rcx, [rcx]                                  the first model
    a.emit(b'\x48\x85\xC9')  # test rcx, rcx
    a.far(0x84, 'done')
    a.emit(b'\x48\x8B\x89' + d32(UNIT))  # mov rcx, [rcx+0x2e8]                its Unit
    find_unit(a)
    a.emit(b'\x41\x83\x7A' + bytes([E_DIRTY]) + b'\x00')  # cmp dword [r10+dirty], 0
    a.far(0x84, 'done')
    a.emit(b'\x41\xC7\x42' + bytes([E_DIRTY]) + d32(0))  # mov dword [r10+dirty], 0
    a.emit(b'\xC7\x83' + d32(C_LAST) + struct.pack('<I', LONG_AGO))  # mov dword [rbx+0x88], -1000000.0
    a.lock_inc(FORCED)
    a.label('done')
    return a.done()


def cave_station():
    """In the model's own tick 0x3070e38, before `cmp byte [rdi+0x88a], r15b`: rdi = the model. A call has just
    returned, so every volatile register is free; rsp is aligned and the routine's own home space lies under it.
    If the battle script left a job for this model, the game's 0x306c308(model, station record) is called."""
    a = mod.Cave()
    a.emit(b'\x83\xB8' + d32(ENABLE) + b'\x00')  # cmp dword [rax+ENABLE], 0
    a.far(0x84, 'done')
    a.emit(b'\x83\xB8' + d32(JOBS_ANY) + b'\x00')  # cmp dword [rax+JOBS_ANY], 0
    a.far(0x84, 'done')  # no jobs anywhere
    a.emit(b'\x48\x8B\x8F' + d32(UNIT))  # mov rcx, [rdi+0x2e8]
    find_unit(a)
    a.emit(b'\x41\x83\xBA' + d32(E_PENDING) + b'\x00')  # cmp dword [r10+pending], 0
    a.far(0x84, 'done')
    a.emit(b'\x49\x8B\x92' + d32(E_JOBS))  # mov rdx, [r10+jobs]
    a.emit(b'\x48\x85\xD2')  # test rdx, rdx
    a.far(0x84, 'done')
    a.emit(b'\x41\x8B\x8A' + d32(E_NJOBS))  # mov ecx, [r10+njobs]
    a.label('scan')
    a.emit(b'\x85\xC9')  # test ecx, ecx
    a.far(0x84, 'done')
    a.emit(b'\x48\x39\x3A')  # cmp [rdx], rdi
    a.jump(0x74, 'hit')
    a.emit(b'\x48\x83\xC2' + bytes([JOB]))  # add rdx, JOB
    a.emit(b'\xFF\xC9')  # dec ecx
    a.jump(0xEB, 'scan')
    a.label('hit')
    a.emit(b'\x48\xC7\x02' + d32(0))  # mov qword [rdx], 0                    the job is taken
    a.emit(b'\xF0\x41\xFF\x8A' + d32(E_PENDING))  # lock dec dword [r10+pending]
    a.lock_inc(JOBS_DONE)
    a.emit(b'\x48\x83\xC2' + bytes([JOB_RECORD]))  # add rdx, 8                the station record
    a.emit(b'\x48\x89\xF9')  # mov rcx, rdi
    a.emit(b'\xFF\x50' + bytes([SET_STATION]))  # call [rax+SET_STATION]
    a.label('done')
    return a.done()


def cave_carrot():
    """Entry of "walk to": rcx = model, rdx = target (x +0, z +8), r8 = settings; [rsp] = return address.
    Must keep rcx, rdx, r8, r9 and xmm3. Changes rax (the table), r10, r11, xmm0-xmm2, xmm4, xmm5, flags, and the
    target's x / z, but only when the caller is the marching call (its target lives in the caller's stack frame)."""
    a = mod.Cave()
    a.emit(b'\x83\xB8' + d32(ENABLE) + b'\x00')  # cmp dword [rax+ENABLE], 0
    a.far(0x84, 'done')
    a.emit(b'\x83\xB8' + d32(WALKERS) + b'\x00')  # cmp dword [rax+WALKERS], 0
    a.far(0x84, 'done')  # no walker forms columns now
    a.emit(b'\x4C\x8B\x14\x24')  # mov r10, [rsp]                              return address
    a.emit(b'\x4C\x3B\x90' + d32(CARROT_RET))  # cmp r10, [rax+CARROT_RET]
    a.far(0x85, 'done')
    a.emit(b'\x4C\x8B\x99' + d32(UNIT))  # mov r11, [rcx+0x2e8]                 the model's unit
    a.emit(b'\x4D\x85\xDB')  # test r11, r11
    a.far(0x84, 'done')
    a.emit(b'\x4C\x8D\x90' + d32(LIST))  # lea r10, [rax+LIST]
    a.label('loop')
    a.emit(b'\x49\x83\x3A\x00')  # cmp qword [r10], 0
    a.far(0x84, 'done')
    a.emit(b'\x4D\x39\x1A')  # cmp [r10], r11
    a.jump(0x74, 'found')
    a.emit(b'\x49\x81\xC2' + d32(ENTRY))  # add r10, ENTRY
    a.jump(0xEB, 'loop')
    a.label('found')
    a.lock_inc(CARROT_SEEN)
    a.emit(b'\xF3\x41\x0F\x10\xAA' + d32(E_WQ))  # movss xmm5, [r10+WQ]          Q
    a.emit(b'\x0F\x57\xC0')  # xorps xmm0, xmm0
    a.emit(b'\x0F\x2E\xE8')  # ucomiss xmm5, xmm0
    a.far(0x84, 'done')  # not a walker now
    a.emit(b'\xF3\x0F\x10\x0A')  # movss xmm1, [rdx]                          x
    a.emit(b'\xF3\x41\x0F\x5C\x8A' + d32(E_WOX))  # subss xmm1, [r10+WOX]
    a.emit(b'\xF3\x0F\x10\x52\x08')  # movss xmm2, [rdx+8]                   z
    a.emit(b'\xF3\x41\x0F\x5C\x92' + d32(E_WOZ))  # subss xmm2, [r10+WOZ]
    a.emit(b'\x0F\x28\xC1')  # movaps xmm0, xmm1
    a.emit(b'\xF3\x41\x0F\x59\x82' + d32(E_WBX))  # mulss xmm0, [r10+WBX]
    a.emit(b'\x0F\x28\xE2')  # movaps xmm4, xmm2
    a.emit(b'\xF3\x41\x0F\x59\xA2' + d32(E_WBZ))  # mulss xmm4, [r10+WBZ]
    a.emit(b'\xF3\x0F\x58\xC4')  # addss xmm0, xmm4                          t
    a.emit(b'\x41\x0F\x2F\x82' + d32(E_WT0))  # comiss xmm0, [r10+WT0]
    a.far(0x82, 'done')  # jb: before the crossing zone
    a.emit(b'\x41\x0F\x2F\x82' + d32(E_WT1))  # comiss xmm0, [r10+WT1]
    a.far(0x87, 'done')  # ja: behind it
    a.emit(b'\xF3\x41\x0F\x59\x8A' + d32(E_WAX))  # mulss xmm1, [r10+WAX]
    a.emit(b'\xF3\x41\x0F\x59\x92' + d32(E_WAZ))  # mulss xmm2, [r10+WAZ]
    a.emit(b'\xF3\x0F\x58\xCA')  # addss xmm1, xmm2                          s
    a.emit(b'\x41\x0F\x2F\x8A' + d32(E_WSL))  # comiss xmm1, [r10+WSL]
    a.far(0x82, 'done')  # jb: beside the standing unit, nothing in its way
    a.emit(b'\x41\x0F\x2F\x8A' + d32(E_WSH))  # comiss xmm1, [r10+WSH]
    a.far(0x87, 'done')  # ja: beside it on the other side
    a.emit(b'\x0F\x28\xC1')  # movaps xmm0, xmm1
    a.emit(b'\xF3\x0F\x5E\xC5')  # divss xmm0, xmm5
    a.emit(b'\xF3\x0F\x5C\x80' + d32(HALF))  # subss xmm0, [rax+HALF]
    a.emit(b'\xF3\x44\x0F\x2D\xD8')  # cvtss2si r11d, xmm0                 k = round(s / Q - 0.5)
    a.emit(b'\x41\x81\xFB\x00\x00\x00\x80')  # cmp r11d, 0x80000000
    a.far(0x84, 'done')
    a.emit(b'\x45\x3B\x9A' + d32(E_WKH))  # cmp r11d, [r10+WKH]
    a.jump(0x7E, 'not_high')  # jle
    a.emit(b'\x45\x8B\x9A' + d32(E_WKH))  # mov r11d, [r10+WKH]
    a.label('not_high')
    a.emit(b'\x45\x3B\x9A' + d32(E_WKL))  # cmp r11d, [r10+WKL]
    a.jump(0x7D, 'not_low')  # jge
    a.emit(b'\x45\x8B\x9A' + d32(E_WKL))  # mov r11d, [r10+WKL]
    a.label('not_low')
    a.emit(SIDE_CODE)  # count the points sent to a lane with k < 0 (NASM source below)
    a.emit(b'\xF3\x41\x0F\x2A\xC3')  # cvtsi2ss xmm0, r11d
    a.emit(b'\xF3\x0F\x58\x80' + d32(HALF))  # addss xmm0, [rax+HALF]
    a.emit(b'\xF3\x0F\x59\xC5')  # mulss xmm0, xmm5                          c
    a.emit(b'\x0F\x28\xD1')  # movaps xmm2, xmm1
    a.emit(b'\xF3\x0F\x5C\xD0')  # subss xmm2, xmm0                          s - c
    a.emit(b'\xF3\x41\x0F\x59\x92' + d32(E_WC))  # mulss xmm2, [r10+WC]
    a.emit(b'\xF3\x0F\x58\xD0')  # addss xmm2, xmm0                          s'
    a.emit(b'\xF3\x0F\x5C\xD1')  # subss xmm2, xmm1                          d = s' - s
    a.emit(PULL_CODE)  # sideways into the lane, and nearer by LAG * |d| (NASM source below)
    a.lock_inc(CARROTS)
    a.label('done')
    return a.done()


# xmm2 = d (sideways), rcx = the model, rdx = the target, r10 = the walker's entry. Run 15/16: 14-16 files cannot
# squeeze into 2 x 3-4 files at the same speed; a model pulled further sideways gets its point nearer, so it slows
# down and falls in behind the files already in its lane (the column grows deeper, like a zipper).
PULL_SOURCE = '''
    movaps xmm4, xmm2           ; d
    movaps xmm0, xmm2
    mulss xmm0, [r10+E_WAX]
    addss xmm0, [rdx]
    movss [rdx], xmm0           ; x += d * ax
    mulss xmm2, [r10+E_WAZ]
    addss xmm2, [rdx+8]
    movss [rdx+8], xmm2         ; z += d * az
    movss xmm0, [rdx]
    subss xmm0, [rcx+0x88]
    mulss xmm0, [r10+E_WBX]
    movss xmm1, [rdx+8]
    subss xmm1, [rcx+0x90]
    mulss xmm1, [r10+E_WBZ]
    addss xmm0, xmm1            ; ahead = (target - model) . b
    xorps xmm1, xmm1
    subss xmm1, xmm4
    maxss xmm1, xmm4            ; |d|
    mulss xmm1, [r10+E_WLAG]
    movaps xmm2, xmm0
    subss xmm2, xmm1            ; ahead - LAG |d|
    maxss xmm2, [r10+E_WMIN]    ; not under MIN
    minss xmm2, xmm0            ; never further than it was
    subss xmm2, xmm0            ; change along b (<= 0)
    movaps xmm0, xmm2
    mulss xmm0, [r10+E_WBX]
    addss xmm0, [rdx]
    movss [rdx], xmm0
    mulss xmm2, [r10+E_WBZ]
    addss xmm2, [rdx+8]
    movss [rdx+8], xmm2
'''
PULL_CODE = nasm.assemble(PULL_SOURCE, {'E_WAX': E_WAX, 'E_WAZ': E_WAZ, 'E_WBX': E_WBX, 'E_WBZ': E_WBZ, 'E_WLAG': E_WLAG, 'E_WMIN': E_WMIN})

SIDE_SOURCE = '''
    test r11d, r11d
    jns lane_k_not_negative
    lock inc dword [rax+CARROTS_NEG]
lane_k_not_negative:
'''
SIDE_CODE = nasm.assemble(SIDE_SOURCE, {'CARROTS_NEG': CARROTS_NEG})


# cave 5 "ghost_pass": at the start of the call setup of the pair test in 0x316ff04: r15 = entity A, r14 = entity B (both after the 0x200000
# redirect), rax = the table (set by the cave head). Free: rax, rcx, rdx, r8, r9, r10, r11, flags (the replaced instructions set them all before
# the call). A pair of the same alliance and of different units (both entities with a unit) of which one unit is in the ghost list: skipped to
# the end of the pair handler (no touch test, no push, no blocked byte). Each pair is visited once per pass, so "either listed" holds both ways.
GHOST_SOURCE = '''
    mov ecx, [rax+GHOSTS]
    test ecx, ecx
    jz ghost_done                  ; the list is empty (the script wrote 0)
    cmp ecx, GHOST_MAX
    ja ghost_done                  ; not a count the cave knows
    mov rdx, [r15+ALLIANCE]
    cmp rdx, [r14+ALLIANCE]
    jne ghost_done                 ; enemies: as ever
    mov rdx, [r15+ENTITY_UNIT]     ; A's unit
    mov r8, [r14+ENTITY_UNIT]      ; B's unit
    test rdx, rdx
    jz ghost_done
    test r8, r8
    jz ghost_done
    cmp rdx, r8
    je ghost_done                  ; the same unit: as ever
    lea r9, [rax+GHOST_LIST]
ghost_scan:
    mov r10, [r9]
    cmp r10, rdx
    je ghost_skip
    cmp r10, r8
    je ghost_skip
    add r9, 8
    dec ecx
    jnz ghost_scan
    jmp ghost_done
ghost_skip:
    lock inc dword [rax+GHOST_SKIPS]
    jmp [rax+GHOST_TO]             ; past the pair test and the handler
ghost_done:
'''


def cave_ghost():
    return nasm.assemble(GHOST_SOURCE, {'GHOSTS': GHOSTS, 'GHOST_MAX': GHOST_MAX, 'ALLIANCE': ALLIANCE, 'ENTITY_UNIT': ENTITY_UNIT, 'GHOST_LIST': GHOST_LIST,
                                        'GHOST_SKIPS': GHOST_SKIPS, 'GHOST_TO': GHOST_TO})


CAVES = [('place_send', cave_place()), ('recheck', cave_recheck()), ('station', cave_station()), ('walk_to', cave_carrot()), ('ghost_pass', cave_ghost())]
SITES = []  # (name, rva in the installed exe, cave, None, 'inner'), filled by locate()
FOUND = {}  # name -> rva in the installed exe (cave sites and set_station), filled by locate()


def locate(x):
    """Finds the cave sites and the station routine in the installed exe by pattern (each must match once)."""
    FOUND.clear()
    for name in ('place_send', 'recheck', 'station', 'set_station', 'walk_to', 'carrot_ret', 'ghost_pass'):
        FOUND[name] = sites.find(x, name)
    SITES[:] = [(name, FOUND[name], cave, None, 'entry' if name == 'walk_to' else 'inner') for name, cave in CAVES]
    return FOUND


def signatures(x):
    """Checks the analysis against the installed exe (routines found by pattern, addressed as in the analysed build)
    and returns the bytes the battle script compares at each cave site."""
    locate(x)
    hand = sites.call_target(x, FOUND['place_send'] + 9)
    names = {hand: F_HAND[0], FOUND['set_station']: SET_STATION_FN}  # new rva -> old rva of the routines called
    place = sites.View(x, FOUND['place_send'], *F_PLACE, names=names)
    recheck = sites.View(x, FOUND['recheck'], *F_RECHECK)
    tick = sites.View(x, FOUND['station'], *F_TICK)
    assert (place.new(PLACE_SEND), recheck.new(RECHECK), tick.new(STATION)) == (FOUND['place_send'], FOUND['recheck'], FOUND['station'])
    expect = {'place_send': ['mov r8, r13', 'mov rdx, r12', 'mov rcx, rbx', 'call 0x%x' % hand],
              'recheck': ['movss xmm0, dword ptr [rbx + 0x88]', 'addss xmm0, dword ptr [rip + ADDR]', 'mulss xmm1, dword ptr [rip + ADDR]',
                          'comiss xmm1, xmm0'],
              'station': ['cmp byte ptr [rdi + 0x88a], r15b', 'jne 0x%x' % tick.new(0x3071114)],
              'walk_to': ['mov qword ptr [rsp + 8], rbx', 'mov qword ptr [rsp + 0x10], rsi', 'push rdi'],
              'ghost_pass': ['mov rax, qword ptr [rdi + 0x58]', 'mov r9, r15', 'mov qword ptr [rsp + 0x30], r14', 'mov r8, r12', 'mov rdx, rbx',
                             'mov rcx, qword ptr [rax + 0x430]']}
    # cave 4: the marching call hands walk_to a target in its own stack frame, so changing it changes nothing else
    ret = FOUND['carrot_ret']
    assert sites.call_target(x, ret - 5) == FOUND['walk_to'], 'the marching call goes to walk_to'
    assert bytes(x.read(ret - 12, 7)) == bytes.fromhex('488d55cf' '8945cb'), 'lea rdx, [rbp-0x31] / mov [rbp-0x35], eax right before it'
    walk = sites.View(x, FOUND['walk_to'], FOUND['walk_to'], None)
    assert walk[FOUND['walk_to'] + 0x12] == 'mov rsi, rdx' and walk[FOUND['walk_to'] + 0x15] == 'mov rbx, rcx', 'walk_to keeps rcx / rdx: they are its arguments'
    # cave 5: the pair test of the collision pass. The `jne` after the pair routine skips the handler: its target is the end of the vcall
    # (GHOST_SKIP_OFF from the site), where the loop goes on with the next entity; the pair routine is a function of its own
    ghost = FOUND['ghost_pass']
    assert bytes(x.read(ghost + 0x32, 2)) == bytes.fromhex('750f') and ghost + 0x34 + 0x0f == ghost + GHOST_SKIP_OFF, 'the jne after the pair routine skips to the end of the handler call'
    assert bytes(x.read(ghost + GHOST_SKIP_OFF, 4)) == bytes.fromhex('488b55c8'), 'the skip target reloads rdx from the loop frame'
    assert bytes(x.read(ghost + 0x3d, 2)) == bytes.fromhex('ff90'), 'the handler is call [B.vtable + 0xb8]'
    pair = sites.call_target(x, ghost + 0x2b)
    assert x.func(pair) and x.func(pair)[0] == pair, 'the pair routine is a function'
    assert x.func(ghost)[0] < ghost < x.func(ghost)[1], 'the site lies in the collision pass'
    probe.signatures(x, SITES, expect)
    sigs = [sites.expect_bytes(x, rva) for name, rva, tail, leave, kind in SITES]
    # the routine that hands out the places: rbx = C, the loop takes model and record of the same index
    text = place
    assert text[PLACE_SEND + 9] == 'call 0x3279824', 'the call is to the routine checked below'
    assert text[0x327e399] == 'mov rcx, qword ptr [rbx + 8]' and text[0x327e3a1] == 'mov esi, dword ptr [rcx + 0x24]'
    assert text[0x327e3b2] == 'mov rax, qword ptr [rbx + 8]' and text[0x327e3b6] == 'mov r13, qword ptr [rbx + 0x78]'
    assert text[0x327e3c2] == 'add r13, r15' and text[0x327e3c5] == 'mov rcx, qword ptr [rax + 0x28]'
    assert text[0x327e3c9] == 'mov r12, qword ptr [r14 + rcx]'
    assert text[0x327e491] == 'mov dword ptr [rsp + 0x28], eax', 'the random wait is on the stack before the cave'
    assert text[0x327e4b3] == 'add r15, 0x30' and text[0x327e4b7] == 'add r14, 8' and text[0x327e4bd] == 'jb 0x327e3b2'
    assert text[0x327e4fd] == 'movss dword ptr [rbx + 0x88], xmm1', 'the routine notes when it ran'
    for at, line in text.items():  # after the call nothing reads r13 until the loop loads it anew
        if at > PLACE_SEND + 9 and 'r13' in line:
            assert line == 'pop r13', (hex(at), line)
    # the routine that is handed the place: r8 = record (read only through rsi), the wait is its 6th argument;
    # how it fills the station record from the place record and from C (the script does the same)
    text = sites.View(x, hand, *F_HAND, names=names)
    assert text[0x3279836] == 'lea rbp, [rax - 0x78]' and text[0x3279854] == 'mov rsi, r8' and text[0x327984c] == 'mov rdi, rdx'
    assert text[0x32799ff] == 'mov edx, dword ptr [rbp + 0xa8]' and text[0x3279a99] == 'call 0x306c308'
    assert text[0x3279857] == 'mov r8d, dword ptr [rdx + 0x318]' and text[0x3279862] == 'mov edx, 0xc' and text[0x327987a] == 'cmovne edx, r13d'
    assert text[0x3279880] == 'cmp byte ptr [rcx + 0x63], r13b' and text[0x3279886] == 'mov ebx, dword ptr [rcx + 0x5c]'
    assert text[0x327988b] == 'mov ecx, dword ptr [rsi + 0x2c]' and text[0x327988e] == 'sub ecx, 5'
    assert [text[a] for a in (0x32798a7, 0x32798ac, 0x32798b3, 0x32798ba, 0x32798c1)] == [
        'lea ebx, [rcx + 5]', 'mov ebx, 5', 'mov ebx, 0x69', 'mov ebx, 0x68', 'mov ebx, 0x67']
    assert text[0x32798e5] == 'lea r15, [rsi + 0x22]' and text[0x3279a37] == 'movzx eax, word ptr [r15]'
    assert text[0x3279a3b] == 'mov word ptr [rsp + 0x60], ax' and text[0x3279a65] == 'mov dword ptr [rsp + 0x64], ebx'
    assert text[0x3279a69] == 'mov word ptr [rsp + 0x70], r13w' and text[0x3279a7a] == 'mov byte ptr [rsp + 0x72], r13b'
    assert text[0x3279a8e] == 'mov dword ptr [rsp + 0x6c], eax' and text[0x3279a92] == 'mov eax, dword ptr [rsi + 0x24]'
    assert text[0x3279a95] == 'mov dword ptr [rsp + 0x68], eax' and text[0x3279a7f] == 'lea rdx, [rsp + 0x40]'
    for at, line in text.items():  # the record is only read
        dest = line.split(',')[0]
        assert not ('[rsi' in dest and line.split()[0].startswith(('mov', 'add', 'sub', 'and', 'or', 'xor', 'inc', 'dec'))), (hex(at), line)
    # the 5 s test: rbx = C (from rdx - 0x30, like the thunk of the routine above), [C+8] = the model group
    text = recheck
    assert text[0x327114e] == 'lea rax, [rdx - 0x30]' and text[0x3271158] == 'and rbx, rax' and text[0x327115f] == 'mov r9, qword ptr [rbx + 8]'
    assert text[0x32711e2] == 'comiss xmm1, xmm0' and text[0x32711e5] == 'jbe 0x32711f9' and text[0x32711f2] == 'jmp qword ptr [rax + 0xc8]'
    five = FOUND['recheck'] + 16 + struct.unpack('<i', bytes(x.read(FOUND['recheck'] + 12, 4)))[0]  # addss xmm0, [rip + d]
    assert struct.unpack('<f', x.read(five, 4))[0] == 5.0, 'the places are handed out every 5 s'
    thunks = [h for h in x.find('48 8D 42 D0 48 F7 DA 48 1B C9 48 23 C8 E9', 5000)
              if h + 0x12 + struct.unpack('<i', bytes(x.read(h + 0xe, 4)))[0] == place.start]
    assert len(thunks) == 1, 'one thunk leads to the routine that hands out the places, with rcx = rdx - 0x30'
    # how a place record is read: +0x18 an object, else x / y / z at +0 / +4 / +8
    text = sites.View(x, sites.call_target(x, sites.find(x, 'form_loop') + 10), F_READ_RECORD)
    assert text[0x311d0d3] == 'mov rcx, qword ptr [rcx + 0x18]' and text[0x311d0e7] == 'mov eax, dword ptr [r8]'
    assert text[0x311d0f3] == 'mov eax, dword ptr [r8 + 8]'
    # the model's own tick: rdi = the model; the cave sits right after a call (nothing volatile is alive), the
    # frame keeps rsp aligned (5 pushes + 0x160), the tick gate is read through model +0x58 -> +0x440 -> +0x98
    text = tick
    assert [text[a] for a in (0x3070e43, 0x3070e44, 0x3070e45, 0x3070e47, 0x3070e49)] == ['push rbp', 'push rdi', 'push r12', 'push r14', 'push r15']
    assert text[0x3070e50] == 'sub rsp, 0x160' and text[0x3070e5e] == 'mov rdi, rcx' and text[0x3070e57] == 'xor r15d, r15d'
    assert text[0x3070eb3].startswith('call 0x') and text[0x3070eb8] == 'test al, al' and text[0x3070eba] == 'jne 0x3071364'
    assert text[0x3070ecd] == 'mov rax, qword ptr [rdi + 0x58]' and text[0x3070ed1] == 'mov rcx, qword ptr [rax + 0x440]'
    assert text[0x3070ed8] == 'mov eax, dword ptr [rdi + 0x884]' and text[0x3070ede] == 'cmp dword ptr [rcx + 0x98], eax'
    assert text[0x3070ff0] == 'lea r14, [rdi + 0x858]'
    # the game's "here is your station": copies 0x38 bytes of the record to model +0x858
    text = sites.View(x, FOUND['set_station'], *F_SET_STATION)
    assert text.start == FOUND['set_station']
    assert text[0x306c308] == 'push rbx' and text[0x306c30a] == 'sub rsp, 0x90' and text[0x306c315] == 'mov rbx, rcx'
    assert text[0x306c370] == 'movups xmmword ptr [rbx + 0x858], xmm0' and text[0x306c392] == 'movsd qword ptr [rbx + 0x888], xmm1'
    assert text[0x306c3ac].startswith('jmp 0x')
    return sigs


def at(base, off):
    return '%s ptr [%s + %s]' % (base[0], base[1], hex(off) if off > 9 else off) if off else '%s ptr [%s]' % base


WRITES = {
    'place_send': ([at(('dword', 'rax'), o) for o in (COUNT, REACHED, HUNG, PLACE_HIT, PLACE_MISS)] + [at(('dword', 'r10'), E_COPY + REC_X), at(('dword', 'r10'), E_COPY + REC_Z)] + [at(('xmmword', 'r10'), E_TPL + p) for p in (0, 0x10, 0x20)]
                   + [at(('xmmword', 'r10'), E_COPY + p) for p in (0, 0x10, 0x20)]
                   + [at(('dword', 'r10'), o) for o in (E_C5C, E_C63, E_SEQ)] + ['dword ptr [rsp + 0x28]', 'dword ptr [r13]', 'dword ptr [r13 + 8]']),
    'recheck': [at(('dword', 'rax'), o) for o in (CHECKS, FORCED)] + [at(('dword', 'r10'), E_DIRTY), 'dword ptr [rbx + 0x88]'],
    'station': [at(('dword', 'rax'), JOBS_DONE), at(('dword', 'r10'), E_PENDING), 'qword ptr [rdx]'],
    'walk_to': [at(('dword', 'rax'), o) for o in (CARROTS, CARROT_SEEN, CARROTS_NEG)] + ['dword ptr [rdx]', 'dword ptr [rdx + 8]'],
    'ghost_pass': [at(('dword', 'rax'), GHOST_SKIPS)],
}
REGISTERS = {
    'place_send': ('rax', 'rcx', 'ecx', 'edx', 'rdx', 'r8', 'r10', 'r11', 'r11d', 'r13', 'xmm0', 'xmm1', 'xmm2', 'xmm4'),
    'recheck': ('rax', 'rcx', 'r10', 'r11'),
    'station': ('rax', 'rcx', 'ecx', 'rdx', 'r10', 'r11'),
    'walk_to': ('rax', 'r10', 'r11', 'r11d', 'xmm0', 'xmm1', 'xmm2', 'xmm4', 'xmm5'),  # keeps rcx / rdx / r8 / r9 (arguments)
    'ghost_pass': ('rax', 'rcx', 'ecx', 'rdx', 'r8', 'r9', 'r10'),  # (never r14 / r15 / rdi / rbx / r12 / r13 / rbp / rsp: the replaced code and the loop use them)
}


def check_decode(x):
    for name, rva, tail, leave, kind in SITES:
        code = HEAD + b'\0' * 8 + tail
        end = 0
        copied = False
        for ins in x.cs.disasm(code, 0):
            end = ins.address + ins.size
            line = '%s %s' % (ins.mnemonic, ins.op_str)
            assert ins.mnemonic in ('movabs', 'cmp', 'je', 'jne', 'jbe', 'jb', 'ja', 'jle', 'jge', 'jmp', 'mov', 'movzx', 'test', 'lea', 'add',
                                    'movss', 'xorps', 'ucomiss', 'comiss', 'subss', 'mulss', 'addss', 'divss', 'cvtss2si', 'cvtsi2ss', 'cmovg',
                                    'cmovl', 'neg', 'movaps', 'movups', 'lock inc', 'lock dec', 'inc', 'dec', 'call', 'maxss', 'minss', 'jns', 'xor'), line
            assert 'xmm3' not in line, line  # an argument of the call after cave 1
            assert 'xmm5' not in line or name == 'walk_to', line
            if name not in ('place_send', 'walk_to'):
                assert 'xmm' not in line, line
            if ins.mnemonic == 'jmp' and '[' in ins.op_str:  # cave 5: the one jump out, to the address the script wrote (past the pair handler)
                assert name == 'ghost_pass' and ins.op_str == 'qword ptr [rax + 0x%x]' % GHOST_TO, line
                continue
            if ins.mnemonic.startswith('j'):
                assert 0 <= int(ins.op_str, 16) <= len(code), 'jump out of the cave'
                continue
            if ins.mnemonic == 'call':
                assert name == 'station' and line == 'call qword ptr [rax + 0x30]' and end == len(code), line
                continue
            dest = ins.op_str.split(',')[0]
            if ins.mnemonic in ('cmp', 'test', 'ucomiss', 'comiss'):
                continue
            if '[' in dest:
                assert dest in WRITES[name], line
                if dest in ('dword ptr [r13]', 'dword ptr [r13 + 8]'):
                    assert copied, 'the place is written before r13 points to the copy: ' + line
            else:
                assert dest in REGISTERS[name], line
                if dest == 'r13':
                    assert line == 'lea r13, [r10 + 0x30]', line
                    copied = True
        assert end == len(code)
        assert copied == (name == 'place_send')
        print('cave %s decodes: %d bytes' % (name, len(code)))


def native_test():
    k32 = ctypes.windll.kernel32
    k32.VirtualAlloc.restype = ctypes.c_void_p
    k32.VirtualAlloc.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_uint32, ctypes.c_uint32]
    table = (ctypes.c_ubyte * (TABLE_SIZE + 0x40))()
    tbl = ctypes.addressof(table)
    mem = k32.VirtualAlloc(None, 0x3000, 0x3000, 0x40)
    state = (ctypes.c_ubyte * 0x90)()
    # f(rcx -> r12 = model, rdx -> r13 = place record, r8d -> [rsp+0x28] = wait, r9 = where to put the wait afterwards), rbx = C
    # returns the record pointer the game would hand on
    stub = (bytes.fromhex('53' '4154' '4155' '4883ec40' '4989cc' '4989d5' '4489442428' '4c894c2430' '48bb') + struct.pack('<Q', ctypes.addressof(state))
            + HEAD + struct.pack('<Q', tbl) + CAVES[0][1]
            + bytes.fromhex('4c89e8' '8b4c2428' '488b542430' '890a' '4883c440' '415d' '415c' '5b' 'c3'))
    ctypes.memmove(mem, stub, len(stub))
    run = ctypes.CFUNCTYPE(ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint32, ctypes.c_void_p)(mem)
    # g(rcx -> rbx = C)
    stub2 = bytes.fromhex('53' '4889cb') + HEAD + struct.pack('<Q', tbl) + CAVES[1][1] + bytes.fromhex('5b' 'c3')
    ctypes.memmove(mem + 0x1000, stub2, len(stub2))
    run2 = ctypes.CFUNCTYPE(None, ctypes.c_void_p)(mem + 0x1000)
    # h(rcx -> rdi = model); rsp is aligned as in the game's routine
    stub3 = bytes.fromhex('57' '4883ec20' '4889cf') + HEAD + struct.pack('<Q', tbl) + CAVES[2][1] + bytes.fromhex('4883c420' '5f' 'c3')
    ctypes.memmove(mem + 0x2000, stub3, len(stub3))
    run3 = ctypes.CFUNCTYPE(None, ctypes.c_void_p)(mem + 0x2000)
    U1, U2 = 0x7001000, 0x7002000
    model = (ctypes.c_ubyte * 0x400)()
    record = (ctypes.c_ubyte * 0x30)()
    waited = ctypes.c_uint32()
    checks = 0
    keep = []  # (the place tables of the cases stay alive)
    FILL = (0x11111111, 0x22222222, 0x33333333, 0x44444444, 0x55555555, 0x66666666, 0x77777777, 0x88888888, 0x99999999)

    def fill(enable, entries):
        ctypes.memset(tbl, 0, TABLE_SIZE + 0x40)
        struct.pack_into('<I', table, ENABLE, enable)
        for i, e in enumerate(entries):
            u, ox, oz, ax, az, p, lane, n = e[:8]
            struct.pack_into('<Q6fiIIf', table, LIST + ENTRY * i, u, ox, oz, ax, az, p, lane, n, 0, 5, e[8] if len(e) > 8 else 0.0)
            if len(e) > 9:
                struct.pack_into('<2f', table, LIST + ENTRY * i + E_G, e[9], e[10])

    def go(enable, entries, unit, x, z, hung=0, wait=15, places=None, nplaces=None, places_on=None):
        fill(enable, entries)
        if places is not None:  # 085i: the table of places of the unit (places_on: the unit that has it, default the model's): records (model, x, z)
            on = [en[0] for en in entries].index(places_on if places_on is not None else unit)
            ptab = (ctypes.c_ubyte * max(16, PLACE_REC * len(places)))()
            for i, (pm, px, pz) in enumerate(places):
                struct.pack_into('<Qff', ptab, PLACE_REC * i, pm, px, pz)
            keep.append(ptab)
            struct.pack_into('<Q', table, LIST + ENTRY * on + E_PLACES, ctypes.addressof(ptab) if nplaces != 'null' else 0)
            struct.pack_into('<I', table, LIST + ENTRY * on + E_NPLACES, len(places) if nplaces in (None, 'null') else nplaces)
        struct.pack_into('<Q', model, UNIT, unit)
        struct.pack_into('<3f9I', record, 0, x, 7.25, z, *FILL)
        struct.pack_into('<Q', record, REC_OBJECT, hung)
        ctypes.memset(ctypes.addressof(state), 0, 0x90)
        struct.pack_into('<I', state, C_KIND, 0x68)
        struct.pack_into('<B', state, C_HAS_KIND, 1)
        before = bytes(record)
        out = run(ctypes.addressof(model), ctypes.addressof(record), wait, ctypes.addressof(waited))
        assert bytes(record) == before, "the game's own record must never change"
        assert struct.unpack_from('<I', table, REACHED)[0] == 1
        slot = [e[0] for e in entries].index(unit) if unit in [e[0] for e in entries] else None
        if slot is not None:  # the template is kept for every listed unit, also when the mod is switched off
            entry = LIST + ENTRY * slot
            assert bytes(table[entry + E_TPL:entry + E_TPL + 0x30]) == before
            assert struct.unpack_from('<III', table, entry + E_C5C) == (0x68, 1, 1)
        if out == ctypes.addressof(record):
            got = bytes(record)
        else:
            assert out == tbl + LIST + ENTRY * slot + E_COPY, 'the copy belongs to the unit'
            got = bytes(table[out - tbl:out - tbl + 0x30])
            assert got[4:8] == before[4:8] and got[12:] == before[12:], 'only x and z may differ'
        nx, y, nz = struct.unpack_from('<3f', got, 0)
        assert bytes(table[TABLE_SIZE:]) == bytes(0x40)
        go.table = struct.unpack_from('<II', table, PLACE_HIT)[0], struct.unpack_from('<I', table, PLACE_MISS)[0]  # (hits, misses of this call)
        return round(nx, 3), round(nz, 3), struct.unpack_from('<I', table, COUNT)[0], waited.value, struct.unpack_from('<I', table, HUNG)[0]

    e = [(U2, 0.0, 0.0, 1.0, 0.0, 9.0, 0.0, 9), (U1, 100.0, 50.0, 1.0, 0.0, 4.0, 1.8, 2)]  # U2: closed; U1: lanes across x
    one = [(U1, 100.0 - 50000.0, 50.0, 1.0, 0.0, 100000.0, 6.0, 1, -0.5)]  # one lane of 6 m, centred on x = 100
    cases = [
        # enable, entries, unit, x, z -> x, z, counted, wait
        (1, e, U1, 100.0, 50.0, 100.0, 50.0, 1, 5),  # middle block stays
        (1, e, U1, 101.9, 44.0, 101.9, 44.0, 1, 5),
        (1, e, U1, 102.1, 44.0, 103.9, 44.0, 1, 5),  # first block to the right: + 1.8
        (1, e, U1, 103.9, 60.0, 105.7, 60.0, 1, 5),
        (1, e, U1, 106.5, 50.0, 110.1, 50.0, 1, 5),  # second block: + 3.6
        (1, e, U1, 96.0, 50.0, 94.2, 50.0, 1, 5),  # first block to the left
        (1, e, U1, 120.0, 50.0, 123.6, 50.0, 1, 5),  # far out: limited to the second block
        (1, e, U1, 60.0, 50.0, 56.4, 50.0, 1, 5),
        (1, e, U2, 120.0, 50.0, 120.0, 50.0, 0, 15),  # lanes closed
        (1, e, 0x7003000, 120.0, 50.0, 120.0, 50.0, 0, 15),  # unit not listed
        (1, e, 0, 120.0, 50.0, 120.0, 50.0, 0, 15),  # model without a unit
        (0, e, U1, 120.0, 50.0, 120.0, 50.0, 0, 15),  # switched off
        (1, [], U1, 120.0, 50.0, 120.0, 50.0, 0, 15),  # empty list
        (1, [(U1, 100.0, 50.0, 0.0, 1.0, 4.0, 1.5, 3)], U1, 97.0, 56.5, 97.0, 59.5, 1, 5),  # lanes across z
        (1, [(U1, 100.0, 50.0, 0.0, -1.0, 4.0, 1.5, 3)], U1, 97.0, 56.5, 97.0, 59.5, 1, 5),  # the axis may point either way
        (1, [(U1, 0.0, 0.0, 0.6, 0.8, 5.0, 2.0, 4)], U1, 6.0, 8.0, 8.4, 11.2, 1, 5),  # slanted: s = 10, n = 2, shift 4
        (1, one, U1, 100.5, 50.0, 103.5, 50.0, 1, 5),  # one lane: right of the line + 3
        (1, one, U1, 130.0, 50.0, 133.0, 50.0, 1, 5),
        (1, one, U1, 99.5, 50.0, 96.5, 50.0, 1, 5),  # left of the line - 3
        (1, one, U1, 70.0, 50.0, 67.0, 50.0, 1, 5),
        # closing up by G = -0.1 around the walker's line: s = 6.5 -> 3.6 - 0.65; s = -4 -> -1.8 + 0.4
        (1, [(U1, 100.0, 50.0, 1.0, 0.0, 4.0, 1.8, 2, 0.0, -0.1, 0.0)], U1, 106.5, 50.0, 109.45, 50.0, 1, 5),
        (1, [(U1, 100.0, 50.0, 1.0, 0.0, 4.0, 1.8, 2, 0.0, -0.1, 0.0)], U1, 96.0, 50.0, 94.6, 50.0, 1, 5),
        (1, [(U1, 100.0, 50.0, 1.0, 0.0, 4.0, 1.8, 2, 0.0, -0.1, 0.0)], U1, 100.0, 50.0, 100.0, 50.0, 1, 5),
        # one lane (origin 50000 m to the left, S0 = 50000): x = 130 -> + 3 - 3 = 130; x = 90 -> - 3 + 1 = 88
        (1, [one[0] + (-0.1, 50000.0)], U1, 130.0, 50.0, 130.0, 50.0, 1, 5),
        (1, [one[0] + (-0.1, 50000.0)], U1, 90.0, 50.0, 88.0, 50.0, 1, 5),
    ]
    for enable, entries, unit, x, z, wx, wz, wc, ww in cases:
        got = go(enable, entries, unit, x, z)
        assert (abs(got[0] - wx) < 1e-3 * max(1, abs(wx)) and abs(got[1] - wz) < 1e-3 and got[2:] == (wc, ww, 0)), ((enable, unit, x, z), got, (wx, wz, wc, ww))
        checks += 5
    assert go(1, e, U1, 106.5, 50.0, hung=0x1234) == (106.5, 50.0, 0, 15, 1)  # a place that hangs on an object is left alone
    assert go(1, e, U1, 106.5, 50.0, wait=0) == (110.1, 50.0, 1, 0, 0)  # a wait of 0 stays 0
    assert go(1, e, U1, 106.5, 50.0, wait=3) == (110.1, 50.0, 1, 3, 0)  # a shorter wait stays
    got = go(1, [(U1, 100.0, 50.0, 1.0, 0.0, 4.0, 1.8, 2)], U1, 3.0e12, 50.0)
    assert got[0] > 2.9e12 and got[2:] == (0, 5, 0)  # absurd place: untouched
    full = [(0x9000000 + i, 0, 0, 1, 0, 4, 1, 1) for i in range(MAX_UNITS - 1)] + [(U1, 100.0, 50.0, 1.0, 0.0, 4.0, 1.8, 2)]
    assert go(1, full, U1, 104.0, 50.0) == (105.8, 50.0, 1, 5, 0)  # the last entry of a full list
    checks += 5

    # cave 1, 085i: the per-model table of places (hit = the table's x / z; miss = the old formula; empty / no / absurd table = the old formula; bounded loop)
    me1 = ctypes.addressof(model)
    others = [(0x111, 1.0, 2.0), (0x222, 3.0, 4.0)]
    assert go(1, e, U1, 106.5, 50.0, places=others + [(me1, 500.0, 700.0)]) == (500.0, 700.0, 1, 5, 0) and go.table == (1, 0)  # hit: x / z from the table, counted as shifted
    assert go(1, e, U1, 106.5, 50.0, places=[(me1, 500.0, 700.0)] + others) == (500.0, 700.0, 1, 5, 0) and go.table == (1, 0)  # the first record
    assert go(1, e, U1, 106.5, 50.0, places=others) == (110.1, 50.0, 1, 5, 0) and go.table == (0, 1)  # miss: the old formula, counted
    assert go(1, e, U1, 106.5, 50.0, places=[(me1, 500.0, 700.0)], nplaces=0) == (110.1, 50.0, 1, 5, 0) and go.table == (0, 0)  # empty table
    assert go(1, e, U1, 106.5, 50.0, places=[(me1, 500.0, 700.0)], nplaces='null') == (110.1, 50.0, 1, 5, 0) and go.table == (0, 0)  # records but no pointer
    assert go(1, e, U1, 106.5, 50.0) == (110.1, 50.0, 1, 5, 0) and go.table == (0, 0)  # no table at all
    far = [(0x1000 + i, 1.0, 2.0) for i in range(PLACE_CAP - 1)]
    assert go(1, e, U1, 106.5, 50.0, places=far + [(me1, 500.0, 700.0)]) == (500.0, 700.0, 1, 5, 0) and go.table == (1, 0)  # the last record that counts (256th)
    assert go(1, e, U1, 106.5, 50.0, places=far + [(0x1, 0.0, 0.0), (me1, 500.0, 700.0)], nplaces=PLACE_CAP) == (110.1, 50.0, 1, 5, 0) and go.table == (0, 1)  # a record beyond the count: not looked at
    assert go(1, e, U1, 106.5, 50.0, places=far + [(0x1, 0.0, 0.0), (me1, 500.0, 700.0)]) == (110.1, 50.0, 1, 5, 0) and go.table == (0, 0)  # 257 records: more than the cave knows = no table
    assert go(1, e, U1, 106.5, 50.0, places=[(me1, 500.0, 700.0)], places_on=U2) == (110.1, 50.0, 1, 5, 0) and go.table == (0, 0)  # the table belongs to another unit
    assert go(1, e, U2, 120.0, 50.0, places=[(me1, 500.0, 700.0)]) == (120.0, 50.0, 0, 15, 0) and go.table == (0, 0)  # lanes closed (L = 0): the table is not read
    assert go(0, e, U1, 106.5, 50.0, places=[(me1, 500.0, 700.0)]) == (106.5, 50.0, 0, 15, 0) and go.table == (0, 0)  # switched off
    assert go(1, e, U1, 106.5, 50.0, hung=0x1234, places=[(me1, 500.0, 700.0)]) == (106.5, 50.0, 0, 15, 1) and go.table == (0, 0)  # a place on an object is left alone
    assert go(1, e, U1, 106.5, 50.0, wait=3, places=[(me1, 500.0, 700.0)]) == (500.0, 700.0, 1, 3, 0)  # the wait is shortened as before
    checks += 13 * 2

    # cave 2
    group = (ctypes.c_ubyte * 0x40)()
    models = (ctypes.c_uint64 * 2)(ctypes.addressof(model), 0)
    TIME = struct.unpack('<I', struct.pack('<f', 123.5))[0]

    def recheck(enable, entries, unit, dirty, count=1, with_group=True, with_list=True, first=True):
        fill(enable, entries)
        slot = [e[0] for e in entries].index(unit) if unit in [e[0] for e in entries] else None
        if slot is not None:
            struct.pack_into('<I', table, LIST + ENTRY * slot + E_DIRTY, dirty)
        struct.pack_into('<Q', model, UNIT, unit)
        models[0] = ctypes.addressof(model) if first else 0
        ctypes.memset(ctypes.addressof(group), 0, 0x40)
        struct.pack_into('<I', group, G_COUNT, count)
        struct.pack_into('<Q', group, G_LIST, ctypes.addressof(models) if with_list else 0)
        ctypes.memset(ctypes.addressof(state), 0x5a, 0x90)
        struct.pack_into('<Q', state, C_GROUP, ctypes.addressof(group) if with_group else 0)
        struct.pack_into('<I', state, C_LAST, TIME)
        before = bytes(state)
        run2(ctypes.addressof(state))
        assert bytes(state[:C_LAST]) == before[:C_LAST] and bytes(state[C_LAST + 4:]) == before[C_LAST + 4:]
        assert struct.unpack_from('<I', table, CHECKS)[0] == 1 and bytes(table[TABLE_SIZE:]) == bytes(0x40)
        left = struct.unpack_from('<I', table, LIST + ENTRY * slot + E_DIRTY)[0] if slot is not None else None
        last = struct.unpack_from('<I', state, C_LAST)[0]
        assert last in (LONG_AGO, TIME)
        return last == LONG_AGO, left, struct.unpack_from('<I', table, FORCED)[0]

    assert struct.unpack('<f', struct.pack('<I', LONG_AGO))[0] == -1000000.0
    assert recheck(1, e, U1, 1) == (True, 0, 1)  # marked: the time goes far back, the mark is taken
    assert recheck(1, e, U1, 0) == (False, 0, 0)  # not marked
    assert recheck(1, e, U2, 1) == (True, 0, 1)  # also for a unit whose lanes are closed (it has to close up)
    assert recheck(0, e, U1, 1) == (False, 1, 0)  # switched off
    assert recheck(1, e, 0x7003000, 1) == (False, None, 0)  # unit not listed
    assert recheck(1, e, U1, 1, count=0) == (False, 1, 0)  # no models
    assert recheck(1, e, U1, 1, with_group=False) == (False, 1, 0)
    assert recheck(1, e, U1, 1, with_list=False) == (False, 1, 0)
    assert recheck(1, e, U1, 1, first=False) == (False, 1, 0)
    assert recheck(1, e, 0, 1) == (False, None, 0)  # model without a unit
    checks += 10

    # cave 3
    calls = []
    callback = ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_void_p)(lambda m, r: calls.append((m, r)))
    jobs = (ctypes.c_ubyte * (JOB * 8))()
    me = ctypes.addressof(model)

    def station(enable, unit, listed, pending, njobs, with_jobs=True, jobs_any=1):
        fill(enable, e)
        struct.pack_into('<I', table, JOBS_ANY, jobs_any)
        struct.pack_into('<Q', table, SET_STATION, ctypes.cast(callback, ctypes.c_void_p).value)
        entry = LIST + ENTRY * 1  # U1
        struct.pack_into('<I', table, entry + E_PENDING, pending)
        struct.pack_into('<Q', table, entry + E_JOBS, ctypes.addressof(jobs) if with_jobs else 0)
        struct.pack_into('<I', table, entry + E_NJOBS, njobs)
        ctypes.memset(ctypes.addressof(jobs), 0, JOB * 8)
        for i, m in enumerate(listed):
            struct.pack_into('<Q', jobs, JOB * i, m)
        struct.pack_into('<Q', model, UNIT, unit)
        del calls[:]
        run3(me)
        left = [struct.unpack_from('<Q', jobs, JOB * i)[0] for i in range(len(listed))]
        return list(calls), left, struct.unpack_from('<I', table, entry + E_PENDING)[0], struct.unpack_from('<I', table, JOBS_DONE)[0]

    J = ctypes.addressof(jobs)
    assert station(1, U1, [0x111, me, 0x333], 3, 3) == ([(me, J + JOB + JOB_RECORD)], [0x111, 0, 0x333], 2, 1)  # its job is taken, once
    assert station(1, U1, [me], 1, 1) == ([(me, J + JOB_RECORD)], [0], 0, 1)
    assert station(1, U1, [0x111, 0x222, me], 3, 2) == ([], [0x111, 0x222, me], 3, 0)  # beyond the count: not looked at
    assert station(1, U1, [0x111, 0x222], 2, 2) == ([], [0x111, 0x222], 2, 0)  # no job for this model
    assert station(1, U1, [me], 0, 1) == ([], [me], 0, 0)  # nothing pending
    assert station(1, U1, [me], 1, 1, with_jobs=False)[0] == []  # no job memory
    assert station(0, U1, [me], 1, 1) == ([], [me], 1, 0)  # switched off
    assert station(1, U1, [me], 1, 1, jobs_any=0) == ([], [me], 1, 0)  # no jobs anywhere: the list is not searched
    assert station(1, U2, [me], 1, 1) == ([], [me], 1, 0)  # the jobs belong to another unit
    assert station(1, 0x7003000, [me], 1, 1) == ([], [me], 1, 0)  # unit not listed
    assert station(1, 0, [me], 1, 1) == ([], [me], 1, 0)
    checks += 11

    # cave 4: g4(rcx = model, rdx = target, r8 = mark) is called from a stub, so [rsp] is the stub's return address;
    # the table names that address as the marching call (or not)
    mem4 = k32.VirtualAlloc(None, 0x1000, 0x3000, 0x40)
    body = HEAD + struct.pack('<Q', tbl) + SITES[3][2] + bytes.fromhex('c3')
    # stub(rcx, rdx, r8 = out): push r8 / r9 = mark / call body (skipping the jmp) / jmp over the body /
    # pop r10 / out = rcx, rdx, r8, r9 as they came back from the cave
    head = bytes.fromhex('4150' '49b9') + struct.pack('<Q', 0x9999) + bytes.fromhex('e8') + struct.pack('<i', 5) + bytes.fromhex('e9') + struct.pack('<i', len(body))
    stub = head + body + bytes.fromhex('415a' '49890a' '49895208' '4d894210' '4d894a18' 'c3')
    ctypes.memmove(mem4, stub, len(stub))
    ret_addr = mem4 + len(head) - 5
    run4 = ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p)(mem4)
    back = (ctypes.c_uint64 * 4)()
    target = (ctypes.c_ubyte * 0x20)()
    model4 = (ctypes.c_ubyte * 0x400)()

    def walk(enable, unit, x, z, q=0.25, wq=5.0, t0=-10.0, t1=10.0, kl=-3, kh=2, ours=True, listed=True, sl=-1e30, sh=1e30, lag=0.0, at=(0.0, 0.0), walkers=1):
        ctypes.memset(tbl, 0, TABLE_SIZE + 0x40)
        struct.pack_into('<I', table, ENABLE, enable)
        struct.pack_into('<I', table, WALKERS, walkers)
        struct.pack_into('<Q', table, CARROT_RET, ret_addr if ours else ret_addr + 1)
        struct.pack_into('<f', table, HALF, 0.5)
        if listed:
            e = LIST + ENTRY  # second entry
            struct.pack_into('<Q', table, LIST, 0x7005000)
            struct.pack_into('<Q', table, e, U1)
            # lanes across x (a = (1, 0)), walking along z (b = (0, 1)), origin (100, 50)
            struct.pack_into('<10f2i2f', table, e + E_WQ, wq, 100.0, 50.0, 1.0, 0.0, 0.0, 1.0, t0, t1, q, kl, kh, sl, sh)
            struct.pack_into('<2f', table, e + E_WLAG, lag, 1.0)
        struct.pack_into('<3f', model4, 0x88, at[0], 0.0, at[1])
        struct.pack_into('<Q', model4, UNIT, unit)
        struct.pack_into('<3f5I', target, 0, x, 7.0, z, 1, 2, 3, 4, 5)
        before = bytes(target)
        run4(ctypes.addressof(model4), ctypes.addressof(target), ctypes.addressof(back))
        assert tuple(back) == (ctypes.addressof(model4), ctypes.addressof(target), ctypes.addressof(back), 0x9999), 'arguments kept'
        assert bytes(target)[4:8] == before[4:8] and bytes(target)[12:] == before[12:], 'only x and z may change'
        nx, ny, nz = struct.unpack_from('<3f', target, 0)
        assert bytes(table[TABLE_SIZE:]) == bytes(0x40)
        return round(nx, 3), round(nz, 3), struct.unpack_from('<I', table, CARROTS)[0], struct.unpack_from('<I', table, CARROT_SEEN)[0]

    # Q = 5: lanes centred at s = -12.5, -7.5, -2.5, 2.5, 7.5, 12.5 (k = -3 .. 2); squeeze q = 0.25
    assert walk(1, U1, 101.0, 50.0) == (102.125, 50.0, 1, 1)  # s = 1 -> lane k = 0 (c = 2.5): 2.5 + (1 - 2.5) * 0.25 = 2.125
    assert walk(1, U1, 99.0, 55.0) == (97.875, 55.0, 1, 1)  # s = -1 -> k = -1 (c = -2.5): -2.5 + 1.5 * 0.25 = -2.125
    assert struct.unpack_from('<I', table, CARROTS_NEG)[0] == 1, 'counted as a point for a lane with k < 0'
    walk(1, U1, 101.0, 50.0)
    assert struct.unpack_from('<I', table, CARROTS_NEG)[0] == 0, 'k = 0 is not counted'
    assert walk(1, U1, 104.0, 41.0) == (102.875, 41.0, 1, 1)  # s = 4: k = 0: 2.5 + 1.5 * 0.25 = 2.875 (t = -9: inside)
    assert walk(1, U1, 130.0, 50.0) == (112.5 + 100 + 4.375 - 100, 50.0, 1, 1)  # s = 30 -> limited to k = 2 (c = 12.5): 12.5 + 17.5 * 0.25
    assert walk(1, U1, 60.0, 50.0) == (100 - 12.5 - 6.875, 50.0, 1, 1)  # s = -40 -> k = -3 (c = -12.5): -12.5 - 27.5 * 0.25
    assert walk(1, U1, 101.0, 50.0, q=0.0) == (102.5, 50.0, 1, 1)  # q = 0: on the middle of the lane
    assert walk(1, U1, 101.0, 39.0) == (101.0, 39.0, 0, 1)  # t = -11: before the zone
    assert walk(1, U1, 101.0, 61.0) == (101.0, 61.0, 0, 1)  # t = 11: behind it
    assert walk(1, U1, 101.0, 50.0, wq=0.0) == (101.0, 50.0, 0, 1)  # not a walker now
    assert walk(1, U1, 101.0, 50.0, ours=False) == (101.0, 50.0, 0, 0)  # another caller (its target may be a stored record)
    assert walk(0, U1, 101.0, 50.0) == (101.0, 50.0, 0, 0)  # switched off
    assert walk(1, U1, 101.0, 50.0, walkers=0) == (101.0, 50.0, 0, 0)  # no walker anywhere: the list is not searched
    assert walk(1, U2, 101.0, 50.0) == (101.0, 50.0, 0, 0)  # unit not listed
    assert walk(1, 0, 101.0, 50.0) == (101.0, 50.0, 0, 0)  # model without a unit
    assert walk(1, U1, 101.0, 50.0, listed=False) == (101.0, 50.0, 0, 0)  # empty list
    got = walk(1, U1, 3.0e12, 50.0, t0=-1e30, t1=1e30)
    assert abs(got[0] - 3.0e12) < 1e6 and got[1:] == (50.0, 0, 1), got  # absurd: untouched
    assert walk(1, U1, 101.0, 50.0, sl=-20.0, sh=20.0) == (102.125, 50.0, 1, 1)  # s = 1: in front of the standing unit
    assert walk(1, U1, 125.0, 50.0, sl=-20.0, sh=20.0) == (125.0, 50.0, 0, 1)  # s = 25: beside it, free
    assert walk(1, U1, 75.0, 50.0, sl=-20.0, sh=20.0) == (75.0, 50.0, 0, 1)  # s = -25: beside it on the other side
    # the lag: model at (101, 45), point 5 m ahead, pulled 1.125 m sideways: 0.5 x 1.125 nearer -> 4.4375 ahead
    assert walk(1, U1, 101.0, 50.0, lag=0.5, at=(101.0, 45.0)) == (102.125, 49.438, 1, 1)
    # never under 1 m ahead, never further than it was
    assert walk(1, U1, 101.0, 50.0, lag=10.0, at=(101.0, 45.0)) == (102.125, 46.0, 1, 1)
    assert walk(1, U1, 101.0, 50.0, lag=10.0, at=(101.0, 49.5)) == (102.125, 50.0, 1, 1)
    assert walk(1, U1, 99.0, 55.0, lag=0.5, at=(99.0, 51.0)) == (97.875, 54.438, 1, 1)  # pulled the other way: |d|
    checks += 23

    # cave 5 (ghost_pass): g5(rcx -> r15 = entity A, rdx -> r14 = entity B) returns 0 when the replaced instructions would run (the cave fell
    # through) and 1 when the cave jumped to the address in GHOST_TO (a skipped pair); r14 / r15 are kept
    mem5 = k32.VirtualAlloc(None, 0x1000, 0x3000, 0x40)
    pre5 = bytes.fromhex('4156' '4157' '4989cf' '4989d6')  # push r14 / push r15 / mov r15, rcx / mov r14, rdx
    body5 = HEAD + struct.pack('<Q', tbl) + CAVES[4][1]
    fall5 = bytes.fromhex('31c0' '415f' '415e' 'c3')  # xor eax, eax / pop r15 / pop r14 / ret
    skip5 = bytes.fromhex('b801000000' '415f' '415e' 'c3')  # mov eax, 1 / ...
    ctypes.memmove(mem5, pre5 + body5 + fall5 + skip5, len(pre5) + len(body5) + len(fall5) + len(skip5))
    skip_at = mem5 + len(pre5) + len(body5) + len(fall5)
    run5 = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p)(mem5)
    ent_a, ent_b = (ctypes.c_ubyte * 0x400)(), (ctypes.c_ubyte * 0x400)()
    AL1, AL2 = 0x7100000, 0x7200000
    UA, UB, UC = 0x7001000, 0x7002000, 0x7003000

    def ghost(listed, ua=UA, ub=UB, aa=AL1, ab=AL1, count=None, to=None):
        ctypes.memset(tbl, 0, TABLE_SIZE + 0x40)
        for i, u in enumerate(listed):
            struct.pack_into('<Q', table, GHOST_LIST + 8 * i, u)
        struct.pack_into('<I', table, GHOSTS, len(listed) if count is None else count)
        struct.pack_into('<Q', table, GHOST_TO, skip_at if to is None else to)
        for ent, u, al in ((ent_a, ua, aa), (ent_b, ub, ab)):
            ctypes.memset(ctypes.addressof(ent), 0x5a, 0x400)
            struct.pack_into('<Q', ent, ALLIANCE, al)
            struct.pack_into('<Q', ent, ENTITY_UNIT, u)
        before = bytes(ent_a) + bytes(ent_b)
        got = run5(ctypes.addressof(ent_a), ctypes.addressof(ent_b))
        assert bytes(ent_a) + bytes(ent_b) == before, 'the cave writes nothing of the entities'
        assert bytes(table[TABLE_SIZE:]) == bytes(0x40)
        assert bytes(table[GHOST_LIST:GHOST_LIST + 8 * GHOST_MAX]) == b''.join(struct.pack('<Q', u) for u in listed) + bytes(8 * (GHOST_MAX - len(listed))), 'the list is only read'
        return got, struct.unpack_from('<I', table, GHOST_SKIPS)[0]

    assert ghost([UA]) == (1, 1)  # friends, different units, A is a ghost: skipped, counted
    assert ghost([UB]) == (1, 1)  # B is a ghost
    assert ghost([UC, UA, UB]) == (1, 1)  # both listed (and another): once
    assert ghost([UC]) == (0, 0)  # neither listed: the replaced instructions run
    assert ghost([UA], ab=AL2) == (0, 0)  # enemies: as ever, even with a ghost in the pair
    assert ghost([UA], ub=UA) == (0, 0)  # the same unit (a ghost unit's own models): as ever
    assert ghost([]) == (0, 0)  # the list is empty
    assert ghost([UA], count=0) == (0, 0)  # count 0 with a stale list (the script switched it off): nothing
    assert ghost([UA], ua=0, ub=UA) == (0, 0)  # an entity without a unit
    assert ghost([UA], ub=0) == (0, 0)
    assert ghost([0], ua=0, ub=UB) == (0, 0)  # a 0 in the list never matches a missing unit
    one_more = [0x7010000 + 0x1000 * i for i in range(GHOST_MAX - 1)] + [UB]
    assert ghost(one_more) == (1, 1)  # the last (32nd) place of the list is read
    assert ghost(one_more, count=GHOST_MAX - 1) == (0, 0)  # beyond the count: not looked at
    assert ghost([UA], count=GHOST_MAX + 1) == (0, 0)  # a count the cave does not know: nothing
    checks += 14
    print('native test: %d checks passed' % checks)


def lua_script(x, sigs, release=False, narrow_off=False):
    lua = open(os.path.join(HERE, 'lanes.lua.tpl'), encoding='utf8').read()
    if narrow_off:  # 085h: the comparison build, narrowing off (big walkers take the ordinary lanes / columns path); never a release
        assert not release, '--narrow-off is a test build only'
        assert lua.count('\tNARROW = true,') == 1, 'TUNE.NARROW = true is not in the template exactly once'
        lua = lua.replace('\tNARROW = true,', '\tNARROW = false,')
    for (name, rva, tail, leave, kind), sig in zip(SITES, sigs):
        assert sites.expect_bytes(x, rva) == sig, name
    values = {
        'VERSION': VERSION, 'FIND_SITE': sites.lua_find_site(),
        'RELEASE': 'true' if release else 'false', 'PACK': RELEASE_PACK if release else PACK,
        'BUILD_KIND': 'release build' if release else 'TEST build',
        'BUILD_NOTE': '' if release else (' TEST BUILD NARROW OFF' if narrow_off else ' TEST BUILD'),
        'SITES': ',\n'.join('\t' + sites.lua_site(x, name, tail=tail) for name, rva, tail, leave, kind in SITES),
        'STATION_FN': sites.lua_site(x, 'set_station'),
        'HEAD': probe.lua_bytes(HEAD), 'TABLE_SIZE': TABLE_SIZE, 'ENABLE': ENABLE, 'COUNT': COUNT, 'LIST': LIST, 'ENTRY': ENTRY,
        'REACHED': REACHED, 'HUNG': HUNG, 'CHECKS': CHECKS, 'FORCED': FORCED, 'SET_STATION': SET_STATION, 'POOL': POOL, 'JOBS_DONE': JOBS_DONE,
        'E_OX': E_OX, 'E_OZ': E_OZ, 'E_AX': E_AX, 'E_AZ': E_AZ, 'E_P': E_P, 'E_L': E_L, 'E_N': E_N, 'E_DIRTY': E_DIRTY, 'E_WAIT': E_WAIT,
        'E_BIAS': E_BIAS, 'E_TPL': E_TPL, 'E_C5C': E_C5C, 'E_C63': E_C63, 'E_SEQ': E_SEQ, 'E_PENDING': E_PENDING, 'E_JOBS': E_JOBS,
        'E_NJOBS': E_NJOBS, 'JOB': JOB, 'JOB_RECORD': JOB_RECORD, 'CAP': CAP, 'POOL_SIZE': POOL_SIZE, 'MAX_UNITS': MAX_UNITS,
        'CARROT_RET': CARROT_RET, 'CARROTS': CARROTS, 'CARROT_SEEN': CARROT_SEEN, 'HALF': HALF, 'CARROT_SITE': sites.lua_site(x, 'carrot_ret'), 'CARROTS_NEG': CARROTS_NEG,
        'E_WQ': E_WQ, 'E_WOX': E_WOX, 'E_WOZ': E_WOZ, 'E_WAX': E_WAX, 'E_WAZ': E_WAZ, 'E_WBX': E_WBX, 'E_WBZ': E_WBZ, 'E_WT0': E_WT0,
        'E_WT1': E_WT1, 'E_WC': E_WC, 'E_WKL': E_WKL, 'E_WKH': E_WKH, 'E_WSL': E_WSL, 'E_WSH': E_WSH, 'E_G': E_G, 'E_S0': E_S0, 'E_WLAG': E_WLAG, 'E_WMIN': E_WMIN,
        'EXPENDABLE': '{ ' + ', '.join("['%s'] = true" % k for k in expendable_units()) + ' }',
        'KINDS': unitkinds.lua_table(),
        'FIRE_GUNS': unitkinds.lua_fire_guns(),
        'ETHEREAL': unitkinds.lua_ethereal(),
        'SKV_SITE': probe.skaven_cave.P_SITE, 'SKV_SITE_OFF': probe.skaven_cave.SITE_OFF, 'SKV_CAVE_A': probe.lua_bytes(probe.skaven_cave.CAVE_A),
        'SKV_CAVE_B': probe.lua_bytes(probe.skaven_cave.CAVE_B), 'SKV_CAVE_C': probe.lua_bytes(probe.skaven_cave.CAVE_C),
        'SKV_OFF_ENABLE': probe.skaven_cave.OFF_ENABLE, 'SKV_OFF_ALL': probe.skaven_cave.OFF_ALL, 'SKV_OFF_SHOOTERS': probe.skaven_cave.OFF_SHOOTERS,
        'SKV_OFF_BLOCKERS': probe.skaven_cave.OFF_BLOCKERS, 'SKV_MAX_SHOOTERS': probe.skaven_cave.MAX_SHOOTERS, 'SKV_MAX_BLOCKERS': probe.skaven_cave.MAX_BLOCKERS,
        'LEAD_NORM': unitkinds.lead_norm()[0], 'LEAD_ROUTE': sites.parse(sites.PATTERNS['is_routing'])[0], 'LEAD_ROUTE_AT': sites.parse(sites.PATTERNS['is_routing'])[1],
        'LEAD_WAVER': sites.parse(sites.PATTERNS['is_wavering'])[0], 'LEAD_WAVER_AT': sites.parse(sites.PATTERNS['is_wavering'])[1],
        'P_UID': probe.skaven_cave.P_UID, 'UID_OFF': probe.skaven_cave.UID_OFF, 'WALKERS': WALKERS, 'JOBS_ANY': JOBS_ANY,
        'PLACE_HIT': PLACE_HIT, 'PLACE_MISS': PLACE_MISS, 'E_PLACES': E_PLACES, 'E_NPLACES': E_NPLACES, 'PLACE_REC': PLACE_REC, 'PLACE_CAP': PLACE_CAP,
        'GHOSTS': GHOSTS, 'GHOST_SKIPS': GHOST_SKIPS, 'GHOST_TO': GHOST_TO, 'GHOST_LIST': GHOST_LIST, 'GHOST_MAX': GHOST_MAX, 'GHOST_SKIP_OFF': GHOST_SKIP_OFF,
    }
    for key, value in values.items():
        assert ('--%s--' % key) in lua, key
        lua = lua.replace('--%s--' % key, str(value))
    left = re.findall(r'--[A-Z_0-9]+--', lua)
    assert not left, left
    return lua.encode('utf8')


def check_leadership(x):
    """The morale block patterns match once each in the installed exe and S = B + 0x40; the leadership that counts as x1
    comes from the tables (median of the vanilla units' base leadership)."""
    found = []
    for name in ('is_routing', 'is_wavering'):
        pattern, mark = sites.parse(sites.PATTERNS[name])
        hits = x.find(pattern, 3)
        assert len(hits) == 1, '%s: %d matches in the installed exe, expected 1' % (name, len(hits))
        found.append(int.from_bytes(x.read(hits[0] + mark, 4), 'little'))
    assert found[1] == found[0] + 0x40, 'S is not B + 0x40'
    guns = unitkinds.direct_fire_units()
    print('line of fire: %d direct-fire guns: %s' % (len(guns), ', '.join(guns)))
    median, count = unitkinds.lead_norm()
    print('leadership: morale block B = 0x%x (unit + 0x%x = current leadership); x1 = %d (median base leadership of %d vanilla units)' % (found[0], found[0] + 0x48, median, count))
    return found[0]


def leadership_test():
    """The median is computed from the rows it is given (not a fixed number), with the filters of the brief."""
    def table(shift):
        lands = [dict(key='l%d' % i, morale=m + shift if m else 0) for i, m in enumerate((40, 50, 60, 70, 80, 0, 200, 90, 999, 999, 999, 999, 999))]
        rows = [dict(unit='u%d' % i, land_unit='l%d' % i, caste='melee_infantry', is_naval=False) for i in range(8)]
        rows += [dict(unit='x_ror_u', land_unit='l8', caste='melee_infantry', is_naval=False), dict(unit='x_lord', land_unit='l9', caste='lord', is_naval=False),
                 dict(unit='x_ship', land_unit='l10', caste='melee_infantry', is_naval=True), dict(unit='x_boss', land_unit='l11', caste='melee_infantry', is_naval=False),
                 dict(unit='x_nolu', land_unit='missing', caste='melee_infantry', is_naval=False)]
        return rows, lands
    assert unitkinds.median_leadership(*table(0)) == (70, 7), unitkinds.median_leadership(*table(0))  # 40 50 60 70 80 90 200
    assert unitkinds.median_leadership(*table(10)) == (80, 7)  # every number follows the table
    return 2


LISTING = os.path.join(HERE, 'caves_listing.asm')


def cave_listing(x):
    """The disassembly of the five caves, byte for byte as the battle script installs them."""
    lines = ['; Military Drill %s - the machine code of the five code caves, byte for byte as the battle script installs them.' % VERSION,
             '; Written by "python lanes.py listing"; every build and check fails when the caves no longer match this file.',
             '; Each cave starts with "movabs rax, <table>": the 8 zero bytes are the address of the mod\'s table, written in',
             '; when the cave is installed (memreader Plus code_cave(site, cave)). A cave ends by running off its last byte:',
             '; memreader Plus places the game instructions it replaced and the jump back into the game after it. Offsets are',
             '; from the start of the cave.',
             '; Each place is found in Warhammer3.exe by the byte pattern of the same name in sites.py (it must match exactly once).']
    for name, rva, tail, leave, kind in SITES:
        code = HEAD + b'\0' * 8 + tail
        lines += ['', '; cave "%s" (%s), %d bytes' % (name, 'at the entry of the game routine' if kind == 'entry' else 'inside the game routine', len(code))]
        for ins in x.cs.disasm(code, 0):
            lines.append('%04x  %-36s %s %s' % (ins.address, ins.bytes.hex(' '), ins.mnemonic, ins.op_str))
    return '\n'.join(lines) + '\n'


def check_listing(x):
    have = open(LISTING, encoding='utf8').read() if os.path.exists(LISTING) else ''
    assert have == cave_listing(x), 'the caves differ from caves_listing.asm: review the change, then run "python lanes.py listing"'
    print('caves match caves_listing.asm')


def main():
    x = probe.exe()
    sigs = signatures(x)
    print('site bytes read from the installed exe')
    check_decode(x)
    native_test()
    if len(sys.argv) > 1 and sys.argv[1] == 'listing':
        with open(LISTING, 'w', encoding='utf8', newline='\n') as f:
            f.write(cave_listing(x))
        print('written', LISTING)
        return
    check_listing(x)
    check_leadership(x)
    leadership_test()
    print('leadership median test passed (computed from the rows it is given)')
    if len(sys.argv) > 2 and sys.argv[1] == 'build':
        release = '--release' in sys.argv[3:]
        lua = lua_script(x, sigs, release, '--narrow-off' in sys.argv[3:])
        out = sys.argv[2]
        mct = open(os.path.join(HERE, 'mct_settings.lua'), 'rb').read()
        pack, script, path = (RELEASE_PACK, RELEASE_SCRIPT, RELEASE_BATTLE_PATH) if release else (PACK, SCRIPT, BATTLE_PATH)
        probe.write_pack(os.path.join(out, pack), {path: lua, MCT_PATH: mct})
        with open(os.path.join(out, script), 'wb') as f:
            f.write(lua)
        print('%s: script %d lines, version %s' % (pack, lua.count(b'\n'), VERSION))


if __name__ == '__main__':
    main()
