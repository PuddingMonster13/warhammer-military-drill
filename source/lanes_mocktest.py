"""Offline test of the lanes battle script (Lua 5.1 through lupa, fake memreader Plus, fake armies).

    python lanes_mocktest.py <out_dir of 'lanes.py build'>                   -> must end with ALL PASS
    python lanes_mocktest.py <out_dir of 'lanes.py build --release'> --release
        the RELEASE script (military_drill.lua): it starts, makes way, writes no file of its own, and only its start
        and problems reach the game's mod log
    python lanes_mocktest.py <out_dir> --quick <group>[,<group>]   -> only those groups; ends with QUICK PASS (never ALL PASS)
    python lanes_mocktest.py --list                                 -> the group names
"""
import os
import re
import struct
import sys
import tempfile

import lupa.lua51 as lupa

import lanes

# the groups of main() for check_all.py --quick (cases inside one group share their battle / helpers; a group on its own is complete)
GROUPS = (
    ('core', 'one battle, run in order: wrong build / patterns, caves, lanes, columns, B/F/G cases, build49 / build55 cases, SHAPE CHECK, PERF lines, next battle, tick error'),
    ('fire', 'line of fire: one lane for a gun, Skaven mod mock, E1 - E5 (Skaven place searched once, errors, replan)'),
    ('fire_gaps', 'H: one gap per gun (battery lanes, replan, flicker)'),
    ('half_close', 'O: lane-by-lane closing (half close, quick close)'),
    ('guard_room', 'M: room beside a standing unit, guard mode (outer strip)'),
    ('big_park_ghost', 'L: big walkers (monstrous / cavalry columns), K: parked inside, N: ethereal units'),
    ('alliance', 'J: G2 log, I2: second army of an alliance, leadership patterns'),
    ('columns', '085c fix 1 (outer strip squeeze) and fix 2 (guard mode: one lane for columns)'),
    ('late', '085c P3: LATE walkers, units inside each other'),
    ('perf_log', '085c P4 / D3: reads per look, plans, test log, PERF phases, memo, send limit, lane band at contact'),
    ('bundle2', '085d bundle 2: line of fire (target debounce, gap update, grace, enemy near), quick stand, columns of the next walker, serve sub-phases, digest'),
    ('narrow', '085e: narrowed big walkers (A2 / B1: issue, read-back, same order, forget, restore, live run state, player only), ghost list (cave 5), bulk model read (D1), G2'),
    ('f085', '085f: closing at 90 % through, stragglers, sequential walkers (F1), extended goal of the narrowed column (F2), guard mode without room (F3), line of fire for infantry only (F4)'),
    ('h085', '085h: big walkers are never stuck: no wide restore into a one-lane gap (deferred, no give-up), lane from the width sent, files counted when steady, no re-send for big walkers, stuck lane grows, closing at 100 %, no closing up on a free side, whole-front lane for monstrous cavalry that is not narrowed, narrow-off build'),
    ('i085', '085i: the standing unit stays in its outline: cramped blocks laid out anew with extra ranks (table of places), 5 % share, no whole-front lane for mi / mc, big walkers close up too'),
    ('j085', '085j: math.huge is nil in the game, deferral cap 5 s, cavalry walkers of more than 20 bodies are narrowed to 4 files or take the columns'),
    ('pure', 'the pure Lua parts on their own: formation, merge, room, artillery gaps, crew strips'),
)
if '--list' in sys.argv:
    for name, text in GROUPS:
        print('%-15s %s' % (name, text))
    sys.exit(0)
QUICK = None  # None: the whole suite; else the set of group names asked for with --quick
if '--quick' in sys.argv[2:]:
    QUICK = set(sys.argv[sys.argv.index('--quick') + 1].split(','))
    assert QUICK <= set(name for name, _ in GROUPS), 'unknown group in --quick (try --list): %s' % ', '.join(sorted(QUICK - set(name for name, _ in GROUPS)))


def on(group):
    return QUICK is None or group in QUICK


RELEASE = '--release' in sys.argv[2:]
SCRIPT = open(os.path.join(os.path.abspath(sys.argv[1]), lanes.RELEASE_SCRIPT if RELEASE else lanes.SCRIPT), encoding='utf8').read()
# the real script (the part M tests use it); the older checks of the mechanism were written when a standing unit stepped out as far as
# its lanes needed (and the neighbours allowed): they run with no limit on that (TUNE.OUT_SHARE; 085i: 5 % of its width per flank); the default has its own checks
REAL_SCRIPT = SCRIPT
if not RELEASE:
    SCRIPT = SCRIPT.replace('OUT_SHARE = 0.05,', 'OUT_SHARE = 1000000,', 1)
    assert SCRIPT != REAL_SCRIPT
    # (085d: a unit that has just stopped stands after 1 s instead of 4 s when a friendly walker already comes for it; the older checks were
    # written for the 4 s: they run without the quick stand - its own checks use the real script)
    OLD = SCRIPT
    SCRIPT = SCRIPT.replace('STILL_FAST = 1.0,', 'STILL_FAST = 4.0,', 1)
    assert SCRIPT != OLD
MODLOG = []  # what the script hands the game's ModLog (lua_mod_log.txt)
LOG = 'units_lanes_log.txt'
TIMER = 'units_lanes_tick'
BASE = 0x140000000
UID_FIELD = 0x3ea0
UID_CODE = 0x142f169c3
checks = 0
FINDS = {}  # how often the script searched for each code pattern
LEAD_NORM = [lanes.unitkinds.lead_norm()[0]]


def expect(cond, what):
    global checks
    checks += 1
    if not cond and os.path.exists(LOG):
        print(''.join([l for l in open(LOG, encoding='utf8') if 'units with open lanes' not in l][-12:]))
    if not cond and os.environ.get('MOCK_SOFT'):  # (a builder's aid: list every failed expect instead of stopping at the first)
        print('SOFT FAIL #%d: %s' % (checks, ' | '.join(str(what)[:int(os.environ.get('SOFT_W', '700'))].splitlines())))
        return
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


