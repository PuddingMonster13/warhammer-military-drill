"""DB tables: decode the game's tables, encode a mod's own table files.

    python -m wh3tools.db schema [--update]            download / refresh the RPFM schema (cache/schema_wh3.ron)
    python -m wh3tools.db table <table> [--grep text] [--limit n]   print rows of a vanilla table as JSON lines
    python -m wh3tools.db fields <table>                show the field list of the version the game uses

In Python:
    from wh3tools import db
    rows = db.table('effect_bundles_to_effects_junctions')   # vanilla rows (all game packs), cached per game build
    rows = db.decode(raw_bytes, 'effect_bundles_tables')      # one table file
    raw  = db.encode('effect_bundles_tables', version, rows)  # a table file for a mod pack

Table names may be given with or without the '_tables' suffix. Vanilla rows are cached in cache/db/<build>/ as JSON;
the cache is thrown away automatically when Steam updates the game (build id from the appmanifest).
"""
import json
import os
import re
import struct
import sys
import urllib.request
import uuid

from . import paths
from .pack import Pack, game_packs

SCHEMA_URL = 'https://raw.githubusercontent.com/Frodo45127/rpfm-schemas/master/schema_wh3.ron'
SCHEMA_PATH = os.path.join(paths.CACHE, 'schema_wh3.ron')
GUID_MARK = b'\xfd\xfe\xfc\xff'
VERSION_MARK = b'\xfc\xfd\xfe\xff'

_schema_text = None


def _tname(table):
    return table if table.endswith('_tables') else table + '_tables'


def schema(update=False):
    global _schema_text
    if update or not os.path.exists(SCHEMA_PATH):
        os.makedirs(paths.CACHE, exist_ok=True)
        tmp = SCHEMA_PATH + '.download'
        urllib.request.urlretrieve(SCHEMA_URL, tmp)
        os.replace(tmp, SCHEMA_PATH)
        _schema_text = None
    if _schema_text is None:
        with open(SCHEMA_PATH, encoding='utf8') as f:
            _schema_text = f.read()
    return _schema_text


def definitions(table):
    """{version: [(name, type, is_key), ...]} for a table, from the RPFM schema."""
    s = schema()
    t = _tname(table)
    start = s.find('\n        "%s": [' % t)
    if start < 0:
        raise KeyError('table %s is not in the schema (run: python -m wh3tools.db schema --update)' % t)
    end = s.index('\n        ],', start)
    block = s[start:end]
    out = {}
    for m in re.finditer(r'\(\s*version: (-?\d+),\s*fields: \[(.*?)\n                \],', block, re.S):
        fields = re.findall(r'name: "([^"]*)",\s*field_type: ([A-Za-z0-9_]+(?:\([^)]*\))?),\s*is_key: (true|false)', m.group(2))
        out[int(m.group(1))] = [(n, t_, k == 'true') for n, t_, k in fields]
    return out


def _rd(b, p, t):
    if t == 'Boolean':
        return bool(b[p]), p + 1
    if t == 'F32':
        return struct.unpack_from('<f', b, p)[0], p + 4
    if t == 'F64':
        return struct.unpack_from('<d', b, p)[0], p + 8
    if t == 'I16':
        return struct.unpack_from('<h', b, p)[0], p + 2
    if t == 'I32':
        return struct.unpack_from('<i', b, p)[0], p + 4
    if t == 'I64':
        return struct.unpack_from('<q', b, p)[0], p + 8
    if t == 'OptionalI32':
        if not b[p]:
            return None, p + 1
        return struct.unpack_from('<i', b, p + 1)[0], p + 5
    if t == 'ColourRGB':
        return b[p:p + 4].hex(), p + 4
    if t == 'StringU8':
        n, = struct.unpack_from('<H', b, p)
        return b[p + 2:p + 2 + n].decode('utf8'), p + 2 + n
    if t == 'StringU16':
        n, = struct.unpack_from('<H', b, p)
        return b[p + 2:p + 2 + 2 * n].decode('utf-16le'), p + 2 + 2 * n
    if t == 'OptionalStringU8':
        if not b[p]:
            return '', p + 1
        n, = struct.unpack_from('<H', b, p + 1)
        return b[p + 3:p + 3 + n].decode('utf8'), p + 3 + n
    if t == 'OptionalStringU16':
        if not b[p]:
            return '', p + 1
        n, = struct.unpack_from('<H', b, p + 1)
        return b[p + 3:p + 3 + 2 * n].decode('utf-16le'), p + 3 + 2 * n
    raise NotImplementedError('field type %s' % t)


