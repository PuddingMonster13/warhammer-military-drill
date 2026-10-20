"""Offline test of the lanes battle script (Lua 5.1 through lupa, fake memreader Plus, fake armies).

    python lanes_mocktest.py <out_dir of 'lanes.py build'>                   -> must end with ALL PASS
    python lanes_mocktest.py <out_dir of 'lanes.py build --release'> --release
        the RELEASE script (military_drill.lua): it starts, makes way, writes no file of its own, and only its start
        and problems reach the game's mod log
"""
import os
import struct
import sys
import tempfile

import lupa.lua51 as lupa

import lanes

RELEASE = '--release' in sys.argv[2:]
SCRIPT = open(os.path.join(os.path.abspath(sys.argv[1]), lanes.RELEASE_SCRIPT if RELEASE else lanes.SCRIPT), encoding='utf8').read()
MODLOG = []  # what the script hands the game's ModLog (lua_mod_log.txt)
LOG = 'units_lanes_log.txt'
TIMER = 'units_lanes_tick'
BASE = 0x140000000
UID_FIELD = 0x3ea0
UID_CODE = 0x142f169c3
checks = 0


def expect(cond, what):
    global checks
    checks += 1
    if not cond and os.path.exists(LOG):
        print(''.join([l for l in open(LOG, encoding='utf8') if 'units with open lanes' not in l][-12:]))
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


# alliance, name, x, z, width, bearing, model radius
ARMY = [(1, 'stand', 0.0, 0.0, 40.0, 0.0, 0.65), (1, 'walk', 0.0, -80.0, 30.0, 0.0, 0.65), (1, 'aside', 200.0, 0.0, 40.0, 0.0, 0.65),
        (2, 'enemy', 0.0, 30.0, 40.0, 180.0, 0.65), (1, 'walk2', -300.0, -80.0, 30.0, 0.0, 0.65),
        (1, 'guns', 0.0, 400.0, 40.0, 0.0, 0.5), (1, 'cart', 300.0, 400.0, 20.0, 0.0, 0.5), (1, 'walk3', -500.0, -80.0, 30.0, 0.0, 0.65)]
# guns: artillery (is_artillery); cart: a chariot whose crew rides (is_war_machine, as for a Doomwheel)
CASTES = {'cart': 'chariot'}


def run_battle(mem, hooks, game_code, wrong_build=False, castes=None, mct=None):
    lua = lupa.LuaRuntime(unpack_returned_tuples=True, encoding='latin-1')
    g = lua.globals()
    phases, timers = {}, {}
    units = {}
    by_alliance = {1: [], 2: []}
    # like the game: CA's string.sub counts UTF-8 characters (runs 24-27 crashed on raw words cut with it);
    # the byte version is string.sub_lua
    lua.execute('''
        string.sub_lua = string.sub
        local bsub = string.sub
        string.sub = function(s, i, j)
            local starts, k = {}, 1
            while k <= #s do
                starts[#starts + 1] = k
                local c = s:byte(k)
                k = k + ((c >= 0xf0 and 4) or (c >= 0xe0 and 3) or (c >= 0xc0 and 2) or 1)
            end
            local n = #starts
            i, j = i or 1, j or -1
            if i < 0 then i = n + i + 1 end
            if j < 0 then j = n + j + 1 end
            if i < 1 then i = 1 end
            if j > n then j = n end
            if i > j then return '' end
            return bsub(s, starts[i], (j < n) and (starts[j + 1] - 1) or #s)
        end
        -- run 31: string.find(s, x, 1, true) broke the game's whole Lua state at load (and loadfast's notes name
        -- match / gsub too): these must never be called by the script
        for _, name in ipairs({ 'find', 'match', 'gmatch', 'gsub', 'lower', 'upper' }) do
            string[name] = function() error('string.' .. name .. ' is not safe in the Lua of the game (run 31)', 2) end
        end
    ''')
    lua.execute('''
        function make_unit(interface, get)
            local u = { _interface = interface }
            local function vec(x, z) return { get_x = function() return x end, get_y = function() return 0 end, get_z = function() return z end } end
            function u:unique_ui_id() return get('uid') end
            function u:type() return get('name') end
            function u:ammo_left() return get('ammo') end
            function u:number_of_men_alive() return get('men') end
            function u:is_moving() return get('moving') end
            function u:is_moving_fast() return get('fast') end
            function u:slow_speed() return 3 end
            function u:fast_speed() return get('fastv') end
            function u:is_idle() return not get('moving') and not get('notidle') end
            function u:is_in_melee() return get('melee') end
            function u:is_routing() return false end
            function u:position() return vec(get('x') + get('jx'), get('z')) end
            function u:ordered_position() return vec(get('gx'), get('gz')) end
            function u:ordered_width() return get('width') end
            function u:bearing() return get('bearing') end
            function u:ordered_bearing() return get('bearing') end
            function u:is_artillery() return get('artillery') end
            function u:is_war_machine() return get('war_machine') end
            function u:is_currently_flying() return false end
            function u:unit_caste() return get('caste') end
            return u
        end
        function make_list(items) return { count = function() return #items end, item = function(_, i) return items[i] end } end
    ''')
    for n, (alliance, name, x, z, width, bearing, radius) in enumerate(ARMY):
        unit_at = mem.alloc(0x4000)
        mem.write(unit_at + UID_FIELD, struct.pack('<I', 100 + n))
        model = mem.alloc(0xc00)
        mem.write(model + 0xa0, struct.pack('<f', radius))
        mem.write(model + 0x88, struct.pack('<3f', x, 0.0, z))
        ctx, clock = mem.alloc(0x448), mem.alloc(0xa0)
        mem.write(model + 0x58, struct.pack('<Q', ctx))
        mem.write(ctx + 0x440, struct.pack('<Q', clock))
        mem.write(clock + 0x98, struct.pack('<I', 1000))  # the battle tick
        list_at = mem.alloc(8)
        mem.write(list_at, struct.pack('<Q', model))
        mem.write(unit_at + 0xa4, struct.pack('<I', 1))
        mem.write(unit_at + 0xa8, struct.pack('<Q', list_at))
        interface = mem.alloc(0x10)
        mem.write(interface + 8, struct.pack('<Q', unit_at))
        u = dict(at=unit_at, model=model, clock=clock, uid=100 + n, name=name, x=x, z=z, gx=x, gz=z, width=width, bearing=bearing, men=100, ammo=0, moving=False, melee=False, caste=(castes or {}).get(name, CASTES.get(name, 'melee_infantry')),
                 artillery=name == 'guns', war_machine=name in ('guns', 'cart'), jx=0.0, fast=False, notidle=False, fastv=6)
        units[name] = u
        by_alliance[alliance].append(g.make_unit(interface, (lambda key, u=u: u[key])))
    lists = []
    for a in (1, 2):
        army = lua.eval('function(units) return { units = function() return units end } end')(g.make_list(lua.table(*by_alliance[a])))
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
    mr['read_uint32'] = lambda p, off=0: struct.unpack('<f', struct.pack('<f', float(struct.unpack('<I', mem.read(int(p) + int(off), 4))[0])))[0]
    mr['read_float'] = lambda p, off=0: struct.unpack('<f', mem.read(int(p) + int(off), 4))[0]
    mr['ud_topointer'] = lambda unit: unit['_interface']
    # the patterns of sites.py are searched in the installed exe; anything else is the unit id pattern
    mr['find_pattern'] = lambda pattern: (game_code['patterns'][pattern] if pattern in game_code['patterns']
                                          else (UID_CODE - lanes.probe.skaven_cave.UID_OFF, 1))
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
        at = int(p) + int(off)
        raw = bytes(game_code['exe'].read(at - BASE, int(n))) if BASE <= at < BASE + 0x10000000 else b'\0' * int(n)
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
    g.ModLog = lambda text: MODLOG.append(str(text))
    if mct is not None:  # a fake Mod Configuration Tool with these finalized settings
        values = lua.table()
        for key, value in mct.items():
            values[key] = value
        g.MCT_VALUES = values
        lua.execute('''
            function get_mct()
                return { get_mod_by_key = function(_, k)
                    if k ~= 'military_drill' then return nil end
                    return { get_option_by_key = function(_, key)
                        local v = MCT_VALUES[key]
                        if v == nil then return nil end
                        return { get_finalized_setting = function() return v end }
                    end }
                end }
            end
        ''')
    lua.execute(SCRIPT)
    return dict(lua=lua, phases=phases, timers=timers, units=units)


def entry_of(mem, tbl, unit_at):
    """The table line of a unit as the cave would read it, or None."""
    for slot in range(lanes.MAX_UNITS + 1):
        at = tbl + lanes.LIST + lanes.ENTRY * slot
        u, ox, oz, ax, az, p, lane, n, dirty, wait, bias = struct.unpack('<Q6fiIIf', mem.read(at, 0x30))
        if u == 0:
            return None
        if u == unit_at:
            pending, jobs, njobs = struct.unpack('<IQI', mem.read(at + lanes.E_PENDING, 16))
            return dict(at=at, ox=ox, oz=oz, ax=ax, az=az, p=p, lane=lane, n=n, dirty=dirty, wait=wait, bias=bias, pending=pending, jobs=jobs, njobs=njobs)
    raise AssertionError('list without an end')


def code_of(exe):
    """What the fake memreader knows of the game code: the installed exe, and where each pattern of sites.py
    matches in it (address, count)."""
    patterns = {}
    for name in lanes.sites.PATTERNS:
        pattern, mark = lanes.sites.parse(lanes.sites.PATTERNS[name])
        hits = exe.find(pattern, 3)
        patterns[pattern] = (BASE + hits[0] if hits else None, len(hits))
    return dict(exe=exe, patterns=patterns)


def release_battle(logging, through=True):
    work = tempfile.mkdtemp()
    os.chdir(work)
    del MODLOG[:]
    exe = lanes.probe.exe()
    lanes.signatures(exe)
    game_code = code_of(exe)
    mem = Memory()
    mem.regions[UID_CODE & ~0xFFF] = bytearray(0x1000)
    mem.write(UID_CODE, struct.pack('<I', UID_FIELD))
    hooks = {}
    b = run_battle(mem, hooks, game_code, mct=None if logging is None else {'military_drill_logging': logging})
    if not through:
        b['phases']['Deployed']()
        return list(MODLOG)
    tbl = struct.unpack('<Q', mem.read(hooks[BASE + lanes.SITES[0][1]] + 2, 8))[0]
    u = b['units']

    def tick():
        for name in ('walk', 'walk2', 'walk3'):
            mem.write(u[name]['model'] + 0x88, struct.pack('<3f', u[name]['x'], 0.0, u[name]['z']))
        for one in u.values():
            now = struct.unpack('<I', mem.read(one['clock'] + 0x98, 4))[0]
            mem.write(one['clock'] + 0x98, struct.pack('<I', now + 2))
        b['timers'][TIMER]()

    b['phases']['Deployed']()
    at = entry_of(mem, tbl, u['stand']['at'])['at']
    mem.write(at + lanes.E_TPL, struct.pack('<3fI QQ HH I I I', 1.0, 2.0, 3.0, 0, 0, 0, 0xE3A5, 0xC07B, 7, 0, 0))
    mem.write(at + lanes.E_SEQ, struct.pack('<I', 1))
    for _ in range(30):
        tick()
    w = u['walk']
    w.update(dict(x=0.0, z=-60.0, gx=0.0, gz=80.0, moving=True))
    opened = False
    for _ in range(120):
        w['z'] += 0.6
        tick()
        if entry_of(mem, tbl, u['stand']['at'])['lane'] > 0:
            opened = True
            break
    expect(opened, 'the release script makes way: %r' % MODLOG)
    expect(len(hooks) == 4, 'the caves are installed')
    expect(not os.listdir(work), 'no file of its own: %r' % os.listdir(work))
    return list(MODLOG)


def main_release():
    # (Off (default) / Problems / Basic)
    for logging in (None, 'off'):  # no MCT / MCT "Logging" off: nothing at all
        lines = release_battle(logging)
        expect(lines == [], 'no logging by default: %r' % lines)
    lines = release_battle('problems')
    expect(lines == ['[Military Drill] Military Drill %s, battle script loaded' % lanes.VERSION], 'problems: the start only: %r' % lines)
    lines = release_battle('basic')
    opens = [l for l in lines if l.startswith('[Military Drill] OPEN ')]
    expect(any('logging basic' in l for l in lines) and any('fighting starts' in l for l in lines) and opens and not any(' | ' in l for l in opens)
           and not any('PERF ' in l and 'PERF BATTLE' not in l for l in lines) and not any('jobs for the models' in l for l in lines),
           'basic: the settings, one short line per opening: %r' % lines)
    # switched off at load (a game update the mod does not fit) with "Log problems" on: the MOD OFF line is written
    work = tempfile.mkdtemp()
    os.chdir(work)
    del MODLOG[:]
    exe = lanes.probe.exe()
    mem = Memory()
    mem.regions[UID_CODE & ~0xFFF] = bytearray(0x1000)
    mem.write(UID_CODE, struct.pack('<I', UID_FIELD))
    run_battle(mem, {}, code_of(exe), wrong_build=True, mct={'military_drill_logging': 'problems'})
    expect(any('MOD OFF' in l and 'game build does not fit' in l for l in MODLOG), 'MOD OFF before the fighting: %r' % MODLOG)
    print('mock test (release script): %d checks, ALL PASS' % checks)


