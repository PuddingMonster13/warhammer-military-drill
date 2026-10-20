"""Offline test of the place probe's battle script (Lua 5.1 through lupa, fake memreader Plus, a fake "game").

    python place_probe_mocktest.py <out_dir of 'place_probe.py build'>     -> must end with ALL PASS

The fake game keeps each model's place in two spots: in a table the unit points to (the real place: a changed
number makes the model walk there) and in the model itself (a copy the game writes again every tick).
"""
import os
import struct
import sys
import tempfile

import lupa.lua51 as lupa

import place_probe as pp

SCRIPT = open(os.path.join(os.path.abspath(sys.argv[1]), pp.SCRIPT), encoding='utf8').read()
LOG = 'units_place_probe_log.txt'
TIMER = 'units_place_probe_tick'
BASE = 0x140000000
UID_FIELD = 0x3ea0
UID_CODE = 0x142f169c3
CALLER = BASE + 0x2dd4ccc
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

    def f(self, address):
        return struct.unpack('<f', self.read(address, 4))[0]


def run_battle(mem, hooks, game_code, wrong_build=False):
    lua = lupa.LuaRuntime(unpack_returned_tuples=True, encoding='latin-1')
    g = lua.globals()
    phases, timers = {}, {}
    units = []
    for index, count in enumerate((1, 24)):  # a lone character and a unit of 24
        unit_at = mem.alloc(0x4800)
        mem.write(unit_at + UID_FIELD, struct.pack('<I', 100 + index))
        list_at = mem.alloc(8 * count)
        places = mem.alloc(16 * count)  # the real places: x, z, 0, 0 per model
        mem.write(unit_at + 0x100, struct.pack('<Q', places))
        mem.write(unit_at + 0x108, struct.pack('<Q', 0x1234))  # something that is no readable address
        models = []
        for s in range(count):
            model = mem.alloc(0xb40)
            x, z = 100.0 + s, 50.0 + index
            mem.write(model + 0x74, struct.pack('<I', 1))
            mem.write(model + 0x88, struct.pack('<3f', x, 2.0, z))
            mem.write(model + 0x2e8, struct.pack('<Q', unit_at))
            mem.write(model + 0x7f8, struct.pack('<3f', x, 2.0, z))  # the copy
            mem.write(places + 16 * s, struct.pack('<2f', x, z))
            mem.write(list_at + 8 * s, struct.pack('<Q', model))
            models.append(model)
        mem.write(unit_at + 0xa4, struct.pack('<I', count))
        mem.write(unit_at + 0xa8, struct.pack('<Q', list_at))
        interface = mem.alloc(0x10)
        mem.write(interface + 8, struct.pack('<Q', unit_at))
        units.append(dict(at=unit_at, models=models, places=places, uid=100 + index, interface=interface))

    lua.execute('''
        function make_unit(interface, uid, key)
            local u = { _interface = interface }
            function u:unique_ui_id() return uid end
            function u:type() return key end
            function u:is_idle() return true end
            function u:is_moving() return false end
            return u
        end
        function make_list(items) return { count = function() return #items end, item = function(_, i) return items[i] end } end
    ''')
    us = [g.make_unit(u['interface'], u['uid'], 'unit_key_%d' % n) for n, u in enumerate(units)]
    army = lua.eval('function(units) return { units = function() return units end } end')(g.make_list(lua.table(*us)))
    alliance = lua.eval('function(armies) return { armies = function() return armies end } end')(g.make_list(lua.table(army)))
    battle = lua.eval('function(alliances) return { alliances = function() return alliances end } end')(g.make_list(lua.table(alliance)))
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
    mr['find_pattern'] = lambda pattern: (UID_CODE - pp.probe.skaven_cave.UID_OFF, 1)
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
        at = mem.alloc(len(code))
        mem.write(at, code.encode('latin-1'))
        hooks[int(p)] = at
        return at

    mr['write'], mr['read_original'], mr['hook_info'], mr['code_cave'] = write, read_original, hook_info, code_cave
    wrap = lua.eval('function(f) return function(...) return f(...) end end')
    for name in [k for k in mr.keys() if callable(mr[k])]:
        mr[name] = wrap(mr[name])
    g.memreader_plus = mr
    lua.execute(SCRIPT)
    return dict(lua=lua, phases=phases, timers=timers, units=units)


def game_tick(mem, tbl, units):
    """The fake game: copies the real place into the model, and walks a model that is not at its place 0.5 m
    toward it after noting a "walk to" call in the ring (what the cave does in the real game)."""
    for u in units:
        for s, model in enumerate(u['models']):
            px, pz = struct.unpack('<2f', mem.read(u['places'] + 16 * s, 8))
            mem.write(model + 0x7f8, struct.pack('<3f', px, 2.0, pz))
            x, y, z = struct.unpack('<3f', mem.read(model + 0x88, 12))
            dx = px - x
            if abs(dx) > 0.01:
                n = struct.unpack('<I', mem.read(tbl + pp.INDEX, 4))[0]
                at = tbl + pp.RING_AT + (n % pp.RING) * pp.ENTRY
                mem.write(at, struct.pack('<QQ8f', CALLER, model, px, 2.0, pz, 0, 0, 0, 0, 0))
                mem.write(tbl + pp.INDEX, struct.pack('<I', n + 1))
                step = max(-0.5, min(0.5, dx))
                mem.write(model + 0x88, struct.pack('<f', x + step))


