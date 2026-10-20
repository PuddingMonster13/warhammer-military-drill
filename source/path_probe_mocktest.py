"""Offline test of the path probe's battle script. It borrows the fake game of the place probe's test.

    python path_probe_mocktest.py <out_dir holding BOTH builds: place_probe.py build and path_probe.py build>
"""
import os
import struct
import sys
import tempfile

import place_probe_mocktest as m  # reads the place probe's script from the out_dir; replaced below
import path_probe as q

m.SCRIPT = open(os.path.join(os.path.abspath(sys.argv[1]), q.SCRIPT), encoding='utf8').read()
LOG = 'units_path_probe_log.txt'
TIMER = 'units_path_probe_tick'
checks = 0


def expect(cond, what):
    global checks
    checks += 1
    assert cond, what


def main():
    work = tempfile.mkdtemp()
    os.chdir(work)
    log = lambda: open(LOG, encoding='utf8').read()
    game_code = {m.BASE + s[1]: sig for s, sig in zip(q.SITES, q.signatures(q.probe.exe()))}
    mem = m.Memory()
    mem.regions[m.UID_CODE & ~0xFFF] = bytearray(0x1000)
    mem.write(m.UID_CODE, struct.pack('<I', m.UID_FIELD))

    hooks = {}
    m.run_battle(mem, hooks, game_code, wrong_build=True)
    expect(not hooks and 'PROBE OFF' in log() and 'not the analysed one' in log(), 'wrong build must switch the probe off')

    hooks = {}
    b = m.run_battle(mem, hooks, game_code)
    expect('PROBE OFF' not in log() and len(hooks) == len(q.SITES) and '12 sites' in log(), log())
    tbl = struct.unpack('<Q', mem.read(hooks[m.BASE + q.SITES[0][1]] + 2, 8))[0]
    for name, rva, tail, leave, kind in q.SITES:
        want = q.HEAD + struct.pack('<Q', tbl) + tail
        expect(mem.read(hooks[m.BASE + rva], len(want)) == want, 'cave bytes of ' + name)
    slot = lambda name: q.SLOTS_AT + 0x10 * ([s[0] for s in q.SITES].index(name) - 1)
    # the formation routine "ran" 7 times while the battle loaded
    mem.write(tbl + slot('formation_routine'), struct.pack('<IIQ', 7, 0, m.BASE + 0x3265b0f))
    tick = b['timers'][TIMER]
    tick()
    b['phases']['Deployed']()
    expect('before the fighting' in log() and 'formation_routine    ran 7 times; callers seen: none' in log(), log())
    unit = b['units'][1]
    for t in range(1, 110):
        if TIMER not in b['timers']:
            break
        if t == 10:  # two routines run while everything stands
            mem.write(tbl + slot('start_move_5b8'), struct.pack('<IIQ', 40, 0, m.BASE + 0x2f7a295))
            mem.write(tbl + slot('formation_loop'), struct.pack('<I', 300))
        if t == 50:
            mem.write(tbl + slot('move_action_new'), struct.pack('<IIQ', 5, 0, m.BASE + 0x306ada8))
        m.game_tick(mem, tbl, b['units'])
        tick()
        if t == 40:
            expect(abs(struct.unpack('<f', mem.read(unit['models'][0] + 0x88, 4))[0] - 104.0) < 0.01, 'models moved')
    text = log()
    expect('PROBE OFF' not in text, text)
    expect('subject unit: A1.1.2 unit_key_1, 24 models' in text, text)
    expect('the first 6 s of the fighting, everything stands:' in text, text)
    expect('start_move_5b8       ran 40 times; callers seen: 0000000142f7a295 x' in text, text)
    expect('formation_loop       ran 300 times; callers seen: none' in text and 'formation_routine    ran 0 times' in text, text)
    expect('MOVED 3 models 4.0 m aside' in text, text)
    expect(('WALK ORDER for A1.1.2 unit_key_1 model 0 from %016x to 100.00 / 51.00 (its place was 100.00 / 51.00)' % m.CALLER) in text, text)
    expect('the 12 s after the move:' in text and 'move_action_new      ran 5 times; callers seen: 0000000142306ada8 x'.replace('142306', '14306') in text, text)
    expect(('the moved models: %016x x' % m.CALLER) in text, text)
    expect('distance of the moved models from their places now (m): 0.0 0.0 0.0' in text, text)
    expect('PROBE FINISHED' in text and TIMER not in b['timers'], text)

    before = dict(hooks)
    m.run_battle(mem, hooks, game_code)
    expect(hooks == before and 'PROBE OFF' not in log(), log())
    print('mock test: %d checks, ALL PASS' % checks)


if __name__ == '__main__':
    main()
