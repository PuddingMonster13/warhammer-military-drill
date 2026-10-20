"""Offline test of the stack probe's battle script. It borrows the fake armies of the place probe's test.

    python stack_probe_mocktest.py <out_dir holding BOTH builds: place_probe.py build and stack_probe.py build>
"""
import os
import struct
import sys
import tempfile

import place_probe_mocktest as m
import stack_probe as q

m.SCRIPT = open(os.path.join(os.path.abspath(sys.argv[1]), q.SCRIPT), encoding='utf8').read()
LOG = 'units_stack_probe_log.txt'
TIMER = 'units_stack_probe_tick'
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
    expect('PROBE OFF' not in log() and 'cave installed' in log() and len(hooks) == 1, log())
    tbl = struct.unpack('<Q', mem.read(hooks[m.BASE + q.START_MOVE] + 2, 8))[0]
    unit = b['units'][1]

    def call(model, x, z, stack):
        """What the cave notes for one "start move"."""
        n = struct.unpack('<I', mem.read(tbl + q.INDEX, 4))[0]
        at = tbl + q.RING_AT + (n % q.RING) * q.ENTRY
        words = list(stack) + [0x7ffe0000 + i for i in range(q.WORDS - len(stack))]
        mem.write(at, struct.pack('<Q4f%dQ' % q.WORDS, model, x, 2.0, z, 0.0, *words))
        mem.write(tbl + q.INDEX, struct.pack('<I', n + 1))

    chain_a = [m.BASE + 0x31173c4, 0x1234, m.BASE + 0x30710f7, 0x7ffe1111, m.BASE + 0x3082394, m.BASE + 0x2da7e0d]
    chain_b = [m.BASE + 0x2f7a183, m.BASE + 0x2f6b1e0, 0x99, m.BASE + 0x3083215]
    tick = b['timers'][TIMER]
    b['phases']['Deployed']()
    for t in range(1, 110):
        if TIMER not in b['timers']:
            break
        call(unit['models'][5], 105.0, 51.0, chain_b)  # another model is ordered all the time
        if t in (45, 46, 50):
            call(unit['models'][0], 100.0, 51.0, chain_a)  # the moved model is sent back
        tick()
    text = log()
    expect('PROBE OFF' not in text, text)
    expect('subject unit: A1.1.2 unit_key_1, 24 models, class of its models' in text, text)
    expect('the first 8 s, everything stands: "start move" ran 40 times' in text, text)
    expect('chains for other models: x40: 2f7a183 < 2f6b1e0 < 3083215' in text.lower(), text)
    expect('MOVED 3 models 4.0 m aside' in text, text)
    expect('START MOVE for A1.1.2 unit_key_1 model 0 to 100.00 / 51.00 (it stood at 100.00 / 51.00), came through: 31173c4 < 30710f7 < 3082394 < 2da7e0d' in text.lower().replace('start move for a1.1.2', 'START MOVE for A1.1.2'), text)
    expect(text.count('START MOVE for') == 2, 'two lines per model at most')
    expect('chains for the moved models: x3: 31173c4 < 30710f7 < 3082394 < 2da7e0d' in text.lower(), text)
    expect('PROBE FINISHED' in text and TIMER not in b['timers'], text)
    print('mock test: %d checks, ALL PASS' % checks)


if __name__ == '__main__':
    main()
