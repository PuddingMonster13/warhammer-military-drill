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
LOG = 'units_leader_probe_log.txt'
TIMER = 'units_leader_probe_tick'
LEAD, TIMER_FIELD, FIXED = 0x5e4, 0x600, 0x604  # where the fake units keep leadership, a timer, a fixed number
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
    game_code = m.code_of(exe)
    mem = m.Memory()
    mem.regions[m.UID_CODE & ~0xFFF] = bytearray(0x1000)
    mem.write(m.UID_CODE, struct.pack('<I', m.UID_FIELD))
    b = m.run_battle(mem, {}, game_code)
    u = b['units']
    lua = b['lua']
    expect('LEADERSHIP PROBE 1 TEST PACK' in log() and 'PROBE OFF' not in log(), log())
    # the game's unit panel and the wavering flag (the fake units have neither)
    lua.execute('''
        wavering = {}
        panel_text = ""
        local function node(id, text, children)
            return { Id = function() return id end, GetStateText = function() return text() end, Visible = function() return true end,
                ChildCount = function() return #children end, Find = function(_, i) return children[i + 1] end }
        end
        local value = node("dy_value", function() return panel_text end, {})
        local panel = node("stat_morale", function() return "" end, { value })
        local leader = node("leadership", function() return panel_text end, {})
        local root = node("root", function() return "" end, { node("units_panel", function() return "" end, { panel, leader }) })
        core = { get_ui_root = function() return root end }
        UIComponent = function(c) return c end
        for a = 1, bm.battle:alliances():count() do
            local armies = bm.battle:alliances():item(a):armies()
            for i = 1, armies:count() do
                local units = armies:item(i):units()
                for j = 1, units:count() do
                    local unit = units:item(j)
                    unit.is_wavering = function(self) return wavering[self:unique_ui_id()] == true end
                    unit.is_commanding_unit = function(self) return self:type() == "aside" end
                end
            end
        end
    ''')
    lead = {name: 60.0 + 5 * n for n, name in enumerate(u)}
    for name, one in u.items():
        mem.write(one['at'] + LEAD, struct.pack('<f', lead[name]))
        mem.write(one['at'] + FIXED, struct.pack('<f', 50.0))
    b['phases']['Deployed']()
    expect('watching 3 units' in log(), 'the player army only: ' + log())
    expect('candidate places' in log(), log())
    tick = b['timers'][TIMER]
    for t in range(1300):
        for n, (name, one) in enumerate(u.items()):
            mem.write(one['at'] + TIMER_FIELD, struct.pack('<f', 1.0 + (t % 97)))  # changes all the time
            if name in ('stand', 'enemy') and t % 40 == 0 and one['men'] > 40:
                one['men'] -= 5  # losses: leadership falls with them
                lead[name] -= 4
                mem.write(one['at'] + LEAD, struct.pack('<f', lead[name]))
            lua.globals().wavering[one['uid']] = lead[name] < 40
        # the general ('aside') stands near 'stand' every other 200 ticks: its leadership is 6 higher then
        near = (t // 200) % 2 == 0
        u['aside']['x'], u['aside']['z'] = (5.0, 0.0) if near else (200.0, 0.0)
        mem.write(u['stand']['at'] + LEAD, struct.pack('<f', lead['stand'] + (6.0 if near else 0.0)))
        if t % 100 == 0:  # the player selects a unit now and then: the panel shows its leadership
            name = ('stand', 'enemy', 'walk')[(t // 100) % 3]
            lua.globals().panel_text = 'Leadership: %d' % round(lead[name])
        tick()
    text = log()
    expect('summary:' in text and 'PROBE OFF' not in text, text)
    expect('RANKING AT SCENARIO END' not in text, 'no ranking before the scenario ends')
    lua.globals().units_scenario_finished = 1
    tick()
    tick()
    text = log()
    expect(text.count('RANKING AT SCENARIO END:') == 1 and '+0x5e4 float' in text[text.index('RANKING AT SCENARIO END'):], text[-1500:])
    b['phases']['Complete']()
    text = log()
    final = text[text.index('FINAL SUMMARY'):]
    first = final.splitlines()[1]
    expect('+0x5e4 float' in first, 'leadership place ranked first: ' + final[:1500])
    expect('panel numbers matched' in first and 'shaken' in first and 'corr. with men 0.9' in first, first)
    expect('+0x600 float' not in final and '+0x604 float' not in final, 'the timer and the fixed number are not candidates: ' + final[:1500])
    expect('UI component seen' not in text and 'UI /root' not in text, 'the panel is not walked any more (run 16)')
    expect('A1.1.1 stand 100 -> 40 men, shaken in' in final and 'lord near in' in final, final[:600])
    expect('lord near' in first and 'vs far' in first, 'the aura shows in the ranking: ' + first)
    print('leader probe mock test: %d checks, ALL PASS' % checks)


if __name__ == '__main__':
    main()
