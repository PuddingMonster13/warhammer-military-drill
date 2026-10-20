"""Offline test of the diagnostic probe's battle script. It borrows the fake armies of the lanes test.

    python diag_probe_mocktest.py <out_dir holding BOTH builds: lanes.py build and diag_probe.py build>
"""
import os
import struct
import sys
import tempfile

import lanes_mocktest as m
import diag_probe as q
import lanes

m.SCRIPT = open(os.path.join(os.path.abspath(sys.argv[1]), q.SCRIPT), encoding='utf8').read()
LOG = 'units_diag_probe_log.txt'
TIMER = 'units_diag_probe_tick'
BASE = m.BASE
checks = 0


def expect(cond, what):
    global checks
    checks += 1
    assert cond, what


def main():
    work = tempfile.mkdtemp()
    os.chdir(work)
    log = lambda: open(LOG, encoding='utf8').read()
    exe = q.probe.exe()
    q.signatures(exe)
    game_code = m.code_of(exe)
    mem = m.Memory()
    mem.regions[m.UID_CODE & ~0xFFF] = bytearray(0x1000)
    mem.write(m.UID_CODE, struct.pack('<I', m.UID_FIELD))

    hooks = {}
    m.run_battle(mem, hooks, game_code, wrong_build=True)
    expect(not hooks and 'PROBE OFF: this game build does not fit (' in log(), 'wrong build must switch the probe off')
    for name in ('start_move', 'form_loop', 'place_send'):  # a pattern found twice (the lanes one too): off, nothing installed
        pattern = q.sites.parse(q.sites.PATTERNS[name])[0]
        hooks = {}
        m.run_battle(mem, hooks, dict(game_code, patterns=dict(game_code['patterns'], **{pattern: (game_code['patterns'][pattern][0], 2)})))
        expect(not hooks and 'PROBE OFF' in log() and ('the %s code was found 2 times' % name) in log(), 'pattern %s found twice' % name)

    # the lanes build is running: its cave 1 holds the lanes table
    lanes_tbl = mem.alloc(lanes.TABLE_SIZE)
    lanes_cave = mem.alloc(0x40)
    mem.write(lanes_cave, lanes.HEAD + struct.pack('<Q', lanes_tbl))
    hooks = {BASE + q.sites.find(exe, 'place_send'): lanes_cave}
    b = m.run_battle(mem, hooks, game_code)
    expect('PROBE OFF' not in log() and '5 caves installed, 0 reused' in log() and len(hooks) == 6, log())
    tbl = struct.unpack('<Q', mem.read(hooks[BASE + q.SITES[0][1]] + 2, 8))[0]
    u = b['units']
    for n, name in enumerate(('stand', 'walk', 'aside')):
        mem.write(lanes_tbl + lanes.LIST + lanes.ENTRY * n, struct.pack('<Q', u[name]['at']))
        mem.write(u[name]['model'] + 0x2e8, struct.pack('<Q', u[name]['at']))
    mem.write(u['walk']['model'] + 0x74, struct.pack('<I', 1))
    b['phases']['Deployed']()
    text = log()
    expect('fighting starts: 3 units of your side; lanes test build found' in text, text)
    expect('UNIT A1.1.2 walk: 100 men, ordered width 30.0, no flags, class ? | list of 1 | 1 x class ' in text and 'kind 1: radius 0.65..0.65' in text, text)
    expect('+0xb64 = 0 (no sub-models)' in text and 'own unit yes' in text, text)
    tick = b['timers'][TIMER]

    def ring_write(name, model, ret, x, z, words=()):
        rva, slots, kind = next((r[1], r[2], r[3]) for r in q.RINGS if r[0] == name)
        n = struct.unpack('<I', mem.read(tbl + q.INDEX_AT[name], 4))[0]
        at = tbl + q.RING_AT[name] + (n % slots) * q.entry_size(kind)
        mem.write(at, struct.pack('<QQ', ret, model))
        if kind != 'stack':
            mem.write(at + 0x10, struct.pack('<3f', x, 0.0, z))
        if 'stack' in kind:
            mem.write(at + q.stack_at(kind), struct.pack('<%dQ' % len(words), *words))
        mem.write(tbl + q.INDEX_AT[name], struct.pack('<I', n + 1))

    for _ in range(3):
        tick()
    expect('WATCH' not in log(), 'nobody moves: nothing watched')
    w = u['walk']
    w['moving'] = True
    model = w['model']
    for t in range(60):
        if t < 10:
            ring_write('start_move', model, BASE + 0x30710f7, 1.0, 2.0, [0x1234, BASE + 0x3082394, BASE + 0x30b5f96])
            ring_write('walk_to', model, BASE + 0x3256166, 3.0, 4.0)
            ring_write('order_390', model, BASE + 0x3284dee, 0, 0, [BASE + 0x3265969])
            ring_write('start_move', u['aside']['model'], BASE + 0x2f7a183, 9.0, 9.0, [])  # not watched
        tick()
    text = log()
    expect('WATCH MOVE A1.1.2 walk (infantry): it starts to move from 0.0 / -80.0 to 0.0 / -80.0' in text, text)
    expect('walk_to 5 (from x5: 3256166)' in text and 'start_move 5 (from x5: 30710f7)' in text.lower(), text)
    expect('last start_move to 1.00 / 2.00 = 2.2 m away, last walk_to to 3.00 / 4.00 = 5.0 m away' in text and 'order_390 5 (from x5: 3284dee)' in text.lower(), text)
    expect('END MOVE A1.1.2 walk: start_move chains: x10: 30710F7 < 3082394 < 30B5F96'.lower() in text.lower(), text)
    expect('END MOVE A1.1.2 walk: order_390 chains: x10: 3284DEE < 3265969'.lower() in text.lower(), text)
    expect('formation routine ran 0 times' in text and 'calls in the last 10 s, all units: ' in text, text)
    # the standing unit opens lanes
    mem.write(lanes_tbl + lanes.LIST + lanes.E_L, struct.pack('<f', 1.8))
    mem.write(lanes_tbl + lanes.LIST + lanes.E_PENDING, struct.pack('<I', 100))
    mem.write(u['stand']['model'] + 0x858, struct.pack('<3f', 6.5, 0.0, 7.5))
    mem.write(u['stand']['model'] + 0x888, bytes([0, 1, 1, 0]))
    mem.write(u['stand']['model'] + 0x5c0, bytes([1, 0, 0, 0]))
    for _ in range(30):
        tick()
    text = log()
    expect('WATCH OPEN A1.1.1 stand (infantry): it opens lanes of 1.8 m; jobs pending 100' in text, text)
    expect('station 6.50 / 7.50, gate 0, flags 0 1 1, action 1 0, 6f8 0, 600 0, 8b0 0, 324 0 | no calls' in text, text)
    b['phases']['Complete']()
    expect('battle complete: 1 MOVE and 1 OPEN watches. PROBE FINISHED' in log() and TIMER not in b['timers'], log())

    # next battle: caves reused
    before = dict(hooks)
    b = m.run_battle(mem, hooks, game_code)
    expect(hooks == before and '0 caves installed, 5 reused' in log() and 'PROBE OFF' not in log(), log())
    print('mock test: %d checks, ALL PASS' % checks)


if __name__ == '__main__':
    main()
