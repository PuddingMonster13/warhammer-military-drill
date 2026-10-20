"""The byte pattern of the game's unique_ui_id script function (from the mod "Skaven lore-firendly (no) friendly
fire", same author, MIT): the battle script finds the offset of a unit's id in the game's unit object with it."""
import re

P_UID_SRC = '''
48 8B F1                    ; mov rsi,rcx               this
48 8B FA                    ; mov rdi,rdx
48 8B CA                    ; mov rcx,rdx
E8 ?? ?? ?? ??              ; call
48 8B CF                    ; mov rcx,rdi
48 8B D8                    ; mov rbx,rax
E8 ?? ?? ?? ??              ; call
48 8B 4E 08                 ; mov rcx,[rsi+8]           Unit*
8B 91
|
?? ?? ?? ??                 ; mov edx,[rcx+0x3ea0]      unique_ui_id
48 8B CB                    ; mov rcx,rbx
E8 ?? ?? ?? ??              ; call
48 8B 5C 24 30              ; mov rbx,[rsp+0x30]
B8 01 00 00 00              ; mov eax,1
'''


def parse(src):
    """-> (pattern string for memreader, offset of the '|' mark in bytes)"""
    toks = []
    mark = None
    for line in src.splitlines():
        line = line.split(';')[0].strip()
        if not line:
            continue
        if line == '|':
            mark = len(toks)
            continue
        toks += line.split()
    for t in toks:
        assert t == '??' or re.fullmatch('[0-9A-F]{2}', t), t
    assert toks[0] != '??' and len(toks) <= 256, len(toks)
    return ' '.join(toks), mark


P_UID, UID_OFF = parse(P_UID_SRC)