def main():
    work = tempfile.mkdtemp()
    os.chdir(work)
    log = lambda: open(LOG, encoding='utf8').read()
    exe = lanes.probe.exe()
    lanes.signatures(exe)
    game_code = code_of(exe)
    mem = Memory()
    mem.regions[UID_CODE & ~0xFFF] = bytearray(0x1000)
    mem.write(UID_CODE, struct.pack('<I', UID_FIELD))

    hooks = {}
    run_battle(mem, hooks, game_code, wrong_build=True)
    expect(not hooks and 'MOD OFF' in log() and 'game build does not fit (unexpected code at the ' in log(), 'wrong build must switch the mod off')
    for name in ('set_station', 'place_send', 'station'):  # a pattern found twice, or not at all: off, nothing installed
        for count in (2, 0):
            pattern = lanes.sites.parse(lanes.sites.PATTERNS[name])[0]
            changed = dict(game_code, patterns=dict(game_code['patterns'], **{pattern: (game_code['patterns'][pattern][0] if count else None, count)}))
            hooks = {}
            run_battle(mem, hooks, changed)
            expect(not hooks and 'MOD OFF' in log() and ('the %s code was found %d times' % (name, count)) in log(), 'pattern %s found %d times' % (name, count))

    hooks = {}
    b = run_battle(mem, hooks, game_code)
    expect('MOD OFF' not in log() and log().count('cave installed') == 4 and len(hooks) == 4 and 'walker columns ON' in log(), log())
    tbl = struct.unpack('<Q', mem.read(hooks[BASE + lanes.SITES[0][1]] + 2, 8))[0]
    for site in lanes.SITES:
        cave = hooks[BASE + site[1]]
        expect(mem.read(cave, len(lanes.HEAD) + 8 + len(site[2])) == lanes.HEAD + struct.pack('<Q', tbl) + site[2], 'cave bytes ' + site[0])
    enabled = lambda: struct.unpack('<I', mem.read(tbl + lanes.ENABLE, 4))[0]
    u = b['units']
    expect(struct.unpack('<Q', mem.read(tbl + lanes.CARROT_RET, 8))[0] == BASE + lanes.FOUND['carrot_ret'], 'cave 4 knows the marching call')
    expect(struct.unpack('<f', mem.read(tbl + lanes.HALF, 4))[0] == 0.5, 'the constant 0.5')

    def tick(battle_ticks=2):  # the walkers' first models walk with their units; the battle clock goes on (0.1 s a tick)
        for name in ('walk', 'walk2', 'walk3'):
            mem.write(u[name]['model'] + 0x88, struct.pack('<3f', u[name]['x'], 0.0, u[name]['z']))
        for one in u.values():
            now = struct.unpack('<I', mem.read(one['clock'] + 0x98, 4))[0]
            mem.write(one['clock'] + 0x98, struct.pack('<I', now + battle_ticks))
        b['timers'][TIMER]()

    e_walk_at = lambda: entry_of(mem, tbl, u['walk']['at'])['at'] + lanes.E_WLAG

    def walker(name):
        at = entry_of(mem, tbl, u[name]['at'])['at']
        q, ox, oz, ax, az, bx, bz, t0, t1, c, kl, kh, sl, sh = struct.unpack('<10f2i2f', mem.read(at + lanes.E_WQ, 0x38))
        return dict(q=q, ox=ox, oz=oz, ax=ax, az=az, bx=bx, bz=bz, t0=t0, t1=t1, c=c, kl=kl, kh=kh, sl=sl, sh=sh)
    tick()
    expect(enabled() == 0 and entry_of(mem, tbl, u['stand']['at'])['lane'] == 0 and 'listed before the fighting: 8 units' in log(), 'off, units listed before the fighting')
    expect(struct.unpack('<Q', mem.read(tbl + lanes.SET_STATION, 8))[0] == BASE + lanes.FOUND['set_station'], 'the game routine for cave 3')
    pool = struct.unpack('<Q', mem.read(tbl + lanes.POOL, 8))[0]
    expect(pool != 0 and entry_of(mem, tbl, u['stand']['at'])['jobs'] == pool, 'job memory')
    b['phases']['Deployed']()
    expect(enabled() == 1 and 'mod ON: 8 units' in log(), log())
    expect('settings (defaults: MCT not installed or the mod not registered): enabled true (for every army), lanes auto, expendable units make way yes, wavering units make way NO' in log(), log())
    for name in u:
        e = entry_of(mem, tbl, u[name]['at'])
        expect(e is not None and e['lane'] == 0 and e['p'] == 4.0 and e['dirty'] == 0 and e['wait'] == 4, 'every unit listed with closed lanes: ' + name)

    lane = lambda name: entry_of(mem, tbl, u[name]['at'])['lane']
    # what cave 1 does when the game hands out places in the standing unit: it keeps the last record as the template
    stand_entry = entry_of(mem, tbl, u['stand']['at'])['at']
    # words as in the game (run 27: facing 0xC07B): bytes >= 0x80 cut with CA's string.sub would shorten the record
    mem.write(stand_entry + lanes.E_TPL, struct.pack('<3fI QQ HH I I I', 1.0, 2.0, 3.0, 0, 0, 0, 0xE3A5, 0xC07B, 7, 0, 0))
    mem.write(stand_entry + lanes.E_SEQ, struct.pack('<I', 1))
    mem.write(u['stand']['model'] + 0x88, struct.pack('<3f', 8.0, 2.0, 1.0))  # where its one model stands
    for _ in range(5):
        tick()
    expect(lane('stand') == 0, 'nobody moves: no lanes')

    # the walker is ordered straight through the standing unit, 3 m/s = 0.6 m per tick
    w = u['walk']
    w['moving'], w['gx'], w['gz'] = True, 0.0, 80.0
    opened_at = closed_at = None
    widest, steps_seen = 0.0, set()
    for t in range(400):
        w['z'] += 0.6
        if w['z'] >= 80.0:
            w['z'], w['moving'] = 80.0, False
        tick()
        if opened_at is None and lane('stand') > 0:
            opened_at = w['z']
            e = entry_of(mem, tbl, u['stand']['at'])
            # 3 blocks of 40 / 3 m; 2 lanes of 0.4 x the walker's 30 m front / 2 = 6 m (more than 2 files x 2 x 0.65 + 0.5)
            expect(e['n'] == 1 and abs(e['p'] - 40 / 3) < 1e-4, '3 blocks, 2 lanes: %r' % e)
            expect(abs(e['ax'] + 1.0) < 1e-5 and abs(e['az']) < 1e-5 and e['bias'] == 0, 'lanes across the way of the walker: %r' % e)
            # the model is sent to its lane place at once: a job for cave 3
            expect(e['pending'] == 1 and e['njobs'] == 1, 'one job: %r' % e)
            job = mem.read(e['jobs'], lanes.JOB)
            expect(struct.unpack_from('<Q', job, 0)[0] == u['stand']['model'], 'the job names the model')
            x, y, z, pad, q1, q2, facing, kind, extra, gate, f1, f2 = struct.unpack_from('<3fIQQIIIIII', job, lanes.JOB_RECORD)
            clock_now = struct.unpack('<I', mem.read(u['stand']['clock'] + 0x98, 4))[0]  # the battle tick of the model
            # it moves 6 m outwards in 4 walking steps of 1.5 m: the first one now
            expect(abs(x - 9.5) < 1e-4 and y == 2.0 and z == 1.0, 'x = 8 is in the block next to the middle one: first step 1.5 m outwards: %r' % ((x, y, z),))
            expect(abs(e['lane'] - 1.5) < 1e-5, 'the game hands out places as far as the first step: %r' % e)
            expect((pad, q1, q2, facing, kind, extra, f1, f2) == (0, 0, 0, 0xC07B, 0, 7, 0, 0) and clock_now <= gate <= clock_now + 4, 'the rest of the station record')
            expect(abs(e['ox']) < 1e-3 and abs(e['oz']) < 1e-3, 'origin where the walker crosses: %r' % e)
            expect(e['dirty'] == 1, 'the unit is told to hand out its places at once')
            # the walker forms 2 columns: one every 19.33 m (block 13.33 + lane 6), k = -1 .. 0, along its way (0, 1)
            c = walker('walk')
            word = lambda off: struct.unpack('<I', mem.read(tbl + off, 4))[0]
            expect(word(lanes.WALKERS) == 1 and word(lanes.JOBS_ANY) == 1, 'cave 4 and cave 3 look from now on')
            expect(abs(c['q'] - 58 / 3) < 1e-4 and c['kl'] == -1 and c['kh'] == 0 and abs(c['bz'] - 1) < 1e-6 and abs(c['ax'] + 1) < 1e-6, 'columns: %r' % c)
            # zone: the standing model is at t = 1; from where the walker's front is (11.9 m before it) to 1 m behind
            # it; files squeezed to (6 - 1.3) / 19.33 of their strip
            expect(struct.unpack('<2f', mem.read(e_walk_at(), 8)) == (struct.unpack('<f', struct.pack('<f', 0.8))[0], 1.0), 'lag 0.8, at least 1 m ahead')
            expect(abs(c['t0'] - (1 - 11.9)) < 1e-3 and abs(c['t1'] - 2.0) < 1e-4 and abs(c['c'] - 4.7 * 3 / 58) < 1e-5, 'zone: %r' % c)
            # across: the standing model (x = 8, s = -8, after the opening s = -14) +- (0.65 + 0.65 + 0.5)
            expect(abs(c['sl'] - (-14 - 1.8)) < 1e-4 and abs(c['sh'] - (-14 + 1.8)) < 1e-4, 'free beside the standing unit: %r' % c)
            mem.write(e['at'] + lanes.E_DIRTY, struct.pack('<I', 0))  # what the cave does
            mem.write(u['stand']['model'] + 0x88, struct.pack('<f', 14.0))  # the model walks to its new place
            mem.write(e['jobs'], struct.pack('<Q', 0))  # cave 3 took the job
            wat = entry_of(mem, tbl, u['walk']['at'])['at'] + lanes.E_SEQ  # the game hands the walker 3 new places
            mem.write(wat, struct.pack('<I', struct.unpack('<I', mem.read(wat, 4))[0] + 3))
        if opened_at is not None and closed_at is None and lane('stand') > 0:
            widest = max(widest, lane('stand'))
            steps_seen.add(round(lane('stand'), 3))
        if opened_at is not None and closed_at is None and lane('stand') == 0:
            closed_at = w['z']
            e = entry_of(mem, tbl, u['stand']['at'])
            expect(e['dirty'] == 1 and e['pending'] == 1, 'the unit is told to close up at once')
            job = mem.read(e['jobs'], lanes.JOB)
            expect(struct.unpack_from('<Q3f', job, 0) == (u['stand']['model'], 8.0, 2.0, 1.0), 'back to the place noted at the opening')
            expect(walker('walk')['q'] == 0, 'columns off when the lanes close')
            expect(struct.unpack('<I', mem.read(tbl + lanes.WALKERS, 4))[0] == 0, 'no walkers any more: cave 4 leaves at once')
        expect(lane('aside') == 0 and lane('enemy') == 0 and lane('walk') == 0, 'only the unit in the way opens lanes')
    # timing: the walker (3 m/s, measured from its models) reaches the standing model (z = 1, both radius 0.65) in
    # (-z - 0.3) / 3 s; the split takes 0.4 s + 4 steps x (1.5 m / 3 m/s + 0.2 s) = 3.2 s; open when contact <= 3.2 +
    # 0.75 + 0.2 s: z >= -12.75, i.e. at z = -12.2 (steps of 0.6 m)
    expect(opened_at is not None and -12.5 <= opened_at <= -12.0, 'opened with the walker at z = %r' % opened_at)
    expect('timing: fronts 11.9 m apart, walker 3.0 m/s -> contact in 4.0 s; split takes 3.2 s (models walk 3.0 m/s)' in log(), log())
    expect('split in 4 steps of 1.5 m, one every 0.7 s' in log(), log())
    expect('REGROUP? A1.1.2 walk was given 3 new places by the game' in log() and 'the walker was given 3 places by the game meanwhile' in log(), log()[-2500:])
    text = log()
    expect('MARCH' not in text and 'NUDGE' not in text and 'STATION TEST' not in text, 'no research left: ' + text[-3000:])
    expect(abs(widest - 6.0) < 1e-5 and steps_seen == {1.5, 3.0, 4.5, 6.0}, 'the lanes widen step by step to 6 m: %r' % steps_seen)
    # closes 8 ticks after the walker is more than 20 + 5 m past the middle closing up starts as before (the walker 25 m
    # past and 1.6 s quiet); the model walks back 6 m in 4 steps of 1.5 m, 0.5 s each, so the lanes are shut 3 steps
    # later
    expect(closed_at is not None and 25.0 + 7 * 0.6 + 3 * 3 * 0.6 <= closed_at <= 25.0 + 10 * 0.6 + 4 * 4 * 0.6, 'closed with the walker at z = %r' % closed_at)
    expect('CLOSING A1.1.1 stand (nobody is passing any more): back in 4 steps of 1.5 m, one every 0.5 s | A1.1.2 walk: its rear' in log(), log()[-2500:])
    text = log()
    expect('OPEN  A1.1.1 stand makes way for A1.1.2 walk (100 models, radius 0.65): 3 blocks of 13.3 m, 2 lanes of 6.0 m, across (-1.00, 0.00)' in text, text)
    expect('columns: 2 columns every 19.3 m, files squeezed to 24 % of their strip, from 11.9 m before to 1.0 m behind the standing unit, -15.8 .. -12.2 m across (beside it: free)' in text, text)
    expect('1 models sent at once (facing word 49275' in text and '1 models sent back at once' in text, text)
    expect('CLOSE A1.1.1 stand (nobody is passing any more)' in text and text.count('OPEN ') == 1, text)
    expect('3 s after opening, A1.1.1 stand: 1 of 1 models stand more than 1 m from where they stood, the farthest 6.0 m' in text, text)
    expect('; 1 of 1 models stand more than 1 m' in text and 'places handed out by the game 0, shifted 0' in text, text)

    # the standing unit gets an order itself while its lanes are open: they close at once
    w['x'], w['z'], w['gz'], w['moving'] = 0.0, -13.0, 80.0, True
    for _ in range(12):
        w['z'] += 0.6
        tick()
    expect(lane('stand') > 0, 'open again')
    u['stand']['moving'] = True  # its own models walking to their lane places must not close the lanes
    tick()
    expect(lane('stand') > 0 and 'it no longer just stands' not in log(), 'still open while only its models shuffle')
    u['stand']['gx'] = 30.0  # a real order: the formation the player asked for is somewhere else now
    tick()
    expect(lane('stand') == 0 and 'it no longer just stands' in log(), 'closed when the unit is ordered away')
    u['stand']['moving'], u['stand']['gx'] = False, 0.0
    # a walker that fights does not count
    w['melee'] = True
    for _ in range(20):  # (the standing unit also has to stand 4 s again)
        tick()
    expect(lane('stand') == 0, 'no lanes for a unit in melee')
    w['melee'] = False
    tick()
    expect(lane('stand') > 0, 'lanes again')
    # a walker passing to the side of the unit does not open lanes
    # a walker that stops INSIDE the standing unit keeps the lanes open (its models would walk into it)
    # (the mock's standing unit is its one model, as it stood at the opening)
    w['x'], w['z'], w['moving'] = struct.unpack('<f', mem.read(u['stand']['model'] + 0x88, 4))[0], 1.0, False
    for _ in range(12):
        tick()
    expect(lane('stand') > 0 and 'CLOSING' not in log()[-3000:], 'open while the stopped walker is inside: ' + log()[-1500:])
    # but not for ever: a walker parked inside holds them 5 s after it stopped, then they close up
    for _ in range(50):
        tick()
    expect('CLOSING A1.1.1 stand (nobody is passing any more)' in log()[-3000:] and 'not walking' in log()[-3000:], 'parked walker: closing after 5 s: ' + log()[-1500:])
    # one that stops in front of it: closed, in walking steps
    w['z'] = -30.0
    for _ in range(25):
        tick()
    expect(lane('stand') == 0, 'closed after the walker stopped')
    w['x'], w['z'], w['gx'], w['gz'], w['moving'] = 60.0, -40.0, 60.0, 80.0, True
    for _ in range(10):
        tick()
    expect(lane('stand') == 0, 'walks past: no lanes')

    def walk_in(step, **state):  # the walker comes along +z from z = -30 (x = 0) until the lanes open
        w.update(dict(dict(x=0.0, z=-30.0, gx=0.0, gz=80.0, moving=True, melee=False), **state))
        for _ in range(60):
            w['z'] += step
            tick()
            if lane('stand') > 0:
                return True
        return False

    def stop_walker():
        w['moving'], w['melee'] = False, False
        for _ in range(60):  # 1.6 s quiet, then the models walk back in steps (9 m: 5 steps, 7 s)
            tick()
        expect(lane('stand') == 0, 'closed after the walker stopped: ' + '\n'.join(l for l in log().splitlines()[-60:] if 'units with open lanes' not in l and 'PERF' not in l)[-2500:])
    # skirmishers, a drawn path: the order points elsewhere, the unit moves straight at the standing unit: its heading
    # comes from its movement
    expect(walk_in(0.6, gx=300.0, gz=-30.0) and 'walker heading from its movement' in log(), 'heading from the movement: ' + log()[-1500:])
    stop_walker()
    # the pace probe: the model of the standing unit did not move at all, so the split finishes at once
    expect('HURRY A1.1.1 stand: its models came 0.0 of 1.5 m by step 1 of 4: the rest at once' in log(), log()[-2500:])
    # a unit with a few models caught in a fight that really walks gets lanes
    expect(walk_in(0.6, melee=True), 'lanes for a walker with models in melee that walks')
    stop_walker()
    # a running walker (5 m/s, more than 1.3 x its walking speed): the standing models run, in one step
    expect(walk_in(1.0) and 'split in 1 steps of 6.0 m' in log() and 'walker 5.0 m/s' in log(), 'one running step: ' + log()[-1500:])
    stop_walker()
    # a TELEPORTED standing unit (runs 24 / 25 crashed on jobs 2 s after the scenario put the unit down) makes way
    # only after standing 4 s from the jump, although the walker is already in range
    # (a teleport moves the unit's MODELS 5 m and more: smaller jumps and jumps of unit:position() alone are jitter,
    # runs 33 / 34)
    u['stand']['x'] = 6.0
    sx, sy, sz = struct.unpack('<3f', mem.read(u['stand']['model'] + 0x88, 12))
    mem.write(u['stand']['model'] + 0x88, struct.pack('<3f', sx + 6.0, sy, sz))
    w.update(dict(x=6.0, gx=6.0, z=-12.0, gz=80.0, moving=True, melee=False))
    early = []
    for t in range(30):
        w['z'] += 0.3
        tick()
        early.append(lane('stand') > 0)
    expect(not any(early[:19]) and any(early[19:]) and 'its models jumped 6.0 m' in log(), 'lanes only 4 s after the jump: %r' % early)
    stop_walker()
    u['stand']['x'] = 0.0
    mem.write(u['stand']['model'] + 0x88, struct.pack('<3f', sx, sy, sz))
    for _ in range(21):
        tick()

    # ONE walking body with many riders (run 28: Ikit Claw on the Doomwheel, 25 models in one wheel, got two lanes and
    # columns): one lane centred on its way, no columns
    wheel = mem.alloc(0xc00)
    mem.write(wheel + 0xa0, struct.pack('<f', 3.0))
    mem.write(wheel + 0x2e8, struct.pack('<Q', w['at']))
    mem.write(wheel + 0x58, mem.read(w['model'] + 0x58, 8))
    riders = [mem.alloc(0xc00) for _ in range(3)]
    for r in riders:
        mem.write(r + 0x2e0, struct.pack('<Q', wheel))
        mem.write(r + 0xa0, struct.pack('<f', 0.5))
    rider_list = mem.alloc(8 * len(riders))
    mem.write(rider_list, b''.join(struct.pack('<Q', r) for r in riders))
    old_count, old_list = mem.read(w['at'] + 0xa4, 4), mem.read(w['at'] + 0xa8, 8)
    mem.write(w['at'] + 0xa4, struct.pack('<I', len(riders)))
    mem.write(w['at'] + 0xa8, struct.pack('<Q', rider_list))
    w['men'] = 25
    expect(walk_in(0.6, x=0.0, gx=0.0) and 'one walking body carries its 25 models -> one lane, no columns' in log(), 'one body: ' + log()[-1500:])
    e = entry_of(mem, tbl, u['stand']['at'])
    expect(e['n'] == 1 and e['bias'] == -0.5 and 'COLUMNS' not in log()[-1500:], 'one lane for one body: %r' % e)
    stop_walker()
    mem.write(w['at'] + 0xa4, old_count)
    mem.write(w['at'] + 0xa8, old_list)
    w['men'] = 100
    for _ in range(21):
        tick()

    # TWO big walkers side by side through one standing unit (Ikit Claw and a Doomwheel): the lane opened for the first
    # one grows to hold the second (the split point stays: the standing model 8 m to the side goes on to that side)
    w2 = u['walk2']
    w['men'], w2['men'] = 1, 1
    mem.write(u['stand']['model'] + 0x88, struct.pack('<3f', 8.0, 2.0, 1.0))  # its one model, 8 m to the side
    for _ in range(80):  # the places noted at the last opening are forgotten (BASE_KEEP_TICKS)
        tick()
    expect(walk_in(0.6), 'lanes for the first walker: ' + log()[-1500:])
    e = entry_of(mem, tbl, u['stand']['at'])
    expect(e['bias'] == -0.5 and abs(e['lane'] - 1.8) < 1e-5, 'one lane of 1.8 m for the first: %r' % e)
    w2.update(dict(x=8.0, z=w['z'] - 2.0, gx=8.0, gz=80.0, moving=True))
    wider = False
    for _ in range(40):
        w2['z'] += 0.6
        mem.write(w2['model'] + 0x88, struct.pack('<3f', w2['x'], 0.0, w2['z']))
        tick()
        if 'WIDER A1.1.1 stand for A1.1.4 walk2 too' in log():
            wider = True
            break
    expect(wider and 'walking 0 deg across the lanes' in log(), 'the lane grows for the second walker: ' + log()[-2000:])
    w2['moving'] = False
    for _ in range(25):
        tick()
    e = entry_of(mem, tbl, u['stand']['at'])
    # the standing model at x = 8 (across -8): left of the split, 8.65 + 0.25 m; right 0.9 m as before
    expect(abs(e['lane'] - 9.8) < 1e-3 and abs(e['bias'] + 8.9 / 9.8) < 1e-4, 'grown to 8.9 / 0.9 m: %r' % e)
    job = mem.read(e['jobs'], lanes.JOB)
    x = struct.unpack_from('<f', job, lanes.JOB_RECORD)[0]
    expect(abs(x - 16.9) < 1e-3, 'the standing model goes 8.9 m outwards, to x = 16.9: %r' % x)
    # it stopped inside the standing unit: the lanes stay open until it is gone
    expect(lane('stand') > 0, 'open while the second walker stands inside')
    w2.update(dict(x=-300.0, z=-80.0, gx=-300.0, gz=-80.0, moving=False))
    mem.write(w2['model'] + 0x88, struct.pack('<3f', -300.0, 0.0, -80.0))
    stop_walker()
    for _ in range(21):
        tick()
    # the same at an angle: the second walker comes at 45 deg from the side and crosses the first one's way
    expect(walk_in(0.6), 'lanes for the first walker again: ' + log()[-1500:])
    w2.update(dict(z=w['z'] - 4.0, moving=True))
    w2['x'] = 8.0 + (1.0 - w2['z'])  # its way (45 deg, towards -x) runs through the standing model at x = 8, z = 1
    w2['gx'], w2['gz'] = w2['x'] - 90.0, w2['z'] + 90.0
    wider = False
    for _ in range(40):
        w2['x'] -= 0.6 * 0.5 ** 0.5
        w2['z'] += 0.6 * 0.5 ** 0.5
        mem.write(w2['model'] + 0x88, struct.pack('<3f', w2['x'], 0.0, w2['z']))
        tick()
        if log().count('WIDER A1.1.1 stand for A1.1.4 walk2 too') == 2:
            wider = True
            break
    expect(wider and 'walking 45 deg across the lanes' in log(), 'the lane grows for a walker at an angle: ' + '\n'.join(l for l in log().splitlines() if 'walk2' in l or 'WIDER' in l or 'OPEN' in l or 'CLOSE' in l)[-3000:])
    w2['moving'] = False
    for _ in range(25):
        tick()
    e = entry_of(mem, tbl, u['stand']['at'])
    expect(e['lane'] > 9.0 and e['bias'] < -0.85, 'grown to the left for the walker at an angle: %r' % e)
    w2.update(dict(x=-300.0, z=-80.0, gx=-300.0, gz=-80.0))
    mem.write(w2['model'] + 0x88, struct.pack('<3f', -300.0, 0.0, -80.0))
    stop_walker()
    for _ in range(21):
        tick()
    # BOTH come at once, side by side, while the lanes are shut (run 29: the lane grown in the same look came too
    # late): the opening plans for both, its split timed by the whole lane
    w.update(dict(x=0.0, z=-30.0, gx=0.0, gz=80.0, moving=True, melee=False))
    w2.update(dict(x=8.0, z=-30.0, gx=8.0, gz=80.0, moving=True))
    opened = False
    for _ in range(60):
        w['z'] += 0.6
        w2['z'] += 0.6
        mem.write(w2['model'] + 0x88, struct.pack('<3f', w2['x'], 0.0, w2['z']))
        tick()
        if lane('stand') > 0:
            opened = True
            break
    expect(opened and 'WIDER A1.1.1 stand for A1.1.4 walk2 too, at the opening' in log(), 'one plan for both: ' + log()[-2000:])
    e = entry_of(mem, tbl, u['stand']['at'])
    # side by side, 8 m apart (one 20 m lane for two Doomwheels was): a lane of its own for each - lanes of 1.8 m around
    # x = 0 and x = 8, the block between them (6.2 m) stays, origin in their middle
    expect(e['bias'] == 0 and e['n'] == 1 and abs(e['p'] - 6.2) < 1e-4 and abs(e['lane'] - 1.8) < 1e-4 and abs(e['ox'] - 4.0) < 1e-4,
           'a lane for each walker: %r' % e)
    expect('a lane of its own, 2 lanes of 1.8 m with blocks of 6.2 m between them' in log(), log()[-1500:])
    job = mem.read(e['jobs'], lanes.JOB)
    x = struct.unpack_from('<f', job, lanes.JOB_RECORD)[0]
    expect(abs(x - 9.8) < 1e-3, 'the standing model in the second walker\'s way (x = 8) steps one lane outwards, to 9.8: %r' % x)
    w2.update(dict(x=-300.0, z=-80.0, gx=-300.0, gz=-80.0, moving=False))
    mem.write(w2['model'] + 0x88, struct.pack('<3f', -300.0, 0.0, -80.0))
    stop_walker()
    w['men'], w2['men'] = 100, 1
    for _ in range(21):
        tick()


    # --- build49 ------------------------------------------------------------------------------
    def new_model(x, z, radius, carrier=0):
        m = mem.alloc(0xc00)
        mem.write(m + 0xa0, struct.pack('<f', radius))
        mem.write(m + 0x88, struct.pack('<3f', x, 0.0, z))
        mem.write(m + 0x58, mem.read(u['stand']['model'] + 0x58, 8))
        if carrier:
            mem.write(m + 0x2e0, struct.pack('<Q', carrier))
        return m

    def set_list(name, models):  # the model list of a unit; returns the old one
        lst = mem.alloc(8 * len(models))
        mem.write(lst, b''.join(struct.pack('<Q', m) for m in models))
        old = (mem.read(u[name]['at'] + 0xa4, 4), mem.read(u[name]['at'] + 0xa8, 8))
        mem.write(u[name]['at'] + 0xa4, struct.pack('<I', len(models)))
        mem.write(u[name]['at'] + 0xa8, struct.pack('<Q', lst))
        return old

    def restore_list(name, old):
        mem.write(u[name]['at'] + 0xa4, old[0])
        mem.write(u[name]['at'] + 0xa8, old[1])

    def template(name):  # what cave 1 keeps when the game hands out places in the unit
        at = entry_of(mem, tbl, u[name]['at'])['at']
        mem.write(at + lanes.E_TPL, struct.pack('<3fI QQ HH I I I', 1.0, 2.0, 3.0, 0, 0, 0, 0xE3A5, 0xC07B, 7, 0, 0))
        mem.write(at + lanes.E_SEQ, struct.pack('<I', 1))

    w3 = u['walk3']

    def park(*ones):  # walkers that took part go back to their waiting places
        for one, x in ones:
            one.update(dict(x=x, z=-80.0, gx=x, gz=-80.0, moving=False))

    def lane_centres(e):  # across (x * ax + z * az) of the lanes of a unit with lanes per file
        q = e['p'] + e['lane']
        a_o = e['ox'] * e['ax'] + e['oz'] * e['az']
        return [a_o + (k + 0.5) * q for k in range(-e['n'], e['n'])]

    def side_by_side(walkers, steps=60):
        for one, x, z, dx in walkers:
            one.update(dict(x=x, z=z, gx=x + 100 * dx, gz=z + 100 * (1 - dx * dx) ** 0.5, moving=True, melee=False, men=1))
        for _ in range(steps):
            for one, x, z, dx in walkers:
                one['x'] += 0.6 * dx
                one['z'] += 0.6 * (1 - dx * dx) ** 0.5
            tick()
            if lane('stand') > 0:
                for _ in range(10):  # (the lanes open in steps: until they are as wide as planned)
                    tick()
                return True
        return False

    def shut():
        park((w2, -300.0), (w3, -500.0))
        stop_walker()
        for _ in range(21):
            tick()

    mem.write(u['stand']['model'] + 0x88, struct.pack('<3f', 8.0, 2.0, 1.0))
    # (the standing unit as a rank of 5 models from x = -10 to 18: walkers at 0 .. 14 all walk through it)
    old_rank = set_list('stand', [new_model(x, 1.0, 0.65) for x in (-10.0, -3.0, 4.0, 11.0, 18.0)])
    # THREE walkers side by side, the middle one inside the lane grown for the outer two (lanes of their own for the
    # outer two only put a standing block where the middle one walks): a lane for each
    expect(side_by_side([(w, 0.0, -30.0, 0.0), (w2, 14.0, -30.0, 0.0), (w3, 7.0, -30.0, 0.0)]), 'three walkers: ' + log()[-1500:])
    expect('3 walkers side by side -> a lane for each, 3 lanes' in log() and '3 lanes of 1.8 m with blocks of 5.2 m between them' in log(), 'a lane for each of three: ' + log()[-2000:])
    e = entry_of(mem, tbl, u['stand']['at'])
    for x in (0.0, 7.0, 14.0):
        expect(any(abs(c + x) < 0.05 for c in lane_centres(e)), 'a lane where the walker at x = %r goes: %r %r' % (x, lane_centres(e), e))
    shut()
    # one BEHIND the other and one beside them: those one behind the other share a lane
    expect(side_by_side([(w, 0.0, -30.0, 0.0), (w2, 8.0, -30.0, 0.0), (w3, 0.0, -36.0, 0.0)]), 'one behind the other: ' + log()[-1500:])
    expect('3 walkers side by side -> a lane for each, 2 lanes' in log(), 'two lanes for three walkers: ' + log()[-2000:])
    e = entry_of(mem, tbl, u['stand']['at'])
    expect(e['n'] == 1 and abs(e['p'] - 6.2) < 1e-3, 'two lanes: %r' % e)
    shut()
    # one of them walking 8 deg towards the other (the lanes were put where the walkers were when they opened; this one
    # drifts 1.7 m before it reaches the standing unit): its lane lies where it CROSSES the unit
    dx = -0.139173
    expect(side_by_side([(w, 0.0, -30.0, 0.0), (w2, 10.0, -30.0, dx)]), 'at an angle: ' + log()[-1500:])
    e = entry_of(mem, tbl, u['stand']['at'])
    cross = w2['x'] + (1.0 - w2['z']) * dx / (1 - dx * dx) ** 0.5  # where its way meets the standing model's rank (z = 1)
    expect(abs(w2['x'] - cross) > 1.0, 'it really drifts: %r -> %r' % (w2['x'], cross))
    expect(any(abs(c + cross) < 0.1 for c in lane_centres(e)) and any(abs(c) < 0.1 for c in lane_centres(e)), 'its lane where it crosses (x = %.2f): %r %r' % (cross, lane_centres(e), e))
    shut()
    restore_list('stand', old_rank)
    w['men'], w2['men'], w3['men'] = 100, 1, 1

    # a walker that comes while the unit closes up: it opens again at once, as wide as before
    for _ in range(80):
        tick()
    expect(walk_in(0.6), 'lanes before closing: ' + log()[-1500:])
    before = log().count('CLOSING A1.1.1 stand')
    for _ in range(150):
        w['z'] += 0.6
        tick()
        if log().count('CLOSING A1.1.1 stand') > before:
            break
    expect(log().count('CLOSING A1.1.1 stand') > before and lane('stand') > 0, 'closing: ' + log()[-1500:])
    w.update(dict(x=0.0, z=-20.0, gx=0.0, gz=80.0, moving=True))
    for _ in range(3):
        w['z'] += 0.6
        tick()
    expect('A1.1.1 stand opens again for A1.1.2 walk while closing up' in log() and abs(lane('stand') - 6.0) < 1e-5, 'open again: %r %s' % (lane('stand'), log()[-1500:]))
    stop_walker()
    for _ in range(21):
        tick()

    # a standing unit whose models are shuffled (the game says it moves, e.g. pushed by the unit before it making way)
    # but whose order is the same and which is at its place still stands and makes way (the 2nd unit in a row did not
    # make way for the Hell Pit Abomination)
    expect('NOT STANDING A1.1.1 stand any more: it was given another order' in log(), 'why it stopped standing: ' + log()[-1500:])
    u['stand']['moving'] = True
    for _ in range(25):
        tick()
    expect(walk_in(0.6), 'lanes while its models shuffle: ' + log()[-1500:])
    stop_walker()
    u['stand']['moving'] = False
    for _ in range(21):
        tick()

    # unit:position() jumps ~2 m now and then, for standing and walking units alike (run 33: standing units lost
    # "standing" every few seconds and made way late or not at all; a walker's heading came from the jitter): the
    # lanes open all the same, the walker's heading stays its order
    w.update(dict(x=0.0, z=-30.0, gx=0.0, gz=80.0, moving=True, melee=False))
    opened, mark = False, len(log())
    for i in range(60):
        w['z'] += 0.6
        u['stand']['jx'] = 2.3 if (i // 5) % 2 else 0.0
        w['jx'] = 2.3 if (i // 3) % 2 else 0.0
        tick()
        if lane('stand') > 0:
            opened = True
            break
    expect(opened and 'walker heading from its order' in log()[-3000:] and 'NOT STANDING A1.1.1 stand any more: its position jumped' not in log()[mark:],
           'lanes despite the jitter: ' + log()[-1500:])
    u['stand']['jx'], w['jx'] = 0.0, 0.0
    stop_walker()
    for _ in range(21):
        tick()

    # a NEW ORDER right next to the standing unit: for 2 s the walker still moves the old way while it turns (run 34:
    # seen only at contact, "heading from its movement"): its way is the order's line from the first look
    for _ in range(80):
        tick()
    # (walking along +x 10 m in front of it, 15 m to its left: the standing model at x = 8 is 23 m ahead - too far to
    # split; the new order turns it straight at the unit, and while it turns (1.8 s) it moves at 45 deg: the lanes
    # open during the turn, along the ORDER's line - across (-1, 0), not the 45 deg of its movement)
    w.update(dict(x=-18.6, z=-10.0, gx=100.0, gz=-10.0, moving=True, melee=False))
    for _ in range(6):  # walking along +x
        w['x'] += 0.6
        tick()
    expect(lane('stand') == 0, 'no lanes while it walks past')
    w['gx'], w['gz'] = w['x'] + 4.0, 80.0  # the new order: straight through the standing unit
    turned = False
    for _ in range(9):
        w['x'] += 0.42  # (still turning: it moves at 45 deg)
        w['z'] += 0.42
        tick()
        if lane('stand') > 0:
            turned = True
            break
    e = entry_of(mem, tbl, u['stand']['at'])
    expect(turned and 'walker heading from its order' in log()[-3000:] and abs(e['ax'] + 1.0) < 0.05, 'the new order counts at once: %r %s' % (e, log()[-1500:]))
    stop_walker()
    for _ in range(21):
        tick()


    # --- build55 --------------------------------------------------------------------------------
    import math as _m
    import re as _re

    def lines_since(mark, key=None):
        return [l for l in log()[mark:].splitlines() if (key is None or key in l) and 'units with open lanes' not in l and 'PERF' not in l]

    A = u['aside']  # (it has never made way: no pace, places or "seen" left from earlier tests)

    # 1. a ROW of units (the 2nd unit took the 1st one, already split, for a wall beside it and closed up to 50 %):
    # walk2 stands 2 m behind stand as a split rank (+-4.85 m around x = 4), the walker (one body, radius 4.6 -> a 9.7 m
    # lane) goes through both: stand has room. Control: walk2 right beside stand's flank -> it closes up
    old_r = mem.read(w['model'] + 0xa0, 4)
    mem.write(w['model'] + 0xa0, struct.pack('<f', 4.6))
    w['men'] = 1
    old_rank = set_list('stand', [new_model(x, 1.0, 0.65) for x in (-10.0, -3.0, 4.0, 11.0, 18.0)])
    for name, xs, zz, cx, want in (('row', (-14.85, -7.85, 8.85, 15.85, 22.85), 3.0, 4.0, 'room enough'),
                                   ('beside', (26.0, 33.0, 40.0, 47.0, 54.0), 1.0, 40.0, 'closes up')):
        old2 = set_list('walk2', [new_model(x, zz, 0.65) for x in xs])
        w2.update(dict(x=cx, z=zz, gx=cx, gz=zz, moving=False, men=5))
        w.update(dict(x=4.0, z=-60.0, gx=4.0, gz=-60.0, moving=False))
        for _ in range(80):
            tick()
        mark = len(log())
        w.update(dict(x=4.0, z=-40.0, gx=4.0, gz=80.0, moving=True))
        for _ in range(80):
            w['z'] += 0.6
            tick()
            if lane('stand') > 0:
                break
        opened = lines_since(mark, 'OPEN  A1.1.1 stand')
        expect(opened and want in opened[0], '%s: %s' % (name, opened[0][opened[0].find('beside it'):][:90] if opened else log()[-1500:]))
        w['moving'] = False
        for _ in range(80):
            tick()
        restore_list('walk2', old2)
        park((w2, -300.0))
    restore_list('stand', old_rank)
    mem.write(w['model'] + 0xa0, old_r)
    w['men'] = 100
    for _ in range(80):
        tick()

    # 2. the lanes stay while the walker still comes along its order line (its heading turned to its movement, passing()
    # failed and the unit closed before it came: 00:02:43). The widths are NARROW on purpose: passing() forgives s.half
    # + w.half / 2 sideways, so a wide unit would never fail it
    A['width'] = 20.0
    old_a = set_list('aside', [new_model(x, 1.0, 0.65) for x in (192.0, 196.0, 200.0, 204.0, 208.0)])
    template('aside')
    old_r = mem.read(w['model'] + 0xa0, 4)
    mem.write(w['model'] + 0xa0, struct.pack('<f', 3.0))
    w.update(dict(men=1, width=6.0, x=200.0, z=-60.0, gx=200.0, gz=-60.0, moving=False, jx=0.0))
    for _ in range(30):
        tick()
    mark = len(log())
    w.update(dict(x=200.0, z=-50.0, gx=200.0, gz=80.0, moving=True))
    opened = None
    for _ in range(80):
        w['z'] += 1.2
        tick()
        if lane('aside') > 0:
            opened = w['z']
            break
    closing_during = False
    for _ in range(16):  # 60 deg off its order line, still coming
        w['z'] += 0.3
        w['jx'] += 0.3 * _m.tan(_m.radians(60))
        tick()
        if lines_since(mark, 'CLOSING A1.1.3 aside'):
            closing_during = True
    w['jx'] = 0.0
    for _ in range(60):
        w['z'] += 0.6
        tick()
    closings = lines_since(mark, 'CLOSING A1.1.3 aside')
    rear = closings and _re.search(r'its rear ([-\d.]+) m', closings[0])
    expect(opened is not None and not closing_during and rear and float(rear.group(1)) >= 0,
           'held while it still comes: %r %r %s' % (opened, closing_during, closings[:1]))
    w.update(dict(moving=False, x=0.0, z=-80.0, gx=0.0, gz=-80.0))
    for _ in range(60):
        tick()
    restore_list('aside', old_a)
    A['width'] = 40.0
    mem.write(w['model'] + 0xa0, old_r)
    w.update(dict(men=100, width=30.0))
    for _ in range(80):
        tick()

    # 3. the heading turns to the movement only after 5 looks off the order line (one sample did, while the walker
    # squeezed into columns: 00:04:16). The opening time is fixed by the stand count (aside stands 20 looks after its
    # melee ends); 3 looks of 40 deg drift just before it: its order; 6 looks: its movement
    old_a = set_list('aside', [new_model(x, 1.0, 0.65) for x in (184.0, 192.0, 200.0, 208.0, 216.0)])
    template('aside')
    for drift, want in ((3, 'heading from its order'), (6, 'heading from its movement')):
        w.update(dict(men=100, x=200.0, z=-60.0, gx=200.0, gz=-60.0, moving=False, jx=0.0))
        A['melee'] = True
        for _ in range(30):
            tick()
        mark = len(log())
        w.update(dict(z=-24.3 - 0.6 * (drift - 3), gz=80.0, moving=True))
        for _ in range(10):
            w['z'] += 0.6
            tick()
        A['melee'] = False
        for i in range(30):
            w['z'] += 0.6
            if i >= 20 - drift:
                w['jx'] += 0.5
            tick()
            if lane('aside') > 0:
                break
        e = entry_of(mem, tbl, A['at'])
        opened = lines_since(mark, 'OPEN  A1.1.3')
        expect(opened and want in opened[0] and (drift > 5 or abs(e['ax'] + 1.0) < 0.05), 'drift of %d looks: %r %s' % (drift, e, opened[:1]))
        w['jx'] = 0.0
        w.update(dict(moving=False, x=0.0, z=-80.0, gx=0.0, gz=-80.0))
        for _ in range(80):
            tick()

    # 4. a walker that starts running from a standstill (the measured speed lags behind, the lanes opened late): for 5 s
    # after its order it counts at least its nominal speed. The real margin (time to contact from its steps still to
    # come, minus the split) must be >= 0.7 s
    w.update(dict(men=100, x=200.0, z=-18.3, gx=200.0, gz=-18.3, moving=False, jx=0.0, fast=True))
    for _ in range(30):
        tick()
    mark = len(log())
    w.update(dict(gz=80.0, moving=True))
    steps = [min(1.2, 0.2 * (k + 1)) for k in range(200)]
    opened = None
    for i in range(100):
        w['z'] += steps[i]
        tick()
        if lane('aside') > 0:
            opened = i
            break
    line = lines_since(mark, 'OPEN  A1.1.3')
    t, k, left = 0.0, (opened or 0) + 1, -0.3 - w['z']
    while left > 0:
        left -= steps[k]
        k += 1
        t += 0.2
    split = line and _re.search(r'split takes ([\d.]+) s', line[0])
    expect(opened is not None and split and t - float(split.group(1)) >= 0.7, 'accelerating walker: margin %.2f %s' % (t - float(split.group(1)) if split else -1, line[:1]))
    w.update(dict(moving=False, x=0.0, z=-80.0, gx=0.0, gz=-80.0, fast=False))
    for _ in range(80):
        tick()


    # a walker that DRIVES AN ARC onto its new order (run 35: Ikit Claw's Doomwheel turned for several seconds; after
    # TURN_TICKS its heading came from its movement and the clanrats opened lanes across his arc, 30-40 deg off his
    # way): while its direction comes closer to its order line look by look it is turning, its way is the order line
    old_a = set_list('aside', [new_model(x, 1.0, 0.65) for x in (184.0, 192.0, 200.0, 208.0, 216.0)])
    template('aside')
    old_r = mem.read(w['model'] + 0xa0, 4)
    mem.write(w['model'] + 0xa0, struct.pack('<f', 3.0))
    w.update(dict(men=1, width=6.0, x=185.0, z=-38.0, gx=185.0, gz=-38.0, moving=False, jx=0.0, fast=True))
    for _ in range(30):
        tick()
    mark = len(log())
    w.update(dict(gx=200.0, gz=60.0, moving=True))
    ang = 75.0  # degrees off the line to its goal, shrinking 2.5 per look
    opened = None
    for i in range(60):
        gx, gz = 200.0 - w['x'], 60.0 - w['z']
        base = _m.atan2(gx, gz)
        a = base + _m.radians(ang)
        w['x'] += 1.2 * _m.sin(a)
        w['z'] += 1.2 * _m.cos(a)
        ang = max(0.0, ang - 2.5)
        tick()
        if lane('aside') > 0:
            opened = i
            break
    e = entry_of(mem, tbl, A['at'])
    line = lines_since(mark, 'OPEN  A1.1.3')
    ox, oz = 200.0 - w['x'], 60.0 - w['z']
    norm = _m.hypot(ox, oz)
    want_ax, want_az = -oz / norm, ox / norm  # across the ORDER line
    expect(opened is not None and line and 'heading from its order' in line[0] and abs(e['ax'] - want_ax) < 0.1 and abs(e['az'] - want_az) < 0.1,
           'arc onto the order (opened at look %r, %.0f deg left): %r want (%.2f, %.2f) %s' % (opened, ang, e, want_ax, want_az, line[:1]))
    w.update(dict(moving=False, x=0.0, z=-80.0, gx=0.0, gz=-80.0, fast=False))
    for _ in range(80):
        tick()
    restore_list('aside', old_a)
    mem.write(w['model'] + 0xa0, old_r)
    w.update(dict(men=100, width=30.0))
    for _ in range(80):
        tick()

    # far from opening a standing unit plans only every 3rd look: a walker 20 s away, 25 looks of it in one PERF line ->
    # about 9 plans, not 25
    for _ in range(80):
        tick()
    w.update(dict(men=100, x=0.0, z=-66.0, gx=0.0, gz=80.0, moving=True, melee=False, jx=0.0))
    mark = len(log())
    for _ in range(60):
        w['z'] += 0.6
        tick()
    counts = [int(_re.search(r'(\d+) plans', l).group(1)) for l in log()[mark:].splitlines() if ' PERF ' in l and 'plans' in l]
    expect(len(counts) >= 2 and 5 <= counts[1] <= 10 and lane('stand') == 0, 'plans per 25 looks while far: %r' % counts)
    stop_walker()
    for _ in range(21):
        tick()

    # a unit that shoots makes way all the same
    A['ammo'] = 10
    for _ in range(30):
        tick()
    mark = len(log())
    A['ammo'] = 9
    w.update(dict(men=100, x=200.0, z=-30.0, gx=200.0, gz=80.0, moving=True, jx=0.0))
    opened = False
    for _ in range(40):
        w['z'] += 0.6
        if _ % 5 == 0:
            A['ammo'] -= 1  # it goes on shooting
        tick()
        if lane('aside') > 0:
            opened = True
            break
    expect(opened and not lines_since(mark, 'NOT STANDING A1.1.3 aside'), 'a shooting unit makes way: ' + '\n'.join(lines_since(mark))[-1500:])
    w.update(dict(moving=False, x=0.0, z=-80.0, gx=0.0, gz=-80.0))
    A['ammo'] = 0
    for _ in range(80):
        tick()

    # a unit the AI holds: it does not move, but the game says it is not idle - it stands after 4 s all the same and
    # makes way (its order points 30 m away: the AI's, not where it stands - else the SETTLED rule already counts it as
    # standing)
    mark = len(log())
    A['notidle'], A['gx'] = True, A['x'] + 30.0
    for _ in range(30):
        tick()
    w.update(dict(men=100, x=200.0, z=-30.0, gx=200.0, gz=80.0, moving=True, jx=0.0))
    opened = False
    for _ in range(40):
        w['z'] += 0.6
        tick()
        if lane('aside') > 0:
            opened = True
            break
    expect(lines_since(mark, 'A1.1.3 aside stands although the game says it is not idle'),
           'not idle, not moving: ' + '\n'.join(lines_since(mark))[-1500:])
    expect(opened, 'the AI-held unit makes way: ' + '\n'.join(lines_since(mark))[-1500:])
    # the AI moves its order 3 m while the lanes are open: they stay open
    mark2 = len(log())
    A['gx'] += 3.0
    for _ in range(3):
        w['z'] += 0.6
        tick()
    expect(not lines_since(mark2, 'CLOSE A1.1.3 aside') and lane('aside') > 0, 'a small re-order of the AI: ' + '\n'.join(lines_since(mark2))[-1500:])
    A['gx'] -= 3.0
    w.update(dict(moving=False, x=0.0, z=-80.0, gx=0.0, gz=-80.0))
    A['notidle'], A['gx'] = False, A['x']
    for _ in range(80):
        tick()
    # held, then idle for a moment, then not idle again at the same place: it does NOT stand at once on the place noted
    # before (its 4 s start again)
    A['notidle'], A['gx'] = True, A['x'] + 30.0
    for _ in range(30):
        tick()
    A['notidle'] = False
    tick()
    tick()
    mark = len(log())
    A['notidle'] = True
    tick()
    expect(lines_since(mark, 'NOT STANDING A1.1.3 aside any more: the game says it is not idle'), 'no stale held place: ' + '\n'.join(lines_since(mark))[-1500:])
    A['notidle'], A['gx'] = False, A['x']
    for _ in range(40):
        tick()

    # 6a. unit:position() of a standing unit jumps without its models (run 34: Rat Ogres 7-9 m right after a close):
    # it goes on standing; a real teleport moves the models and restarts the count; a model that dies (another first
    # model) is no jump
    mark = len(log())
    A['jx'] = 7.4
    for _ in range(3):
        tick()
    A['jx'] = 0.0
    w.update(dict(men=100, x=200.0, z=-30.0, gx=200.0, gz=80.0, moving=True))
    opened = False
    for _ in range(40):
        w['z'] += 0.6
        tick()
        if lane('aside') > 0:
            opened = True
            break
    expect(opened and not lines_since(mark, 'NOT STANDING A1.1.3 aside') and lines_since(mark, 'its position jumped 7.4 m but its models did not'),
           'position jitter alone: ' + '\n'.join(lines_since(mark))[-1500:])
    w.update(dict(moving=False, x=0.0, z=-80.0, gx=0.0, gz=-80.0))
    for _ in range(80):
        tick()
    mark = len(log())
    rank = [struct.unpack_from('<Q', mem.read(struct.unpack('<Q', mem.read(A['at'] + 0xa8, 8))[0] + 8 * i, 8))[0] for i in range(5)]
    A['x'] += 12.0
    tick()
    tick()
    for m in rank:
        mx, my, mz = struct.unpack('<3f', mem.read(m + 0x88, 12))
        mem.write(m + 0x88, struct.pack('<3f', mx + 12.0, my, mz))
    tick()
    expect(lines_since(mark, 'NOT STANDING A1.1.3 aside any more: its models jumped 12.0 m'), 'a teleport: ' + '\n'.join(lines_since(mark))[-1500:])
    # (log) a friendly walker inside it meanwhile: a MISSED line with the reason (and the log said nothing)
    w.update(dict(men=100, x=A['x'] + 3.0, z=1.0, gx=A['x'] + 3.0, gz=80.0, moving=True))
    for _ in range(6):
        tick()
    missed = lines_since(mark, 'MISSED? A1.1.2 walk is inside A1.1.3 aside')
    expect(missed and 'no lanes are open: it does not stand: it has stood only' in missed[0] and '(its models jumped then)' in missed[0], 'missed: ' + '\n'.join(lines_since(mark))[-1500:])
    w.update(dict(moving=False, x=0.0, z=-80.0, gx=0.0, gz=-80.0))
    tick()
    for m in rank:
        mx, my, mz = struct.unpack('<3f', mem.read(m + 0x88, 12))
        mem.write(m + 0x88, struct.pack('<3f', mx - 12.0, my, mz))
    A['x'] -= 12.0
    for _ in range(30):
        tick()
    mark = len(log())
    set_list('aside', rank[1:])  # its first model died
    for _ in range(3):
        tick()
    expect(not lines_since(mark, 'NOT STANDING A1.1.3 aside'), 'a model died: ' + '\n'.join(lines_since(mark))[-1500:])
    restore_list('aside', old_a)
    for _ in range(30):
        tick()

    # 6b. an infantry walker behind another through lanes of 2: the right reason
    mark = len(log())
    w.update(dict(men=100, x=0.0, z=-30.0, gx=0.0, gz=80.0, moving=True))
    w2.update(dict(men=100, x=0.0, z=-36.0, gx=0.0, gz=80.0, moving=True))
    for _ in range(40):
        w['z'] += 0.6
        w2['z'] += 0.6
        tick()
    expect(lines_since(mark, 'further walker A1.1.4 walk2 through A1.1.1 stand: not a big walker'), 'reason: ' + '\n'.join(lines_since(mark, 'walk2'))[-1500:])
    park((w2, -300.0))
    w2['men'] = 1
    stop_walker()
    for _ in range(80):
        tick()

    # 6c. COLUMNS counts the standing models reaching into each lane (the narrowest gap read one straggler as a closed
    # lane): one straggler in the lane at x = 9.67 (k = -1), the others in the blocks (two ranks, 3 m apart: the walker
    # is inside its depth for a few looks)
    old_rank = set_list('stand', [new_model(x, z, 0.65) for x, z in ((-18.0, 1.0), (0.0, 1.0), (18.0, 1.0), (9.67, 1.0), (-18.0, 4.0), (0.0, 4.0), (18.0, 4.0))])
    for _ in range(80):
        tick()
    mark = len(log())
    w.update(dict(men=100, x=0.0, z=-30.0, gx=0.0, gz=80.0, moving=True))
    for _ in range(60):
        w['z'] += 0.6
        tick()
        if lines_since(mark, 'COLUMNS A1.1.2 walk in A1.1.1 stand'):
            break
    cols = lines_since(mark, 'COLUMNS A1.1.2 walk in A1.1.1 stand')
    expect(cols and 'standing models reaching into the lanes now 1 / 0 (of 7 within its depth' in cols[0], 'lane count: %s' % cols[:1])
    stop_walker()
    restore_list('stand', old_rank)
    for _ in range(80):
        tick()

    # models read far from their unit's middle (run 30: right after a teleport) and a model whose place is not a
    # number: left out of the timing, the walker's line and the jobs
    far_stand = new_model(0.0, -100.0, 0.65)  # would be "ahead" of the walker: contact at once
    nan_model = new_model(8.0, 1.0, 0.65)
    mem.write(nan_model + 0x88, b'\xff\xff\xff\xff')
    inf_model = new_model(8.0, 1.0, 0.65)
    mem.write(inf_model + 0x88, b'\x00\x00\x80\x7f')  # +infinity
    old_s = set_list('stand', [u['stand']['model'], far_stand, nan_model, inf_model])
    far_walk = new_model(150.0, 60.0, 0.65)  # would move the walker's middle 75 m aside
    old_w = set_list('walk', [w['model'], far_walk])
    for _ in range(80):
        tick()
    w.update(dict(x=0.0, z=-30.0, gx=0.0, gz=80.0, moving=True, melee=False))
    opened_at = None
    for _ in range(60):
        w['z'] += 0.6
        tick()
        if lane('stand') > 0:
            opened_at = w['z']
            break
    e = entry_of(mem, tbl, u['stand']['at'])
    # (not at once, as the far model "ahead" would make it; the fronts measured from the near models only: the
    # standing model at z = 1, both radius 0.65)
    expect(opened_at is not None and opened_at < -12.0 and ('fronts %.1f m apart' % (1.0 - 1.3 - opened_at)) in log()[-3000:],
           'on time despite the far models: %r %s' % (opened_at, log()[-1500:]))
    expect(abs(e['ox']) < 1e-3 and e['njobs'] == 1, 'the walker\'s line through its near models, one job (no far or unreadable model): %r' % e)
    stop_walker()
    restore_list('stand', old_s)
    restore_list('walk', old_w)
    for _ in range(21):
        tick()

    # STANDING ARTILLERY: 3 machines 12 m apart, 4 crewmen each (2 at the machine, 2 beside it)
    g = u['guns']
    crew, machines = [], []
    for gx in (-12.0, 0.0, 12.0):
        machine = new_model(gx, 400.0, 1.5)
        machines.append(machine)
        mem.write(machine + 0x2e8, struct.pack('<Q', g['at']))
        for cx, cz in ((0.5, -1.0), (-0.3, -1.2), (3.0, -1.0), (-4.0, 0.5)):
            crew.append(new_model(gx + cx, 400.0 + cz, 0.5, carrier=machine))
    set_list('guns', crew)
    template('guns')
    w.update(dict(x=6.0, z=360.0, gx=6.0, gz=480.0, moving=True, melee=False, men=100))
    for _ in range(100):
        w['z'] += 0.6
        tick()
        if 'OPEN  A1.1.5 guns' in log():  # (its E_L stays 0: the machines stay, the jobs move the crew)
            break
    e = entry_of(mem, tbl, g['at'])
    expect(lane('guns') == 0 and e['n'] == 0 and 'the gaps of its 3 machines' in log(), 'artillery opens (its own places stay: E_L 0): %r %s' % (e, log()[-1500:]))
    expect(e['njobs'] == 6 and e['pending'] == 6, 'jobs only for the 6 crewmen beside their machines: %r' % e)
    jobbed = {struct.unpack_from('<Q', mem.read(e['jobs'] + i * lanes.JOB, 8))[0] for i in range(6)}
    expect(jobbed == {crew[i] for i in range(12) if i % 4 >= 2}, 'the crewmen beside the machines')
    expect(e['dirty'] == 0, 'no forced handing out of places for artillery (they are not shifted)')
    # while the lanes are open the moved crewmen get their jobs again every second (else the game's 5 s test sends
    # them back into the lanes)
    gat = e['at']
    mem.write(gat + lanes.E_PENDING, struct.pack('<I', 0))  # cave 3 took them
    for _ in range(6):
        w['z'] += 0.6
        tick()
    e = entry_of(mem, tbl, g['at'])
    expect(e['pending'] == 6, 'jobs again after a second: %r' % e)
    mem.write(machines[0] + 0x88, struct.pack('<3f', -10.0, 0.0, 400.0))  # the first machine is pushed 2 m
    mem.write(crew[7] + 0x2e0, struct.pack('<Q', machines[2])) # a crewman now belongs to another machine
    closed = False
    for _ in range(300):
        if w['z'] < 470.0:
            w['z'] += 0.6
        else:
            w['moving'] = False
        tick()
        if 'CLOSE A1.1.5 guns' in log():
            closed = True
            break
    e = entry_of(mem, tbl, g['at'])
    # (the closing had the game hand out the places again)
    expect(closed and "5 crewmen sent back beside their machines: the game's places are left alone" in log() and e['dirty'] == 0 and e['pending'] == 5,
           'closing: the moved crewmen go back by jobs, the game hands out nothing: %r %s' % (e, log()[-1500:]))
    back = {}
    for i in range(e['njobs']):
        model = struct.unpack_from('<Q', mem.read(e['jobs'] + i * lanes.JOB, 8))[0]
        back[model] = struct.unpack('<3f', mem.read(e['jobs'] + i * lanes.JOB + lanes.JOB_RECORD, 12))
    expect(set(back) == {crew[i] for i in range(12) if i % 4 >= 2 and i != 7}, 'the moved crewmen, not the one of another machine now: %r' % back)
    for model, want in ((crew[2], (-7.0, 399.0)), (crew[3], (-14.0, 400.5)), (crew[6], (3.0, 399.0))):
        got = back[model]
        expect(abs(got[0] - want[0]) < 1e-3 and abs(got[2] - want[1]) < 1e-3, 'beside its machine as it stands now: %r, want %r' % (got, want))
    mem.write(gat + lanes.E_PENDING, struct.pack('<I', 0))  # cave 3 took them
    mem.write(crew[7] + 0x2e0, struct.pack('<Q', machines[1]))
    mem.write(machines[0] + 0x88, struct.pack('<3f', -12.0, 0.0, 400.0))
    for _ in range(21):
        tick()

    # the same guns with their whole crew AT the machines (after a teleport the crew walked away from its guns when the
    # closing had the game hand out its places again): nobody has to move -> no job, and the closing leaves the game's
    # places alone (no E_DIRTY)
    crew2 = []
    for gx in (-12.0, 0.0, 12.0):
        machine = new_model(gx, 400.0, 1.5)
        mem.write(machine + 0x2e8, struct.pack('<Q', g['at']))
        for cx, cz in ((0.5, -1.0), (-0.3, -1.2)):
            crew2.append(new_model(gx + cx, 400.0 + cz, 0.5, carrier=machine))
    set_list('guns', crew2)
    for _ in range(80):  # (the places noted at the last opening are forgotten)
        tick()
    mem.write(gat + lanes.E_DIRTY, struct.pack('<I', 0))  # (what the cave does after the last closing)
    mark = len(log())
    w.update(dict(x=6.0, z=360.0, gx=6.0, gz=480.0, moving=True, melee=False, men=100))
    closed = False
    for _ in range(400):
        if w['z'] < 470.0:
            w['z'] += 0.6
        else:
            w['moving'] = False
        tick()
        if 'CLOSE A1.1.5 guns' in log()[mark:]:
            closed = True
            break
    e = entry_of(mem, tbl, g['at'])
    expect(closed and 'nobody had to move' in log()[mark:] and 'no crewman was moved: the game\'s places are left alone' in log()[mark:] and e['dirty'] == 0,
           'crew at its guns: %r %s' % (e, log()[mark:][-1500:]))
    for _ in range(21):
        tick()

    # (log) a crewman 20 m from its machine: a CREW line says so
    mark = len(log())
    cx0, cy0, cz0 = struct.unpack('<3f', mem.read(crew2[0] + 0x88, 12))
    mem.write(crew2[0] + 0x88, struct.pack('<3f', cx0 + 20.0, cy0, cz0))
    for _ in range(30):
        tick()
    expect(lines_since(mark, 'CREW A1.1.5 guns: 1 of 6 crewmen more than 8 m from their machines, the farthest 20.'), 'crew away: ' + '\n'.join(lines_since(mark))[-1500:])
    expect(' its lanes closed ' in lines_since(mark, 'CREW A1.1.5')[0], lines_since(mark, 'CREW')[:1])
    mem.write(crew2[0] + 0x88, struct.pack('<3f', cx0, cy0, cz0))
    for _ in range(30):
        tick()
    expect(lines_since(mark, 'CREW A1.1.5 guns: 0 of 6'), 'back at its machine: ' + '\n'.join(lines_since(mark, 'CREW')))

    # artillery that SHOOTS while its lanes are open: its lanes stay open, no closing because of the shot
    set_list('guns', crew)
    mem.write(machines[0] + 0x88, struct.pack('<3f', -12.0, 0.0, 400.0))  # (all machines where they were)
    g['ammo'] = 10
    for _ in range(80):  # (the places noted at the last opening are forgotten)
        tick()
    mark = len(log())
    w.update(dict(x=6.0, z=360.0, gx=6.0, gz=480.0, moving=True, melee=False, men=100))
    for _ in range(100):
        w['z'] += 0.6
        tick()
        if 'OPEN  A1.1.5 guns' in log()[mark:]:
            break
    mem.write(gat + lanes.E_PENDING, struct.pack('<I', 0))  # cave 3 took them
    for _ in range(3):
        w['z'] += 0.6
        tick()
    g['ammo'] = 9  # it shoots
    for _ in range(5):
        w['z'] += 0.6
        tick()
    expect(not lines_since(mark, 'CLOSE A1.1.5 guns') and not lines_since(mark, 'NOT STANDING A1.1.5 guns'),
           'a shot while open: ' + '\n'.join(lines_since(mark))[-1500:])
    for _ in range(300):  # (the walker passes; the lanes close as usual)
        if w['z'] < 470.0:
            w['z'] += 0.6
        else:
            w['moving'] = False
        tick()
        if 'CLOSE A1.1.5 guns' in log()[mark:]:
            break
    mem.write(gat + lanes.E_PENDING, struct.pack('<I', 0))
    w.update(dict(moving=False, x=0.0, z=-80.0, gx=0.0, gz=-80.0))
    g['ammo'] = 0
    set_list('guns', crew2)
    for _ in range(80):
        tick()

    # WALKING artillery as wide as the whole standing front: 4 machines of radius 3.5, 11.4 m apart (warp lightning
    # cannons ran into stormvermin 2.4 s after the opening, the split of 20 m took 6.4 s)
    wm, wcrew = [], []
    for gx in (-17.1, -5.7, 5.7, 17.1):
        machine = new_model(gx, 0.0, 3.5)
        wm.append(machine)
        mem.write(machine + 0x2e8, struct.pack('<Q', g['at']))
        for cx in (-1.0, 1.0):
            wcrew.append(new_model(gx + cx, -1.0, 0.5, carrier=machine))
    set_list('guns', wcrew)

    offsets = {m: struct.unpack('<3f', mem.read(m + 0x88, 12)) for m in wm + wcrew}  # (around z = 0)

    def guns_to(z):
        g['z'] = z
        for m, (x0, y0, z0) in offsets.items():
            mem.write(m + 0x88, struct.pack('<3f', x0, y0, z + z0))

    def guns_drive(z, step, ticks, stop_at=None):
        g.update(dict(x=0.0, z=z, gx=0.0, gz=80.0, moving=True, fast=step > 1.0, melee=False, men=8))
        guns_to(z)
        for _ in range(ticks):
            guns_to(g['z'] + step)
            tick()
            if stop_at and stop_at in log()[mark:]:
                return True
        return False

    def guns_park():
        g.update(dict(moving=False, fast=False, x=0.0, z=400.0, gx=0.0, gz=400.0))
        guns_to(400.0)
        for _ in range(40):
            tick()

    # 1. it comes running, much too close for the split (as in run 42: contact 2.4 s into a split of 6.4 s, less than
    # TUNE.LATE_SHARE of it): no lanes, the game's own way
    mark = len(log())
    guns_drive(-12.0, 1.8, 2)
    text = '\n'.join(lines_since(mark, 'NO LANES A1.1.1 stand for A1.1.5 guns'))
    expect('too late for a lane as wide as its whole front' in text and 'left out while it passes' in text and not lines_since(mark, 'OPEN  A1.1.1 stand'),
           'late, as wide as the front: no lanes: ' + (text or log()[mark:][-3000:]))
    # ... and a unit FOLLOWING it gets its lanes while the guns still cross; the guns do not widen them and get no
    # second NO LANES line - also when, inside the unit, the guns stop moving for a look and the standing unit stops
    # standing for a look (run 43: the bloodcrushers' exclusion dropped while they were inside the ironsworn and the
    # lanes opened round them)
    for i in range(12):  # (the guns roll into the unit, no lanes open: the MISSED? line gives the reason)
        guns_to(min(g['z'] + 1.8, -2.0))
        g['moving'] = i != 6
        u['stand']['moving'] = i == 8
        tick()
    g['moving'], u['stand']['moving'] = True, False
    w.update(dict(x=0.0, z=-45.0, gx=0.0, gz=80.0, moving=True, melee=False, men=100))
    followed = False
    for _ in range(80):
        guns_to(g['z'] + 0.05)
        w['z'] += 0.6
        tick()
        if 'OPEN  A1.1.1 stand makes way for A1.1.2 walk' in log()[mark:]:
            followed = True
            break
    expect(followed and g['z'] < 5.0, 'the follower gets lanes while the guns (z %.1f) are still inside: %s' % (g['z'], log()[mark:][-3000:]))
    expect(len(lines_since(mark, 'NO LANES A1.1.1 stand for A1.1.5 guns')) == 1 and not lines_since(mark, 'WIDER A1.1.1 stand for A1.1.5 guns')
           and not lines_since(mark, 'OPEN  A1.1.1 stand makes way for A1.1.5 guns'),
           'the late guns stay left out through the flickers: ' + '\n'.join(lines_since(mark, 'A1.1.5 guns')))
    missed = lines_since(mark, 'MISSED? A1.1.5 guns is inside A1.1.1 stand')
    expect(missed and all('left out: too late for a lane as wide as its whole front' in l for l in missed), 'the MISSED? reason of the late guns: %r' % missed)
    guns_park()
    stop_walker()
    # 1b. a follower of ONE lane (a single model: the kind a further walker can widen) gets it first; then guns that
    # were never refused come running, too late: they do not widen it (try_widen has the rule too) (guns 8 m apart and a
    # standing front of 30 m: the widened lane, 30-40 m, is allowed but takes the whole front)
    wide_offsets = dict(offsets)
    for m, (x0, y0, z0) in wide_offsets.items():
        offsets[m] = (x0 * 0.7, y0, z0)
    u['stand']['width'] = 30.0
    for _ in range(25):  # (it stands again after the flicker of case 1)
        tick()
    mark = len(log())
    w.update(dict(x=0.0, z=-40.0, gx=0.0, gz=80.0, moving=True, melee=False, men=1))
    followed = False
    for _ in range(80):
        w['z'] += 0.6
        tick()
        if 'OPEN  A1.1.1 stand makes way for A1.1.2 walk' in log()[mark:]:
            followed = True
            break
    expect(followed, 'the single follower opens: ' + log()[mark:][-1500:])
    guns_drive(-12.0, 1.8, 3)
    expect(lines_since(mark, 'further walker A1.1.5 guns through A1.1.1 stand: too late for a lane as wide as its whole front')
           and not lines_since(mark, 'WIDER A1.1.1 stand for A1.1.5 guns'), 'late guns do not widen: ' + '\n'.join(lines_since(mark, 'A1.1.5 guns')))
    guns_park()
    stop_walker()
    w['men'] = 100
    offsets.update(wide_offsets)
    u['stand']['width'] = 40.0
    # 1c. late, but not hopeless (bloodcrushers, contact 5.0 s into a split of 8.3 s, were refused and pushed through as
    # vanilla): contact 2.3 s into a split of 3.9 s, more than TUNE.LATE_SHARE: it opens
    mark = len(log())
    opened = guns_drive(-18.0, 1.8, 6, 'OPEN  A1.1.1 stand makes way for A1.1.5 guns')
    text = '\n'.join(lines_since(mark, 'OPEN  A1.1.1 stand'))
    expect(opened and 'ONE lane of 40.0 m' in text and not lines_since(mark, 'NO LANES A1.1.1 stand for A1.1.5 guns'), 'late but not hopeless: it opens: ' + (text or log()[mark:][-1500:]))
    guns_park()
    stop_walker()
    # 1d. refused from FAR out (fast guns of a narrow order, 30 m/s), the AI re-ordering them by a few metres every look
    # on the way in: one NO LANES line, not one per look (the exclusion dropped on the next look when the refusal came
    # further out than both half widths + TUNE.LATE_CLEAR, and on every re-order of more than 0.5 m)
    u['guns']['width'], g['fastv'] = 4.0, 30
    mark = len(log())
    g.update(dict(x=0.0, z=-56.0, gx=0.0, gz=80.0, moving=True, fast=True, melee=False, men=8))
    guns_to(-56.0)
    for i in range(14):
        guns_to(min(g['z'] + 6.0, -2.0))
        g['gz'] = 80.0 + (2.0 if i % 2 else -2.0)
        tick()
    refused = lines_since(mark, 'NO LANES A1.1.1 stand for A1.1.5 guns')
    expect(len(refused) == 1 and 'too late for a lane as wide as its whole front' in refused[0] and not lines_since(mark, 'OPEN  A1.1.1 stand makes way for A1.1.5 guns'),
           'refused far out, re-ordered on the way: one NO LANES line: %r' % refused)
    g['gz'], g['fastv'], u['guns']['width'] = 80.0, 6, 40.0
    guns_park()
    stop_walker()
    # 2. only a little late (contact 0.1 s before the split is done): it still opens
    mark = len(log())
    opened = guns_drive(-27.0, 1.8, 12, 'OPEN  A1.1.1 stand makes way for A1.1.5 guns')
    expect(opened and not lines_since(mark, 'NO LANES A1.1.1 stand for A1.1.5 guns'), 'a little late: it opens: ' + log()[mark:][-1500:])
    guns_park()
    stop_walker()
    # 3. it comes walking from further away: the one wide lane opens in time, as before
    mark = len(log())
    opened = guns_drive(-40.0, 0.6, 100, 'OPEN  A1.1.1 stand makes way for A1.1.5 guns')
    text = '\n'.join(lines_since(mark, 'OPEN  A1.1.1 stand'))
    expect(opened and 'ONE lane of 40.0 m' in text and not lines_since(mark, 'NO LANES A1.1.1 stand for A1.1.5 guns'), 'in time: one wide lane: ' + (text or log()[mark:][-1500:]))
    guns_park()
    stop_walker()
    for _ in range(21):
        tick()
    g.update(dict(moving=False, x=0.0, z=400.0, gx=0.0, gz=400.0, men=100))
    set_list('guns', crew2)
    for _ in range(80):
        tick()

    # a STANDING Doomwheel-like unit (is_war_machine, its crew rides ONE body): it does not make way at all; the walker
    # goes round it
    c = u['cart']
    body = new_model(308.0, 400.0, 2.0)
    mem.write(body + 0x2e8, struct.pack('<Q', c['at']))
    set_list('cart', [new_model(308.0, 400.0, 0.5, carrier=body) for _ in range(3)])
    c['men'] = 3
    template('cart')
    w.update(dict(x=300.0, z=360.0, gx=300.0, gz=480.0, moving=True, melee=False, men=100))
    for _ in range(60):
        w['z'] += 0.6
        tick()
    expect(lane('cart') == 0 and 'OPEN  A1.1.6 cart' not in log() and 'NO LANES A1.1.6 cart: one body (3 models) does not make way' in log(),
           'one body does not make way: ' + log()[-1500:])
    w['moving'] = False
    for _ in range(10):
        tick()
    # a standing chariot unit of TWO bodies: one job per body, not one per rider
    body2 = new_model(292.0, 400.0, 2.0)
    mem.write(body2 + 0x2e8, struct.pack('<Q', c['at']))
    set_list('cart', [new_model(308.0, 400.0, 0.5, carrier=body) for _ in range(3)] + [new_model(292.0, 400.0, 0.5, carrier=body2) for _ in range(2)])
    c['men'] = 5
    w.update(dict(x=300.0, z=360.0, gx=300.0, gz=480.0, moving=True, melee=False, men=100))
    for _ in range(100):
        w['z'] += 0.6
        tick()
        if lane('cart') > 0:
            break
    e = entry_of(mem, tbl, c['at'])
    jobbed = {struct.unpack_from('<Q', mem.read(e['jobs'] + i * lanes.JOB, 8))[0] for i in range(e['njobs'])}
    expect(e['njobs'] == 2 and jobbed == {body, body2}, 'one job for each of the chariots\' bodies: %r' % e)
    expect('2 models sent at once' in log() and ', 2 of them through their mounts' in log(), log()[-1500:])
    # it leaves the battle while its lanes are open and the walker forms columns: everything is shut
    expect(walker('walk')['q'] > 0, 'the walker forms columns')
    cat = e['at']
    c['men'] = None  # (its look fails)
    tick()
    expect('LEFT OUT A1.1.6 cart from now on' in log() and struct.unpack('<f', mem.read(cat + lanes.E_L, 4))[0] == 0
           and walker('walk')['q'] == 0 and struct.unpack('<Q', mem.read(cat, 8))[0] == 1, 'retired with lanes open: ' + log()[-1500:])
    stop_walker()
    for _ in range(21):
        tick()

    # a single monster gets one lane centred on its way; a turned unit is not sent at once
    w['x'], w['gx'], w['z'], w['men'], w['moving'] = 0.0, 0.0, -4.0, 1, True
    u['stand']['bearing'] = 45.0
    tick()
    e = entry_of(mem, tbl, u['stand']['at'])
    expect(lane('stand') > 0 and enabled() == 1, 'open before the end')
    expect(e['bias'] == -0.5 and e['n'] == 1 and e['p'] == 100000.0 and abs(e['ox'] - 50000.0) < 1 and e['pending'] == 0, 'one lane: %r' % e)
    expect('ONE lane of 1.8 m' in log() and 'NOT sent at once: the unit was turned since its places were last handed out' in log(), log())
    b['phases']['Complete']()
    expect(enabled() == 0 and lane('stand') == 0 and 'battle complete, mod off' in log() and TIMER not in b['timers'], 'everything closed at the end')
    # performance lines: every 5 s and for the whole battle
    expect('PERF ' in log() and 'game speed' in log() and 'looks over 4 ms' in log(), 'PERF lines: ' + log()[-1500:])
    expect('PERF BATTLE' in log() and 'of the real time' in log(), 'PERF BATTLE line: ' + log()[-1500:])
    expect('attempt to call global' not in log() and 'attempt to index global' not in log(), 'a function used before it is defined: ' + '\n'.join(l for l in log().splitlines() if 'global' in l)[:1500])

    # next battle of the session: the cave is reused and the old list is gone
    mem.write(tbl + lanes.ENABLE, struct.pack('<I', 1))
    before = dict(hooks)
    b = run_battle(mem, hooks, game_code, castes={'walk': None})  # the walker: a unit of another mod without a vanilla caste
    expect(hooks == before and 'reusing them' in log() and 'MOD OFF' not in log(), log())
    expect(enabled() == 0 and 'listed before the fighting: 8 units' in log(), 'off when the script loads')
    expect(struct.unpack('<Q', mem.read(tbl + lanes.POOL, 8))[0] == pool, 'the job memory is used again')
    old = entry_of(mem, tbl, u['stand']['at'])
    expect(old is None, 'the units of the last battle are gone from the list')
    b['phases']['Deployed']()
    w = b['units']['walk']
    w['z'] = -4.0
    for _ in range(21):  # the standing unit stands 4 s first (the battle clock stands still here: no movement measured)
        b['timers'][TIMER]()
    w['moving'], w['gz'] = True, 80.0
    mem.write(w['model'] + 0x88, struct.pack('<3f', 0.0, 0.0, -4.0))
    b['timers'][TIMER]()
    stand_at = b['units']['stand']['at']
    expect(entry_of(mem, tbl, stand_at)['lane'] > 0, 'works in the next battle: ' + log()[-1500:])
    # a unit of another mod without a vanilla caste walks like a monster: ONE lane, and it forms ONE column
    e = entry_of(mem, tbl, stand_at)
    wat = entry_of(mem, tbl, b['units']['walk']['at'])['at']
    q, kl, kh = struct.unpack('<f', mem.read(wat + lanes.E_WQ, 4))[0], *struct.unpack('<2i', mem.read(wat + lanes.E_WKL, 8))
    c = dict(q=q, kl=kl, kh=kh)
    expect('unit of another mod A1.1.2 walk: kind one (unit_class() gives nothing, unit_caste() gives nothing)' in log() and e['n'] == 1 and e['bias'] == -0.5, 'one lane: ' + log()[-1500:])
    expect(c['q'] > 0 and c['kl'] == 0 and c['kh'] == 0 and 'one column' in log(), 'one column: %r' % c)
    # an error in a tick closes everything
    b['lua'].execute('bm.battle = nil')
    for _ in range(60):
        if TIMER in b['timers']:
            b['timers'][TIMER]()
    expect('MOD OFF: tick' in log() and enabled() == 0 and entry_of(mem, tbl, stand_at)['lane'] == 0 and TIMER not in b['timers'], log())
    import re
    count = 0
    for line in SCRIPT.split('\n'):
        if re.match(r'local function \w+', line):
            count += 1
        else:
            m = re.match(r'local ([\w, ]+?)(=|$)', line)
            if m:
                count += len([x for x in m.group(1).split(',') if x.strip()])
    expect(count <= 192, 'the main chunk has %d locals (at most 200 load at all): make room' % count)
    # the walker's formation, measured from its models (lane width follows the files and ranks)
    part = SCRIPT.split('-- [formation part: tested on its own by the mock test]')
    expect(len(part) == 3, 'formation function marked')
    lua = lupa.LuaRuntime(unpack_returned_tuples=True)
    formation = lua.execute(part[1] + '\nreturn formation')

    def grid(files, ranks, step):  # a block walking along +z
        places = lua.table()
        n = 0
        for f in range(files):
            for r in range(ranks):
                n += 1
                places[n] = lua.table(f * step, 0.0, -r * step)
        return places
    got = formation(grid(24, 4, 1.2), 0.0, 1.0, 96)
    expect(got[0] == 24 and got[1] == 4 and abs(got[2] - 1.2) < 1e-6, 'wide and shallow: %r' % (got,))
    got = formation(grid(10, 10, 1.5), 0.0, 1.0, 100)
    expect(got[0] == 10 and got[1] == 10 and abs(got[2] - 1.5) < 1e-6, 'square: %r' % (got,))
    got = formation(grid(12, 5, 1.0), 1.0, 0.0, 60)  # walking along +x: its files lie along z
    expect(got[0] == 5 and got[1] == 12, 'turned: %r' % (got,))
    expect(formation(grid(1, 3, 1.0), 0.0, 1.0, 3) is None, 'too few models: not readable')
    # room beside a standing unit: the pure part on its own
    # a further walker while the lanes are open: the span it sweeps, and how the gap grows (the pure part on its own)
    part = SCRIPT.split('-- [merge part: tested on its own by the mock test]')
    expect(len(part) == 3, 'merge functions marked')
    ml = lupa.LuaRuntime(unpack_returned_tuples=True)
    sweep, widen = ml.execute(part[1] + '\nreturn sweep, widen')
    got = sweep(ml.table(ml.table(-8.0, 0.0, 0.0)), 0.0, 0.65)
    expect(abs(got[0] + 8.65) < 1e-9 and abs(got[1] + 7.35) < 1e-9, 'beside: %r' % (got,))
    # at 45 deg: from 2 to 12 m ahead it drifts 0.707 m across per metre
    got = sweep(ml.table(ml.table(0.0, 2.0, 12.0)), -0.5 ** 0.5, 0.65)
    expect(abs(got[0] - (-12 * 0.5 ** 0.5 - 0.65)) < 1e-9 and abs(got[1] - (-2 * 0.5 ** 0.5 + 0.65)) < 1e-9, 'at an angle: %r' % (got,))
    got = sweep(ml.table(ml.table(-2.0, 0.0, 5.0), ml.table(3.0, 0.0, 5.0)), 0.0, 1.0)
    expect(got == (-3.0, 4.0), 'two bodies: %r' % (got,))
    expect(sweep(ml.table(), 0.0, 1.0) is None, 'no body')
    got = widen(0.9, 0.9, -8.65, -7.35, 0.25, 40.0)
    expect(abs(got[0] - 8.9) < 1e-9 and abs(got[1] - 0.9) < 1e-9, 'grows to the left only: %r' % (got,))
    got = widen(0.9, 0.9, -9.13, 0.2, 0.25, 40.0)
    expect(abs(got[0] - 9.38) < 1e-9 and abs(got[1] - 0.9) < 1e-9, 'at an angle: %r' % (got,))
    got = widen(0.9, 0.9, -3.0, 4.0, 0.25, 40.0)
    expect(abs(got[0] - 3.25) < 1e-9 and abs(got[1] - 4.25) < 1e-9, 'both sides: %r' % (got,))
    got = widen(0.9, 0.9, -0.5, 0.6, 0.25, 40.0)
    expect(got == (None, 'fits'), 'fits: %r' % (got,))
    got = widen(0.9, 0.9, -50.0, 0.0, 0.25, 40.0)
    expect(got[0] is None and 'at most 40.0' in got[1], 'too wide: %r' % (got,))

    part = SCRIPT.split('-- [room part: tested on its own by the mock test]')
    expect(len(part) == 3, 'room function marked')
    rl = lupa.LuaRuntime(unpack_returned_tuples=True)
    room_of = rl.execute('local ROOM_MARGIN, MAX_CLOSE_UP = 0.3, 0.5\n' + part[1] + '\nreturn room_of')
    pts = lambda *xs: rl.table(*[rl.table(t, a) for t, a in xs])
    own = pts((0, -14), (0, 14), (2, 0), (-2, 5))  # a unit 28 m wide, the walker's line through its middle
    got = room_of(own, pts(), 6.5, 1.2)
    expect(got[0] == 0 and got[1] is None and got[2] is None, 'nobody beside it: %r' % (got,))
    got = room_of(own, pts((0, 30.0)), 6.5, 1.2)  # 16 m away: 7.4 m each, more than 6.5
    expect(got[0] == 0 and abs(got[2] - 14.8) < 1e-9 and got[1] is None, 'room enough: %r' % (got,))
    got = room_of(own, pts((1, 18.2), (40, -16)), 6.5, 1.2)  # 3 m gap on the right; the unit 40 m behind does not count
    # free 1.5 - 0.3 = 1.2 m of 6.5: G = (1.2 - 6.5) / 14
    expect(abs(got[0] - (1.2 - 6.5) / 14) < 1e-9 and abs(got[2] - 3.0) < 1e-9 and got[1] is None, 'closes up: %r' % (got,))
    got = room_of(own, pts((0, 15.5), (0, -15.5)), 6.5, 1.2)  # neighbours 0.3 m away on both sides: no room at all
    expect(abs(got[0] + 6.5 / 14) < 1e-9, 'closes up by the whole lane: %r' % (got,))
    got = room_of(own, pts((0, 15.5), (0, -15.5)), 10.0, 1.2)
    expect(got[0] == -0.5, 'at most half: %r' % (got,))
    # standing artillery: the lanes are the gaps between its machines (the pure part on its own)
    part = SCRIPT.split('-- [artillery part: tested on its own by the mock test]')
    expect(len(part) == 3, 'artillery functions marked')
    al = lupa.LuaRuntime(unpack_returned_tuples=True)
    gun_gaps, out_of_gap, into_strip = al.execute('local GUN_GAP_SLACK, GUN_MARGIN = 0.25, 0.2\n' + part[1] + '\nreturn gun_gaps, out_of_gap, into_strip')
    # 3 machines 12 m apart; the crews step in to their machines, so a lane of 3 m needs 3 + 2 x 1.5 + 0.4 = 6.4 m:
    # lanes between and beside them, as wide as wanted
    got = gun_gaps(al.table(10.0, -14.0, -2.0), 3.0, 1.5, 1.8)
    expect(got == (-14.0, 12.0, -1, 2, 3.0), 'three machines: %r' % (got,))
    got = gun_gaps(al.table(5.0), 3.0, 1.5, 1.8)
    expect(got[0] == 5.0 and abs(got[1] - 6.4) < 1e-9 and got[2:] == (-1, 0, 3.0), 'one machine: a lane on each side: %r' % (got,))
    # 6 m apart: 2.6 m are left between the machines -> the columns narrow to 2.6 m
    got = gun_gaps(al.table(0.0, 6.0), 3.0, 1.5, 1.8)
    expect(got[:4] == (0.0, 6.0, -1, 1) and abs(got[4] - 2.6) < 1e-6, 'narrowed columns: %r' % (got,))
    # run 28: warp lightning cannons 10 m apart (radius 3.5), clanrat columns of 7 m -> 2.6 m columns
    got = gun_gaps(al.table(0.0, 10.0, 20.0, 30.0), 7.0, 3.5, 1.8)
    expect(got[:4] == (0.0, 10.0, -1, 3) and abs(got[4] - 2.6) < 1e-6, 'run 28 cannons: %r' % (got,))
    got = gun_gaps(al.table(0.0, 4.5), 3.0, 1.5, 1.8)
    expect(got[0] is None and 'too close even for a single file of 1.8 m' in got[1], 'too close: %r' % (got,))
    # warp lightning cannons 10 m apart (radius 3.5), warp grinders of radius 1.3: one file of 3.1 m with margins does
    # not fit (2.6 m), the bare file of 2.6 m does: 3.0 m between the machines
    got = gun_gaps(al.table(0.0, 10.0, 20.0), 7.0, 3.5, 3.1, 2.6)
    expect(got[:4] == (0.0, 10.0, -1, 2) and abs(got[4] - 3.0) < 1e-6, 'one file squeezed: %r' % (got,))
    got = gun_gaps(al.table(0.0, 9.5, 19.0), 7.0, 3.5, 3.1, 2.6)
    expect(got[0] is None and 'too close even for a single file of 2.6 m' in got[1], 'not even one bare file: %r' % (got,))
    got = gun_gaps(al.table(0.0, 8.0, 30.0), 3.0, 1.5, 1.8)
    expect(got[0] is None and 'unevenly' in got[1], 'uneven: %r' % (got,))
    got = gun_gaps(al.table(), 3.0, 1.5, 1.8)
    expect(got[0] is None and 'cannot be read' in got[1], 'none: %r' % (got,))
    # crewmen (from their machine: across, along; radius 1.5 / 0.5 -> strip 1.0, ring 2.0): at the machine they stay,
    # beside it they step into its strip, and off the machine along the way
    for at, want in (((0.5, -1.0), (0.5, -1.0)), ((3.0, -1.0), (1.0, -3 ** 0.5)), ((-4.0, 5.0), (-1.0, 5.0)),
                     ((2.5, 0.0), (1.0, 3 ** 0.5)), ((-6.0, -0.5), (-1.0, -3 ** 0.5))):
        got = into_strip(at[0], at[1], 1.5, 0.5)
        expect(abs(got[0] - want[0]) < 1e-9 and abs(got[1] - want[1]) < 1e-9, 'crewman at %r goes to %r, not %r' % (at, want, got))
    # crewmen in a lane (centre 6, half 2) step out to their machine's side; the others stay
    for a, want in ((5.0, 4.0), (7.0, 8.0), (1.0, 1.0), (-5.0, -4.0), (-7.0, -8.0), (40.0, 40.0), (29.0, 28.0)):
        got = out_of_gap(a, 12.0, -1, 2, 2.0)
        expect(abs(got - want) < 1e-9, 'crewman at %r goes to %r, not %r' % (a, want, got))
    print('mock test: %d checks, ALL PASS' % checks)


if __name__ == '__main__':
    main_release() if RELEASE else main()