def _wr(v, t):
    if t == 'Boolean':
        return b'\x01' if v else b'\x00'
    if t == 'F32':
        return struct.pack('<f', float(v))
    if t == 'F64':
        return struct.pack('<d', float(v))
    if t == 'I16':
        return struct.pack('<h', int(v))
    if t == 'I32':
        return struct.pack('<i', int(v))
    if t == 'I64':
        return struct.pack('<q', int(v))
    if t == 'OptionalI32':
        return b'\x00' if v is None else b'\x01' + struct.pack('<i', int(v))
    if t == 'ColourRGB':
        return bytes.fromhex(v)
    if t == 'StringU8':
        e = v.encode('utf8')
        return struct.pack('<H', len(e)) + e
    if t == 'StringU16':
        return struct.pack('<H', len(v)) + v.encode('utf-16le')
    if t == 'OptionalStringU8':
        if not v:
            return b'\x00'
        e = v.encode('utf8')
        return b'\x01' + struct.pack('<H', len(e)) + e
    if t == 'OptionalStringU16':
        if not v:
            return b'\x00'
        return b'\x01' + struct.pack('<H', len(v)) + v.encode('utf-16le')
    raise NotImplementedError('field type %s' % t)


def header(b):
    """-> (version, position of the row count)"""
    p = 0
    version = 0
    if b[:4] == GUID_MARK:
        n, = struct.unpack_from('<H', b, 4)
        p = 6 + 2 * n
    if b[p:p + 4] == VERSION_MARK:
        version, = struct.unpack_from('<i', b, p + 4)
        p += 8
    return version, p + 1


def decode(b, table):
    version, p = header(b)
    defs = definitions(table)
    if version not in defs:
        raise KeyError('%s version %d is not in the schema (have %s); update the schema' % (table, version, sorted(defs)))
    fields = defs[version]
    count, = struct.unpack_from('<I', b, p)
    p += 4
    rows = []
    for _ in range(count):
        row = {}
        for name, t, _k in fields:
            row[name], p = _rd(b, p, t)
        rows.append(row)
    if p != len(b):
        raise ValueError('%s: %d bytes left after %d rows: schema does not fit' % (table, len(b) - p, count))
    return rows


def encode(table, version, rows):
    """A table file for a mod pack (GUID + version header like RPFM writes). Missing fields raise KeyError."""
    fields = definitions(table)[version]
    guid = str(uuid.uuid4())
    out = [GUID_MARK, struct.pack('<H', len(guid)), guid.encode('utf-16le')]
    if version:
        out.append(VERSION_MARK + struct.pack('<i', version))
    out.append(b'\x01' + struct.pack('<I', len(rows)))
    for row in rows:
        for name, t, _k in fields:
            out.append(_wr(row[name], t))
    return b''.join(out)


def key_fields(table, version=None):
    defs = definitions(table)
    v = version if version is not None else max(defs)
    return [n for n, _t, k in defs[v] if k]


def game_build():
    try:
        with open(paths.APPMANIFEST, encoding='utf8') as f:
            m = re.search(r'"buildid"\s+"(\d+)"', f.read())
        return m.group(1) if m else 'unknown'
    except OSError:
        return 'unknown'


def table(name, packs=None):
    """All vanilla rows of a table (from every game pack that has files of it), cached per game build."""
    t = _tname(name)
    cache_dir = os.path.join(paths.CACHE, 'db', game_build())
    cache = os.path.join(cache_dir, t + '.json')
    if packs is None and os.path.exists(cache):
        with open(cache, encoding='utf8') as f:
            return json.load(f)
    rows = []
    for p in packs or game_packs():
        pk = Pack(p)
        for n in pk.names('db/%s/' % t):
            rows += decode(pk.read(n), t)
    if packs is None:
        os.makedirs(cache_dir, exist_ok=True)
        with open(cache, 'w', encoding='utf8') as f:
            json.dump(rows, f, ensure_ascii=False)
    return rows


def table_version(name):
    """The version number the game's own files of this table use."""
    t = _tname(name)
    for p in game_packs():
        pk = Pack(p)
        for n in pk.names('db/%s/' % t):
            return header(pk.read(n))[0]
    raise KeyError(t)


def _main(argv):
    if argv[:1] == ['schema']:
        schema(update='--update' in argv)
        print(SCHEMA_PATH, os.path.getsize(SCHEMA_PATH), 'bytes')
    elif argv[:1] == ['fields'] and len(argv) > 1:
        v = table_version(argv[1])
        print('%s version %d' % (_tname(argv[1]), v))
        for n, t, k in definitions(argv[1])[v]:
            print('  %-40s %-18s %s' % (n, t, 'key' if k else ''))
    elif argv[:1] == ['table'] and len(argv) > 1:
        grep = argv[argv.index('--grep') + 1].lower() if '--grep' in argv else None
        limit = int(argv[argv.index('--limit') + 1]) if '--limit' in argv else 0
        shown = 0
        for row in table(argv[1]):
            line = json.dumps(row, ensure_ascii=False)
            if grep and grep not in line.lower():
                continue
            print(line)
            shown += 1
            if limit and shown >= limit:
                break
    else:
        print(__doc__)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(_main(sys.argv[1:]))
