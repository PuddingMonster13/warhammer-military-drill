"""Total War .pack files (PFH5, the Warhammer III format): list, read, extract, write.

Reading only loads the file table; file contents are read on demand, so the multi-GB game packs are fine.

    python -m wh3tools.pack list <pack> [substring ...]
    python -m wh3tools.pack extract <pack> <out_dir> [substring ...]     (no substring = everything)
    python -m wh3tools.pack find <substring> [--mods]                   which game (or mod) packs hold a file

Paths inside a pack use '/' here; they are stored with '\\' in the file.
"""
import glob
import os
import struct
import sys

PFH5 = b'PFH5'
FLAG_ENCRYPTED_DATA = 0x10
FLAG_INDEX_TIMESTAMPS = 0x40
FLAG_ENCRYPTED_INDEX = 0x80
FLAG_EXTENDED_HEADER = 0x100
TYPE_MOD = 3
ZSTD_MAGIC = b'\x28\xb5\x2f\xfd'


class Pack:
    """Pack(path).files = {name: (offset, size, compressed)}; .read(name) -> bytes (decompressed)."""

    def __init__(self, path):
        self.path = path
        with open(path, 'rb') as f:
            head = f.read(28)
            self.magic = head[:4]
            if self.magic != PFH5:
                raise ValueError('%s: not a PFH5 pack (%r)' % (path, self.magic))
            self.flags, pack_count, pack_index_size, file_count, file_index_size, self.timestamp = struct.unpack('<6I', head[4:28])
            if self.flags & (FLAG_ENCRYPTED_DATA | FLAG_ENCRYPTED_INDEX):
                raise ValueError('%s: encrypted pack, not supported' % path)
            header_size = 28 + (20 if self.flags & FLAG_EXTENDED_HEADER else 0)
            f.seek(header_size)
            pack_index = f.read(pack_index_size)
            index = f.read(file_index_size)
        self.dependencies = [n.decode('utf8') for n in pack_index.split(b'\0') if n]
        self.pack_type = self.flags & 0xF
        self.files = {}
        p = 0
        offset = header_size + pack_index_size + file_index_size
        for _ in range(file_count):
            size, = struct.unpack_from('<I', index, p)
            p += 4
            if self.flags & FLAG_INDEX_TIMESTAMPS:
                p += 4
            compressed = index[p]
            p += 1
            end = index.index(b'\0', p)
            name = index[p:end].decode('utf8').replace('\\', '/')
            p = end + 1
            self.files[name] = (offset, size, compressed)
            offset += size

    def raw(self, name):
        offset, size, _ = self.files[name]
        with open(self.path, 'rb') as f:
            f.seek(offset)
            return f.read(size)

    def read(self, name):
        b = self.raw(name)
        if self.files[name][2]:
            b = decompress(b)
        return b

    def names(self, *substrings):
        subs = [s.lower().replace('\\', '/') for s in substrings]
        return [n for n in self.files if not subs or any(s in n.lower() for s in subs)]


def decompress(b):
    size, = struct.unpack_from('<I', b, 0)
    body = b[4:]
    if body[:4] == ZSTD_MAGIC:
        import zstandard
        return zstandard.ZstdDecompressor().decompress(body, max_output_size=size)
    if body[:4] == b'\x04\x22\x4d\x18':
        import lz4.frame
        return lz4.frame.decompress(body)
    import lzma
    return lzma.decompress(body)


def write_pack(path, files, dependencies=()):
    """Writes an uncompressed mod pack. files = {'script/campaign/mod/x.lua': bytes, ...}.
    Names are sorted like the game and RPFM expect. dependencies = names of packs this one needs (usually empty)."""
    pack_index = b''.join(d.encode('utf8') + b'\0' for d in dependencies)
    index = b''
    data = []
    for name in sorted(files, key=lambda n: n.lower()):
        content = files[name]
        if isinstance(content, str):
            content = content.encode('utf8')
        index += struct.pack('<I', len(content)) + b'\0' + name.replace('/', '\\').encode('utf8') + b'\0'
        data.append(content)
    with open(path, 'wb') as f:
        f.write(PFH5 + struct.pack('<6I', TYPE_MOD, len(dependencies), len(pack_index), len(files), len(index), 0))
        f.write(pack_index)
        f.write(index)
        for content in data:
            f.write(content)
    return os.path.getsize(path)


def extract(pack_path, out_dir, *substrings):
    """Extracts matching files; refuses names that would land outside out_dir. Returns the list of names."""
    pk = Pack(pack_path)
    out = os.path.abspath(out_dir)
    done = []
    for name in pk.names(*substrings):
        target = os.path.abspath(os.path.join(out, name))
        if not target.startswith(out + os.sep):
            raise ValueError('unsafe name in pack: %r' % name)
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, 'wb') as f:
            f.write(pk.read(name))
        done.append(name)
    return done


def game_packs():
    """The game's own packs in data/ (CA packs are the ones not listed as mods in the launcher; this takes every pack that
    is not a mod pack by its type flag)."""
    from . import paths
    out = []
    for p in sorted(glob.glob(os.path.join(paths.DATA, '*.pack'))):
        try:
            if Pack(p).pack_type != TYPE_MOD:
                out.append(p)
        except ValueError:
            pass
    return out


def mod_packs():
    from . import paths
    found = glob.glob(os.path.join(paths.WORKSHOP, '*', '*.pack')) + glob.glob(os.path.join(paths.DATA, '*.pack'))
    out = []
    for p in sorted(found):
        try:
            if Pack(p).pack_type == TYPE_MOD:
                out.append(p)
        except ValueError:
            pass
    return out


def _main(argv):
    if len(argv) >= 2 and argv[0] == 'list':
        pk = Pack(argv[1])
        print('%s  type %d  flags 0x%x  %d files' % (os.path.basename(argv[1]), pk.pack_type, pk.flags, len(pk.files)))
        for n in pk.names(*argv[2:]):
            print('%10d %s %s' % (pk.files[n][1], 'z' if pk.files[n][2] else ' ', n))
    elif len(argv) >= 3 and argv[0] == 'extract':
        names = extract(argv[1], argv[2], *argv[3:])
        print('%d files -> %s' % (len(names), argv[2]))
    elif len(argv) >= 2 and argv[0] == 'find':
        packs = mod_packs() if '--mods' in argv else game_packs()
        sub = argv[1].lower()
        for p in packs:
            hits = [n for n in Pack(p).files if sub in n.lower()]
            for n in hits[:50]:
                print('%s: %s' % (os.path.relpath(p, os.path.dirname(os.path.dirname(p))), n))
            if len(hits) > 50:
                print('%s: ... %d more' % (os.path.basename(p), len(hits) - 50))
    else:
        print(__doc__)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(_main(sys.argv[1:]))
