"""Offline test of the leadership probe's battle script. It borrows the fake armies of the lanes test.

    python leader_probe_mocktest.py <out_dir with the leader_probe.py build>
"""
import os
import struct
import sys
import tempfile

import lanes_mocktest as m
import leader_probe as q

m.SCRIPT = open(os.path.join(os.path.abspath(sys.argv[1]), q.SCRIPT), encoding='utf8').read()
BUILT = m.SCRIPT
# the fake units are not in the game's tables: give them the base leadership 50 (a repeated key in a Lua table: the last wins)
m.SCRIPT = m.SCRIPT.replace('local MORALE = { ', 'local MORALE = { ' + ', '.join("['%s'] = 50" % n for n in ('stand', 'walk', 'aside', 'walk2', 'guns', 'cart', 'walk3', 'enemy')) + ', ', 1)
assert "['stand'] = 50" in m.SCRIPT
PLAIN = m.SCRIPT
LOG = 'units_leader_probe_log_1.txt'
TIMER = 'units_leader_probe_tick'
LEAD, TIMER_FIELD, FIXED = 0x5e4, 0x600, 0x604  # where the fake units keep leadership, a timer, a fixed number
SUB, SUBSUB = 0x118, 0x40  # pointer to a sub-object in the unit, pointer to a sub-sub-object in the sub-object
HIDDEN, HIDDEN_INT = 0x348, 0x350  # the leadership float / int of the hidden tests, in the sub-sub-object
DECOY, DECOY_OK = 0x100, 0x108  # in the sub-object: falls with losses but is far from the base / is near the base
BANNED = ('find(', 'match(', 'gmatch(', 'gsub(', 'lower(', 'upper(')  # run 31: these broke the game's whole Lua state
MAX_SAMPLE = 16384
checks = 0
bytes_seen = {}


def expect(cond, what):
    global checks
    checks += 1
    assert cond, what


def rotation(lua, b, log):
    """more battles in the same game folder (the Lua state is fresh per battle: the script is loaded again)"""
    files = lambda: sorted(f for f in os.listdir('.') if f.startswith('units_leader_probe_log_'))
    read = lambda i: open('units_leader_probe_log_%d.txt' % i, encoding='utf8').read()
    expect(files() == ['units_leader_probe_log_1.txt'], files())
    first = newest = read(1)
    for n in (2, 3):  # a battle that is quit before it is deployed pushes nothing out
        lua.execute(m.SCRIPT)
        expect(files() == ['units_leader_probe_log_%d.txt' % i for i in range(1, n)] and read(1) == newest, 'only a loaded script: no rotation')
        b['phases']['Deployed']()
        expect(files() == ['units_leader_probe_log_%d.txt' % i for i in range(1, n + 1)], files())
        newest = read(1)
        expect(read(1).startswith('==== BATTLE ') and 'watching 8 units' in read(1) and 'FINAL SUMMARY' not in read(1), read(1)[:300])
        expect('FINAL SUMMARY' in read(n), 'the oldest log is the first battle: ' + read(n)[-300:])
    expect(read(3) == first and 'FINAL SUMMARY' in read(3), 'three battles: _1 newest, _3 oldest')
    for n in range(4, 7):
        lua.execute(m.SCRIPT)
        b['phases']['Deployed']()
    expect(files() == ['units_leader_probe_log_%d.txt' % i for i in range(1, 6)], 'six battles keep exactly five files: %s' % files())
    expect(read(5) != first and all(read(i).startswith('==== BATTLE ') for i in range(1, 6)), 'the oldest is dropped')
    # a script that fails before the battle: the newest log stays, the lines are appended with a header
    before = read(1)
    lua.execute('memreader_plus.is_null = nil')
    lua.execute(m.SCRIPT)
    after = read(1)
    expect(after.startswith(before) and '==== not started ' in after and 'PROBE OFF: this memreader has no is_null' in after and 'battle script loaded' in after, after[len(before) - 100:])
    expect(read(2) != before and files() == ['units_leader_probe_log_%d.txt' % i for i in range(1, 6)], 'no rotation')


