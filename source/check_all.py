"""Units Pass Through - every offline check in one go: builds the lanes and the scenario packs, then runs both mock tests AT THE SAME
TIME, and prints only the summary lines. Full outputs: <out_dir>\\b.txt, sb.txt, m.txt, sm.txt.

    python check_all.py <out_dir>     -> the last line is "ALL PASS" or "FAILED: ..."
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


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    out = os.path.abspath(sys.argv[1])
    os.makedirs(out, exist_ok=True)
    t0 = time.time()
    # the DB schema once before the builds run side by side (on a fresh copy both would download it into one file)
    shared = os.path.dirname(HERE)
    subprocess.run([sys.executable, '-c', 'import sys; sys.path.insert(0, sys.argv[1]); from wh3tools import db; db.schema()', shared], cwd=HERE)
    builds = [run(['lanes.py', 'build', out], os.path.join(out, 'b.txt')), run(['scenario.py', 'build', out], os.path.join(out, 'sb.txt'))]
    failed = [name for name, p in zip(('lanes build', 'scenario build'), builds) if p.wait() != 0]
    if not failed and run(['lanes.py', 'build', out, '--release'], os.path.join(out, 'rb.txt')).wait() != 0:
        failed.append('release build')
    if not failed:
        mocks = [run(['lanes_mocktest.py', out], os.path.join(out, 'm.txt')), run(['scenario_mocktest.py', out], os.path.join(out, 'sm.txt')),
                 run(['lanes_mocktest.py', out, '--release'], os.path.join(out, 'rm.txt'))]
        failed += [name for name, p in zip(('lanes mock', 'scenario mock', 'release mock'), mocks) if p.wait() != 0]
    for name, key in (('b.txt', 'native test'), ('b.txt', '.pack'), ('rb.txt', '.pack'), ('sb.txt', '.pack'), ('m.txt', 'mock test'), ('sm.txt', 'mock test'), ('rm.txt', 'mock test')):
        path = os.path.join(out, name)
        if os.path.exists(path):
            line = last(path, key)
            print('%-7s %s' % (name, line if line else '(no "%s" line)' % key))
    for name in ('m.txt', 'sm.txt', 'rm.txt'):
        path = os.path.join(out, name)
        if os.path.exists(path) and not last(path, 'ALL PASS'):
            err = last(path, 'Error')
            if err:
                print('%-7s %s' % (name, err[:300]))
    for name in ('m.txt', 'sm.txt', 'rm.txt'):  # a mock that did not end with ALL PASS has failed, whatever its exit code
        path = os.path.join(out, name)
        if os.path.exists(path) and not last(path, 'ALL PASS') and name not in failed:
            failed.append(name)
    print('%.0f s' % (time.time() - t0))
    print('FAILED: ' + ', '.join(failed) if failed else 'ALL PASS')
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
