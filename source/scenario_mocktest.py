"""Offline test of the automatic scenario (Lua 5.1 through lupa, fake battle with moving units).

    python scenario_mocktest.py <out_dir of 'scenario.py build'>     -> must end with ALL PASS
"""
import math
import os
import sys
import tempfile

import lupa.lua51 as lupa

import scenario

SCRIPT_DEFAULT = open(os.path.join(os.path.abspath(sys.argv[1]), scenario.SCRIPT), encoding='utf8').read()
# the default is ALL rounds with the single-model passes on; most old checks were written with the singles off, and the
# checks of rounds 19 - 25 and of 4 / 13 - 18 each with their own selector
ONLY_LINE = 'local ONLY_ROUNDS = nil'
SINGLES_LINE = 'local INCLUDE_SINGLES = true'
assert SCRIPT_DEFAULT.count(ONLY_LINE) == 1 and SCRIPT_DEFAULT.count(SINGLES_LINE) == 1
SCRIPT = SCRIPT_DEFAULT.replace(SINGLES_LINE, 'local INCLUDE_SINGLES = false')
# (the selector the checks of rounds 4 and 13 - 18 were written for)
SCRIPT_OLD_DEFAULT = SCRIPT.replace(ONLY_LINE, 'local ONLY_ROUNDS = { [4] = true, [13] = true, [14] = true, [15] = true, [16] = true, [17] = true, [18] = true, [27] = true }')
# (and the one of rounds 19 - 25)
SCRIPT_LATE = SCRIPT.replace(ONLY_LINE, 'local ONLY_ROUNDS = { [19] = true, [20] = true, [21] = true, [22] = true, [23] = true, [24] = true, [25] = true, [26] = true, [27] = true, [28] = true, [29] = true }')
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


