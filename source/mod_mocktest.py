"""Offline test of the mod's battle script (Lua 5.1 through lupa, fake memreader Plus, fake armies).

    python mod_mocktest.py <out_dir of 'mod.py build'>     -> must end with ALL PASS
"""
import os
import struct
import sys
import tempfile

import lupa.lua51 as lupa

import mod

SCRIPT = open(os.path.join(os.path.abspath(sys.argv[1]), mod.SCRIPT), encoding='utf8').read()
LOG = 'units_pass_through_log.txt'
TIMER = 'units_pass_through_tick'
BASE = 0x140000000
UID_FIELD = 0x3ea0
UID_CODE = 0x142f169c3  # where the fake find_pattern "finds" the unit id code
checks = 0


def expect(cond, what):
    global checks
    checks += 1
    assert cond, what


class U32:
    def __init__(self, value):
        self.value = value


class Memory:
    def __init__(self):
        self.regions = {}
        self.next = 0x20000000000

    def alloc(self, size):
        at = self.next
        self.next += (size + 0xFFF) & ~0xFFF
        self.regions[at] = bytearray(size)
        return at

    def find(self, address, size):
        for at, data in self.regions.items():
            if at <= address and address + size <= at + len(data):
                return data, address - at
        raise lupa.LuaError('failed to read memory')

    def read(self, address, size):
        data, off = self.find(int(address), size)
        return bytes(data[off:off + size])

    def write(self, address, raw):
        data, off = self.find(int(address), len(raw))
        data[off:off + len(raw)] = raw

    def u32(self, address):
        return struct.unpack('<I', self.read(address, 4))[0]


def run_battle(mem, hooks, game_code, wrong_build=False, refuse=(), n_units=2):
    lua = lupa.LuaRuntime(unpack_returned_tuples=True, encoding='latin-1')
    g = lua.globals()
    phases, timers = {}, {}

    # fake game objects: 2 alliances x 1 army x n_units units
    units = []
    for a in range(2):
        for index in range(n_units):
            unit_at = mem.alloc(0x4000)
            uid = 100 + a * 1000 + index
            mem.write(unit_at + UID_FIELD, struct.pack('<I', uid))
            interface = mem.alloc(0x10)
            mem.write(interface + 8, struct.pack('<Q', unit_at))
            units.append(dict(a=a, index=index, at=unit_at, uid=uid, interface=interface, ammo=20, moving=False, melee=False, routing=False, idle=True, bad_id=False))

    lua.execute('''
        function make_unit(interface, key, get)
            local u = { _interface = interface }
            function u:unique_ui_id() return get('uid') + (get('bad_id') and 1 or 0) end
            function u:type() return key end
            function u:ammo_left() return get('ammo') end
            function u:is_moving() return get('moving') end
            function u:is_idle() return get('idle') end
            function u:is_in_melee() return get('melee') end
            function u:is_routing() return get('routing') end
            return u
        end
        function make_list(items) return { count = function() return #items end, item = function(_, i) return items[i] end } end
    ''')
    lists = []
    for a in range(2):
        us = [g.make_unit(u['interface'], 'unit_key_%d_%d' % (a, u['index']), (lambda name, u=u: u[name])) for u in units if u['a'] == a]
        army = lua.eval('function(units) return { units = function() return units end } end')(g.make_list(lua.table(*us)))
        lists.append(lua.eval('function(armies) return { armies = function() return armies end } end')(g.make_list(lua.table(army))))
    battle = lua.eval('function(alliances) return { alliances = function() return alliances end } end')(g.make_list(lua.table(*lists)))
    bm = lua.eval('function(battle) return { battle = battle } end')(battle)
    bm['register_phase_change_callback'] = lambda self, name, f: phases.__setitem__(name, f)
    bm['repeat_callback'] = lambda self, f, ms, name: timers.__setitem__(name, f)
    bm['remove_callback'] = lambda self, name: timers.pop(name, None)
    g.bm = bm

    mr = lua.table()
    mr['plus_version'] = '0.10.0'
    mr['base'] = BASE
    mr['add'] = lambda p, n: int(p) + int(n)
    mr['is_null'] = lambda p: int(p) == 0
    mr['tostring'] = lambda p: '%016x' % int(p)
    mr['alloc'] = lambda n: mem.alloc(int(n))
    mr['game_alloc'] = lambda n: mem.alloc(int(n))
    mr['read'] = lambda p, off, n: mem.read(int(p) + int(off), int(n)).decode('latin-1')
    mr['read_pointer'] = lambda p, off=0: struct.unpack('<Q', mem.read(int(p) + int(off), 8))[0]
    mr['read_uint32'] = lambda p, off=0: struct.unpack('<I', mem.read(int(p) + int(off), 4))[0]
    mr['ud_topointer'] = lambda unit: unit['_interface']
    mr['find_pattern'] = lambda pattern: (UID_CODE - mod.probe.skaven_cave.UID_OFF, 1)
    mr['uint32'] = lambda n: U32(int(n))

    def write(p, off, value):
        if isinstance(value, U32):
            raw = struct.pack('<I', value.value)
        elif isinstance(value, str):
            raw = value.encode('latin-1')
        else:
            raw = struct.pack('<Q', int(value))
        mem.write(int(p) + int(off), raw)

    def read_original(p, off, n):
        raw = game_code.get(int(p) + int(off), b'\0' * int(n))[:int(n)]
        return (b'\x90' * int(n) if wrong_build else raw).decode('latin-1')

    def hook_info(p):
        if int(p) not in hooks:
            return None
        t = lua.table()
        t['kind'] = 'cave'
        t['cave'] = hooks[int(p)]
        return t

    def code_cave(p, code):
        name = next(s[0] for s in mod.SITES if BASE + s[1] == int(p))
        if name in refuse:
            raise lupa.LuaError('refused: test')
        at = mem.alloc(len(code))
        mem.write(at, code.encode('latin-1'))
        hooks[int(p)] = at
        return at

    mr['write'], mr['read_original'], mr['hook_info'], mr['code_cave'] = write, read_original, hook_info, code_cave
    # the script asks type(x) == 'function': hand it real Lua functions, not Python objects
    wrap = lua.eval('function(f) return function(...) return f(...) end end')
    for name in [k for k in mr.keys() if callable(mr[k])]:
        mr[name] = wrap(mr[name])
    g.memreader_plus = mr
    lua.execute(SCRIPT)
    return dict(lua=lua, phases=phases, timers=timers, units=units)


