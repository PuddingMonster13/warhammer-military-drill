"""Offline test of the order probe's battle script. It borrows the fake armies of the place probe's test.

    python order_probe_mocktest.py <out_dir holding BOTH builds: place_probe.py build and order_probe.py build>
"""
import os
import struct
import sys
import tempfile

import place_probe_mocktest as m  # reads the place probe's script from the out_dir; replaced below
import order_probe as q

m.SCRIPT = open(os.path.join(os.path.abspath(sys.argv[1]), q.SCRIPT), encoding='utf8').read()
LOG = 'units_order_probe_log.txt'
TIMER = 'units_order_probe_tick'
RET = m.BASE + 0x2f83b85
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
    tbl = struct.unpack('<Q', mem.read(hooks[m.BASE + q.STEP] + 2, 8))[0]

    # the fake game: every model of the unit of 24 but the last has an order object with its place
    unit = b['units'][1]
    battle = mem.alloc(0x100)
    mem.write(battle + 0x98, struct.pack('<I', 500))
    p30 = mem.alloc(0x500)
    mem.write(p30 + 0x440, struct.pack('<Q', battle))
    orders = []
    for s, model in enumerate(unit['models'][:-1]):
        holder = mem.alloc(0x20)
        mem.write(holder + 8, struct.pack('<Q', model))
        order = mem.alloc(0xb0)
        mem.write(order, struct.pack('<Q', m.BASE + q.ORDER_VTABLE))
        mem.write(order + 0x10, struct.pack('<Q', holder))
        mem.write(order + 0x30, struct.pack('<Q', p30))
        mem.write(order + 0x40, struct.pack('<3f', 100.0 + s, 2.0, 51.0))
        mem.write(order + 0x74, struct.pack('<II', 3, 1))
        mem.write(order + 0xa0, struct.pack('<I', 520))
        orders.append((order, model))

    def game_tick():
        n = struct.unpack('<I', mem.read(tbl + q.INDEX, 4))[0]
        for order, model in orders:  # every order is stepped; a model walks 0.5 m per tick to its order's place
            mem.write(tbl + q.RING_AT + (n % q.RING) * q.ENTRY, struct.pack('<QQ', order, RET))
            n += 1
            if struct.unpack('<Q', mem.read(order, 8))[0] != m.BASE + q.ORDER_VTABLE:
                continue
            px = struct.unpack('<f', mem.read(order + 0x40, 4))[0]
            x = struct.unpack('<f', mem.read(model + 0x88, 4))[0]
            if abs(px - x) > 0.01:
                mem.write(model + 0x88, struct.pack('<f', x + max(-0.5, min(0.5, px - x))))
        mem.write(tbl + q.RING_AT + (n % q.RING) * q.ENTRY, struct.pack('<QQ', 0x1234, RET))  # something that is no order
        mem.write(tbl + q.INDEX, struct.pack('<I', n + 1))

    tick = b['timers'][TIMER]
    b['phases']['Deployed']()
    m0, m12 = unit['models'][0], unit['models'][12]
    x_of = lambda model: struct.unpack('<f', mem.read(model + 0x88, 4))[0]
    reached = 0
    for t in range(1, 130):
        if TIMER not in b['timers']:
            break
        if t == 60:  # the order of model 12 is replaced by something else while its place is edited
            mem.write(orders[12][0], struct.pack('<Q', 0x5555))
        game_tick()
        tick()
        if t == 41:
            expect(abs(struct.unpack('<f', mem.read(orders[0][0] + 0x40, 4))[0] - 102.5) < 0.01, 'place edited')
            expect(struct.unpack('<I', mem.read(orders[0][0] + 0xa0, 4))[0] == 0, 'wait cleared')
            expect(abs(x_of(unit['models'][23]) - 127.0) < 0.01, 'the model without an order was moved 4 m')
        if 70 <= t <= 79:
            reached = max(reached, x_of(m0))
    text = log()
    expect('PROBE OFF' not in text, text)
    expect('ORDER of A1.1.2 unit_key_1 model 0 found at' in text and 'place 100.00 2.00 51.00' in text and 'kind 3, state 1' in text and 'waits for tick 520 (battle tick now 500)' in text, text)
    expect('first 3 s: the step routine ran 360 times; 24 different objects seen so far' in text, text)
    expect('subject models with an order: 2 of 3' in text, text)
    expect('EDITED the order of A1.1.2 unit_key_1 model 0: place x 100.00 -> 102.50, wait cleared' in text, text)
    expect('A1.1.2 unit_key_1 model 23 has no order object: MOVED the model 4.0 m instead' in text, text)
    expect(abs(reached - 102.5) < 0.01, 'the model walked to the edited place: %r' % reached)
    expect('A1.1.2 unit_key_1 model 0: 2.50 m from where it stood | order: place 102.50' in text, text)
    expect('RESTORED the order of A1.1.2 unit_key_1 model 0: place x back to 100.00' in text, text)
    expect('order of A1.1.2 unit_key_1 model 12 NOT restored' in text and "no longer this model's order" in text, text)
    expect(abs(x_of(m0) - 100.0) < 0.01 and abs(struct.unpack('<f', mem.read(orders[0][0] + 0x40, 4))[0] - 100.0) < 0.01, 'model 0 and its order back')
    expect(abs(struct.unpack('<f', mem.read(orders[12][0] + 0x40, 4))[0] - 114.5) < 0.01, 'the replaced object is not written to')
    expect('PROBE FINISHED' in text and TIMER not in b['timers'], text)
    print('mock test: %d checks, ALL PASS' % checks)


if __name__ == '__main__':
    main()