def scenario(mode):
    """level1: leadership float in the unit object itself; budget: the same with a read budget of 1024 bytes;
    float3: a leadership float only in a sub-sub-object (level 3), units of both alliances; enemy3: an int in a
    sub-sub-object of the enemy unit only, which routs"""
    hidden = mode in ('float3', 'enemy3')
    m.SCRIPT = PLAIN.replace('local MAX_SAMPLE_BYTES = 16384', 'local MAX_SAMPLE_BYTES = 1024') if mode == 'budget' else PLAIN
    assert mode != 'budget' or 'MAX_SAMPLE_BYTES = 1024' in m.SCRIPT
    work = tempfile.mkdtemp()
    os.chdir(work)
    log = lambda: open(LOG, encoding='utf8').read() if os.path.exists(LOG) else ''
    exe = q.probe.exe()
    game_code = m.code_of(exe)
    mem = m.Memory()
    mem.regions[m.UID_CODE & ~0xFFF] = bytearray(0x1000)
    mem.write(m.UID_CODE, struct.pack('<I', m.UID_FIELD))
    b = m.run_battle(mem, {}, game_code)
    u = b['units']
    lua = b['lua']
    expect(log() == '', 'nothing is written before the battle starts: ' + log())
    # the wavering / routing flags and hit points (the fake units have none; 'aside' stays without hit points: men only)
    lua.execute('''
        wavering = {}
        routing = {}
        hp = {}
        for a = 1, bm.battle:alliances():count() do
            local armies = bm.battle:alliances():item(a):armies()
            for i = 1, armies:count() do
                local units = armies:item(i):units()
                for j = 1, units:count() do
                    local unit = units:item(j)
                    unit.is_wavering = function(self) return wavering[self:unique_ui_id()] == true end
                    unit.is_routing = function(self) return routing[self:unique_ui_id()] == true end
                    unit.is_commanding_unit = function(self) return self:type() == "aside" end
                    if unit:type() ~= "aside" then unit.unary_hitpoints = function(self) return hp[self:unique_ui_id()] end end
                end
            end
        end
    ''')
    lead = {name: 60.0 + 5 * n for n, name in enumerate(u)}
    objects, empties = {}, {}
    for name, one in u.items():
        if hidden and (mode == 'float3' or name == 'enemy'):
            sub, subsub = mem.alloc(0x1000), mem.alloc(0x1000)
            mem.write(one['at'] + SUB, struct.pack('<Q', sub))
            mem.write(sub + SUBSUB, struct.pack('<Q', subsub))
            lead[name] = 60.0  # base 50 + 10
            objects[name] = (sub, subsub)
            empties[name] = mem.alloc(0x1000)  # an object with nothing that looks like leadership: it must not be read per sample
            mem.write(one['at'] + 0x120, struct.pack('<Q', empties[name]))
        elif not hidden:
            mem.write(one['at'] + FIXED, struct.pack('<f', 50.0))

    def put(name, value, rally=False):
        if mode == 'enemy3':
            if name == 'enemy':
                mem.write(objects[name][1] + HIDDEN_INT, struct.pack('<i', int(value)))
        elif hidden:
            sub, subsub = objects[name]
            mem.write(subsub + HIDDEN, struct.pack('<f', value))
            mem.write(sub + DECOY, struct.pack('<f', value - 40.0))  # far from the base 50 at the start (20)
            if not rally:  # near it (55); it does not come back when the unit rallies
                mem.write(sub + DECOY_OK, struct.pack('<f', value - 5.0))
        else:
            mem.write(u[name]['at'] + LEAD, struct.pack('<f', value))

    for name in u:
        put(name, lead[name])
    b['phases']['Deployed']()
    expect('LEADERSHIP PROBE 4 TEST PACK' in log() and 'PROBE OFF' not in log(), log())
    expect(log().startswith('==== BATTLE ') and ', probe 4, units A1 7, A2 1\n' in log(), 'header: ' + log()[:200])
    expect('watching 8 units' in log(), 'both alliances: ' + log())
    expect('A2.1.1 enemy: 100 men, base leadership 50' in log() and 'A1.1.1 stand: 100 men' in log(), log())
    expect('candidate places' in log() and 'scan took' in log() and 'bytes read per sample' in log(), log())
    expect(log().count('==== BATTLE ') == 1, log())
    tick = b['timers'][TIMER]
    # count what sample() reads from now on
    lua.execute('''
        reads, read_bytes, read_at = 0, 0, {}
        local orig = memreader_plus.read
        memreader_plus.read = function(p, off, n) reads, read_bytes = reads + 1, read_bytes + n; read_at[p] = (read_at[p] or 0) + 1; return orig(p, off, n) end
    ''')
    for t in range(1500):
        for n, (name, one) in enumerate(u.items()):
            mem.write(one['at'] + TIMER_FIELD, struct.pack('<f', 1.0 + (t % 97)))  # changes all the time
            if name in ('stand', 'enemy', 'aside') and t % 40 == 0 and one['men'] > 40 and t < 1000:
                one['men'] -= 5  # losses: leadership falls with them
                lead[name] -= 4
                put(name, lead[name])
            if mode == 'float3' and name in ('stand', 'enemy', 'aside') and t == 1100:
                lead[name] += 30  # the unit rallies
                put(name, lead[name], True)
            if mode == 'enemy3' and name == 'enemy' and t == 1000:
                lead[name] = 0.0  # the enemy routs: leadership 0
                put(name, 0.0)
                lua.globals().routing[one['uid']] = True
            lua.globals().wavering[one['uid']] = lead[name] < 40
            lua.globals().hp[one['uid']] = one['men'] * 10
        if not hidden:
            # the general ('aside') stands near 'stand' every other 200 ticks: its leadership is 6 higher then
            near = (t // 200) % 2 == 0
            u['aside']['x'], u['aside']['z'] = (5.0, 0.0) if near else (200.0, 0.0)
            mem.write(u['stand']['at'] + LEAD, struct.pack('<f', lead['stand'] + (6.0 if near else 0.0)))
        tick()
    ticks = 1500
    per_sample = lua.globals().read_bytes / ticks
    bytes_seen[mode] = per_sample
    print('%s: %.0f bytes and %.2f reads per sample' % (mode, per_sample, lua.globals().reads / ticks))
    expect(per_sample <= (1024 if mode == 'budget' else MAX_SAMPLE), 'the read budget holds: %s' % per_sample)
    if mode == 'budget':
        expect('budget cut' in log(), 'the cut is logged: ' + log()[:1500])
    if hidden:
        expect(all(lua.globals().read_at[at] is None for at in empties.values()), 'a block without candidates is not read in sample()')
        expect(all(lua.globals().read_at[at] is not None for at in (objects['enemy'][1],) + ((objects['enemy'][0],) if mode == 'float3' else ())), 'the blocks with candidates are')
    text = log()
    expect('summary:' in text and 'PROBE OFF' not in text and 'sample failed' not in text, text[-1500:])
    expect('RANKING AT SCENARIO END' not in text, 'no ranking before the scenario ends')
    # the 60 s summary is short: 5 places, 2 examples
    periodic = text[text.index(' summary: '):]
    periodic = periodic[:periodic.index('  cost ')]
    expect(len([l for l in periodic.splitlines() if ' x' in l and '|' in l]) <= 5 and ' tr ' not in periodic, periodic)
    lua.globals().units_scenario_finished = 1
    tick()
    tick()
    text = log()
    expect(text.count('RANKING AT SCENARIO END:') == 1, text[-1500:])
    b['phases']['Complete']()
    text = log()
    final = text[text.index('FINAL SUMMARY'):]
    lines = final.splitlines()
    first = lines[1]
    expect('+0x600 float' not in final and '+0x604 float' not in final, 'the timer and the fixed number are not candidates: ' + final[:1500])
    expect('UI component seen' not in text and 'panel numbers' not in text, 'no panel any more')
    if mode == 'enemy3':
        expect(text.count('A2.1.1 enemy starts to waver (') == 1 and text.count('A2.1.1 enemy routs (') == 1, 'the enemy: ' + text[:2500])
        expect('[+0x118][+0x40]+0x350 int' in first, 'the enemy int ranked first: ' + final[:2500])
        trace = [l for l in lines if '  tr A2.1.1 enemy: 60 min 0 wav ' in l and ' rout 0 ' in l and ' ev 12/12' in l]
        expect(trace, 'trace of the enemy: ' + final[:2500])
        return
    expect(text.count('A1.1.1 stand starts to waver (') == 1 and 'men left)' in text, 'one line when a unit first wavers: ' + text[:1500])
    expect('A1.1.1 stand 100 -> 40 men, shaken in' in final, final[:600])
    expect(any(('  tr A1.1.1 stand: ' in l) and ' wav ' in l and ' ev ' in l for l in lines), 'a trace per unit: ' + final[:2500])
    if hidden:
        expect('[+0x118][+0x40]+0x348 float' in first, 'the level-3 leadership ranked first: ' + final[:2500])
        expect('[+0x118]+0x100 float' not in text, 'a level-2 number far from the base is not kept')
        expect('[+0x118]+0x108 float' in final, 'a level-2 number near the base is kept: ' + final[:2500])
        expect(text.count('A1.1.1 stand rallies (') == 1 and text.count('A2.1.1 enemy rallies (') == 1, 'rallies are logged')
        aside = [l for l in lines if '  tr A1.1.3 aside: ' in l]
        expect(aside and ' ev 12/12' in aside[0], 'no hit points: the men alone tell the losses: ' + final[:2500])
        stand = [l for l in lines if '  tr A1.1.1 stand: ' in l]
        expect(stand and ': 60 min' in stand[0] and ' ev 12/12' in stand[0], stand)
        rotation(lua, b, log)
    else:
        expect('+0x5e4 float' in text[text.index('RANKING AT SCENARIO END'):], text[-1500:])
        expect('+0x5e4 float' in first, 'leadership place ranked first: ' + final[:1500])
        expect(', r 0.9' in first and ', w ' in first, first)
        expect(', l ' in first, 'the aura shows in the ranking: ' + first)


def main():
    for word in BANNED:
        expect(word not in BUILT, 'the script contains ' + word)
    for mode in ('level1', 'budget', 'float3', 'enemy3'):
        scenario(mode)
    print('leader probe mock test: %d checks, ALL PASS (bytes per sample: %s)' % (checks, ', '.join('%s %.0f' % kv for kv in bytes_seen.items())))


if __name__ == '__main__':
    main()