def run(army, speed=3.0, script=None):
    lua = lupa.LuaRuntime(unpack_returned_tuples=True)
    g = lua.globals()
    units, calls, phases, timers = [], [], {}, {}
    call_ticks, clock = [], [0]
    # run 31: string.find(s, x, 1, true) broke the game's whole Lua state at load: these must never be called
    lua.execute('''
        for _, name in ipairs({ 'find', 'match', 'gmatch', 'gsub', 'lower', 'upper' }) do
            string[name] = function() error('string.' .. name .. ' is not safe in the game\\'s Lua (run 31)', 2) end
        end
        -- run 58: math.huge is NIL in the game's Lua: any use must fail here too
        math.huge = nil
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
            function u:is_behaviour_active(name) return get('defend') end
            function u:missile_range() return get('range') end
            function u:ammo_left() return get('ammo') end
            function u:missile_range() return 120 end
            UNITS = UNITS or {}
            UNITS[get('uid')] = u
            function u:current_target()
                local t = get('target')
                if t == 'error' then error('no target for you') end
                return t and UNITS[t] or nil
            end
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
        u = dict(defend=bool(extra.get('guard', False)), uid=100 + n, name=name, men=men, width=float(extra.get('width', 20.0)), moving=False, ammo=10, range=float(extra.get('range', 0.0)), target=None, no_attack=bool(extra.get('no_attack', False)), x=float(x), z=float(z), ox=float(x), oz=float(z), bearing=0.0, fast=False, goal=None, control=False)
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
            if name == 'defend':
                u['defend'] = on
            record('behaviour', name, on)

        uc['change_behaviour_active'] = behaviour

        def attack(self, target):
            if u['no_attack']:
                raise RuntimeError('no attack_unit')
            record('attack', target.unique_ui_id(target))

        uc['attack_unit'] = attack
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
    lua.execute(script or SCRIPT)

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
        for name in ('units_scenario_tick', 'units_scenario_orders', 'units_scenario_targets'):
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
    # (10 at the start, one more when the gun of round 23 stops firing, one more for round 29 (085d: the same pass with a second target and an
    # intruder, which this roster lacks - they are optional))
    expect(sum(1 for c in b['calls'] if c[2:] == ('behaviour', 'fire_at_will', False)) == 12, 'fire at will off for every unit of both sides')
    # round 23: the bolt thrower (R, uid 106) stands 40 m behind A (uid 101) and fires at the enemy unit (EA, uid 109) 150 m from it for 30 s
    expect('PASS 23a: R fires at EA through A' in text and 'PASS 23a done after 30.0 s: fire at will off again' in text and 'PASS 23b skipped' in text, text[-3000:])
    gun_calls = [c[2:] for c in b['calls'] if c[0] == 106 and c[2] in ('attack', 'behaviour') and (c[2] == 'attack' or c[3] == 'fire_at_will')]
    expect(gun_calls[-3:] == [('behaviour', 'fire_at_will', True), ('attack', 109), ('behaviour', 'fire_at_will', False)], 'the gun fires: %r' % gun_calls)
    line = [l for l in text.splitlines() if 'PASS 23a: R fires at EA through A' in l][0]
    near = lambda a, b: math.hypot(a[0] - b[0], a[1] - b[1])
    point = lambda key: [float(v) for v in line.split(key)[1].split(',')[0].split(' m ')[0].split(' / ')]
    stand_pt, gun_pt, target_pt = point('stands in its line at '), point('the gun at '), point('the target at ')
    expect(abs(near(stand_pt, gun_pt) - 40) < 1.5 and abs(near(gun_pt, target_pt) - 150) < 1.5 and abs(near(stand_pt, target_pt) - 110) < 1.5, 'the layout of the line of fire: %s' % line)
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
    # artillery crews rejoin their guns after every teleport of the bolt thrower (uid 106, the only crewed unit): an order to its own spot 0.5 m
    # back with its bearing 3 looks later (no run), a halt later; infantry gets none
    regroups = [(k, c) for k, c in enumerate(b['calls']) if c[0] == 106 and c[2] == 'goto' and c[7] is False]
    teleports = [(k, c) for k, c in enumerate(b['calls']) if c[0] == 106 and c[2] == 'teleport']
    expect(len(regroups) >= 8 and text.count('REGROUP R (bolt_thrower): ordered 0.5 m back to its own spot at ') == len(regroups) and 'REGROUP A' not in text and not any(c[0] != 106 and c[2] == 'goto' and c[7] is False for c in b['calls']),
           'one regroup per teleport of the gun, none for infantry: %d / %s' % (len(regroups), text[-1500:]))
    for k, c in regroups:
        before = [(j, t) for j, t in teleports if j < k][-1]
        halts = [j for j, h in enumerate(b['calls']) if h[0] == 106 and h[2] == 'halt' and j > k]
        expect(abs(math.hypot(c[3] - before[1][3], c[4] - before[1][4]) - 0.5) < 0.1 and abs(c[5] - before[1][5]) < 0.1 and b['call_ticks'][k] - b['call_ticks'][before[0]] == 3
               and halts and b['call_ticks'][halts[0]] - b['call_ticks'][k] == 15, 'regroup %r after teleport %r' % (c, before[1]))
        # (fix 1) no newer order (a walk, an attack) of the gun between the regroup order and its halt: the regroup is dropped by it, not halted over it
        expect(not any(h[0] == 106 and h[2] in ('goto', 'attack') and h is not c for h in b['calls'][k + 1:halts[0]]), 'a newer order of the gun inside its regroup window %r' % (b['calls'][k:halts[0] + 1],))
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
    # the target log (read only): the bolt thrower (uid 106) is the only artillery unit; changes only
    expect('the targets of the 1 artillery units of both sides are written down' in text and 'units_scenario_targets' in b['timers'], text[-1500:])
    gun, foe = b['units'][6], b['units'][9]
    count = lambda: sum(1 for l in log().splitlines() if l[9:].startswith('TARGET R bolt_thrower: '))
    for _ in range(12):
        b['tick']()
    expect(count() == 1 and 'TARGET R bolt_thrower: nil; ammo 10, missile range 120' in log(), 'first look: no target: ' + log()[-800:])
    for _ in range(30):
        b['tick']()
    expect(count() == 1, 'nothing logged while the answer does not change')
    gun['target'] = foe['uid']
    for _ in range(6):
        b['tick']()
    expect(count() == 3 and 'TARGET R bolt_thrower: table, target #109 enemy, ' in log() and ' m; ammo 10, missile range 120' in log()
           and 'TARGET R bolt_thrower: first result ' in log(), 'a target: ' + log()[-800:])
    for _ in range(30):
        b['tick']()
    foe['x'] += 5.0  # (it moves, the answer is the same unit: no new line)
    for _ in range(10):
        b['tick']()
    expect(count() == 3, 'same target: no new line')
    gun['target'] = 'error'
    for _ in range(6):
        b['tick']()
    expect(count() == 4 and 'TARGET R bolt_thrower: error (' in log() and 'no target for you' in log(), 'an erroring current_target: ' + log()[-800:])
    gun['target'] = foe['uid']
    for _ in range(6):
        b['tick']()
    method = b['lua'].globals().UNITS[gun['uid']]['current_target']
    b['lua'].globals().UNITS[gun['uid']]['current_target'] = None  # the method is missing
    for _ in range(6):
        b['tick']()
    expect(count() == 6 and 'TARGET LOG STOPPED' not in log() and 'attempt to call' in log(), 'a missing current_target: ' + log()[-800:])
    b['lua'].globals().UNITS[gun['uid']]['current_target'] = method
    # the limit: at most 200 lines, then one line and the timer stops
    for k in range(260):
        gun['target'] = foe['uid'] if k % 2 else 'error'
        for _ in range(5):
            b['tick']()
    expect(log().count('TARGET LOG: limit reached') == 1 and 'units_scenario_targets' not in b['timers'] and 'TARGET LOG STOPPED' not in log(), 'limit: ' + log()[-800:])
    expect(sum(1 for l in log().splitlines() if l[9:].startswith('TARGET R ')) == 200, 'exactly 200 target lines')
    b['phases']['Complete']()
    expect('battle ended: order log closed' in log() and 'units_scenario_orders' not in b['timers'], log())
    # round 1: the three walkers (W, W2, R) get their orders together, nothing in between
    at_ = [k for k, c in enumerate(calls) if c[2] == 'goto' and c[7] is True]  # (the regroup orders of the artillery walk, not run)
    goes = [calls[k] for k in at_]
    # 8 orders in rounds 1-4, the lord G alone in rounds 7-10 (no vehicle V in this army: 7b-10b skipped), and 4 in
    # rounds 15 / 16 (guarded W through A, C through W2; W through A, W2 through A2); no fast big bodies or ghosts here;
    # and 8 in rounds 19 - 22: 2 walkers away (19a, 19b), 2 walkers that turn back (20a, 20b) and are ordered back
    # once each, 21a, 22a; 1 in round 24 (the bolt thrower walks through A2: 24b); 2 in round 25 (25a, 25b)
    expect(len(goes) == 28, goes) # the walkers run (28: 27 + the one of 26a)
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
    # (the enemy's first army has no chariot: the second army's goes to EV - a unit has one role - so TV is missing)
    expect('role EV from enemy army 2: V (tk_chariot, 8 men)' in text and 'second enemy army (3 units): infantry TA (tk_spears, 100 men), chariot / war machine TV (missing)' in text and 'enemy roles:' in text, text[:2500])
    expect('ROUND 23: 2 passes at the same time' in text and 'PASS 23a done' in text and 'PASS 23b: ER fires at A2 through EA2' in text and 'PASS 23b done' in text, 'round 23, both sides: ' + text[-3000:])
    expect('PASS 24b done' in text and 'PASS 24d done' in text and 'PASS 24a skipped' in text, 'round 24, artillery walks through a unit: ' + text[-3000:])
    expect('PASS 1g skipped' in text and 'PASS 4g skipped' in text and 'PASS 4e: EV (tk_chariot, 8 men) walks through EA2' in text and 'PASS 4e done' in text and 'all rounds done' in text and 'TIMED OUT' not in text, text[-3000:])
    expect('ROUND 1: 6 passes at the same time' in text, text[-3000:])

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
    # (a role the first army lacks is LENT from the next army and that unit has no role of its own army any more: the allies
    # get a second ship, Leviathan, Heartseeker unit and the Tomb Kings a second chariot, so that rounds 11 / 12 still have theirs)
    mixu += [(4, 'land_ship', 1, ('is_chariot',), 240, -60), (4, 'leviathan', 5, (), 270, -60)]
    daemons += [(5, 'heartseekers', 40, ('is_cavalry',), 660, -100)]
    big = ours + mixu + daemons + ogres + tk + [(3, 'tk_chariot', 8, ('is_chariot',), 70, 650)] + hef
    b = run(big)
    b['phases']['Deployed']()
    n = 0
    while 'units_scenario_tick' in b['timers'] and n < 80000:
        b['tick']()
        n += 1
    text = log()
    expect('allied army 2 (10 units): LA (deckhands, 113 men), LW (deckhands, 113 men), LA2 (pistols, 100 men), LW2 (pistols, 100 men), LV (land_ship, 1 men), LMI (leviathan, 5 men)' in text, text[:3000])
    expect('allied army 3 (21 units): KA (horrors, 150 men), KW (horrors, 150 men), KA2 (daemonettes, 90 men), KW2 (daemonettes, 90 men), KV (seeker_chariot, 6 men), KMI (missing), KC (heartseekers, 40 men)' in text, text[:3000])
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
    expect('ROW CHECK our side: 40 units in 2 rows (the longest' in text and 'at most 900 m planned), 0 pairs inside each other; the farthest from its place 0.5 m' in text, text[:4000])  # (0.5: the regroup of the three guns)
    expect('ROW CHECK enemy side: 16 units in 3 rows' in text, text[:4000])
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

    # an army without cavalry and monster: those steps are skipped, the rest runs (the gun cannot be ordered to attack: the pass goes on)
    b = run([u[:6] + ({'no_attack': True},) if u[1] == 'bolt_thrower' else u for u in ARMY if u[1] not in ('wolves', 'destroyer')])
    b['phases']['Deployed']()
    n = 0
    while 'units_scenario_tick' in b['timers'] and n < 20000:
        b['tick']()
        n += 1
    text = log()
    expect('PASS 1b skipped' in text and 'PASS 2b skipped' in text and 'PASS 1a done' in text and 'PASS 3b done' in text and 'all rounds done' in text, text)
    expect('attack_unit FAILED (' in text and 'fire at will on worked' in text and 'PASS 23a done' in text and 'ERROR' not in text, 'a gun that cannot attack: ' + text[-2000:])
    # DEFAULT ONLY_ROUNDS (rounds 4, 13 - 18 only), fast big bodies, ghosts and guard mode. Real unit keys (the kinds, the
    # speeds and the ghosts come from the game's tables): demigryph knights 7.5 m/s then great stag knights 9.0 m/s (both
    # monstrous cavalry), plague ogres 4.8 then rat ogres 6.0 (monstrous infantry), Hexwraiths (ethereal cavalry)
    demi, stag = 'wh_main_emp_cav_demigryph_knights_0', 'wh2_dlc16_wef_cav_great_stag_knights_0'
    plague, rats, ghost = 'wh3_dlc25_nur_inf_plague_ogres', 'wh2_main_skv_mon_rat_ogres', 'wh2_dlc09_tmb_cav_hexwraiths'
    mine = [u for u in ARMY if u[0] == 1]
    mine[1] = mine[1][:6] + ({'guard': True},)  # A starts in guard mode
    mine += [(1, demi, 8, ('is_cavalry',), 100, 0), (1, stag, 8, ('is_cavalry',), 120, 0), (1, plague, 6, (), 140, 0), (1, rats, 6, (), 160, 0),
             (1, ghost, 20, ('is_cavalry',), 180, 0)]
    foes = [(2, 'bulls', 40, ('is_infantry',), -30, 600), (2, 'bulls', 40, ('is_infantry',), 0, 600), (2, 'gnoblars', 160, ('is_infantry',), 30, 600),
            (2, 'gnoblars', 160, ('is_infantry',), 60, 600), (2, demi, 8, ('is_cavalry',), 90, 600), (2, stag, 8, ('is_cavalry',), 120, 600),
            (2, plague, 6, (), 150, 600), (2, rats, 6, (), 180, 600), (2, ghost, 20, ('is_cavalry',), 210, 600)]
    b = run(mine + foes, script=SCRIPT_OLD_DEFAULT)
    b['phases']['Deployed']()
    n = 0
    while 'units_scenario_tick' in b['timers'] and n < 40000:
        b['tick']()
        n += 1
    text = log()
    expect('all rounds done' in text and 'ERROR' not in text and 'TIMED OUT' not in text, text[-3000:])
    # the default selector: only rounds 4 and 13 - 18 run
    expect('ROUND 1 skipped: not in ONLY_ROUNDS' in text and 'ROUND 12 skipped: not in ONLY_ROUNDS' in text and 'PASS 1a:' not in text and 'PASS 12a' not in text, text[:3000])
    expect('PASS 4a:' in text and 'PASS 13a done' in text and 'PASS 14a done' in text and 'PASS 15a done' in text and 'PASS 16a done' in text, text[-3000:])
    # (L) round 27: the fast monstrous cavalry crosses A off-centre in guard mode (27a) and EA off-centre normally (27b)
    expect('PASS 27a done' in text and 'PASS 27b done' in text and '(behind, 9 m beside its middle)' in text and '(behind, -9 m beside its middle)' in text, 'round 27: ' + text[-2500:])
    expect('guard mode on (read back: yes)' in [l for l in text.splitlines() if 'PASS 27a:' in l][0] and 'guard mode off (read back: no)' in [l for l in text.splitlines() if 'PASS 27b:' in l][0], 'guard mode of round 27')
    # (2) the faster of two monstrous cavalry units is FMC (the stag, listed after the demigryph), likewise FMI
    expect('fast big bodies / ethereal: FMC (%s, 8 men) 9.0 m/s, FMI (%s, 6 men) 6.0 m/s, X (%s, 20 men)' % (stag, rats, ghost) in text, text[:3000])
    expect('enemy FMC' not in text and '; enemy EFMC (%s, 8 men) 9.0 m/s, EFMI (%s, 6 men) 6.0 m/s, EX (%s, 20 men)' % (stag, rats, ghost) in text, text[:3000])
    expect('PASS 13a: FMC (%s, 8 men) walks through A' % stag in text and 'PASS 13b: FMI (%s, 6 men) walks through A2' % rats in text, text[-3000:])
    # (1) the ghost unit has only the role X: not C (cavalry), nor A / W / MC / MI
    ghost_lines = [l[9:] for l in text.splitlines() if ghost in l and 'PASS' in l]
    expect(ghost_lines and all(l.startswith('PASS 17') or l.startswith('PASS 18') for l in ghost_lines), ghost_lines)
    expect('roles: A (' in text and 'C (wolves, 75 men)' in text and 'MI (%s, 6 men), MC (%s, 8 men)' % (plague, demi) in text, text[:3000])
    # (4) rounds 17 / 18: the ghost walks through infantry; infantry walks through a standing ghost unit
    expect('ROUND 17: 2 passes at the same time' in text and 'ROUND 18: 2 passes at the same time' in text, text[-3000:])
    for name in ('17a', '17c', '18a', '18b'):
        expect('PASS %s done' % name in text, 'ghost pass %s: %s' % (name, text[-3000:]))
    expect('PASS 17a: X (%s, 20 men) walks through A' % ghost in text and 'PASS 18a: W (ironsworn, 125 men) walks through X (%s, 20 men)' % ghost in text
           and 'PASS 17c: EX (' in text and 'PASS 18b: EW (' in text and 'walks through EX (%s' % ghost in text, text[-3000:])
    # (3) guard mode: a guarded pass turns 'defend' on for its stander and logs it; an unguarded one turns it off for a stander that
    # started in guard mode (A, uid 101: pass 13a is unguarded, 15a guarded); parked units get it off as well
    plines = {l[9:].split(':')[0]: l for l in text.splitlines() if l[9:].startswith('PASS ') and ' walks through ' in l}
    expect('guard mode off (read back: no)' in plines['PASS 13a'] and 'guard mode on (read back: yes)' in plines['PASS 15a'], (plines['PASS 13a'], plines['PASS 15a']))
    expect('guard mode on (read back: yes)' in plines['PASS 16b'] and 'guard mode off (read back: no)' in plines['PASS 4a'], (plines['PASS 16b'], plines['PASS 4a']))
    states = [c[4] for c in b['calls'] if c[0] == 101 and c[2] == 'behaviour' and c[3] == 'defend']
    expect(states and states[0] is False and True in states and False in states[states.index(True):], states)
    expect(any(c[2:] == ('behaviour', 'defend', False) and c[0] == 100 for c in b['calls']), 'a parked unit gets guard mode off')
    # (5) a close start: lag -15 puts the walker 30 m from the stander (45 m in pass 13a)
    def gap(line):
        start = line.split('from ')[1].split(' to ')[0].split(' / ')
        stand = line.split(' stands at ')[1].split(',')[0].split(' / ')
        return math.hypot(float(start[0]) - float(stand[0]), float(start[1]) - float(stand[1]))
    expect(abs(gap(plines['PASS 14a']) - 30) < 1 and abs(gap(plines['PASS 14b']) - 30) < 1 and abs(gap(plines['PASS 13a']) - 45) < 1,
           (plines['PASS 14a'], plines['PASS 14b'], plines['PASS 13a']))
    expect('-15 m further back' in plines['PASS 14a'], plines['PASS 14a'])
    # the unit that holds FMC also holds MC / C; MC (demigryph) is still the walker of pass 4c
    expect('PASS 4c: MC (%s, 8 men)' % demi in text, text[-3000:])
    # the default selector: only rounds 19 - 22 run (a walker that walks away, one that turns back, two lines)
    # (two missile infantry units a side: round 24, the second 10 m behind the first)
    shooters = [(1, 'wh_main_emp_inf_handgunners', 40, ('is_infantry',), 200, 0, {'range': 100.0}), (1, 'wh_main_emp_inf_crossbowmen', 40, ('is_infantry',), 230, 0, {'range': 100.0})]
    foe_shooters = [(2, 'wh2_main_hef_inf_archers_0', 40, ('is_infantry',), 240, 600, {'range': 100.0}), (2, 'wh2_main_hef_inf_lothern_sea_guard_0', 40, ('is_infantry',), 270, 600, {'range': 100.0})]
    b = run(mine + shooters + foes + foe_shooters, script=SCRIPT_LATE)
    b['phases']['Deployed']()
    n = 0
    while 'units_scenario_tick' in b['timers'] and n < 40000:
        b['tick']()
        n += 1
    text = log()
    expect('all rounds done' in text and 'ERROR' not in text and 'TIMED OUT' not in text, text[-3000:])
    expect('ROUND 4 skipped: not in ONLY_ROUNDS' in text and 'ROUND 18 skipped: not in ONLY_ROUNDS' in text and 'PASS 4a:' not in text and 'PASS 13a' not in text, text[:3000])
    for number, count in ((19, 4), (20, 4), (21, 2), (22, 2), (23, 1), (24, 5)):
        expect('ROUND %d: %d passes at the same time' % (number, count) in text, 'round %d: %s' % (number, text[-3000:]))
    for name in ('19a', '19b', '19c', '19d', '20a', '20b', '20c', '20d', '21a', '21b', '22a', '22b', '23a', '24a', '24a2', '24b', '24c', '24c2'):
        expect('PASS %s done' % name in text, 'pass %s: %s' % (name, text[-3000:]))
    plines = {l[9:].split(':')[0]: l for l in text.splitlines() if l[9:].startswith('PASS ') and ' walks through ' in l}
    # round 24: the missile infantry (roles MS / MS2, not A / W) run through A, the second starting 10 m behind the first; the gun walks
    expect('missile infantry: MS (wh_main_emp_inf_handgunners, 40 men), MS2 (wh_main_emp_inf_crossbowmen, 40 men); enemy EMS (wh2_main_hef_inf_archers_0, 40 men), EMS2 (wh2_main_hef_inf_lothern_sea_guard_0, 40 men)' in text,
           'the missile infantry roles: ' + text[:3000])
    expect('PASS 24d skipped' in text and 'PASS 24a: MS (wh_main_emp_inf_handgunners' in text and 'PASS 24a2: MS2 (' in text, 'round 24 passes: ' + text[-3000:])
    starts = lambda name: [float(v) for v in plines['PASS ' + name].split('from ')[1].split(' to ')[0].split(' / ')]
    plines = {l[9:].split(':')[0]: l for l in text.splitlines() if l[9:].startswith('PASS ') and ' walks through ' in l}
    expect(abs(math.hypot(starts('24a')[0] - starts('24a2')[0], starts('24a')[1] - starts('24a2')[1]) - 10) < 1.5 and '10 m further back' in plines['PASS 24a2'], 'the second one starts 10 m behind: %s' % plines['PASS 24a2'])
    # (085d, D-a) the second walker's GOAL lies 10 m short of the first one's and across by the first walker's width (20 m) + 4 m: MS2 does not end inside MS
    def second_goal(first, second):
        a, b = plines['PASS ' + first], plines['PASS ' + second]
        bearing = math.radians(float(a.split(', bearing ')[1].split(';')[0]))
        d = (math.sin(bearing), math.cos(bearing))
        g1, g2 = [float(v) for v in a.split(' to ')[1].split(',')[0].split(' / ')], [float(v) for v in b.split(' to ')[1].split(',')[0].split(' / ')]
        along = (g2[0] - g1[0]) * d[0] + (g2[1] - g1[1]) * d[1]
        across = (g2[0] - g1[0]) * d[1] - (g2[1] - g1[1]) * d[0]
        return along, across, b
    for first, second in (('24a', '24a2'), ('24c', '24c2')):
        along, across, line = second_goal(first, second)
        expect(abs(along + 10) < 1.5 and abs(across - 24) < 1.5 and "its goal lies 10 m short of" in line and '24 m across from it (that walker is 20 m wide)' in line, 'D-a: the goal of %s: %.1f m along, %.1f m across of the first one: %s' % (second, along, across, line))
    for first, second in (('28a', '28a2'), ('28b', '28b2')):
        along, across, line = second_goal(first, second)
        expect(abs(along + 10) < 1.5 and abs(across - 24) < 1.5, 'D-a: the goal of %s (the starts are 24 m apart: -12 / 12): %.1f / %.1f' % (second, along, across))
    # (085d) round 29: the gun on two near targets (second, first, second, first, second) and an enemy unit sent at the stander after 16 s
    expect('ROUND 29: 1 passes at the same time' in text and 'PASS 29a done' in text and text.count('PASS 29a: R ordered to attack EW2 (worked)') == 3 and text.count('PASS 29a: R ordered to attack EA (worked)') == 2
           and 'PASS 29a: EW sent to walk at A (the lane for the line of fire must close when it is 15 m away)' in text, 'round 29: ' + text[-2500:])
    flips = [c[3] for c in b['calls'] if c[0] == 106 and c[2] == 'attack']
    expect(len(flips) == 7 and flips[0] == flips[1] and flips[2:] == [flips[2], flips[1], flips[2], flips[1], flips[2]] and flips[2] != flips[1], 'the gun: round 23 at EA, round 29 at EA, then second, first, second, first, second: %r' % (flips,))
    expect(plines['PASS 24b'].count('bolt_thrower') == 1 and abs(math.hypot(*[float(a) - float(b) for a, b in zip(plines['PASS 24b'].split('from ')[1].split(' to ')[0].split(' / '), plines['PASS 24b'].split(' stands at ')[1].split(',')[0].split(' / '))]) - 40) < 1.5, 'the gun walks 40 m back (slow): %s' % plines['PASS 24b'])
    # round 25: orders that end short of / past / inside the stander, a walker re-ordered sideways, a stander re-ordered back
    expect('ROUND 25: 9 passes at the same time' in text and 'PASS 25j skipped' in text, 'round 25: ' + text[-3000:])
    for name in ('25a', '25b', '25c', '25d', '25e', '25f', '25g', '25h', '25i'):
        expect('PASS %s done' % name in text, 'pass %s: %s' % (name, text[-3000:]))
    goal_note = lambda name: plines['PASS ' + name].split('the walker is ordered to stop ')[1]
    expect(goal_note('25a').startswith('27 m before the stander') and "its front 5 m before the stander's front" in goal_note('25a'), goal_note('25a'))
    expect(" behind the stander's middle (its rear 3 m behind the stander's back rank" in goal_note('25b'), goal_note('25b'))
    expect(goal_note('25c').startswith('0 m at the stander') and "its middle at the stander's middle" in goal_note('25c'), goal_note('25c'))
    # (the goals: before the stander's middle, behind it, at it: from the logged positions)
    def where(name):
        line = plines['PASS ' + name]
        start = [float(v) for v in line.split('from ')[1].split(' to ')[0].split(' / ')]
        goal = [float(v) for v in line.split(' to ')[1].split(',')[0].split(' / ')]
        stand = [float(v) for v in line.split(' stands at ')[1].split(',')[0].split(' / ')]
        way = [goal[0] - start[0], goal[1] - start[1]]
        length = math.hypot(*way)
        return ((stand[0] - start[0]) * way[0] + (stand[1] - start[1]) * way[1]) / length - length  # the stander's middle ahead of the goal (> 0: the goal is before it)
    expect(where('25a') > 20 and where('25b') < -5 and abs(where('25c')) < 2, 'goals: %r %r %r' % (where('25a'), where('25b'), where('25c')))
    expect('PASS 25d: MS2 RE-ORDERED 30 m to the side (90 deg) while its front is inside MI' in text and 'PASS 25i: EMS2 RE-ORDERED' in text, 'the sideways order: ' + text[-3000:])
    expect('PASS 25e: MC (the stander) ORDERED 20 m back while the walker G is' in text, 'the stander is ordered back: ' + text[-3000:])
    # round 26: W parks inside A (its order ends at A's middle), then a foe is put 12 m beyond it and W is ordered to attack it; the pass
    # goes on 12 s after the order
    expect('PASS 26a: W parked inside A, an enemy unit (' in text and 'attack_unit worked' in text and 'PASS 26a done' in text and 'PASS 26b: EW parked inside EA, an enemy unit (general, 1 men) put 12 m beyond it' in text and 'PASS 26b done' in text, 'round 26: ' + text[-2500:])
    att = [(k, c) for k, c in enumerate(b['calls']) if c[0] == 102 and c[2] == 'attack']
    expect(len(att) == 1, 'W is ordered to attack the enemy unit once: %r' % att)
    done26 = [l for l in text.splitlines() if 'PASS 26a done after' in l][0]
    expect(float(done26.split('done after ')[1].split(' s')[0]) >= 12, 'the pass goes on 12 s after the attack order: ' + done26)
    # round 28: two missile units side by side (12 m each side of A's middle), the second 10 m later
    expect('PASS 28a done' in text and 'PASS 28a2 done' in text and 'PASS 28b done' in text and 'ROUND 28: 4 passes at the same time' in text and '(behind, -12 m beside its middle)' in text
           and '(behind, 12 m beside its middle, 10 m further back)' in text, 'round 28: ' + text[-2500:])
    calls = b['calls']
    stands = lambda line: [float(v) for v in line.split(' stands at ')[1].split(',')[0].split(' / ')]
    starts = lambda line: [float(v) for v in line.split('from ')[1].split(' to ')[0].split(' / ')]
    goals = lambda line: [float(v) for v in line.split(' to ')[1].split(',')[0].split(' / ')]
    # 19: the walker starts 12 m from the stander's middle and walks 40 m further away, facing away (bearing 180 or 0)
    for name in ('19a', '19b', '19c', '19d'):
        line = plines['PASS ' + name]
        stand, start, goal = stands(line), starts(line), goals(line)
        expect(abs(math.hypot(start[0] - stand[0], start[1] - stand[1]) - 12) < 0.5 and abs(math.hypot(goal[0] - start[0], goal[1] - start[1]) - 40) < 0.5
               and math.hypot(goal[0] - stand[0], goal[1] - stand[1]) > math.hypot(start[0] - stand[0], start[1] - stand[1]) + 39 and 'walks AWAY from it, starting 12 m from its middle' in line, line)
    # (facing away: against the stander's bearing for 'behind' - 19a, 19c -, with it for 'front' - 19b, 19d)
    for name, turned_round in (('19a', True), ('19b', False), ('19c', True), ('19d', False)):
        line = plines['PASS ' + name]
        walker_b, stander_b = float(line.split(', bearing ')[1].split(';')[0]), float(line.split(', bearing ')[2].split(',')[0])
        expect(abs(turn(walker_b, stander_b) - (180 if turned_round else 0)) < 1, 'pass %s faces away: %s' % (name, line))
    # 20: ordered back to its start once, 15 / 20 m before the stander's middle, facing back
    for name, metres in (('20a', 15), ('20b', 20), ('20c', 15), ('20d', 20)):
        turned = [l for l in text.splitlines() if 'PASS %s: ' % name in l and 'TURNED BACK' in l]
        expect(len(turned) == 1, 'pass %s turns back once: %r' % (name, turned))
        near = float(turned[0].split('TURNED BACK ')[1].split(' m from')[0])
        expect(metres - 1.0 <= near <= metres, 'turned %.1f m before the middle (%d): %s' % (near, metres, turned[0]))
        expect('turns back %d m before its middle' % metres in plines['PASS ' + name], plines['PASS ' + name])
    # 20a: W (uid 102) gets its order to the far side, then one back to its start
    w_orders = [c for c in calls if c[0] == 102 and c[2] == 'goto']
    start20 = starts(plines['PASS 20a'])
    expect(any(abs(c[3] - start20[0]) < 0.6 and abs(c[4] - start20[1]) < 0.6 for c in w_orders), 'ordered back to its start: %r' % w_orders)
    # 21 / 22: the second line stands 30 / 14 m behind the first (same bearing), the walker's goal 40 m past it, and it is used
    for name, gap in (('21a', 30), ('21b', 30), ('22a', 14), ('22b', 14)):
        line = plines['PASS ' + name]
        first, goal = stands(line), goals(line)
        second = [float(v) for v in line.split(' behind its middle, at ')[1].split(',')[0].split(' / ')]
        expect(abs(math.hypot(second[0] - first[0], second[1] - first[1]) - gap) < 1.5 and abs(math.hypot(goal[0] - second[0], goal[1] - second[1]) - 40) < 1.5
               and ('second line A2 ' in line or 'second line EA2 ' in line) and ('%d m behind its middle' % gap) in line, line)
        tele = [c for c in calls if c[2] == 'teleport' and abs(c[3] - second[0]) < 0.6 and abs(c[4] - second[1]) < 0.6]
        expect(tele, 'the second line is put down at %r' % second)
        expect(abs(tele[-1][5] - float(line.split('bearing ')[2].split(',')[0])) < 0.5, 'same bearing as the first: %r %s' % (tele[-1], line))
    # an army without the second infantry unit: the pass is skipped, nothing breaks
    b = run([u for u in ARMY if u[1] not in ('swords', 'warriors')], script=SCRIPT_LATE)
    b['phases']['Deployed']()
    n = 0
    while 'units_scenario_tick' in b['timers'] and n < 40000:
        b['tick']()
        n += 1
    text = log()
    expect('PASS 21a skipped: ' in text and 'a unit is missing' in text and 'all rounds done' in text and 'ERROR' not in text, text[-3000:])
    # the DEFAULT script: every round runs (the old ones too), also the passes of single models; only the aura round is off
    b = run(ARMY, script=SCRIPT_DEFAULT)
    b['phases']['Deployed']()
    n = 0
    while 'units_scenario_tick' in b['timers'] and n < 40000:
        b['tick']()
        n += 1
    text = log()
    expect('all rounds done' in text and 'ERROR' not in text and 'TIMED OUT' not in text and 'not in ONLY_ROUNDS' not in text and 'ROUND 5 skipped' not in text, text[-3000:])
    expect('PASS 5a done' in text and 'PASS 1a done' in text and 'PASS 25a done' in text and 'ROUND 6 skipped: the lord aura round is off' in text, 'all rounds: ' + text[-3000:])
    # SEVERAL ARMIES per side, none with every kind of unit (I2). Ours: army 1 infantry (two pairs), a cavalry unit and the general;
    # army 2 (ally) a direct-fire great cannon, two missile units and monstrous infantry; army 3 (ally) a fast monstrous cavalry
    # unit, a ghost unit and a monster. The enemy: army 1 infantry and two missile units; army 2 a cannon, a ghost unit, monstrous cavalry
    cannon, hand, cross = 'wh2_dlc13_emp_art_great_cannon_imperial_supply', 'wh_main_emp_inf_handgunners', 'wh_main_emp_inf_crossbowmen'
    archers, sea_guard = 'wh2_main_hef_inf_archers_0', 'wh2_main_hef_inf_lothern_sea_guard_0'
    army1 = [(1, 'general', 1, ('is_commanding_unit',), 0, -20), (1, 'ironsworn', 125, ('is_infantry',), -30, 0), (1, 'ironsworn', 125, ('is_infantry',), 30, 0),
             (1, 'swords', 80, ('is_infantry',), -60, 0), (1, 'swords', 80, ('is_infantry',), -90, 0), (1, 'wolves', 75, ('is_cavalry',), 0, 10)]
    army2 = [(4, cannon, 4, ('is_artillery',), 40, -30), (4, hand, 40, ('is_infantry',), 200, 0, {'range': 100.0}), (4, cross, 40, ('is_infantry',), 230, 0, {'range': 100.0}),
             (4, plague, 6, (), 140, 0)]
    army3 = [(5, demi, 8, ('is_cavalry',), 100, 0), (5, ghost, 20, ('is_cavalry',), 180, 0), (5, 'destroyer', 1, ('is_war_beasts',), 10, -10)]
    foe1 = [(2, 'bulls', 40, ('is_infantry',), -30, 600), (2, 'bulls', 40, ('is_infantry',), 0, 600), (2, 'gnoblars', 160, ('is_infantry',), 30, 600),
            (2, 'gnoblars', 160, ('is_infantry',), 60, 600), (2, archers, 40, ('is_infantry',), 240, 600, {'range': 100.0}), (2, sea_guard, 40, ('is_infantry',), 270, 600, {'range': 100.0})]
    foe2 = [(3, cannon, 4, ('is_artillery',), 300, 600), (3, ghost, 20, ('is_cavalry',), 330, 600), (3, demi, 8, ('is_cavalry',), 360, 600)]
    b = run(army1 + army2 + army3 + foe1 + foe2, script=SCRIPT_DEFAULT)
    b['phases']['Deployed']()
    n = 0
    while 'units_scenario_tick' in b['timers'] and n < 60000:
        b['tick']()
        n += 1
    text = log()
    expect('all rounds done' in text and 'ERROR' not in text and 'TIMED OUT' not in text, text[-3000:])
    for line in ('role R from allied army 2: R (%s, 4 men)' % cannon, 'role MS from allied army 2: MS (%s, 40 men)' % hand, 'role MS2 from allied army 2: MS2 (%s, 40 men)' % cross,
                 'role MI from allied army 2: MI (%s, 6 men)' % plague, 'role MC from allied army 3: MC (%s, 8 men)' % demi, 'role FMC from allied army 3: FMC (%s, 8 men)' % demi,
                 'role X from allied army 3: X (%s, 20 men)' % ghost, 'role M from allied army 3: M (destroyer, 1 men)',
                 'role ER from enemy army 2: R (%s, 4 men)' % cannon, 'role EX from enemy army 2: X (%s, 20 men)' % ghost, 'role EC from enemy army 2: C (%s, 8 men)' % demi,
                 'role EMC from enemy army 2: MC (%s, 8 men)' % demi, 'role EFMC from enemy army 2: FMC (%s, 8 men)' % demi):
        expect(line in text, 'lent role: %s | %s' % (line, text[:3000]))
    expect('role C from' not in text and 'role A from' not in text and 'role EMS from' not in text and 'role EA from' not in text, 'nothing lent that the first army has: ' + text[:3000])
    # (missing: the enemy has no monstrous infantry, no single monster, no chariot: nothing to lend)
    expect('role EMI from' not in text and 'role V from' not in text, text[:3000])
    # a unit lent to a role of the first army is not also a role of its own army: the ally roles are picked afterwards
    lent = {}
    for l in text.splitlines():
        if ' from allied army ' in l or ' from enemy army ' in l:
            lent.setdefault((' from enemy' in l, l.rsplit(': ', 1)[1]), []).append(l.split('role ')[1].split(' from')[0])
    for unit, roles in lent.items():
        expect(len(roles) == 1 or set(roles) <= {'MC', 'FMC', 'C', 'EC', 'EMC', 'EFMC'}, 'a unit with several roles: %r %r' % (unit, roles))
    expect('allied army 2 (4 units): LA (missing), LW (missing), LA2 (missing), LW2 (missing), LV (missing), LMI (missing), LC (missing)' in text
           and 'allied army 3 (3 units): KA (missing), KW (missing), KA2 (missing), KW2 (missing), KV (missing), KMI (missing), KC (missing)' in text, 'the allies kept nothing: ' + text[:3000])
    # the rounds that need these units
    expect('PASS 4a: MI (%s, 6 men) walks through A' % plague in text and 'PASS 4c: MC (%s, 8 men) walks through W' % demi in text and 'PASS 4d: EMI' not in text, 'round 4: ' + text[-3000:])
    for name in ('4a', '4c', '13a', '13b', '17a', '17c', '18a', '18b', '23a', '23b', '24a', '24a2', '24b', '24c', '24c2', '24d', '5a', '1a', '21a', '25a'):
        expect('PASS %s done' % name in text, 'pass %s: %s' % (name, text[-3000:]))
    expect('PASS 23a: R fires at EA through A' in text and 'PASS 23b: ER fires at A2 through EA2' in text, 'the guns of the fire round: ' + text[-3000:])
    expect('PASS 13a: FMC (%s, 8 men) walks through A' % demi in text and 'PASS 17a: X (%s, 20 men) walks through A' % ghost in text and 'PASS 17c: EX (%s, 20 men) walks through EA' % ghost in text, text[-3000:])
    expect('PASS 24a: MS (%s, 40 men)' % hand in text and 'PASS 24c: EMS (%s, 40 men)' % archers in text and 'PASS 24b: R (%s' % cannon in text, text[-3000:])
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