def main():
    work = tempfile.mkdtemp()
    os.chdir(work)
    log = lambda: open(LOG, encoding='utf8').read()
    game_code = {BASE + s[1]: sig for s, sig in zip(pp.SITES, pp.signatures(pp.probe.exe()))}
    mem = Memory()
    mem.regions[UID_CODE & ~0xFFF] = bytearray(0x1000)
    mem.write(UID_CODE, struct.pack('<I', UID_FIELD))

    hooks = {}
    run_battle(mem, hooks, game_code, wrong_build=True)
    expect(not hooks and 'PROBE OFF' in log() and 'not the analysed one' in log(), 'wrong build must switch the probe off')

    hooks = {}
    b = run_battle(mem, hooks, game_code)
    expect('PROBE OFF' not in log() and 'cave installed' in log() and len(hooks) == 1, log())
    cave = hooks[BASE + pp.SITES[0][1]]
    tbl = struct.unpack('<Q', mem.read(cave + 2, 8))[0]
    expect(mem.read(cave, len(pp.HEAD) + 8 + len(pp.SITES[0][2])) == pp.HEAD + struct.pack('<Q', tbl) + pp.SITES[0][2], 'cave bytes')
    tick = b['timers'][TIMER]
    for _ in range(10):  # nothing before the fighting
        tick()
    expect('subject' not in log(), 'waits for the fighting')
    b['phases']['Deployed']()
    unit = b['units'][1]
    m0 = unit['models'][0]
    moved_seen = far = 0
    for t in range(1, 280):
        if TIMER not in b['timers']:
            break
        game_tick(mem, tbl, b['units'])
        tick()
        if t == 40:
            moved_seen = mem.f(m0 + 0x88)
        if 151 <= t <= 174:
            far = max(far, mem.f(m0 + 0x88) - 100.0)
    text = log()
    expect('PROBE OFF' not in text, text)
    expect('subject unit: A1.1.2 unit_key_1, 24 models' in text and 'unit_key_0' not in text, 'picks the unit of 24, not the lone character')
    expect('place of A1.1.2 unit_key_1 model 0' in text and ': 100.00 2.00 51.00' in text, text)
    expect('model+0x88 (z 8 bytes later)' in text and 'model+0x7f8 (z 8 bytes later)' in text and '[unit+0x100]+0x0 (z 4 bytes later)' in text, text)
    expect('MOVED 3 models 4.0 m aside' in text and abs(moved_seen - 104.0) < 0.01, 'models moved: %r' % moved_seen)
    expect(('WALK ORDER for A1.1.2 unit_key_1 model 0 from %016x: target numbers 100.00 2.00 51.00' % CALLER) in text, text)
    expect('model+0x7f8 (z 8 bytes later): 100.00 / 51.00  <- STILL THE OLD PLACE' in text, text)
    expect('[unit+0x100]+0x0 (z 4 bytes later): 100.00 / 51.00  <- STILL THE OLD PLACE' in text, text)
    expect('    model+0x88 (z 8 bytes later): 103.50 / 51.00' in text and 'model+0x88 (z 8 bytes later): 103.50 / 51.00  <-' not in text
           and 'TRY 2: model+0x88' not in text, 'the position itself is not a place')
    expect('distance of the models from their old places (m): 0.0 0.0 0.0' in text, 'the models walked back')
    expect(('calls of "walk to" for them: %016x x' % CALLER) in text, text)
    expect('2 spots will be tried' in text, text)
    expect('TRY 1: [unit+0x100]+0x0 (z 4 bytes later) changed from 100.00 to 102.50' in text, text)
    expect(abs(far - 2.5) < 0.01, 'the model walked to the changed place: %r' % far)
    expect('try 1: the number had stayed changed; put back to 100.00' in text, text)
    expect('TRY 2: model+0x7f8 (z 8 bytes later) changed from 100.00 to 102.50' in text and 'try 2: the game had already overwritten the number (100.00)' in text, text)
    expect(abs(mem.f(unit['places']) - 100.0) < 0.01 and abs(mem.f(m0 + 0x88) - 100.0) < 0.01, 'everything back in place')
    expect(('callers for the moved models: %016x x' % CALLER) in text and 'PROBE FINISHED' in text and TIMER not in b['timers'], text)

    # next battle of the session: the cave is reused
    before = dict(hooks)
    run_battle(mem, hooks, game_code)
    expect(hooks == before and 'reusing it' in log() and 'PROBE OFF' not in log(), log())
    print('mock test: %d checks, ALL PASS' % checks)


if __name__ == '__main__':
    main()
