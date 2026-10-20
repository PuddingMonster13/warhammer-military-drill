"""Offline test of the automatic scenario (Lua 5.1 through lupa, fake battle with moving units).

    python scenario_mocktest.py <out_dir of 'scenario.py build'>     -> must end with ALL PASS
"""
import math
import os
import sys
import tempfile

import lupa.lua51 as lupa

import scenario

SCRIPT = open(os.path.join(os.path.abspath(sys.argv[1]), scenario.SCRIPT), encoding='utf8').read()
LOG = 'units_scenario_log.txt'
checks = 0


def expect(cond, what):
    global checks
    checks += 1
    assert cond, what


# alliance, name, men, flags, x, z
ARMY = [(1, 'general', 1, ('is_commanding_unit',), 0, -20), (1, 'ironsworn', 125, ('is_infantry',), -30, 0), (1, 'ironsworn', 125, ('is_infantry',), 30, 0),
        (1, 'warriors', 90, ('is_infantry',), 60, 0), (1, 'wolves', 75, ('is_cavalry',), 0, 10), (1, 'destroyer', 1, ('is_war_beasts',), 10, -10),
        (1, 'bolt_thrower', 3, ('is_artillery',), 40, -30), (1, 'swords', 80, ('is_infantry',), -60, 0), (1, 'swords', 80, ('is_infantry',), -90, 0),
        (2, 'enemy', 100, ('is_infantry',), 0, 600)]


