"""Kinds of units from the game's tables, for the battle scripts (built into the packs).
Vanilla tables only; units of other mods get their kind in
battle from the script interface (unit_class(), is_artillery(), ...; see collect_units in lanes.lua.tpl)."""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def unit_kinds():
    """unit key -> 'char' (lord / hero), 'art' (artillery), 'veh' (chariots, war machines), 'mi' / 'mc' (monstrous
    infantry / cavalry), 'inf' (infantry, war beasts), 'cav' (cavalry), 'mon' (monsters),
    from the game's tables (main_units caste, land_units category / class)"""
    sys.path.insert(0, os.path.dirname(HERE))
    from wh3tools import db
    land = {r['key']: r for r in db.table('land_units')}
    kinds = {}
    for m in db.table('main_units'):
        l = land.get(m['land_unit'])
        if not l or m.get('is_naval'):
            continue
        if m['caste'] in ('lord', 'hero'):
            kinds[m['unit']] = 'char'  # characters never make way
        elif l['category'] == 'artillery' or str(l['class']).startswith('art'):
            kinds[m['unit']] = 'art'
        elif m['caste'] in ('chariot', 'warmachine'):
            kinds[m['unit']] = 'veh'
        elif m['caste'] == 'monstrous_infantry':
            kinds[m['unit']] = 'mi'
        elif m['caste'] == 'monstrous_cavalry':
            kinds[m['unit']] = 'mc'
        elif m['caste'] in ('melee_infantry', 'missile_infantry', 'war_beast'):
            kinds[m['unit']] = 'inf'
        elif m['caste'] in ('melee_cavalry', 'missile_cavalry'):
            kinds[m['unit']] = 'cav'
        elif m['caste'] == 'monster':
            kinds[m['unit']] = 'mon'
    assert 1500 < len(kinds) < 3500, len(kinds)
    return kinds


def lua_table():
    return '{ ' + ', '.join("['%s'] = '%s'" % kv for kv in sorted(unit_kinds().items())) + ' }'
