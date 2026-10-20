# Run 11 - AUTOMATIC scenario for the diagnostic probe (2026-10-19)

**Run 12 (lanes 0.7) uses the same scenario WITHOUT the diagnostic probe** (it was removed: it hooks the same game
code as the new walker-column patch). Packs for run 12: `!units_lanes_debug.pack` 0.7 + `!units_scenario_debug.pack`.

Packs in `data\` (all three ticked in the launcher, plus memreader Plus):
- `!units_lanes_debug.pack` - lanes test build 0.6 (the mod itself; finds the game code by pattern since 2026-10-19)
- `!units_diag_probe_debug.pack` - diagnostic probe 7 (only watches, changes nothing)
- `!units_scenario_debug.pack` - automatic scenario 1 (places the units and gives every order itself)

If a log says "this game build does not fit", the game was updated again: stop and report it.

## What you do
1. Start the game (restart it completely if it is running) and check in the launcher that the three packs are ticked.
2. Custom battle on an **open, flat map** (few trees and buildings; the units walk up to about 110 m).
3. Your army (Chaos Dwarfs as before) must contain:
   | Role | Unit | |
   |---|---|---|
   | A and W | 2 x Infernal Ironsworn | two infantry units of 20+ men, best the same type |
   | C | Hobgoblin Wolf Raiders (or Bull Centaurs) | one cavalry unit |
   | M | K'daai Destroyer | one single big monster (not the general) |
   | R | Hobgoblin Bolt Thrower | one artillery piece |
   The general and any other units are fine: they are sent 150 m behind the line. Any enemy army.
4. Deploy anywhere and press **Start battle**. Then only watch: do not give orders (the script controls every unit,
   the enemy's too - they stand still).
5. When `units_scenario_log.txt` says "SCENARIO FINISHED" (about 6-8 minutes), the control is yours again. You
   can play on with the mouse in the same battle (the enemy moves again from then on); the script now only writes
   down your orders ("ORDER ..." and "STOPPED ..." lines). Then leave the battle and say "check logs".

## What the script does (log: `units_scenario_log.txt`)
- Step 0: it takes control of all units, halts the enemy, places A, C, R in a line facing the enemy (45 m apart)
  and W, M 60 m behind; everybody stands for 10 s.
- Each pass: the standing unit is put back in place, the walker is placed 60 m away (artillery 40 m) and walks
  (never runs) through it to 50 m beyond (artillery 35 m); the next pass starts 5 s after the walker stopped.
  1. W through A (towards the enemy)
  2. M through A (away from the enemy)
  3. W through C
  4. M through C
  5. R through A (artillery moving)
  6. W through R (artillery standing: it must NOT make way)
  7. W through A diagonally (45 degrees: lanes along the walker's way)
- A pass whose units are missing is skipped (said in the log). On any error the script stops and gives the control
  back. At the end it gives the control of all units back.
- Note: orders given by script are not exactly mouse orders (e.g. formation width); the final check stays a normal
  battle played by hand.

## What each pass answers
| Pass | Question |
|---|---|
| 0 | what every unit consists of (sizes of the monster, the cavalry, the bolt thrower and its crew) |
| 1, 3, 6, 7 | which game routines place the models of a MARCHING infantry unit (needed for making it form columns) |
| 2, 4 | the same for a marching monster; whether infantry and cavalry open a wide enough gap for it |
| 3, 4 | what the models of standing cavalry do with the lane places they are sent to (why they did not move) |
| 5 | what a moving war machine consists of and how the infantry reacts to it |
| 6 | that standing artillery does not make way |
| 7 | lanes along a diagonal way |
