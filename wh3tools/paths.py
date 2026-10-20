"""Where the game lives. Found through Steam (registry and libraryfolders.vdf); override with environment
variables if that does not work:

    WH3_GAME       the game folder (the one with Warhammer3.exe)
    WH3_STEAMAPPS  the Steam library folder that holds it (...\\steamapps)
"""
import os
import re


def _steam_libraries():
    roots = []
    try:
        import winreg
        for hive, key in ((winreg.HKEY_CURRENT_USER, r'Software\Valve\Steam'), (winreg.HKEY_LOCAL_MACHINE, r'SOFTWARE\WOW6432Node\Valve\Steam')):
            try:
                with winreg.OpenKey(hive, key) as k:
                    for name in ('SteamPath', 'InstallPath'):
                        try:
                            roots.append(winreg.QueryValueEx(k, name)[0])
                        except OSError:
                            pass
            except OSError:
                pass
    except ImportError:
        pass
    libraries = []
    for root in roots:
        steamapps = os.path.join(root, 'steamapps')
        libraries.append(steamapps)
        vdf = os.path.join(steamapps, 'libraryfolders.vdf')
        if os.path.exists(vdf):
            for path in re.findall(r'"path"\s+"([^"]+)"', open(vdf, encoding='utf8', errors='replace').read()):
                libraries.append(os.path.join(path.replace('\\\\', '\\'), 'steamapps'))
    return libraries


def _find_steamapps():
    for steamapps in _steam_libraries():
        if os.path.exists(os.path.join(steamapps, 'appmanifest_1142710.acf')):
            return steamapps
    return ''


STEAMAPPS = os.environ.get('WH3_STEAMAPPS') or _find_steamapps()
GAME = os.environ.get('WH3_GAME') or os.path.join(STEAMAPPS, 'common', 'Total War WARHAMMER III')
DATA = os.path.join(GAME, 'data')
EXE = os.path.join(GAME, 'Warhammer3.exe')
WORKSHOP = os.path.join(STEAMAPPS, 'workshop', 'content', '1142710')
APPMANIFEST = os.path.join(STEAMAPPS, 'appmanifest_1142710.acf')
SHARED = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE = os.path.join(SHARED, 'cache')
TEMPLATES = os.path.join(SHARED, 'templates')
