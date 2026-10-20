# Military Drill

A mod for **Total War: WARHAMMER III**: friendly units make way for each other.
A standing unit splits into blocks and opens lanes just in time, the walking unit narrows into columns, passes
through, and both reform behind it.

This repository holds the complete source, the offline tests and the automatic test scenario used during development.

## Requirements

- [memreader Plus](https://steamcommunity.com/sharedfiles/filedetails/?id=3811098873) by Druwski
  ([source](https://github.com/PKozdra/memreader_plus)): the mod hooks a few places of the game's code in memory.
- Optional: the [Mod Configuration Tool](https://steamcommunity.com/sharedfiles/filedetails/?id=2927955021)
  (on / off, blocks and columns, expendable and wavering units, logging).

## How it works

The game keeps a table of places for the models of a standing unit and walks every model back to its place. Four
small code caves (written out in `source/lanes.py`) shift those places sideways while lanes are open, make the game
hand them out at once, let the script give single models a place, and move the walker's marching points into the
nearest lane. Each cave is found by a byte pattern that must match exactly once (`source/sites.py`), and
`source/caves_listing.asm` is their disassembly, byte for byte; every build checks the caves against it. The battle
script (`source/lanes.lua.tpl`) decides who stands, who walks through whom, and when lanes open and close.

After a game update that changes the hooked code, the patterns no longer match and the mod switches itself off for
the battle. Single player only.

## Building and testing

Windows, Python 3, [NASM](https://www.nasm.us/) on the PATH, and the game installed through Steam (set `WH3_GAME`
if the game folder is not found).

    pip install -r requirements.txt
    cd source
    python check_all.py <out_dir>             builds everything and runs every offline test -> ALL PASS
    python lanes.py build <out_dir> --release the release pack military_drill.pack
    python lanes.py build <out_dir>           test build with a full log (units_lanes_log.txt in the game folder)
    python scenario.py build <out_dir>        the automatic test scenario

`lanes_mocktest.py` runs the battle script against a fake game and a fake memreader Plus. The `*_probe.*` files and
`research/` are the probes and tools used to find how the game moves models; most are tied to the game build they
were made on and are kept as a record. Listings of the game's own code are not published.

## License

MIT (see `LICENSE`). Total War: WARHAMMER III, its code and its content belong to Creative Assembly and SEGA; this
repository holds none of their files.
