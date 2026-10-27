"""Units Pass Through - every offline check in one go: builds the lanes and the scenario packs, then runs both mock tests AT THE SAME
TIME, and prints only the summary lines. Full outputs: <out_dir>\\b.txt, sb.txt, m.txt, sm.txt.

    python check_all.py <out_dir>     -> the last line is "ALL PASS" or "FAILED: ..."

Quick mode (while iterating on one part; NOT a substitute for the full run before a deploy):

    python check_all.py <out_dir> --quick <group>[,<group>]   -> builds as above, runs only those groups of the lanes mock (the
                                      scenario mock is skipped; the release mock only with the group "release"); the last line is
                                      "QUICK PASS (groups ...)" - never "ALL PASS" - or "FAILED: ..."
    python check_all.py --list        -> the group names
    python check_all.py <out_dir> --narrow-off -> 085h comparison build: the TEST pack with TUNE.NARROW = false is built into the folder narrow_off of <out_dir> and the core, columns and
                                      big_park_ghost groups of the lanes mock run on it (the narrow groups need narrowing); ends "QUICK PASS (narrow-off ...)"

Groups (from lanes_mocktest.py GROUPS; the cases of one group share their battle, so a group is complete on its own):
    core            one battle, run in order: wrong build, caves, lanes, columns, B / F / G cases, build49 / 55, SHAPE CHECK, PERF, tick error
    fire            line of fire, Skaven mod mock, E1 - E5
    fire_gaps       H: one gap per gun
    half_close      O: lane-by-lane closing (half close, quick close)
    guard_room      M: room beside a standing unit, guard mode
    big_park_ghost  L: big walkers, K: parked inside, N: ethereal units
    alliance        J: G2 log, I2: second army, leadership patterns
    columns         085c fix 1 / fix 2 (outer strip squeeze, guard mode columns)
    late            085c P3: LATE walkers
    perf_log        085c P4 / D3: reads per look, plans, test log, PERF phases, send limit, lane band
    bundle2         085d bundle 2: line of fire (target debounce, gap update, grace, enemy near), quick stand, next walker's columns, serve sub-phases, digest
    narrow          085e: narrowed big walkers (A2 / B1), ghost list (cave 5), bulk model read (D1), G2
    f085            085f: closing at 90 % through, stragglers, sequential walkers, extended goal of narrowed columns, guard mode without room, line of fire for infantry only
    h085            085h: big walkers never stuck (deferred wide restore, lane from the width sent, steady file count, closing at 100 %, no closing up on a free side, whole-front lane, narrow-off build)
    i085            085i: layout of cramped blocks (extra ranks, table of places), 5 % share for every stander, no whole-front lane for mi / mc
    j085            085j: math.huge is nil in the game, deferral cap 5 s, cavalry rule (4 files or the old columns)
    pure            pure Lua parts: formation, merge, room, artillery gaps, crew strips
    release         the release mock (lanes_mocktest.py --release, 18 checks incl. logging levels)
"""
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))


def run(args, out):
    with open(out, 'w', encoding='utf8') as f:
        return subprocess.Popen([sys.executable] + args, cwd=HERE, stdout=f, stderr=subprocess.STDOUT)


def last(path, key):
    lines = [l.rstrip() for l in open(path, encoding='utf8', errors='replace') if key in l]
    return lines[-1] if lines else None


NARROW_OFF_GROUPS = ['core', 'columns', 'big_park_ghost']


