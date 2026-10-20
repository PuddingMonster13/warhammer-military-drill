"""Offline test of the probe battle script (Lua 5.1 through lupa, fake memreader Plus, fake armies).

    python probe_mocktest.py <out_dir of 'probe.py build'>     -> must end with ALL PASS
"""
import os
import struct
import sys
import tempfile

import lupa.lua51 as lupa

import probe

SCRIPT = open(os.path.join(os.path.abspath(sys.argv[1]), probe.SCRIPT), encoding='utf8').read()
LOG = 'units_pass_probe_log.txt'
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


def run_battle(mem, hooks, game_code, wrong_build=False, refuse=()):
    lua = lupa.LuaRuntime(unpack_returned_tuples=True, encoding='latin-1')
    g = lua.globals()
    phases, timers = {}, {}

    # fake game objects: 2 alliances x 1 army x 2 units, 3 models each
    units = []
    for a in range(2):
        for index in range(2):
            unit_at = mem.alloc(0x4000)
            uid = 100 + a * 10 + index
            mem.write(unit_at + UID_FIELD, struct.pack('<I', uid))
            list_at = mem.alloc(8 * 3)
            models = []
            for s in range(3):
                model = mem.alloc(0x400)
                mem.write(model + 0x74, struct.pack('<I', 1))
                mem.write(model + 0x80, struct.pack('<Q', 0x5000 + a))
                mem.write(model + 0xa0, struct.pack('<f', 0.45))
                mem.write(model + 0xe0, struct.pack('<3f', 3.0, 0.0, 4.0))
                mem.write(model + 0x2e8, struct.pack('<Q', unit_at))
                mem.write(list_at + 8 * s, struct.pack('<Q', model))
                models.append(model)
            mem.write(unit_at + 0xa4, struct.pack('<I', 3))
            mem.write(unit_at + 0xa8, struct.pack('<Q', list_at))
            interface = mem.alloc(0x10)
            mem.write(interface + 8, struct.pack('<Q', unit_at))
            units.append(dict(a=a, index=index, at=unit_at, models=models, uid=uid, interface=interface, ammo=20, moving=False))

    lua.execute('''
        function make_unit(interface, uid, key, ammo_fn, moving_fn)
            local u = { _interface = interface }
            function u:unique_ui_id() return uid end
            function u:type() return key end
            function u:number_of_men_alive() return 3 end
            function u:ammo_left() return ammo_fn() end
            function u:is_moving() return moving_fn() end
            function u:is_idle() return not moving_fn() end
            function u:is_in_melee() return false end
            function u:is_routing() return false end
            function u:is_moving_fast() return false end
            function u:is_under_missile_attack() return false end
            return u
        end
        function make_list(items) return { count = function() return #items end, item = function(_, i) return items[i] end } end
    ''')
    lists = []
    for a in range(2):
        us = [g.make_unit(u['interface'], u['uid'], 'unit_key_%d_%d' % (a, u['index']), (lambda u=u: u['ammo']), (lambda u=u: u['moving'])) for u in units if u['a'] == a]
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
    mr['read_float'] = lambda p, off=0: struct.unpack('<f', mem.read(int(p) + int(off), 4))[0]
    mr['ud_topointer'] = lambda unit: unit['_interface']
    mr['find_pattern'] = lambda pattern: (UID_CODE - probe.skaven_cave.UID_OFF, 1)
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
        name = next(s[0] for s in probe.SITES if BASE + s[1] == int(p))
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
    cave_at = hooks[BASE + probe.SITES[0][1]]
    return struct.unpack('<Q', mem.read(cave_at + 2, 8))[0]