def run(army, speed=3.0):
    lua = lupa.LuaRuntime(unpack_returned_tuples=True)
    g = lua.globals()
    units, calls, phases, timers = [], [], {}, {}
    call_ticks, clock = [], [0]
    # run 31: string.find(s, x, 1, true) broke the game's whole Lua state at load: these must never be called
    lua.execute('''
        for _, name in ipairs({ 'find', 'match', 'gmatch', 'gsub', 'lower', 'upper' }) do
            string[name] = function() error('string.' .. name .. ' is not safe in the game\\'s Lua (run 31)', 2) end
        end
    ''')
    lua.execute('''
        function v(x, z) return { get_x = function() return x end, get_y = function() return 0 end, get_z = function() return z end } end
        function make_unit(get, call)
            local u = {}
            function u:unique_ui_id() return get('uid') end
            function u:type() return get('name') end
            function u:number_of_men_alive() return get('men') end
            function u:ordered_width() return get('width') end
            function u:is_moving() return get('moving') end
            function u:position() return v(get('x'), get('z')) end
            function u:ordered_position() return v(get('ox'), get('oz')) end
            function u:ordered_bearing() return get('bearing') end
            function u:is_moving_fast() return get('fast') end
            for _, flag in ipairs({ 'is_infantry', 'is_cavalry', 'is_war_beasts', 'is_artillery', 'is_commanding_unit', 'is_chariot', 'can_fly' }) do
                u[flag] = function() return get(flag) end
            end
            return u
        end
        function make_list(items) return { count = function() return #items end, item = function(_, i) return items[i] end } end
    ''')
    # 1: our army, 2: the enemy's, 3: a second army of alliance 2, 4 / 5: our allies' armies (2nd / 3rd of alliance 1),
    # 6: a third army of alliance 2
    by_alliance = {1: [], 2: [], 3: [], 4: [], 5: [], 6: []}
    for n, unit in enumerate(army):
        alliance, name, men, flags, x, z = unit[:6]
        extra = unit[6] if len(unit) > 6 else {}
        u = dict(uid=100 + n, name=name, men=men, width=float(extra.get('width', 20.0)), moving=False, x=float(x), z=float(z), ox=float(x), oz=float(z), bearing=0.0, fast=False, goal=None, control=False)
        for flag in ('is_infantry', 'is_cavalry', 'is_war_beasts', 'is_artillery', 'is_commanding_unit', 'is_chariot', 'can_fly'):
            u[flag] = flag in flags
        units.append(u)
        by_alliance[alliance].append(g.make_unit(lambda key, u=u: u[key], None))
    ucs = {}

    def create_unitcontroller(unit):
        u = units[unit.unique_ui_id(unit) - 100]
        uc = lua.table()

        def record(kind, *args):
            calls.append((u['uid'], u['name'], kind) + tuple(args))
            call_ticks.append(clock[0])

        def take(self):
            u['control'] = True
            record('take')

        def release(self):
            u['control'] = False
            record('release')

        def halt(self):
            u['goal'] = None  # (it stops where it is)
            record('halt')

        def teleport(self, pos, bearing, width):
            u['x'], u['z'], u['goal'] = pos.get_x(), pos.get_z(), None
            u['ox'], u['oz'], u['bearing'] = pos.get_x(), pos.get_z(), bearing
            record('teleport', round(pos.get_x(), 1), round(pos.get_z(), 1), round(bearing, 1), width)

        def goto(self, pos, bearing, width, run):
            assert u['control'], 'orders only for units under control'
            u['goal'] = (pos.get_x(), pos.get_z())
            u['ox'], u['oz'], u['bearing'] = pos.get_x(), pos.get_z(), bearing
            record('goto', round(pos.get_x(), 1), round(pos.get_z(), 1), round(bearing, 1), width, run)

        uc['take_control'], uc['release_control'], uc['halt'], uc['teleport_to_location'], uc['goto_location_angle_width'] = take, release, halt, teleport, goto

        def behaviour(self, name, on):
            record('behaviour', name, on)

        uc['change_behaviour_active'] = behaviour
        ucs[u['uid']] = uc
        return uc

    g.create_unitcontroller = create_unitcontroller
    lists = []
    make_army = lua.eval('function(units) return { units = function() return units end } end')
    for a, further in ((1, (4, 5)), (2, (3, 6))):
        armies = [make_army(g.make_list(lua.table(*by_alliance[a])))]
        for b in further:
            if by_alliance[b]:
                armies.append(make_army(g.make_list(lua.table(*by_alliance[b]))))
        lists.append(lua.eval('function(armies) return { armies = function() return armies end } end')(g.make_list(lua.table(*armies))))
    battle = lua.eval('function(alliances) return { alliances = function() return alliances end } end')(g.make_list(lua.table(*lists)))
    bm = lua.eval('function(battle) return { battle = battle } end')(battle)
    bm['register_phase_change_callback'] = lambda self, name, f: phases.__setitem__(name, f)
    bm['repeat_callback'] = lambda self, f, ms, name: timers.__setitem__(name, f)
    bm['remove_callback'] = lambda self, name: timers.pop(name, None)
    g.bm = bm
    lua.execute(SCRIPT)

    def tick():
        clock[0] += 1
        for u in units:  # the fake game: units walk to their goal
            if u['goal'] and u['x'] is not None:
                dx, dz = u['goal'][0] - u['x'], u['goal'][1] - u['z']
                d = math.hypot(dx, dz)
                step = (0.6 if u['is_artillery'] else speed) * 0.2
                if d <= step:
                    u['x'], u['z'], u['goal'], u['moving'] = u['goal'][0], u['goal'][1], None, False
                else:
                    u['x'], u['z'], u['moving'] = u['x'] + dx / d * step, u['z'] + dz / d * step, True
        for name in ('units_scenario_tick', 'units_scenario_orders'):
            if name in timers:
                timers[name]()

    return dict(units=units, calls=calls, call_ticks=call_ticks, phases=phases, timers=timers, tick=tick, lua=lua)