def narrow_off(out, t0):
    """085h: the comparison build (TUNE.NARROW = false) in the folder narrow_off of <out>, with the quick groups that do not need narrowing."""
    sub = os.path.join(out, 'narrow_off')
    os.makedirs(sub, exist_ok=True)
    shared = os.path.dirname(HERE)
    subprocess.run([sys.executable, '-c', 'import sys; sys.path.insert(0, sys.argv[1]); from wh3tools import db; db.schema()', shared], cwd=HERE)
    failed = []
    if run(['lanes.py', 'build', sub, '--narrow-off'], os.path.join(sub, 'b.txt')).wait() != 0:
        failed.append('narrow-off build')
    else:
        if run(['lanes_mocktest.py', sub, '--quick', ','.join(NARROW_OFF_GROUPS)], os.path.join(sub, 'm.txt')).wait() != 0:
            failed.append('lanes mock')
    for name, key in (('b.txt', 'native test'), ('b.txt', '.pack'), ('m.txt', 'mock test')):
        path = os.path.join(sub, name)
        if os.path.exists(path):
            line = last(path, key)
            print('%-7s %s' % (name, line if line else '(no "%s" line)' % key))
    path = os.path.join(sub, 'm.txt')
    if 'lanes mock' not in failed and os.path.exists(path) and not last(path, 'QUICK PASS'):
        failed.append('lanes mock')
    print('%.0f s' % (time.time() - t0))
    print('FAILED: ' + ', '.join(failed) if failed else 'QUICK PASS (narrow-off: groups %s)' % ','.join(NARROW_OFF_GROUPS))
    return 1 if failed else 0


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    if sys.argv[1] == '--list':
        subprocess.run([sys.executable, 'lanes_mocktest.py', '--list'], cwd=HERE)
        print('%-15s %s' % ('release', 'the release mock (lanes_mocktest.py --release)'))
        return 0
    quick = None
    if '--quick' in sys.argv:
        i = sys.argv.index('--quick')
        if i + 1 >= len(sys.argv):
            print('--quick needs group names (python check_all.py --list)')
            return 2
        quick = [g for g in sys.argv[i + 1].split(',') if g]
    out = os.path.abspath(sys.argv[1])
    os.makedirs(out, exist_ok=True)
    t0 = time.time()
    if '--narrow-off' in sys.argv:
        return narrow_off(out, t0)
    # the DB schema once before the builds run side by side (on a fresh copy both would download it into one file)
    shared = os.path.dirname(HERE)
    subprocess.run([sys.executable, '-c', 'import sys; sys.path.insert(0, sys.argv[1]); from wh3tools import db; db.schema()', shared], cwd=HERE)
    builds = [run(['lanes.py', 'build', out], os.path.join(out, 'b.txt')), run(['scenario.py', 'build', out], os.path.join(out, 'sb.txt'))]
    failed = [name for name, p in zip(('lanes build', 'scenario build'), builds) if p.wait() != 0]
    if not failed and run(['lanes.py', 'build', out, '--release'], os.path.join(out, 'rb.txt')).wait() != 0:
        failed.append('release build')
    if not failed:
        if quick is None:
            mocks = [run(['lanes_mocktest.py', out], os.path.join(out, 'm.txt')), run(['scenario_mocktest.py', out], os.path.join(out, 'sm.txt')),
                     run(['lanes_mocktest.py', out, '--release'], os.path.join(out, 'rm.txt'))]
            failed += [name for name, p in zip(('lanes mock', 'scenario mock', 'release mock'), mocks) if p.wait() != 0]
        else:  # (the old outputs of the mocks that are not run now must not be read: they are removed)
            for name in ('m.txt', 'sm.txt', 'rm.txt'):
                if os.path.exists(os.path.join(out, name)):
                    os.remove(os.path.join(out, name))
            lanes_groups = [g for g in quick if g != 'release']
            mocks = []
            if lanes_groups:
                mocks.append(('lanes mock', run(['lanes_mocktest.py', out, '--quick', ','.join(lanes_groups)], os.path.join(out, 'm.txt'))))
            if 'release' in quick:
                mocks.append(('release mock', run(['lanes_mocktest.py', out, '--release'], os.path.join(out, 'rm.txt'))))
            failed += [name for name, p in mocks if p.wait() != 0]
    for name, key in (('b.txt', 'native test'), ('b.txt', '.pack'), ('rb.txt', '.pack'), ('sb.txt', '.pack'), ('m.txt', 'mock test'), ('sm.txt', 'mock test'), ('rm.txt', 'mock test')):
        path = os.path.join(out, name)
        if os.path.exists(path):
            line = last(path, key)
            print('%-7s %s' % (name, line if line else '(no "%s" line)' % key))
    ok = 'ALL PASS' if quick is None else 'PASS'  # (quick: "QUICK PASS" of the lanes mock, "ALL PASS" of the release mock)
    for name in ('m.txt', 'sm.txt', 'rm.txt'):
        path = os.path.join(out, name)
        if os.path.exists(path) and not last(path, ok):
            err = last(path, 'Error')
            if err:
                print('%-7s %s' % (name, err[:300]))
    for name in ('m.txt', 'sm.txt', 'rm.txt'):  # a mock that did not end with ALL PASS has failed, whatever its exit code
        path = os.path.join(out, name)
        if os.path.exists(path) and not last(path, ok) and name not in failed:
            failed.append(name)
    print('%.0f s' % (time.time() - t0))
    print('FAILED: ' + ', '.join(failed) if failed else ('ALL PASS' if quick is None else 'QUICK PASS (groups %s)' % ','.join(quick)))
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