def main():
    work = tempfile.mkdtemp()
    os.chdir(work)
    log = lambda: open(LOG, encoding='utf8').read()
    game_code = {BASE + s[1]: sig for s, sig in zip(probe.SITES, probe.signatures(probe.exe()))}
    mem = Memory()
    mem.regions[UID_CODE & ~0xFFF] = bytearray(0x1000)
    mem.write(UID_CODE, struct.pack('<I', UID_FIELD))

    # 1. wrong game build: nothing installed
    hooks = {}
    run_battle(mem, hooks, game_code, wrong_build=True)
    expect(not hooks and 'PROBE OFF' in log() and 'not the analysed one' in log(), 'wrong build must switch the probe off')

    # 2. normal battle
    hooks = {}
    b = run_battle(mem, hooks, game_code)
    expect('PROBE OFF' not in log(), log())
    expect(len(hooks) == len(probe.SITES) and log().count('cave installed') == len(probe.SITES), 'all caves installed')
    tbl = table_of(mem, hooks)
    for name, rva, tail, leave, kind in probe.SITES:
        want = probe.HEAD + struct.pack('<Q', tbl) + tail.replace(probe.EXIT_MARK, struct.pack('<Q', BASE + (leave or 0)))
        expect(mem.read(hooks[BASE + rva], len(want)) == want, 'cave bytes of ' + name)
        expect(probe.EXIT_MARK not in mem.read(hooks[BASE + rva], len(want)), 'exit filled in at ' + name)
    tick = b['timers']['units_pass_probe_tick']
    for _ in range(30):  # before the fighting: stays in mode 0
        tick()
    expect(mem.u32(tbl + probe.MODE) == 0, 'mode 0 before the fighting')
    b['phases']['Deployed']()
    expect('--- battle 1 since the game was started: mode 0' in log() and 'fighting starts, NOW mode 0' in log() and 'A1.1.1 unit_key_0_0: men 3, unit: idle | first model: kind 1, radius 0.45, mass 0, speed 5.00' in log(), log())

    # the caves "see" pairs: counters and a friendly block of unit 0 (moving) by unit 1 (standing)
    u0, u1, u2 = b['units'][0], b['units'][1], b['units'][2]
    u0['moving'] = True
    mem.write(tbl + probe.H_TOTAL, struct.pack('<4I', 50, 30, 15, 5))
    mem.write(tbl + probe.H_RET_OTHER, struct.pack('<Q', BASE + 0x2f71234))
    mem.write(tbl + probe.B1 + probe.B_OTHER, struct.pack('<I', 7))
    mem.write(tbl + probe.B1 + probe.B_A, struct.pack('<QQ', u0['models'][0], u1['models'][1]))
    mem.write(tbl + probe.G_OTHER, struct.pack('<I', 9))
    for _ in range(25):
        tick()
    text = log()
    expect('[mode 0] pairs handled +50; same unit +30; other friendly unit +15; enemy +5; BLOCKED mover=A other friendly unit +7; partner gate other friendly unit +9' in text, text)
    expect('pair handler caller (other friendly unit): 0000000142f71234 x' in text, text)
    expect('friendly block: A1.1.1 unit_key_0_0 [moving] blocked by A1.1.2 unit_key_0_1 [idle] x1' in text, text)
    expect('latest friendly block: mover A1.1.1 unit_key_0_0 [moving]: kind 1' in text and '|| blocker A1.1.2 unit_key_0_1 [idle]: kind 1' in text, text)

    # a pair the script cannot read must not switch the probe off
    mem.write(tbl + probe.B2 + probe.B_A, struct.pack('<QQ', 0x1234, 0x5678))
    mem.write(tbl + probe.B2 + probe.B_OTHER, struct.pack('<I', 1))
    u0['ammo'] = 18
    for _ in range(25):
        tick()
    expect('friendly block: READ FAILED' in log() and 'PROBE OFF' not in log(), log())

    # battle 1 stays in mode 0; totals every 60 s of fighting (300 ticks)
    for _ in range(250):
        tick()
    expect(mem.u32(tbl + probe.MODE) == 0, 'battle 1 is mode 0 all the time')
    text = log()
    expect('=== totals so far, mode 0: game unchanged (60 s of fighting): pairs handled 50; same unit 30; other friendly unit 15; enemy 5; BLOCKED mover=A other friendly unit 7; BLOCKED mover=B other friendly unit 1; partner gate other friendly unit 9' in text, text)
    expect('units now:' in text and 'unit_key_0_0: men 3, unit: moving,shooting |' in text, 'ammunition use is reported')
    b['phases']['Complete']()
    expect(mem.u32(tbl + probe.MODE) == 0 and 'battle complete' in log(), 'battle 1 ends')
    expect('units_pass_probe_tick' not in b['timers'], 'timer removed')

    # 3. the next battles of the same game session: caves reused, log continued, one mode per battle
    for battle, mode in ((2, 1), (3, 2), (4, 0)):
        mem.write(tbl + probe.MODE, struct.pack('<I', 2))  # as if the last battle had been left without its end
        before = dict(hooks)
        b = run_battle(mem, hooks, game_code)
        text = log()
        expect(hooks == before and text.count('reusing them') == battle - 1 and 'PROBE OFF' not in text, text)
        expect('--- battle 1 since' in text and ('--- battle %d since the game was started: mode %d' % (battle, mode)) in text, 'log continued, battle %d' % battle)
        expect(mem.u32(tbl + probe.MODE) == 0, 'mode 0 while the battle loads and during deployment')
        b['timers']['units_pass_probe_tick']()
        expect(mem.u32(tbl + probe.MODE) == 0, 'still mode 0 before the fighting')
        b['phases']['Deployed']()
        expect(mem.u32(tbl + probe.MODE) == mode and ('fighting starts, NOW mode %d' % mode) in log(), 'battle %d runs in mode %d' % (battle, mode))
        mem.write(tbl + probe.G_SKIP, struct.pack('<I', 4 * (battle - 1)))
        for _ in range(30):
            b['timers']['units_pass_probe_tick']()
        expect(('[mode %d] not picked (mode 2) +4' % mode) in log(), log())
        expect(mem.u32(tbl + probe.MODE) == mode, 'mode kept during the fighting')
        b['phases']['Complete']()
        expect(mem.u32(tbl + probe.MODE) == 0, 'mode 0 after battle %d' % battle)
    expect(mem.u32(tbl + probe.BATTLES) == 4, 'battles counted')

    # 4. a cave of another probe version is in place: stay off
    other = dict(hooks)
    other[BASE + probe.SITES[3][1]] = mem.alloc(0x100)
    run_battle(mem, other, game_code)
    expect('PROBE OFF' in log() and 'restart the game' in log(), log())

    # 5. memreader refuses one site: the others still work
    hooks = {}
    b = run_battle(mem, hooks, game_code, refuse=('partner_gate',))
    expect(len(hooks) == len(probe.SITES) - 1 and 'partner_gate     NOT installed: ' in log() and 'PROBE OFF' not in log(), log())
    b['phases']['Deployed']()
    for _ in range(30):
        b['timers']['units_pass_probe_tick']()
    expect('PROBE OFF' not in log(), log())

    # 6. an error in a tick switches the probe off and leaves mode 0
    tbl = table_of(mem, hooks)
    expect(mem.u32(tbl + probe.MODE) == 0, 'first battle of a fresh game is mode 0')
    mem.write(tbl + probe.MODE, struct.pack('<I', 1))
    b['lua'].execute('bm.battle = nil')
    for _ in range(150):
        if 'units_pass_probe_tick' in b['timers']:
            b['timers']['units_pass_probe_tick']()
    expect('PROBE OFF: tick' in log() and mem.u32(tbl + probe.MODE) == 0 and 'units_pass_probe_tick' not in b['timers'], log())

    print('mock test: %d checks, ALL PASS' % checks)


if __name__ == '__main__':
    main()
