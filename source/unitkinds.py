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


def direct_fire_units():
    """Main unit keys (unit:type() in battle) of the direct-fire guns: land_units.category 'artillery' whose missile weapon's
    default projectile flies a flat path (trajectory_sight 'low', max_elevation <= 50) and is fast (muzzle_velocity >= 70) or a
    canister shot; also the flame cannons (muzzle velocity 40, but elevation <= 40: a flat stream). Cannons, bolt throwers,
    organ guns, helblasters, carronades, warp lightning / doom magma cannons, flame cannons - not catapults, rock lobbers,
    grudge throwers, mortars, hellcannons, trebuchets, rocket batteries, the screaming skull."""
    db = _shared_db()
    land = {r['key']: r for r in db.table('land_units')}
    engines = {r['key']: r for r in db.table('battlefield_engines')}
    weapons = {r['key']: r for r in db.table('missile_weapons')}
    projectiles = {r['key']: r for r in db.table('projectiles')}
    keys = []
    for m in db.table('main_units'):
        l = land.get(m['land_unit'])
        if not l or m.get('is_naval') or l['category'] != 'artillery':
            continue
        weapon = l.get('primary_missile_weapon') or (engines.get(l.get('engine')) or {}).get('missile_weapon')
        projectile = projectiles.get((weapons.get(weapon) or {}).get('default_projectile'))
        if not projectile:
            continue
        elevation, velocity = projectile['max_elevation'] or 0, projectile['muzzle_velocity'] or 0
        if projectile['trajectory_sight'] == 'low' and elevation <= 50 and (velocity >= 70 or projectile['shot_type'] == 'artillery_canister' or elevation <= 40):
            keys.append(m['unit'])
    assert 10 < len(keys) < 60, len(keys)
    return sorted(set(keys))


def lua_fire_guns():
    return '{ ' + ', '.join("['%s'] = true" % k for k in direct_fire_units()) + ' }'


# main unit keys that hold one of these are Regiments of Renown and other buffed / special variants: not the normal unit
LEAD_EXCLUDE = ('_ror', 'boss', 'summon', '_qb', 'blessed', 'prologue', 'dummy')


def median_leadership(mains, lands):
    """(median, count) of the base leadership (land_units.morale) of the vanilla not-buffed units: main_units rows that are
    not naval, not lords / heroes, whose land unit has morale > 0 and whose key holds none of LEAD_EXCLUDE.
    """
    import statistics
    land = {r['key']: r for r in lands}
    values = []
    for m in mains:
        l = land.get(m['land_unit'])
        if not l or m.get('is_naval') or m['caste'] in ('lord', 'hero'):
            continue
        if any(bad in m['unit'] for bad in LEAD_EXCLUDE):
            continue
        morale = int(l['morale'] or 0)
        if morale > 0:
            values.append(morale)
    assert values, 'no units with morale'
    return int(round(statistics.median(values))), len(values)


def lead_norm():
    """(median, count): what leadership counts as x1 (computed from the game's tables at build time)"""
    db = _shared_db()
    return median_leadership(db.table('main_units'), db.table('land_units'))


def _shared_db():
    sys.path.insert(0, os.path.dirname(HERE))
    from wh3tools import db
    return db


def ethereal_units():
    """set of unit keys whose land unit has the ground stat effect group 'ethereal' (Hexwraiths, Cairn Wraiths, Spirit Hosts, ...)"""
    db = _shared_db()
    ghosts = {r['key'] for r in db.table('land_units') if r['ground_stat_effect_group'] == 'ethereal'}
    units = {m['unit'] for m in db.table('main_units') if m['land_unit'] in ghosts and not m.get('is_naval')}
    assert 10 < len(units) < 200, len(units)
    return units


def run_speeds():
    """unit key -> run speed in m/s (rounded to 0.1) of the entity that moves (the mount's if it has one, else the man's)"""
    db = _shared_db()
    speed = {r['key']: r['run_speed'] for r in db.table('battle_entities')}
    mount_entity = {r['key']: r['entity'] for r in db.table('mounts')}
    land = {}
    for r in db.table('land_units'):
        entity = mount_entity.get(r['mount']) if r['mount'] else r['man_entity']
        if entity in speed and speed[entity] is not None:
            land[r['key']] = round(float(speed[entity]), 1)
    speeds = {}
    for m in db.table('main_units'):
        if m['land_unit'] in land and not m.get('is_naval'):
            speeds[m['unit']] = land[m['land_unit']]
    assert 1500 < len(speeds) < 3500, len(speeds)
    return speeds


def lua_ethereal():
    return '{ ' + ', '.join("['%s'] = true" % k for k in sorted(ethereal_units())) + ' }'


def lua_speeds():
    return '{ ' + ', '.join("['%s'] = %s" % kv for kv in sorted(run_speeds().items())) + ' }'