def table_of(mem, hooks):
    cave_at = hooks[BASE + mod.SITES[0][1]]
    return struct.unpack('<Q', mem.read(cave_at + 2, 8))[0]


def listed(mem, tbl):
    """The Unit pointers the caves would see."""
    out = []
    while len(out) <= mod.MAX_UNITS:
        v = struct.unpack('<Q', mem.read(tbl + mod.LIST + 8 * len(out), 8))[0]
        if not v:
            return out
        out.append(v)
    raise AssertionError('list without an end')


def main():
    work = tempfile.mkdtemp()
    os.chdir(work)
    log = lambda: open(LOG, encoding='utf8').read()
    sigs, extra = mod.signatures(mod.probe.exe())
    game_code = {BASE + s[1]: sig for s, sig in zip(mod.SITES, sigs)}
    game_code.update({BASE + rva: sig for (name, rva), sig in zip(mod.EXTRA, extra)})
    mem = Memory()
    mem.regions[UID_CODE & ~0xFFF] = bytearray(0x1000)
    mem.write(UID_CODE, struct.pack('<I', UID_FIELD))

    # 1. wrong game build: nothing installed
    hooks = {}
    run_battle(mem, hooks, game_code, wrong_build=True)
    expect(not hooks and 'MOD OFF' in log() and 'not the analysed one' in log(), 'wrong build must switch the mod off')

    # 2. normal battle
    hooks = {}
    b = run_battle(mem, hooks, game_code)
    expect('MOD OFF' not in log(), log())
    expect(len(hooks) == len(mod.SITES) and log().count('cave installed') == len(mod.SITES), 'all caves installed')
    tbl = table_of(mem, hooks)
    for name, rva, tail, leave, kind in mod.SITES:
        want = mod.HEAD + struct.pack('<Q', tbl) + tail.replace(mod.EXIT_MARK, struct.pack('<Q', BASE + (leave or 0))).replace(
            mod.CALL_MARK, struct.pack('<Q', BASE + mod.FLINCH)).replace(mod.TABLE_MARK, struct.pack('<Q', tbl))
        expect(mem.read(hooks[BASE + rva], len(want)) == want, 'cave bytes of ' + name)
        expect(not any(mark in want for mark in (mod.EXIT_MARK, mod.CALL_MARK, mod.TABLE_MARK)), 'every address filled in at ' + name)
    got = struct.unpack('<4f', mem.read(tbl + mod.K_SMALL, 16))
    want = (mod.K_SMALL_VALUE, mod.K_BIG_VALUE, mod.MAX_K_VALUE, 1.0 / mod.MAX_K_VALUE)
    expect(all(abs(x - y) < 1e-6 * y for x, y in zip(got, want)), 'weights in the table: %r' % (got,))
    chance = struct.unpack('<f', mem.read(tbl + mod.CHANCE, 4))[0]
    expect(abs(chance - mod.CHANCE_VALUE) < 1e-6 and mem.u32(tbl + mod.STEP_ON) == 0, 'step animation chance in the table')
    expect(list(struct.unpack('<8I', mem.read(tbl + mod.REMAP, 32))) == mod.STEP_SLOTS, 'step animations in the table')
    expect(mem.u32(tbl + mod.STEP_MASK) == mod.STEP_MASK_VALUE, 'step throttle in the table')
    tick = b['timers'][TIMER]
    for _ in range(5):
        tick()
    expect(mem.u32(tbl + mod.ENABLE) == 0 and listed(mem, tbl) == [], 'off and empty before the fighting')
    b['phases']['Deployed']()
    u = b['units']
    ptr = [x['at'] for x in u]
    expect(mem.u32(tbl + mod.ENABLE) == 1 and listed(mem, tbl) == ptr, 'all four idle units give way at the start')
    expect('fighting starts, mod ON: 4 units' in log() and 'units that give way now (4 of 4): A1.1.1 unit_key_0_0, A1.1.2 unit_key_0_1, A2.1.1 unit_key_1_0, A2.1.2 unit_key_1_1' in log(), log())

    def after_tick():
        tick()
        return listed(mem, tbl)

    u[0]['moving'] = True
    expect(after_tick() == ptr[1:], 'a moving unit does not give way')
    u[1]['melee'] = True
    expect(after_tick() == ptr[2:], 'a unit in melee does not give way')
    u[2]['routing'] = True
    expect(after_tick() == ptr[3:], 'a routing unit does not give way')
    u[3]['idle'] = False
    expect(after_tick() == [], 'a unit that is not idle does not give way')
    u[0]['moving'], u[1]['melee'], u[2]['routing'], u[3]['idle'] = False, False, False, True
    expect(after_tick() == ptr, 'all standing again')
    u[2]['ammo'] = 19  # unit 2 shoots once
    expect(after_tick() == [ptr[0], ptr[1], ptr[3]], 'a unit that just shot does not give way')
    for _ in range(25):
        expect(after_tick() == [ptr[0], ptr[1], ptr[3]], 'still counted as shooting')
    expect(after_tick() == ptr, 'gives way again 5 s after its last shot')

    # counters show up in the log
    mem.write(tbl + mod.C_B_GIVES, struct.pack('<6I', 40, 2, 7, 5, 9, 300))
    for _ in range(25):
        tick()
    expect('4 of 4 units give way | standing model gave way (second of the pair) +40; standing model gave way (first of the pair) +2; walker not blocked (place 1) +7; walker not blocked (place 2) +5; walker touched a standing model +300; step animation asked for +9' in log(), log())

    # a unit whose game object no longer answers to its id is dropped at the next look for units
    u[1]['bad_id'] = True
    for _ in range(50):
        tick()
    expect(listed(mem, tbl) == [ptr[0], ptr[2], ptr[3]], 'unconfirmed unit left out')
    u[1]['bad_id'] = False
    for _ in range(50):
        tick()
    expect(listed(mem, tbl) == ptr and 'script cost:' in log(), 'unit back, cost reported')

    b['phases']['Complete']()
    expect(mem.u32(tbl + mod.ENABLE) == 0 and listed(mem, tbl) == [] and 'battle complete, mod off' in log(), 'off at the end')
    expect('totals of this battle: standing model gave way (second of the pair) 40;' in log() and TIMER not in b['timers'], log())

    # 3. next battle of the same game session: caves reused, mod off until the fighting
    mem.write(tbl + mod.ENABLE, struct.pack('<I', 1))  # as if the last battle had been left without its end
    mem.write(tbl + mod.STEP_ON, struct.pack('<I', 1))
    mem.write(tbl + mod.LIST, struct.pack('<Q', 0x1234))
    before = dict(hooks)
    b = run_battle(mem, hooks, game_code)
    expect(hooks == before and 'reusing them' in log() and 'MOD OFF' not in log(), log())
    expect(mem.u32(tbl + mod.ENABLE) == 0 and mem.u32(tbl + mod.STEP_ON) == 0 and listed(mem, tbl) == [], 'switched off when the script loads')
    b['phases']['Deployed']()
    expect(mem.u32(tbl + mod.ENABLE) == 1 and len(listed(mem, tbl)) == 4, 'on again')

    # an error in a tick switches the mod off
    b['lua'].execute('bm.battle = nil')
    for _ in range(60):
        if TIMER in b['timers']:
            b['timers'][TIMER]()
    expect('MOD OFF: tick' in log() and mem.u32(tbl + mod.ENABLE) == 0 and listed(mem, tbl) == [] and TIMER not in b['timers'], log())

    # 4. a cave of something else (the probe pack) sits at a site: stay off
    other = dict(hooks)
    other[BASE + mod.SITES[1][1]] = mem.alloc(0x200)
    mem.write(other[BASE + mod.SITES[1][1]] + 2, struct.pack('<Q', tbl))  # same table, other code
    run_battle(mem, other, game_code)
    expect('MOD OFF' in log() and 'is not this version' in log() and 'restart the game' in log(), log())
    expect(mem.u32(tbl + mod.ENABLE) == 0, 'stays off')
    other[BASE + mod.SITES[1][1]] = mem.alloc(0x200)
    mem.write(other[BASE + mod.SITES[1][1]] + 2, struct.pack('<Q', mem.alloc(0x100)))  # a cave with a table of its own
    run_battle(mem, other, game_code)
    expect('MOD OFF' in log() and 'different tables' in log(), log())

    # the routine the caves call is not the analysed one: nothing installed
    changed = dict(game_code)
    changed[BASE + mod.FLINCH] = bytes(32)
    fresh = {}
    run_battle(mem, fresh, changed)
    expect(not fresh and 'MOD OFF' in log() and 'flinch_routine' in log(), log())

    # 5. memreader refuses a cave: the mod stays off
    hooks = {}
    b = run_battle(mem, hooks, game_code, refuse=('blocked_b_by_a',))
    expect('MOD OFF' in log() and 'blocked_b_by_a   NOT installed' in log(), log())
    expect(mem.u32(table_of(mem, hooks) + mod.ENABLE) == 0, 'caves already placed stay switched off')
    if 'Deployed' in b['phases']:
        b['phases']['Deployed']()
    expect(mem.u32(table_of(mem, hooks) + mod.ENABLE) == 0, 'and are not switched on later')
    # ... and in the next battle of that session only a part of the caves is found
    b = run_battle(mem, hooks, game_code)
    expect('only some caves' in log() and mem.u32(table_of(mem, hooks) + mod.ENABLE) == 0, log())

    # 6. more units than the list holds
    hooks = {}
    b = run_battle(mem, hooks, game_code, n_units=mod.MAX_UNITS // 2 + 10)
    b['phases']['Deployed']()
    tbl = table_of(mem, hooks)
    expect(len(listed(mem, tbl)) == mod.MAX_UNITS and 'MOD OFF' not in log(), 'list capped')

    print('mock test: %d checks, ALL PASS' % checks)


if __name__ == '__main__':
    main()