def main():
    work = tempfile.mkdtemp()
    os.chdir(work)
    log = lambda: open(LOG, encoding='utf8').read()

    b = run(ARMY)
    expect('battle script loaded' in log() and not b['timers'], 'nothing before the fighting')
    b['phases']['Deployed']()
    n = 0
    while 'units_scenario_tick' in b['timers'] and n < 20000:
        b['tick']()
        n += 1
    text = log()
    expect('SCENARIO FINISHED (all rounds done)' in text and 'ERROR' not in text, text)
    expect(b['lua'].globals().units_scenario_finished == 1, 'the probe is told that the scenario finished')
    expect('roles: A (ironsworn, 125 men), W (ironsworn, 125 men), A2 (swords, 80 men), W2 (swords, 80 men), C (wolves, 75 men), R (bolt_thrower, 3 men), M (destroyer, 1 men), G (general, 1 men)' in text, text)
    expect('STEP 0: all 9 units wait in a row 150 m behind the line, 1 enemy units halted, fire at will off for 10 units of both sides' in text, text)
    expect(sum(1 for c in b['calls'] if c[2:] == ('behaviour', 'fire_at_will', False)) == 10, 'fire at will off for every unit of both sides')
    for name in ('1a', '1b', '1c', '2a', '2b', '2c', '3a', '3b'):
        expect(('PASS %s:' % name) in text and ('PASS %s done' % name) in text, 'pass %s: %s' % (name, text))
    expect('ROUND 1: 3 passes at the same time' in text and 'ROUND 3: 2 passes at the same time' in text, text)  # the mock's enemy is one unit
    expect('PASS 1d skipped' in text, text)
    expect('ROUND 5 skipped: passes of single models are off' in text and 'PASS 5a' not in text and 'PASS 4a skipped' in text, text)
    expect('TIMED OUT' not in text, text)
    expect('ROUND 6 skipped: the lord aura round is off' in text and 'lord NEAR' not in text, text[-2500:])
    calls = b['calls']
    # the enemy is taken and halted; everybody is released at the end
    expect((109, 'enemy', 'take') in calls and (109, 'enemy', 'halt') in calls, calls)
    expect(all(any(c[0] == u['uid'] and c[2] == 'release' for c in calls) for u in b['units']), 'all released')
    expect(not any(u['control'] for u in b['units']), 'no unit stays under script control')
    expect('from now on your orders to your 9 units are written down here' in text and 'units_scenario_orders' in b['timers'], text)
    orders = lambda t: [l for l in t.splitlines() if l[9:].startswith('ORDER ')]
    expect(not orders(text), "the scenario's own orders are not written down as yours")
    # the player orders the monster 80 m to the side, running, after the scenario
    m = b['units'][5]
    m['ox'], m['oz'], m['goal'], m['fast'], m['bearing'] = m['x'] + 80.0, m['z'], (m['x'] + 80.0, m['z']), True, 90.0
    n_calls = len(b['calls'])
    for _ in range(200):
        b['tick']()
    text = log()
    expect('ORDER M destroyer: from ' in text and '(79 m), width 20, facing 90, running' in text, text)
    expect('STOPPED M destroyer at ' in text and '(0.0 m from its destination)' in text, text)
    expect(len(orders(text)) == 1 and len(b['calls']) == n_calls, 'one order line; the script gives no orders any more')
    b['phases']['Complete']()
    expect('battle ended: order log closed' in log() and 'units_scenario_orders' not in b['timers'], log())
    # round 1: the three walkers (W, W2, R) get their orders together, nothing in between
    at_ = [k for k, c in enumerate(calls) if c[2] == 'goto']
    goes = [calls[k] for k in at_]
    # 8 orders in rounds 1-4, and the lord G alone in rounds 7-10 (no vehicle V in this army: 7b-10b skipped)
    expect(len(goes) == 12 and all(c[7] is True for c in goes), goes) # the walkers run
    expect({c[0] for c in goes[:3]} == {102, 108, 106} and at_[2] - at_[0] == 2, 'round 1 walks at once: %r' % goes[:3])
    # the walk order comes 15 s (75 looks) after the round's teleports (the models of a teleported unit arrive about 10
    # s after its position, and a standing unit makes way only after it stood 4 s)
    last_tele = max(k for k in range(at_[0]) if calls[k][2] == 'teleport')
    expect(b['call_ticks'][at_[0]] - b['call_ticks'][last_tele] >= 75, 'settle before the walk: %d looks' % (b['call_ticks'][at_[0]] - b['call_ticks'][last_tele]))
    tele = lambda uid: [c for c in calls if c[0] == uid and c[2] == 'teleport']
    dist = lambda a, b: math.hypot(a[3] - b[3], a[4] - b[4])
    # round 1: A (pass 1a) and C (pass 1b) stand 90 m apart; W walks 40 m beyond A, facing the enemy
    a1, c1 = tele(101)[1], tele(104)[1]
    expect(abs(dist(a1, c1) - 90) < 0.2, (a1, c1))
    b0 = a1[5]
    w_goal = goes[[c[0] for c in goes].index(102)]
    turn = lambda x, y: min(abs(x - y) % 360, 360 - abs(x - y) % 360)
    expect(abs(math.hypot(w_goal[3] - a1[3], w_goal[4] - a1[4]) - 40) < 0.5 and turn(w_goal[5], b0) < 0.5, (w_goal, a1))
    # round 3: A and A2 stand side by side, their fronts (20 m each) 4 m apart
    a3, b3 = tele(101)[3], tele(107)[3]
    expect(abs(dist(a3, b3) - 24) < 0.2, (a3, b3))
    # round 2: W2 walks through A diagonally (45 degrees)
    d2 = goes[3:6][[c[0] for c in goes[3:6]].index(108)]
    expect(turn(d2[5], b0) > 44 and turn(d2[5], b0) < 46, d2)

    # an enemy army of another race: its passes run on their own line 200 m in front of ours
    ogres = [(2, 'bulls', 40, ('is_infantry',), -30, 600), (2, 'bulls', 40, ('is_infantry',), 0, 600), (2, 'gnoblars', 160, ('is_infantry',), 30, 600),
             (2, 'gnoblars', 160, ('is_infantry',), 60, 600), (2, 'mournfang', 12, ('is_cavalry',), 90, 600), (2, 'ironblaster', 4, ('is_artillery',), 120, 600),
             (2, 'ironguts', 16, ('is_infantry',), 150, 600)]
    b = run([u for u in ARMY if u[0] == 1] + ogres)
    b['phases']['Deployed']()
    n = 0
    while 'units_scenario_tick' in b['timers'] and n < 40000:
        b['tick']()
        n += 1
    text = log()
    expect('enemy roles: EA (bulls, 40 men), EW (bulls, 40 men), EA2 (gnoblars, 160 men), EW2 (gnoblars, 160 men), EC (mournfang, 12 men), ER (ironblaster, 4 men)' in text, text[:1500])
    for name in ('1d', '1e', '1f', '2d', '2e', '2f', '3c', '3d', '4d'):
        expect(('PASS %s done' % name) in text, 'enemy pass %s: %s' % (name, text[-3000:]))
    expect('all rounds done' in text and 'TIMED OUT' not in text and 'ERROR' not in text, text[-2000:])
    calls = b['calls']
    ea = [c for c in calls if c[1] == 'bulls' and c[2] == 'teleport']
    oa = [c for c in calls if c[1] == 'ironsworn' and c[2] == 'teleport']
    expect('ROUND 1: 6 passes at the same time' in text and 'ROUND 3: 4 passes at the same time' in text, 'both armies at once: ' + text[-3000:])
    # the enemy's pass 1d stands 200 m in front of our pass 1a (both 90 m to the left of the middle)
    a1 = [c for c in oa if c[0] == 101][1]  # A where it stands in pass 1a
    expect(any(abs(math.hypot(c[3] - a1[3], c[4] - a1[4]) - 200) < 1 for c in ea if c[0] == 109), (ea, a1))

    # a second army of the enemy alliance (Tomb Kings, skeleton spearmen and a chariot): its own line
    tk = [(3, 'tk_lord', 1, ('is_commanding_unit',), 0, 650), (3, 'tk_spears', 100, ('is_infantry',), 20, 650), (3, 'tk_chariot', 8, ('is_chariot',), 40, 650)]
    b = run([u for u in ARMY if u[0] == 1] + ogres + tk)
    b['phases']['Deployed']()
    n = 0
    while 'units_scenario_tick' in b['timers'] and n < 40000:
        b['tick']()
        n += 1
    text = log()
    expect('second enemy army (3 units): infantry TA (tk_spears, 100 men), chariot / war machine TV (tk_chariot, 8 men)' in text, text[:2500])
    expect('PASS 1g done' in text and 'PASS 4g done' in text and 'all rounds done' in text and 'TIMED OUT' not in text, text[-3000:])
    expect('ROUND 1: 7 passes at the same time' in text, text[-3000:])

    # an enemy army of two infantry units and a chariot, no third infantry unit (the chariot's pass 4e through EA2 was
    # skipped): it walks through EA instead
    tombs = [(2, 'skeletons', 100, ('is_infantry',), -30, 600), (2, 'skeletons', 100, ('is_infantry',), 0, 600), (2, 'chariots', 8, ('is_chariot',), 30, 600)]
    b = run([u for u in ARMY if u[0] == 1] + tombs)
    b['phases']['Deployed']()
    n = 0
    while 'units_scenario_tick' in b['timers'] and n < 40000:
        b['tick']()
        n += 1
    text = log()
    expect('PASS 4e: EA2 is missing, EV (chariots, 8 men) walks through EA (skeletons, 100 men) instead' in text and 'PASS 4e done' in text
           and 'all rounds done' in text and 'TIMED OUT' not in text, 'the chariot walks through EA: ' + text[-3000:])

    # TWO big walkers through ONE standing unit (Ikit Claw and a generic Doomwheel, also at an angle)
    b = run([u for u in ARMY if u[0] == 1] + [(1, 'doomwheel', 1, ('is_chariot',), 70, 0)])
    b['phases']['Deployed']()
    n = 0
    while 'units_scenario_tick' in b['timers'] and n < 40000:
        b['tick']()
        n += 1
    text = log()
    for name in ('7a', '7b', '8a', '8b', '9a', '9b', '10a', '10b'):
        expect(('PASS %s done' % name) in text, 'two walkers, pass %s: %s' % (name, text[-3000:]))
    expect('(behind, 7 m beside its middle)' in text and '(behind, -7 m beside its middle)' in text, 'side by side: ' + text[-3000:])
    expect('(behind, 25 m further back)' in text and '(diagonal, 15 m further back)' in text, 'one after the other / at an angle: ' + text[-3000:])
    expect('ROUND 10: 2 passes at the same time' in text and 'all rounds done' in text and 'TIMED OUT' not in text, text[-3000:])
    calls = b['calls']
    g7 = [c for c in calls if c[1] == 'general' and c[2] == 'teleport']
    v7 = [c for c in calls if c[1] == 'doomwheel' and c[2] == 'teleport']
    # round 7: both start 45 m before A, 14 m apart side by side
    starts = [(g, v) for g in g7 for v in v7 if abs(math.hypot(g[3] - v[3], g[4] - v[4]) - 14) < 0.2]
    expect(len(starts) >= 1, 'side by side, 14 m apart: %r %r' % (g7, v7))

    # A BIG BATTLE: our army without cavalry, an allied army of another mod's faction, an allied daemon army (42 m wide
    # units, flying cavalry, many units: its row wraps), and three enemy armies
    ours = [u for u in ARMY if u[0] == 1 and u[1] != 'wolves'] + [(1, 'rat_ogres', 8, (), -120, 0)]
    mixu = [(4, 'admiral', 1, ('is_commanding_unit',), 0, -60), (4, 'deckhands', 113, ('is_infantry',), 30, -60), (4, 'deckhands', 113, ('is_infantry',), 60, -60),
            (4, 'pistols', 100, ('is_infantry',), 90, -60), (4, 'pistols', 100, ('is_infantry',), 120, -60), (4, 'land_ship', 1, ('is_chariot',), 150, -60),
            (4, 'leviathan', 5, (), 180, -60), (4, 'mortar', 3, ('is_artillery',), 210, -60)]
    daemons = [(5, 'herald', 1, ('is_commanding_unit',), 0, -100), (5, 'horrors', 150, ('is_infantry',), 40, -100, {'width': 42.0}),
               (5, 'horrors', 150, ('is_infantry',), 80, -100, {'width': 42.0}), (5, 'daemonettes', 90, ('is_infantry',), 120, -100),
               (5, 'daemonettes', 90, ('is_infantry',), 160, -100), (5, 'drones', 12, ('is_cavalry', 'can_fly'), 200, -100, {'width': 42.0}),
               (5, 'heartseekers', 40, ('is_cavalry',), 240, -100), (5, 'seeker_chariot', 6, ('is_chariot',), 280, -100, {'width': 40.0})]
    daemons += [(5, 'nurglings', 60, ('is_infantry',), 300 + 30 * i, -100, {'width': 30.0}) for i in range(12)]
    hef = [(6, 'hef_spears', 100, ('is_infantry',), 0, 700), (6, 'hef_spears', 100, ('is_infantry',), 30, 700), (6, 'lions', 80, ('is_infantry',), 60, 700),
           (6, 'lions', 80, ('is_infantry',), 90, 700), (6, 'tiranoc', 8, ('is_chariot',), 120, 700)]
    big = ours + mixu + daemons + ogres + tk + hef
    b = run(big)
    b['phases']['Deployed']()
    n = 0
    while 'units_scenario_tick' in b['timers'] and n < 80000:
        b['tick']()
        n += 1
    text = log()
    expect('allied army 2 (8 units): LA (deckhands, 113 men), LW (deckhands, 113 men), LA2 (pistols, 100 men), LW2 (pistols, 100 men), LV (land_ship, 1 men), LMI (leviathan, 5 men)' in text, text[:3000])
    expect('allied army 3 (20 units): KA (horrors, 150 men), KW (horrors, 150 men), KA2 (daemonettes, 90 men), KW2 (daemonettes, 90 men), KV (seeker_chariot, 6 men), KMI (missing), KC (heartseekers, 40 men)' in text, text[:3000])
    expect('third enemy army (5 units): HA (hef_spears, 100 men), HW (hef_spears, 100 men), HA2 (lions, 80 men), HV (tiranoc, 8 men)' in text, text[:3000])
    # our army has no cavalry: C comes from the allies, and not the flying one; our Rat Ogres are MI, the allies'
    # Leviathan MC
    expect('C (heartseekers, 40 men)' in text and 'roles: A (ironsworn, 125 men)' in text, text[:3000])
    expect('big bodies: MI (rat_ogres, 8 men), MC (leviathan, 5 men)' in text and 'drones' not in ''.join(l for l in text.splitlines() if 'PASS' in l), text[:3000])
    for name in ('11a', '11b', '11c', '11d', '11e', '12a', '12b', '12c', '12d', '12e', '12f'):
        expect(('PASS %s done' % name) in text, 'big battle, pass %s: %s' % (name, text[-4000:]))
    expect('PASS 11a: LW (deckhands, 113 men) walks through A (ironsworn, 125 men)' in text and 'PASS 11b: W (ironsworn, 125 men) walks through LA (deckhands, 113 men)' in text
           and 'PASS 11c: KW (horrors, 150 men) walks through LA2 (pistols, 100 men)' in text and 'PASS 12d: KC (heartseekers, 40 men) walks through LA (deckhands, 113 men)' in text, text[-4000:])
    expect('all rounds done' in text and 'TIMED OUT' not in text and 'ERROR' not in text and 'another pass of this round' not in text, text[-3000:])
    # the waiting rows: no unit stands inside another (35 m between the middles let 42 m wide daemons overlap), a long
    # side wraps into rows of at most 900 m; the scenario checks it in the battle too (ROW CHECK)
    calls = b['calls']
    first = {}
    for c in calls:
        if c[2] == 'teleport' and c[0] not in first:
            first[c[0]] = c
    width = {u['uid']: u['width'] for u in b['units']}
    spots = list(first.values())
    f = (math.sin(math.radians(spots[0][5])), math.cos(math.radians(spots[0][5])))  # towards the enemy
    along = lambda c: c[3] * f[0] + c[4] * f[1]
    across = lambda c: c[3] * f[1] - c[4] * f[0]
    clash = [(a[1], c[1]) for i, a in enumerate(spots) for c in spots[i + 1:]
             if abs(along(a) - along(c)) < 20 and abs(across(a) - across(c)) < (width[a[0]] + width[c[0]]) / 2 + 5]
    expect(len(spots) == len(big) and not clash, 'units waiting inside each other: %r (%d of %d placed)' % (clash[:5], len(spots), len(big)))
    expect('ROW CHECK our side: 37 units in 2 rows (the longest' in text and 'at most 900 m planned), 0 pairs inside each other; the farthest from its place 0.0 m' in text, text[:4000])
    expect('ROW CHECK enemy side: 15 units in 3 rows' in text, text[:4000])
    # (run 38, another map: a crash right after the teleports to rows up to ~550 m in front of our middle) further rows
    # go on both sides of the first: ours 110 .. 190 m behind the line, the enemy's 310 .. 430 m in front of it; the
    # plan is logged before the teleports
    plan = [l for l in text.splitlines() if 'ROW PLAN' in l]
    expect(plan and text.index('ROW PLAN') < text.index('STEP 0'), 'the plan before the teleports: ' + text[:3000])
    lo, hi = [float(v) for v in plan[0].split('planned ')[1].split(' m along')[0].split(' .. ')]
    expect(lo >= -191 and hi <= 431, 'rows within the proven depth: %s' % plan[0])
    # a waiting unit of an allied army is taken over and halted again each round, not only once at the start
    herald = [u['uid'] for u in b['units'] if u['name'] == 'herald'][0]
    holds = [c for c in calls if c[0] == herald and c[2] in ('take', 'halt')]
    expect(sum(1 for c in holds if c[2] == 'halt') >= 10 and sum(1 for c in holds if c[2] == 'take') >= 10, 'the allied herald held each round: %r' % holds[:6])
    longest = [l for l in text.splitlines() if 'ROW CHECK our side' in l][0]
    expect(int(longest.split('the longest ')[1].split(' m')[0]) <= 900, longest)

    # an army without cavalry and monster: those steps are skipped, the rest runs
    b = run([u for u in ARMY if u[1] not in ('wolves', 'destroyer')])
    b['phases']['Deployed']()
    n = 0
    while 'units_scenario_tick' in b['timers'] and n < 20000:
        b['tick']()
        n += 1
    text = log()
    expect('PASS 1b skipped' in text and 'PASS 2b skipped' in text and 'PASS 1a done' in text and 'PASS 3b done' in text and 'all rounds done' in text, text)
    # an error in a step stops the scenario and gives the control back
    b = run(ARMY)
    b['phases']['Deployed']()
    for _ in range(70):  # into the walk of step 1
        b['tick']()
    b['units'][2]['x'] = None  # the walker's position breaks
    for _ in range(400):
        if 'units_scenario_tick' not in b['timers']:
            break
        b['tick']()
    text = log()
    expect('SCENARIO ERROR' in text and 'stopped by an error' in text and not any(u['control'] for u in b['units']), text)
    print('mock test: %d checks, ALL PASS' % checks)


if __name__ == '__main__':
    main()
