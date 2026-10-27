"""Units Pass Through - automatic test scenario (test pack). Lua only, no cave.

    python scenario.py build <out_dir>     writes !units_scenario_debug.pack and the script
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import probe
import unitkinds

VERSION = 2
PACK = '!units_scenario_debug.pack'
SCRIPT = 'units_scenario.lua'
BATTLE_PATH = 'script/battle/mod/' + SCRIPT


def lua_script():
    lua = open(os.path.join(HERE, 'scenario.lua.tpl'), encoding='utf8').read()
    assert lua.count('--VERSION--') == 2 and lua.count('--KINDS--') == 1
    assert lua.count('--ETHEREAL--') == 1 and lua.count('--SPEED--') == 1 and lua.count('--FIRE_GUNS--') == 1
    lua = lua.replace('--VERSION--', str(VERSION)).replace('--KINDS--', unitkinds.lua_table())
    return lua.replace('--ETHEREAL--', unitkinds.lua_ethereal()).replace('--SPEED--', unitkinds.lua_speeds()).replace('--FIRE_GUNS--', unitkinds.lua_fire_guns()).encode('utf8')


def main():
    if len(sys.argv) > 2 and sys.argv[1] == 'build':
        lua = lua_script()
        probe.write_pack(os.path.join(sys.argv[2], PACK), {BATTLE_PATH: lua})
        with open(os.path.join(sys.argv[2], SCRIPT), 'wb') as f:
            f.write(lua)
        print('script: %d lines' % lua.count(b'\n'))
    else:
        print(__doc__)


if __name__ == '__main__':
    main()