def run_battle(mem, hooks, game_code, wrong_build=False, castes=None, mct=None, script=None, split=(), foes=()):  # split: units of a SECOND army of their alliance; foes: units that fight on the other side (alliance 2)
    lua = lupa.LuaRuntime(unpack_returned_tuples=True, encoding='latin-1')
    g = lua.globals()
    phases, timers = {}, {}
    units = {}
    by_alliance = {1: [], 2: []}
    by_second = {1: [], 2: []}
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
        -- run 58: math.huge is NIL in the game's Lua (085i FIRE.layout compared numbers with nil): any use must fail here too
        math.huge = nil
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
            function u:is_routing() return get('routing') == true end
            function u:position() return vec(get('x') + get('jx'), get('z')) end
            function u:ordered_position() return vec(get('gx'), get('gz')) end
            function u:ordered_width() return get('width') end
            function u:bearing() return get('bearing') end
            function u:ordered_bearing() return get('bearing') end
            function u:is_artillery() return get('artillery') end
            UNITS = UNITS or {}
            UNITS[get('uid')] = u
            function u:missile_range() return get('range') end
            function u:is_limbered_artillery() return get('limbered') end
            function u:current_target()
                local t = get('target')
                if t == 'error' then error('no target for you') end
                return t and UNITS[t] or nil
            end
            function u:is_war_machine() return get('war_machine') end
            function u:is_behaviour_active(name) return get('guard') == true end
            function u:is_currently_flying() return false end
            function u:unit_caste() return get('caste') end
            return u
        end
        function make_list(items) return { count = function() return #items end, item = function(_, i) return items[i] end } end
        function v(x, z) return { get_x = function() return x end, get_y = function() return 0 end, get_z = function() return z end } end
    ''')
    lead_pattern, lead_mark = lanes.sites.parse(lanes.sites.PATTERNS['is_routing'])
    lead_hits = game_code['exe'].find(lead_pattern, 3)
    lead_at = int.from_bytes(game_code['exe'].read(lead_hits[0] + lead_mark, 4), 'little') + 0x48 if lead_hits else None
    for n, (alliance, name, x, z, width, bearing, radius) in enumerate(ARMY):
        unit_at = mem.alloc(0x4000)
        if lead_at:  # every unit has the leadership that counts as x1 (a leadership of 0 counts as the lowest steadiness)
            mem.write(unit_at + lead_at, struct.pack('<I', LEAD_NORM[0]))
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
                 artillery=name == 'guns', war_machine=name in ('guns', 'cart'), jx=0.0, fast=False, notidle=False, fastv=6, target=None, range=0.0, limbered=False, guard=False, routing=False)
        units[name] = u
        (by_second if name in split else by_alliance)[2 if name in foes else alliance].append(g.make_unit(interface, (lambda key, u=u: u[key])))
    lists = []
    for a in (1, 2):
        make_army = lua.eval('function(units, player) return { units = function() return units end, is_player_controlled = function() return player end } end')
        armies = [make_army(g.make_list(lua.table(*by_alliance[a])), a == 1)] + ([make_army(g.make_list(lua.table(*by_second[a])), False)] if by_second[a] else [])
        lists.append(lua.eval('function(armies) return { armies = function() return armies end } end')(g.make_list(lua.table(*armies))))
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
    def find_pattern(pattern):
        FINDS[pattern] = FINDS.get(pattern, 0) + 1
        return game_code['patterns'][pattern] if pattern in game_code['patterns'] else (UID_CODE - lanes.probe.skaven_cave.UID_OFF, 1)
    mr['find_pattern'] = find_pattern
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
    lua.execute(script or SCRIPT)
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
    pattern = lanes.probe.skaven_cave.P_SITE  # the place of the Skaven mod's cave
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
    expect(len(hooks) == 5, 'the caves are installed')
    expect(not os.listdir(work), 'no file of its own: %r' % os.listdir(work))
    b['phases']['Complete']()  # (the end of the battle: its lines go through the same filter)
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
           and not any('PERF ' in l for l in lines) and not any('jobs for the models' in l for l in lines),
           'basic: the settings, one short line per opening: %r' % lines)
    # (085c, D2) no PERF BATTLE line and no 'unit of another mod' lines (only the 'LEFT OUT' ones keep a unit's name at level 1)
    expect(not any('unit of another mod' in l for l in lines) and not any('PERF BATTLE' in l for l in lines), 'basic: no PERF BATTLE, no unit-of-another-mod lines: %r' % lines)
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

    if on('core'):
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
        expect('MOD OFF' not in log() and log().count('cave installed') == 5 and len(hooks) == 5 and 'walker columns ON' in log(), log())
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

    if on('core'):
        e_walk_at = lambda: entry_of(mem, tbl, u['walk']['at'])['at'] + lanes.E_WLAG

    def walker(name):
        at = entry_of(mem, tbl, u[name]['at'])['at']
        q, ox, oz, ax, az, bx, bz, t0, t1, c, kl, kh, sl, sh = struct.unpack('<10f2i2f', mem.read(at + lanes.E_WQ, 0x38))
        return dict(q=q, ox=ox, oz=oz, ax=ax, az=az, bx=bx, bz=bz, t0=t0, t1=t1, c=c, kl=kl, kh=kh, sl=sl, sh=sh)
    if on('core'):
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
        # 085f: closes 8 ticks after the walker's only body is past the back rank + radii + margin (z = 1 + 0.65 + 0.65 + 1.5 + 0.65 = 4.45, 90 % through:
        # no longer 25 m past the middle) and 1.6 s quiet; the model walks back 6 m in 4 steps of 1.5 m, 0.5 s each, so the lanes are shut 3 steps later
        expect(closed_at is not None and 4.45 + 6 * 0.6 + 3 * 3 * 0.6 <= closed_at <= 4.45 + 10 * 0.6 + 4 * 4 * 0.6, 'closed with the walker at z = %r' % closed_at)
        expect('CLOSING A1.1.1 stand (nobody is passing any more, 100 % of A1.1.2 walk through): back in 4 steps of 1.5 m, one every 0.5 s | A1.1.2 walk: its rear' in log(), log()[-2500:])
        text = log()
        expect('OPEN  A1.1.1 stand makes way for A1.1.2 walk (100 models, radius 0.65): 3 blocks of 13.3 m, 2 lanes of 6.0 m, across (-1.00, 0.00)' in text, text)
        expect('columns: 2 columns every 19.3 m, files squeezed to 24 % of their strip (outer strip 19.3 m), from 11.9 m before to 1.0 m behind the standing unit, -15.8 .. -12.2 m across (beside it: free)' in text, text)
        expect('1 models sent at once (facing word 49275' in text and '1 models sent back at once' in text, text)
        expect('CLOSE A1.1.1 stand (nobody is passing any more, 100 % of A1.1.2 walk through)' in text and text.count('OPEN ') == 1, text)
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
    if on('core'):
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

        # --- fix B1: a walker that walks AWAY from the standing unit (its middle past the unit's middle) is not passing it ---
        mx = struct.unpack('<f', mem.read(u['stand']['model'] + 0x88, 4))[0]  # the standing model's x: the walkers run along it

    def run_away(z0, gz, step):  # running (5 m/s) from just in front of / behind the standing unit, away from it
        mark = len(log())
        w.update(dict(x=mx, z=z0, gx=mx, gz=gz, moving=True, melee=False, men=100))
        for _ in range(30):
            w['z'] += step
            tick()
            expect(lane('stand') == 0, 'B1: lanes opened for a walker walking away (z %.1f): %s' % (w['z'], log()[mark:][-1500:]))
        expect('OPEN  A1.1.1 stand' not in log()[mark:], 'B1: no new opening for a walker walking away: ' + log()[mark:][-1500:])
        stop_walker()
    if on('core'):
        run_away(-6.0, -80.0, -1.0)  # (the standing unit lies behind it, in front of the unit's middle)
        run_away(6.0, 80.0, 1.0)
        # (A4) one that walks away but whose bodies still overlap the standing unit's models (its middle is past the unit's
        # middle, the unit has no lanes) is not 'leaving': it may still get lanes, as before the fix
        w.update(dict(x=mx, z=-0.5, gx=mx, gz=-80.0, moving=True, melee=False, men=100))
        overlap_open = False
        for _ in range(3):
            w['z'] -= 1.0
            tick()
            overlap_open = overlap_open or lane('stand') > 0
        # (085c P3: its front is already 2 - 4 m inside - more than LATE_INSIDE - when the lanes would open: left to the game, said once; before 085c
        # it got lanes. The 'leaving' rule itself (an overlapping walker is not 'leaving') is unchanged: it is a candidate, and LATE answers it)
        expect(not overlap_open and log().count('LATE A1.1.2 walk already ') >= 1, 'A4 (085c): a walker leaving with its bodies already inside the standing unit is LATE: ' + log()[-1500:])
        stop_walker()

        # a walker that turns back after the lanes opened for it: they close within HOLD_TICKS + 4 ticks (not when it is 25 m away)
        expect(walk_in(1.0, x=mx), 'B1: lanes for the walker coming in: ' + log()[-1500:])
        mark, closing_after = len(log()), None
        w.update(dict(gz=-80.0))
        for n in range(1, 60):
            w['z'] -= 1.0
            tick()
            if closing_after is None and 'CLOSING A1.1.1 stand (nobody is passing any more' in log()[mark:]:
                closing_after = n
        expect(closing_after is not None and closing_after <= 8 + 4, 'B1: closing %r ticks after the walker turned back: %s' % (closing_after, log()[mark:][-1500:]))
        stop_walker()
        w.update(dict(x=0.0, z=-80.0, gx=0.0, gz=-80.0, moving=False))
        for _ in range(21):
            tick()

        # --- fix B2: two standing units one behind the other (the second 20 m behind the first): the walker's columns pass on ---
        aside = u['aside']
        aside.update(dict(x=0.0, z=20.0, gx=0.0, gz=20.0))
        mem.write(aside['model'] + 0x88, struct.pack('<3f', mx, 0.0, 21.0))  # its one model, 20 m behind the standing one's
        for _ in range(25):  # (a teleported unit stands 4 s before it makes way)
            tick()
        mark = len(log())
        w.update(dict(x=mx, z=-30.0, gx=mx, gz=80.0, moving=True, melee=False, men=100))
        frame_aside = None
        for _ in range(120):
            w['z'] += 1.0
            tick()
            if frame_aside is None and lane('aside') > 0 and w['z'] >= 6.0:
                frame_aside = (walker('walk'), entry_of(mem, tbl, aside['at']))
            if w['z'] >= 80.0:
                break
        text = log()[mark:]
        expect('OPEN  A1.1.1 stand makes way for A1.1.2 walk' in text and 'OPEN  A1.1.3 aside makes way for A1.1.2 walk' in text, 'B2: both lines open: ' + text[-2500:])
        expect(frame_aside is not None, 'B2: the second line opened by the time the walker is past the first: ' + text[-2500:])
        c, ea = frame_aside
        t_model = (mx - c['ox']) * c['bx'] + (21.0 - c['oz']) * c['bz']
        expect(c['q'] > 0 and abs(c['ox'] - ea['ox']) < 1e-3 and abs(c['oz'] - ea['oz']) < 1e-3 and abs(c['t1'] - (t_model + 1.0)) < 0.05,
               'B2: the walker forms its columns for the second line (t1 %.2f, its model at %.2f): %r' % (c['t1'], t_model, c))
        expect('hands the walker\'s columns over to A1.1.3 aside' in text or 'COLUMNS LATE A1.1.3 aside for A1.1.2 walk' in text, 'B2: hand-over or late columns logged: ' + text[-2500:])
        stop_walker()
        for _ in range(21):
            tick()
        expect(lane('aside') == 0 and lane('stand') == 0, 'B2: both lines closed after the walker passed')
        aside.update(dict(x=200.0, z=0.0, gx=200.0, gz=0.0))
        mem.write(aside['model'] + 0x88, struct.pack('<3f', 200.0, 0.0, 0.0))
        for _ in range(25):
            tick()

        # --- fix B2, the retry: the second line is only 8 m behind the first, so it opens while the walker's (one) body is
        # still in the first line's column zone: the columns are refused, and handed over at a later look (COLUMNS LATE)
        aside.update(dict(x=0.0, z=8.0, gx=0.0, gz=8.0))
        mem.write(aside['model'] + 0x88, struct.pack('<3f', mx, 0.0, 9.0))
        for _ in range(25):
            tick()
        mark = len(log())
        w.update(dict(x=mx, z=-30.0, gx=mx, gz=80.0, moving=True, melee=False, men=100))
        frame_aside, late_at = None, None
        for _ in range(120):
            w['z'] += 1.0
            tick()
            if late_at is None and 'COLUMNS LATE A1.1.3 aside for A1.1.2 walk' in log()[mark:]:
                late_at = w['z']
                frame_aside = (walker('walk'), entry_of(mem, tbl, aside['at']))
            if w['z'] >= 80.0:
                break
        text = log()[mark:]
        expect('the walker forms them for another unit' in text and 'OPEN  A1.1.3 aside makes way for A1.1.2 walk' in text, 'B2 retry: refused at the opening: ' + text[-2500:])
        expect(late_at is not None and text.count('COLUMNS LATE A1.1.3 aside for A1.1.2 walk') == 1 and 'failed' not in text, 'B2 retry: one COLUMNS LATE line: ' + text[-2500:])
        c, ea = frame_aside
        t_model = (mx - c['ox']) * c['bx'] + (9.0 - c['oz']) * c['bz']
        expect(c['q'] > 0 and abs(c['ox'] - ea['ox']) < 1e-3 and abs(c['oz'] - ea['oz']) < 1e-3 and abs(c['t1'] - (t_model + 1.0)) < 0.05,
               'B2 retry: the columns of the walker belong to the second line (t1 %.2f, its model at %.2f): %r' % (c['t1'], t_model, c))
        expect("hands the walker's columns over to A1.1.3 aside" in text, 'B2 retry: handed over: ' + text[-2500:])
        stop_walker()
        for _ in range(21):
            tick()
        expect(log()[mark:].count('COLUMNS LATE') == 1 and lane('aside') == 0 and lane('stand') == 0, 'B2 retry: the wait is dropped, both lines closed')
        aside.update(dict(x=200.0, z=0.0, gx=200.0, gz=0.0))
        mem.write(aside['model'] + 0x88, struct.pack('<3f', 200.0, 0.0, 0.0))
        for _ in range(25):
            tick()

        # (A1) the same, but the walker stops walking for ONE look while the second line waits for the columns: the wait goes
        # on (it was dropped for good by one look where the walker did not pass), and the columns come later
        aside.update(dict(x=0.0, z=8.0, gx=0.0, gz=8.0))
        mem.write(aside['model'] + 0x88, struct.pack('<3f', mx, 0.0, 9.0))
        for _ in range(25):
            tick()
        mark = len(log())
        w.update(dict(x=mx, z=-30.0, gx=mx, gz=80.0, moving=True, melee=False, men=100))
        paused, late_at, frame_aside = False, None, None
        for _ in range(120):
            if not paused and 'OPEN  A1.1.3 aside makes way for A1.1.2 walk' in log()[mark:]:
                paused, w['moving'] = True, False  # one look without walking
            else:
                w['moving'] = True
                w['z'] += 1.0
            tick()
            if late_at is None and 'COLUMNS LATE A1.1.3 aside for A1.1.2 walk' in log()[mark:]:
                late_at = w['z']
                frame_aside = (walker('walk'), entry_of(mem, tbl, aside['at']))
            if w['z'] >= 80.0:
                break
        text = log()[mark:]
        expect(paused and late_at is not None and text.count('COLUMNS LATE A1.1.3 aside for A1.1.2 walk') == 1, 'A1: the wait survives a look without walking: ' + text[-2500:])
        c, ea = frame_aside
        expect(c['q'] > 0 and abs(c['ox'] - ea['ox']) < 1e-3 and abs(c['oz'] - ea['oz']) < 1e-3, 'A1: the walker forms its columns for the second line: %r' % c)
        stop_walker()
        for _ in range(21):
            tick()
        aside.update(dict(x=200.0, z=0.0, gx=200.0, gz=0.0))
        mem.write(aside['model'] + 0x88, struct.pack('<3f', 200.0, 0.0, 0.0))
        for _ in range(25):
            tick()

        # (A3) the FARTHER line opens first (the nearer one was teleported: it makes way only 4 s after the jump): the nearer
        # one takes the columns over while the walker has not reached the farther line's zone yet. The window is under one
        # tick wide (a line opens when the walker is at the start of its zone), so the jump is timed from a dry run
    def two_lines_run(jump_at):  # the walker runs through stand (z 1) and aside (z 9); stand's model jumps at tick jump_at
        aside.update(dict(x=0.0, z=8.0, gx=0.0, gz=8.0))
        mem.write(aside['model'] + 0x88, struct.pack('<3f', mx, 0.0, 9.0))
        if jump_at is not None:
            mem.write(u['stand']['model'] + 0x88, struct.pack('<3f', mx, 2.0, -9.0))
        for _ in range(25):
            tick()
        mark = len(log())
        w.update(dict(x=mx, z=-30.0, gx=mx, gz=80.0, moving=True, melee=False, men=100))
        opened, took_at = None, None
        for n in range(120):
            if n == jump_at:
                mem.write(u['stand']['model'] + 0x88, struct.pack('<3f', mx, 2.0, 1.0))
            # (the walker creeps while only the farther line is open: the nearer one has to open before the walker reaches the
            # start of the farther line's zone)
            w['z'] += 0.1 if (opened is not None and lane('stand') == 0 and jump_at is not None) else 1.0
            tick()
            if took_at is None and "A1.1.3 aside hands the walker's columns over to A1.1.1 stand" in log()[mark:]:
                took_at = w['z']
            if opened is None and 'OPEN  A1.1.3 aside' in log()[mark:]:
                opened = n
            if w['z'] >= 80.0:
                break
        text = log()[mark:]
        stop_walker()
        for _ in range(21):
            tick()
        mem.write(u['stand']['model'] + 0x88, struct.pack('<3f', mx, 2.0, 1.0))
        aside.update(dict(x=200.0, z=0.0, gx=200.0, gz=0.0))
        mem.write(aside['model'] + 0x88, struct.pack('<3f', 200.0, 0.0, 0.0))
        for _ in range(25):
            tick()
        return opened, text, took_at
    if on('core'):
        k_a, _, _ = two_lines_run(None)
        expect(k_a is not None, 'A3: dry run: the second line opens')
        took = False
        for jump in (k_a - 19, k_a - 20, k_a - 18, k_a - 21, k_a - 17) + tuple(k_a - j for j in range(8, 40)):  # (085f: the window moved with the 90 % close; searched wider)
            opened, text, took_at = two_lines_run(jump)
            if took_at is not None and took_at < 0:  # (a hand-over made late, with the walker already past both lines, does not count)
                took = True
                break
        expect(took, 'A3: the nearer line takes the columns from the farther one that opened first: ' + text[-2500:])
        # --- leadership: the standing unit's steadiness = leadership / the median of the tables (x0.5 .. x1.5): a steadier unit
        # waits less (shorter random waits) and opens LATER (its split is shorter); the leadership int is at Unit* + B + 0x48
        norm = lanes.unitkinds.lead_norm()[0]
        pattern, mark = lanes.sites.parse(lanes.sites.PATTERNS['is_routing'])
        lead_at = int.from_bytes(exe.read(exe.find(pattern, 3)[0] + mark, 4), 'little') + 0x48

    def with_leadership(n):
        mem.write(u['stand']['at'] + lead_at, struct.pack('<I', n))
        w.update(dict(x=mx, z=-30.0, gx=mx, gz=-30.0, moving=False))  # (085f: the walker is put back and settles first; a jump while it runs leaves it not standing for a while)
        for _ in range(30):
            tick()
        mark = len(log())
        opened = None
        w.update(dict(x=mx, z=-30.0, gx=mx, gz=80.0, moving=True, melee=False, men=100))
        for _ in range(60):
            w['z'] += 1.0
            tick()
            if lane('stand') > 0:
                opened = w['z']
                break
        wait = entry_of(mem, tbl, u['stand']['at'])['wait']
        for _ in range(60):  # it closes
            w['z'] += 1.0 if w['z'] < 80.0 else 0.0
            tick()
        stop_walker()
        mem.write(u['stand']['at'] + lead_at, struct.pack('<I', norm))
        return opened, wait, log()[mark:]
    if on('core'):
        low, low_wait, low_text = with_leadership(norm // 2 - 10)  # under x0.5: the floor
        mid, mid_wait, mid_text = with_leadership(norm)
        high, high_wait, high_text = with_leadership(norm * 2)  # over x1.5: the cap
        expect(low_wait == 8 and mid_wait == 4 and high_wait == 3, 'leadership: waits %r %r %r' % (low_wait, mid_wait, high_wait))
        expect(low is not None and mid is not None and high is not None and low < mid <= high and low < high, 'leadership: the steadier unit opens later: z %r %r %r' % (low, mid, high))
        expect('leadership %d (x0.50)' % (norm // 2 - 10) in low_text and 'leadership %d (x1.00)' % norm in mid_text and 'leadership %d (x1.50)' % (norm * 2) in high_text,
               'leadership in the OPEN lines: ' + low_text[-600:])
        expect(all('CLOSING A1.1.1 stand' in t and 'leadership' in t.split('CLOSING A1.1.1 stand')[1] for t in (low_text, mid_text, high_text)), 'leadership in the CLOSING lines')
        expect('leadership %d (x1.00)' % norm in log(), 'the default leadership counts as x1 (every other test)')
        # a leadership of 0 or less (read as a signed number) counts as the lowest steadiness; over 1000 is no leadership at all (x1)
        zero, zero_wait, zero_text = with_leadership(0)
        minus, minus_wait, minus_text = with_leadership(2 ** 32 - 5)
        junk, junk_wait, junk_text = with_leadership(5000)
        expect(zero_wait == 8 and minus_wait == 8 and 'leadership 0 (x0.50)' in zero_text and 'leadership -5 (x0.50)' in minus_text, 'leadership 0 / -5: x0.5: %r %r' % (zero_wait, minus_wait))
        expect(junk_wait == 4 and 'leadership unknown (x1.00)' in junk_text, 'leadership 5000 is garbage: unknown, x1')
        stop_walker()
        for _ in range(21):
            tick()

        # --- F: quick close behind ranged walkers
    def quick_run(walkers, ticks=70, start=-30.0, step=1.0):
        """walkers: [(name, offset behind the first, ranged)]; all run along +z at 1.0 per tick. Returns the log text and the
        z of the first walker when CLOSING (or None) and the ticks the lane was open"""
        for name, back, ranged in walkers:
            u[name].update(dict(x=mx, z=start - back, gx=mx, gz=80.0, moving=True, melee=False, men=100, range=100.0 if ranged else 0.0))
        mark = len(log())
        closing_z = None
        for _ in range(ticks):
            for name, back, ranged in walkers:
                u[name]['z'] += step
            tick()
            if closing_z is None and 'CLOSING A1.1.1 stand' in log()[mark:]:
                closing_z = [u[name]['z'] for name, back, ranged in walkers]
        text = log()[mark:]
        for name, back, ranged in walkers:
            u[name].update(dict(range=0.0))
        return text, closing_z

    def settle():
        for name in ('walk', 'walk2', 'walk3'):
            u[name].update(dict(moving=False, x=-300.0 if name == 'walk2' else (-500.0 if name == 'walk3' else mx), z=-80.0, gx=-300.0 if name == 'walk2' else mx, gz=-80.0))
        for _ in range(60):
            tick()
    if on('core'):
        settle()
        # (a) one ranged walker: the lanes close right after its rear clears the unit, in one step; a melee walker: as before
        text, z_quick = quick_run([('walk', 0.0, True)])
        expect(z_quick is not None and 4.0 <= z_quick[0] <= 6.0 and 'CLOSING A1.1.1 stand (quick: the ranged walker A1.1.2 walk is through): back in 1 steps' in text, 'F(a): ranged: quick close: %r %s' % (z_quick, text[-1200:]))
        settle()
        text, z_slow = quick_run([('walk', 0.0, False)])
        expect(z_slow is not None and z_slow[0] >= 10.0 and 'quick' not in text, 'F(b): melee: the normal close, 085f: when it is 90 %% through (not 25 m past): %r' % (z_slow,))
        settle()
        # (c) two ranged walkers 9 m apart: open for the second, closed right after it
        text, z_two = quick_run([('walk', 0.0, True), ('walk2', 9.0, True)])
        expect(z_two is not None and 2.0 < z_two[1] <= 6.0 and text.count('OPEN  A1.1.1') == 1 and text.count('CLOSING A1.1.1 stand (quick') == 1, 'F(c): two ranged walkers: %r %s' % (z_two, text[-1500:]))
        settle()
        # (d) the second one 60 m behind: closed behind the first, opened again for the second in time
        text, z_far = quick_run([('walk', 0.0, True), ('walk2', 60.0, True)], ticks=110)
        expect(z_far is not None and z_far[0] <= 6.0 and text.count('OPEN  A1.1.1') == 2 and text.count('CLOSING A1.1.1 stand (quick') == 2, 'F(d): a follower 60 m behind: %r %s' % (z_far, text[-1800:]))
        settle()
        # (f) a ranged walker followed closely by a melee one: the melee one keeps the lanes open, then the old close
        text, z_mix = quick_run([('walk', 0.0, True), ('walk2', 9.0, False)])
        expect(z_mix is not None and z_mix[1] >= 10.0 and 'quick' not in text, 'F(f): a melee follower: no quick close: %r' % (z_mix,))
        settle()
        # a WALKING ranged follower 10 m behind (0.6 m a tick): the lanes stay open for it, then the quick close
        text, z_walk = quick_run([('walk', 0.0, True), ('walk2', 10.0, True)], ticks=130, step=0.6)
        expect(z_walk is not None and z_walk[1] >= 3.8 and text.count('OPEN  A1.1.1') == 1 and text.count('CLOSING A1.1.1 stand (quick') == 1, 'F: a walking ranged follower: %r %s' % (z_walk, text[-1500:]))
        settle()
        # (e) artillery that walks (a gun pulled by its crew): quick close
        u['guns'].update(dict(x=mx, z=-30.0, gx=mx, gz=80.0, moving=True, range=150.0))
        mark = len(log())
        gun_z = None
        for _ in range(70):
            u['guns']['z'] += 1.0
            mem.write(u['guns']['model'] + 0x88, struct.pack('<3f', mx, 0.0, u['guns']['z']))
            tick()
            if gun_z is None and 'CLOSING A1.1.1 stand' in log()[mark:]:
                gun_z = u['guns']['z']
        text = log()[mark:]
        expect(gun_z is not None and gun_z <= 8.0 and 'quick: the ranged walker A1.1.5 guns is through' in text, 'F(e): artillery walker: quick close: %r %s' % (gun_z, text[-1500:]))
        u['guns']['moving'] = False
        for _ in range(12):
            tick()
        expect(entry_of(mem, tbl, u['guns']['at'])['dirty'] == 0, 'a walking gun stopped: its places are not handed out anew (E_DIRTY stays 0)')
        u['guns'].update(dict(x=0.0, z=400.0, gx=0.0, gz=400.0, moving=False, range=0.0))
        mem.write(u['guns']['model'] + 0x88, struct.pack('<3f', 0.0, 0.0, 400.0))
        settle()
        for _ in range(25):
            tick()

        # --- G2: a walker whose order ends before the real front of the standing unit opens no lanes and forms no columns
    def short_run(gz):
        w.update(dict(x=mx, z=-30.0, gx=mx, gz=gz, moving=True, melee=False, men=100))
        opened, columns = False, False
        for _ in range(70):
            if w['z'] < gz:
                w['z'] = min(gz, w['z'] + 1.0)
            w['moving'] = w['z'] < gz
            tick()
            opened = opened or lane('stand') > 0
            columns = columns or walker('walk')['q'] > 0
        stop_walker()
        for _ in range(21):
            tick()
        return opened, columns
    # (the standing model is at z = 1, radius 0.65: its front is at z = 0.35; the walker's front body reaches 0.65 + 1 m further)
    if on('core'):
        expect(short_run(-4.65) == (False, False), 'G2: an order that ends 5 m before the front: no lanes, no columns')
        expect(short_run(6.0) == (True, True), 'G2: an order 5 m into the unit: lanes and columns as ever')

        # (a charging unit: its ordered position stays where it is; it walks on its movement and gets lanes as ever)
        w.update(dict(x=mx, z=-30.0, gx=mx, gz=-30.0, moving=True, melee=False, men=100))
        charged = False
        for _ in range(40):
            w['z'] += 1.0
            tick()
            charged = charged or lane('stand') > 0
        expect(charged, 'G2: an order at itself (a charge) is no arriving walker: lanes: ' + log()[-800:])
        stop_walker()
        for _ in range(21):
            tick()

        # --- G3: the columns go off and the walker stands still within 10 s: its places are handed out at once (E_DIRTY)
    def columns_off_run(keep_moving):
        walker_at = entry_of(mem, tbl, u['walk']['at'])['at']
        mem.write(walker_at + lanes.E_DIRTY, struct.pack('<I', 0))
        w.update(dict(x=mx, z=-30.0, gx=mx, gz=300.0 + (5.0 if keep_moving else 0.0), moving=True, melee=False, men=100))  # (085f: a new order each time: a walker that went through on its order is not planned for again)
        was_on, off_at = False, None
        for n in range(200):
            w['z'] += 1.0
            tick()
            q = walker('walk')['q']
            was_on = was_on or q > 0
            if was_on and q == 0 and off_at is None:
                off_at = n
                break
        stopped = False
        for n in range(80):
            if not keep_moving and n == 5 or keep_moving and n == 70:
                w['moving'] = False
                stopped = True
            elif not stopped:
                w['z'] += 1.0
            tick()
        dirty = entry_of(mem, tbl, u['walk']['at'])['dirty']
        stop_walker()
        for _ in range(21):
            tick()
        return was_on and off_at is not None, dirty
    if on('core'):
        expect(columns_off_run(False) == (True, 1), 'G3: the walker stands still after its columns went off: E_DIRTY')
        expect(columns_off_run(True) == (True, 0), 'G3: it keeps walking for more than 10 s: no E_DIRTY')

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
        mem.write(wheel + 0x88, struct.pack('<3f', 0.0, 0.0, -3.0))  # (085c: the wheel stands at its front touching the standing model, not 2.65 m inside it: that would be LATE)
        riders = [mem.alloc(0xc00) for _ in range(3)]
        for r in riders:
            mem.write(r + 0x2e0, struct.pack('<Q', wheel))
            mem.write(r + 0xa0, struct.pack('<f', 0.5))
            mem.write(r + 0x88, struct.pack('<3f', 0.0, 0.0, -3.0))
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

    if on('core'):
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

    if on('core'):
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

    if on('core'):
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
        mem.write(entry_of(mem, tbl, g['at'])['at'] + lanes.E_DIRTY, struct.pack('<I', 0))  # (the cave clears it in the game; the gun walked in the quick close test)
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
        w.update(dict(x=6.0, z=360.0, gx=6.0, gz=481.0, moving=True, melee=False, men=100))
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
    if on('core'):
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
        # --- G1: the SHAPE CHECK. A stander of 5 files x 4 ranks: 10 s after its lanes closed its shape is compared with the one it had
        u['stand'].update(dict(x=0.0, z=0.0, gx=0.0, gz=0.0, moving=False, bearing=0.0, width=40.0))
        mx = 14.0
        old_list = set_list('stand', [new_model(mx + 2.0 * i, 1.0 + 2.0 * j, 0.65) for i in range(-2, 3) for j in range(4)])
        grid = [(mx + 2.0 * i, 1.0 + 2.0 * j) for i in range(-2, 3) for j in range(4)]
        stand_models = struct.unpack('<20Q', mem.read(struct.unpack('<Q', mem.read(u['stand']['at'] + 0xa8, 8))[0], 160))
        # (085c: the walker of the test before stood inside the new grid when the unit began to stand again: LATE, no lanes; it waits far away first)
        w.update(dict(x=-300.0, z=-80.0, gx=-300.0, gz=-80.0, moving=False))
        for _ in range(25):
            tick()

    shape_runs = [0]

    def shape_run(line_up, recover, ticks=60):
        mark = len(log())
        shape_runs[0] += 1  # (085f: a new order each time: a walker that went through on its order is not planned for again)
        w.update(dict(x=mx, z=-30.0, gx=mx, gz=80.0 + shape_runs[0], moving=True, melee=False, men=100))
        closed = False
        for _ in range(200):
            w['z'] += 1.0 if w['z'] < 80.0 else 0.0
            w['moving'] = w['z'] < 80.0
            tick()
            if lane('stand') == 0 and 'CLOSE A1.1.1 stand' in log()[mark:]:
                closed = True
                break
        expect(closed, 'G1: the lanes closed: ' + log()[mark:][-800:])
        if line_up:  # the models of the unit stay in one long line (bad places)
            for k, m in enumerate(stand_models):
                mem.write(m + 0x88, struct.pack('<3f', mx, 0.0, 1.0 + 2.0 * k))
        for n in range(ticks):
            if recover and n == 55:  # ... and are back in their shape before the second look
                for m, (x, z) in zip(stand_models, grid):
                    mem.write(m + 0x88, struct.pack('<3f', x, 0.0, z))
            tick()
        for m, (x, z) in zip(stand_models, grid):
            mem.write(m + 0x88, struct.pack('<3f', x, 0.0, z))
        text = log()[mark:]
        stop_walker()
        for _ in range(21):
            tick()
        return text
    if on('core'):
        text = shape_run(False, False)
        expect('LONG LINES?' not in text, 'G1: a unit in its shape is not flagged: ' + text[-800:])
        text = shape_run(True, False, 115)
        expect('LONG LINES? A1.1.1 stand (stander of A1.1.2 walk, nobody is passing any more, 100 % of A1.1.2 walk through): width 0.0 of 9.0 m, depth 38.0 (expected 6.0), fields L ' in text
               and ' pending ' in text and 'places handed out by the game in the 10 s: 0, models far from their game place: 20 of 20' in text, 'G1: flagged: ' + text[-1500:])
        expect('LONG LINES? still: A1.1.1 stand' in text and 'recovered' not in text, 'G1: flagged again 10 s later: still')
    def shape_order():  # the unit is given a new order after its lanes closed: the check is dropped
        mark = len(log())
        w.update(dict(x=mx, z=-30.0, gx=mx, gz=80.0, moving=True, melee=False, men=100))
        for _ in range(200):
            w['z'] += 1.0 if w['z'] < 80.0 else 0.0
            w['moving'] = w['z'] < 80.0
            tick()
            if lane('stand') == 0 and 'CLOSE A1.1.1 stand' in log()[mark:]:
                break
        for k, m in enumerate(stand_models):
            mem.write(m + 0x88, struct.pack('<3f', mx, 0.0, 1.0 + 2.0 * k))
        u['stand']['gx'] = 3.0
        for _ in range(60):
            tick()
        text = log()[mark:]
        u['stand']['gx'] = 0.0
        for m, (x, z) in zip(stand_models, grid):
            mem.write(m + 0x88, struct.pack('<3f', x, 0.0, z))
        stop_walker()
        for _ in range(21):
            tick()
        return text
    if on('core'):
        expect('LONG LINES?' not in shape_order(), 'G1: a new order after the close: no shape check')
        text = shape_run(True, True, 115)
        expect('LONG LINES? A1.1.1 stand' in text and 'LONG LINES? recovered: A1.1.1 stand' in text, 'G1: recovered: ' + text[-1500:])
        restore_list('stand', old_list)
        for _ in range(25):
            tick()

        b['phases']['Complete']()
        expect(enabled() == 0 and lane('stand') == 0 and 'battle complete, mod off' in log() and TIMER not in b['timers'], 'everything closed at the end')
        expect(any(l[9:].startswith('SHAPE CHECK: ') and l.endswith(' checked, 2 flagged, 1 recovered') for l in log().splitlines()), 'G1: the summary line: ' + log()[-1500:])
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
        count = 0
        for line in SCRIPT.split('\n'):
            if re.match(r'local function \w+', line):
                count += 1
            else:
                m = re.match(r'local ([\w, ]+?)(=|$)', line)
                if m:
                    count += len([x for x in m.group(1).split(',') if x.strip()])
        expect(count <= 192, 'the main chunk has %d locals (at most 200 load at all): make room' % count)
    # --- the line of fire: a direct-fire gun with a target behind a friendly standing unit makes it open ONE lane along the line
    FIRE_SCRIPT = SCRIPT.replace('local FIRE = { guns = { ', "local FIRE = { guns = { ['guns'] = true, ", 1)  # (the mock's gun is a direct-fire gun)
    if on('fire'):
        expect(FIRE_SCRIPT != SCRIPT, 'the mock gun is listed')
    skv_pattern = lanes.probe.skaven_cave.P_SITE
    skv_site = BASE + exe.find(skv_pattern, 3)[0] + lanes.probe.skaven_cave.SITE_OFF

    class Fire:  # a fresh battle: our gun at (-30, -60) shoots at the enemy at (30, 60) through 'stand' at (0, 0); 'walk' waits at z = -80
        def __init__(self, script=FIRE_SCRIPT, mct=None, skaven=None, deploy=True, split=(), foes=(), hooks0=None):
            self.mem = Memory()
            self.mem.regions[UID_CODE & ~0xFFF] = bytearray(0x1000)
            self.mem.write(UID_CODE, struct.pack('<I', UID_FIELD))
            self.hooks = dict(hooks0 or {})
            self.b = run_battle(self.mem, self.hooks, game_code, script=script, mct=mct, split=split, foes=foes)
            self.u = self.b['units']
            self.tbl = struct.unpack('<Q', self.mem.read(self.hooks[BASE + lanes.SITES[0][1]] + 2, 8))[0]
            self.u['guns'].update(dict(x=-30.0, z=-60.0, gx=-30.0, gz=-60.0, ammo=10, range=150.0))
            self.mem.write(self.u['guns']['model'] + 0x88, struct.pack('<3f', -30.0, 0.0, -60.0))
            self.u['enemy'].update(dict(x=30.0, z=60.0, gx=30.0, gz=60.0))
            self.mem.write(self.u['stand']['model'] + 0x88, struct.pack('<3f', 1.0, 0.0, 0.0))  # (not exactly on the line: it has to step aside)
            at = entry_of(self.mem, self.tbl, self.u['stand']['at'])['at']  # what cave 1 keeps when the game hands out places in the unit
            self.mem.write(at + lanes.E_TPL, struct.pack('<3fI QQ HH I I I', 1.0, 2.0, 3.0, 0, 0, 0, 0xE3A5, 0xC07B, 7, 0, 0))
            self.mem.write(at + lanes.E_SEQ, struct.pack('<I', 1))
            if skaven is not None:
                self.skaven(**skaven)
            self.tick()
            if deploy:
                self.b['phases']['Deployed']()

        def tick(self):
            for name in ('walk', 'walk2', 'walk3'):
                self.mem.write(self.u[name]['model'] + 0x88, struct.pack('<3f', self.u[name]['x'], 0.0, self.u[name]['z']))
            for one in self.u.values():
                now = struct.unpack('<I', self.mem.read(one['clock'] + 0x98, 4))[0]
                self.mem.write(one['clock'] + 0x98, struct.pack('<I', now + 2))
            self.b['timers'][TIMER]()

        def run(self, n=1):
            for _ in range(n):
                self.tick()

        def model(self, x, z, radius=0.5, carrier=0, owner=0):
            m = self.mem.alloc(0xc00)
            self.mem.write(m + 0xa0, struct.pack('<f', radius))
            self.mem.write(m + 0x88, struct.pack('<3f', x, 0.0, z))
            self.mem.write(m + 0x58, self.mem.read(self.u['stand']['model'] + 0x58, 8))
            if carrier:
                self.mem.write(m + 0x2e0, struct.pack('<Q', carrier))
            if owner:
                self.mem.write(m + 0x2e8, struct.pack('<Q', owner))
            return m

        def set_models(self, name, models):
            lst = self.mem.alloc(8 * len(models))
            self.mem.write(lst, b''.join(struct.pack('<Q', m) for m in models))
            self.mem.write(self.u[name]['at'] + 0xa4, struct.pack('<I', len(models)))
            self.mem.write(self.u[name]['at'] + 0xa8, struct.pack('<Q', lst))

        def battery(self, offs, bare=(), loose=()):  # the gun unit: a crewed machine at each offset across the line (a = (-0.894, 0.447) from the unit's middle), 2 crewmen each; bare: machines without crew; loose: crewmen without a machine
            g, gat, models = self.u['guns'], self.u['guns']['at'], []
            for off in offs:
                machine = self.model(-30.0 - 0.8944 * off, -60.0 + 0.4472 * off, 1.5, owner=gat)
                models += [self.model(-30.0 - 0.8944 * off + 0.5, -60.0 + 0.4472 * off, 0.5, carrier=machine), self.model(-30.0 - 0.8944 * off - 0.5, -60.0 + 0.4472 * off, 0.5, carrier=machine)]
            for off in bare:
                models.append(self.model(-30.0 - 0.8944 * off, -60.0 + 0.4472 * off, 1.5, owner=gat))
            for off in loose:
                models.append(self.model(-30.0 - 0.8944 * off, -60.0 + 0.4472 * off, 0.5))
            self.set_models('guns', models)

        def row(self, x0, x1, step=1.3, ranks=2, name='stand'):  # the standing unit: ranks of models along x from x0 to x1
            models, x = [], x0
            while x <= x1 + 1e-6:
                for r in range(ranks):
                    models.append(self.model(x, -1.3 * r, 0.65))
                x += step
            self.set_models(name, models)

        def centres(self, e):  # across (a = (-0.894, 0.447)) of the lanes of the open plan
            q, a_o = e['p'] * (1 + self.extra(e)[0]) + e['lane'], e['ox'] * e['ax'] + e['oz'] * e['az']  # (closing up: G < 0 shrinks the blocks)
            return [round(a_o + (k + 0.5) * q, 1) for k in range(-e['n'], e['n'])]

        def extra(self, e, name='stand'):  # E_G, E_S0
            return struct.unpack('<2f', self.mem.read(e['at'] + lanes.E_G, 8))

        def entry(self, name):
            return entry_of(self.mem, self.tbl, self.u[name]['at'])

        def lane(self, name):
            return self.entry(name)['lane']

        def log(self):
            return open(LOG, encoding='utf8').read()

        def skaven(self, shooters=('guns',), blockers=('stand',), all=0, enable=1, bytes_ok=True, bad_b=False):  # the Skaven mod's cave and table
            table = self.mem.alloc(0x1080)
            self.mem.write(table, struct.pack('<II', enable, all))
            self.mem.write(table + 0x10, b''.join(struct.pack('<Q', self.u[n]['at']) for n in shooters) + b'\0' * 8)
            self.mem.write(table + 0x400, b''.join(struct.pack('<Q', self.u[n]['at']) for n in blockers) + b'\0' * 8)
            sk = lanes.probe.skaven_cave
            cave = self.mem.alloc(len(sk.CAVE_A) + len(sk.CAVE_B) + len(sk.CAVE_C) + 16)
            part_a = sk.CAVE_A if bytes_ok else b'\x90' * len(sk.CAVE_A)
            part_b = sk.CAVE_B if not bad_b else b'\x90' + sk.CAVE_B[1:]
            self.mem.write(cave, part_a + struct.pack('<Q', table) + part_b + struct.pack('<Q', 0x1234) + sk.CAVE_C)
            self.hooks[skv_site] = cave

    if on('fire'):
        fire = Fire()
        fire.u['guns']['target'] = 103  # (the enemy's uid)
        fire.run(26)
        expect(fire.lane('stand') > 0, 'FIRE: a lane for the line of fire: ' + fire.log()[-1500:])
        e = fire.entry('stand')
        text = fire.log()
        expect(e['n'] == 1 and e['bias'] == -0.5 and abs(e['lane'] - 4.0) < 1e-5 and e['njobs'] == 1 and e['dirty'] == 1, 'FIRE: ONE lane of 4 m, all at once: %r' % e)
        fire.run(14)
        # across the line gun -> target (0.447, 0.894): (-0.894, 0.447); the lane is centred on the line through the middle of the unit
        expect(abs(e['ax'] + 0.8944) < 1e-3 and abs(e['az'] - 0.4472) < 1e-3 and abs(e['ox'] * e['ax'] + e['oz'] * e['az'] + 50000) < 0.5, 'FIRE: across the line: %r' % e)
        expect('FIRE OPEN A1.1.1 stand clears the line of fire of A1.1.5 guns at A2.1.1 enemy (134 m, lane 4.0 m, crossing at 67 m)' in text and text.count('FIRE OPEN') == 1, 'FIRE: the log: ' + text[-1500:])
        expect("FIRE: current_target() of A1.1.5 guns returned a table" in text and 'columns' not in text.split('FIRE OPEN')[1].split('\n')[0], 'FIRE: type logged once, no columns')
        expect(fire.lane('walk') == 0 and fire.lane('guns') == 0 and fire.lane('enemy') == 0 and fire.lane('aside') == 0, 'FIRE: only the unit in the line')
        # the target moves a little (the line moves 1 m at the unit): the same lane stays; 2.3 m (more than half a lane): planned anew at once
        # (085d: the gap is updated when the line moved more than TUNE.FIRE_GAP_DRIFT 0.5 m; a smaller move changes nothing; an update sends only the
        # models that must move and does not make the game hand out all places)
        fire.u['enemy'].update(dict(x=30.4))
        fire.run(14)
        expect('FIRE REPLAN' not in fire.log() and fire.lane('stand') > 0, 'FIRE: a small move (0.2 m): no new plan')
        fire.u['enemy'].update(dict(x=32.0))
        fire.run(14)
        expect('FIRE REPLAN A1.1.1 stand: gap updated, the line of A1.1.5 guns at A2.1.1 enemy moved 0.9 m' in fire.log() and fire.lane('stand') > 0 and 'CLOSING' not in fire.log(), 'FIRE: a 1 m move: the gap is updated: ' + fire.log()[-1500:])
        old_oz = fire.entry('stand')['oz']
        fire.u['enemy'].update(dict(x=35.1))
        fire.run(14)
        e = fire.entry('stand')
        expect('FIRE REPLAN A1.1.1 stand: gap updated, the line of A1.1.5 guns at A2.1.1 enemy moved 1.4 m' in fire.log() and fire.lane('stand') > 0 and abs(e['oz'] - old_oz) > 100 and 'CLOSING' not in fire.log(), 'FIRE: re-planned, not closed: ' + fire.log()[-1500:])
        # the target is lost: the lane closes after HOLD_TICKS
        fire.u['guns']['target'] = None
        fire.run(6)
        expect(fire.lane('stand') > 0 and 'CLOSING' not in fire.log(), 'FIRE: a short gap in the target does not close the lane')
        fire.run(40)
        expect(fire.lane('stand') == 0 and 'CLOSING A1.1.1 stand (the line of fire is clear)' in fire.log() and 'FIRE CLOSE A1.1.1 stand (the line of fire is clear)' in fire.log(), 'FIRE: closed when the target is lost: ' + fire.log()[-1500:])
        expect('ERROR' not in fire.log(), 'FIRE: no error')
        fire.u['guns']['target'] = 'error'  # current_target fails: nothing happens, no crash
        fire.run(40)
        expect(fire.lane('stand') == 0 and 'ERROR' not in fire.log() and fire.log().count('FIRE OPEN') == 1, 'FIRE: an erroring current_target does nothing')

        # a walker comes while the lane is open: the walker wins
        fire = Fire()
        fire.u['guns']['target'] = 103  # (the enemy's uid)
        fire.run(40)
        expect(fire.lane('stand') > 0 and 'FIRE OPEN A1.1.1 stand' in fire.log(), 'FIRE: open again in a fresh battle')
        fire.u['walk'].update(dict(x=0.0, z=-40.0, gx=0.0, gz=80.0, moving=True))
        for _ in range(40):
            fire.u['walk']['z'] += 1.0
            fire.tick()
        text = fire.log()
        expect('FIRE CLOSE A1.1.1 stand (a walker needs it)' in text and 'OPEN  A1.1.1 stand makes way for A1.1.2 walk' in text and text.count('FIRE OPEN') == 1, 'FIRE: the walker wins: ' + text[-2500:])
        # and no fire lane opens while the walker's lanes are open / the walker is about to need the unit
        fire.u['walk'].update(dict(x=0.0, z=-300.0, gx=0.0, gz=-300.0, moving=False))
        fire.run(150)
        expect(fire.log().count('FIRE OPEN') >= 1, 'FIRE: after the walker it may open again')

        # a walker is about to need the unit before the lane could open: no fire lane at all
        fire = Fire()
        fire.u['walk'].update(dict(x=0.0, z=-40.0, gx=0.0, gz=80.0, moving=True))
        fire.u['guns']['target'] = 103
        for _ in range(6):
            fire.u['walk']['z'] += 0.5
            fire.tick()
        expect('FIRE OPEN' not in fire.log(), 'FIRE: a walker on its way keeps the fire lane from opening: ' + fire.log()[-800:])

        # not a direct-fire gun (the mock's gun is not in the tables' list): nothing
        fire = Fire(script=SCRIPT)
        fire.u['guns']['target'] = 103
        fire.run(60)
        expect(fire.lane('stand') == 0 and 'FIRE' not in fire.log() and 'ERROR' not in fire.log(), 'FIRE: not a direct-fire gun: nothing')
        # the MCT option is off: nothing
        fire = Fire(mct={'military_drill_fire_lanes': False})
        fire.u['guns']['target'] = 103
        fire.run(60)
        expect(fire.lane('stand') == 0 and 'FIRE OPEN' not in fire.log() and 'clear the line of fire NO' in fire.log(), 'FIRE: MCT option off: nothing')
        fire = Fire(mct={'military_drill_fire_lanes': True})
        fire.u['guns']['target'] = 103
        fire.run(60)
        expect(fire.lane('stand') > 0 and 'clear the line of fire yes' in fire.log(), 'FIRE: MCT option on')
        # no ammunition / limbered: nothing
        for key, value in (('ammo', 0), ('limbered', True)):
            fire = Fire()
            fire.u['guns'][key] = value
            fire.u['guns']['target'] = 103
            fire.run(60)
            expect(fire.lane('stand') == 0 and 'FIRE OPEN' not in fire.log(), 'FIRE: %s: nothing' % key)
        # a target out of the gun's range: nothing
        fire = Fire()
        fire.u['guns'].update(dict(range=100.0, target=103))
        fire.run(60)
        expect(fire.lane('stand') == 0, 'FIRE: out of range: nothing')
        # current_target never gives a unit: nothing, and it is said once
        fire = Fire()
        fire.run(330)
        expect(fire.lane('stand') == 0 and fire.log().count('FIRE: current_target() returned no unit in') == 1 and 'ERROR' not in fire.log(), 'FIRE: no target ever: said once')

        # the Skaven mod (its cave and table at its place in the game code): it overrides the lane for the pairs it lets shoot through
        fire = Fire(skaven=dict(shooters=('guns',), blockers=('stand',)))
        fire.u['guns']['target'] = 103
        fire.run(60)
        expect(fire.lane('stand') == 0 and fire.log().count("FIRE: the Skaven mod shoots through A1.1.1 stand for A1.1.5 guns: no lane") == 1 and 'FIRE OPEN' not in fire.log(),
               'FIRE: the Skaven mod shoots through: no lane: ' + fire.log()[-1200:])
        fire = Fire(skaven=dict(shooters=('guns',), blockers=(), all=1))
        fire.u['guns']['target'] = 103
        fire.run(60)
        expect(fire.lane('stand') == 0 and 'FIRE OPEN' not in fire.log(), 'FIRE: the Skaven mod lets every friendly unit through (all = 1): no lane')
        fire = Fire(skaven=dict(shooters=('guns',), blockers=(), all=0))
        fire.u['guns']['target'] = 103
        fire.run(60)
        expect(fire.lane('stand') > 0, 'FIRE: the Skaven table lists the gun but not the unit: lane')
        fire = Fire(skaven=dict(shooters=(), blockers=('stand',)))
        fire.u['guns']['target'] = 103
        fire.run(60)
        expect(fire.lane('stand') > 0, 'FIRE: the Skaven table does not list the gun: lane')
        fire = Fire(skaven=dict(shooters=('guns',), blockers=('stand',), enable=0))
        fire.u['guns']['target'] = 103
        fire.run(60)
        expect(fire.lane('stand') > 0, 'FIRE: the Skaven mod is switched off: lane')
        fire = Fire(skaven=dict(shooters=('guns',), blockers=('stand',), bytes_ok=False))
        fire.u['guns']['target'] = 103
        fire.run(60)
        expect(fire.lane('stand') > 0 and 'ERROR' not in fire.log(), 'FIRE: something else at the Skaven mod\'s place: override off, lane')
        fire = Fire(skaven=dict(shooters=('guns',), blockers=('stand',), bad_b=True))
        fire.u['guns']['target'] = 103
        fire.run(60)
        expect(fire.lane('stand') > 0 and 'ERROR' not in fire.log(), 'FIRE: the second part of the Skaven cave differs (not its cave): override off, lane')
        # its cave comes later in the battle: the lane that was open closes (the Skaven mod decides from then on)
        fire = Fire()
        fire.u['guns']['target'] = 103
        fire.run(40)
        expect(fire.lane('stand') > 0, 'FIRE: lane before the Skaven mod is there')
        fire.skaven(shooters=('guns',), blockers=('stand',))
        fire.run(60)
        expect(fire.lane('stand') == 0 and 'the Skaven mod shoots through' in fire.log(), 'FIRE: the Skaven mod appears: the lane closes')
        # the place of the Skaven mod found twice / not at all: its override is not available (said once); the lane works
        for count in (2, 0):
            changed = dict(game_code, patterns=dict(game_code['patterns'], **{skv_pattern: (game_code['patterns'][skv_pattern][0] if count else None, count)}))
            mem3 = Memory()
            mem3.regions[UID_CODE & ~0xFFF] = bytearray(0x1000)
            mem3.write(UID_CODE, struct.pack('<I', UID_FIELD))
            hooks3 = {}
            b3 = run_battle(mem3, hooks3, changed, script=FIRE_SCRIPT)
            b3['units']['guns'].update(dict(x=-30.0, z=-60.0, gx=-30.0, gz=-60.0, ammo=10, range=150.0, target=103))
            mem3.write(b3['units']['guns']['model'] + 0x88, struct.pack('<3f', -30.0, 0.0, -60.0))
            b3['units']['enemy'].update(dict(x=30.0, z=60.0, gx=30.0, gz=60.0))
            tbl3 = struct.unpack('<Q', mem3.read(hooks3[BASE + lanes.SITES[0][1]] + 2, 8))[0]
            for n in range(60):
                if n == 1:
                    b3['phases']['Deployed']()
                for one in b3['units'].values():
                    now = struct.unpack('<I', mem3.read(one['clock'] + 0x98, 4))[0]
                    mem3.write(one['clock'] + 0x98, struct.pack('<I', now + 2))
                b3['timers'][TIMER]()
            text = open(LOG, encoding='utf8').read()
            expect(entry_of(mem3, tbl3, b3['units']['stand']['at'])['lane'] > 0 and text.count('the place of the Skaven mod in the game code was found %d times' % count) == 1, 'FIRE: Skaven pattern found %d times: said once, lane' % count)

        # --- E: the Skaven mod's place is searched ONCE per battle, and not at all when there is no direct-fire gun
        FINDS.clear()
        fire = Fire()
        fire.u['guns']['target'] = 103
        fire.run(100)
        expect(FINDS.get(skv_pattern) == 1, 'E1: the Skaven place is searched once per battle: %r' % FINDS.get(skv_pattern))
        FINDS.clear()
        fire = Fire(script=SCRIPT)
        fire.run(100)
        expect(FINDS.get(skv_pattern) is None, 'E1: no direct-fire gun in the battle: the Skaven mod is not looked for')

        # E2: an error while the lane opens closes it, the walkers' lanes are not touched, after 3 errors the unit is left out
        fire = Fire()
        fire.u['guns']['target'] = 103
        stand_off = fire.entry('stand')['at'] - fire.tbl + lanes.E_OX
        fire.b['lua'].execute("""
            local write = memreader_plus.write
            memreader_plus.write = function(p, off, value)
                if FAIL_OFF and off == FAIL_OFF then error('boom') end
                return write(p, off, value)
            end
        """)
        fire.b['lua'].globals().FAIL_OFF = stand_off
        fire.run(60)
        text = fire.log()
        expect(fire.lane('stand') == 0 and text.count('in the line of fire (') == 3 and 'it is left out of the line of fire' in text and 'MOD OFF' not in text and 'FIRE OPEN' not in text, 'E2: errors in the lane: ' + text[-1500:])
        fire.b['lua'].globals().FAIL_OFF = None
        fire.run(60)
        expect(fire.lane('stand') == 0 and fire.log().count('in the line of fire (') == 3, 'E2: left out: no more tries')
        fire.u['walk'].update(dict(x=0.0, z=-40.0, gx=0.0, gz=80.0, moving=True))
        for _ in range(40):
            fire.u['walk']['z'] += 1.0
            fire.tick()
        expect('OPEN  A1.1.1 stand makes way for A1.1.2 walk' in fire.log(), 'E2: the walkers still get their lanes')

        # E3: one body (a monster, a chariot, a single model) has no models to step aside: no lane for the line of fire
        fire = Fire()
        fire.u['guns']['target'] = 103
        fire.u['stand']['men'] = 1
        fire.run(60)
        expect(fire.lane('stand') == 0 and 'FIRE OPEN' not in fire.log(), 'E3: one body: no fire lane')

        # E4: a fire lane opens HOLD_TICKS after the unit's lanes closed at the earliest
        fire = Fire()
        fire.u['guns']['target'] = 103
        fire.run(26)
        expect(fire.lane('stand') > 0, 'E4: open')
        fire.u['guns']['target'] = None
        closed_n = None
        for n in range(80):
            fire.tick()
            if closed_n is None and fire.lane('stand') == 0:
                closed_n = n
                fire.u['guns']['target'] = 103
                break
        reopen = None
        for n in range(1, 40):
            fire.tick()
            if fire.lane('stand') > 0:
                reopen = n
                break
        expect(closed_n is not None and reopen is not None and 8 <= reopen <= 11, 'E4: reopened %r ticks after the close' % reopen)

        # E5: a lane that is planned anew is not planned again within FIRE.replan_every looks (three models: the line may swing across them)
        fire = Fire()
        for model, x in zip((fire.u['stand']['model'],), (0.0,)):
            pass
        old_list = fire.mem.read(fire.u['stand']['at'] + 0xa4, 12)
        models = []
        for x in (-3.0, 0.0, 3.0):
            m = fire.mem.alloc(0xc00)
            fire.mem.write(m + 0xa0, struct.pack('<f', 0.65))
            fire.mem.write(m + 0x88, struct.pack('<3f', x, 0.0, 0.0))
            fire.mem.write(m + 0x58, fire.mem.read(fire.u['stand']['model'] + 0x58, 8))
            models.append(m)
        lst = fire.mem.alloc(24)
        fire.mem.write(lst, b''.join(struct.pack('<Q', m) for m in models))
        fire.mem.write(fire.u['stand']['at'] + 0xa4, struct.pack('<I', 3))
        fire.mem.write(fire.u['stand']['at'] + 0xa8, struct.pack('<Q', lst))
        fire.u['guns']['target'] = 103
        fire.run(26)
        expect(fire.lane('stand') > 0, 'E5: open with three models')
        fire.u['enemy'].update(dict(x=35.1))
        for _ in range(14):  # (085d: replan_every 10 looks from the opening's plan)
            fire.tick()
            if fire.log().count('FIRE REPLAN') == 1:
                break
        first = fire.log().count('FIRE REPLAN')
        fire.u['enemy'].update(dict(x=24.9))
        fire.run(4)
        expect(first == 1 and fire.log().count('FIRE REPLAN') == 1, 'E5: the second plan waits: %d %d' % (first, fire.log().count('FIRE REPLAN')))
        fire.run(14)
        expect(fire.log().count('FIRE REPLAN') == 2, 'E5: and comes later')

    # H: ONE GAP PER GUN. The battery's crewed machines stand across the line; the lines converge on the target (half way: half
    # the offset), the standing unit opens a gap at each crossing (lanes per file: period q, blocks q - 4 m)
    def battle_h(offs, x0=-19.5, x1=19.5, run=30, **kw):
        h = Fire()
        h.battery(offs, **kw)
        h.row(x0, x1)
        h.u['guns']['target'] = 103
        h.run(run)
        return h

    if on('fire_gaps'):
        h = battle_h((-21.0, -7.0, 7.0, 21.0))  # 4 even guns: crossings -10.5 / -3.5 / 3.5 / 10.5
        e = h.entry('stand')
        text = h.log()
        expect(h.lane('stand') > 0 and e['n'] == 2 and e['bias'] == 0 and abs(e['lane'] - 4.0) < 1e-5 and abs(e['p'] - 3.0) < 1e-3, 'H: 4 guns: 4 gaps of 4 m with blocks of 3 m: %r %s' % (e, text[-1500:]))
        expect(h.centres(e) == [-10.5, -3.5, 3.5, 10.5], 'H: the gaps at the projected crossings: %r' % h.centres(e))
        expect('4 gaps of 4.0 m every 7.0 m' in text and 'FIRE: 4 machines of A1.1.5 guns through A1.1.1 stand, crossing it across the line at -10.5 / -3.5 / 3.5 / 10.5 m projected on the target, -21.0 / -7.0 / 7.0 / 21.0 m parallel to the line' in text, 'H: the log: ' + text[-1500:])
        expect(h.log().count('FIRE: 4 machines') == 1, 'H: the crossings are logged once')
        # a unit beside it closes it up (G < 0): the blocks grow by as much (P / (1 + G)), the gaps stay on the guns
        h = Fire()
        h.battery((-21.0, -7.0, 7.0, 21.0))
        h.row(-19.5, 19.5)
        h.u['aside'].update(dict(x=-45.0, z=0.0, gx=-45.0, gz=0.0))
        h.row(-60.0, -21.0, name='aside')
        h.u['guns']['target'] = 103
        h.run(30)
        e = h.entry('stand')
        g0 = h.extra(e)[0]
        expect(h.lane('stand') > 0 and g0 < -0.2 and e['p'] > 3.5 and h.centres(e) == [-10.5, -3.5, 3.5, 10.5] and 'closes up to' in h.log(), 'H: closing up: the gaps stay on the guns: G %r %r %s' % (g0, e, h.log()[-900:]))
        # a loose crewman and a machine without crew get no gap of their own
        h = battle_h((-21.0, -7.0, 7.0, 21.0), bare=(0.0,), loose=(14.0, -14.0))
        e = h.entry('stand')
        expect(h.lane('stand') > 0 and e['n'] == 2 and h.centres(e) == [-10.5, -3.5, 3.5, 10.5] and '4 gaps of 4.0 m every 7.0 m' in h.log(), 'H: loose crewmen / a machine without crew: no gap: %r %s' % (e, h.log()[-1200:]))
        # 3 guns (odd): one gap more than needed, on the side of the unit with FEWER models beyond the outermost needed gap
        h = battle_h((-14.0, 0.0, 14.0), x0=-9.0, x1=19.5)  # (across = -0.894 x: the models lie on the minus side)
        e = h.entry('stand')
        expect(h.lane('stand') > 0 and e['n'] == 2 and h.centres(e) == [-7.0, 0.0, 7.0, 14.0] and '3 gaps of 4.0 m every 7.0 m' in h.log(), 'H: 3 guns, the extra gap on the emptier plus side: %r %s' % (h.centres(e), h.log()[-1200:]))
        h = battle_h((-14.0, 0.0, 14.0), x0=-19.5, x1=9.0)
        e = h.entry('stand')
        expect(h.lane('stand') > 0 and e['n'] == 2 and h.centres(e) == [-14.0, -7.0, 0.0, 7.0], 'H: 3 guns, the extra gap on the emptier minus side: %r' % (h.centres(e),))
        # guns standing unevenly: ONE wide lane over all of them
        h = battle_h((-20.0, -5.0, 25.0))
        e = h.entry('stand')
        expect(h.lane('stand') > 0 and e['n'] == 1 and e['bias'] == -0.5 and abs(e['lane'] - 26.5) < 1e-3 and 'one wide lane of 26.5 m (the guns stand unevenly)' in h.log(), 'H: uneven: one wide lane: %r %s' % (e, h.log()[-1200:]))
        # gaps too close for a block between them (q 2 m): one wide lane
        h = battle_h((-6.0, -2.0, 2.0, 6.0))
        e = h.entry('stand')
        expect(h.lane('stand') > 0 and e['n'] == 1 and abs(e['lane'] - 10.0) < 1e-3 and 'one wide lane of 10.0 m (the gaps of 2.0 m leave no block of 2.0 m between them)' in h.log(), 'H: too tight: one wide lane: %r %s' % (e, h.log()[-1200:]))
        # one crewed machine: today's single lane of 4 m
        h = battle_h((6.0,))
        e = h.entry('stand')
        expect(h.lane('stand') > 0 and e['n'] == 1 and e['bias'] == -0.5 and abs(e['lane'] - 4.0) < 1e-5 and 'one lane of 4.0 m' in h.log() and h.centres(e) != [], 'H: one machine: one lane: %r' % (e,))
        # the whole front would have to open: no lane (said once)
        h = Fire()
        h.u['stand']['width'] = 10.0
        h.battery((-14.0, -4.0, 14.0))
        h.row(-6.0, 6.0)
        h.u['guns']['target'] = 103
        h.run(60)
        expect(h.lane('stand') == 0 and h.log().count('FIRE: no lane in A1.1.1 stand for the line of A1.1.5 guns: it would take the whole front') == 1 and 'FIRE OPEN' not in h.log(), 'H: whole front: no lane, said once: ' + h.log()[-1200:])
        # the target moves sideways: the plan is made anew and ALL the numbers are written (P, N, bias, S0, G)
        h = battle_h((-21.0, -7.0, 7.0, 21.0))
        old = h.entry('stand')
        h.u['enemy'].update(dict(x=40.0))
        h.run(14)
        e = h.entry('stand')
        text = h.log()
        expect('FIRE REPLAN A1.1.1 stand' in text and '4 gaps of 4.0 m every ' in text.split('FIRE REPLAN')[1], 'H: replanned: ' + text[-1200:])
        expect(h.lane('stand') > 0 and e['n'] == 2 and abs(e['oz'] - old['oz']) + abs(e['ox'] - old['ox']) > 0.5 and (e['ax'], e['az']) != (old['ax'], old['az']) and abs(e['p'] - old['p']) < 3 and e['bias'] == 0, 'H: replan wrote the plan: %r vs %r' % (e, old))
        # a battery that was one gun and became several (count changed): P / N / bias are rewritten, not only the origin
        h = battle_h((6.0,))
        expect(h.entry('stand')['n'] == 1 and h.entry('stand')['bias'] == -0.5, 'H: one gun first')
        h.battery((-21.0, -7.0, 7.0, 21.0))
        h.run(14)
        e = h.entry('stand')
        expect('FIRE REPLAN A1.1.1 stand' in h.log() and e['n'] == 2 and e['bias'] == 0 and abs(e['p'] - 3.0) < 1e-3 and h.centres(e) == [-10.5, -3.5, 3.5, 10.5], 'H: the replan writes P / N / bias: %r %s' % (e, h.log()[-800:]))
        # (fix 8) the hit count flickers 4 <-> 2 every look: no new plan (REPLAN spam); a count that stays gets its plan after 2 x replan_every looks
        h = battle_h((-21.0, -7.0, 7.0, 21.0))
        before = h.log().count('FIRE REPLAN')
        for i in range(40):
            if i % 2 == 0:  # (the lines are looked at every FIRE.every = 2 looks: the count changes between two checks)
                h.battery((-7.0, 7.0) if (i // 2) % 2 else (-21.0, -7.0, 7.0, 21.0))
            h.run(1)
        expect(h.log().count('FIRE REPLAN') == before, 'fix 8: a flickering hit count makes no new plan: %d -> %d %s' % (before, h.log().count('FIRE REPLAN'), h.log()[-800:]))
        # (085c: an open lane is planned only every replan_every looks, so the count is seen at most every 6th look: the new count is set from the
        # original one after a settled stretch; it waits 2 x replan_every looks from its first sight, which is at the earliest at the change)
        h.battery((-21.0, -7.0, 7.0, 21.0))
        h.run(30)
        before = h.log().count('FIRE REPLAN')
        h.battery((-7.0, 7.0))
        h.run(4)
        expect(h.log().count('FIRE REPLAN') == before, 'fix 8: a new count waits 2 x replan_every looks')
        h.run(20)
        expect(h.log().count('FIRE REPLAN') == before + 1 and '2 gaps of 4.0 m every ' in h.log().split('FIRE REPLAN')[-1], 'fix 8: a count that stays gets its plan: %s' % h.log()[-800:])
        # a machine line just beside the unit hits it only after the shift: it joins at the re-check (one plan more)
        # (the outer block steps 2 x 4 m outwards: the model at 17.4 stands at 25.4; the line at 24 passes 6.6 m from it, then 1.4 m)
        h = battle_h((-14.0, 0.0, 14.0, 48.0), x0=-19.5, x1=9.0)
        text = h.log()
        expect(h.lane('stand') > 0 and h.entry('stand')['n'] == 1 and 'one wide lane of 35.0 m (the guns stand unevenly)' in text.split('FIRE OPEN')[1], 'H: a line beside the unit that the shift moves the blocks into joins: ' + text[-1200:])
        # (the same battery with the unit narrower on that side: the fourth line never hits it: 3 gaps)
        h = battle_h((-14.0, 0.0, 14.0, 48.0), x0=-12.0, x1=9.0)
        expect(h.lane('stand') > 0 and '3 gaps of 4.0 m every 7.0 m' in h.log() and h.entry('stand')['n'] == 2, 'H: no hit even after the shift: 3 gaps: ' + h.log()[-800:])
    if on('half_close'):
        # O: LANE-BY-LANE CLOSING. Two ranged walkers (4 bodies each, one in each lane of a 3-block plan: strips -1 (x > 0: across = -x) and 0),
        # the second 3 m behind the first; the one that is through first closes ITS lane alone (the walker that still needs the other lane
        # stays served), the rest closes when the other is through (the quick close)
        BODIES = ((-1.0, 0.0), (1.0, 0.0), (-1.0, -1.5), (1.0, -1.5))
        LANE_X = 8.4  # (half a lane period: 13.3 + 3.5 = 16.8 m; the lanes lie at x = +8.4 (strip -1: across = -x) and -8.4 (strip 0))

    def half_battle(first_lane, quick=True, width=40.0, lag=3.0):
        h = Fire()
        h.u['stand']['width'] = width
        h.row(-19.5, 19.5)
        h.u['stand']['men'] = 62
        walkers = {}
        for name, lane, z0 in (('walk', first_lane, -40.0), ('walk2', -1 - first_lane, -40.0 - lag)):
            models = [h.model(dx, z0 + dz, 0.65) for dx, dz in BODIES]
            h.set_models(name, models)
            h.u[name].update(dict(x=0.0, z=z0, gx=0.0, gz=80.0, moving=True, men=4, range=100.0 if quick else 0.0))
            walkers[name] = (models, LANE_X if lane == -1 else -LANE_X)
        return h, walkers

    def half_step(h, walkers, stop=()):  # (the walkers come in a block round x = 0 and are in their lanes from 12 m before the unit: the columns of cave 4)
        for name, (models, x) in walkers.items():
            if name not in stop:
                h.u[name]['z'] += 1.0
            for m, (dx, dz) in zip(models, BODIES):
                h.mem.write(m + 0x88, struct.pack('<3f', (x if h.u[name]['z'] > -12.0 else 0.0) + dx, 0.0, h.u[name]['z'] + dz))
        h.tick()

    def shifts(h, e):  # d of a model in the blocks -1 / 0 / +1 (x = +13.3 / 0 / -13.3) as cave 1 shifts it
        g, s0 = h.extra(e)
        out = []
        for x in (13.3, 0.0, -13.3):
            s = (x - e['ox']) * e['ax'] + (0 - e['oz']) * e['az']
            n = max(-e['n'], min(e['n'], int((s / e['p'] + 0.5) // 1)))
            out.append(round((n + e['bias']) * e['lane'] + g * (s - s0), 2))
        return out
    if on('half_close'):
        for first_lane, closed, bias in ((-1, -1, 0), (0, 0, -1)):
            h, walkers = half_battle(first_lane)
            full = None
            for i in range(200):
                half_step(h, walkers)
                if full is None and h.lane('stand') > 0:
                    e = h.entry('stand')
                    if e['lane'] > 3.4:
                        full = (dict(e), shifts(h, e))
                if 'HALF CLOSE' in h.log():
                    break
            text = h.log()
            e = h.entry('stand')
            L = round(e['lane'], 2)
            expect(full is not None and full[0]['n'] == 1 and abs(full[0]['p'] - 13.3) < 0.1 and full[0]['bias'] == 0 and full[1] == [-L, 0.0, L], 'O: the full lanes first: %r' % (full,))
            want = [0.0, 0.0, L] if closed == -1 else [-L, 0.0, 0.0]
            expect(text.count('HALF CLOSE A1.1.1 stand: lane %d ' % closed) == 1 and 'CLOSING' not in text.split('HALF CLOSE')[0] and h.lane('stand') > 0
                   and e['n'] == 1 and e['p'] == 100000 and e['bias'] == bias and shifts(h, e) == [round(v, 2) for v in want],
                   'O: lane %d closed alone: one-lane frame, the blocks next to it join: %r %r %s' % (closed, e, shifts(h, e), text[-1500:]))
            # the other walker is through: the rest closes at once (the quick close)
            for i in range(80):
                half_step(h, walkers)
            text = h.log()
            expect(h.lane('stand') == 0 and 'CLOSING A1.1.1 stand (quick: ' in text and text.index('HALF CLOSE') < text.index('CLOSING A1.1.1'), 'O: then the quick close: ' + text[-1500:])
        # a follower after the half close: both lanes again, the original numbers
        h, walkers = half_battle(-1)
        for i in range(200):
            half_step(h, walkers)
            if 'HALF CLOSE' in h.log():
                break
        h.u['walk3'].update(dict(x=0.0, z=-30.0, gx=0.0, gz=80.0, moving=True))
        for i in range(30):  # (it restores once the follower is near enough to need the lanes: FIRE.quick.soon)
            h.u['walk3']['z'] += 1.0
            half_step(h, walkers, stop=('walk', 'walk2'))
        expect(h.log().count('HALF CLOSE A1.1.1 stand: lane') == 1 and h.log().count('both lanes open again') == 1, 'O: restored once, no flapping: ' + h.log()[-1500:])
        e = h.entry('stand')
        expect('HALF CLOSE A1.1.1 stand: both lanes open again' in h.log() and e['p'] < 20 and e['n'] == 1 and e['bias'] == 0 and h.lane('stand') > 0, 'O: a follower: the original lanes again: %r %s' % (e, h.log()[-1200:]))
        # not ranged walkers, a wider front (5 blocks) -> as today: no half close
        h, walkers = half_battle(-1, quick=False)
        for i in range(160):
            half_step(h, walkers)
        expect('HALF CLOSE' not in h.log() and 'CLOSE A1.1.1 stand' in h.log(), 'O: not ranged: as today: ' + h.log()[-800:])
        h, walkers = half_battle(-1, width=100.0)
        for i in range(160):
            half_step(h, walkers)
        expect('HALF CLOSE' not in h.log() and 'OPEN ' in h.log(), 'O: a wide front (more than 3 blocks): as today: ' + h.log()[-800:])
    # M: the room beside a standing unit: guard mode opens the lanes INSIDE its outline (narrower lanes when the models cannot close up
    # enough, never below one walker file), the default steps out 3.5 m per side at most, with a neighbour min(gap / 2 - margin, 3.5)
    REAL_FIRE = REAL_SCRIPT.replace('local FIRE = { guns = { ', "local FIRE = { guns = { ['guns'] = true, ", 1)
    # (085h item 9: a big walker's lanes are not limited to OUT_MAX any more - a free side does not close up. The older column checks below are about the geometry of cave 4 in a
    # room that IS limited, so they run with the old rule: the line that lifts the limit for big walkers is taken out)
    OLDROOM = REAL_FIRE.replace('if not s.guard_now and model_radius(w) >= TUNE.BIG_RADIUS then out_max = nil end', '', 1) if 'model_radius(w) >= TUNE.BIG_RADIUS then out_max = nil' in REAL_FIRE else REAL_FIRE

    def outer(h, x_walk=0.0, step=1.3, guard=False, aside=None, x0=-19.5, x1=19.5):
        # a pass through 'stand' (two ranks, step apart); returns the plan and how far its outermost models stand beyond the outline
        # (across the walker's way: ax = -1, i.e. -x), as cave 1 shifts them (the same arithmetic as shifted() in the script)
        h.u['stand']['guard'] = guard
        h.row(x0, x1, step=step)
        h.u['stand']['men'] = 2 * (int((x1 - x0) / step + 1e-6) + 1)  # (the men of the models there are)
        if aside:
            h.u['aside'].update(dict(x=aside[0], z=0.0, gx=aside[0], gz=0.0))
            h.row(aside[1], aside[2], name='aside')
        h.u['walk'].update(dict(x=x_walk, z=-40.0, gx=x_walk, gz=80.0, moving=True))
        for _ in range(45):
            h.u['walk']['z'] += 1.0
            h.tick()
        e = h.entry('stand')
        g, s0 = h.extra(e)
        lo, hi = 1e9, -1e9
        out_lo, out_hi = 1e9, -1e9
        x = x0
        while x <= x1 + 1e-6:
            a = -x
            s = (x - e['ox']) * e['ax'] + (0 - e['oz']) * e['az']
            n = max(-e['n'], min(e['n'], int((s / e['p'] + 0.5) // 1)))
            d = (n + e['bias']) * e['lane'] + g * (s - s0)
            lo, hi = min(lo, a), max(hi, a)
            out_lo, out_hi = min(out_lo, a + d), max(out_hi, a + d)
            x += step
        return e, g, (lo - out_lo, out_hi - hi)

    if on('guard_room'):
        h = Fire(script=REAL_FIRE)
        e, g, (below, above) = outer(h, step=1.9, guard=True)  # (ranks 1.9 m apart: it closes up to ~17 % at most, so the lanes narrow)
        text = h.log()
        expect(h.lane('stand') > 0 and 'GUARD MODE: the lanes open inside its outline' in text and 'lanes narrowed from 6.0 to ' in text and -0.25 < g < -0.1 and below <= 1.05 and above <= 1.05,
               'M: guard mode: no model beyond the outline, lanes narrowed: G %.2f beyond %.2f / %.2f %r %s' % (g, below, above, e, text[-1500:]))
        expect(e['lane'] >= 2 * 0.65 + 0.4, 'M: a lane never narrower than one file of the walker: %r' % e['lane'])
        h = Fire(script=REAL_FIRE)
        e, g, (below, above) = outer(h, step=2.6)
        expect(h.lane('stand') > 0 and 'out of its outline at most 2.0 m per side' in h.log() and 'GUARD MODE' not in h.log() and below <= 2.05 and above <= 2.05 and max(below, above) > 1.5,
               'M: default: the outer models stand 5 %% of the 40 m front = 2.0 m out at most: %.2f / %.2f G %.2f' % (below, above, g))
        # tightly packed ranks cannot close up in guard mode: the lanes get as narrow as one walker file (and the unit steps out a little)
        h = Fire(script=REAL_FIRE)
        e, g, (below, above) = outer(h, step=1.3, guard=True)
        expect(g == 0 and e['lane'] <= 2 * 0.65 + 0.5 + 0.01 and ('(one walker file at least)' in h.log()), 'M: packed ranks: lanes as narrow as one file: G %r %r %s' % (g, e['lane'], h.log()[-900:]))
        # with a neighbour 3 m away: min(gap / 2 - 0.3, 2.0) = 1.2 m on that side (the other side 2.0, 5 % of 40 m)
        h = Fire(script=REAL_FIRE)
        e, g, (below, above) = outer(h, step=2.6, aside=(-45.0, -60.2, -23.8))
        expect(h.lane('stand') > 0 and above <= 1.25 and below <= 2.05, 'M: a neighbour: %.2f (<= 1.2) on its side, %.2f (<= 2.0) on the other: G %.2f %s' % (above, below, g, h.log()[-600:]))
        # an off-centre crossing (walker 8 m beside the middle): guard mode still keeps everything inside the outline
        h = Fire(script=REAL_FIRE)
        e, g, (below, above) = outer(h, x_walk=8.0, step=2.6, guard=True)  # (here closing up is enough: no narrowing)
        expect(h.lane('stand') > 0 and below <= 1.05 and above <= 1.05, 'M: off-centre in guard mode: inside the outline: %.2f / %.2f G %.2f %r' % (below, above, g, e))
    # L: fast monstrous infantry / cavalry form COLUMNS like infantry (4 files x 2 ranks of mounts 3 m apart, riders on them), not one
    # lane per file: the formation comes from the BODIES (mounts), not from the models (riders and mounts would count twice)
    def big_walk(kind, bodies=8, files=4, space=3.0):  # (the mock's walker 'walk' is made a unit of that kind of the game's tables)
        script = FIRE_SCRIPT.replace('local KIND = { ', "local KIND = { ['walk'] = '%s', " % kind, 1)
        expect(script != FIRE_SCRIPT, 'L: the walker has a kind')
        h = Fire(script=script)
        h.row(-19.5, 19.5)
        h.u['walk']['men'] = bodies
        offs, models = [], []
        for i in range(bodies):
            off = ((i % files - (files - 1) / 2) * space, -(i // files) * space)
            mount = h.model(off[0], -40.0 + off[1], 1.5, owner=h.u['walk']['at'])
            rider = h.model(off[0], -40.0 + off[1], 0.5, carrier=mount)
            offs += [(mount, off), (rider, off)]
            models += [mount, rider]
        h.set_models('walk', models)
        h.u['walk'].update(dict(x=0.0, z=-40.0, gx=0.0, gz=80.0, moving=True))
        for _ in range(50):
            h.u['walk']['z'] += 1.0
            for m, off in offs:
                h.mem.write(m + 0x88, struct.pack('<3f', off[0], 0.0, h.u['walk']['z'] + off[1]))
            h.tick()
        return h
    if on('big_park_ghost'):
        for kind in ('mc', 'mi'):
            h = big_walk(kind)
            text = h.log()
            e = h.entry('stand')
            expect('OPEN  A1.1.1 stand makes way for A1.1.2 walk' in text and 'keeps its formation' not in text and 'ONE lane' not in text.split('OPEN ')[1].split(chr(10))[0]
                   and 'walker 4 files x 2 ranks, 3.0 m apart -> columns' in text, 'L: %s: columns from the bodies, not one lane per file: %s' % (kind, text[-1500:]))
            expect(e['n'] >= 1 and e['bias'] == 0 and e['lane'] < 12 and 'COLUMNS A1.1.2 walk in A1.1.1 stand' in text and ' | per lane ' in text, 'L: %s: 2 or more normal lanes + the per-lane COLUMNS line: %r %s' % (kind, e, text[-1500:]))
        # a cart (veh) keeps its formation as before
        h = big_walk('veh')
        expect('keeps its formation' in h.log() or 'ONE lane' in h.log(), 'L: a chariot still keeps its formation: ' + h.log()[-900:])
        # K: a unit PARKED inside another is left to the game: the lanes close 5 s after the walker stopped (also while it fights: its
        # w.moving flickers), the game hands out the places of BOTH units anew (E_DIRTY) - not for a walker that fights, has a crew
    def park_inside(script=FIRE_SCRIPT, fight=False):
        h = Fire(script=script)
        h.row(-5.0, 5.0)
        h.u['walk'].update(dict(x=0.0, z=-40.0, gx=0.0, gz=0.0, moving=True))
        for _ in range(40):
            h.u['walk']['z'] = min(0.0, h.u['walk']['z'] + 1.0)
            h.tick()
        h.u['walk'].update(dict(moving=fight, melee=fight))
        h.run(4)
        expect(h.lane('stand') > 0, 'K: open with the walker inside: ' + h.log()[-900:])
        h.mem.write(h.entry('walk')['at'] + lanes.E_DIRTY, struct.pack('<I', 0)) # (the cave clears it in the game; the walker's columns marked it)
        h.dirty_seen = []  # (the walker's columns going off mark it too: the cave clears it, so look at it after every look and clear it)
        for i in range(120):
            if fight:
                h.u['walk']['moving'] = i % 2 == 0 # (flickers)
            was = h.lane('stand') > 0
            h.tick()
            at = h.entry('walk')['at'] + lanes.E_DIRTY
            if was and h.lane('stand') == 0:
                h.dirty_seen.append(struct.unpack('<I', h.mem.read(at, 4))[0])  # (the look that closed the lanes)
            h.mem.write(at, struct.pack('<I', 0))
        return h
    if on('big_park_ghost'):
        h = park_inside()
        expect(h.lane('stand') == 0 and 'CLOSING A1.1.1 stand (nobody is passing any more)' in h.log() and h.dirty_seen == [1] and 'A1.1.2 walk parked inside A1.1.1 stand: its places are handed out anew too' in h.log(),
               'K: a parked walker: the lanes close, its places are handed out anew: ' + h.log()[-1200:])
        h = park_inside(fight=True)
        expect(h.lane('stand') == 0 and h.dirty_seen == [0] and 'parked inside' not in h.log(), 'K: a walker that fights inside: the lanes close, no E_DIRTY for it: ' + h.log()[-1200:])
        script = FIRE_SCRIPT.replace('local KIND = { ', "local KIND = { ['walk'] = 'art', ", 1)
        expect(script != FIRE_SCRIPT, 'K: the walker is artillery')
        h = park_inside(script=script)
        expect(h.dirty_seen == [0] and 'parked inside' not in h.log(), 'K: a crewed walker: no E_DIRTY for it: ' + h.log()[-1200:])
        # N: ETHEREAL units (ghosts): no lanes for them, none for their walk; they stay neighbours for the room check
    def ghost_script(*names):
        script = FIRE_SCRIPT.replace('ethereal = { ', 'ethereal = { ' + ''.join("['%s'] = true, " % n for n in names), 1)
        expect(script != FIRE_SCRIPT, 'N: the ghost table is there')
        return script

    def pass_through(h):
        h.u['walk'].update(dict(x=0.0, z=-40.0, gx=0.0, gz=80.0, moving=True))
        for _ in range(50):
            h.u['walk']['z'] += 1.0
            h.tick()
    if on('big_park_ghost'):
        h = Fire(script=FIRE_SCRIPT)
        pass_through(h)
        expect(h.log().count('OPEN  A1.1.1 stand makes way for A1.1.2 walk') == 1, 'N: (control) a normal pass opens lanes')
        h = Fire(script=ghost_script('stand'))
        pass_through(h)
        expect(h.lane('stand') == 0 and 'OPEN ' not in h.log() and h.log().count('A1.1.1 stand makes no way: it is ethereal') == 1, 'N: an ethereal stander opens nothing, said once: ' + h.log()[-900:])
        h = Fire(script=ghost_script('walk'))
        pass_through(h)
        expect(h.lane('stand') == 0 and 'OPEN ' not in h.log() and 'MISSED?' not in h.log(), 'N: an ethereal walker gets no lanes and is not a MISSED?: ' + h.log()[-900:])
        h = Fire(script=ghost_script('walk'))
        h.battery((-21.0, -7.0, 7.0, 21.0))
        h.row(-19.5, 19.5)
        h.u['guns']['target'] = 103
        h.u['walk'].update(dict(x=0.0, z=-40.0, gx=0.0, gz=80.0, moving=True))
        for _ in range(40):
            h.u['walk']['z'] += 0.5
            h.tick()
        expect('FIRE OPEN A1.1.1 stand' in h.log() and 'FIRE CLOSE' not in h.log(), 'N: an ethereal walker does not keep the fire lane from opening: ' + h.log()[-900:])
        # a ghost unit beside the stander is still a neighbour: the room check closes the unit up
        h = Fire(script=ghost_script('aside'))
        h.battery((-21.0, -7.0, 7.0, 21.0))
        h.row(-19.5, 19.5)
        h.u['aside'].update(dict(x=-45.0, z=0.0, gx=-45.0, gz=0.0))
        h.row(-60.0, -21.0, name='aside')
        h.u['guns']['target'] = 103
        h.run(30)
        expect(h.lane('stand') > 0 and h.extra(h.entry('stand'))[0] < -0.2, 'N: an ethereal neighbour counts for the room check: ' + h.log()[-900:])
    if on('alliance'):
        # J: the G2 log: once per walker / stander / order, with the numbers the decision rests on (an order that ends before the
        # stander's front: stops short, no lanes; one that reaches it: lanes)
        for goal, word, opens in ((-12.0, 'stops short: no lanes', False), (-2.0, 'reaches it: lanes', True)):
            h = Fire()
            h.row(-19.5, 19.5)
            h.u['walk'].update(dict(x=0.0, z=-40.0, gx=0.0, gz=goal, moving=True))
            for _ in range(30):
                h.u['walk']['z'] = min(goal, h.u['walk']['z'] + 1.0)
                h.tick()
            said = [l for l in h.log().splitlines() if 'G2? A1.1.1 stand, order of A1.1.2 walk: way ' in l]
            expect(len(said) == 1 and (' -> ' + word + ' | position() ') in said[0] and 'ordered_position() 0.0 / %.1f' % goal in said[0] and 'models middle 0.0 / -' in said[0] and 'heading order' in said[0]
                   and 'stander front ' in said[0] and 'walker front bodies ' in said[0] and 'radii 0.65 / 0.65' in said[0], 'J: the G2 line (%s): %r' % (word, said))
            expect((h.lane('stand') > 0) == opens, 'J: lanes %s' % opens)
        # I2: armies of one alliance are one side: a walker of the alliance's SECOND army gets lanes in a stander of its FIRST army, a unit of
        # the other alliance never does
        h = Fire(split={'walk'})
        h.u['walk'].update(dict(x=0.0, z=-40.0, gx=0.0, gz=80.0, moving=True))
        for _ in range(50):
            h.u['walk']['z'] += 1.0
            h.tick()
        expect('OPEN  A1.1.1 stand makes way for A1.2.1 walk' in h.log() and 'ERROR' not in h.log(), 'I2: a walker of another army of the alliance gets its lanes: ' + h.log()[-1200:])
        h = Fire(split={'walk'})
        h.u['enemy'].update(dict(x=0.0, z=40.0, gx=0.0, gz=-80.0, moving=True))
        h.mem.write(h.u['enemy']['model'] + 0x88, struct.pack('<3f', 0.0, 0.0, 40.0))
        for _ in range(60):
            h.u['enemy']['z'] -= 1.0
            h.mem.write(h.u['enemy']['model'] + 0x88, struct.pack('<3f', 0.0, 0.0, h.u['enemy']['z']))
            h.tick()
        expect(h.lane('stand') == 0 and 'OPEN ' not in h.log() and 'ERROR' not in h.log(), 'I2: a unit of the other alliance walks through: no lanes: ' + h.log()[-1200:])
        # (and the stander of the second army, the walker of the first)
        h = Fire(split={'stand'})
        h.u['walk'].update(dict(x=0.0, z=-40.0, gx=0.0, gz=80.0, moving=True))
        for _ in range(50):
            h.u['walk']['z'] += 1.0
            h.tick()
        expect('OPEN  A1.2.1 stand makes way for A1.1.1 walk' in h.log(), 'I2: a stander of the second army: ' + h.log()[-1200:])
        # no leadership patterns (found 0 or 2 times): the mod is NOT switched off; every unit counts as x1 (wavering x0.5)
        for name in ('is_routing', 'is_wavering'):
            for count in (2, 0):
                pattern = lanes.sites.parse(lanes.sites.PATTERNS[name])[0]
                changed = dict(game_code, patterns=dict(game_code['patterns'], **{pattern: (game_code['patterns'][pattern][0] if count else None, count)}))
                mem2 = Memory()
                mem2.regions[UID_CODE & ~0xFFF] = bytearray(0x1000)
                mem2.write(UID_CODE, struct.pack('<I', UID_FIELD))
                hooks2 = {}
                b2 = run_battle(mem2, hooks2, changed)
                b2['phases']['Deployed']()
                expect('MOD OFF' not in log() and len(hooks2) == 5 and 'LEADERSHIP UNAVAILABLE (every unit counts as x1, wavering x0.5): the %s code was found %d times' % (name, count) in log()
                       and 'mod ON: 8 units' in log(), 'leadership pattern %s found %d times: the mod stays on: %s' % (name, count, log()[-1500:]))
    # 085c, fix 1: the squeeze of the walker's columns comes from its outermost files against the clamped outer strip. A walker wider
    # than the standing unit has files beyond the outermost strip, which cave 4 clamps into it with the same squeeze (run 51: demigryph
    # files landed in the blocks); a walker narrower than the strip squeezes exactly as before
    def wfields(h, name='walk'):  # what the walker's columns hold in the table (cave 4 reads it)
        at = h.entry(name)['at']
        q, ox, oz, ax, az, bx, bz, t0, t1, c, kl, kh, sl, sh = struct.unpack('<10f2i2f', h.mem.read(at + lanes.E_WQ, 0x38))
        lag, ahead = struct.unpack('<2f', h.mem.read(at + lanes.E_WLAG, 8))
        return dict(q=q, ox=ox, oz=oz, ax=ax, az=az, c=c, kl=kl, kh=kh, sl=sl, sh=sh, lag=lag)

    def column_walk(kind, files, ranks, space, radius, riders=False, stand=(-14.5, 14.5), guard=False, script=None, stand_step=1.3, stand_ranks=2,
                    steps=50, looks=None, before=None, skew=None, rank_space=None, z0=-40.0, mct=None, each=None, step=1.0, split=()):
        # the mock's walker 'walk' (a unit of that kind of the game's tables: files x ranks bodies, riders on their mounts) through a row of
        # standing models; returns the battle, the walker's fields and the bodies' places at the opening, and the widest lane seen
        script = (script or REAL_FIRE).replace('local KIND = { ', "local KIND = { ['walk'] = '%s', " % kind, 1)
        h = Fire(script=script, mct=mct, split=split)
        h.u['stand']['guard'] = guard
        h.row(stand[0], stand[1], step=stand_step, ranks=stand_ranks)
        h.u['stand']['men'] = stand_ranks * (int((stand[1] - stand[0]) / stand_step + 1e-6) + 1)
        h.u['stand']['width'] = stand[1] - stand[0]
        offs, models, bodies = [], [], []
        for i in range(files * ranks):
            off = ((i % files - (files - 1) / 2) * space + (skew(i) if skew else 0.0), -(i // files) * (rank_space or space))
            body = h.model(off[0], z0 + off[1], radius, owner=h.u['walk']['at'] if riders else 0)
            offs.append((body, off))
            models.append(body)
            bodies.append(off)
            if riders:
                rider = h.model(off[0], z0 + off[1], 0.5, carrier=body)
                offs.append((rider, off))
                models.append(rider)
        h.set_models('walk', models)
        h.offs = offs
        h.u['walk'].update(dict(x=0.0, z=z0, gx=0.0, gz=80.0, moving=True, men=files * ranks, width=(files - 1) * space + 2 * radius))
        got = dict(fields=None, pos=None, lane=0.0, models=models)
        if before:
            before(h)
        for look_no in range(looks or steps):
            if each:
                each(h, look_no)
            h.u['walk']['z'] += step
            for m, off in offs:
                h.mem.write(m + 0x88, struct.pack('<3f', off[0], 0.0, h.u['walk']['z'] + off[1]))
            h.tick()
            if h.lane('stand') > 0:
                got['lane'] = max(got['lane'], h.entry('stand')['lane'])
                if got['fields'] is None and h.entry('walk')['at']:
                    got['fields'] = wfields(h)
                    got['pos'] = [(off[0], h.u['walk']['z'] + off[1]) for off in bodies]
        return h, got

    def landing(f, pos):  # cave 4 on the bodies in front of the standing unit: k = round(s / Q - 0.5) clamped, s' = c + (s - c) C; the largest |s' - c|
        worst = 0.0
        for x, z in pos:
            s = (x - f['ox']) * f['ax'] + (z - f['oz']) * f['az']
            if f['sl'] <= s <= f['sh']:
                c = (max(f['kl'], min(f['kh'], s // f['q'])) + 0.5) * f['q']
                worst = max(worst, abs((s - c) * f['c']))
        return worst
    # demigryphs: 10 files x 3 ranks 3.8 m apart (a front of 34 m; 085d: 4.4 m and more apart they get lanes per file, see below) through 29 m of
    # halberdiers: the outer files have to land inside their lane
    if on('columns'):
        h, got = column_walk('mc', 10, 3, 3.8, 1.2, riders=True, script=OLDROOM)
        f = got['fields']
        expect(f is not None and f['kl'] == -1 and f['kh'] == 0, '085c: the demigryph columns are on: ' + h.log()[-1500:])
        expect('columns: block test failed' in h.log() and 'keeps its formation' not in h.log() and 'n lanes per side: front 1, needed 1, room clamped' in h.log(),
               '085d: 3.8 m apart: the block test fails, columns, the lane count line (room clamped by the default 3.5 m): ' + h.log()[-1500:])
        expect(landing(f, got['pos']) <= got['lane'] / 2 - 1.2 + 0.01, '085c: every demigryph file lands inside its lane: %.2f > %.2f (lane %.2f) %r' % (landing(f, got['pos']), got['lane'] / 2 - 1.2, got['lane'], f))
        strip = float(re.search(r'outer strip ([0-9.]+) m', h.log()).group(1)) if 'outer strip ' in h.log() else 0.0
        expect(strip > f['q'] + 1.0 and abs(f['c'] - (got['lane'] - 2.4) / strip) < 0.01 and abs(f['lag'] - 0.8) < 1e-6,
               '085c: the squeeze counts the whole front (outer strip %.1f m, strip q %.1f), the lag stays 0.8: %r' % (strip, f['q'], f))
        # walkers narrower than the strip: the squeeze is (lane - 2 r) / q exactly as before (EW.C, EW.LAG and the lane of 085b)
        for kind, files, ranks, space, radius, riders in (('inf', 16, 5, 1.2, 0.4, False), ('mi', 5, 2, 6.8, 1.1, False), ('mi', 4, 2, 6.7, 1.0, False), ('cav', 8, 3, 2.0, 0.8, True)):
            h, got = column_walk(kind, files, ranks, space, radius, riders=riders)
            f = got['fields']
            expect(f is not None and f['kl'] == -1, '085c: %s walker has columns: %s' % (kind, h.log()[-1200:]))
            expect('keeps its formation' not in h.log() and 'one lane per file' not in h.log(), '085d: a %s walker (squeezable) stays with the columns, measured not by kind: %s' % (kind, h.log()[-900:]))
            want = max(0.0, min(0.5, (got['lane'] - 2 * radius) / f['q']))
            expect(abs(f['c'] - want) < 1e-4 and abs(f['lag'] - 0.8) < 1e-6 and ('outer strip %.1f m' % f['q']) in h.log(),
                   '085c: a %s walker narrower than the strip keeps the squeeze of 085b: C %.4f (want %.4f), lag %.2f, strip %r: %r' % (kind, f['c'], want, f['lag'], f['q'], (f, got['lane'], [l[:900] for l in h.log().splitlines() if 'OPEN ' in l][:1])))
        # 085c, fix 2: guard mode. A stander in guard mode closes up to its floor and steps out GUARD_OUT (1 m) at most: a walker of columns gets ONE
        # lane centred on its way as wide as that room (make_room again for it), or none when not one file fits / the column would be too deep
    def outline(h, stand=(-14.5, 14.5), step=1.3):  # how far the standing row's models stand beyond its outline as cave 1 shifts them (below / above)
        e = h.entry('stand')
        g, s0 = h.extra(e)
        lo, hi, out_lo, out_hi = 1e9, -1e9, 1e9, -1e9
        x = stand[0]
        while x <= stand[1] + 1e-6:
            a = -x  # (across = -x: the walker goes along +z)
            s = (x - e['ox']) * e['ax'] + (0 - e['oz']) * e['az']
            n = max(-e['n'], min(e['n'], int((s / e['p'] + 0.5) // 1)))
            d = (n + e['bias']) * e['lane'] + g * (s - s0)
            lo, hi, out_lo, out_hi = min(lo, a), max(hi, a), min(out_lo, a + d), max(out_hi, a + d)
            x += step
        return lo - out_lo, out_hi - hi, g
    # (1) halberdiers: 16 files x 5 ranks of 1.2 m through a guard stander that can close up (ranks 1.9 m apart): ONE lane, one column of files
    if on('columns'):
        h, got = column_walk('inf', 16, 5, 1.2, 0.4, guard=True, stand_step=1.9)
        e, text = h.entry('stand'), h.log()
        f = got['fields']
        expect(h.lane('stand') > 0 and e['n'] == 1 and e['bias'] == -0.5 and 'GUARD MODE: ONE lane of ' in text and f is not None and f['kl'] == 0 and f['kh'] == 0
               and 'narrower lanes' in text, '085c: guard mode, halberdiers: ONE lane, one column: %r %r %s' % (e, f, text[-1800:]))
        expect(got['lane'] >= 2 * 0.4 + 0.5 + 1.2 - 0.05 and got['lane'] > 2.9, '085c: guard mode: the lane holds at least 2 files (%.2f m), not the 1.8 - 2.9 m of two lanes' % got['lane'])
        below, above, g = outline(h, step=1.9)
        expect(g < -0.05 and below <= 1.05 and above <= 1.05, '085c: guard mode, one lane: make_room again with S0 = 50000, both halves inside the outline (1 m): %.2f / %.2f G %.2f' % (below, above, g))
        expect(abs(h.extra(e)[1] - 50000) < 1 and 'GUARD MODE: no room' not in text, '085c: guard mode, one lane: S0 50000 (one-lane frame): %r' % (h.extra(e),))
        # (2) demigryphs in a guard stander packed too tight to close up: not one file fits (2 r + 0.5 = 2.9 m): no lanes, said once, walker goes round
        h, got = column_walk('mc', 10, 3, 4.4, 1.2, riders=True, guard=True)
        text = h.log()
        expect(h.lane('stand') > 0 and text.count('GUARD MODE: no room inside the outline of A1.1.1 stand for A1.1.2 walk (needs 2.9 m for one file, has ') == 1
               and ' - normal lanes instead' in text and 'ERROR' not in text, '085f F3 (was 085c): guard mode, demigryphs: no room: the normal lanes open, one line: ' + text[-1800:])
        # (2b) 085c review 7: the 'guard' late entry goes when the walker gets a new order (a new pass is looked at afresh)
        for reorder, lines in ((False, 1), (True, 2)):
            h, got = column_walk('mc', 10, 3, 4.4, 1.2, riders=True, guard=True, looks=30)
            if reorder:
                h.u['walk'].update(dict(gx=12.0))
            for _ in range(4):
                h.u['walk']['z'] += 1.0
                h.tick()
            said = h.log().count('GUARD MODE: no room inside the outline of A1.1.1 stand for A1.1.2 walk')
            expect(said == 1 and h.lane('stand') > 0,  # (085f: the normal lanes open at the first look, once)
                   '085c review 7: guard late entry, new order %s: said %d times: %s' % (reorder, h.log().count('GUARD MODE: no room'), h.log()[-1200:]))
        # (3) a column that would be too deep (16 files in 2 m: one file per column, 16 times as deep as the walker): the same
        h, got = column_walk('inf', 16, 5, 1.2, 0.4, guard=True, stand_step=1.3)
        text = h.log()
        expect(h.lane('stand') > 0 and text.count('GUARD MODE: no room inside the outline of A1.1.1 stand for A1.1.2 walk') == 1 and 'times as deep as the walker, at most 7.5' in text and ' - normal lanes instead' in text,
               '085f F3 (was 085c): guard mode, a column too deep: the normal lanes: ' + text[-1800:])
        # (4) not in guard mode: the same walker gets its two lanes as before
        h, got = column_walk('inf', 16, 5, 1.2, 0.4, guard=False, stand_step=1.3)
        expect(h.lane('stand') > 0 and h.entry('stand')['bias'] == 0 and 'GUARD MODE' not in h.log() and got['fields']['kl'] == -1, '085c: no guard mode: two lanes: ' + h.log()[-1200:])
    # 085d bundle 1 (A3 + review 1 - 4, B, C, G): big walkers whose files cannot be squeezed get LANES PER FILE (the walker keeps its formation)
    def open_line(h):
        return ([l for l in h.log().splitlines() if 'OPEN ' in l] or [''])[0]

    def margin_of(h):  # contact time - split time of the first OPEN line = the margin the lanes were opened with
        m = re.search(r'contact in ([0-9.]+) s; split takes ([0-9.]+) s', open_line(h))
        return float(m.group(1)) - float(m.group(2)) if m else None
    if on('columns'):
        # (V1) demigryphs 10 files x 3 ranks 4.6 m apart (run 52: squeezed to 0.9 m, a model needs 2.4 m) through 29 m of halberdiers: one lane per file
        # that crosses the standing unit, at the files' places (spacing from the bodies), no columns, the lane NOT widened by LANE_EXTRA_BIG
        h, got = column_walk('mc', 10, 3, 4.6, 1.2, riders=True, looks=32)
        e, text, line = h.entry('stand'), h.log(), open_line(h)
        expect(h.lane('stand') > 0 and (got['fields'] is None or got['fields']['q'] == 0) and e['bias'] == 0 and 'one lane per file' in line and 'no columns (the walker keeps its formation' in line,
               '085d A3: demigryphs 4.6 m apart: lanes per file, no columns: %r %s' % (e, line[-1500:]))
        expect(abs(e['lane'] - 3.1) < 0.01, '085d review 1 (fix 1): the lane of a big walker is 2 r + LANE_EXTRA_BIG capped at spacing - 2 rs - 0.2 = 3.1 m (was 2.9): %r' % e['lane'])
        expect(e['n'] == 4 and 'spacing 4.60 m from its bodies' in line and '8 of 10 files cross the standing unit -> 4 lanes per side (cap; all files would be 5)' in line,
               '085d review 1 / 2: the count of lanes capped at the crossing files, the spacing from the bodies: %r %s' % (e, line[-1500:]))
        expect('vector: squeezed ' in line and ' m < needs 2.60 m, block 1.50 m >= 1.50 m' in line and 'margin 1.75 s' in line,
               '085d: the OPEN line says why vector, the block and the margin: ' + line[-1500:])
        expect('; room: need ' in line and ' m per side vs ' in line and 'closing up' in line, '085d review 8 (fix 8): the OPEN line of lanes per file says the room: ' + line[-700:])
        expect(e['n'] * e['lane'] <= 12.5, '085d review 1: farthest shift %.1f m (not the 14.5 m of all files)' % (e['n'] * e['lane']))
        for i in range(10):
            x = (i - 4.5) * 4.6
            if abs(x) < 14.5 + 0.65 + 1.45:  # (a file whose lane reaches the standing unit)
                expect(any(abs(c - (-x)) < 0.06 for c in h.centres(e)), '085d review 2: a lane at the file x = %.1f: %r' % (x, h.centres(e)))
        # (V2) files that do not line up (one file 0.6 m off its place): not lanes per file, the columns of 085c, the reason in the line
        h, got = column_walk('mc', 10, 3, 4.6, 1.2, riders=True, looks=32, skew=lambda i: 0.6 if i % 10 == 3 else 0.0)
        line = open_line(h)
        expect(h.lane('stand') > 0 and 'one lane per file' not in line and 'files do not line up: file 7 of 10 lies 0.60 m from its place' in line and got['fields'] is not None,
               '085d review 2: files out of line: columns, with the reason: ' + h.log()[-1500:])
        # (V3) the formation estimate of formation() drifts when the ranks are closer than the files (13 files for 10): the lanes still come from the bodies
        h, got = column_walk('mc', 10, 3, 4.6, 1.2, riders=True, looks=32, rank_space=2.0)
        line = open_line(h)
        expect(h.lane('stand') > 0 and (('one lane per file' not in line and ('block test failed' in line or 'files do not line up' in line)) or ('one lane per file' in line and 'spacing 4.60 m from its bodies' in line)),
               '085d review 2: a drifting estimate gives lanes at the real spacing or columns, never lanes at the estimate: ' + line[-1200:])
        # (V4) guard mode: no lanes per file (the lanes open inside the outline): the columns path decides (no room for demigryphs there)
        h, got = column_walk('mc', 10, 3, 4.6, 1.2, riders=True, guard=True, looks=32)
        text = h.log()
        expect(h.lane('stand') > 0 and 'keeps its formation' not in text and 'GUARD MODE: no room inside the outline of A1.1.1 stand for A1.1.2 walk' in text,
               '085d review 4: guard mode: no lanes per file, the 085c answer: ' + text[-1500:])
        # (V5) a cart (veh) of 12 files keeps its formation too (VECTOR_MAX_FILES 8 is gone), only its crossing files get lanes
        h, got = column_walk('veh', 12, 2, 4.6, 1.2, riders=False, looks=32)
        e, line = h.entry('stand'), open_line(h)
        expect(h.lane('stand') > 0 and 'one lane per file' in line and 'files cross the standing unit' in line and 1 <= e['n'] <= 4 and (got['fields'] is None or got['fields']['q'] == 0),
               '085d: 12 files of a veh: lanes per file, capped: %r %s' % (e, line[-1200:]))
        # (B) the number of lanes: a big walker whose squeezed files do not fit gets more lanes per side when the room allows (no 3.5 m limit here)
        h, got = column_walk('mc', 10, 3, 3.8, 1.2, riders=True, script=FIRE_SCRIPT, looks=32)
        e, line = h.entry('stand'), open_line(h)
        expect(h.lane('stand') > 0 and e['n'] == 2 and e['bias'] == 0 and 'n lanes per side: front 1, needed 2, room ok' in line and 'columns: block test failed' in line,
               '085d B: 3.8 m apart demigryphs through 29 m: 2 lanes per side: %r %s' % (e, line[-1200:]))
        h, got = column_walk('mc', 14, 3, 3.6, 1.2, riders=True, script=FIRE_SCRIPT, stand=(-22.0, 22.0), looks=32)
        expect(h.entry('stand')['n'] == 3 and 'front 1, needed 3, room ok' in open_line(h), '085d B: 14 files: 3 lanes per side: ' + open_line(h)[-900:])
        # (B) with the real room limit the extra lanes do not fit: front count kept, said so
        h, got = column_walk('mc', 10, 3, 3.8, 1.2, riders=True, looks=32, script=OLDROOM)
        expect(h.entry('stand')['n'] == 1 and 'front 1, needed 1, room clamped' in open_line(h), '085d B: no room for more lanes: ' + open_line(h)[-900:])
        # (B) MCT forces its own count whatever the walker needs
        h, got = column_walk('mc', 10, 3, 3.8, 1.2, riders=True, script=FIRE_SCRIPT, looks=32, mct={'military_drill_lanes': '2'})
        expect(h.entry('stand')['n'] == 1 and 'room forced by MCT' in open_line(h), '085d B: MCT 2 lanes: ' + open_line(h)[-900:])
        # (B) infantry through infantry of the same width: as before (one lane per side, no counting of more)
        h, got = column_walk('inf', 16, 5, 1.2, 0.4, script=FIRE_SCRIPT, stand=(-9.0, 9.0), looks=32)
        expect(h.entry('stand')['n'] == 1 and 'front 1, needed 1' in open_line(h), '085d B: infantry: n stays: ' + open_line(h)[-900:])
        # (fix 2, 085d review) the B loop's trial plans read the guard mode of THIS look (s.guard_now was the one of the last opening) and the narrowing
        # of a trial plan (narrow_to) does not stay on the plan that is used: a stander that was in guard mode when the walker was refused, and is not
        # any more, gets its extra lanes (n 2) for the walker's new order
        def leave_guard(h, i):  # (the walker was refused by the guard mode at the first order; at look 34 the stander is not in guard mode any more and the walker, sent back 35 m, is ordered again)
            if i == 34:
                h.u['stand']['guard'] = False
                h.u['walk'].update(dict(gx=12.0, z=h.u['walk']['z'] - 35.0))
        h, got = column_walk('mc', 10, 3, 3.8, 1.2, riders=True, guard=True, script=FIRE_SCRIPT, looks=75, z0=-40.0, each=leave_guard)
        expect(h.lane('stand') > 0 and h.entry('stand')['n'] == 2 and 'front 1, needed 2, room ok' in open_line(h) and 'lanes narrowed' not in open_line(h),
               '085d review 2 (fix 2): guard mode left: the trial plans are not made with the old guard mode: %r %s' % (h.entry('stand'), open_line(h)[-700:] or h.log()[-900:]))
        # (C) the opening margin: a big walker (radius >= 1.0) has BIG_MARGIN (1.0 s) more than infantry, which keeps the 0.75 s
        hb, _ = column_walk('mc', 10, 3, 4.6, 1.2, riders=True, z0=-90.0, looks=85)
        mb, line_b = margin_of(hb), open_line(hb)  # (the log file is the one of the LAST battle: read it before the next one)
        hi, _ = column_walk('inf', 16, 5, 1.2, 0.4, z0=-90.0, looks=85)
        mi, line_i = margin_of(hi), open_line(hi)
        expect(mb is not None and mi is not None and mb >= 1.3 and mi <= 1.2 and mb - mi >= 0.6 and 'opening margin 1.75 s (0.75 + big 1.00: radius 1.20' in line_b and 'opening margin 0.75 s;' in line_i + ';',
               '085d C: margins: big %r, infantry %r: %s / %s' % (mb, mi, line_b[-300:], line_i[-300:]))
        # (G) the log of a one-lane (one_column) walker: its middle is measured from the lane's middle (it was q / 2 off)
        h, got = column_walk('one', 8, 3, 1.2, 0.4)
        cols = [l for l in h.log().splitlines() if 'COLUMNS A1.1.2 walk in A1.1.1 stand' in l and 'walker middle' in l]
        m = re.search(r'walker middle (-?[0-9.]+) m across', cols[0]) if cols else None
        expect(m is not None and abs(float(m.group(1))) < 1.0, '085d G/E5: a centred one-column walker is ~0 m from the lane middle: %r %s' % (cols[:1], open_line(h)[-500:]))
    # 085c, P3: a walker whose front is already deep inside the stander when the stander begins to stand gets no lanes (LATE, left to the game);
    # a unit within SETTLED of its order that "stands" does not walk (two units inside each other opened for each other)
    def late_battle(inside, fast=False, ticks=45):
        # a deep stander (12 ranks, z 0 .. -14.3, its front at -14.3 m); the walker (moving, stuck) has its front `inside` m inside it; the stander
        # stands after STILL_TICKS looks, so the walker is there when it begins to stand
        h = Fire(script=FIRE_SCRIPT)
        h.row(-14.5, 14.5, ranks=12)
        h.u['stand']['men'] = 12 * 23
        h.u['walk'].update(dict(x=0.0, z=-15.6 + inside, gx=0.0, gz=80.0, moving=True, fast=fast, fastv=15.0))
        h.run(ticks)
        return h
    if on('late'):
        h = late_battle(11.0)
        text = h.log()
        expect(h.lane('stand') == 0 and 'OPEN ' not in text and text.count('LATE A1.1.2 walk already 11.') == 1 and ' m inside A1.1.1 stand - left to the game' in text
               and 'ERROR' not in text, '085c P3: a walker 11 m inside when the stander begins to stand: no lanes, one LATE line: ' + text[-1800:])
        h = late_battle(1.0)
        expect(h.lane('stand') > 0 and 'LATE ' not in h.log() and 'OPEN  A1.1.1 stand makes way for A1.1.2 walk' in h.log(), '085c P3: 1 m inside: opens as today: ' + h.log()[-1200:])
        # LATE_INSIDE counts at least the walker's speed x one look: a running walker (15 m/s: 3 m) 2.5 m inside opens, a walking one (3 m/s) is LATE
        h = late_battle(2.5, fast=True)
        expect(h.lane('stand') > 0 and 'LATE ' not in h.log(), '085c P3: a fast walker 2.5 m inside is not late: ' + h.log()[-1200:])
        h = late_battle(2.5)
        expect(h.lane('stand') == 0 and h.log().count('LATE A1.1.2 walk already 2.5') == 1, '085c P3: a slow walker 2.5 m inside is late: ' + h.log()[-1200:])
        # two units standing inside each other, both "moving" within 5 m of their (same) orders for 4 s: they stand, they do not walk: no opening
        h = Fire(script=FIRE_SCRIPT)
        h.row(-14.5, 14.5, ranks=6)
        h.row(-8.0, 8.0, ranks=4, name='walk')
        h.u['stand']['men'] = 6 * 23
        h.u['walk']['men'] = 4 * 13
        h.u['stand'].update(dict(gx=0.0, gz=3.0, moving=True))
        h.u['walk'].update(dict(x=0.0, z=-2.0, gx=0.0, gz=1.0, moving=True))
        h.run(60)
        expect('OPEN ' not in h.log() and 'LATE ' not in h.log() and h.lane('stand') == 0 and h.lane('walk') == 0, '085c P3: two units within SETTLED of their orders, inside each other: no opening: ' + h.log()[-1500:])
    # 085c, P4 (the stutter): the cost per look. Counted with wrappers on the fake memreader (reads of a model's place at 0x88 and of its carrier
    # at 0x2e0 per model) and with counters patched into the script (plans)
    def count_reads(h):
        h.b['lua'].execute("""
            READS, CARRIERS = {}, {}
            local mr = memreader_plus
            local read, read_pointer = mr.read, mr.read_pointer
            mr.read = function(p, off, n) if off == 0x88 then READS[p] = (READS[p] or 0) + 1 end return read(p, off, n) end
            mr.read_pointer = function(p, off) if off == 0x2e0 then CARRIERS[p] = (CARRIERS[p] or 0) + 1 end return read_pointer(p, off) end
        """)

    def reads_of(h, address, what='READS'):
        return h.b['lua'].eval(what)[address] or 0
    # FIRE.unit: a standing unit that no line passes near has its models NOT read by the line of fire (the cheap FIRE.near test comes first)
    if on('perf_log'):
        fire = Fire()
        count_reads(fire)
        fire.u['guns']['target'] = 103
        fire.run(100)
        n_far = reads_of(fire, fire.u['aside']['model'])
        expect(fire.log().count('FIRE OPEN') >= 1 and n_far <= 30, '085c P4: FIRE reads no model of a unit far from every line: %d reads of the far unit in 100 looks (probe only: ~20)' % n_far)
        # FIRE.keep: an open lane is planned only every FIRE.replan_every looks (not on every FIRE look), and the machines' places are read once per
        # FIRE.skv_every looks per gun, not on every look
        plan_script = FIRE_SCRIPT.replace('function FIRE.plan(s, ln, places)\n', 'function FIRE.plan(s, ln, places)\n\tPLANS = (PLANS or 0) + 1\n', 1)
        expect(plan_script != FIRE_SCRIPT, '085c P4: the plan counter is in')
        fire = Fire(script=plan_script)
        count_reads(fire)
        fire.battery((-21.0, -7.0, 7.0, 21.0))
        fire.row(-19.5, 19.5)
        fire.u['guns']['target'] = 103
        fire.run(30)
        expect(fire.lane('stand') > 0, '085c P4: the lane is open: ' + fire.log()[-900:])
        plans0 = fire.b['lua'].eval('PLANS')
        lst = struct.unpack('<Q', fire.mem.read(fire.u['guns']['at'] + 0xa8, 8))[0]
        models = [struct.unpack('<Q', fire.mem.read(lst + 8 * i, 8))[0] for i in range(struct.unpack('<I', fire.mem.read(fire.u['guns']['at'] + 0xa4, 4))[0])]
        machines = sorted(set(struct.unpack('<Q', fire.mem.read(m + 0x2e0, 8))[0] for m in models))  # (the crewmen's carriers: the unit's list holds the crew only)
        expect(len(machines) == 4, '085c P4: the battery has 4 machines: %d of %d models' % (len(machines), len(models)))
        reads0 = sum(reads_of(fire, m) for m in machines)
        fire.run(60)
        plans, reads = fire.b['lua'].eval('PLANS') - plans0, sum(reads_of(fire, m) for m in machines) - reads0
        expect(fire.lane('stand') > 0 and 'CLOSING' not in fire.log() and 3 <= plans <= 12, '085c P4: an open lane is planned every replan_every looks: %d plans in 60 looks (30 FIRE looks)' % plans)
        expect(reads <= 4 * 8, '085c P4: the machines are read every skv_every looks: %d reads of 4 machines in 60 looks' % reads)
        # the stagger: units spread over the two looks of FIRE.every: the lane still closes when the target is lost
        fire.u['guns']['target'] = None
        fire.run(60)
        expect(fire.lane('stand') == 0 and 'FIRE CLOSE A1.1.1 stand (the line of fire is clear)' in fire.log(), '085c P4: the lane of the line of fire closes: ' + fire.log()[-900:])
        # the test log: the lines of a look are written in ONE go (the file is opened once per look, not once per line); the PERF line names the phases
        fire = Fire()
        fire.b['lua'].execute('OPENS = 0; local o = io.open; io.open = function(...) OPENS = OPENS + 1; return o(...) end')
        fire.u['walk'].update(dict(x=0.0, z=-40.0, gx=0.0, gz=80.0, moving=True))
        most_lines, most_opens, before_lines = 0, 0, len(fire.log().splitlines())
        for _ in range(60):
            fire.u['walk']['z'] += 1.0
            opens = fire.b['lua'].eval('OPENS')
            fire.tick()
            lines = len(fire.log().splitlines())
            most_lines, most_opens, before_lines = max(most_lines, lines - before_lines), max(most_opens, fire.b['lua'].eval('OPENS') - opens), lines
        text = fire.log()
        expect(most_lines >= 3 and most_opens <= 1 and 'OPEN  A1.1.1 stand makes way for A1.1.2 walk' in text, '085c P4: one write per look: %d lines in the busiest look, %d file opens' % (most_lines, most_opens))
        perf = [l for l in text.splitlines() if ' PERF ' in l]
        expect(perf and 'ms per look by phase: look ' in perf[-1] and ', the line of fire ' in perf[-1] and ', log ' in perf[-1] and ', collect / report ' in perf[-1] and 'the longest look was in: ' in perf[-1]
               and 'closing sends put off 0' in perf[-1], '085c P4: the PERF line has the phases: %r' % (perf[-1:],))
        # timing() is asked up to 3 times per candidate per look: the same answer within one look comes from the memo (calculations < calls)
        memo_script = FIRE_SCRIPT.replace('function FIRE.timing_calc(s, w, lanes)\n', 'function FIRE.timing_calc(s, w, lanes)\n\tTCALC = (TCALC or 0) + 1\n', 1).replace('local function timing(s, w, lanes)\n', 'local function timing(s, w, lanes)\n\tTALL = (TALL or 0) + 1\n', 1)
        expect(memo_script.count('TCALC = (TCALC or 0)') == 1 and memo_script.count('TALL = (TALL or 0)') == 1, '085c P4: the timing counters are in the script')
        fire = Fire(script=memo_script)
        fire.u['walk'].update(dict(x=0.0, z=-40.0, gx=0.0, gz=80.0, moving=True))
        for _ in range(50):
            fire.u['walk']['z'] += 1.0
            fire.tick()
        calc, calls = fire.b['lua'].eval('TCALC'), fire.b['lua'].eval('TALL')
        expect(fire.lane('stand') > 0 and 0 < calc < calls, '085c P4: the timing memo: %d calculations for %d calls' % (calc, calls))
        # an infantry walker's models are read ONCE per look (bodies_of takes the places that model_places read: no carrier read of its models)
        h, got = column_walk('inf', 16, 5, 1.2, 0.4, before=count_reads)
        expect(h.lane('stand') > 0 and reads_of(h, got['models'][5], 'CARRIERS') == 0, '085c P4: an infantry walker model is not read twice per look (carrier reads: %d)' % reads_of(h, got['models'][5], 'CARRIERS'))
        # 085c review 6: a send deferred 3 looks in a row is sent at the 4th look at the latest, however much the look has sent
        busy_script = FIRE_SCRIPT.replace('-- are the bodies of walker w inside standing unit s,', 'function BUSYTEST(s) state.sent_look = TUNE.SEND_MAX; return FIRE.busy_sending(s) end\n-- are the bodies of walker w inside standing unit s,', 1)
        expect(busy_script != FIRE_SCRIPT, '085c review 6: BUSYTEST is in the script')
        h = Fire(script=busy_script)
        got = h.b['lua'].eval('(function() local s = {} local r = {} for i = 1, 5 do r[i] = BUSYTEST(s) and 1 or 0 end return table.concat(r, ",") end)()')
        expect(got == '1,1,1,0,1', '085c review 6: deferred 3 looks in a row, sent on the 4th, then deferred again: %r' % (got,))
        # a whole closing-type send waits for the next look once SEND_MAX models went out (two standers close in the same look: the second one a look later)
        send_script = FIRE_SCRIPT.replace('SEND_MAX = 250,', 'SEND_MAX = 40,', 1)
        expect(send_script != FIRE_SCRIPT, '085c P4: SEND_MAX is in the script')
        h = Fire(script=send_script)
        h.row(-19.5, 19.5)
        h.row(180.5, 219.5, name='aside')
        at = h.entry('aside')['at']
        h.mem.write(at + lanes.E_TPL, struct.pack('<3fI QQ HH I I I', 1.0, 2.0, 3.0, 0, 0, 0, 0xE3A5, 0xC07B, 7, 0, 0))
        h.mem.write(at + lanes.E_SEQ, struct.pack('<I', 1))
        h.u['stand']['men'] = h.u['aside']['men'] = 62
        for name, x in (('walk', 0.0), ('walk2', 200.0)):
            h.u[name].update(dict(x=x, z=-40.0, gx=x, gz=80.0, moving=True))
        seen_at = {}
        for i in range(170):
            for name in ('walk', 'walk2'):
                h.u[name]['z'] += 1.0
            h.tick()
            text = h.log()
            for key, label in (('stand', 'A1.1.1 stand'), ('aside', 'A1.1.3 aside')):
                if key not in seen_at and 'CLOSING %s (' % label in text:
                    seen_at[key] = i
        expect(len(seen_at) == 2 and seen_at['aside'] == seen_at['stand'] + 1 and h.log().count('OPEN  A1.1.1 stand') == 1 and h.log().count('OPEN  A1.1.3 aside') == 1,
               '085c P4: the second closing send waits one look: %r %s' % (seen_at, h.log()[-1500:]))
        expect(h.lane('stand') == 0 and h.lane('aside') == 0 and 'ERROR' not in h.log(), '085c P4: both closed in the end: ' + h.log()[-900:])
        # 085c, D3: a big walker's lane band at contact (debug log only): how many standing models reach into the band, the smallest clearance per side
        h, got = column_walk('mc', 10, 3, 3.8, 1.2, riders=True)  # (085d: 3.8 m apart = columns; 4.4 m and more apart = lanes per file, which have no columns line)
        band = [l for l in h.log().splitlines() if 'lane band at contact: A1.1.2 walk: ' in l]
        expect(len(band) == 1 and ' standing models inside the band (lane ' in band[0] and 'walker radius 1.20), clearance ' in band[0] and ' m on the minus / plus side of the lane centres' in band[0],
               '085c D3: one lane band line for a demigryph walker: %r' % (band,))
        h, got = column_walk('inf', 16, 5, 1.2, 0.4)
        expect('lane band at contact' not in h.log() and 'COLUMNS A1.1.2 walk in A1.1.1 stand' in h.log(), '085c D3: no band line for a walker of radius 0.4: ' + h.log()[-900:])
    # --- 085d bundle 2 -----------------------------------------------------------------------------------------------
    def clear_jobs(h, name='stand'):  # (cave 3 took every job: the mock has no cave 3)
        h.mem.write(h.entry(name)['at'] + lanes.E_PENDING, struct.pack('<I', 0))

    def set_dirty(h, value, name='stand'):
        h.mem.write(h.entry(name)['at'] + lanes.E_DIRTY, struct.pack('<I', value))

    def open_row_lane(script=FIRE_SCRIPT, foes=(), run=40, take=False):  # the stander is a row of 62 models; the gun shoots at 'enemy'; take: cave 3 takes every job at once (else nobody does: dropped after 3 s, 085d review 6)
        h = Fire(script=script, foes=foes)
        h.row(-19.5, 19.5)
        h.u['stand']['men'] = 62
        h.u['guns']['target'] = 103
        for _ in range(run):
            h.tick()
            if take:
                clear_jobs(h)
        return h

    if on('bundle2'):
        import re as _re
        # F (review 10): the guns' target UNIT changes back and forth every second (run 52: 16 x in 2 min): the lane is not re-formed; it is,
        # once, when the new target is kept for 2 s (TUNE.FIRE_DEBOUNCE); the old plan holds meanwhile
        h = open_row_lane(foes=('aside',))
        h.u['aside'].update(dict(x=31.0, z=60.0, gx=31.0, gz=60.0))  # (the line moves ~0.5 m at the stander)
        expect(h.lane('stand') > 0 and 'FIRE OPEN A1.1.1 stand' in h.log(), 'F10: the lane is open: ' + h.log()[-900:])
        before = h.entry('stand')
        for i in range(36):
            if i % 5 == 0:
                h.u['guns']['target'] = 102 if (i // 5) % 2 == 0 else 103
            h.tick()
        expect(h.log().count('FIRE REPLAN') == 0 and h.lane('stand') > 0 and 'CLOSING' not in h.log() and h.entry('stand')['njobs'] == before['njobs'] and h.entry('stand')['oz'] == before['oz'],
               'F10: a target that flips every second does not re-form the lane: %s' % h.log()[-1200:])
        h.u['guns']['target'] = 103
        h.run(12)
        h.u['guns']['target'] = 102
        h.run(8)
        expect(h.log().count('FIRE REPLAN') == 0, 'F10: a new target held 1.6 s is not enough')
        h.run(10)
        expect(h.log().count('FIRE REPLAN') == 1 and 'new target A2.1.1 aside for A1.1.4 guns (kept ' in h.log() and ', re-formed | ' in h.log() and h.lane('stand') > 0 and 'CLOSING' not in h.log(),
               'F10: a new target kept 2 s: ONE re-forming: ' + h.log()[-1200:])

        # F11: a gap update sends only the models that must move (N of M), and the game is not told to hand out all places (E_DIRTY stays 0)
        h = open_row_lane(take=True)
        clear_jobs(h)
        set_dirty(h, 0)
        h.u['enemy'].update(dict(x=33.0))  # the line moves ~1.3 m at the stander
        h.run(14)
        text = h.log()
        moved = [l for l in text.splitlines() if 'FIRE REPLAN A1.1.1 stand: gap updated' in l]
        got = _re.search(r'\| (\d+) of (\d+) models moved', moved[0]) if moved else None
        e = h.entry('stand')
        expect(got and 0 < int(got.group(1)) <= 8 and int(got.group(2)) >= 60 and e['njobs'] == int(got.group(1)) and e['dirty'] == 0 and 'CLOSING' not in text,
               'F11: a gap update: only the models that must move (%r), E_DIRTY %d: %s' % (got and got.groups(), e['dirty'], text[-1200:]))
        # (TUNE.FIRE_GAP_DIRTY 1 makes the game hand out all places at once: the switch works)
        dirty_script = FIRE_SCRIPT.replace('FIRE_GAP_DIRTY = 0,', 'FIRE_GAP_DIRTY = 1,', 1)
        expect(dirty_script != FIRE_SCRIPT, 'F11: the switch is in the script')
        h = open_row_lane(script=dirty_script, take=True)
        clear_jobs(h)
        set_dirty(h, 0)
        h.u['enemy'].update(dict(x=33.0))
        h.run(14)
        expect('gap updated' in h.log() and h.entry('stand')['dirty'] == 1, 'F11: FIRE_GAP_DIRTY 1: the game hands out all places: %r' % h.entry('stand')['dirty'])
        # while jobs of the last send are still waiting they are void: then all models are sent (nobody is left without a job)
        h = open_row_lane(run=1)
        for _ in range(40):
            if 'FIRE OPEN' in h.log():
                break
            h.tick()
        h.u['enemy'].update(dict(x=33.0))
        h.run(14)
        got = _re.search(r'gap updated.*\| (\d+) models sent', h.log())
        expect(got and int(got.group(1)) >= 60, 'F11: jobs still pending: every model is sent: %s' % h.log()[-600:])

        # F3: the lane stays about 3 s (FIRE_GRACE 7 looks + HOLD_TICKS 8) after the gun lost its target
        h = open_row_lane()
        h.u['guns']['target'] = None
        n_close = None
        for i in range(1, 40):
            h.tick()
            if n_close is None and 'CLOSING A1.1.1 stand (the line of fire is clear)' in h.log():
                n_close = i
        expect(n_close is not None and 14 <= n_close <= 18, 'F3: the lane closes 3 s after the target is lost (look %r)' % n_close)

        # F4: an enemy near (15 m from the stander's models, or reaching it within 3 s) closes the lane for the line of fire, and none opens
        # before every enemy is further than 20 m (hysteresis); a routing enemy does not count
        def gap_z(gap):  # the enemy's z for a gap (centre distance less both halves = 20 + 20) of `gap` m
            return 40.0 + gap
        h = open_row_lane()
        h.u['enemy'].update(dict(x=0.0, z=gap_z(14.0), gx=0.0, gz=gap_z(14.0), routing=True))
        h.run(24)
        expect(h.lane('stand') > 0 and 'an enemy is near' not in h.log(), 'F4: a routing enemy does not count: ' + h.log()[-600:])
        h.u['enemy']['routing'] = False
        h.run(30)
        expect(h.lane('stand') == 0 and 'CLOSING A1.1.1 stand (an enemy is near)' in h.log() and 'FIRE CLOSE A1.1.1 stand (an enemy is near)' in h.log(), 'F4: an enemy 14 m away closes it: ' + h.log()[-900:])
        opens = h.log().count('FIRE OPEN')
        h.u['enemy'].update(dict(x=0.0, z=gap_z(17.0), gx=0.0, gz=gap_z(17.0)))
        h.run(40)
        expect(h.lane('stand') == 0 and h.log().count('FIRE OPEN') == opens, 'F4: an enemy 17 m away (between 15 and 20): it stays closed: ' + h.log()[-600:])
        h.u['enemy'].update(dict(x=0.0, z=gap_z(24.0), gx=0.0, gz=gap_z(24.0)))
        h.run(40)
        expect(h.lane('stand') > 0 and h.log().count('FIRE OPEN') == opens + 1, 'F4: and opens again once the enemy is 24 m away: ' + h.log()[-600:])
        # no lane opens in the first place while an enemy is near
        h = Fire(foes=())
        h.row(-19.5, 19.5)
        h.u['stand']['men'] = 62
        h.u['enemy'].update(dict(x=0.0, z=gap_z(14.0), gx=0.0, gz=gap_z(14.0)))
        h.u['guns']['target'] = 103
        h.run(60)
        expect(h.lane('stand') == 0 and 'FIRE OPEN' not in h.log() and 'ERROR' not in h.log(), 'F4: no lane opens while an enemy is near: ' + h.log()[-600:])
        # an enemy that closes in at 8 m/s (3 measurements first) closes the lane while it is still more than 15 m away
        h = open_row_lane()
        z, closed_gap = gap_z(60.0), None
        h.u['enemy'].update(dict(x=0.0, z=z, gx=0.0, gz=gap_z(0.0), moving=True))
        for i in range(60):
            z -= 1.6
            h.u['enemy']['z'] = z
            h.tick()
            if closed_gap is None and 'CLOSING A1.1.1 stand (an enemy is near)' in h.log():
                closed_gap = z - 40.0
        expect(closed_gap is not None and closed_gap > 16.0, 'F4: an enemy reaching it within 3 s closes the lane before it is near: gap %r m' % (closed_gap,))

        # F5: one candidate test for serve and the lane for the line of fire
        expect('function FIRE.candidate(s, w)' in SCRIPT and 'local along = FIRE.candidate(s, w)' in SCRIPT and 'if FIRE.candidate(s, w) or (s.late and s.late[w] and w.walks and passing(w, s)) then return w end' in SCRIPT, 'F5: serve and FIRE.walker_needs use one test')
        # (and the lane for the line of fire gives way to a walker once: one OPEN, one CLOSE)
        h = open_row_lane()
        h.u['walk'].update(dict(x=0.0, z=-40.0, gx=0.0, gz=80.0, moving=True))
        for _ in range(80):
            h.u['walk']['z'] += 1.0
            h.tick()
        # (085f: the walker is through at 90 % and planned for no more (quick_gone): the lane for the line of fire may open again right behind it, after the walker's own CLOSE)
        expect(h.log().count('FIRE OPEN') in (1, 2) and h.log().count('FIRE CLOSE A1.1.1 stand (a walker needs it)') == 1 and h.log().count('OPEN  A1.1.1 stand makes way for A1.1.2 walk') == 1
               and (h.log().count('FIRE OPEN') == 1 or h.log().rindex('FIRE OPEN') > h.log().index('CLOSE A1.1.1 stand (nobody')), 'F5: one open, one close for a walker: ' + h.log()[-1200:])

        # D-b (review 8): a unit that has just stopped, with a friendly walker already coming for it, stands after 1 s (not 4 s)
        def quick_stand(walker_z, jump=None, model_jump=None, script=REAL_FIRE, run=30, pre=0):
            h = Fire(script=script)
            h.row(-19.5, 19.5)
            h.u['stand']['men'] = 62
            h.run(pre)  # (the unit stands still for `pre` looks first; the walker is far away)
            h.u['walk'].update(dict(x=0.0, z=walker_z, gx=0.0, gz=80.0, moving=True))
            first = None
            for i in range(1, run):
                if jump == i:
                    h.u['stand']['jx'] = 12.0
                elif jump is not None and i == jump + 1:
                    h.u['stand']['jx'] = 0.0
                if model_jump == i:
                    lst = struct.unpack('<Q', h.mem.read(h.u['stand']['at'] + 0xa8, 8))[0]
                    m0 = struct.unpack('<Q', h.mem.read(lst, 8))[0]
                    h.mem.write(m0 + 0x88, struct.pack('<3f', -19.5, 0.0, 12.0))
                    h.u['stand'].update(dict(z=12.0, gz=12.0))  # (a teleport moves the unit's position too)
                h.u['walk']['z'] += 1.0
                h.tick()
                if first is None and h.lane('stand') > 0:
                    first = i
            return h, first
        h, first = quick_stand(-24.0)
        expect(first is not None and first <= 14 and 'A1.1.1 stand stands after 1.0 s (not 4.0): A1.1.2 walk comes for it' in h.log(), 'D-b: stands after 1 s: lanes at look %r: %s' % (first, h.log()[-900:]))
        h, first = quick_stand(-300.0, run=22)
        expect(first is None and 'stands after' not in h.log(), 'D-b: no walker coming: it still takes 4 s')
        h, first = quick_stand(-24.0, jump=2)
        fast = [l for l in h.log().splitlines() if 'stands after 1.0 s' in l]
        expect(first is None or first >= 12, 'D-b: its position jumped (a watch of 2 s): not within the watch: lanes at look %r %r' % (first, fast[:1]))
        h, first = quick_stand(-24.0, model_jump=1, pre=8)
        expect(first is None or first >= 20, 'D-b: its models jumped (a teleport): the full 4 s from there: lanes at look %r' % (first,))
        h, first = quick_stand(-24.0, script=SCRIPT)  # (the older checks' script: STILL_FAST 4.0 = the quick stand is off)
        expect(first is None or first >= 20, 'D-b: STILL_FAST 4.0 switches it off: lanes at look %r' % (first,))

        # D-c (review 9): a further walker through the open lanes gets columns of its own once the first walker's columns are over
        def walker_fields(h, name):
            at = h.entry(name)['at']
            q, ox, oz, ax, az, bx, bz, t0, t1, c, kl, kh, sl, sh = struct.unpack('<10f2i2f', h.mem.read(at + lanes.E_WQ, 0x38))
            return dict(q=q, ox=ox, oz=oz, t0=t0, t1=t1, c=c)
        h = Fire(script=REAL_FIRE)
        h.row(-19.5, 19.5)
        h.u['stand']['men'] = 62
        h.run(25)
        h.u['walk'].update(dict(x=0.0, z=-40.0, gx=0.0, gz=80.0, moving=True))
        h.u['walk2'].update(dict(x=0.0, z=-52.0, gx=0.0, gz=80.0, moving=True))
        late_fields = None
        for i in range(120):
            for name in ('walk', 'walk2'):
                h.u[name]['z'] += 1.0
            h.tick()
            if late_fields is None and 'COLUMNS LATE A1.1.1 stand for A1.1.4 walk2' in h.log():
                late_fields = (walker_fields(h, 'walk2'), walker_fields(h, 'walk'))
        text = h.log()
        expect('further walker A1.1.4 walk2 through A1.1.1 stand: not a big walker: it goes through the lanes as they are' in text and 'COLUMNS A1.1.1 stand: the columns of A1.1.2 walk are over (it is through), A1.1.4 walk2 is next' in text, 'D-c: the first walker is through: ' + text[-1500:])
        expect(late_fields and late_fields[0]['q'] > 0 and late_fields[0]['c'] > 0 and late_fields[1]['q'] == 0 and text.count('COLUMNS LATE A1.1.1 stand for A1.1.4 walk2') == 1,
               'D-c: the further walker gets columns of its own, the first one has none any more: %r' % (late_fields,))
        expect('COLUMNS LATE A1.1.1 stand for A1.1.2 walk' not in text and text.count('the columns of') == 1, 'D-c: no ping-pong back to the first walker: ' + text[-1500:])
        # (not for lanes per file: a vector walker keeps its formation, there are no columns to pass on)
        expect('if not lanes or lanes.vector or s.fire or s.columns_wait or s.half_closed then return end' in SCRIPT, 'D-c: not for vector lanes, the lane for the line of fire')

        # (fix 7) the lane count of the B loop is kept for TUNE.B_CACHE_LOOKS looks per walker order: fewer reads of the standing unit's models
        def b_runs(script):  # how often the B loop really ran (a counter patched into the script)
            counted = script.replace('for nn = n + 1, 3 do', 'BRUNS = (BRUNS or 0) + 1; for nn = n + 1, 3 do', 1)
            h, got = column_walk('mc', 10, 3, 3.8, 1.2, riders=True, script=counted, looks=32)
            return h, counted != script and (h.b['lua'].eval('BRUNS') or 0)
        h_c, runs_cache = b_runs(FIRE_SCRIPT)
        nocache = FIRE_SCRIPT.replace('B_CACHE_LOOKS = 5,', 'B_CACHE_LOOKS = 0,', 1)
        h_n, runs_plain = b_runs(nocache)
        expect(nocache != FIRE_SCRIPT and h_c.entry('stand')['n'] == 2 and h_n.entry('stand')['n'] == 2 and 0 < runs_cache < runs_plain,
               '085d review 7 (fix 7): the cached lane count: same n (2), the B loop runs less often: %r (cache) vs %r (none)' % (runs_cache, runs_plain))

        # (fix 5, 085d review) the walker whose columns ended (stopped) is entered in s.cols_done: if it walks on, it does not get columns again
        h = Fire(script=REAL_FIRE)
        h.row(-19.5, 19.5)
        h.u['stand']['men'] = 62
        h.run(25)
        h.u['walk'].update(dict(x=0.0, z=-40.0, gx=0.0, gz=80.0, moving=True))
        h.u['walk2'].update(dict(x=0.0, z=-56.0, gx=0.0, gz=80.0, moving=True))
        for i in range(140):
            h.u['walk2']['z'] += 1.0
            if not (30 <= i < 50):
                h.u['walk']['z'] += 1.0
            h.u["walk"]["moving"] = not (30 <= i < 50)
            h.tick()
        text = h.log()
        expect('COLUMNS LATE A1.1.1 stand for A1.1.4 walk2' in text and 'COLUMNS LATE A1.1.1 stand for A1.1.2 walk' not in text.split('COLUMNS LATE A1.1.1 stand for A1.1.4 walk2')[-1],
               'D-c (fix 5): the walker that stopped is not given columns again after the next one: ' + chr(10).join(l[:230] for l in text.splitlines() if 'COLUMNS' in l or 'OPEN ' in l or 'further' in l))
        # (fix 10) fast_stands clears why_not (the reason it did not stand) when it lets the unit stand
        hook = REAL_FIRE.replace("local fok, fproblem = pcall(FIRE.fast_stands)\n", "local fok, fproblem = pcall(FIRE.fast_stands)\n\tfor _, e in ipairs(state.units) do if e.fast_stand and WHYNOT == nil then WHYNOT = e.why_not or 'none' end end\n", 1)
        expect(hook != REAL_FIRE, 'D-b (fix 10): the test hook is in the script')
        h, first = quick_stand(-24.0, script=hook)
        expect('stands after 1.0 s' in h.log() and h.b['lua'].eval('WHYNOT') == 'none', 'D-b (fix 10): why_not is cleared when the quick stand sets it: %r' % (h.b['lua'].eval('WHYNOT'),))

        # (fix 9) a late walker (already inside when the stander began to stand) that still walks through counts as one that needs the unit: no lane for the line of fire
        h = Fire(script=REAL_FIRE)
        h.row(-14.5, 14.5, ranks=12)
        h.u['stand']['men'] = 12 * 23
        h.u['walk'].update(dict(x=0.0, z=-4.6, gx=0.0, gz=80.0, moving=True))
        h.u['guns']['target'] = 103
        h.run(60)
        text = h.log()
        expect(' m inside A1.1.1 stand - left to the game' in text and 'FIRE OPEN A1.1.1 stand' not in text and h.lane('stand') == 0,
               'fix 9: a late walker still walks through: no lane for the line of fire: ' + text[-1200:])

        # (fix 4) another gun on the same target obeys the replan rate limit too (two switches in 6 looks: at most one re-forming)
        two_guns = REAL_FIRE.replace("['guns'] = true, ", "['guns'] = true, ['walk3'] = true, ", 1)
        expect(two_guns != REAL_FIRE, 'fix 4: the second gun is in the script')
        h = Fire(script=two_guns)
        h.row(-19.5, 19.5)
        h.u['stand']['men'] = 62
        h.u['walk3'].update(dict(x=-30.0, z=-60.0, gx=-30.0, gz=-60.0, ammo=10, range=150.0, moving=False))
        h.u['guns']['target'] = 103
        h.run(40)
        expect('FIRE OPEN A1.1.1 stand' in h.log(), 'fix 4: the lane is open: ' + h.log()[-900:])
        h.u['guns']['target'] = None
        h.u['walk3']['target'] = 103
        h.run(3)
        h.u['walk3']['target'] = None
        h.u['guns']['target'] = 103
        h.run(3)
        expect(h.log().count('FIRE REPLAN') <= 1, 'fix 4: another gun: at most ONE replan in 6 looks: %d: %s' % (h.log().count('FIRE REPLAN'), h.log()[-1200:]))

        # (fix 6) jobs nobody took are dropped: the next gap update sends ALL models (and E_DIRTY), not only the changed ones
        h = open_row_lane(run=1)
        for _ in range(40):
            if 'FIRE OPEN' in h.log():
                break
            h.tick()
        h.run(20)  # (jobs nobody took within 3 s: dropped)
        expect(h.entry('stand')['pending'] == 0, 'fix 6: the jobs were dropped: %r' % (h.entry('stand'),))
        set_dirty(h, 0)
        h.u['enemy'].update(dict(x=33.0))
        h.run(14)
        got = _re.search(r'gap updated.*\| (\d+) models sent', h.log())
        expect(got and int(got.group(1)) >= 60 and h.entry('stand')['dirty'] == 1, 'fix 6: after dropped jobs a gap update sends every model + E_DIRTY: %s' % h.log()[-700:])


        # P1: serve's time by sub-phase in the PERF line (cand / open / plans / sends / columns / near / inside / checks / widen / rest), and the digest reads them
        h = Fire()
        h.u['walk'].update(dict(x=0.0, z=-40.0, gx=0.0, gz=80.0, moving=True))
        for _ in range(60):
            h.u['walk']['z'] += 1.0
            h.tick()
        perf = [l for l in h.log().splitlines() if ' PERF ' in l and 'by phase' in l]
        names = ('cand', 'open', 'plans', 'sends', 'columns', 'near', 'inside', 'checks', 'widen', 'rest')
        expect(perf and ' serve ' in perf[-1] and all((' %s ' % n) in ' ' + perf[-1].split(' serve ')[1].split('), the line of fire')[0].replace('(', ' ') for n in names), 'P1: the PERF line names the sub-phases of serve: %r' % (perf[-1:],))
        import log_digest
        got = log_digest.perf_windows([(0, perf[-1].split(' ', 1)[1])])[0]
        expect(got and all(n in got[0]['phases'] for n in names + ('serve', 'look')), 'P1: the digest shows them: %r' % (got and got[0]['phases'],))
        vals = [float(v) for v in _re.findall(r'(?:cand|open|plans|sends|columns|near|inside|checks|widen|rest) ([\d.]+)', perf[-1].split(' serve ')[1].split('), the line of fire')[0])]
        serve_ms = float(_re.search(r' serve ([\d.]+) ', perf[-1]).group(1))
        expect(len(vals) == 10 and sum(vals) <= serve_ms + 0.06, 'P1: the sub-phases are exclusive, their sum is not more than serve: %r vs %.2f' % (vals, serve_ms))
        # FIRE.places of a walker is built once per look however often it is asked (cache like near_places)
        places_script = REAL_FIRE.replace('function FIRE.places(w)\n', 'function FIRE.places(w)\n\tPCALLS = (PCALLS or 0) + 1\n', 1).replace('w.fplaces, w.fplaces_tick = list, state.ticks\n', 'w.fplaces, w.fplaces_tick = list, state.ticks\n\t\t\tPBUILDS = (PBUILDS or 0) + 1\n', 1)
        expect(places_script.count('PBUILDS = ') == 1 and places_script.count('PCALLS = ') == 1, 'P1: the FIRE.places counters are in the script (the cache exists)')
        h, got = column_walk('mc', 10, 3, 3.8, 1.2, riders=True, script=places_script)
        calls, builds = h.b['lua'].eval('PCALLS'), h.b['lua'].eval('PBUILDS')
        expect(h.lane('stand') > 0 and 0 < builds < calls, 'P1: FIRE.places is built once per look: %d builds for %d calls' % (builds, calls))
    # --- 085e: narrowed big walkers (A2 / B1), the ghost list (cave 5), the bulk read of the models array (D1), G2 -------------------------------
    NARROW_SCRIPT = REAL_FIRE.replace('local function update()\n', 'function TEST_ENTRY(label) for _, e in ipairs(state.units) do if e.label == label then return e end end end\n'
                                      'function TEST_GIVE_UP(r) give_up(r) end\nfunction TEST_STATE() return state end\nfunction TEST_NARROW() return NARROW end\nfunction TEST_TUNE() return TUNE end\nlocal function update()\n', 1)

    def reform_bodies(h, spec, width, nf=None):
        """085g: the game re-forms the column to the width it was given: files = min(files at the start, int((width + 0.4 space) / space)) (the old (files - 1) x
        spacing gives ONE file, as in run 55; files x spacing + pad gives the files asked for); the bodies (mounts with their riders in h.offs) keep their order"""
        space, files0 = spec['space'], spec['files']
        nf = nf or max(1, min(files0, int((width + spec.get('bias', 0.4) * space) / space + 1e-6)))
        for k in range(0, len(h.offs), 2):
            j = k // 2
            off = ((j % nf - (nf - 1) / 2) * space, -(j // nf) * space)
            h.offs[k] = (h.offs[k][0], off)
            h.offs[k + 1] = (h.offs[k + 1][0], off)
        if spec.get('stray'):  # (review 4: one body far to the side of the column)
            h.offs[0] = (h.offs[0][0], (spec['stray'], h.offs[0][1][1]))
            h.offs[1] = (h.offs[1][0], (spec['stray'], h.offs[1][1][1]))
        spec['nf'] = nf

    def uc_mock(h, clamp=None, delay=0, fail=False, fast_lag=0):
        """create_unitcontroller for the lanes script (the scenario's call): records goto_location_angle_width; the order shows in the unit's
        ordered_position / width / bearing and is_moving_fast `delay` looks later (width clamped up to `clamp`); take_control / release_control are
        recorded (the mod must never call them)"""
        lua = h.b['lua']
        U = dict(calls=[], pending=[], clamp=clamp, delay=delay, fail=fail, controls=[], look=[0], fast_lag=fast_lag, fast_pending=[], later=[])
        by_uid = {u['uid']: u for u in h.u.values()}

        def create(unit):
            u = by_uid[unit.unique_ui_id(unit)]
            uc = lua.table()

            def goto(self, pos, bearing, width, run):
                if U['fail']:
                    raise RuntimeError('no unit controller')
                rec = dict(name=u['name'], x=pos.get_x(), z=pos.get_z(), bearing=bearing, width=width, run=run, look=U['look'][0])
                U['calls'].append(rec)
                U['pending'].append([U['delay'], u, rec])
            uc['goto_location_angle_width'] = goto
            uc['take_control'] = lambda self: U['controls'].append('take')
            uc['release_control'] = lambda self: U['controls'].append('release')
            return uc
        lua.globals().create_unitcontroller = lua.eval('function(f) return function(...) return f(...) end end')(create)  # (a real Lua function: the script tests type())

        def apply():
            U['look'][0] += 1
            for item in list(U['pending']):
                if item[0] <= 0:
                    U['pending'].remove(item)
                    _, u, rec = item
                    width = max(rec['width'], U['clamp']) if U['clamp'] else rec['width']
                    if U.get('read_scale'):  # (review 1: the game shows only part of the width it was sent)
                        width = rec['width'] * U['read_scale']
                    u.update(gx=rec['x'], gz=rec['z'], bearing=rec['bearing'], width=width)
                    if U.get('reform') and u['name'] == 'walk':
                        sp = U['reform']
                        if U.get('read_scale'):
                            width = rec['width']  # (the bodies form the column of the width sent, only the read-back is narrower)
                        if sp.get('stage') and not U.get('staged'):  # (review 3: first more files, `stage[1]` looks later the files of the width)
                            U['staged'] = True
                            reform_bodies(h, sp, width, sp['stage'][0])
                            U['later'].append([sp['stage'][1], width])
                        else:
                            reform_bodies(h, sp, width)
                    U['fast_pending'].append([U['fast_lag'], u, bool(rec['run'])])
                else:
                    item[0] -= 1
            for item in list(U['later']):
                item[0] -= 1
                if item[0] <= 0:
                    U['later'].remove(item)
                    reform_bodies(h, U['reform'], item[1])
            for item in list(U['fast_pending']):  # (is_moving_fast follows the order a few looks later when fast_lag is set)
                if item[0] <= 0:
                    U['fast_pending'].remove(item)
                    item[1]['fast'] = item[2]
                else:
                    item[0] -= 1
        U['apply'] = apply
        return U

    def narrow_walk(files=10, space=3.8, run=False, clamp=None, delay=0, looks=60, uc=True, fail=False, script=None, each=None, before_extra=None, fast_lag=0, reform=True, bias=0.4, stage=None, stray=None, read_scale=None, radius=1.2, kind='mc', cav_rule=False, **kw):
        """the demigryph walker of the columns tests (10 files x 3 ranks, 3.8 m apart: the block test fails, no lanes per file) through 29 m of
        halberdiers, with a fake unit controller; the walker's run state is `run`. Returns the battle (h.U: the controller's record)"""
        holder = {}

        def before(h):
            if uc:
                holder['U'] = uc_mock(h, clamp, delay, fail, fast_lag)
                if reform:  # (085g: the walker's bodies re-form to the width of every order the game takes)
                    holder['U']['reform'] = dict(space=space, files=files, bias=bias, stage=stage, stray=stray)
                holder['U']['read_scale'] = read_scale
            h.u['walk']['fast'] = run
            if before_extra:
                before_extra(h)

        def each2(h, i):
            if 'U' in holder:
                holder['U']['apply']()
            if each:
                each(h, i, holder.get('U'))
        script = script or NARROW_SCRIPT
        if not cav_rule:  # 085j: the cavalry rule (4 files for cav / mc walkers of more than 20 bodies) is lifted for the tests of the older narrowing; the h085j tests switch it on
            lifted = script.replace('NARROW_CAV_MIN_MODELS = 20,', 'NARROW_CAV_MIN_MODELS = 1000000,', 1)
            assert lifted != script, '085j: the cavalry-rule lift is not in the script'
            script = lifted
        h, got = column_walk(kind, files, 3, space, radius, riders=True, script=script, looks=looks, before=before, each=each2, **kw)
        h.U = holder.get('U')
        h.got = got
        return h

    def dbg(h, *words):  # NARROW_DEBUG=1: the log lines with these words
        if os.environ.get('NARROW_DEBUG'):
            print(chr(10).join(l[:420] for l in h.log().splitlines() if any(w in l for w in (words or ('NARROW', 'RESTORED', 'OPEN ', 'CLOSE')))))

    def te(h, label='A1.1.2 walk'):  # the script's entry of a unit
        return h.b['lua'].eval('TEST_ENTRY')(label)
    # --- 085f: closing at 90 % through + sequential walkers (F1), the narrowed column's extended goal (F2), guard mode without room (F3), line of fire for infantry only (F4) ---
    def f085_debug(h, *words):  # F085_DEBUG=1: the log lines with these words
        if os.environ.get('F085_DEBUG'):
            print(chr(10).join(l[:300] for l in h.log().splitlines() if any(w in l for w in (words or ('OPEN', 'CLOS', 'CLOSE', 'again', 'LATE', 'NARROW', 'RESTORED')))))

    def f085_walkers(specs, script=None, gaps=None, **kw):
        """standing halberdiers ('stand') and infantry walkers (name, files, ranks, space, radius, z0) through them; returns the battle. h.f085 = { name: [bodies...] };
        h.f085_adv(n, stuck={name: {body index: z}}) advances every walker 1 m per look (a body in `stuck` stays where it is)"""
        names = [s[0] for s in specs]
        script = (script or REAL_FIRE).replace('local KIND = { ', "local KIND = { %s" % ''.join("['%s'] = 'inf', " % n for n in names), 1)
        h = Fire(script=script, **kw)
        h.row(-14.5, 14.5, step=1.3, ranks=2)
        h.u['stand']['men'] = 2 * 23
        h.u['stand']['width'] = 29.0
        h.f085 = {}
        for name, files, ranks, space, radius, z0 in specs:
            offs, models = [], []
            for i in range(files * ranks):
                off = ((i % files - (files - 1) / 2) * space, -(i // files) * space)
                body = h.model(off[0], z0 + off[1], radius)
                offs.append((body, off))
                models.append(body)
            h.set_models(name, models)
            h.f085[name] = offs
            h.u[name].update(dict(x=0.0, z=z0, gx=0.0, gz=120.0, moving=True, men=files * ranks, width=(files - 1) * space + 2 * radius))

        def adv(n, stuck=None):
            for _ in range(n):
                for name, offs in h.f085.items():
                    if name in getattr(h, 'f085_frozen', ()):
                        continue
                    h.u[name]['z'] += 1.0
                    for i, (m, off) in enumerate(offs):
                        z = (stuck or {}).get(name, {}).get(i, h.u[name]['z'] + off[1])
                        h.mem.write(m + 0x88, struct.pack('<3f', off[0], 0.0, z))
                h.tick()
        h.f085_adv = adv
        return h

    def f085_far(h, gz):  # the walker's order ends at gz; the enemy is moved far away (the mock's one stands 30 m from the way)
        h.u['walk'].update(dict(gz=gz))
        h.u['enemy'].update(dict(x=500.0, z=500.0, gx=500.0, gz=500.0))
    def f085_log_since(h, key):
        return [l for l in h.log().splitlines() if key in l]
    if on('f085'):
        # F1: a walker (16 x 5 = 80 bodies) with 3 stragglers inside the standing unit: 96 % through -> CLOSING after HOLD_TICKS; the last step waits at most
        # CLOSE_STRAGGLER_S (3 s) for them, then the unit closes anyway; 9 stragglers (89 %): the lanes stay open
        for stuck_n, closes in ((3, True), (9, False)):
            h = f085_walkers([('walk', 16, 5, 1.2, 0.4, -40.0)])
            stuck = {'walk': {i: -0.6 for i in range(stuck_n)}}  # (the first rank's bodies stay in the middle of the standing unit)
            h.f085_adv(60, stuck=stuck)
            f085_debug(h)
            text = h.log()
            if closes:
                expect(h.lane('stand') > 0 and 'CLOSING A1.1.1 stand (nobody is passing any more, 96 % of A1.1.2 walk through)' in text,
                       '085f F1: 96 %% through with %d stragglers inside: the closing starts: %s' % (stuck_n, text[-1500:]))
                t_closing = text.count('CLOSING A1.1.1 stand')
                h.f085_adv(30, stuck=stuck)
                text = h.log()
                expect(h.lane('stand') == 0 and 'CLOSE A1.1.1 stand (' in text and re.search(r'CLOSE WAIT A1.1.1 stand: 3 walker models were still behind the back rank, the closing waited (3\.[0-3]) s', text) is not None,
                       '085f F1: the last step waited 3 s for the stragglers, then closed anyway: ' + text[-1500:])
                expect(text.count('CLOSING A1.1.1 stand') == t_closing == 1 and 'opens again' not in text and text.count('LATE') == text.count('COLUMNS LATE'), '085f F1: one closing, no reopen, no LATE after the close: ' + text[-1200:])
            else:
                expect(h.lane('stand') > 0 and 'CLOSING A1.1.1 stand' not in text, '085f F1: 89 %% through (%d stragglers): the lanes stay open: %s' % (stuck_n, text[-1200:]))
        # ... without stragglers the wait does not happen
        h = f085_walkers([('walk', 16, 5, 1.2, 0.4, -40.0)])
        h.f085_adv(80)
        expect(h.lane('stand') == 0 and 'CLOSE WAIT' not in h.log() and 'CLOSING A1.1.1 stand (nobody is passing any more, 100 % of A1.1.2 walk through)' in h.log(), '085f F1: all through: closed, no wait: ' + h.log()[-1200:])

        # SEQUENTIAL WALKERS: a second unit 3 s (15 m) behind the first: ONE opening, no closing before the 2nd is through, no reopen, ONE close
        h = f085_walkers([('walk', 16, 5, 1.2, 0.4, -40.0), ('walk2', 16, 5, 1.2, 0.4, -55.0)])
        h.f085_adv(95)
        f085_debug(h)
        text = h.log()
        expect(text.count('OPEN  A1.1.1 stand') == 1 and 'opens again' not in text and text.count('CLOSING A1.1.1 stand') == 1 and text.count('CLOSE A1.1.1 stand') == 1 and h.lane('stand') == 0,
               '085f sequential: a 3 s gap: one opening, one closing, one close, no flip-flop: ' + text[-2000:])
        expect('% of A1.1.4 walk2 through' in text.split('CLOSING A1.1.1 stand')[1].split('\n')[0] and 'A1.1.2 walk' in text.split('CLOSING A1.1.1 stand')[1].split('\n')[0],
               '085f sequential: the closing began when BOTH were through: ' + text.split('CLOSING A1.1.1 stand')[1][:300])
        # a 12 s gap (60 m): still inside the time the closing would take (hold + steps + straggler wait + follow), the lanes stay open for it: one opening, one close
        h = f085_walkers([('walk', 16, 5, 1.2, 0.4, -40.0), ('walk2', 16, 5, 1.2, 0.4, -100.0)])
        h.f085_adv(150)
        f085_debug(h)
        text = h.log()
        expect(text.count('OPEN  A1.1.1 stand') == 1 and text.count('CLOSE A1.1.1 stand') == 1 and 'opens again' not in text and text.count('LATE') == text.count('COLUMNS LATE') and h.lane('stand') == 0,
               '085f sequential: a 12 s gap: the lanes wait for the second unit: ' + text[-2000:])
        # a 20 s gap (100 m): the lanes close after the first (90 % through), open afresh for the second, no "opens again" line, no LATE
        h = f085_walkers([('walk', 16, 5, 1.2, 0.4, -40.0), ('walk2', 16, 5, 1.2, 0.4, -140.0)])
        h.f085_adv(200)
        f085_debug(h)
        text = h.log()
        expect(text.count('OPEN  A1.1.1 stand') == 2 and text.count('CLOSE A1.1.1 stand') == 2 and 'opens again' not in text and text.count('LATE') == text.count('COLUMNS LATE') and h.lane('stand') == 0,
               '085f sequential: a 20 s gap: close after the first, a fresh opening for the second: ' + text[-2000:])

        # F2: the run-54 geometry: 60 bodies (20 files x 3 ranks, 3.8 m apart), the order ends 90 m ahead, the back rank 40 m ahead: narrowed with an extended goal,
        # restored at 90 % through with the original goal
        h = narrow_walk(files=20, looks=100, step=0.5, before_extra=lambda h: f085_far(h, 30.0))
        dbg(h)
        text, U = h.log(), h.U
        expect(text.count('NARROWED A1.1.2 walk from ') == 1 and 'its goal moved ' in text and len(U['calls']) >= 1, '085f F2: narrowed with an extended goal: ' + text[-1500:])
        expect(abs(U['calls'][0]['z'] - 30.0 - float(re.search(r'its goal moved ([0-9.]+) m on', text).group(1))) < 0.06 and U['calls'][0]['z'] > 55.0 and U['calls'][0]['x'] == 0.0,
               '085f F2: the narrowed order goes on along the heading: %r' % (U['calls'][0],))
        h = narrow_walk(files=20, looks=140, step=1.0, before_extra=lambda h: f085_far(h, 30.0))
        dbg(h)
        text, U = h.log(), h.U
        expect(len(U['calls']) == 2 and 'RESTORED A1.1.2 walk' in text and '100 % of its bodies are past the last standing unit' in text and U['calls'][1]['z'] == 30.0 and U['calls'][1]['look'] > U['calls'][0]['look'] + 20,
               '085f F2 (085h: a big walker: all its bodies): restored at 100 %% with the original goal: %r %s' % (U['calls'], text[-1500:]))
        # the wide order must clear the standing unit: 20 files x 3 ranks, the goal 10 m beyond the back rank only is still refused with the new numbers
        h = narrow_walk(files=20, looks=70, step=0.5, before_extra=lambda h: f085_far(h, -20.0))
        expect(h.U['calls'] == [] and 'narrowing: its order ends ' in h.log() and ' files (10.0 m deep) need ' in h.log(), '085f F2: a goal inside the standing unit: refused, with the wide depth: ' + h.log()[-900:])
        # a goal far enough for the narrowed column: no extension
        h = narrow_walk(files=20, looks=70, step=0.5, before_extra=lambda h: f085_far(h, 150.0))
        expect(len(h.U['calls']) >= 1 and 'its goal moved' not in h.log() and abs(h.U['calls'][0]['z'] - 150.0) < 0.01, '085f F2: the goal is far enough: no extension')

        # F3: guard mode, halberdiers, no room inside the outline: the normal lanes open (the 085c / 085e tests above); and the guard limit stays dropped while it is open
        h, got = column_walk('inf', 16, 5, 1.2, 0.4, guard=True, stand_step=1.3, script=NARROW_SCRIPT)
        text = h.log()
        e = h.entry('stand')
        expect(h.lane('stand') > 0 and e['bias'] == 0 and text.count(' - normal lanes instead') == 1 and te(h, 'A1.1.1 stand')['guard_dropped'] is not None and 'GUARD MODE: ONE lane' not in text,
               '085f F3: guard + no room: two normal lanes, the reason kept in s.guard_dropped: %r %s' % (e, text[-1200:]))
        for _ in range(60):
            h.u['walk']['z'] += 1.0
            for m, off in h.offs:
                h.mem.write(m + 0x88, struct.pack('<3f', off[0], 0.0, h.u['walk']['z'] + off[1]))
            h.tick()
        expect(h.lane('stand') == 0 and te(h, 'A1.1.1 stand')['guard_dropped'] is None, '085f F3: the dropped guard limit is cleared when the lanes close: ' + h.log()[-600:])

        # F4: only infantry (and monstrous infantry) steps aside for a line of fire
        for kind, opens in (('cav', False), ('inf', True), ('mi', True), ('mc', False)):
            fire = Fire(script=REAL_FIRE.replace('local KIND = { ', "local KIND = { ['stand'] = '%s', " % kind, 1))
            fire.u['guns']['target'] = 103
            fire.run(40)
            text = fire.log()
            if opens:
                expect(fire.lane('stand') > 0 and 'FIRE OPEN A1.1.1 stand' in text and 'is not infantry' not in text, '085f F4: a %s unit clears the line of fire: %s' % (kind, text[-900:]))
            else:
                expect(fire.lane('stand') == 0 and 'FIRE OPEN' not in text and text.count('FIRE: A1.1.1 stand is not infantry: no lane for the line of fire of A1.1.5 guns') == 1,
                       '085f F4: a %s unit makes no lane, said once: %s' % (kind, text[-900:]))

        # --- 085f review fixes ---
        # (1) a walker that stops with stragglers inside while the unit closes: its places are handed out anew with the unit's (parked rule), both re-form
        h = f085_walkers([('walk', 16, 5, 1.2, 0.4, -40.0)])
        stuck = {'walk': {i: -0.6 for i in range(3)}}
        for _ in range(60):
            h.f085_adv(1, stuck=stuck)
            if 'CLOSING A1.1.1 stand' in h.log():
                h.u['walk']['moving'] = False
                h.f085_frozen = {'walk'}  # (it stops where it is)
                break
        h.f085_adv(30, stuck=stuck)
        expect('CLOSING A1.1.1 stand' in h.log() and 'A1.1.2 walk parked inside A1.1.1 stand: its places are handed out anew too' in h.log() and h.lane('stand') == 0,
               '085f review 1: a stopped walker with stragglers inside is re-formed with the unit: ' + h.log()[-1200:])
        # (2) a stale member of the group (a follower that came near once and was re-ordered away) does not keep the 90 % rule off
        h = f085_walkers([('walk', 16, 5, 1.2, 0.4, -40.0), ('walk2', 16, 5, 1.2, 0.4, -55.0)])
        h.f085_adv(8)
        h.f085_frozen = {'walk2'}
        h.u['walk2'].update(dict(moving=False, gx=h.u['walk2']['x'], gz=h.u['walk2']['z']))
        h.f085_adv(70)
        text = h.log()
        expect('CLOSING A1.1.1 stand (nobody is passing any more, 100 % of A1.1.2 walk through)' in text and 'opens again' not in text,
               '085f review 2: the walker that stopped far away is no part of the group: ' + text[-1500:])
        # (4) a closing longer than the candidates' horizon: the second walker beyond it holds the lanes open (no "opens again while closing up")
        short = REAL_FIRE.replace('local LOOK_SECONDS = 15.0', 'local LOOK_SECONDS = 5.0', 1)
        expect(short != REAL_FIRE, '085f review 4: the horizon is in the script')
        h = f085_walkers([('walk', 16, 5, 1.2, 0.4, -40.0), ('walk2', 16, 5, 1.2, 0.4, -100.0)], script=short)
        h.f085_adv(160)
        text = h.log()
        expect('opens again' not in text and text.count('OPEN  A1.1.1 stand') == 1 and text.count('CLOSE A1.1.1 stand') == 1 and h.lane('stand') == 0,
               '085f review 4: a second walker beyond the horizon: one opening, no flip-flop: ' + text[-1500:])
        # (7) the lane for the line of fire does not open right behind a walker whose models are still inside the unit
        h = f085_walkers([('walk', 16, 5, 1.2, 0.4, -140.0)], script=REAL_FIRE)
        h.u['guns']['target'] = 103
        h.run(26)
        expect(h.lane('stand') > 0 and h.log().count('FIRE OPEN') == 1, '085f review 7: the lane for the line of fire is open first: ' + h.log()[-600:])
        h.u['walk'].update(dict(z=-40.0))
        stuck = {'walk': {i: -0.6 for i in range(3)}}
        for _ in range(120):
            h.f085_adv(1, stuck=stuck)
            if 'CLOSE A1.1.1 stand (nobody' in h.log():
                break
        h.f085_adv(6, stuck=stuck)  # (the stragglers are still inside, the walker's own order is quick_gone: before the fix the lane for the line of fire opened here)
        expect('CLOSE A1.1.1 stand (nobody' in h.log() and h.log().count('FIRE OPEN') == 1 and h.lane('stand') == 0,
               '085f review 7: no line-of-fire lane while walker models stand in the unit: ' + h.log()[-1200:])

        # (3) the extended goal must not lead the column into another friendly unit (a unit stands beyond the goal)
        def beyond(h):
            f085_far(h, 30.0)
            h.set_models('aside', [h.model(x, 45.0, 0.65) for x in (-6.0, 0.0, 6.0)])
            h.u['aside'].update(dict(x=0.0, z=45.0, gx=0.0, gz=45.0, men=3, width=14.0))
        h = narrow_walk(files=20, looks=70, step=0.5, before_extra=beyond)
        expect(h.U['calls'] == [] and 'a friendly unit stands beyond its goal: A1.1.3 aside' in h.log(), '085f review 3: a friendly unit beyond the extended goal: no narrowing: ' + h.log()[-900:])
        # (5) the game moves the extended goal by 2 m: still the mod's own order (not forgotten)
        def nudge(h, i, U):
            if U['pending'] and U['pending'][0][2]['z'] < 100.0 and not U.get('nudged'):
                U['pending'][0][2]['z'] += 2.0
                U['nudged'] = True
        h = narrow_walk(files=20, looks=140, step=1.0, delay=1, before_extra=lambda h: f085_far(h, 30.0), each=nudge)
        expect(h.U.get('nudged') and 'NARROW FORGOTTEN' not in h.log() and 'NARROWED A1.1.2 walk from 74.6' in h.log() and 'RESTORED A1.1.2 walk (width 74.6 m' in h.log() and len(h.U['calls']) == 2, '085f review 5: a moved extended goal is not another order: %r %r %s' % (h.U.get('nudged'), h.U['calls'], h.log()[-500:]))
        # (6) the room fails for 2 files: 3 files (wider) are not tried, 1 file is
        tried = NARROW_SCRIPT.replace('local rok = pcall(make_room, s, w, pl, nil, true)\n\t\t\tlocal fits', 'NF_TRIED = NF_TRIED or {}; NF_TRIED[#NF_TRIED + 1] = nf\n\t\t\tlocal rok = pcall(make_room, s, w, pl, nil, true)\n\t\t\tif nf >= 2 then rok = false end\n\t\t\tlocal fits', 1)
        expect(tried != NARROW_SCRIPT, '085f review 6: the hook is in the script')
        h = narrow_walk(files=20, looks=40, step=0.5, script=tried, before_extra=lambda h: f085_far(h, 150.0))
        got_nf = [int(x) for x in h.b['lua'].eval('NF_TRIED').values()] if h.b['lua'].eval('NF_TRIED') else []
        expect(got_nf and 2 in got_nf and 1 in got_nf and 3 not in got_nf, '085f review 6: after 2 files failed the room, 3 are skipped: %r' % (got_nf,))
    if on('h085'):
        # --- 085h: big walkers must never be stuck ------------------------------------------------------------------------------------------------
        def morph(frm, to, looks):
            """the bodies of the narrowed walker (2 files, 3.8 m apart at the end) move from frm(j) to to(j) across the way over `looks` looks, from the look after its first order"""
            def hook(h, i, U):
                if not U['calls']:
                    return
                prog = min(1.0, max(0.0, (i - U['calls'][0]['look'] - 1) / float(looks)))
                for k in range(0, len(h.offs), 2):
                    j = k // 2
                    x = frm(j) * (1 - prog) + to(j) * prog
                    h.offs[k] = (h.offs[k][0], (x, -(j // 2) * 3.8))
                    h.offs[k + 1] = (h.offs[k + 1][0], (x, -(j // 2) * 3.8))
            return hook
        final = lambda j: (j % 2 - 0.5) * 3.8

        def both(*hooks):
            def hook(h, i, U):
                for one in hooks:
                    one(h, i, U)
            return hook

        def small_once_narrowed(h, i, U):  # (the plan needs a big walker; the count after the narrowing sees a walker under BIG_RADIUS)
            if U['calls']:
                h.b['lua'].eval('TEST_TUNE')().BIG_RADIUS = 1.5
        # items 1, 5, 6: a disordered mass (run 56: 21 - 35 m across one second after the order) converging to its 2 files over 5 s: not counted while it re-forms
        # ('NOT SETTLED'), never given up, never restored wide
        mess = lambda j: ((j * 7) % 13 - 6) * 2.0
        h = narrow_walk(looks=60, step=0.5, reform=False, each=morph(mess, final, 25))
        dbg(h)
        text = h.log()
        expect('NARROW NOT SETTLED A1.1.2 walk' in text and 'GIVEN UP' not in text and 'RESTORED' not in text and 'NARROW WIDTH' not in text and len(h.U['calls']) == 1 and te(h)['narrow'] is not None,
               '085h items 1 / 6: a re-forming mass is not counted, not given up, not restored wide: %r %s' % (h.U['calls'], text[-1500:]))
        # ... a column that settles inside the window is counted once, at the end (items 5 / 6); a walker under BIG_RADIUS is not sent again for the file it did not have while it formed
        h = narrow_walk(looks=60, step=0.5, reform=False, each=both(morph(lambda j: 0.0, final, 12), small_once_narrowed))
        dbg(h)
        text = h.log()
        expect('NARROW FILES' not in text and text.count('NARROW WIDTH A1.1.2 walk') == 1 and 'files wanted 2 got 2' in text and 'NOT SETTLED' not in text and len(h.U['calls']) == 1,
               '085h item 6: the files are counted after the column has re-formed (no re-send for the file that was not there yet): %r %s' % (h.U['calls'], text[-1200:]))
        # item 5: the cluster gap comes from the body size (min(2 r, max(r + 0.3, 0.6 x file spacing))), not from the old spacing; the files from formation() on the trimmed bodies
        # (a stray body does not make files), clusters when formation() cannot tell
        meas = NARROW_SCRIPT.replace('function TEST_TUNE() return TUNE end', 'function TEST_TUNE() return TUNE end\nfunction TEST_FIRE() return FIRE end', 1).replace(
            'local fok, f = pcall(formation, kept, w.dx, w.dz, #kept)', 'local fok, f = false, nil', 1)
        expect(meas.count('local fok, f = false, nil') == 1 and 'TEST_FIRE' in meas, '085h item 5: the measure hooks are in the script')
        h = narrow_walk(looks=3, step=0.5, script=meas)

        def measure(across):
            lua = h.b['lua']
            lua.execute('PLACES = {' + ','.join('{%f, 0, %f}' % (a, 0.0) for a in across) + '}')  # (w.dx = 0, w.dz = 1: across = -x; the file positions are what counts)
            lua.execute('local FIRE = TEST_FIRE(); FIRE.places = function() return PLACES end')
            try:
                return tuple(lua.eval('function() local f, e = TEST_NARROW().measure(TEST_ENTRY("A1.1.2 walk"), { files = 2, read_w = 9.5, sent_w = 9.5 }); return f, e end')())
            except Exception:  # (the older build has another measure(): the check fails)
                return (None, 0.0)
        two_files = [-1.9] * 10 + [1.9] * 10
        f, e = measure(two_files)
        expect(f == 2 and abs((e or 0) - 3.8) < 0.01, '085h item 5: two files 3.8 m apart are two files: %r' % ((f, e),))
        row = [x * 2.35 for x in range(-4, 4)] * 2  # bodies 2.35 m apart (less than 2 r = 2.4 m): one dense mass, not 8 files (the old gap was 0.6 x 3.8 = 2.28 m)
        f, e = measure(row)
        expect(f == 1, '085h item 5: bodies closer than their own size are one file (gap from the body size): %r' % ((f, e),))
        f, e = measure(two_files[:9] + [15.0] + two_files[10:])  # one stray body 13 m to the side
        expect(f == 2 and abs((e or 0) - 3.8) < 0.01, '085h item 5: the trimmed extent ignores a stray body: %r' % ((f, e),))

        # item 1 / 12: the 'through' verdict comes while the walker is still before the standing unit (run 56: 'through' one second after NARROWED): the wide order waits
        fake = NARROW_SCRIPT.replace('local ok, frac = pcall(FIRE.passed_frac, s, w) -- (085f: through at CLOSE_PASSED_FRAC of its bodies, not the rearmost',
                                     'local ok, frac = pcall(FIRE.passed_frac, s, w) if FAKE_FRAC then ok, frac = true, 1 end -- (085f: through at CLOSE_PASSED_FRAC of its bodies, not the rearmost', 1)
        expect(fake != NARROW_SCRIPT, '085h item 12: the false-verdict hook is in the script')
        h = narrow_walk(looks=45, step=0.5, z0=-16.0, script=fake, before_extra=lambda h: h.b['lua'].execute('FAKE_FRAC = true'))
        dbg(h)
        text = h.log()
        expect('NARROW A1.1.2 walk: the wide order waits (100 % of its bodies are past the last standing unit' in text and 'RESTORED' not in text and len(h.U['calls']) == 1 and te(h)['narrow'] is not None,
               '085h items 1 / 12: a false "through" while the walker is before the unit: the wide order waits: %r %s' % (h.U['calls'], text[-1500:]))

        # item 7: a walker whose narrowing was forgotten (someone gave it its wide order again) drives wide into the ONE narrow lane: the lane grows (0.8's safety net)
        def wide_again(h, i, U):
            if U['calls'] and i == U['calls'][0]['look'] + 4:
                h.u['walk'].update(dict(width=36.6))
                reform_bodies(h, dict(space=3.8, files=10, bias=0.4), 36.6)
        h = narrow_walk(looks=70, step=0.5, z0=-30.0, reform=False, each=wide_again)
        dbg(h, 'NARROW', 'WIDER', 'OPEN ', 'further')
        text = h.log()
        expect('NARROW FORGOTTEN A1.1.2 walk' in text and 'WIDER A1.1.1 stand for A1.1.2 walk too' in text and 'ONE lane of ' in text.split('WIDER A1.1.1 stand for A1.1.2 walk too')[1].split('\n')[0],
               '085h item 7: the narrow lane grows for the walker that came wide: ' + text[-1500:])

        # review fix 1: only the walker the narrow lane was opened for counts as stuck (an infantry walker served by a re-opened narrow stander gets no widened lane)
        got_stuck = h.b['lua'].eval('function() local A, B = {}, {}; local s = { lanes = { narrow = {} }, lane = 1, walker_seen = A, served = { [A] = true, [B] = true } }; return TEST_NARROW().stuck(s, A), TEST_NARROW().stuck(s, B) end')()
        expect(tuple(got_stuck) == (True, False), '085h review fix 1: stuck is the walker_seen only: %r' % (got_stuck,))
        # review fix 2: a forget while the walker is inside the stander (late rule) and a stander narrower than the walker's front, walker wider than 40 m: the lane still grows, to 40 m
        def wide14(h, i, U):
            if U['calls'] and i == U['calls'][0]['look'] + 4:
                h.u['walk'].update(dict(width=55.0))
                reform_bodies(h, dict(space=3.8, files=14, bias=0.4), 55.0)
        for z0 in (-30.0, -22.0):
            # (085i: with the 5 % share the 16 m stander cannot hold the 2-file lane any more; this test is about the stuck walker's lane, so the share is lifted)
            h = narrow_walk(files=14, looks=70, step=0.5, z0=z0, reform=False, each=wide14, stand=(-8.0, 8.0), script=NARROW_SCRIPT.replace('OUT_SHARE = 0.05,', 'OUT_SHARE = 1000000,', 1))
            dbg(h, 'NARROW', 'WIDER', 'OPEN ', 'further')
            text = h.log()
            after = text.split('WIDER A1.1.1 stand for A1.1.2 walk too')[1].split(chr(10))[0] if 'WIDER A1.1.1 stand for A1.1.2 walk too' in text else ''
            expect('NARROW FORGOTTEN A1.1.2 walk' in text and 'ONE lane of 40.0 m' in after,
                   '085h review fix 2: a forgotten walker over 40 m wide gets 40 m, late rule or not (z0 %s): %s' % (z0, text[-1500:]))

        # item 8: a big walker (radius 1.2) holds the lanes open until ALL its bodies are beyond the back rank (3 stragglers inside: 96 %); a small one closes at 90 %
        big_script = REAL_FIRE.replace('BIG_RADIUS = 1.0,', 'BIG_RADIUS = 0.3,', 1)
        expect(big_script != REAL_FIRE, '085h item 8: the big-radius hook is in the script')
        for radius, closes in ((0.4, False), (0.4, True)):  # (the first walker counts as big: BIG_RADIUS 0.3 in its script; the second as small)
            h = f085_walkers([('walk', 16, 5, 1.2, radius, -40.0)], script=big_script if not closes else None)
            stuck = {'walk': {i: -0.6 for i in range(3)}}
            h.f085_adv(70, stuck=stuck)
            text = h.log()
            expect(h.lane('stand') > 0 and ('CLOSING A1.1.1 stand' in text) == closes,
                   '085h item 8: a %s walker with 3 of 80 bodies still inside: the lanes %s: %s' % ('small' if closes else 'big', 'close at 90 %' if closes else 'stay open', text[-1200:]))
            if not closes:
                h.f085_adv(60)  # (the stragglers are free now: all through: 100 %)
                expect(h.lane('stand') == 0 and 'CLOSING A1.1.1 stand (nobody is passing any more, 100 % of A1.1.2 walk through)' in h.log(),
                       '085h item 8: all its bodies beyond the back rank: the big walker\'s lanes close: ' + h.log()[-900:])

        # item 9 (085h; replaced in 085i): a big walker's lanes keep the limit too - a free side closes up like any other (no step-out as far as the side is free)
        small_room = FIRE_SCRIPT.replace('OUT_SHARE = 1000000,', 'OUT_SHARE = 0.01,', 1) if 'OUT_SHARE = 1000000,' in FIRE_SCRIPT else REAL_FIRE.replace('OUT_SHARE = 0.05,', 'OUT_SHARE = 0.01,', 1)
        expect('OUT_SHARE = 0.01,' in small_room, '085h item 9: the small limit is in the script')
        h = narrow_walk(looks=60, step=0.5, script=small_room.replace('local function update()\n', 'function TEST_ENTRY(label) for _, e in ipairs(state.units) do if e.label == label then return e end end end\n'
                                                                       'function TEST_NARROW() return NARROW end\nfunction TEST_TUNE() return TUNE end\nlocal function update()\n', 1))
        line = open_line(h)
        expect('as far as the side is free' not in line and 'closes up to' in line and 'out of its outline at most' in line,
               '085i (was 085h item 9): a big walker lane closes up on a free side too, within the share: ' + line[-900:])
        h, got = column_walk('inf', 16, 5, 1.2, 0.4, script=small_room, stand=(-14.5, 14.5), looks=32)
        line = open_line(h)
        expect('closes up to' in line and 'out of its outline at most 0.3 m per side' in line, '085h item 9: a small walker\'s lanes keep the limit: ' + line[-900:])

        # item 10 (085h; removed in 085i): monstrous cavalry that is not narrowed (the controller fails) is NOT given a whole-front lane any more: the columns squeeze
        h = narrow_walk(looks=60, step=0.5, fail=True)
        line = open_line(h)
        expect('not narrowed: it keeps its formation' not in line and 'columns: block test failed' in line and 'ONE lane of 38.' not in line, '085i: mc not narrowed: columns, no whole-front lane: ' + line[-900:])
        h = narrow_walk(looks=60, step=0.5, fail=True, kind='cav')
        line = open_line(h)
        expect('not narrowed: it keeps its formation' not in line and 'columns: block test failed' in line, '085h item 10: cavalry is not given the whole-front lane: ' + line[-900:])
        h = narrow_walk(looks=60, step=0.5, uc=False)
        expect('not narrowed: it keeps its formation' not in open_line(h) and 'columns: block test failed' in open_line(h), '085h item 10: without a unit controller the columns stay: ' + open_line(h)[-700:])

        # item 11: the narrow-off build (lanes.py build <out> --narrow-off, check_all --narrow-off)
        import inspect
        src = inspect.getsource(lanes.lua_script)
        expect('narrow_off' in inspect.signature(lanes.lua_script).parameters and "'\\tNARROW = true,'" in src and "' TEST BUILD NARROW OFF'" in src and "assert not release" in src,
               '085h item 11: lua_script builds the narrow-off variant, once, never with --release')
        expect(('\tNARROW = true,' in SCRIPT) or ('\tNARROW = false,' in SCRIPT and 'NARROW OFF' in SCRIPT), '085h item 11: the script under test has the switch (on, or off and named so)')

    # --- 085i: the standing unit stays in its outline: blocks that closing up cramps are laid out anew with extra ranks (FIRE.layout), the places go to cave 1
    # in the unit's table (E_PLACES / E_NPLACES), and nobody crosses a lane, grows wider than its strip, or goes beyond the 5 % share ---------------------------
    if on('i085'):
        def unit_models(h, name='stand'):
            count = struct.unpack('<I', h.mem.read(h.u[name]['at'] + 0xa4, 4))[0]
            lst = struct.unpack('<Q', h.mem.read(h.u[name]['at'] + 0xa8, 8))[0]
            return [struct.unpack('<Q', h.mem.read(lst + 8 * i, 8))[0] for i in range(count)]

        def pos_of(h, m):
            x, _, z = struct.unpack('<3f', h.mem.read(m + 0x88, 12))
            return x, z

        def table_of(h, name='stand'):  # the unit's table of places as cave 1 reads it: { model: (x, z) }
            e = h.entry(name)
            n = struct.unpack('<I', h.mem.read(e['at'] + lanes.E_NPLACES, 4))[0]
            ptr = struct.unpack('<Q', h.mem.read(e['at'] + lanes.E_PLACES, 8))[0]
            return {m: (x, z) for m, x, z in (struct.unpack('<Qff', h.mem.read(ptr + i * lanes.PLACE_REC, 16)) for i in range(n))}, n

        def block_of(e, x, z):  # the block (by the original place) and the across of a place, as the script counts them
            s = (x - e['ox']) * e['ax'] + (z - e['oz']) * e['az']
            return max(-e['n'], min(e['n'], int((s / e['p'] + 0.5) // 1))), s

        def finals(h, name='stand'):  # where every model of the unit is sent at the last step: the table's place, else the formula of cave 1
            e = h.entry(name)
            g, s0 = h.extra(e)
            table, n = table_of(h, name)
            out = {}
            for m in unit_models(h, name):
                x, z = pos_of(h, m)
                if m in table:
                    out[m] = table[m]
                else:
                    k, s = block_of(e, x, z)
                    d = (k + e['bias']) * e['lane'] + g * (s - s0)
                    out[m] = (x + d * e['ax'], z + d * e['az'])
            return out, table

        def nearest(points):
            best = 1e9
            for i, a in enumerate(points):
                for b in points[i + 1:]:
                    best = min(best, ((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2) ** 0.5)
            return best

        # a 40 m front of two ranks 1.4 m apart, a walker through the middle: closing up (at most 5 % = 2.0 m out per side) squeezes the files to 1.1 m, a model
        # needs 2 r + 0.2 = 1.5 m: the blocks get extra ranks, in their own strip
        h = Fire(script=REAL_FIRE)
        e, g, (below, above) = outer(h, step=1.4)
        orig = {m: pos_of(h, m) for m in unit_models(h)}
        got, table = finals(h)
        if os.environ.get('I085_DEBUG'):
            print(open_line(h))
        expect(h.lane('stand') > 0 and len(table) > 0 and 'LAYOUT: ' in open_line(h) and 'extra rank' in open_line(h),
               '085i: cramped blocks are laid out anew (table + LAYOUT in the OPEN line): %d in the table %s' % (len(table), open_line(h)[-700:]))
        expect(struct.unpack('<I', h.mem.read(e['at'] + lanes.E_NPLACES, 4))[0] == len(table) <= lanes.PLACE_CAP and all(m in orig for m in table), '085i: the table holds models of this unit, at most the cave cap')
        # nobody crosses a lane: a model stays in its own block (the strip between the lane edges) and keeps its radius from them
        g, s0 = h.extra(e)
        ok_strip = True
        for m, (x, z) in got.items():
            kb, s = block_of(e, *orig[m])
            sf = (x - e['ox']) * e['ax'] + (z - e['oz']) * e['az']
            edge_l = (kb - 0.5) * e['p'] + (kb + e['bias']) * e['lane'] + g * ((kb - 0.5) * e['p'] - s0)
            edge_r = (kb + 0.5) * e['p'] + (kb + e['bias']) * e['lane'] + g * ((kb + 0.5) * e['p'] - s0)
            if kb > -e['n']:
                ok_strip = ok_strip and sf >= edge_l + 0.65 - 0.02
            if kb < e['n']:
                ok_strip = ok_strip and sf <= edge_r - 0.65 + 0.02
        expect(ok_strip, '085i: no model of the layout crosses a lane or touches its edge')
        # files at least 2 r + 0.2 = 1.5 m apart inside each block, ranks added: deeper, not wider
        by_block = {}
        for m, p in got.items():
            by_block.setdefault(block_of(e, *orig[m])[0], []).append(p)
        # (the ranks keep the spacing they had, 1.3 m; within a rank the files are 2 r + 0.2 = 1.5 m apart)
        rows = {}
        for k, ps in by_block.items():
            for x, z in ps:
                rows.setdefault((k, round(z, 1)), []).append((x, z))
        expect(all(nearest(ps) >= 1.3 - 0.02 for ps in by_block.values()) and all(len(r) < 2 or nearest(r) >= 1.5 - 0.02 for r in rows.values()),
               '085i: every block: files at least 2 r + 0.2 apart in a rank, ranks as before: %r' % {k: round(nearest(ps), 2) for k, ps in by_block.items()})
        depth_before = max(z for x, z in orig.values()) - min(z for x, z in orig.values())
        depth_after = max(z for x, z in got.values()) - min(z for x, z in got.values())
        expect(depth_after > depth_before + 0.5, '085i: the blocks grow deeper: %.2f -> %.2f m' % (depth_before, depth_after))
        beyond = max(max(x for x, z in got.values()) - 19.5, -19.5 - min(x for x, z in got.values()))  # (across the walker's way; the 40 m front: the share is 2.0 m)
        expect(beyond <= 2.0 + 0.55, '085i: no model beyond the 5 %% share sideways: %.2f m' % beyond)
        expect('in the unit axes' in open_line(h) and 'walker 0 deg off its depth axis' in open_line(h), '085i: the outline is checked in the unit axes: ' + open_line(h)[-500:])
        # never wider than the formula: an end block does not grow sideways beyond its outermost model (the share is the formula's)
        formula = {}
        for m, (x, z) in orig.items():
            kb, s = block_of(e, x, z)
            d = (kb + e['bias']) * e['lane'] + g * (s - s0)
            formula[m] = (x + d * e['ax'], z + d * e['az'])
        expect(max(x for x, z in got.values()) <= max(x for x, z in formula.values()) + 0.01 and min(x for x, z in got.values()) >= min(x for x, z in formula.values()) - 0.01,
               '085i: the layout is not wider than the formula: %.2f .. %.2f against %.2f .. %.2f' % (min(x for x, z in got.values()), max(x for x, z in got.values()),
                                                                                                   min(x for x, z in formula.values()), max(x for x, z in formula.values())))
        # the table is off when the lanes close
        for _ in range(250):
            h.u['walk']['z'] += 1.0
            h.tick()
        expect(h.lane('stand') == 0 and struct.unpack('<I', h.mem.read(h.entry('stand')['at'] + lanes.E_NPLACES, 4))[0] == 0,
               '085i: the table of places is off after the lanes closed: %r' % h.entry('stand'))

        # a neighbour 3 m beside the unit: its end block grows no further sideways than min(gap / 2 - 0.3, 2.0) = 1.2 m, the ranks go deeper instead
        h = Fire(script=REAL_FIRE)
        outer(h, step=1.4, aside=(-45.0, -60.2, -23.8))
        got2, table2 = finals(h)
        expect(h.lane('stand') > 0 and len(table2) > 0 and min(x for x, z in got2.values()) >= -19.5 - 1.25 - 0.05 and 'LAYOUT: ' in open_line(h),
               '085i: an end block beside a neighbour grows deeper, not sideways: %.2f ' % min(x for x, z in got2.values()) + open_line(h)[-500:])
        # a walker at an angle to the unit's depth axis (the unit turned 90 degrees): the ranks grow along the lanes, the outline is checked in the unit axes and the angle is logged
        h = Fire(script=REAL_FIRE)
        h.u['stand']['bearing'] = 90.0
        outer(h, step=1.4)
        expect('LAYOUT OBLIQUE A1.1.1 stand for A1.1.2 walk: the walker comes 90 deg off the depth axis' in h.log() and 'walker 90 deg off its depth axis' in h.log(),
               '085i: an oblique walker is logged: ' + h.log()[-900:])
        # blocks that are not cramped get no table (the older passes keep their formula): 2.6 m files
        h = Fire(script=REAL_FIRE)
        outer(h, step=2.6)
        expect(h.lane('stand') > 0 and table_of(h)[1] == 0 and 'LAYOUT: ' not in open_line(h), '085i: no cramped block, no table: ' + open_line(h)[-400:])

        # a big walker's lanes close up within the share too (085h item 9, no closing up on a free side, is gone)
        h = Fire(script=REAL_FIRE.replace('BIG_RADIUS = 1.0,', 'BIG_RADIUS = 0.3,', 1))
        outer(h, step=2.6)
        expect('closes up to' in open_line(h) and 'as far as the side is free' not in open_line(h), '085i: a big walker closes up too: ' + open_line(h)[-500:])
        expect('not narrowed: it keeps its formation' not in REAL_FIRE and 'FIRE.whole_front' in REAL_FIRE, '085i: the whole-front branch for mi / mc is gone, the one for veh / art stays')

    # --- 085j: math.huge (nil in the game, run 58), NARROW_DEFER_MAX 5 s, the cavalry rule (cav / mc walkers of more than 20 bodies: 4 files or the old columns) ---
    if on('j085'):
        # math.huge is nil in the game's Lua: the mock bans it, and the script does not use it
        expect(REAL_FIRE.count('math.huge') == 0, '085j item 1: the script does not use math.huge (it is nil in the game, run 58)')
        h = narrow_walk(files=8, space=5.2, radius=2.0, kind='cav', cav_rule=True, looks=2)
        expect(h.b['lua'].eval('math.huge') is None, '085j item 1: the mock bans math.huge (nil, as in the game)')
        expect(REAL_FIRE.count('NARROW_DEFER_MAX = 5.0') == 1, '085j item 2: NARROW_DEFER_MAX is 5 s')
        wide = dict(stand=(-45.0, 45.0))  # (a standing unit wide enough for a 4-file cavalry lane of ~27 m)
        # item 3: 24 bodies of cavalry, 8 files 5.2 m apart: ordered 4 files wide, the lane (> LANE_MAX 15 m) is whatever the room allows
        h = narrow_walk(files=8, space=5.2, radius=2.0, kind='cav', cav_rule=True, looks=100, step=0.5, **wide)
        dbg(h)
        text = h.log()
        m = re.search(r'NARROWED A1\.1\.2 walk from [0-9.]+ to [0-9.]+ m for A1\.1\.1 stand: (\d+) files wide, lane ([0-9.]+) m', text)
        expect(m is not None and m.group(1) == '4' and float(m.group(2)) > 15.0 and len(h.U['calls']) >= 1,
               '085j item 3: a cavalry walker of 24 bodies is narrowed to exactly 4 files, the lane may be wider than LANE_MAX: %r %s' % (m and m.groups(), text[-1500:]))
        # item 4: the room beside a narrow standing unit does not fit 4 files: no narrowing, the reason is logged, the walker takes the squeezed columns
        h = narrow_walk(files=8, space=5.2, radius=2.0, kind='cav', cav_rule=True, looks=100, step=0.5)
        dbg(h)
        text = h.log()
        expect(len(h.U['calls']) == 0 and 'NARROWED' not in text and 'a column of 4 files (cavalry rule) does not fit the room beside the standing unit' in text and 'columns:' in text,
               '085j item 4: no room for 4 files: no narrowing, the old columns: %r %s' % (h.U['calls'][:1], text[-1500:]))
        # review 2: the game forms the 4-file column wider than ordered (clamp 27 m): the lane (> LANE_MAX) follows the READ width, up to LANE_WIDE_MAX
        h = narrow_walk(files=8, space=5.2, radius=2.0, kind='cav', cav_rule=True, looks=100, step=0.5, clamp=27.0, delay=1, z0=-16.0, **wide)
        dbg(h)
        text = h.log()
        m = re.search(r'NARROW WIDER A1.1.1 stand: the lane grows to ([0-9.]+) m for A1.1.2 walk', text)
        expect(m is not None and float(m.group(1)) > 15.0 and h.entry('stand')['lane'] > 15.0 and 'GIVEN UP' not in text,
               '085j review 1: a cavalry lane over 15 m grows with the read-back width: %r %s' % (m and m.groups(), text[-1500:]))
        # cavalry of 20 bodies or fewer and infantry keep the older candidate list (2 files first)
        for kind, files in (('cav', 5), ('inf', 8)):
            h = narrow_walk(files=files, space=3.8, kind=kind, cav_rule=True, looks=100, step=0.5)
            text = h.log()
            expect(len(h.U['calls']) >= 1 and ': 2 files wide, lane ' in text and 'cavalry rule' not in text,
                   '085j item 3: %s walker of %d bodies: the older narrowing (2 files) is unchanged: %s' % (kind, files * 3, text[-1200:]))
        # item 2: the wide order waits at most 5 s
        def stops(h, i, U):
            if U['calls'] and i >= U['calls'][0]['look'] + 14:
                h.u['walk']['z'] -= 0.5
                h.u['walk']['moving'] = False
        h = narrow_walk(looks=65, step=0.5, each=stops, z0=-16.0)
        expect('the wide order waits' in h.log() and '; at most 5 s' in h.log(), '085j item 2: the wait for the wide order is at most 5 s: ' + h.log()[-1200:])

    if on('narrow'):
        # (a) the issue: at the first look that plans the pair (the walker is 20+ m away) the walker gets its own order again, narrowed: same goal and
        # bearing, the run state it had, a width of 2 files; ONE lane (no columns, WALKERS never raised); take_control / release_control never called
        h = narrow_walk(looks=100, step=0.5)  # (0.5 m per look = 2.5 m/s: walking)
        dbg(h)
        text, U = h.log(), h.U
        expect(len(U['calls']) >= 1 and text.count('NARROWED A1.1.2 walk from 36.6 to ') == 1 and ' for A1.1.1 stand: 2 files wide, lane ' in text and U['controls'] == [],
               '085e A2: the walker is narrowed once, no take_control: %r %s' % (U['calls'][:2], text[-1500:]))
        call = U['calls'][0]
        expect(call['x'] == 0.0 and call['z'] == 80.0 and call['bearing'] == 0.0 and call['run'] is False and abs(call['width'] - 9.5) < 0.01,  # (085g: files x spacing + 0.5 x spacing)
               '085e A2: the order is the walker\'s own (goal, bearing, run state) with a narrow width: %r' % (call,))
        e = h.entry('stand')
        line = open_line(h)
        expect(h.lane('stand') > 0 and e['n'] == 1 and e['bias'] == -0.5 and abs(e['lane'] - 13.4) < 0.02 and 'NARROWED' in line + h.log() and 'ONE lane of ' in line
               and 'no columns (the walker was ordered narrow' in line, '085e A2 (085h: the lane is planned from the width SENT: 9.5 + 2.4 + 1.5): ONE lane = the order width + 2 r + LANE_EXTRA_BIG, no columns: %r %s' % (e, line[-1200:]))
        expect(struct.unpack('<I', h.mem.read(h.tbl + lanes.WALKERS, 4))[0] == 0 and struct.unpack('<f', h.mem.read(h.entry('walk')['at'] + lanes.E_WQ, 4))[0] == 0.0,
               '085e A2: cave 4 is not used for the walker (WALKERS 0, E_WQ 0)')
        # same_order: the mod's own order is the same order (order_at stays, 'passing' keeps the lanes open for it)
        entry = te(h)
        expect(entry['narrow'] is not None and entry['order_at'] < 30, '085e A2: the narrowed order did not count as a new order: order_at %r' % entry['order_at'])

        # (b) the read-back: the game makes the column wider than ordered (clamp 11 m): the lane follows the READ width (11 + 2 r + 1.5 = 14.9 m; opened for 13.4 m first:
        # NARROW WIDER). 085h: a width the room does not allow stays narrow ('NARROW TOO WIDE?'), the walker is never given up and never restored wide for it
        h = narrow_walk(looks=60, step=0.5, clamp=11.0)  # (the game forms 3 files; before the opening: the lane is planned from the read width)
        text, U = h.log(), h.U
        expect('NARROW WIDER' not in text and abs(h.entry('stand')['lane'] - 14.9) < 0.02 and 'NARROW GIVEN UP' not in text and len(U['calls']) == 1 and 'ONE lane of 14.9 m' in open_line(h),
               '085e A2 (085h: the READ width): the lane follows the width the game gave (read back before the opening): %r %s' % (h.entry('stand'), text[-1500:]))
        h = narrow_walk(looks=60, step=0.5, clamp=11.0, delay=1, z0=-16.0)  # (opened at once, the read-back comes a look later: the lane grows)
        dbg(h)
        text, U = h.log(), h.U
        expect('NARROW WIDER A1.1.1 stand: the lane grows to 14.9 m for A1.1.2 walk' in text and abs(h.entry('stand')['lane'] - 14.9) < 0.02 and 'NARROW GIVEN UP' not in text and len(U['calls']) == 1,
               '085e A2: the open lane follows the width the game gave: %r %s' % (h.entry('stand'), text[-1500:]))
        nofit = NARROW_SCRIPT.replace('function NARROW.widen(s, w, need)\n', 'function NARROW.widen(s, w, need)\n\tif true then return false, "test: no room" end\n', 1).replace('function NARROW.fits(s, w, need)\n', 'function NARROW.fits(s, w, need)\n\tif true then return false, "test: no room" end\n', 1)
        expect(nofit != NARROW_SCRIPT, '085h item 3: the no-room hook is in the script')
        for z0 in (-40.0, -16.0):  # (a width the room does not allow: before and after the opening)
            h = narrow_walk(looks=50, step=0.5, clamp=40.0, delay=1, z0=z0, script=nofit)
            dbg(h)
            text, U = h.log(), h.U
            expect('NARROW TOO WIDE? A1.1.2 walk: needs 43.9 m, lane 13.4 m - kept narrow (test: no room)' in text and 'GIVEN UP' not in text and 'RESTORED' not in text.split('NARROW TOO WIDE?')[0]
                   and len(U['calls']) == 1 and 'ONE lane of 13.4 m' in open_line(h) and text.count('NARROW TOO WIDE?') == 1,
                   '085h item 1 / 3: a column that does not fit (z0 %s) stays narrow, never given up, never restored wide for it: %r %s' % (z0, U['calls'], text[-1800:]))
        # ... and the room that allows half way: the widest lane that fits (the second of the two tries)
        half = NARROW_SCRIPT.replace('function NARROW.fits(s, w, need)\n', 'function NARROW.fits(s, w, need)\n\tif need > 14.5 then return false, "test: only half way" end\n', 1).replace('function NARROW.widen(s, w, need)\n', 'function NARROW.widen(s, w, need)\n\tif need > 14.5 then return false, "test: only half way" end\n', 1)
        h = narrow_walk(looks=50, step=0.5, clamp=40.0, delay=1, z0=-40.0, script=half)
        expect(h.log().count('NARROW TOO WIDE? A1.1.2 walk: needs 43.9 m, lane 14.2 m - kept narrow') == 1 and 'GIVEN UP' not in h.log() and len(h.U['calls']) == 1,
               '085h item 3: the widest lane that fits is half way between the lane and the need: ' + h.log()[-900:])
        # the game takes the order a look late (delay 1): waited for; not at all within NARROW_WAIT (3) looks: no narrowing, nothing restored
        h = narrow_walk(looks=100, step=0.5, delay=1)
        expect(h.log().count('NARROW WIDTH A1.1.2 walk: sent 9.5 m read 9.5 m, files wanted 2 got 2, across extent 3.8 m (lane 13.4 m)') == 1, '085e review 6: the sent and the read width are logged once: ' + h.log()[-900:])
        expect('NARROWED A1.1.2 walk' in h.log() and 'GIVEN UP' not in h.log() and len(h.U['calls']) == 1 and te(h)['narrow'] is not None, '085e A2: an order the game takes a look late is waited for: ' + h.log()[-1200:])
        h = narrow_walk(looks=60, step=0.5, delay=9)
        expect('NARROW GIVEN UP A1.1.2 walk: the game did not take the order in 3 looks' in h.log() and len(h.U['calls']) == 2 and 'RESTORED A1.1.2 walk (width 36.6 m, walking): the game did not take the narrowed width' in h.log()
               and abs(h.U['calls'][1]['width'] - 36.6) < 0.01 and h.log().count('NARROWED A1.1.2 walk') == 1,
               '085e review 5: an order the game does not take: given up, the saved order is written back (never narrowed again): ' + h.log()[-1200:])
        # a controller that fails: the plan was made, the narrowing fails, the next plan is the columns (this order is not narrowed again)
        h = narrow_walk(looks=60, step=0.5, fail=True)
        expect('NARROW ERROR A1.1.2 walk for A1.1.1 stand: no unit controller' in h.log() and 'NARROWED' not in h.log() and h.lane('stand') > 0 and h.U['calls'] == []
               and 'not narrowed: it keeps its formation' not in open_line(h) and 'block test failed' in open_line(h),
               '085e A2 (085i: no whole-front lane any more): a failing unit controller: the monstrous cavalry that is not narrowed takes the squeezed columns: ' + h.log()[-1500:])

        # (c) FORGET: the player gave the walker another order: it stays, nothing is restored, the narrowing is dropped
        def reorder(h, i, U):
            if U['calls'] and i == U['calls'][0]['look'] + 12:
                h.u['walk'].update(dict(gx=40.0))
        h = narrow_walk(looks=60, step=0.5, each=reorder)
        dbg(h)
        expect(h.log().count('NARROW FORGOTTEN A1.1.2 walk (it was given another order)') == 1 and len(h.U['calls']) == 1 and te(h)['narrow'] is None and 'RESTORED' not in h.log() and h.U['controls'] == [],
               '085e A2: another order of the player: forgotten, never restored over: ' + h.log()[-1500:])
        order_a = te(h)['order_at']
        expect(order_a >= 18, '085e A2: ... and it counts as a new order (order_at %r)' % order_a)

        # --- 085g: the mod keeps its own order while the game settles; width = files x spacing + pad; the files are counted; the lane follows the measured extent ---
        # the width sent is files x spacing + 0.5 x spacing (2 x 3.8 + 1.9), not (files - 1) x spacing
        h = narrow_walk(looks=60, step=0.5)
        expect(len(h.U['calls']) >= 1 and abs(h.U['calls'][0]['width'] - 9.5) < 0.01, '085g: the width sent is files x spacing + pad: %r' % (h.U['calls'][:1],))
        # settling: the game reads 4.0 m (sent 9.5), then drifts the width to 7.0 m and the goal by 1.5 m within the window: still the mod's own order
        def drifts(h, i, U):
            if U['pending'] and not U.get('squeezed'):
                U['pending'][0][2]['width'] = 4.0
                U['squeezed'] = True
            if U.get('squeezed') and not U['pending'] and not U.get('drifted') and i >= U['calls'][0]['look'] + 4:
                h.u['walk'].update(dict(width=7.0, gz=h.u['walk']['gz'] + 1.5))
                U['drifted'] = True
        h = narrow_walk(looks=36, step=0.5, delay=1, reform=False, each=drifts)
        dbg(h)
        expect(h.U.get('drifted') and 'NARROW FORGOTTEN' not in h.log() and te(h)['narrow'] is not None and 'NARROWED A1.1.2 walk' in h.log(),
               '085g: a width and a goal drifting while the game settles do not forget the order: %r %s' % (h.U.get('drifted'), h.log()[-700:]))
        # a real re-order inside the window: the goal 5 m off (beyond the slack) is foreign, and the line says why; the player's own wide width is foreign too
        def far_goal(h, i, U):
            if U['calls'] and i == U['calls'][0]['look'] + 4:
                h.u['walk'].update(dict(gz=h.u['walk']['gz'] + 5.0))
        h = narrow_walk(looks=36, step=0.5, reform=False, each=far_goal)
        expect(h.log().count('NARROW FORGOTTEN A1.1.2 walk (it was given another order): goal 5.0 m (slack 3.0), bearing 0.0 deg, width 9.5 (own w 9.5, sent 9.5, read 9.5)') == 1 and te(h)['narrow'] is None,
               '085g: a goal beyond the slack is foreign, the values are in the line: ' + h.log()[-700:])
        def wide_again(h, i, U):
            if U['calls'] and i == U['calls'][0]['look'] + 4:
                h.u['walk'].update(dict(width=36.6))
        h = narrow_walk(looks=36, step=0.5, reform=False, each=wide_again)
        expect(h.log().count('NARROW FORGOTTEN A1.1.2 walk (it was given another order): goal 0.0 m (slack 3.0), bearing 0.0 deg, width 36.6 (own w 9.5, sent 9.5, read 9.5)') == 1 and te(h)['narrow'] is None,
               '085g: the original wide width is foreign: ' + h.log()[-700:])
        # GIVEN UP -> RESTORED: the order the game takes late (an extended goal) is no FORGOTTEN of the restoring tracker
        h = narrow_walk(files=20, looks=80, step=0.5, delay=9, before_extra=lambda h: f085_far(h, 30.0))
        dbg(h)
        expect('NARROW GIVEN UP A1.1.2 walk: the game did not take the order in 3 looks' in h.log() and 'RESTORED A1.1.2 walk' in h.log() and 'NARROW FORGOTTEN' not in h.log(),
               '085g: GIVEN UP -> RESTORED is not logged as FORGOTTEN: ' + h.log()[-900:])
        # the restoring tracker: the game moves the goal of the RESTORED (original) order by 1.5 m (as it does with an extended goal): within the settling slack, no FORGOTTEN
        def snaps_restore(h, i, U):
            if U['pending'] and U['pending'][0][2]['width'] > 30.0 and not U.get('snapped'):
                U['pending'][0][2]['z'] += 1.5
                U['snapped'] = True
        h = narrow_walk(files=20, looks=140, step=1.0, delay=1, before_extra=lambda h: f085_far(h, 30.0), each=snaps_restore)
        dbg(h)
        expect(h.U.get('snapped') and 'RESTORED A1.1.2 walk (width 74.6 m' in h.log() and 'NARROW FORGOTTEN' not in h.log() and te(h)['narrow'] is None,
               '085g: the restored order, its goal moved a little by the game, is not logged as FORGOTTEN: %r %s' % (h.U.get('snapped'), h.log()[-700:]))
        # fewer files than wanted: a walker under BIG_RADIUS (0.9 m) is sent ONCE more, one spacing wider; the files are counted from the bodies; one WIDTH line at the end
        def small_once_narrowed(h, i, U):  # (the plan needs a big walker; the count after the narrowing sees a walker under BIG_RADIUS)
            if U['calls']:
                h.b['lua'].eval('TEST_TUNE')().BIG_RADIUS = 1.5
        h = narrow_walk(looks=70, step=0.5, bias=-0.6, each=small_once_narrowed)
        dbg(h)
        text = h.log()
        expect(text.count('NARROW FILES A1.1.2 walk: wanted 2, got 1 (sent 9.5 m, read 9.5 m) -> sent 13.3 m') == 1 and [round(c['width'], 1) for c in h.U['calls'][:2]] == [9.5, 13.3]
               and text.count('NARROW WIDTH A1.1.2 walk: sent 13.3 m read 13.3 m, files wanted 2 got 2, across extent 3.8 m') == 1 and 'NARROW GIVEN UP' not in text,
               '085g: one re-send for the missing file (a walker under BIG_RADIUS): %r %s' % (h.U['calls'][:3], text[-900:]))
        # still fewer: kept, no third order
        h = narrow_walk(looks=70, step=0.5, bias=-2.0, each=small_once_narrowed)
        text = h.log()
        expect(text.count('NARROW FILES A1.1.2 walk') == 1 and text.count('files wanted 2 got 1 (kept)') == 1 and len([c for c in h.U['calls'] if c['width'] < 20]) == 2 and 'NARROW GIVEN UP' not in text,
               '085g: still too few files after the re-send: kept, said once: %r %s' % (h.U['calls'][:4], text[-900:]))
        # 085h item 4: a BIG walker (radius 1.2) gets no re-send for fewer files than wanted (a wider column after contact): one order, the WIDTH line says 'kept'
        for bias in (-0.6, -2.0):
            h = narrow_walk(looks=70, step=0.5, bias=bias)
            text = h.log()
            expect('NARROW FILES' not in text and text.count('files wanted 2 got 1 (kept)') == 1 and len([c for c in h.U['calls'] if c['width'] < 20]) == 1 and 'GIVEN UP' not in text and 'RESTORED' not in text,
                   '085h item 4: a big walker is not sent again for missing files: %r %s' % (h.U['calls'][:4], text[-900:]))
        # --- 085g review fixes ---
        # (1) the hold-end re-send of a held runner: the game reads back only 0.4 x the width sent; the order given again is taken (no GIVEN UP), the runner runs again
        h = narrow_walk(looks=70, step=1.0, run=True, z0=-16.0, read_scale=0.4)
        dbg(h)
        expect('the split is done, running again' in h.log() and 'NARROW GIVEN UP' not in h.log() and len(h.U['calls']) >= 2 and h.U['calls'][1]['run'] is True,
               '085g review 1: a held runner whose order the game reads back narrower: no GIVEN UP: %r %s' % (h.U['calls'][:3], h.log()[-900:]))
        # (2) one file: no pad (it would make two files): the width is 2 r
        tried1 = NARROW_SCRIPT.replace('local rok = pcall(make_room, s, w, pl, nil, true)\n\t\t\tlocal fits', 'local rok = pcall(make_room, s, w, pl, nil, true)\n\t\t\tif nf >= 2 then rok = false end\n\t\t\tlocal fits', 1)
        expect(tried1 != NARROW_SCRIPT, '085g review 2: the hook is in the script')
        h = narrow_walk(files=20, looks=40, step=0.5, script=tried1, reform=False, before_extra=lambda h: f085_far(h, 150.0))
        expect(len(h.U['calls']) >= 1 and abs(h.U['calls'][0]['width'] - 2.4) < 0.01, '085g review 2: one file is ordered 2 r wide: %r' % (h.U['calls'][:1],))
        # (3) the column re-forms through MORE files (3 files at first, 2 after 2 s): counted once, at the end
        h = narrow_walk(looks=90, step=0.5, stage=(3, 10))
        dbg(h)
        text = h.log()
        expect(text.count('NARROW WIDTH A1.1.2 walk') == 1 and 'files wanted 2 got 2, across extent 3.8 m' in text and 'NARROW FILES' not in text and 'NARROW GIVEN UP' not in text,
               '085g review 3: a column re-forming through more files is counted at the end: ' + text[-900:])
        # (4) one body far to the side is no file and no extent
        h = narrow_walk(looks=70, step=0.5, stray=15.0)
        dbg(h)
        text = h.log()
        expect(text.count('NARROW WIDTH A1.1.2 walk') == 1 and 'files wanted 2 got 2, across extent 3.8 m' in text and 'NARROW GIVEN UP' not in text,
               '085g review 4: a stray body does not add a file or extent: ' + text[-900:])
        # 085h item 6: the lane never follows the measured extent (only the order width and the width the game read back): 3 files formed (7.6 m across), read 11 m
        h = narrow_walk(looks=60, step=0.5, clamp=11.0)
        expect('across extent 7.6 m (lane 14.9 m)' in h.log() and abs(h.entry('stand')['lane'] - 14.9) < 0.02, '085h: the extent is in the log only, the lane follows the READ width: ' + h.log()[-700:])

        # (d) RESTORE (a): the walker runs through (the rearmost body is past the back rank): its own order again, with the width, goal, bearing and run state it had.
        # The restore is the same order for the script (order_at stays), the stander closes as ever
        h = narrow_walk(looks=110, step=1.0)
        dbg(h)
        text, U = h.log(), h.U
        expect(len(U['calls']) == 2 and 'RESTORED A1.1.2 walk (width 36.6 m, running): 100 % of its bodies are past the last standing unit (A1.1.1 stand: 100 % of ' in text and ' bodies beyond the back rank, its lanes stood ' in text,
               '085e A2 (085h: a big walker: all its bodies, and what the verdict rests on): restored when through: %r %s' % (U['calls'], text[-1500:]))
        first, second = U['calls']
        expect((second['x'], second['z'], second['bearing'], second['run']) == (0.0, 80.0, 0.0, True) and abs(second['width'] - 36.6) < 0.01 and first['run'] is True and second['look'] > first['look'] + 25,
               '085e A2: the restore is the saved order: %r' % (second,))
        e1 = te(h)
        order_before = e1['order_at']
        for _ in range(3):
            h.tick()
        expect(te(h)['narrow'] is None and te(h)['order_at'] == order_before and order_before < 30 and h.U['controls'] == [], '085e A2: the restore is the same order for the script: order_at %r' % te(h)['order_at'])
        for _ in range(40):
            h.u['walk']['z'] += 1.0
            h.tick()
        expect(h.lane('stand') == 0 and 'CLOSE A1.1.1 stand' in h.log() and 'ERROR' not in h.log(), '085e A2: the stander closes as ever: ' + h.log()[-900:])
        # (b) the pass ends otherwise: the standing unit is given an order before the walker arrives (its lanes close): restored a few looks later
        def stander_leaves(h, i, U):
            if U['calls'] and i == U['calls'][0]['look'] + 8:
                h.u['stand'].update(dict(moving=True, gz=-30.0))
        DEFER_1 = NARROW_SCRIPT.replace('NARROW_DEFER_MAX = 5.0', 'NARROW_DEFER_MAX = 1.0', 1)
        expect(DEFER_1 != NARROW_SCRIPT, '085h item 1: the deferral cap is in the script')
        h = narrow_walk(looks=60, step=0.5, each=stander_leaves, script=DEFER_1)
        dbg(h)
        expect('NARROW A1.1.2 walk: the wide order waits (the pass ended' in h.log() and 'RESTORED A1.1.2 walk (width 36.6 m, walking): the pass ended' in h.log() and ' - after waiting 1.' in h.log() and len(h.U['calls']) == 2,
               '085e A2 (085h item 1: the restore waits while the walker is before the unit, at most NARROW_DEFER_MAX): the pass ended before the walker arrived: restored: ' + h.log()[-1500:])
        # (c) time is up (the expected pass time x NARROW_MAX, at least NARROW_MIN_S: here 0.4 s)
        quick = DEFER_1.replace('NARROW_MIN_S = 10.0', 'NARROW_MIN_S = 0.4', 1).replace('NARROW_MAX = 2.0', 'NARROW_MAX = 0.01', 1)
        expect(quick != DEFER_1, '085e A2: the time-out is in the script')
        h = narrow_walk(looks=60, step=0.5, script=quick)
        expect('NARROW A1.1.2 walk: the wide order waits (time is up)' in h.log() and 'RESTORED A1.1.2 walk (width 36.6 m, walking): time is up - after waiting 1.' in h.log() and len(h.U['calls']) == 2,
               '085e A2 (085h item 1): the narrowing times out, the restore waits while the walker is before the unit: ' + h.log()[-1200:])
        # (d) idle at its goal: the walker stops inside the lanes (it does not move for 5 looks)
        def stops(h, i, U):
            if U['calls'] and i >= U['calls'][0]['look'] + 14:
                h.u['walk']['z'] -= 0.5  # (stays where it is)
                h.u['walk']['moving'] = False
        h = narrow_walk(looks=65, step=0.5, each=stops, z0=-16.0, script=DEFER_1)  # (the lanes are open: the pair waits)
        dbg(h, 'NARROW', 'RESTORED', 'OPEN', 'CLOSE', 'MISSED')
        expect('NARROW A1.1.2 walk: the wide order waits (it stands (idle at its goal))' in h.log() and 'RESTORED A1.1.2 walk (width 36.6 m, walking): it stands (idle at its goal) - after waiting 1.' in h.log() and len(h.U['calls']) == 2,
               '085e A2 (085h item 1): idle: the restore waits while it is in / before the unit, then comes back: ' + h.log()[-1500:])
        # ... a walker that stands within max(5, half) of its goal is restored at once (the wait is for a walker that is in the way); here the distance test is opened up by the script
        arrived = NARROW_SCRIPT.replace('math.max(5, w.half or 0) ^ 2', '1e9', 1)
        expect(arrived != NARROW_SCRIPT, '085h item 1: the at-goal test is in the script')
        h = narrow_walk(looks=65, step=0.5, each=stops, z0=-16.0, script=arrived)
        expect('the wide order waits' not in h.log() and 'RESTORED A1.1.2 walk (width 36.6 m, walking): it stands (idle at its goal)' in h.log() and len(h.U['calls']) == 2,
               '085h item 1: idle at its goal: restored at once: ' + h.log()[-1200:])
        # give_up: the mod switches itself off (an error): the narrowed walker gets its order back first
        def mod_off(h, i, U):
            if U['calls'] and i == U['calls'][0]['look'] + 6:
                h.b['lua'].eval('TEST_GIVE_UP')('test failure')
        h = narrow_walk(looks=60, step=0.5, each=mod_off)
        text = h.log()
        expect('RESTORED A1.1.2 walk (width 36.6 m, walking): the mod is off' in text and text.index('RESTORED A1.1.2 walk') < text.index('MOD OFF: test failure') and len(h.U['calls']) == 2,
               '085e A2: the mod off: the order comes back: ' + text[-1200:])

        # (e) review 7: ALL armies, symmetric: a walker of the alliance's second army (the AI's, not the player's) is narrowed the same way; without the unit
        # controller of the game the columns stay
        h = narrow_walk(looks=60, step=0.5, split={'walk'})
        expect(len(h.U['calls']) == 1 and h.log().count('NARROWED A1.2.1 walk from 36.6 to ') == 1 and h.U['controls'] == [] and h.lane('stand') > 0, '085e review 7: an AI army walker is narrowed too: ' + h.log()[-1200:])
        h = narrow_walk(looks=60, step=0.5, uc=False)
        expect('NARROW' not in h.log() and 'columns: block test failed' in open_line(h), '085e A2: without create_unitcontroller: the columns: ' + h.log()[-900:])
        h = narrow_walk(looks=60, step=0.5, script=NARROW_SCRIPT.replace('NARROW = true,', 'NARROW = false,', 1))
        expect(h.U['calls'] == [] and 'NARROW' not in h.log() and 'columns: block test failed' in open_line(h), '085e A2: TUNE.NARROW off: the columns')
        # an attack order / an enemy near: not a plain move
        def aim(h, i, U):
            h.u['walk']['target'] = 103
        h = narrow_walk(looks=70, step=0.5, each=aim)
        expect(h.U['calls'] == [] and 'narrowing: it has an enemy target (an attack order)' in h.log(), '085e A2: an attack order is not narrowed: ' + h.log()[-900:])
        # the order ends inside the standing unit: no narrowing (it would stand in the lane)
        short = narrow_walk(looks=70, step=0.5, each=lambda h, i, U: h.u['walk'].update(dict(gz=6.0)))
        expect(short.U['calls'] == [] and 'narrowing: its order ends ' in short.log() and ' to be through' in short.log(), '085e A2: a goal just beyond the stander: no narrowing: ' + short.log()[-900:])

        # (f) live run state (checked at every look) and B1 (the walk hold). A walker that runs (fast) and meets the standing unit before its split is done
        # (contact 1.1 s, split 1.2 s + 1.75 s of margin) is ordered to WALK; when the split is over (1.9 s) it is ordered to run again
        h = narrow_walk(looks=70, step=1.0, run=True, z0=-16.0)
        dbg(h, 'NARROW', 'RESTORED')
        U = h.U
        held = re.search(r'held at a walk for at most ([0-9.]+) s', h.log())
        expect(len(U['calls']) >= 2 and U['calls'][0]['run'] is False and U['calls'][1]['run'] is True and held and 1.0 <= float(held.group(1)) <= 2.5 and 'the split is done, running again' in h.log(),
               '085e B1: walk first, then run: %r %s' % (U['calls'], h.log()[-1500:]))
        gap = U['calls'][1]['look'] - U['calls'][0]['look']
        expect(abs(gap * 0.2 - float(held.group(1))) <= 0.45 and U['calls'][1]['x'] == 0.0 and U['calls'][1]['z'] == 80.0, '085e B1: the hold ends when the split time is over (%s s): %d looks' % (held.group(1), gap))
        # ... not held when the contact is far enough away (z0 -40: 7 s against 3.65 s): runs at once
        h = narrow_walk(looks=40, step=1.0, run=True, z0=-40.0)
        expect(h.U['calls'][0]['run'] is True and 'held at a walk' not in h.log(), '085e B1: no hold when the split is in time: %r' % (h.U['calls'][:1],))
        # ... seen RUNNING during the hold (the player / the game switched it): the hold ends at once, no order is given
        def runs_again(h, i, U):
            if U['calls'] and i == U['calls'][0]['look'] + 4:
                h.u['walk']['fast'] = True
        h = narrow_walk(looks=70, step=1.0, run=True, z0=-16.0, each=runs_again)
        dbg(h, 'NARROW', 'RESTORED')
        expect(len(h.U['calls']) == 2 and h.U['calls'][0]['run'] is False and h.U['calls'][1]['look'] > h.U['calls'][0]['look'] + 15 and 'seen running during the walk hold' in h.log()
               and 'RESTORED A1.1.2 walk (width 36.6 m, running)' in h.log(), '085e B1: seen running during the hold: the hold ends, no order is given; the restore runs: %r %s' % (h.U['calls'], h.log()[-1500:]))
        # live run state: a walker that ran when it was narrowed and walks later (the player switched it) is restored WALKING
        def slows(h, i, U):
            if U['calls'] and i >= U['calls'][0]['look'] + 12:
                h.u['walk']['fast'] = False
                h.u['walk']['z'] -= 0.5  # (net 0.5 m per look = 2.5 m/s)
        h = narrow_walk(looks=160, step=1.0, run=True, z0=-40.0, each=slows)
        dbg(h, 'NARROW', 'RESTORED')
        expect(len(h.U['calls']) == 2 and h.U['calls'][0]['run'] is True and h.U['calls'][1]['run'] is False and 'RESTORED A1.1.2 walk (width 36.6 m, walking)' in h.log()
               and h.log().count('it walks now') == 1, '085e live run state: the restore uses the latest run state, said once: %r %s' % (h.U['calls'], h.log()[-1500:]))

        # two standing units in a row on the walker's way: ONE narrowing for both (the second is added, no new order); restored only after the LAST one
        def second_row(h):
            models, x = [], -14.5
            while x <= 14.5 + 1e-6:
                for r in range(2):
                    models.append(h.model(x, 14.0 - 1.3 * r, 0.65))
                x += 1.3
            h.set_models('aside', models)
            h.u['aside'].update(dict(x=0.0, z=13.4, gx=0.0, gz=13.4, men=46, width=29.0))
            at = h.entry('aside')['at']
            h.mem.write(at + lanes.E_TPL, struct.pack('<3fI QQ HH I I I', 1.0, 2.0, 3.0, 0, 0, 0, 0xE3A5, 0xC07B, 7, 0, 0))
            h.mem.write(at + lanes.E_SEQ, struct.pack('<I', 1))
        h = narrow_walk(looks=110, step=1.0, run=True, before_extra=second_row)
        dbg(h, 'NARROW', 'RESTORED', 'OPEN ', 'CLOSE')
        text, U = h.log(), h.U
        expect(text.count('NARROWED A1.1.2 walk') == 1 and '  NARROW A1.1.2 walk: the narrowed column goes through A1.1.3 aside too' in text and len(U['calls']) == 2
               and text.count('OPEN  A1.1.1 stand makes way for A1.1.2 walk') == 1 and text.count('OPEN  A1.1.3 aside makes way for A1.1.2 walk') == 1 and text.count('RESTORED A1.1.2 walk') == 1,
               '085e A2: two standing units: one narrowing, one restore: %r %s' % (U['calls'], text[-1800:]))
        stand_closed = [l for l in text.splitlines() if 'CLOSE A1.1.1 stand' in l or 'CLOSING A1.1.1 stand' in l]
        expect(stand_closed and text.index('RESTORED A1.1.2 walk') > text.index('OPEN  A1.1.3 aside') and U['calls'][1]['look'] > U['calls'][0]['look'] + 50,
               '085e A2: the restore comes after the last standing unit, not after the first: %r' % (U['calls'],))

        # --- 085e review fixes ---------------------------------------------------------------------------------------------------------------------
        # (1) a walker that was narrowed and re-formed by the game (2 files, deep) keeps its ONE lane: the plan is not made from its new formation (columns)
        def reform(h, i, U):
            if U['calls'] and i == U['calls'][0]['look'] + 3:
                for k in range(0, len(h.offs), 2):
                    off = (((k // 2) % 2 - 0.5) * 3.8, -(k // 2 // 2) * 3.8)
                    h.offs[k] = (h.offs[k][0], off)
                    h.offs[k + 1] = (h.offs[k + 1][0], off)
        h = narrow_walk(looks=60, step=0.5, run=False, each=reform)
        e = h.entry('stand')
        expect(h.lane('stand') > 0 and e['n'] == 1 and e['bias'] == -0.5 and 'walker ordered narrow (9.5 m): ONE lane of 13.4 m, no columns' in open_line(h),
               '085e review 1: the re-formed narrow walker keeps ONE lane: %r %s' % (e, open_line(h)[-900:]))
        # (2) the run state is not known right after a new order: a walker that walks is narrowed only START_TICKS (5 s) after its order; one seen running at once
        h = narrow_walk(looks=60, step=0.5)
        first = h.U['calls'][0]['look'] if h.U['calls'] else 999
        expect(first >= 20 and h.U['calls'][0]['run'] is False and h.log().index('NARROWED') < h.log().index('OPEN '), '085e review 2: a walking walker waits for START_TICKS (first call at look %d): %s' % (first, h.log()[-600:]))
        h = narrow_walk(looks=40, step=1.0, run=True)
        expect(h.U['calls'][0]['look'] <= 8 and h.U['calls'][0]['run'] is True, '085e review 2: a walker seen running is narrowed at once: %r' % (h.U['calls'][:1],))
        # (3) is_moving_fast lags the walking order by 3 looks (it still says running): the hold goes on until its time is over, it does not end on the stale answer
        h = narrow_walk(looks=70, step=1.0, run=True, z0=-16.0, fast_lag=3)
        dbg(h, 'NARROW', 'RESTORED')
        expect(len(h.U['calls']) >= 2 and h.U['calls'][0]['run'] is False and h.U['calls'][1]['run'] is True and h.U['calls'][1]['width'] < 10 and 'seen running during the walk hold' not in h.log()
               and 'the split is done, running again' in h.log(), '085e review 3: a lagging is_moving_fast does not end the hold: %r %s' % (h.U['calls'], h.log()[-900:]))
        # (4) an error in the narrowing code of a unit: the order comes back, the unit stays in the game, no loop of errors
        bad = NARROW_SCRIPT.replace('function NARROW.observe(w, same)\n', 'function NARROW.observe(w, same)\n\tif OBSERVE_FAIL then error("boom") end\n', 1)
        expect(bad != NARROW_SCRIPT, '085e review 4: the failure hook is in the script')
        h = narrow_walk(looks=60, step=1.0, run=True, script=bad, each=lambda h, i, U: h.b['lua'].execute('OBSERVE_FAIL = true') if U['calls'] and i == U['calls'][0]['look'] + 6 else None)
        dbg(h, 'NARROW', 'RESTORED', 'LEFT OUT')
        expect('NARROW ERROR A1.1.2 walk: ' in h.log() and h.log().count('NARROW ERROR A1.1.2 walk') <= 2 and len(h.U['calls']) == 2 and 'RESTORED A1.1.2 walk (width 36.6 m, running): an error in the script' in h.log()
               and 'LEFT OUT' not in h.log() and te(h)['narrow'] is None, '085e review 4: the error restores the order, once: %r %s' % (h.U['calls'], h.log()[-1200:]))
        # (7) the AI's walker is re-ordered all the time: forgotten once, then the normal way for NARROW_COOLDOWN (3 s), no loop
        def ai_reorders(h, i, U):
            if U['calls'] and i >= U['calls'][0]['look'] + 10 and (i - U['calls'][0]['look']) % 12 == 10:
                h.u['walk'].update(dict(gx=(h.u['walk']['gx'] + 7.0) % 30.0))
        h = narrow_walk(looks=60, step=1.0, run=True, split={'walk'}, each=ai_reorders)
        dbg(h, 'NARROW', 'RESTORED')
        expect(h.log().count('NARROWED A1.2.1 walk') == 1 and h.log().count('NARROW FORGOTTEN A1.2.1 walk') == 1 and ' for 3 s' in h.log() and len(h.U['calls']) == 1,
               '085e review 7: the AI re-orders: forgotten once, no narrowing loop: %s' % (h.log()[-1200:],))
        # (8) the Mod Configuration Tool switches the mod off during the battle: the narrowed walkers get their orders back (read every COLLECT_EVERY looks)
        h = narrow_walk(looks=60, step=1.0, run=True, mct={'military_drill_enabled': True},
                        each=lambda h, i, U: h.b['lua'].execute("MCT_VALUES['military_drill_enabled'] = false") if U['calls'] and i == U['calls'][0]['look'] + 8 else None)
        dbg(h, 'NARROW', 'RESTORED', 'settings')
        expect('the mod was switched off in the Mod Configuration Tool during the battle' in h.log() and 'RESTORED A1.1.2 walk (width 36.6 m, running): the mod was switched off (MCT)' in h.log() and len(h.U['calls']) == 2,
               '085e review 8: MCT off mid-battle: %r %s' % (h.U['calls'], h.log()[-900:]))

        # --- 085e cooldown: 3 s, and at most NARROW_FORGETS_MAX (2) forgets per walker in one pass through a standing unit ----------------------------------
        # (085h item 10: while a walker is not narrowed (cooldown, limit) a monstrous cavalry gets the whole-front lane at once; these tests are about the forgets, so that fallback is off here)
        NARROW_PLAIN = NARROW_SCRIPT  # (085i: the whole-front fallback is gone)
        def forgetter(done, gap=2):  # someone else orders the walker again: 3 looks after its 1st narrowing, `gap` looks after its 2nd one
            def hook(h, i, U):
                n = len(U['calls'])
                for k in (1, 2):
                    if n == k and k not in done and i == U['calls'][k - 1]['look'] + (3 if k == 1 else gap):
                        done.add(k)
                        h.u['walk'].update(dict(gx=h.u['walk']['gx'] - 3.5 * k))  # (085g: beyond the settling slack of 3 m)
            return hook
        h = narrow_walk(looks=110, step=0.5, run=True, each=forgetter(set()), reform=False, script=NARROW_PLAIN, z0=-70.0)  # (085g: the walker's bodies stay as they are: the test is about the forgets, not about the column)
        dbg(h, 'NARROW', 'RESTORED')
        text = h.log()
        expect(text.count('NARROWED A1.1.2 walk') == 2 and text.count('NARROW FORGOTTEN A1.1.2 walk') == 2 and ' for 3 s' in text
               and text.count('NARROW LIMIT A1.1.2 walk for A1.1.1 stand: 2 orders of someone else - normal columns until this pass ends') == 1 and len(h.U['calls']) == 2,
               '085e cooldown: after 2 forgets in the pass: the limit, said once, no 3rd narrowing: %r %s' % (len(h.U['calls']), text[-1500:]))
        expect(te(h)['narrow'] is None and 'columns: ' in open_line(h) + text, '085e cooldown: after the limit the normal columns')
        # ... the limit ends with the pass: the walker is through (the stander closed), the counter is gone; a NEW pass through the same stander narrows again
        def advance(h, n, step=0.5):
            for _ in range(n):
                h.u['walk']['z'] += step
                for m, off in h.offs:
                    h.mem.write(m + 0x88, struct.pack('<3f', off[0], 0.0, h.u['walk']['z'] + off[1]))
                h.U['apply']()
                h.tick()
        advance(h, 130)
        expect(te(h)['nfg'] is None, '085e cooldown: the counter is gone when the pass is over')

        h.u['walk'].update(dict(z=-40.0, gx=-1.0))
        advance(h, 45)
        expect(h.log().count('NARROWED A1.1.2 walk') == 3 and h.log().count('NARROW LIMIT') == 1 and h.log().count('NARROW FORGOTTEN A1.1.2 walk') == 2,
               '085e cooldown: a new pass narrows again (the limit was only for the first pass): ' + h.log()[-900:])
        # ... another standing unit is not affected: the forgets counted only for the stander the narrowing was for (the second row is too far to be planned yet)

        def far_row(h):
            models, x = [], -14.5
            while x <= 14.5 + 1e-6:
                for r in range(2):
                    models.append(h.model(x, 170.0 - 1.3 * r, 0.65))
                x += 1.3
            h.set_models('aside', models)
            h.u['aside'].update(dict(x=0.0, z=169.4, gx=0.0, gz=169.4, men=46, width=29.0))
            h.u['walk'].update(dict(gz=280.0))
        h = narrow_walk(looks=80, step=0.5, run=True, before_extra=far_row, each=forgetter(set()), reform=False, script=NARROW_PLAIN, z0=-70.0)  # (085g: the walker's bodies stay as they are: the test is about the forgets, not about the column)
        e = te(h)
        labels = sorted(str(k['label']) for k in e['nfg'].keys()) if e['nfg'] is not None else None
        expect(labels == ['A1.1.1 stand'] and h.log().count('NARROW LIMIT A1.1.2 walk for A1.1.1 stand') == 1 and 'NARROW LIMIT A1.1.2 walk for A1.1.3 aside' not in h.log(),
               '085e cooldown: the limit is for that stander only: %r %s' % (labels, h.log()[-600:]))
        # (D2) the digest: a pass whose walker "goes through the lanes as they are" (a further walker) is OPEN (further), not "OPEN no"
        import log_digest
        evs = log_digest.parse_lanes([(5, 'further walker A1.1.3 inf_handgunners through A1.1.1 inf_halberdiers: not a big walker: it goes through the lanes as they are')])
        expect(len(evs) == 1 and evs[0]['kind'] == 'FURTHER' and evs[0]['wk'] == 'inf_handgunners' and evs[0]['sk'] == 'inf_halberdiers', '085e D2: the digest reads the further-walker line: %r' % (evs,))
        row = log_digest.row_for(dict(id='24a2', start=0, done=10, walker='inf_handgunners', stander='inf_halberdiers', kind='walk'), evs, [dict(id='24a2', start=0, done=10, walker='inf_handgunners', stander='inf_halberdiers', kind='walk')], {}, False)
        expect('OPEN (further)' in row and 'OPEN no' not in row, '085e D2: the pass is OPEN (further): %r' % (row,))

        # (G2) guard_one_lane: when the room for ONE lane cannot be read (make_room throws) there is NO lane (it used to count as success: lanes one file wide)
        g2 = REAL_FIRE.replace('local ok, text = pcall(make_room, s, w, one) -- fresh, for one lane', 'local ok, text = pcall(function() if GUARD_FAIL then error("test") end return make_room(s, w, one) end) -- fresh, for one lane', 1)
        expect(g2 != REAL_FIRE, '085e G2: the hook is in the script')
        h, got = column_walk('inf', 16, 5, 1.2, 0.4, guard=True, stand_step=1.9, script=g2, before=lambda h: h.b['lua'].execute('GUARD_FAIL = true'))
        text = h.log()
        expect(h.lane('stand') > 0 and text.count('GUARD MODE: no room inside the outline of A1.1.1 stand for A1.1.2 walk (room for one lane not readable) - normal lanes instead') == 1,
               '085e G2 / 085f F3: the one-lane room cannot be read: no ONE lane, the normal lanes, one line: ' + text[-1200:])
        h, got = column_walk('inf', 16, 5, 1.2, 0.4, guard=True, stand_step=1.9, script=g2)
        expect(h.lane('stand') > 0 and 'GUARD MODE: ONE lane of ' in h.log(), '085e G2: (control) without the failure the one lane opens: ' + h.log()[-900:])

        # (C) the ghost list of cave 5. Ethereal units of the battle (the game's tables; here 'walk' and 'walk2') are written to the table at Deployed:
        # the entries first, then the count (grow); the count first when it shrinks; zeroed with switch_off (battle complete); the counter starts at 0
        # and is read and reset every 5 s; MCT off / TUNE.GHOST off / something else at the site: the list stays empty
        def ghost_battle(script, **kw):
            h = Fire(script=script, deploy=False, **kw)
            h.mem.write(h.tbl + lanes.GHOST_SKIPS, struct.pack('<I', 1234))
            h.writes = []
            plain = h.mem.write
            h.mem.write = lambda a, raw: (h.writes.append((a - h.tbl, bytes(raw))), plain(a, raw))[1]
            h.b['phases']['Deployed']()
            return h

        def ghosts(h):
            n = struct.unpack('<I', h.mem.read(h.tbl + lanes.GHOSTS, 4))[0]
            return n, [struct.unpack('<Q', h.mem.read(h.tbl + lanes.GHOST_LIST + 8 * i, 8))[0] for i in range(n)]
        h = ghost_battle(ghost_script('walk', 'walk2'))
        n, units = ghosts(h)
        expect(n == 2 and units == [h.u['walk']['at'], h.u['walk2']['at']], '085e C: the ghost list holds the ethereal units: %r' % ((n, units),))
        order = [off for off, raw in h.writes if off in (lanes.GHOSTS, lanes.GHOST_LIST, lanes.GHOST_LIST + 8)]
        expect(order == [lanes.GHOST_LIST, lanes.GHOST_LIST + 8, lanes.GHOSTS], '085e C: grow = the entries first, then the count: %r' % (order,))
        expect(struct.unpack('<Q', h.mem.read(h.tbl + lanes.GHOST_TO, 8))[0] == BASE + lanes.FOUND['ghost_pass'] + lanes.GHOST_SKIP_OFF and struct.unpack('<I', h.mem.read(h.tbl + lanes.GHOST_SKIPS, 4))[0] == 0,
               '085e C: the cave jumps to the end of the pair handler; the counter starts at 0')
        expect('GHOST PASS: 2 ethereal unit(s) go through friendly models: A1.1.2 walk, A1.1.4 walk2' in h.log(), '085e C: the log names them: ' + h.log()[-600:])
        # shrink: a ghost unit leaves the battle (its game object is not the listed one any more): the count is written first, then the entries
        del h.writes[:]
        h.mem.write(h.u['walk2']['at'] + UID_FIELD, struct.pack('<I', 7777))
        h.run(55)
        order = [off for off, raw in h.writes if off in (lanes.GHOSTS, lanes.GHOST_LIST, lanes.GHOST_LIST + 8)]
        n, units = ghosts(h)
        expect(order == [lanes.GHOSTS, lanes.GHOST_LIST] and n == 1 and units[0] == h.u['walk']['at'], '085e C: shrink = the count first, then the entries: %r %r' % (order, (n, units)))
        # the counter: read and reset every 5 s, logged
        h.mem.write(h.tbl + lanes.GHOST_SKIPS, struct.pack('<I', 777))
        h.run(25)
        expect(struct.unpack('<I', h.mem.read(h.tbl + lanes.GHOST_SKIPS, 4))[0] == 0 and 'GHOST: +777 ghost pairs passed in 5 s (777 in all)' in h.log(), '085e C: the counter is read and reset: ' + h.log()[-700:])
        h.mem.write(h.tbl + lanes.GHOST_SKIPS, struct.pack('<I', 2500))
        h.b['phases']['Complete']()
        expect(ghosts(h)[0] == 0 and 'GHOST: 3.3 thousand ghost pairs passed in all' in h.log(), '085e C: zeroed with the end of the battle, the total said in thousands: %r %s' % (ghosts(h), h.log()[-500:]))
        # no ethereal unit: nothing written; MCT off: nothing; TUNE.GHOST off: nothing
        h = ghost_battle(FIRE_SCRIPT)
        expect(ghosts(h) == (0, []) and not any(off in (lanes.GHOSTS, lanes.GHOST_LIST) for off, raw in h.writes), '085e C: no ethereal unit, no list')
        h = ghost_battle(ghost_script('walk'), mct={'military_drill_enabled': False})
        expect(ghosts(h)[0] == 0, '085e C: MCT off: no ghosts')
        h = ghost_battle(ghost_script('walk').replace('GHOST = true,', 'GHOST = false,', 1))
        expect(ghosts(h)[0] == 0 and 'GHOST PASS' not in h.log(), '085e C: TUNE.GHOST off: no ghosts')
        # something else hooks the collision pass: no ghosts and no error, the other caves work
        site = BASE + lanes.FOUND['ghost_pass']
        h = Fire(script=ghost_script('walk'), hooks0={site: UID_CODE})  # (a cave that is not ours: the bytes there differ)
        expect('GHOST PASS OFF: something else hooks the game\'s collision pass' in h.log() and ghosts(h)[0] == 0 and 'MOD OFF' not in h.log() and 'walker columns ON' in h.log() and len(h.hooks) == 5,
               '085e C: another mod at the collision site: no ghosts, no error: ' + h.log()[-900:])
        # the 'ghost_pass' cave itself is the fifth cave in the table, installed once
        h = Fire(script=FIRE_SCRIPT)
        expect(len(h.hooks) == 5 and BASE + lanes.FOUND['ghost_pass'] in h.hooks and h.log().count('cave installed: ghost_pass') == 1, '085e C: five caves, ghost_pass among them')

        # (D1) the models array is read in ONE call and the model pointers / keys / carriers are reused while it is unchanged: no read_pointer on the
        # list per model per look (085d: 30 per look per use). Same places as before; a changed array (a model swapped) or a changed number of men reads anew
        counter = """
            RP = {}
            local mr = memreader_plus
            local rp = mr.read_pointer
            mr.read_pointer = function(p, off) RP[p] = (RP[p] or 0) + 1 return rp(p, off) end
        """
        h = narrow_walk(looks=60, step=0.5, uc=False, before_extra=lambda h: h.b['lua'].execute(counter))
        lst = struct.unpack('<Q', h.mem.read(h.u['walk']['at'] + 0xa8, 8))[0]
        reads = h.b['lua'].eval('RP')[lst] or 0
        st = h.b['lua'].eval('TEST_STATE()')
        expect(reads <= 250 and (st['mlist_hits'] or 0) >= 60, '085e D1: the walker\'s 60 model pointers are read %d times in 60 looks (cache hits %s, misses %s)' % (reads, st['mlist_hits'], st['mlist_misses']))
        # a model swapped in the array: the next look reads anew (the new model's place is there, the old one's is gone)
        entry = te(h)
        old_key = [k for k in entry['places'].keys()][0]
        model = struct.unpack('<Q', h.mem.read(lst, 8))[0]
        new_model = h.model(5.0, 5.0, 1.2)
        h.mem.write(lst, struct.pack('<Q', new_model))
        h.tick()
        entry = te(h)
        keys = set(k for k in entry['places'].keys())
        expect(('%016x' % new_model) in keys and ('%016x' % model) not in keys, '085e D1: a swapped model is read anew')
        # the same number of men and the same array: the places are the same as the models' own places (identical results)
        places = {k: (entry['places'][k][1], entry['places'][k][3]) for k in entry['places'].keys()}
        expect(len(places) == 60 and abs(places[('%016x' % new_model)][0] - 5.0) < 1e-3, '085e D1: the places are the models\' places: %r' % (list(places.items())[:2],))

    if on('pure'):
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
    if on('pure'):
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
        # (fix 4) guard mode (out_max 0): a narrow flank (0.5 m past the middle) stays inside the outline too; the default skips it
        narrow = pts((0, -14), (0, 0.5), (2, 0))
        got = room_of(narrow, pts(), 3.0, 1.2, 0, True)
        expect(got[0] == -0.5, 'guard mode: the narrow flank closes up: %r' % (got,))
        got = room_of(narrow, pts(), 3.0, 1.2, 1.0, True)  # (085c: guard mode steps out 1 m per side: GUARD_OUT; the guard flag, not out_max == 0, decides)
        expect(got[0] == -0.5, 'guard mode, 1 m out: the narrow flank still closes up: %r' % (got,))
        got = room_of(narrow, pts(), 3.0, 1.2, 0)
        expect(abs(got[0] + 3.0 / 14) < 1e-9, 'no guard flag: out_max 0 alone is not guard mode (the narrow flank is skipped, only the 14 m side closes up): %r' % (got,))
        got = room_of(narrow, pts(), 3.0, 1.2, 3.5)
        expect(abs(got[0]) < 1e-9, 'default: the narrow flank (under 1 m) is skipped, the other side has 3.5 m: %r' % (got,))
        # (fix 5) the lane's formation of a body list (monstrous / cavalry) counts the bodies, not the unit's men
        expect('formation(places, w.dx, w.dz, bodies and #places or w.men)' in SCRIPT and 'return list, true' in SCRIPT, 'fix 5: bodies pass their own count')
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
    print('mock test: %d checks, ALL PASS' % checks if QUICK is None else 'mock test (quick: %s): %d checks, QUICK PASS' % (','.join(name for name, _ in GROUPS if name in QUICK), checks))


if __name__ == '__main__':
    main_release() if RELEASE else main()
