"""Helpers for a mod's build script: fill Lua templates, strip debug lines, include shared Lua blocks.

Template conventions (the same ones the existing mods use):
    --NAME--            replaced by values[NAME] (str() of it); every --NAME-- left over is an error
    --INCLUDE:file--    replaced by the content of that file (looked up next to the template, then in TW3_Shared/templates)
    --@debug-begin / --@debug-end    block kept only in debug builds
    line ending in --@debug          line kept only in debug builds

    from wh3tools.build import fill, lua_bytes
    release = fill('battle.lua.tpl', {'VERSION': '0.9'}, debug=False)
"""
import os
import re

from . import paths

LEFTOVER = re.compile(r'--[A-Z][A-Z_0-9]*--')


def lua_bytes(b):
    """Bytes as a Lua string literal body ('\\072\\139...')."""
    return ''.join('\\%03d' % c for c in b)


def strip_debug(lua):
    out, skipping = [], False
    for line in lua.split('\n'):
        mark = line.strip()
        if mark == '--@debug-begin':
            assert not skipping, 'nested --@debug-begin'
            skipping = True
        elif mark == '--@debug-end':
            assert skipping, '--@debug-end without begin'
            skipping = False
        elif not skipping and not line.rstrip().endswith('--@debug'):
            out.append(line)
    assert not skipping, '--@debug-begin without end'
    lua = '\n'.join(out)
    assert '@debug' not in lua, 'a debug mark was left in the release build'
    return lua


def _include(name, here):
    for d in (here, paths.TEMPLATES):
        p = os.path.join(d, name)
        if os.path.exists(p):
            with open(p, encoding='utf8') as f:
                return f.read().rstrip()
    raise FileNotFoundError(name)


def fill(template, values=None, debug=True):
    """-> filled Lua as bytes (utf8)."""
    here = os.path.dirname(os.path.abspath(template))
    with open(template, encoding='utf8') as f:
        lua = f.read()
    for _ in range(5):  # includes may include
        new = re.sub(r'--INCLUDE:([\w.\-]+)--', lambda m: _include(m.group(1), here), lua)
        if new == lua:
            break
        lua = new
    if not debug:
        lua = strip_debug(lua)
    for name, value in (values or {}).items():
        lua = lua.replace('--%s--' % name, str(value))
    left = LEFTOVER.findall(lua)
    assert not left, (template, sorted(set(left)))
    return lua.encode('utf8')
