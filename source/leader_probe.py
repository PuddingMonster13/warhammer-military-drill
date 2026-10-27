"""Units Pass Through - leadership probe (test pack). Lua only, no cave: it only reads memory.

    python leader_probe.py build <out_dir>     writes !units_leader_probe_debug.pack and the script
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import probe

VERSION = 4
PACK = '!units_leader_probe_debug.pack'
SCRIPT = 'units_leader_probe.lua'
BATTLE_PATH = 'script/battle/mod/' + SCRIPT
IMAGE_SIZE = 0x10000000  # pointers into [base, base + this) belong to the game's image, not to objects of a unit


def morale_table():
    """Lua table: main unit key (what unit:type() gives in battle) -> base morale (land_units.morale) from the decoded game tables"""
    sys.path.insert(0, os.path.dirname(HERE))
    from wh3tools import db
    land = {r['key']: r['morale'] for r in db.table('land_units')}
    morale = {}
    for m in db.table('main_units'):
        if m['land_unit'] in land and not m.get('is_naval'):
            morale[m['unit']] = land[m['land_unit']]
    assert 1500 < len(morale) < 3500, len(morale)
    for key in morale:
        assert "'" not in key and chr(92) not in key, key
    return '{ ' + ', '.join("['%s'] = %g" % kv for kv in sorted(morale.items())) + ' }'


def lua_script():
    lua = open(os.path.join(HERE, 'leader_probe.lua.tpl'), encoding='utf8').read()
    values = {'VERSION': VERSION, 'P_UID': probe.skaven_cave.P_UID, 'UID_OFF': probe.skaven_cave.UID_OFF, 'IMAGE_SIZE': IMAGE_SIZE, 'MORALE': morale_table()}
    for key, value in values.items():
        assert ('--%s--' % key) in lua, key
        lua = lua.replace('--%s--' % key, str(value))
    return lua.encode('utf8')


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
