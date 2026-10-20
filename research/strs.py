"""List C strings in Warhammer3.exe that match a regex (case-insensitive).

python strs.py <regex> [min length]
"""
import re, sys

sys.path.insert(0, __import__('os').path.dirname(__import__('os').path.dirname(__import__('os').path.abspath(__file__))))
from wh3tools.exe import Exe

x = Exe()
pat = re.compile(sys.argv[1].encode(), re.I)
minlen = int(sys.argv[2]) if len(sys.argv) > 2 else 6
seen = set()
for m in re.finditer(rb'[\x20-\x7e]{%d,200}' % minlen, x.m):
    s = m.group()
    if pat.search(s) and s not in seen:
        seen.add(s)
        r = x.rva(m.start())
        print('%8s  %s' % ('%x' % r if r else '-', s.decode()))
