"""NASM for memreader Plus code caves: assemble x64 source to raw bytes, or check hand-written bytes against it.

    python -m wh3tools.nasm asm <file.asm>                    print the bytes (hex) of a source file
    python -m wh3tools.nasm line "<instr>; <instr>; ..."      print the bytes of a few instructions

From Python:
    from wh3tools import nasm
    code = nasm.assemble('movaps xmm0, xmm1\\nsubss xmm0, [r10+0xec]')    # -> bytes (bits 64, flat binary)
    nasm.check(code_bytes, source)                                     # AssertionError naming the first difference

Sources are flat binaries: `bits 64` is added; `org` is 0, so `jmp label` / `call label` are relative to the cave.
Absolute targets (game routines) stay with the cave builder (they depend on where the cave is placed); constants
of a mod (field offsets) can be passed as `defines`, e.g. assemble(src, defines={'E_G': 0xe8}) -> %define E_G 0xe8.
NASM is installed with winget (NASM.NASM, 3.02, 2026-10-19) under %LOCALAPPDATA%\\bin\\NASM; it is only a build tool,
nothing of it goes into a pack.
"""
import os
import shutil
import subprocess
import sys
import tempfile


def exe():
    """Path of nasm.exe, or an error saying how to install it."""
    for path in (shutil.which('nasm'), os.path.join(os.environ.get('LOCALAPPDATA', ''), 'bin', 'NASM', 'nasm.exe'),
                 r'C:\Program Files\NASM\nasm.exe'):
        if path and os.path.isfile(path):
            return path
    raise RuntimeError('nasm.exe not found - install it: winget install --id NASM.NASM --exact')


def assemble(source, defines=None):
    """x64 source -> bytes (flat binary, org 0)."""
    head = 'bits 64\ndefault abs\n' + ''.join('%%define %s %s\n' % (k, v) for k, v in (defines or {}).items())
    work = tempfile.mkdtemp(prefix='wh3nasm_')
    try:
        src, out = os.path.join(work, 'cave.asm'), os.path.join(work, 'cave.bin')
        with open(src, 'w', encoding='utf8') as f:
            f.write(head + source + '\n')
        run = subprocess.run([exe(), '-f', 'bin', '-o', out, src], capture_output=True, text=True)
        if run.returncode != 0:
            raise ValueError('nasm: ' + (run.stderr or run.stdout).strip().replace(src, 'line'))
        with open(out, 'rb') as f:
            return f.read()
    finally:
        shutil.rmtree(work, ignore_errors=True)


def check(code, source, defines=None):
    """Asserts that hand-written bytes equal what NASM makes of the source."""
    want = assemble(source, defines)
    if bytes(code) != want:
        at = next((i for i in range(min(len(code), len(want))) if code[i] != want[i]), min(len(code), len(want)))
        raise AssertionError('bytes differ from NASM at offset %d: have %s, NASM %s' % (at, bytes(code[at:at + 8]).hex(' '), want[at:at + 8].hex(' ')))
    return want


def main():
    if len(sys.argv) >= 3 and sys.argv[1] in ('asm', 'line'):
        source = open(sys.argv[2], encoding='utf8').read() if sys.argv[1] == 'asm' else sys.argv[2].replace(';', '\n')
        print(assemble(source).hex(' '))
    else:
        print(__doc__)


if __name__ == '__main__':
    main()
