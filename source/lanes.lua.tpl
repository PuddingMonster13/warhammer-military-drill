-- Military Drill --VERSION-- (--PACK--), --BUILD_KIND--.
-- A STANDING unit (not moving, not in melee, not routing) that a friendly MOVING unit is about to
-- walk through opens lanes along the way of that unit: its models are sent to places shifted sideways in blocks
-- (three caves in the game's "keep your places" code, see lanes.py); the walking unit squeezes into columns that
-- go through the lanes (cave 4, in the game's "walk to"); when it has passed, the places go back and the unit closes
-- up. When the split starts depends on both units: it is timed so that the lanes are open (and the columns formed)
-- about SPLIT_MARGIN seconds before the walker's front reaches the standing unit. For every army, the AI's too.
-- Needs memreader Plus. If anything does not match this game build, the mod does nothing.
-- Test build: it writes units_lanes_log.txt in the game folder.

local MOD = 'units_lanes'
local LOG_FILE = 'units_lanes_log.txt'
local TIMER = MOD .. '_tick'
local TICK_MS = 200
local COLLECT_EVERY = 50 -- ticks (10 s): look for new units
local REPORT_EVERY = 25 -- ticks (5 s)
local SLOW_LOOK = 0.004 -- s: a look this long takes a quarter of a frame at 60 fps (counted in the PERF lines)
local SHOT_TICKS = 25 -- a unit that used ammunition in the last 5 s counts as shooting
-- tuning
local SPLIT_MARGIN = 0.75 -- the lanes are open this many seconds before the walker's front reaches the standing unit (wanted: 0.25 .. 1.5)
local LOOK_SECONDS = 15.0 -- walkers further away than this (at their speed) are not looked at closely
local WAIT_TICKS = 4 -- opening: a model starts after a random wait of at most this many battle ticks (0.1 s each; the game's own is 15), divided by the unit's steadiness
local CLOSE_WAIT_TICKS = 5 -- the same for closing up
local LANE_FILES = 2 -- a lane of a walker of many models is at least this many files wide
local COLUMN_SHARE = 0.4 -- when the walker's formation cannot be read: all lanes together are this share of its front
local COLUMN_DEPTH = 2.5 -- a column of the walker is at most this many times as deep as the walker's own ranks: its
-- lane is as many files wide as that needs (a wide, shallow unit gets wide lanes, a deep, narrow one narrow lanes)
local RUN_BEYOND = 2.0 -- the game runs a model to a place further away than about this (0x3070e38): then it moves at fast_speed
local HOLD_TICKS = 8 -- lanes stay this long after the last moment a unit was passing (1.6 s)
local BLOCK = 4.0 -- metres of the standing unit between two lanes
local LANE_EXTRA = 0.5 -- lane width = 2 x radius of the walker's model + this
local LANE_MIN, LANE_MAX = 1.4, 15.0
local CREW_MARGIN = 1.5 -- a war machine's lane: its own width + this on each side for the crew
local WIDE_FRONT, WIDER_FRONT = 45.0, 80.0 -- a standing unit splits into 3 blocks (2 lanes), 5 blocks (4 lanes) from this
-- front width, 7 blocks (6 lanes) from the second one; the middle block always stays where it is
local BLOCK_MIN = 2.0
local COLUMN_LAG = 0.8 -- a marching point pulled d metres sideways comes 0.8 |d| nearer: outer files slow down and fall in
local COLUMN_MIN_AHEAD = 1.0 -- behind the inner ones (run 16: they pushed through the blocks instead)
-- vanilla units with the attribute 'expendable' (from the game's tables when the pack is built)
local EXPENDABLE = --EXPENDABLE--
-- walkers of the kinds in KIND keep their formation (see plan_lanes)
local LANE_WIDE_MAX = 40.0 -- one lane for a whole walker front (big bodies too close for lanes per file)
-- kinds of units from the game's tables (built into the pack): 'char', 'art', 'veh', 'mi', 'mc', 'inf', 'cav', 'mon'
local KIND = --KINDS--
local PARKED_TICKS = 25 -- a walker that stopped inside a standing unit holds its lanes open this long at most (5 s after it stopped)
local MAX_CANDIDATES = 10 -- walkers planned for at once per standing unit (the nearest)
local CLEAR_MARGIN = 1.5 -- m: a walker has left a standing unit when its rearmost body is this far beyond the unit's back rank
local STEP_MAX = 1.8 -- the lanes open in steps of at most this many metres (under RUN_BEYOND: the models walk, run 14
-- showed wolf riders running 5 m sideways at 9 m/s looking chaotic)
local ROOM_MARGIN = 0.3 -- metres kept free between a unit's models and half the gap to the unit beside it
local MAX_CLOSE_UP = 0.5 -- a standing unit closes up to at most this share of its width (G >= -0.5)
local SPEED_SAMPLES = 3 -- the measured speed of a walker counts after this many measurements (the first ones after a
-- teleport or a start are wrong: run 13 measured 5.7 m/s for walking Ironsworn and 15.7 m/s for a bolt thrower)
local MIN_MEN = 1 -- every unit makes way by its TYPE, not by its number of models; see makes_way()
local SINGLE_MAX_MEN = 1 -- a walker with this many models or fewer (a monster, a lord) gets ONE lane, centred on its way
-- who walks: also a unit with a few models caught in a fight, skirmishers and units on a drawn path
local ORDER_COS = 0.87 -- moving within 30 degrees of its order: the walker goes the order's straight line; otherwise
-- (skirmishing, a drawn path, leaving a fight) the way it is moving now
local MELEE_WALK = 1.5 -- a unit with models in melee walks only when it really moves this fast (m/s, measured)
-- artillery standing in the way: its machines stay, the lanes are the gaps between them
local GUN_GAP_SLACK = 0.25 -- the gaps between its machines may differ by this share of their mean
local GUN_MARGIN = 0.2
-- the pace of the split
local STILL_TICKS = 20 -- a unit makes way only after standing still this long (4 s). Runs 24 / 25 crashed when jobs
-- went to Mournfang mounts 2 s after the scenario had TELEPORTED the unit (run 25: the dump's model was the first job's
-- model); jobs 3-5 s after a teleport (runs 22 / 23) were fine. A teleport is no "moving" for the game, so a jump of
-- the unit's position starts the count again (JUMP metres between two looks)
local JUMP = 5.0 -- (1.0 until build52: run 33 showed unit:position() of STANDING units jumping 1.6 - 2.4 m now and then,
-- which restarted their count every few seconds - late or no openings; a teleport of the scenario moves 10 m and more)
-- (in one table: the main chunk of the game's Lua may hold 200 locals at most, and is near that)
local TUNE = {
	OUT_SHARE = 0.05, -- 085i: a standing unit goes beyond its outline by at most this share of its width per flank and of its depth in front / behind (guard mode: at most GUARD_OUT m); the rest comes from closing up and from extra ranks (FIRE.layout)
	OUT_SHARE_MAX = 0.10, -- 085i review: when nothing fits within OUT_SHARE (not in guard mode), up to this share per flank / in depth, logged as 'beyond the 5 % share'
	LAY_OBLIQUE = 30, -- degrees: a walker whose way is more than this far from the standing unit's depth axis is logged (the blocks grow along the lanes, the outline is checked in the unit's axes)
	SEND_MAX = 250, -- models sent per look at most: once that many went out, a whole closing-type send (closing up, quick / half close) waits for the next look
	GUARD_OUT = 1.0, -- m: a unit in guard mode steps out of its outline by at most this much per side
	GUARD_DEPTH_MAX = 3 * COLUMN_DEPTH, -- guard mode: ONE lane for a walker's columns only when the column is at most this many times as deep as the walker's own ranks
	SETTLED = 5.0, -- m: a unit this near its ordered place, with the same order, stands even while its models shuffle
	TURN_TICKS = 10, -- for 2 s after a new order a walker is turning: its way is the order's line, not its movement
	-- (run 34: walkers given new orders next to a unit were seen only at contact, "heading from its movement")
	OFF_LOOKS = 5, -- a walker's heading comes from its movement only after it moved off its order line this many looks
	-- in a row (one sample was enough, e.g. while it squeezed into columns: late openings 00:04:16)
	START_TICKS = 25, -- for 5 s after a new order the walker's speed counts at least as its nominal one (it accelerates;
	-- the measured one lags behind)
	SEEN_KEEP = 5, -- (log) a walker out of the candidates this many looks is "seen" anew
	OWN_MAX = 10, -- walkers side by side that get a lane each (own_lanes)
	MOVING_WAY = 10000, -- how far a walker without a usable order line is taken to go on
	CREW_REPOST = 1.0, -- s: while the lanes of artillery are open its moved crewmen get their jobs again this often (the
	-- game's own places are not shifted for artillery, so its 5 s test would send them back into the lanes)
	PACE_BEHIND = 0.7, -- models that came less than this share of the planned way by a step finish at a run at once
	PLAN_FAR = 3.0, -- s: a standing unit whose last plan left more margin than this plans again only every (kept at 3.0 in 085c: 1.5 opened accelerating walkers late)
	PLAN_EVERY = 3, -- this many looks (while its nearest walker and that one's order stay the same)
	LATE_SHARE = 0.5, -- a walker whose lane takes the whole standing front gets none when its contact comes before this
	-- share of the split (refuse only when hopeless - run-42 guns 2.4 / 6.4 s refused, bloodcrushers 5.0 / 8.3 s open)
	LATE_CLEAR = 10.0, -- m: a walker refused as too late stays left out until it is this far clear of the unit (run 43)
	LATE_INSIDE = 2.0, -- m: a walker whose front is already this far inside a standing unit when it would open the lanes is left to the game (run 51:
	-- the first of two ranged walkers made way for the second after it had stopped inside it, "fronts -11.5 m apart"); at least its speed x one look
	TURN_COS = 0.01, -- a walker whose cos to its order line grows by more than this per look is turning onto it
	PROBE_EVERY = 5, -- looks: a unit's first model is read this often as a baseline for teleports (see look)
	WATCH_TICKS = 10, -- looks: after unit:position() jumped, its first model is read on each look this long
	CREW_EVERY = 25, -- (log) looks: how far the crewmen of artillery stand from their machines is looked at this often
	CREW_AWAY = 8.0, -- (log) m: a crewman this far from its machine counts as away from it
	MISSED_DEPTH = 6.0, -- (log) m: a walker whose middle is this near a standing unit's middle line is inside it
	MISSED_KEEP = 150, -- (log) looks: the same walker inside the same unit is logged again after this long
	MISSED_LINES = 10, -- (log) MISSED lines at most per check (then "N more")
	COLS_DIRTY = 50, -- looks: a walker whose columns went off and that stands still within this long gets its places handed out at once
	SHAPE_AFTER = 50, -- (log) looks: the shape of a unit is checked this long after its lanes closed / its columns went off (and again)
	SHAPE_LINES = 30, -- (log) LONG LINES? lines at most per battle
	HELD_TICKS = 20, -- a unit that does not move but is not idle either (the AI holds it) stands after this many looks
	HELD_DRIFT = 5.0, -- m: ... while its position stays this near where it stopped (unit:position() jitters 2-9 m)
	-- 085d (bundle 1): big walkers
	BIG_RADIUS = 1.0, -- m: a walker whose model (or mount) is this big opens earlier and gets wider lanes (run 52: the blocks were not out when demigryphs arrived)
	BIG_SPEED = 5.0, -- m/s: a walker this fast opens earlier too
	BIG_MARGIN = 1.0, -- s: ... by this much more than SPLIT_MARGIN
	LANE_EXTRA_BIG = 1.5, -- m: lane width of a big walker's columns = 2 x radius + this (instead of LANE_EXTRA); lanes per file (vector) get it as far as a standing model fits between two lanes (085d review 1)
	B_CACHE_LOOKS = 5, -- looks: the lane count n of the B loop is kept for this many looks per (standing unit, walker, order) - it costs a make_room per candidate
	VECTOR_CLUSTER = 0.3, -- m: lanes per file only when every file's bodies lie within this of the file's place (spacing from the bodies)
	SQUEEZE_GAIN = 1.25, -- more lanes per side (auto) only when the squeezed spacing of a big walker's files grows by this factor
	-- 085d (bundle 2): the lane for the line of fire
	FIRE_GRACE = 7, -- looks: a lane for the line of fire stays this long after the gun last had a target to shoot at, + HOLD_TICKS (1.6 s) = about 3 s in all
	FIRE_DEBOUNCE = 10, -- looks (2 s): the guns' new target UNIT counts (full re-forming) only after the same new target was kept this long; the old plan stays meanwhile
	FIRE_GAP_DRIFT = 0.5, -- m: the gap is updated (only the models that must move are sent) when the line moved across by more than this; every FIRE.replan_every looks at most
	FIRE_MOVE_MIN = 0.3, -- m: a gap update sends no model whose place moves less than this
	FIRE_GAP_DIRTY = 0, -- 1: a gap update also makes the game hand out ALL places at once (E_DIRTY; the visible regroup). 0 (default): it does not - the models that must move get jobs,
	-- the game's own 5 s test hands the (nearly equal) places to the others (cave 2 only brings that test forward)
	FIRE_ENEMY_NEAR = 15.0, -- m: an enemy unit this near the standing unit (centre distance less both halves) closes its lane for the line of fire ...
	FIRE_ENEMY_SECONDS = 3.0, -- s: ... or one that reaches it within this many seconds at its measured speed (only with SPEED_SAMPLES measurements)
	FIRE_ENEMY_OPEN = 20.0, -- m: ... and no lane for the line of fire opens again before every enemy is further than this (hysteresis)
	STILL_FAST = 1.0, -- s: a unit that has just stopped stands after this long (not STILL_TICKS) when a friendly walker's way already reaches it (never near a teleport / jump)
	FIRE_BLOCK_TICKS = 100, -- looks: after a lane for the line of fire gave way to a walker, none opens in that unit until the walker has passed, 20 s at most
	-- 085e: ghosts (cave 5), narrowed big walkers (A2 / B1), the model cache (D1)
	GHOST = true, -- ethereal units go through friendly models (cave 5); false: the ghost list stays empty
	NARROW = true, -- a walker (of any army, the AI's too) that cannot squeeze into lanes is ordered narrow (the game forms the deep column) and gets ONE lane
	NARROW_FILES = 2, -- files wide the walker is ordered at most (fewer when the room beside the standing unit is short)
	NARROW_GOAL = 2.0, -- m: its order must end this far beyond the standing unit's back rank + the depth of the narrowed column
	NARROW_MAX = 2.0, -- the narrowed order is restored after this many times the expected pass time ...
	NARROW_MIN_S = 10.0, -- ... but not before this many seconds
	NARROW_GONE = 3, -- looks: a (walker, standing unit) pair that has ended for this long ends the narrowing for that unit
	NARROW_IDLE = 5, -- looks: a narrowed walker that does not move for this long is restored
	NARROW_FORGETS_MAX = 2, -- forgets (another order) of a walker in the same pass through one standing unit; then no narrowing for that unit until the pass ends
	NARROW_COOLDOWN = 3.0, -- s: after a narrowing was forgotten (another order) the walker is not narrowed again for this long (the AI's orders do not make a loop)
	NARROW_WAIT = 3, -- looks: the game has to show the narrowed order within this many looks
	-- 085f: closing at 90 % (F1), the narrowed column's extended goal (F2), the line of fire only for infantry (F4)
	CLOSE_PASSED_FRAC = 0.9, -- a served walker with this share of its bodies beyond the standing unit's back rank no longer holds the lanes open (a few tangled models do not)
	CLOSE_STRAGGLER_S = 3.0, -- s: a closing step waits for walker models still inside the unit this long in all, then the unit closes anyway
	CLOSE_FOLLOW_S = 1.0, -- s: a following walker is "soon" this much earlier than the closing arithmetic says (several units one after another through one unit)
	NARROW_GOAL_SLACK = 3.0, -- m: the game may move the extended goal of a narrowed order by this much; the read-back within it is still the mod's own order
	NARROW_FILES_MAX = 3, -- files wide the narrowed walker is ordered at most when two files do not fit the room / the goal
	-- 085g: the mod's own order is not forgotten while the game settles; the width asked for makes the files, which are counted
	NARROW_SETTLE = 2.0, -- s: after each order the mod wrote, the read-back may drift (goal within NARROW_GOAL_SLACK, width between the sent and the read one)
	NARROW_WIDTH_PAD = 0.5, -- x spacing: the sent width is files x spacing + this (the game made ONE file of (files - 1) x spacing, run 55)
	NARROW_COUNT_S = 1.0, -- s: the walker's files are counted this long after the game showed the order (the column re-forms)
	NARROW_COUNT_MAX = 4.0, -- s: a column that is still wider than ordered (not re-formed) is looked at again for this long, then taken as it is
	NARROW_FILE_GAP = 0.6, -- x spacing: bodies this far apart across the way are different files (085h: the gap is min(2 r, max(r + 0.3, this x the expected file spacing)))
	-- 085h: big walkers are never stuck: the restore of the wide order waits while the walker is in / before the standing unit, the files are counted once the column is steady
	NARROW_DEFER_MAX = 5.0, -- s: a restore of the wide order waits at most this long while the walker is still in / before the standing unit (085j: was 20)
	NARROW_CAV_MIN_FILES = 4, -- 085j: a cavalry walker of more than NARROW_CAV_MIN_MODELS bodies is narrowed to exactly this many files, never below (the room test decides, not LANE_MAX)
	NARROW_CAV_MIN_MODELS = 20, -- 085j: see NARROW_CAV_MIN_FILES
	NARROW_STEADY_S = 1.0, -- s: the across extent of the walker's bodies must have changed less than NARROW_STEADY_FRAC over this long before its files are counted
	NARROW_STEADY_FRAC = 0.10, -- share (and 0.2 m noise): see NARROW_STEADY_S
	FIRE_KINDS = { inf = true, mi = true }, -- only units of these kinds step aside for a line of fire (F4: no lane for artillery through cavalry)
	HOLD_MAX = 1.5, -- s: a running walker is held at a walk (while the standing unit splits) for this much extra time at most, compared with running
	MODEL_REFRESH = 50, -- looks: the cached model pointers / carriers of a unit are read anew this often even when its model list did not change
}
-- units of other mods (not in the vanilla tables): the vanilla castes as the game tells them. Run 25: the battle
-- interface has NO unit_caste(), only unit_class() (com, inf_mel, cav_shk, art_fld, chariot,...), which does not tell
-- monstrous units from others. So: lords / heroes, artillery and chariots by the vanilla marks, everything else kind
-- 'one': one lane, the walker in one column
local CASTE_KIND = { lord = 'char', hero = 'char', chariot = 'veh', warmachine = 'veh', monstrous_infantry = 'mi',
	monstrous_cavalry = 'mc', melee_infantry = 'inf', missile_infantry = 'inf', war_beast = 'inf', melee_cavalry = 'cav',
	missile_cavalry = 'cav', monster = 'mon' }
local BASE_KEEP_TICKS = 75 -- re-opening within 15 s of closing uses the places noted at the first opening
local COLUMN_POST = 1.0 -- the walker keeps its columns until its points are this far behind the standing unit
local COLUMN_LEAD = 2.0 -- and starts them this far + SPLIT_MARGIN seconds of its way + its widest sideways step before
-- the standing unit (its models are steered by points about 5 m ahead: they turn about 45 degrees into the lanes)

-- generated by lanes.py (do not edit by hand) -----------------------------------------------------------------------
-- the cave sites and the game's station routine, found by byte pattern (sites.py)
local SITES = {
--SITES--
}
local STATION_FN = --STATION_FN--
local HEAD = '--HEAD--'
local TABLE_SIZE = --TABLE_SIZE--
local ENABLE, COUNT, LIST, ENTRY, MAX_UNITS = --ENABLE--, --COUNT--, --LIST--, --ENTRY--, --MAX_UNITS--
-- (counters and the walker's column fields in tables: the main chunk may hold 200 locals at most, build49 had more)
local CN = { REACHED = --REACHED--, HUNG = --HUNG--, CHECKS = --CHECKS--, FORCED = --FORCED--, JOBS_DONE = --JOBS_DONE-- }
local SET_STATION, POOL = --SET_STATION--, --POOL--
-- 085i: the per-model place table of a unit (cave 1): pool pointer, hit / miss counters, the entry fields, record size, records at most
CN.PLACE_HIT, CN.PLACE_MISS, CN.E_PLACES, CN.E_NPLACES, CN.PLACE_REC, CN.PLACE_CAP = --PLACE_HIT--, --PLACE_MISS--, --E_PLACES--, --E_NPLACES--, --PLACE_REC--, --PLACE_CAP--
local E_OX, E_OZ, E_AX, E_AZ, E_P, E_L, E_N, E_DIRTY, E_WAIT, E_BIAS = --E_OX--, --E_OZ--, --E_AX--, --E_AZ--, --E_P--, --E_L--, --E_N--, --E_DIRTY--, --E_WAIT--, --E_BIAS--
local E_TPL, E_C5C, E_C63, E_SEQ, E_PENDING, E_JOBS, E_NJOBS = --E_TPL--, --E_C5C--, --E_C63--, --E_SEQ--, --E_PENDING--, --E_JOBS--, --E_NJOBS--
local JOB, JOB_RECORD, CAP, POOL_SIZE = --JOB--, --JOB_RECORD--, --CAP--, --POOL_SIZE--
local CARROT_RET, HALF = --CARROT_RET--, --HALF--
CN.CARROTS, CN.CARROT_SEEN, CN.CARROTS_NEG = --CARROTS--, --CARROT_SEEN--, --CARROTS_NEG--
local E_WQ = --E_WQ--
local EW = { OX = --E_WOX--, OZ = --E_WOZ--, AX = --E_WAX--, AZ = --E_WAZ--, BX = --E_WBX--, BZ = --E_WBZ--, T0 = --E_WT0--, T1 = --E_WT1--,
	C = --E_WC--, KL = --E_WKL--, KH = --E_WKH--, SL = --E_WSL--, SH = --E_WSH--, LAG = --E_WLAG--, MIN = --E_WMIN-- }
local E_G, E_S0 = --E_G--, --E_S0--
CN.WALKERS, CN.JOBS_ANY = --WALKERS--, --JOBS_ANY--
local GHOST = { COUNT = --GHOSTS--, SKIPS = --GHOST_SKIPS--, TO = --GHOST_TO--, LIST = --GHOST_LIST--, MAX = --GHOST_MAX--, SKIP_OFF = --GHOST_SKIP_OFF--, total = 0 } -- (cave 5, functions below)
local NARROW = { stats = { narrowed = 0, restored = 0, forgotten = 0, gave_up = 0 } } -- (085e: big walkers ordered narrow, functions below)
local CARROT_SITE = --CARROT_SITE--
local P_UID = '--P_UID--'
-- leadership (steadiness of a unit): x1 at LEAD.norm (the median base leadership of the vanilla units, from the game's tables); the
-- morale block of the Unit object is found from the code of is_routing (B) and is_wavering (S = B + 0x40); the current
-- leadership is the int at Unit* + B + 0x48 (run 48)
local LEAD = { norm = --LEAD_NORM--, route = '--LEAD_ROUTE--', route_at = --LEAD_ROUTE_AT--, waver = '--LEAD_WAVER--', waver_at = --LEAD_WAVER_AT--,
	state_in_block = 0x40, current_in_block = 0x48 }
local UID_OFF = --UID_OFF--
-- the line of fire (clearing it for the direct-fire guns, from the game's tables) and the Skaven mod's cave, which overrides it
local FIRE = { guns = --FIRE_GUNS--, ethereal = --ETHEREAL--, -- (ethereal: ghosts, unit keys from the game's tables: no lanes for them, none for their walk)
	skv = { site = '--SKV_SITE--', site_off = --SKV_SITE_OFF--, cave_a = '--SKV_CAVE_A--', cave_b = '--SKV_CAVE_B--', cave_c = '--SKV_CAVE_C--', off_enable = --SKV_OFF_ENABLE--, off_all = --SKV_OFF_ALL--,
		off_shooters = --SKV_OFF_SHOOTERS--, off_blockers = --SKV_OFF_BLOCKERS--, max_shooters = --SKV_MAX_SHOOTERS--, max_blockers = --SKV_MAX_BLOCKERS-- } }
-- end of generated part -------------------------------------------------------------------------------------------

--FIND_SITE--

local NEEDED = { 'find_pattern', 'hook_info', 'code_cave', 'game_alloc', 'alloc', 'read', 'read_original', 'write', 'read_pointer', 'read_uint32', 'read_float', 'ud_topointer', 'add', 'is_null', 'tostring', 'uint32' }
local COUNTERS = { COUNT, CN.REACHED, CN.HUNG, CN.CHECKS, CN.FORCED, CN.JOBS_DONE, CN.CARROTS, CN.CARROT_SEEN, CN.CARROTS_NEG, CN.PLACE_HIT, CN.PLACE_MISS }

local mr = nil
local state = { dead = false, tbl = nil, pool = nil, uid_offset = nil, buffer = nil, started = false, units = {}, slots = 0, ticks = 0, cfg = { enabled = true, lanes = 'auto', expendable = false, wavering = true, fire = true, logging = 'off' }, clock = nil, dt = TICK_MS / 1000, fighting = false, last = {}, opened = 0, cost = 0, longest = 0, ph = {}, ph_look = {}, sent_look = 0, deferred = 0, logbuf = {} }

-- the release build writes no file of its own: the MCT option "Logging" (level 0 off, the default / 1 problems / 2
-- basic) sends lines to the game's own mod log (lua_mod_log.txt). Problems: the start, switched off and why, script
-- errors, units left out. Basic: also the settings, every opening / closing / refusal / miss as one short line, and the
-- battle's cost at the end. The full log is the test build (lanes.py build without --release). Lines before the
-- settings are read wait in a short buffer.
local RELEASE = { on = --RELEASE--, level = 0, decided = false, buffer = {},
	keep = { { 'Military Drill', 1 }, { 'MOD OFF', 1 }, { 'ERROR', 1 }, { 'LEFT OUT', 1 }, { 'WALKER COLUMNS OFF', 1 }, { 'LEADERSHIP UNAVAILABLE', 1 },
		{ 'memreader Plus', 2 }, { 'settings', 2 }, { 'fighting starts', 2 }, { 'OPEN', 2, true }, { 'FIRE', 2, true },
		{ 'CLOSE ', 2, true }, { 'NO LANES', 2, true }, { 'WIDER', 2, true }, { 'MISSED?', 2, true }, { 'LONG LINES?', 2 }, { 'SHAPE CHECK', 2 },
		{ 'NARROW', 2, true }, { 'RESTORED', 2, true }, { 'GHOST', 2 } },
	LEVELS = { off = 0, problems = 1, basic = 2 } }

-- (test build) the buffered lines to the file
function RELEASE.flush()
	local lines = state.logbuf
	if #lines == 0 then return end
	state.logbuf = {}
	local file = io.open(LOG_FILE, state.started and 'a' or 'w')
	state.started = true
	if not file then return end
	file:write(table.concat(lines))
	file:close()
end

-- (release) one line to the game's mod log
function RELEASE.write(message)
	pcall(function() ModLog('[Military Drill] ' .. message) end)
end

-- (release) a line cut before its first ' | ' (the details of the test build's log); bytes only, no string.find
function RELEASE.cut(message)
	for i = 1, #message - 2 do
		if message:byte(i) == 32 and message:byte(i + 1) == 124 and message:byte(i + 2) == 32 then return (string.sub_lua or string.sub)(message, 1, i - 1) end
	end
	return message
end

-- (release) the logging level is known: the lines that waited are written or dropped
function RELEASE.decide(level)
	RELEASE.decided, RELEASE.level = true, level
	for _, line in ipairs(RELEASE.buffer) do
		if line[2] <= level then RELEASE.write(line[1]) end
	end
	RELEASE.buffer = {}
end

local function log(message)
	if RELEASE.on then
		message = tostring(message)
		for _, k in ipairs(RELEASE.keep) do
			if (string.sub_lua or string.sub)(message, 1, #k[1]) == k[1] then
				if k[3] then message = RELEASE.cut(message) end
				-- (switched off before the fighting: the MCT option is read on the spot, or the line would be lost)
				if not RELEASE.decided and k[1] == 'MOD OFF' then
					local ok, v = pcall(function() return get_mct():get_mod_by_key('military_drill'):get_option_by_key('military_drill_logging'):get_finalized_setting() end)
					RELEASE.decide(ok and RELEASE.LEVELS[v] or 0)
				end
				if not RELEASE.decided then
					if #RELEASE.buffer < 50 then RELEASE.buffer[#RELEASE.buffer + 1] = { message, k[2] } end
				elseif k[2] <= RELEASE.level then
					RELEASE.write(message)
				end
				return
			end
		end
		return
	end
	-- (test build) the lines of one look are written in one go at its end (opening and closing the file for every line took a
	-- share of the stutter spikes, run 51); outside a look at once
	state.logbuf[#state.logbuf + 1] = os.date('%H:%M:%S') .. ' ' .. tostring(message) .. '\n'
	if not state.in_tick then RELEASE.flush() end
end

-- CA's string.sub (and so s:sub) counts UTF-8 characters, not bytes: bytes >= 0x80 made the raw words short, the
-- station record slid by 5-6 bytes and its clock word became the model's state (runs 24-27 crashed at 0x32611ec)
local byte_sub = string.sub_lua or string.sub

-- the 4 bytes of a float, built from SMALL numbers only. Run 22: the game's Lua keeps numbers in single precision,
-- so whole numbers above 2^24 are rounded (4294967295 = -1 became 4294967296 -> bytes 0 0 0 0: cave 4 got lanes
-- k 0 .. 0 and sent every point to one lane). Words read from the game are copied as raw bytes for the same reason.
-- NaN / infinity by their text: the game's Lua compares NaN wrongly, so a range test alone does not catch it. Only
-- string.byte: run 31 (build49) used string.find(t, 'nan', 1, true) here, and in the game's Lua that broke the
-- whole Lua state at load (0 units listed, the timer manager failing, every later battle script not loaded; the
-- loadfast mod's notes describe the same). Never use find / match / gsub / lower / upper in these scripts.
local function not_a_number(x)
	local t = tostring(x)
	for i = 1, #t do
		local c = t:byte(i)
		-- digits, signs, the point and the exponent mark only ('nan', 'inf', '-nan(ind)', '1.#INF' have other signs)
		if not ((c >= 48 and c <= 57) or c == 43 or c == 45 or c == 46 or c == 69 or c == 101) then return true end
	end
	return false
end

local function float_bytes(x)
	if type(x) ~= 'number' or not_a_number(x) or not (x > -1000000 and x < 1000000) then error('not a usable number: ' .. tostring(x), 0) end
	if x == 0 then return '\0\0\0\0' end
	local sign = 0
	if x < 0 then sign, x = 1, -x end
	local m, e = math.frexp(x)
	local exponent = e + 126
	local fraction = math.floor((m * 2 - 1) * 8388608 + 0.5)
	if fraction == 8388608 then fraction, exponent = 0, exponent + 1 end
	if exponent < 1 or exponent > 254 or fraction % 1 ~= 0 or fraction < 0 or fraction > 8388607 then error('not a usable number: ' .. tostring(x), 0) end
	return string.char(fraction % 256, math.floor(fraction / 256) % 256, math.floor(fraction / 65536) + (exponent % 2) * 128, math.floor(exponent / 2) + sign * 128)
end

-- the 4 bytes of a small negative whole number (two's complement), built from small numbers only
local function s32(n)
	local m = -n - 1
	return string.char(255 - m % 256, 255 - math.floor(m / 256) % 256, 255 - math.floor(m / 65536) % 256, 255 - math.floor(m / 16777216) % 256)
end

local u32
local function put_float(offset, value)
	mr.write(state.tbl, offset, float_bytes(value))
end

-- a signed 32-bit word from its 4 raw bytes (a number read whole would be rounded in single precision: -1 showed as 0)
local function signed_word(raw)
	if type(raw) ~= 'string' or #raw ~= 4 then return 0 end
	local b1, b2, b3, b4 = raw:byte(1, 4)
	if b4 < 128 then return b1 + b2 * 256 + b3 * 65536 + b4 * 16777216 end
	return -((255 - b1) + (255 - b2) * 256 + (255 - b3) * 65536 + (255 - b4) * 16777216) - 1
end

-- the 4 bytes of a 32-bit number, lowest first
function u32(n)
	return string.char(n % 256, math.floor(n / 256) % 256, math.floor(n / 65536) % 256, math.floor(n / 16777216) % 256)
end

-- a float of the game from its 4 raw bytes at raw[i], or nil when it is not an ordinary number (NaN / infinity: the
-- game's Lua compares NaN wrongly) or out of range. Decoded here from small numbers only (exact in single precision;
-- read_uint32 would round 0xFFFFFFFF to 2^32): one memreader call per float, or one for x / y / z (the cost spikes were
-- the model reads, 8 calls per model)
local function float_of(raw, i)
	if type(raw) ~= 'string' or #raw < i + 3 then return nil end
	local b1, b2, b3, b4 = raw:byte(i, i + 3)
	local e = (b4 % 128) * 2 + math.floor(b3 / 128)
	if e == 255 then return nil end
	local m = (b3 % 128) * 65536 + b2 * 256 + b1
	local v = (e == 0) and m * 2 ^ -149 or (m + 8388608) * 2 ^ (e - 150)
	if b4 >= 128 then v = -v end
	if not (v > -100000 and v < 100000) then return nil end
	return v
end

local function get_float(pointer, offset)
	return float_of(mr.read(pointer, offset, 4), 1)
end

-- where a model stands: x, y, z (each nil when unreadable), with ONE memreader call
local function get_xyz(model)
	local raw = mr.read(model, 0x88, 12)
	return float_of(raw, 1), float_of(raw, 5), float_of(raw, 9)
end

-- close every lane and switch the caves off: the game is then exactly as without the mod
local function switch_off()
	if not state.tbl then return end
	mr.write(state.tbl, ENABLE, mr.uint32(0))
	mr.write(state.tbl, CN.WALKERS, mr.uint32(0))
	mr.write(state.tbl, CN.JOBS_ANY, mr.uint32(0))
	mr.write(state.tbl, GHOST.COUNT, mr.uint32(0)) -- (the ghost list: count first; the units in it stay as they are)
	for slot = 0, MAX_UNITS - 1 do
		mr.write(state.tbl, LIST + ENTRY * slot + E_L, mr.uint32(0))
		mr.write(state.tbl, LIST + ENTRY * slot + E_PENDING, mr.uint32(0))
		mr.write(state.tbl, LIST + ENTRY * slot + E_WQ, mr.uint32(0))
	end
end

local function give_up(reason)
	state.dead = true
	pcall(NARROW.restore_all, 'the mod is off') -- (085e: narrowed walkers get their own orders back)
	pcall(switch_off)
	state.in_tick = false -- (the line is written at once)
	log('MOD OFF: ' .. tostring(reason))
end

local function raw8(pointer)
	state.buffer = state.buffer or mr.alloc(16)
	mr.write(state.buffer, 0, pointer)
	return mr.read(state.buffer, 0, 8)
end

local function hex(pointer)
	return mr.tostring(pointer)
end

-- installing ------------------------------------------------------------------------------------------------------

local function install()
	for _, name in ipairs(NEEDED) do
		if type(mr[name]) ~= 'function' then error('this memreader has no ' .. name, 0) end
	end
	if not mr.base then error('this memreader does not tell where the game is loaded', 0) end
	local address, count = mr.find_pattern(P_UID)
	if not address or count ~= 1 then error('unit id code found ' .. tostring(count) .. ' times', 0) end
	state.uid_offset = mr.read_uint32(mr.add(address, UID_OFF), 0)
	if type(state.uid_offset) ~= 'number' or state.uid_offset < 8 or state.uid_offset > 0x100000 then error('implausible unit id offset', 0) end
	if float_bytes(1) ~= '\0\0\128\63' or float_bytes(-2.5) ~= '\0\0\32\192' or float_bytes(0.1) ~= '\205\204\204\61'
		or float_bytes(-214.53) ~= '\174\135\86\195' or s32(-1) ~= '\255\255\255\255' or s32(-3) ~= '\253\255\255\255' then error('float conversion is wrong', 0) end
	state.single = (16777217 == 16777216) -- true when the game's Lua numbers are single precision (logged at the start)
	if u32(1065353216) ~= '\0\0\128\63' then error('byte conversion is wrong', 0) end
	if float_of('\0\0\128\63', 1) ~= 1 or float_of('\0\0\32\192', 1) ~= -2.5 or float_of('\174\135\86\195', 1) ~= float_of(float_bytes(-214.53), 1)
		or float_of('\0\0\0\0', 1) ~= 0 or float_of('\255\255\255\255', 1) ~= nil or float_of('\0\0\128\127', 1) ~= nil then error('float decoding is wrong', 0) end
	local set_station = find_site(mr, STATION_FN)
	for _, site in ipairs(SITES) do site.at = find_site(mr, site) end
	local carrot_ret = find_site(mr, CARROT_SITE)
	local found = 0
	for _, site in ipairs(SITES) do
		site.info = mr.hook_info(site.at)
		if site.info and site.name == 'walk_to' and (site.info.kind ~= 'cave' or not site.info.cave or mr.read(site.info.cave, #HEAD + 8, #site.tail) ~= site.tail) then
			site.skip = true -- another mod (e.g. the diagnostic probe) hooks "walk to": no columns, the rest works
			log('WALKER COLUMNS OFF: something else hooks the game\'s "walk to" (remove !units_diag_probe_debug.pack)')
		elseif site.info and site.name == 'ghost_pass' and (site.info.kind ~= 'cave' or not site.info.cave or mr.read(site.info.cave, #HEAD + 8, #site.tail) ~= site.tail) then
			site.skip, GHOST.off = true, true -- something else hooks the collision pass: no ghosts, the rest works
			log('GHOST PASS OFF: something else hooks the game\'s collision pass')
		elseif site.info then
			if site.info.kind ~= 'cave' or not site.info.cave then error('something else hooks ' .. site.name, 0) end
			if mr.read(site.info.cave, #HEAD + 8, #site.tail) ~= site.tail then error('a cave of another version is in place: restart the game', 0) end
			local tbl = mr.read_pointer(site.info.cave, #HEAD)
			if mr.is_null(tbl) or (state.tbl and hex(tbl) ~= hex(state.tbl)) then error('caves without a common table: restart the game', 0) end
			state.tbl = tbl
			found = found + 1
		end
	end
	if found > 0 then
		-- the list still names the units of the last battle: empty it before anything else
		mr.write(state.tbl, ENABLE, mr.uint32(0))
		mr.write(state.tbl, CN.WALKERS, mr.uint32(0))
		mr.write(state.tbl, CN.JOBS_ANY, mr.uint32(0))
		mr.write(state.tbl, GHOST.COUNT, mr.uint32(0))
		mr.write(state.tbl, LIST, string.rep('\0', TABLE_SIZE - LIST))
		log(found .. ' caves of an earlier battle of this session found, reusing them')
	else
		state.tbl = mr.game_alloc(TABLE_SIZE) -- zeroed = switched off; never freed, the caves stay for the game session
	end
	-- the game routine cave 3 calls, and the memory for the jobs (one block for the whole game session)
	mr.write(state.tbl, SET_STATION, set_station)
	state.pool = mr.read_pointer(state.tbl, POOL)
	if mr.is_null(state.pool) then
		state.pool = mr.game_alloc(POOL_SIZE)
		mr.write(state.tbl, POOL, state.pool)
	end
	state.pplace = mr.add(state.pool, MAX_UNITS * CAP * JOB) -- 085i: the place tables follow the jobs in the same block
	mr.write(state.tbl, CARROT_RET, carrot_ret)
	put_float(HALF, 0.5)
	for _, site in ipairs(SITES) do -- cave 5 jumps here for a skipped pair (the end of the pair handler of the collision pass)
		if site.name == 'ghost_pass' then mr.write(state.tbl, GHOST.TO, mr.add(site.at, GHOST.SKIP_OFF)) end
	end
	state.columns = true
	for _, site in ipairs(SITES) do
		if site.skip and site.name == 'walk_to' then state.columns = false end
		if not site.info and not site.skip then
			local ok, problem = pcall(mr.code_cave, site.at, HEAD .. raw8(state.tbl) .. site.tail)
			if not ok then error('memreader refused the cave ' .. site.name .. ': ' .. tostring(problem), 0) end
			log('cave installed: ' .. site.name)
		end
	end
	if mr.read_uint32(state.tbl, ENABLE) ~= 0 or not mr.is_null(mr.read_pointer(state.tbl, LIST)) then error('the table is not empty', 0) end
	for _, offset in ipairs(COUNTERS) do state.last[offset] = mr.read_uint32(state.tbl, offset) end
end

-- units -------------------------------------------------------------------------------------------------------------

local function unit_pointer(unit)
	local pointer = mr.read_pointer(mr.ud_topointer(unit), 8)
	if mr.is_null(pointer) or mr.read_uint32(pointer, state.uid_offset) ~= unit:unique_ui_id() then return nil end
	return pointer
end

local function yes(unit, method)
	local ok, value = pcall(function() return unit[method](unit) end)
	return ok and value == true
end

-- a unit whose game object is no longer the one listed (gone from the battle), or that cannot be read any more: its
-- line stays (the list must not end early) but names no unit, so the caves pass it by; the script leaves it alone
local function retire(entry, why)
	entry.gone, entry.stands, entry.walks, entry.lane, entry.jobs_at, entry.narrow, entry.nfg = true, false, false, 0, nil, nil, nil
	mr.write(state.tbl, entry.at + E_L, mr.uint32(0))
	mr.write(state.tbl, entry.at + CN.E_NPLACES, mr.uint32(0))
	mr.write(state.tbl, entry.at + E_PENDING, mr.uint32(0))
	mr.write(state.tbl, entry.at + E_WQ, mr.uint32(0))
	mr.write(state.tbl, entry.at, '\1\0\0\0\0\0\0\0')
	if entry.columns_for then entry.columns_for.columns_of, entry.columns_for.col_zone, entry.columns_for = nil, nil, nil end
	local w = entry.columns_of
	if w then
		mr.write(state.tbl, w.at + E_WQ, mr.uint32(0))
		w.columns_for, entry.columns_of, entry.col_zone = nil, nil, nil
	end
	log(('LEFT OUT %s from now on: %s'):format(entry.label, tostring(why)))
end

-- every unit of the battle whose game object is confirmed through its id gets a line in the table (lanes closed)
local function collect_units()
	local known = {}
	for _, entry in ipairs(state.units) do
		known[entry.key] = entry
		if not entry.gone then -- still the same game object? (a unit that left the battle may be freed)
			local ok, pointer = pcall(unit_pointer, entry.unit)
			if not ok or not pointer or hex(pointer) ~= entry.key then retire(entry, 'its game object is not the listed one any more') end
		end
	end
	local alliances = bm.battle:alliances()
	for a = 1, alliances:count() do
		local armies = alliances:item(a):armies()
		for b = 1, armies:count() do
			local army = armies:item(b)
			local list = army:units()
			for index = 1, list:count() do
				local unit = list:item(index)
				local ok, pointer = pcall(unit_pointer, unit)
				if ok and pointer and not known[hex(pointer)] and state.slots < MAX_UNITS then
					local entry = { key = hex(pointer), unit = unit, pointer = pointer, alliance = a, army = army, shot_at = -1000, lane = 0, quiet = 0, closed_at = -100000,
						label = ('A%d.%d.%d %s'):format(a, b, index, tostring(unit:type())), at = LIST + ENTRY * state.slots,
						jobs = mr.add(state.pool, state.slots * CAP * JOB), places_at = mr.add(state.pplace, state.slots * CN.PLACE_CAP * CN.PLACE_REC), machine = yes(unit, 'is_war_machine'),
						expendable = EXPENDABLE[tostring(unit:type())] == true or yes(unit, 'is_expendable'), kind = KIND[tostring(unit:type())], ethereal = FIRE.ethereal[tostring(unit:type())] == true }
					-- lords and heroes never make way (by type; a unit not in the game's tables: by the script's own idea)
					if entry.kind == nil then -- a unit of another mod (not in the vanilla tables): the game's own vanilla marks
						local ok, caste = pcall(function() return unit:unit_caste() end) -- (not in battle so far: kept for a later game version)
						caste = ok and type(caste) == 'string' and caste or nil
						local okc, class = pcall(function() return unit:unit_class() end)
						class = okc and type(class) == 'string' and class or nil
						if yes(unit, 'is_commanding_unit') or class == 'com' then entry.kind = 'char'
						elseif yes(unit, 'is_artillery') or (class and class:sub(1, 3) == 'art') then entry.kind = 'art'
						elseif caste and CASTE_KIND[caste] then entry.kind = CASTE_KIND[caste]
						elseif class == 'chariot' then entry.kind = 'veh'
						else entry.kind = 'one' end -- no clear vanilla mark: like a monster, one lane, the walker in one column
						entry.guessed = true
						state.guessed = (state.guessed or 0) + 1
						log(('unit of another mod %s: kind %s (unit_class() %s, unit_caste() %s)'):format(entry.label, entry.kind, class or 'gives nothing', caste or 'gives nothing'))
					end
					entry.character = entry.kind == 'char'
					-- a crew that walks on foot beside its machine: artillery only (is_war_machine is also true for chariot-like
					-- machines whose crew RIDE, e.g. a Doomwheel; those make way through their carrier like a chariot)
					entry.crewed = entry.kind == 'art'
					-- the numbers first (lanes closed), the unit last: the caves only find complete lines
					put_float(entry.at + E_OX, 0)
					put_float(entry.at + E_OZ, 0)
					put_float(entry.at + E_AX, 1)
					put_float(entry.at + E_AZ, 0)
					put_float(entry.at + E_P, BLOCK)
					mr.write(state.tbl, entry.at + E_L, mr.uint32(0))
					mr.write(state.tbl, entry.at + E_N, mr.uint32(0))
					mr.write(state.tbl, entry.at + E_DIRTY, mr.uint32(0))
					mr.write(state.tbl, entry.at + E_WAIT, mr.uint32(WAIT_TICKS))
					mr.write(state.tbl, entry.at + E_BIAS, mr.uint32(0))
					mr.write(state.tbl, entry.at + E_G, mr.uint32(0))
					mr.write(state.tbl, entry.at + E_S0, mr.uint32(0))
					mr.write(state.tbl, entry.at + E_PENDING, mr.uint32(0))
					mr.write(state.tbl, entry.at + E_NJOBS, mr.uint32(0))
					mr.write(state.tbl, entry.at + E_JOBS, entry.jobs)
					mr.write(state.tbl, entry.at + CN.E_NPLACES, mr.uint32(0))
					mr.write(state.tbl, entry.at + CN.E_PLACES, entry.places_at)
					mr.write(state.tbl, entry.at, pointer)
					state.slots = state.slots + 1
					state.units[#state.units + 1] = entry
					known[entry.key] = entry
				end
			end
		end
	end
end

local function turn(a, b) -- degrees between two bearings
	local d = math.abs(a - b) % 360
	return d > 180 and 360 - d or d
end

-- (log) diagnostics, each logs only (one table: the main chunk is near the 200 locals of the game's Lua)
local diag = {}

-- (test build) the time of a look by phase, for the PERF line: where the time goes (the release build does not time)
function diag.t()
	return (not RELEASE.on) and os.clock() or 0
end

function diag.add(name, t0)
	if RELEASE.on then return end
	local d = os.clock() - t0
	state.ph[name] = (state.ph[name] or 0) + d
	state.ph_look[name] = (state.ph_look[name] or 0) + d
end

-- (test build) the time INSIDE serve by sub-phase, exclusive: only one sub-phase runs at a time. diag.sw_begin() starts serve of one unit
-- ('rest'), diag.sw(name) switches to a sub-phase and returns the one it interrupted (switch back with diag.sw(that)); outside serve it does
-- nothing (returns nil). The times are in state.ph['sv_' .. name]
function diag.sw_begin()
	if RELEASE.on then return end
	state.sw_name, state.sw_at = 'rest', os.clock()
end

function diag.sw(name)
	local prev = state.sw_name
	if RELEASE.on or not prev then return nil end
	local now = os.clock()
	local key = 'sv_' .. prev
	local d = now - state.sw_at
	state.ph[key] = (state.ph[key] or 0) + d
	state.ph_look[key] = (state.ph_look[key] or 0) + d
	state.sw_name, state.sw_at = name or prev, now
	return prev
end

function diag.sw_end()
	if RELEASE.on or not state.sw_name then return end
	diag.sw(state.sw_name)
	state.sw_name = nil
end

-- one check under pcall: its time counts apart (PERF "diagnostics"), its first error is logged once (a check that fails
-- silently looks like a check that found nothing)
function diag.run_check(name, check, arg)
	if RELEASE.on and not ((name == 'missed' or name == 'shape') and RELEASE.level >= 2) then return end
	local started = os.clock()
	local ok, problem = pcall(check, arg)
	state.diag_cost = (state.diag_cost or 0) + (os.clock() - started)
	diag.add('diag', started)
	diag.failed = diag.failed or {}
	if not ok and not diag.failed[name] then
		diag.failed[name] = true
		log(('  the %s check failed (logged once, it goes on): %s'):format(name, tostring(problem)))
	end
end

-- how far the crewmen of artillery stand from their machines: a line when that changes; artillery that never made way
-- shows what the game does by itself
function diag.crew(entry)
	local count, list = mr.read_uint32(entry.pointer, 0xa4), mr.read_pointer(entry.pointer, 0xa8)
	if count < 1 or count > 2000 or mr.is_null(list) then return end
	local away, crewmen, loose, far, spots = 0, 0, 0, 0, {}
	for index = 0, count - 1 do
		local model = mr.read_pointer(list, index * 8)
		if not mr.is_null(model) then
			local machine = mr.read_pointer(model, 0x2e0)
			if mr.is_null(machine) then
				loose = loose + 1
			else
				local key = hex(machine)
				local m = spots[key]
				if not m then
					local mx, _, mz = get_xyz(machine)
					m = { mx, mz }
					spots[key] = m
				end
				local x, _, z = get_xyz(model)
				if x and z and m[1] and m[2] then
					crewmen = crewmen + 1
					local d = math.sqrt((x - m[1]) ^ 2 + (z - m[2]) ^ 2)
					if d > TUNE.CREW_AWAY then away = away + 1 end
					if d > far then far = d end
				end
			end
		end
	end
	if crewmen == 0 or away == entry.crew_away then return end
	entry.crew_away = away
	local when = function(t) return (state.ticks - t) * TICK_MS / 1000 end
	log(('CREW %s: %d of %d crewmen more than %.0f m from their machines, the farthest %.1f m (%d models without a machine: the machines themselves, or crewmen that lost theirs) | %s; %s; %s'):format(
		entry.label, away, crewmen, TUNE.CREW_AWAY, far, loose,
		entry.lane > 0 and 'its lanes are open' or (entry.closed_at > -100000 and ('its lanes closed %.1f s ago'):format(when(entry.closed_at)) or 'it never made way'),
		entry.jumped_at and ('its models jumped %.1f s ago'):format(when(entry.jumped_at)) or 'no teleport seen',
		entry.moving and 'the game says it moves' or 'it stands'))
end

-- what a unit is doing now
local function look(entry)
	local unit = entry.unit
	local ammo = unit:ammo_left()
	if type(ammo) == 'number' then
		if entry.ammo and ammo < entry.ammo then entry.shot_at = state.ticks end
		entry.ammo = ammo
	end
	entry.men = unit:number_of_men_alive()
	entry.moving = unit:is_moving()
	entry.idle = unit:is_idle()
	local melee, routing = unit:is_in_melee(), unit:is_routing()
	local busy = melee or routing
	entry.busy = busy
	local was = entry.stands
	local og = entry.gx and { entry.gx, entry.gz, entry.gw } -- the order as it was at the last look
	local goal = unit:ordered_position()
	entry.gx, entry.gz, entry.gw, entry.gb = goal:get_x(), goal:get_z(), unit:ordered_width(), unit:ordered_bearing()
	local same_order = og and math.abs(entry.gx - og[1]) < 0.5 and math.abs(entry.gz - og[2]) < 0.5 and math.abs(entry.gw - og[3]) < 0.5
	if entry.narrow then -- (085e: the order the mod wrote is the same order; a different one forgets the narrowing. An error here must not retire the unit)
		local nok, nsame = pcall(NARROW.observe, entry, same_order)
		if nok then same_order = nsame else pcall(NARROW.fail, entry, nsame) end
	end
	if not same_order then entry.order_at = state.ticks end
	-- the template (the last place the game handed out in this unit) belongs to the facing the unit had then
	local seq = mr.read_uint32(state.tbl, entry.at + E_SEQ)
	if seq ~= entry.seq then entry.seq, entry.tpl_bearing = seq, entry.gb end
	local still = not entry.moving and entry.idle
	local why = (not still) and (entry.moving and 'the game says it moves' or 'the game says it is not idle') or nil
	if entry.lane > 0 and entry.goal then
		-- its own models walk to their lane places now, which may count as "moving": while the lanes are open the unit stands
		-- for as long as the formation the player gave it (place and width) is the same (a unit the AI holds: its goal may
		-- move within TUNE.SETTLED, the AI re-issues its orders)
		local tol = entry.held and TUNE.SETTLED or 0.5
		still = math.abs(entry.gx - entry.goal[1]) < tol and math.abs(entry.gz - entry.goal[2]) < tol and math.abs(entry.gw - entry.goal[3]) < tol
		why = (not still) and 'it was given another order' or nil
	elseif not still and same_order and entry.x and (entry.x - entry.gx) ^ 2 + (entry.z - entry.gz) ^ 2 < TUNE.SETTLED * TUNE.SETTLED then
		-- the same order and at its place, only its models shuffle (the 2nd unit in a row did not make way for the Hell Pit
		-- Abomination; every late one had stood exactly STILL_TICKS: pushed by the unit before it making way?): it still
		-- stands
		still, why = true, nil
	end
	-- not moving but not idle either (AI-held allies never stood): it stands once it has not moved for TUNE.HELD_TICKS,
	-- staying within TUNE.HELD_DRIFT m of where it stopped (it counts as standing since it stopped; when it moves again it
	-- no longer stands and its lanes close) (the count starts again whenever the unit is idle, moves, or its order moves
	-- more than TUNE.SETTLED - a place noted earlier would let it stand at once; small re-orders of the AI do not count)
	if not still and not entry.moving and entry.x and not (entry.lane > 0 and entry.goal) then
		local h = entry.held_at
		if not h or (entry.x - h[1]) ^ 2 + (entry.z - h[2]) ^ 2 > TUNE.HELD_DRIFT * TUNE.HELD_DRIFT
			or (entry.gx - h[4]) ^ 2 + (entry.gz - h[5]) ^ 2 > TUNE.SETTLED * TUNE.SETTLED then
			entry.held_at = { entry.x, entry.z, state.ticks, entry.gx, entry.gz }
		elseif state.ticks - h[3] >= TUNE.HELD_TICKS then
			still, why, entry.held = true, nil, true
			entry.still_since = entry.still_since or h[3]
			if not entry.held_said then
				entry.held_said = true
				log(('  %s stands although the game says it is not idle (it has not moved for %.1f s; logged once)'):format(entry.label, (state.ticks - h[3]) * TICK_MS / 1000))
			end
		end
	elseif not (entry.lane > 0 and entry.goal) then
		entry.held_at = nil
		if still or entry.moving then entry.held = nil end
	end
	if busy then why = melee and 'in melee' or 'routing' end
	if still and not busy then entry.still_since = entry.still_since or state.ticks else entry.still_since = nil end
	-- (units that shoot make way too; being shot at never kept a unit from it. shot_at is still kept: the artillery
	-- close uses it)
	if not entry.still_since then entry.fast_stand = nil end
	-- (085d: fast_stand is set by FIRE.fast_stands after the looks: a unit that has just stopped and that a friendly walker already comes for stands after
	-- TUNE.STILL_FAST instead of STILL_TICKS; it is cleared whenever the count starts again)
	entry.stands = still and not busy and (state.ticks - entry.still_since >= STILL_TICKS or entry.fast_stand == true) and entry.men >= MIN_MEN
	-- the settings (MCT): who makes way at all
	local cfg = state.cfg
	-- by type: lords and heroes never make way, every other unit does, however few models it has left
	if entry.stands and (entry.character or entry.ethereal or not cfg.enabled or (cfg.expendable and entry.expendable) or (cfg.wavering and yes(unit, 'is_wavering'))) then
		entry.stands = false
		-- (once per unit, for the log: run 34's Gnoblars never made way for the Ironblaster, and nothing said why)
		why = entry.character and 'a lord or hero (they never make way)' or (entry.ethereal and 'it is ethereal (a ghost unit neither splits nor needs a lane)') or (not cfg.enabled and 'the mod is switched off (MCT)')
			or ((cfg.expendable and entry.expendable) and 'it is expendable (MCT: expendable units make no way)') or 'it is wavering (MCT: wavering units make no way)'
		if not entry.character and not entry.cfg_said then
			entry.cfg_said = true
			log(('  %s makes no way: %s'):format(entry.label, entry.ethereal and 'it is ethereal (a ghost unit neither splits nor needs a lane)' or not cfg.enabled and 'the mod is switched off (MCT)'
				or ((cfg.expendable and entry.expendable) and 'it is expendable, and the MCT setting says expendable units make no way'
				or 'it is wavering (MCT: wavering units make no way)')))
		end
	end
	-- a walker may have models in a fight (a few caught by enemies): it walks if it really moves (below); not one that stands by the SETTLED
	-- rule (a few metres from its order, same order): it stood AND walked, and two such units inside each other made way for each other (run 51)
	entry.walks = entry.moving and not routing and entry.men >= 1 and entry.lane == 0 and not entry.stands and not yes(unit, 'is_currently_flying')
	if entry.columns_for and not entry.walks then -- it stopped or was ordered elsewhere: no columns
		mr.write(state.tbl, entry.at + E_WQ, mr.uint32(0))
		entry.cols_off_at, entry.cols_off_for = state.ticks, entry.columns_for.label
		entry.columns_for.cols_next = true -- (085d: serve gives a further walker its columns in these lanes, FIRE.columns_next)
		entry.columns_for.cols_done = entry.columns_for.cols_done or {} -- (085d review 5: this walker's columns are over, the next one is not it)
		entry.columns_for.cols_done[entry] = true
		entry.columns_for.columns_of, entry.columns_for.col_zone, entry.columns_for = nil, nil, nil
	end
	if entry.cols_off_at then -- its columns went off: when it stands still within 10 s, the game hands out its places at once
		if state.ticks - entry.cols_off_at > TUNE.COLS_DIRTY then
			entry.cols_off_at = nil
		elseif not entry.moving and not entry.columns_for then
			if not entry.crewed then mr.write(state.tbl, entry.at + E_DIRTY, mr.uint32(1)) end -- (artillery: its places are never handed out anew)
			entry.cols_off_at = nil
			diag.shape_arm(entry, 'walker', entry.cols_off_for or '?', 'columns off', entry.shape_base)
		end
	end
	-- the places noted for this unit are good only while it keeps the same formation and the same men
	if entry.base and entry.lane == 0 and (not entry.stands or entry.men ~= entry.base_men or state.ticks - entry.closed_at > BASE_KEEP_TICKS) then entry.base = nil end
	local p = unit:position()
	local x, z = p:get_x(), p:get_z()
	if state.dt <= 0 and entry.x then
		x, z = entry.x, entry.z -- the game is paused: nothing to measure
	elseif entry.x and entry.moving then
		local vx, vz = (x - entry.x) / state.dt, (z - entry.z) / state.dt
		local v = math.sqrt(vx * vx + vz * vz)
		local fast = unit:fast_speed()
		local limit = 1.5 * math.max(2, (type(fast) == 'number' and fast > 0.3 and fast < 40) and fast or 6)
		if v > 40 then
			entry.measured, entry.samples, entry.vx, entry.vz = nil, 0, nil, nil -- a jump (a teleport), not walking
		elseif v > limit then
			-- faster than it can run: unit:position() jumped (run 33: ~2 m now and then; it turned a walker's heading
			-- 60 deg off its way, so the unit in front was seen only at contact): this sample is left out
		else
			entry.measured = entry.measured and (entry.measured * 0.5 + v * 0.5) or v
			entry.vx = entry.vx and (entry.vx * 0.5 + vx * 0.5) or vx
			entry.vz = entry.vz and (entry.vz * 0.5 + vz * 0.5) or vz
			entry.samples = (entry.samples or 0) + 1
		end
	else
		entry.measured, entry.samples, entry.vx, entry.vz = nil, 0, nil, nil
	end
	-- a teleport moves the MODELS; unit:position() alone also jumps now and then (run 33: ~2 m, run 34: Rat Ogres 7-9 m
	-- right after their lanes closed). Only a jump of its first model (the same one) restarts the stand count. (The
	-- model is read here, not with models_of: that is defined further down.)
	-- (cheap: the first model is read once a second as a baseline, spread over the units, and on every look for 2 s
	-- after unit:position() jumped, to see whether the models follow - build55 read it every look: 3x the calls)
	local jumped = entry.x and (x - entry.x) ^ 2 + (z - entry.z) ^ 2 > JUMP * JUMP
	if jumped then entry.watch_until, entry.watch_jump = state.ticks + TUNE.WATCH_TICKS, math.sqrt((x - entry.x) ^ 2 + (z - entry.z) ^ 2) end
	local watching = entry.watch_until and state.ticks <= entry.watch_until
	if watching or (state.ticks + math.floor(entry.at / ENTRY)) % TUNE.PROBE_EVERY == 0 then
		local probe, px, pz = nil, nil, nil
		pcall(function()
			local count, list = mr.read_uint32(entry.pointer, 0xa4), mr.read_pointer(entry.pointer, 0xa8)
			if count >= 1 and count <= 2000 and not mr.is_null(list) then
				local m = mr.read_pointer(list, 0)
				if not mr.is_null(m) then
					local mx, _, mz = get_xyz(m)
					probe, px, pz = hex(m), mx, mz
				end
			end
		end)
		local moved = px and pz and probe == entry.probe and entry.probe_x and (px - entry.probe_x) ^ 2 + (pz - entry.probe_z) ^ 2 > JUMP * JUMP
		if moved then entry.jumped_at = state.ticks end
		if moved and entry.lane == 0 then
			entry.still_since, entry.stands, entry.fast_stand = state.ticks, false, nil -- it jumped (a teleport): it has to stand STILL_TICKS from now
			why = ('its models jumped %.1f m'):format(math.sqrt((px - entry.probe_x) ^ 2 + (pz - entry.probe_z) ^ 2))
		end
		-- the baseline: not while it waits for its models to follow a jump (a move found later must still count)
		if moved or not watching or probe ~= entry.probe then
			entry.probe, entry.probe_x, entry.probe_z = probe, px, pz
			if moved then entry.watch_until = nil end
		end
	end
	if entry.watch_until and state.ticks == entry.watch_until and not entry.jitter_said then
		entry.jitter_said = true
		log(('  %s: its position jumped %.1f m but its models did not - not a teleport, it goes on standing (logged once)'):format(entry.label, entry.watch_jump or 0))
	end
	if not entry.stands and not why then
		if entry.still_since and state.ticks - entry.still_since < STILL_TICKS then
			why = ('it has stood only %.1f s of %.1f (%.1f when a friendly walker already comes for it)'):format((state.ticks - entry.still_since) * TICK_MS / 1000, STILL_TICKS * TICK_MS / 1000, TUNE.STILL_FAST)
				.. ((entry.jumped_at and entry.jumped_at >= entry.still_since) and ' (its models jumped then)' or '')
		else
			why = 'no men left'
		end
	end
	entry.why_not = (not entry.stands) and why or nil
	-- (for the log: why a unit that stood stops standing; run 32's late openings had no answer to that)
	if was and not entry.stands then
		log(('  NOT STANDING %s any more: %s'):format(entry.label, why or 'wavering, or the settings'))
	end
	entry.x, entry.z = x, z
	entry.half = entry.gw / 2
	if entry.crewed and (state.ticks + math.floor(entry.at / ENTRY)) % TUNE.CREW_EVERY == 0 then diag.run_check('crew', diag.crew, entry) end
	if entry.walks then
		local dx, dz = entry.gx - entry.x, entry.gz - entry.z
		local length = math.sqrt(dx * dx + dz * dz)
		local v = entry.vx and math.sqrt(entry.vx * entry.vx + entry.vz * entry.vz) or 0
		local measured = (entry.samples or 0) >= SPEED_SAMPLES and v > 0.5
		-- how many looks in a row it has moved off its order line (more than 30 deg) WITHOUT turning towards it: a
		-- chariot or a Doomwheel drives an arc onto its new order for several seconds (run 35: Ikit Claw near the
		-- clanrats, lanes opened across his arc, 30-40 deg off his way); one that comes closer to its order line's
		-- direction look by look is turning, not skirmishing
		local cos = (measured and length >= 2) and (dx * entry.vx + dz * entry.vz) / (length * v) or nil
		local turning = cos and entry.last_cos and entry.last_order == entry.order_at and cos > entry.last_cos + TUNE.TURN_COS
		if cos and cos < ORDER_COS and not turning then
			entry.off_order = (entry.off_order or 0) + 1
		elseif not (cos and cos < ORDER_COS) then
			entry.off_order = 0
		end
		entry.last_cos, entry.last_order = cos, entry.order_at
		-- arriving at its order: it was 2 m or more from it at the last look (same order) and is nearer now, or has been arriving
		-- on this order and does not move away (a run steps over the 2 m)
		local same = entry.prev_len_order == entry.order_at and entry.prev_len
		local arriving = length < 2 and same and length <= entry.prev_len and (entry.prev_len >= 2 or entry.arriving_order == entry.order_at)
		if arriving then entry.arriving_order = entry.order_at end
		entry.prev_len, entry.prev_len_order = length, entry.order_at
		if melee and not (measured and v >= MELEE_WALK) then
			entry.walks = false -- in a fight and not going anywhere (its models chase their enemies)
		elseif length >= 2 and (not measured or state.ticks - (entry.order_at or -1000) < TUNE.TURN_TICKS or entry.columns_for
			or state.ticks - (entry.crossing_at or -1000) <= 1 or (entry.off_order or 0) < TUNE.OFF_LOOKS) then
			entry.dx, entry.dz, entry.way, entry.heading = dx / length, dz / length, length, 'order'
		elseif arriving then
			entry.walks = false -- it arrives at its order: no lanes for the last metres (its heading would be its movement, without an end)
			-- (a charging unit whose order stays at itself is no arriving walker: it walks on its movement, as ever)
		elseif measured then
			-- skirmishing, a drawn path (its order is the end of the path), leaving a fight: the way it moves now
			entry.dx, entry.dz, entry.way, entry.heading = entry.vx / v, entry.vz / v, TUNE.MOVING_WAY, 'movement'
		else
			entry.walks = false
		end
		if entry.walks then
			local speed = unit:is_moving_fast() and unit:fast_speed() or unit:slow_speed()
			entry.speed = (type(speed) == 'number' and speed > 0.3 and speed < 40) and speed or 4
		end
	end
end

-- the living models of a unit: count and list, or nil
local function models_of(entry)
	local count = mr.read_uint32(entry.pointer, 0xa4)
	local models = mr.read_pointer(entry.pointer, 0xa8)
	if count < 1 or count > 2000 or mr.is_null(models) then return nil end
	return count, models
end

local function plausible_radius(r)
	return r and r > 0.2 and r < 20 and r or nil
end

-- radius of the first model of a unit and of what carries it (the mount of a rider, the machine of a crewman: model
-- +0x2e0, run 11); returns the larger one (the lane must fit it) and the carrier's alone (nil if none). 0.6 when
-- unreadable. Read once per look (it is asked for many times per look)
local function model_radius(entry)
	if entry.radius_tick == state.ticks and entry.radius then return entry.radius, entry.radius_carried end
	local ok, radius, carried = pcall(function()
		local count, models = models_of(entry)
		if not count then return nil end
		local model = mr.read_pointer(models, 0)
		local own = plausible_radius(get_float(model, 0xa0))
		local carrier = mr.read_pointer(model, 0x2e0)
		local other = (not mr.is_null(carrier)) and plausible_radius(get_float(carrier, 0xa0)) or nil
		return math.max(own or 0, other or 0), other
	end)
	if not (ok and radius and radius > 0) then radius, carried = 0.6, nil end
	entry.radius, entry.radius_carried, entry.radius_tick = radius, carried, state.ticks
	return radius, carried
end

-- is a model near the unit's own middle? (run 30: right after the scenario's teleports models were still read at the
-- unit's old place, 40-150 m away: "fronts -32 m" and lanes opened far too late)
local function near_unit(entry, x, z)
	if not entry.x then return true end
	local reach = math.max(entry.half or 0, 10) + 30
	return (x - entry.x) ^ 2 + (z - entry.z) ^ 2 <= reach * reach
end

-- 085e (D1): the model list of a unit with ONE bulk read of its models array (the pointers of all models, 8 bytes each). When the bytes equal the
-- cached ones (plain ==) and the number of men is the same, the cached model pointers and their hex keys are reused (and the carriers bodies_of found:
-- list.cr); only the places (get_xyz) are read again. The cache is read anew every TUNE.MODEL_REFRESH looks. Returns { n, ptr[i], key[i], cr[i] } or nil.
local function model_list(entry)
	if entry.mlist_tick == state.ticks then return entry.mlist end -- (once per look)
	entry.mlist_tick, entry.mlist = state.ticks, nil
	local count, models = models_of(entry)
	if not count then return nil end
	local parts, at, bytes = {}, 0, count * 8
	while at < bytes do
		local n = math.min(4096, bytes - at)
		parts[#parts + 1] = mr.read(models, at, n)
		at = at + n
	end
	local raw = table.concat(parts)
	local c = entry.mcache
	if not (c and c.raw == raw and c.men == entry.men and state.ticks - c.tick < TUNE.MODEL_REFRESH) then
		c = { raw = raw, men = entry.men, tick = state.ticks, n = count, ptr = {}, key = {}, cr = {} }
		for index = 1, count do
			local model = mr.read_pointer(models, (index - 1) * 8)
			if mr.is_null(model) then
				c.ptr[index], c.key[index] = false, false
			else
				c.ptr[index], c.key[index] = model, hex(model)
			end
		end
		entry.mcache = c
		state.mlist_misses = (state.mlist_misses or 0) + 1
	else
		state.mlist_hits = (state.mlist_hits or 0) + 1
	end
	entry.mlist = c
	return c
end

-- where the models of a unit stand now: { model = { x, y, z } } (models that cannot be read are left out)
local function model_places(entry)
	if entry.places_tick == state.ticks and entry.places then return entry.places end -- the models do not move within a look
	local places = {}
	local list = model_list(entry)
	if not list then return places end
	local prev = diag.sw('near') -- (test build: serve's time by sub-phase)
	for index = 1, list.n do
		local model = list.ptr[index]
		if model then
			local x, y, z = get_xyz(model)
			if x and y and z then places[list.key[index]] = { x, y, z } end
		end
	end
	diag.sw(prev)
	entry.places, entry.places_tick = places, state.ticks
	return places
end

-- the same without the models far from the unit's middle (near_unit): what every plan, timing and measurement uses (the
-- walker's middle, its speed and its formation were still taken from all its models)
local function near_places(entry)
	if entry.near_tick == state.ticks and entry.near then return entry.near end
	local near = {}
	for key, p in pairs(model_places(entry)) do
		if near_unit(entry, p[1], p[3]) then near[key] = p end
	end
	entry.near, entry.near_tick = near, state.ticks
	return near
end

-- (log) SHAPE CHECK, for the player report "troops line up in long lines and do not recover": 10 s after a standing unit's
-- lanes closed, and 10 s after a walker's columns went off and it stands still, the width and depth of its models are
-- compared with the shape they had before (the places noted at the opening / before the columns); a unit much deeper or
-- narrower is logged (once, and again 10 s later: still / recovered) with what tells bad places from models that lag
function diag.shape_of(entry, places)
	local a = math.rad(entry.gb or 0)
	local fx, fz = math.sin(a), math.cos(a)
	local alo, ahi, dlo, dhi, n = nil, nil, nil, nil, 0
	for _, p in pairs(places) do
		local across, along = p[1] * fz - p[3] * fx, p[1] * fx + p[3] * fz
		alo, ahi = math.min(alo or across, across), math.max(ahi or across, across)
		dlo, dhi = math.min(dlo or along, along), math.max(dhi or along, along)
		n = n + 1
	end
	if n == 0 then return nil end
	return ahi - alo, dhi - dlo, n
end

function diag.shape_arm(entry, kind, other, why, base)
	if (RELEASE.on and RELEASE.level < 2) or entry.shape_due or not base or entry.gone then return end
	local ok, w0, d0, n0 = pcall(diag.shape_of, entry, base)
	if not ok or not n0 or n0 < 6 then return end -- (a few models have no shape to speak of)
	entry.shape_due = { at = state.ticks + TUNE.SHAPE_AFTER, kind = kind, other = other, why = why, base = base, w0 = w0, d0 = d0, tries = 0,
		order_at = entry.order_at, gw = entry.gw or w0,
		seq = mr.read_uint32(state.tbl, entry.at + E_SEQ) }
end

function diag.shape_run(entry)
	local due = entry.shape_due
	if not due or state.ticks < due.at then return end
	if entry.gone or entry.order_at ~= due.order_at then entry.shape_due = nil return end -- (a new order: it is meant to look different)
	if entry.moving or entry.lane > 0 or entry.columns_for or entry.columns_of then -- not at rest now: a little later, not for ever
		due.tries = due.tries + 1
		due.at = state.ticks + 25
		if due.tries > 4 then entry.shape_due = nil end
		return
	end
	local st = state.shape or { checked = 0, flagged = 0, recovered = 0, lines = 0 }
	state.shape = st
	local width, depth, n = diag.shape_of(entry, near_places(entry))
	if not n or n < 6 then entry.shape_due = nil return end
	local ow = math.min(due.gw, due.w0 + 1)
	local ed = math.max(due.d0, 2) * math.max(1, due.w0 / math.max(1, math.min(due.gw, due.w0))) -- (ordered narrower than it stood: deeper)
	local bad = depth > 1.8 * ed or width < 0.6 * ow
	local function text(head)
		local far = 0
		for key, p in pairs(near_places(entry)) do
			local b = due.base[key]
			if b and (p[1] - b[1]) ^ 2 + (p[3] - b[3]) ^ 2 > 4 then far = far + 1 end
		end
		local function field(offset) local ok, v = pcall(mr.read_float, state.tbl, entry.at + offset) return ok and v or 0 end
		return ('%s %s (%s of %s, %s): width %.1f of %.1f m, depth %.1f (expected %.1f), fields L %.1f G %.2f WQ %.1f pending %d jobs %d, places handed out by the game in the %d s: %d, models far from their game place: %d of %d'):format(
			head, entry.label, due.kind, due.other, due.why, width, ow, depth, ed, field(E_L), field(E_G), field(E_WQ), mr.read_uint32(state.tbl, entry.at + E_PENDING),
			mr.read_uint32(state.tbl, entry.at + E_NJOBS), TUNE.SHAPE_AFTER * TICK_MS / 1000, (mr.read_uint32(state.tbl, entry.at + E_SEQ) - due.seq) % 4294967296, far, n)
	end
	local function say(line)
		if st.lines < TUNE.SHAPE_LINES then
			st.lines = st.lines + 1
			log(line)
		end
	end
	if not due.flagged then
		st.checked = st.checked + 1
		if bad then
			st.flagged = st.flagged + 1
			say(text('LONG LINES?'))
			due.flagged, due.at = true, state.ticks + TUNE.SHAPE_AFTER
			return
		end
	elseif bad then
		say(text('LONG LINES? still:'))
	else
		st.recovered = st.recovered + 1
		say(('LONG LINES? recovered: %s (%s of %s): width %.1f, depth %.1f'):format(entry.label, due.kind, due.other, width, depth))
	end
	entry.shape_due = nil
end

function diag.shape_summary()
	local st = state.shape or { checked = 0, flagged = 0, recovered = 0 }
	log(('SHAPE CHECK: %d checked, %d flagged, %d recovered'):format(st.checked, st.flagged, st.recovered))
end

-- the bodies of a unit that really walk: a rider's or crewman's carrier (mount, wheel, machine) instead of the model.
-- Returns { { x, z } } and how many models ride a carrier (once per look)
local function bodies_of(w)
	if w.bodies_tick == state.ticks and w.bodies then return w.bodies, w.riders end
	local list, seen, riders = {}, {}, 0
	local ml = model_list(w) -- (085e D1: the model pointers, hex keys and carriers come from the cache while the models array is unchanged)
	local n = ml and ml.n
	local prev = diag.sw('near')
	if n and n > 0 and w.kind == 'inf' then
		-- a foot unit whose first model has no carrier has none at all: its bodies are its models, already read for model_places (infantry
		-- were read twice every look, run 51's stutter)
		local first = ml.ptr[1]
		if first and ml.first_free == nil then ml.first_free = mr.is_null(mr.read_pointer(first, 0x2e0)) end
		if first and ml.first_free then
			for _, p in pairs(near_places(w)) do list[#list + 1] = { p[1], p[3] } end
			w.bodies, w.riders, w.bodies_tick = list, 0, state.ticks
			diag.sw(prev)
			return list, 0
		end
	end
	for index = 1, (n or 0) do
		local model = ml.ptr[index]
		if model then
			local cr = ml.cr[index]
			if cr == nil then
				local carrier = mr.read_pointer(model, 0x2e0)
				if not mr.is_null(carrier) and hex(mr.read_pointer(carrier, 0x2e8)) == w.key then cr = { carrier, hex(carrier) } else cr = false end
				ml.cr[index] = cr
			end
			local body, key = model, ml.key[index]
			if cr then body, key, riders = cr[1], cr[2], riders + 1 end
			if not seen[key] then
				seen[key] = true
				local x, _, z = get_xyz(body)
				if x and z and near_unit(w, x, z) then list[#list + 1] = { x, z } end
			end
		end
	end
	w.bodies, w.riders, w.bodies_tick = list, riders, state.ticks
	diag.sw(prev)
	return list, riders
end

-- a unit that is ONE walking body: a single model, or one body its models ride (a Doomwheel, Ikit Claw)
local function one_body(e)
	if e.men <= SINGLE_MAX_MEN then return true end
	if e.kind == 'cav' and e.men > 4 then return false end -- one rider per mount: many bodies
	if e.kind == 'inf' and e.men > 4 then
		-- foot: many bodies, unless its models ride (2 reads: the first model's carrier)
		local ok, carried = pcall(function()
			local count, models = models_of(e)
			if not count then return false end
			return not mr.is_null(mr.read_pointer(mr.read_pointer(models, 0), 0x2e0))
		end)
		if not (ok and carried) then return false end
	end
	local ok, bodies, riders = pcall(bodies_of, e)
	return ok and riders > 0 and #bodies >= 1 and #bodies <= SINGLE_MAX_MEN
end

-- a walker of few big bodies: a chariot, war machine, monster, artillery, a single model, or one body many ride
local function big_walker(w)
	if w.kind == 'veh' or w.kind == 'art' then return true end
	if w.kind == 'mi' or w.kind == 'mc' then -- (a fast monstrous unit of many bodies forms columns like infantry: not merged into a wide lane)
		if w.big_at ~= state.ticks then -- (bodies_of once per look)
			local ok, bodies = pcall(bodies_of, w)
			w.big_at, w.big_few = state.ticks, not (ok and #bodies > 4)
		end
		return w.big_few
	end
	return one_body(w)
end

-- the places of a walker's walking bodies: monstrous infantry / cavalry and cavalry by their BODIES (riders and mounts would
-- count twice and the spacing came out about 1 / sqrt(2) too small), every other unit by its models
function FIRE.places(w)
	if w.kind == 'mi' or w.kind == 'mc' or w.kind == 'cav' then
		if w.fplaces_tick == state.ticks and w.fplaces then return w.fplaces, true end -- (085d: the list is read by several plans / tests of one look)
		local ok, bodies = pcall(bodies_of, w)
		if ok and #bodies >= 1 then
			local list = {}
			for i, b in ipairs(bodies) do list[i] = { b[1], 0, b[2] } end
			w.fplaces, w.fplaces_tick = list, state.ticks
			return list, true -- (true: bodies)
		end
	end
	return near_places(w)
end

-- the seconds before its front reaches the standing unit that the lanes are open for walker w (085d): a big body (radius
-- TUNE.BIG_RADIUS, model or mount) or a fast walker gets TUNE.BIG_MARGIN more (run 52: lane band at contact negative for every
-- big walker, demigryphs 7.5 m/s reached the blocks 1.6 - 4.0 s after the split began)
function FIRE.open_margin(w)
	local ok, r = pcall(model_radius, w)
	if (ok and r >= TUNE.BIG_RADIUS) or (w.speed or 0) > TUNE.BIG_SPEED then return SPLIT_MARGIN + TUNE.BIG_MARGIN end
	return SPLIT_MARGIN
end

-- guard mode (defend behaviour) of standing unit s, read once per look
function FIRE.guard_of(s)
	if s.guard_tick ~= state.ticks then
		local ok, g = pcall(function() return s.unit:is_behaviour_active('defend') end)
		s.guard_seen, s.guard_tick = ok and g == true, state.ticks
	end
	return s.guard_seen
end

-- smallest and largest coordinate of a unit's models near its middle along (bx, bz) from (ox, oz); nil when unreadable
local function extent_near(entry, ox, oz, bx, bz)
	local lo, hi = nil, nil
	for _, p in pairs(near_places(entry)) do
		local t = (p[1] - ox) * bx + (p[3] - oz) * bz
		if not lo or t < lo then lo = t end
		if not hi or t > hi then hi = t end
	end
	return lo, hi
end

-- how far the models are from where they stood when the lanes were opened (for the log only)
local function moved_since(entry)
	local ok, text = pcall(function()
		local now = model_places(entry)
		local seen, moved, farthest = 0, 0, 0
		for key, old in pairs(entry.stood or {}) do
			local new = now[key]
			if new then
				local d = math.sqrt((new[1] - old[1]) ^ 2 + (new[3] - old[3]) ^ 2)
				seen = seen + 1
				if d > 1 then moved = moved + 1 end
				if d > farthest then farthest = d end
			end
		end
		return ('%d of %d models stand more than 1 m from where they stood, the farthest %.1f m'):format(moved, seen, farthest)
	end)
	return ok and text or 'models not readable'
end

-- the same shift as cave 1 does: lanes = { ox, oz, ax, az, P, L, N, bias, G, S0 }
local function shifted(lanes, x, z)
	local s = (x - lanes[1]) * lanes[3] + (z - lanes[2]) * lanes[4]
	local n = math.max(-lanes[7], math.min(lanes[7], math.floor(s / lanes[5] + 0.5)))
	local d = (n + lanes[8]) * lanes[6] + (lanes[9] or 0) * (s - (lanes[10] or 0))
	return x + d * lanes[3], z + d * lanes[4]
end

-- [artillery part: tested on its own by the mock test]
-- artillery standing in the way: its machines cannot step aside, so the lanes are the gaps between them and beside the
-- outer ones. The crewmen not working their machine step into their machine's strip across the way (into_strip), so a
-- gap only has to hold the lane and the machines; when it is narrower, the walker's columns narrow to fit it, down to
-- one file. guns: the machines across the walker's way (from its line); lane: the width the walker's columns would
-- like; min_lane: one file of the walker; gun_r: radius of a machine. Returns origin (across), q, kl, kh, lane: lane
-- centres at origin + (k + 0.5) q for k = kl.. kh, lane the width they get; or nil and why.
local function gun_gaps(guns, lane, gun_r, min_lane, bare)
	table.sort(guns)
	local m = #guns
	if m == 0 then return nil, 'its machines cannot be read' end
	local need = lane + 2 * gun_r + 2 * GUN_MARGIN -- a lane between two machines
	local q = need
	if m >= 2 then
		q = (guns[m] - guns[1]) / (m - 1)
		for i = 2, m do
			local gap = guns[i] - guns[i - 1]
			if math.abs(gap - q) > GUN_GAP_SLACK * q then
				return nil, ('its machines stand unevenly (%.1f m apart, %.1f on average)'):format(gap, q)
			end
		end
		if q < need then
			local fit = q - 2 * gun_r - 2 * GUN_MARGIN
			if fit < min_lane then
				-- one file squeezed between the machines, without margins: the bare width of one walker model (the warp grinders
				-- went into the crews of a warp lightning cannon whose machines stood 10 m apart)
				local tight = q - 2 * gun_r
				if not bare or tight < bare then
					return nil, ('its machines stand %.1f m apart: too close even for a single file of %.1f m between them'):format(q, bare or min_lane)
				end
				fit = tight
			end
			lane = fit
		end
	end
	return guns[1], q, -1, m - 1, lane
end

-- where a crewman goes, a across and t along the walker's way, both from the middle of its own machine: at the
-- machine (inside its strip across the way) it works it and stays; beside it, it steps into the strip, and out of
-- the machine's ring along the way if it would stand on the machine
local function into_strip(a, t, gun_r, crew_r)
	local strip = math.max(0, gun_r - crew_r)
	if math.abs(a) <= strip then return a, t end
	local na = a < 0 and -strip or strip
	local ring = gun_r + crew_r
	if na * na + t * t < ring * ring then
		local dt = math.sqrt(ring * ring - na * na)
		t = t < 0 and -dt or dt
	end
	return na, t
end

-- where a crewman at a (across, from the origin of gun_gaps) goes: out of the lane it stands in, to its machine's side
local function out_of_gap(a, q, kl, kh, half)
	local k = math.max(kl, math.min(kh, math.floor(a / q)))
	local c = (k + 0.5) * q
	if math.abs(a - c) >= half then return a end
	return a < c and c - half or c + half
end
-- [artillery part: tested on its own by the mock test]

-- where a model of a standing unit goes with these lanes open (key: the model, for artillery crews)
local function place_of(lanes, x, z, key, key2)
	if not lanes.art then
		local px, pz = shifted(lanes, x, z)
		-- 085i: the layout of a closed-up block (FIRE.layout): the correction to the formula's place, grown with the steps (key2: the rider of a mount)
		local c = lanes.layout and ((key and lanes.layout[key]) or (key2 and lanes.layout[key2]))
		if c then
			local f = lanes.lay_f or 1
			px, pz = px + c[1] * f, pz + c[2] * f
		end
		return px, pz
	end
	local gun = key and lanes.gun_of[key]
	if gun then
		-- along the way: (az, -ax) is the walker's heading
		local ax, az = lanes[3], lanes[4]
		local a, t = (x - gun[1]) * ax + (z - gun[2]) * az, (x - gun[1]) * az - (z - gun[2]) * ax
		local na, nt = into_strip(a, t, lanes.gun_r, lanes.crew_r)
		return gun[1] + na * ax + nt * az, gun[2] + na * az - nt * ax
	end
	local a = (x - lanes[1]) * lanes[3] + (z - lanes[2]) * lanes[4]
	local d = out_of_gap(a, lanes.q, lanes.kl, lanes.kh, lanes[6] / 2 + lanes.crew_r) - a
	return x + d * lanes[3], z + d * lanes[4]
end

-- the farthest a model of a standing unit has to go
local function farthest_shift(lanes)
	-- (085i: the models of the layout also walk back / forward a rank depth: the split takes as long as their way)
	return math.max(lanes.shift or (lanes[8] ~= 0 and lanes[6] / 2 or lanes[6] * lanes[7]), lanes.lay_move or 0)
end

-- the template of a unit: what a place of this unit looks like besides x / y / z (kept by cave 1), or nil and why
local function template(entry)
	local at = entry.at + E_TPL
	if mr.read_uint32(state.tbl, entry.at + E_SEQ) == 0 then return nil, 'the game has not handed out places in this unit yet' end
	for offset = 0x10, 0x1c, 4 do
		if mr.read_uint32(state.tbl, at + offset) ~= 0 then return nil, 'its places hang on an object' end
	end
	if not entry.tpl_bearing or turn(entry.tpl_bearing, entry.gb) > 1 then return nil, 'the unit was turned since its places were last handed out' end
	-- the words copied into the station record go as raw bytes (single precision would round big ones: see float_bytes)
	local words = mr.read(state.tbl, at + 0x20, 8)
	if #words ~= 8 then return nil, 'its place words cannot be read' end
	return { facing = words:byte(3) + words:byte(4) * 256, facing_bytes = byte_sub(words, 3, 4) .. '\0\0', d24 = mr.read_uint32(state.tbl, at + 0x24),
		d24_bytes = byte_sub(words, 5, 8), k2c = mr.read_uint32(state.tbl, at + 0x2c), c5c = mr.read_uint32(state.tbl, entry.at + E_C5C),
		c5c_bytes = mr.read(state.tbl, entry.at + E_C5C, 4), c63 = mr.read_uint32(state.tbl, entry.at + E_C63) % 256 }
end

local function battle_tick(model)
	local ok, tick = pcall(function()
		local a = mr.read_pointer(model, 0x58)
		if mr.is_null(a) then return nil end
		local b = mr.read_pointer(a, 0x440)
		if mr.is_null(b) then return nil end
		return mr.read_uint32(b, 0x98)
	end)
	return ok and tick or nil
end

-- leaves a job for every model of the unit: "your station is <place>"; cave 3 hands it to the game when the model
-- ticks. lanes = nil sends the models back to the places noted when the lanes were opened.
-- Returns how many jobs were left, or 0 and why.
-- prev (085d, a gap update of the lane for the line of fire): the lanes the models were sent to last; a model whose place moves less than
-- TUNE.FIRE_MOVE_MIN from there gets no job (not while jobs of the last send are still waiting: they are void now). s.send_seen: the models
-- looked at, jobs: the ones sent.
local function send_stations(s, lanes, wait, prev)
	local t_sent = diag.t()
	local prev_sw = diag.sw('sends')
	if prev and (s.jobs_at or not lanes or lanes.art) then prev = nil end
	s.send_seen = 0
	mr.write(state.tbl, s.at + E_PENDING, mr.uint32(0)) -- older jobs are void; nobody looks at the jobs while they are written
	mr.write(state.tbl, s.at + CN.E_NPLACES, mr.uint32(0)) -- 085i: nor at the table of places while it is written (and a send that returns early leaves none)
	local tpl, why = template(s)
	if not tpl then diag.sw(prev_sw) return 0, why end
	local count, models = models_of(s)
	if not count then diag.sw(prev_sw) return 0, 'its models cannot be read' end
	if not s.base then
		if not lanes then diag.sw(prev_sw) return 0, 'no places noted' end
		s.base, s.base_men = {}, s.men
	end
	local jobs, tick = 0, nil
	local job_models = {}
	s.job_models = job_models
	s.via_mount = 0
	local given = {} -- one job per walking body: the riders of one carrier share it
	-- 085i: the table of places of cave 1 (models of the layout, with the place of this step); empty while it is written and when nothing is laid out
	local table_n, with_table = 0, lanes and lanes.layout and not lanes.art
	for index = 0, count - 1 do
		if jobs >= CAP then break end
		local model = mr.read_pointer(models, index * 8)
		local key, rider, rkey = nil, nil, nil
		if not mr.is_null(model) then
			-- a rider does not walk by itself (its tick skips the station code): its mount gets the job (run 11)
			local carrier = mr.read_pointer(model, 0x2e0)
			local mounted = not s.crewed and not mr.is_null(carrier) and hex(mr.read_pointer(carrier, 0x2e8)) == s.key
			rider, rkey = model, hex(model) -- (085i: the layout is keyed by the rider)
			if mounted then model = carrier end
			key = hex(model)
			if given[key] then
				key = nil
			else
				given[key] = true
				if mounted then s.via_mount = s.via_mount + 1 end
			end
		end
		if key then
			local base = s.base[key]
			if not base and lanes then
				local x, y, z = get_xyz(model)
				-- (a model read far from the unit's middle, e.g. at its old place right after a teleport, gets no job:
				-- it would be sent to a place shifted from where it is not)
				if x and y and z and near_unit(s, x, z) then
					base = { x, y, z }
					s.base[key] = base
					if s.crewed then -- where it stands beside its machine (the machine may be pushed meanwhile)
						local machine = mr.read_pointer(model, 0x2e0)
						if not mr.is_null(machine) then
							local mx, _, mz = get_xyz(machine)
							if mx and mz then base.machine, base.dx, base.dz = machine, x - mx, z - mz end
						end
					end
				end
			end
			local x, z = base and base[1], base and base[3]
			if base and lanes then x, z = place_of(lanes, x, z, key, rkey) end
			-- 085i: a model of the layout is written to the unit's table of places (cave 1 hands it this place when the game hands out places); a rider's mount too
			if base and with_table and (lanes.layout[key] or (rkey and lanes.layout[rkey])) and table_n < CN.PLACE_CAP then
				local rec = float_bytes(x) .. float_bytes(z)
				mr.write(s.places_at, table_n * CN.PLACE_REC, model)
				mr.write(s.places_at, table_n * CN.PLACE_REC + 8, rec)
				table_n = table_n + 1
				if rider and key ~= rkey and table_n < CN.PLACE_CAP then
					mr.write(s.places_at, table_n * CN.PLACE_REC, rider)
					mr.write(s.places_at, table_n * CN.PLACE_REC + 8, rec)
					table_n = table_n + 1
				end
			end
			-- back at the closing a crewman goes to its place beside its machine, where that machine stands now, found through
			-- its own carrier now (the noted places are stale when a machine was pushed; a noted machine pointer may be stale
			-- too). No job when its machine is another
			-- one, is unreadable, or the place is not near the unit: the game's own places hold then
			if base and not lanes and s.crewed then
				local now = mr.read_pointer(model, 0x2e0)
				local mx, mz = nil, nil
				if base.machine and not mr.is_null(now) and hex(now) == hex(base.machine) then
					local _
					mx, _, mz = get_xyz(now)
				end
				if mx and mz and near_unit(s, mx + base.dx, mz + base.dz) then x, z = mx + base.dx, mz + base.dz else base = nil end
			end
			-- a crewman gets a job only when it must leave a lane, and is sent back only when it did; the others keep the game's
			-- own places
			if base and s.crewed then
				s.crew_moved = s.crew_moved or {}
				if lanes then
					if (x - base[1]) ^ 2 + (z - base[3]) ^ 2 < 0.09 then base = nil else s.crew_moved[key] = true end
				elseif not s.crew_moved[key] then
					base = nil
				end
			end
			if base and prev then
				s.send_seen = s.send_seen + 1
				local px, pz = place_of(prev, base[1], base[3], key, rkey)
				if (x - px) ^ 2 + (z - pz) ^ 2 < TUNE.FIRE_MOVE_MIN * TUNE.FIRE_MOVE_MIN then base = nil end
			end
			if base then
				tick = tick or battle_tick(model) or 0
				-- the kind of the order, as the game works it out (0x3279824)
				local kind = (mr.read_uint32(model, 0x318) == 12) and 12 or 0
				if tpl.c63 ~= 0 then
					kind = nil -- the unit's own kind word, as it is
				elseif tpl.k2c == 5 then kind = 0x67
				elseif tpl.k2c == 6 then kind = 0x68
				elseif tpl.k2c == 7 then kind = 0x69
				elseif tpl.k2c == 8 then kind = 5
				elseif tpl.k2c == 9 then kind = 6
				end
				local record = float_bytes(x) .. float_bytes(base[2]) .. float_bytes(z) .. u32(0) .. string.rep('\0', 16)
					.. tpl.facing_bytes .. (kind and u32(kind) or tpl.c5c_bytes) .. tpl.d24_bytes .. u32((tick + math.random(0, wait)) % 4294967296) .. u32(0) .. u32(0)
				-- a record of another length would hand the game shifted words (runs 24-27): no job at all then
				if #record ~= JOB - JOB_RECORD then error(('station record of %d bytes'):format(#record), 0) end
				mr.write(s.jobs, jobs * JOB + JOB_RECORD, record)
				mr.write(s.jobs, jobs * JOB, model)
				jobs = jobs + 1
				if jobs <= 16 then job_models[jobs] = key end -- for the log: a crash dump names the model it was in
			end
		end
	end
	mr.write(state.tbl, s.at + CN.E_NPLACES, mr.uint32(table_n)) -- (085i: from now on cave 1 looks the models up)
	mr.write(state.tbl, s.at + E_NJOBS, mr.uint32(jobs))
	mr.write(state.tbl, s.at + E_PENDING, mr.uint32(jobs))
	if jobs > 0 and not state.jobs_any then -- cave 3 looks for jobs from now on (update switches it back)
		mr.write(state.tbl, CN.JOBS_ANY, mr.uint32(1))
		state.jobs_any = true
	end
	s.jobs_at = state.ticks
	state.sent_look = state.sent_look + jobs
	diag.add('sends', t_sent)
	diag.sw(prev_sw)
	return jobs, tpl
end

-- a look sends at most TUNE.SEND_MAX models: once that many went out, a WHOLE closing-type send (closing up, quick close, half close /
-- restore) waits for the next look (never half a send: send_stations voids the pending jobs; never an opening, a step or a widening)
-- (a send deferred 3 looks in a row is sent at the next one whatever the look has sent: s.defer_n)
function FIRE.busy_sending(s)
	if state.sent_look >= TUNE.SEND_MAX and (s.defer_n or 0) < 3 then
		s.defer_n = (s.defer_n or 0) + 1
		state.deferred = state.deferred + 1
		return true
	end
	s.defer_n = 0 -- (the send happens now)
	return false
end

-- are the bodies of walker w inside standing unit s, whose models stand at places: one of them between the front of s and
-- CLEAR_MARGIN beyond its back rank, and within its width? Moving or not: one that stops inside keeps the lanes open.
local function overlaps(s, w, places, margin)
	if w.gone or not w.dx or not places then return false end
	local ax, az = -w.dz, w.dx
	local front, back, smin, smax = nil, nil, nil, nil
	for _, q in pairs(places) do
		local t, a = q[1] * w.dx + q[3] * w.dz, q[1] * ax + q[3] * az
		if not front or t < front then front = t end
		if not back or t > back then back = t end
		if not smin or a < smin then smin = a end
		if not smax or a > smax then smax = a end
	end
	if not front then return false end
	local r, reach = model_radius(w), model_radius(s)
	margin = margin or (w.walks and CLEAR_MARGIN or 0) -- one that stopped holds them only while it really overlaps
	for _, b in ipairs(bodies_of(w)) do
		local t, a = b[1] * w.dx + b[2] * w.dz, b[1] * ax + b[2] * az
		if t + r > front - reach and t - r < back + reach + margin and a + r > smin - reach and a - r < smax + reach then return true end
	end
	return false
end

-- is walker w still inside standing unit s (as s stood at the opening)?
local function still_inside(s, w)
	return overlaps(s, w, s.stood)
end

-- does the order of walker w end before the real front of standing unit s (the nearest model along its way, less the radius;
-- the walker's own front bodies and radius and 1 m counted)? Only asked when the order ends short of the middle of s.
function FIRE.stops_short(w, s)
	local ok, places = pcall(near_places, s)
	if not ok then return false end
	local front = nil
	for _, p in pairs(places) do
		local t = (p[1] - w.x) * w.dx + (p[3] - w.z) * w.dz
		if not front or t < front then front = t end
	end
	if not front then return false end
	local ahead = 0 -- how far its front bodies lie ahead of its middle
	local bok, bodies = pcall(bodies_of, w)
	for _, b in ipairs(bok and bodies or {}) do
		ahead = math.max(ahead, (b[1] - w.x) * w.dx + (b[2] - w.z) * w.dz)
	end
	local stops = w.way + ahead + (model_radius(w)) + 1 < front - (model_radius(s))
	if not RELEASE.on then -- (log, debug level only) once per walker / stander / order: what the decision rests on (run 50: a walker ordered to stop short opened lanes)
		w.stops_said = w.stops_said or {}
		local key = s.key .. ':' .. tostring(w.order_at)
		if not w.stops_said[key] then
			w.stops_said[key] = true
			local n, sx, sz = 0, 0, 0
			local nok, near = pcall(near_places, w)
			for _, p in pairs(nok and near or {}) do n, sx, sz = n + 1, sx + p[1], sz + p[3] end
			log(('  G2? %s, order of %s: way %.1f, stander front %.1f, walker front bodies %.1f ahead, radii %.2f / %.2f -> %s | position() %.1f / %.1f, ordered_position() %.1f / %.1f, models middle %s, heading %s'):format(
				s.label, w.label, w.way, front, ahead, model_radius(w), model_radius(s), stops and 'stops short: no lanes' or 'reaches it: lanes', w.x, w.z, w.gx or 0, w.gz or 0,
				n > 0 and ('%.1f / %.1f'):format(sx / n, sz / n) or 'unreadable', tostring(w.heading)))
		end
	end
	return stops
end

-- is walker w about to pass through standing unit s? Returns how far ahead of w the middle of s is, or nil (and 'leaving'
-- when it walks away from s)
local function passing(w, s)
	local rx, rz = s.x - w.x, s.z - w.z
	local along = rx * w.dx + rz * w.dz
	local across = math.abs(rx * w.dz - rz * w.dx)
	if across > s.half + w.half * 0.5 then return nil end -- walks past it
	if along < 0 then -- the middle of s lies behind the middle of w
		if s.lane > 0 and s.served and s.served[w] and s.opened_at and (w.order_at or -1000) <= s.opened_at then
			-- the lanes opened for this order: they stay while it crosses, and close when it is well past (as ever)
			if along < -(s.half + 5) then return nil end
		else
			-- walking away from s (it was never in front of it, or turned back): it counts only while its bodies still
			-- overlap s (as it stood at the opening, or as it stands now when it has no lanes: it may still get them)
			local ok, inside = false, false
			if s.lane > 0 then
				ok, inside = pcall(still_inside, s, w)
			elseif along >= -(s.half + 5) then -- (further behind than that it is not worth reading the models)
				ok, inside = pcall(function() return overlaps(s, w, near_places(s)) end)
			end
			if not (ok and inside) then return nil, 'leaving' end
		end
	end
	if along > w.way + s.half then return nil end -- the walker stops before it
	if along > s.half + w.speed * LOOK_SECONDS + 5 then return nil end -- still far away
	if along > w.way and FIRE.stops_short(w, s) then return nil end -- ... or its order ends before its real front
	return along
end

-- the current leadership of a unit (the int in its morale block), or nil when the block is not known / the number odd
function LEAD.of(entry)
	if not state.lead_at then return nil end
	local ok, n = pcall(function()
		local pointer = unit_pointer(entry.unit)
		return pointer and signed_word(mr.read(pointer, state.lead_at, 4))
	end)
	if ok and type(n) == 'number' and n >= -1000 and n <= 1000 then return n end -- (0 or less is the lowest steadiness; beyond 1000 either way is no leadership)
	return nil
end

-- how steady a unit is: its leadership / LEAD.norm, between x0.5 and x1.5 (x1 when the leadership is not known); a
-- wavering unit is x0.5 at most. A steadier unit reorganises faster (shorter waits, less lag per step) and, because the
-- split takes less time, later. Read at most once per look (cached by tick).
local function steadiness(entry)
	if entry.lead_tick ~= state.ticks then
		entry.lead_tick, entry.lead_now = state.ticks, LEAD.of(entry)
	end
	local f = entry.lead_now and math.max(0.5, math.min(1.5, entry.lead_now / LEAD.norm)) or 1.0
	if yes(entry.unit, 'is_wavering') then f = math.min(f, 0.5) end
	return f
end

-- (log) the leadership of a unit and what it counts as
function LEAD.note(entry)
	local f = steadiness(entry)
	return ('leadership %s (x%.2f)'):format(entry.lead_now and tostring(entry.lead_now) or 'unknown', f)
end

-- the random wait (battle ticks) before a model of this unit starts: shorter for a steadier unit
local function waits(entry, ticks)
	return math.max(0, math.floor(ticks / steadiness(entry) + 0.5))
end

-- [formation part: tested on its own by the mock test]
-- the walker's formation, from where its models stand: files across its way, ranks along it, spacing of the men.
-- With W, D the spread across / along and s the spacing, (W / s + 1) (D / s + 1) = men. Returns nil when unreadable.
local function formation(places, dx, dz, men)
	local alo, ahi, dlo, dhi, count = nil, nil, nil, nil, 0
	for _, p in pairs(places) do
		local a, d = p[1] * dz - p[3] * dx, p[1] * dx + p[3] * dz
		if not alo or a < alo then alo = a end
		if not ahi or a > ahi then ahi = a end
		if not dlo or d < dlo then dlo = d end
		if not dhi or d > dhi then dhi = d end
		count = count + 1
	end
	men = math.max(men or 0, count)
	if count < 4 then return nil end
	local W, D = ahi - alo, dhi - dlo
	if W < 0.5 then return nil end
	local b = W + D
	local spacing = (b + math.sqrt(b * b + 4 * (men - 1) * W * D)) / (2 * (men - 1))
	if not (spacing > 0.3 and spacing < 20) then return nil end
	local files = math.floor(W / spacing + 0.5) + 1
	local ranks = math.max(1, math.floor(men / files + 0.5))
	return files, ranks, spacing
end
-- [formation part: tested on its own by the mock test]

-- [merge part: tested on its own by the mock test]
-- a further walker while the lanes are open (several big units through one standing unit, also at an angle). Across =
-- along the lanes' axis, from the split point (where the two halves of the standing unit part). bodies: { { a, t0, t1 }
-- }: a body's across now and the stretch of its way (t0.. t1 m ahead) that lies in the standing unit; k: how far it
-- drifts across per metre of its way (0 when it walks along the lanes). Returns the span it sweeps, r (its radius)
-- added, or nil when there is no body.
local function sweep(bodies, k, r)
	local lo, hi = nil, nil
	for _, b in ipairs(bodies) do
		local a0, a1 = b[1] + b[2] * k, b[1] + b[3] * k
		if not lo or math.min(a0, a1) < lo then lo = math.min(a0, a1) end
		if not hi or math.max(a0, a1) > hi then hi = math.max(a0, a1) end
	end
	if not lo then return nil end
	return lo - r, hi + r
end

-- the gap -dl .. dr around the split point, grown to hold lo .. hi with margin to each side: nil and 'fits' when it
-- does already, nil and why when the lane would get wider than most; else the new dl, dr. The split point stays, so
-- no standing model changes its side while the first walker is still in the gap.
local function widen(dl, dr, lo, hi, margin, most)
	local nl, nr = math.max(dl, margin - lo), math.max(dr, hi + margin)
	if nl <= dl + 0.05 and nr <= dr + 0.05 then return nil, 'fits' end
	if nl + nr > most then return nil, ('it would need a lane of %.1f m (at most %.1f)'):format(nl + nr, most) end
	return nl, nr
end
-- [merge part: tested on its own by the mock test]

-- [room part: tested on its own by the mock test]
-- how much a standing unit has to close up so that it does not push into the units beside it.
-- own / others: lists of { t, a } (along the walker's way / across it, from the walker's line); need: how far its
-- outermost models would step out without closing up; reach: radius of its models + radius of the others.
-- Each unit may use half the gap to its neighbour, and on every side at most out_max (nil: no limit); guard (guard
-- mode): a narrow flank counts too. Returns G (0 .. -MAX_CLOSE_UP), the two gaps beside it (nil = none), the sides it was worked out for ({ free, edge }:
-- 085i, for the overshoot that is left after closing up) and the gaps in front of / behind it (nil = none, 085i: units that stand across its width).
local function room_of(own, others, need, reach, out_max, guard)
	local lo, hi, tlo, thi = nil, nil, nil, nil
	for _, p in ipairs(own) do
		if not lo or p[2] < lo then lo = p[2] end
		if not hi or p[2] > hi then hi = p[2] end
		if not tlo or p[1] < tlo then tlo = p[1] end
		if not thi or p[1] > thi then thi = p[1] end
	end
	if not lo then return 0, nil, nil, {} end
	local left, right = nil, nil
	for _, p in ipairs(others) do
		if p[1] >= tlo - 3 and p[1] <= thi + 3 then
			if p[2] > hi then
				local gap = p[2] - hi - reach
				if not right or gap < right then right = gap end
			elseif p[2] < lo then
				local gap = lo - p[2] - reach
				if not left or gap < left then left = gap end
			end
		end
	end
	local g, sides = 0, {}
	for _, side in ipairs({ { right, hi }, { left, -lo } }) do
		local gap, edge = side[1], side[2]
		if (gap or out_max) and edge and edge >= (guard and 0.05 or 1) then -- (guard mode: a narrow flank stays inside the outline too)
			local free = gap and math.max(0, gap / 2 - ROOM_MARGIN) or out_max
			if out_max then free = math.min(free, out_max) end
			if free < need then g = math.min(g, (free - need) / edge) end
			sides[#sides + 1] = { free, edge }
		end
	end
	-- 085i: the units in front of / behind it (across its width): the extra ranks of its blocks may use half that gap
	local front, back = nil, nil
	for _, p in ipairs(others) do
		if p[2] >= lo - reach and p[2] <= hi + reach then
			if p[1] > thi + 3 then
				local gap = p[1] - thi - reach
				if not back or gap < back then back = gap end
			elseif p[1] < tlo - 3 then
				local gap = tlo - p[1] - reach
				if not front or gap < front then front = gap end
			end
		end
	end
	return math.max(-MAX_CLOSE_UP, g), left, right, sides, front, back
end
-- [room part: tested on its own by the mock test]

-- 085i: the layout of the blocks of a standing unit that closes up (G < 0) to make room for the lanes. The formula of cave 1 / shifted() squeezes the
-- files of a block together (x (1 + G)); where that leaves two models closer than a model needs (2 r + 0.2) and closer than they stood, the block is laid
-- out anew inside its strip between the lanes: files of at least 2 r + 0.2 apart, filled left to right, front to back, so the block gets EXTRA RANKS
-- (deeper, not wider; nobody crosses a lane, the end blocks never grow beyond their outermost model). The ranks are packed down to 2 r + 0.2 inside the
-- outline first, then grow straight in front of / behind it (the same on both sides). Beyond the outline the unit goes by TUNE.OUT_SHARE of its depth at
-- most per side (less where a unit stands in front of / behind it: lanes.vfront / lanes.vback); a strong recommendation, not a hard limit - what does not fit is
-- logged. The ranks grow ALONG THE LANES (the walker's way); for a walker at an angle the outline is checked in the unit's own axes and logged.
-- lanes.layout[key] = { dx, dz }: the correction to the place cave 1's formula gives (full lanes); place_of adds it (times the step share). Sets lanes.lay_note,
-- lanes.lay_move (the farthest a model of the layout goes from where it stands), lanes.lay_beyond (m beyond the allowed depth) and lanes.lay_oblique (degrees,
-- when the walker's way is more than TUNE.LAY_OBLIQUE off the unit's depth axis). places: { key = { x, y, z } }.
function FIRE.layout(s, lanes, places)
	lanes.layout, lanes.lay_note, lanes.lay_move, lanes.lay_beyond, lanes.lay_oblique = nil, nil, nil, nil, nil
	if lanes.art or (lanes[9] or 0) > -0.005 or not places then return nil end
	local ox, oz, ax, az, P, L, N, bias, G, S0 = lanes[1], lanes[2], lanes[3], lanes[4], lanes[5], lanes[6], lanes[7], lanes[8], lanes[9], lanes[10] or 0
	local bx, bz = az, -ax -- along the lanes (the walker's way)
	local rs = model_radius(s)
	local pitch = 2 * rs + 0.2
	local blocks, total = {}, 0
	for key, p in pairs(places) do
		local dx, dz = p[1] - ox, p[3] - oz
		local a, v = dx * ax + dz * az, dx * bx + dz * bz
		local n = math.max(-N, math.min(N, math.floor(a / P + 0.5)))
		local blk = blocks[n]
		if not blk then blk = { n = n, list = {} } blocks[n] = blk end
		blk.list[#blk.list + 1] = { key = key, a = a, v = v, u = a + (n + bias) * L + G * (a - S0) }
		total = total + 1
	end
	-- the smallest distance between two models of a list (across ukey, along v) closer than `cell` (1e9: none)
	local function nearest(list, ukey, cell)
		local cells, best = {}, 1e9
		for _, m in ipairs(list) do
			local cu, cv = math.floor(m[ukey] / cell), math.floor(m.v / cell)
			for du = -1, 1 do
				local col = cells[cu + du]
				if col then
					for dv = -1, 1 do
						local o = col[cv + dv]
						if o then
							for _, q in ipairs(o) do
								local d = math.sqrt((m[ukey] - q[ukey]) ^ 2 + (m.v - q.v) ^ 2)
								if d < best then best = d end
							end
						end
					end
				end
			end
			cells[cu] = cells[cu] or {}
			cells[cu][cv] = cells[cu][cv] or {}
			cells[cu][cv][#cells[cu][cv] + 1] = m
		end
		return best
	end
	local layout, targets, listed, move, beyond, ranks_added, blocks_laid = {}, {}, 0, 0, 0, 0, 0
	local ordered = {}
	for n in pairs(blocks) do ordered[#ordered + 1] = n end
	table.sort(ordered)
	for _, n in ipairs(ordered) do
		local list = blocks[n].list
		table.sort(list, function(p, q) if p.v ~= q.v then return p.v < q.v end if p.u ~= q.u then return p.u < q.u end return p.key < q.key end)
		local m = #list
		local now, was = nearest(list, 'u', pitch), nearest(list, 'a', pitch)
		if m >= 2 and now < math.min(pitch, was) - 0.1 * pitch then
			local umin, umax, vmin, vmax = 1e9, -1e9, 1e9, -1e9
			for _, q in ipairs(list) do
				umin, umax = math.min(umin, q.u), math.max(umax, q.u)
				vmin, vmax = math.min(vmin, q.v), math.max(vmax, q.v)
			end
			-- the strip between the lanes (the images of the block's edges); the outer side of an end block is its outermost model
			local function image(e) return e + (n + bias) * L + G * (e - S0) end
			-- (an edge that is no lane edge near the block - the outer side of an end block, the far side of a one-lane half - is the outermost model; a real edge may be
			-- used up to half a pitch beyond the outermost model: the files stood half a spacing from it)
			local el, er = image((n - 0.5) * P) + rs + 0.05, image((n + 0.5) * P) - rs - 0.05
			local lo = (n > -N and umin - el <= 2 * pitch) and math.max(el, umin - pitch / 2) or umin
			local hi = (n < N and er - umax <= 2 * pitch) and math.min(er, umax + pitch / 2) or umax
			if hi < lo then lo = (lo + hi) / 2 hi = lo end
			local files = math.max(1, math.floor((hi - lo) / pitch + 1e-6) + 1)
			local rows = math.ceil(m / files)
			-- the ranks of the block now (distinct v) and their spacing
			local ranks0, last = 0, nil
			for _, q in ipairs(list) do
				if not last or q.v - last > 0.25 * pitch then ranks0 = ranks0 + 1 end
				last = q.v
			end
			local depth0, vc = vmax - vmin, (vmin + vmax) / 2
			local sp0 = ranks0 >= 2 and depth0 / (ranks0 - 1) or pitch
			local sp = rows >= 2 and math.min(sp0, math.max(pitch, depth0 / (rows - 1))) or 0
			local depth = (rows - 1) * sp
			local over = math.max(0, (depth - depth0) / 2) -- per side, in front of / behind the block
			local allowed = math.min(lanes.vfront or 1e9, lanes.vback or 1e9)
			if over > allowed + 0.05 then beyond = math.max(beyond, over - allowed) end
			ranks_added = ranks_added + math.max(0, rows - ranks0)
			blocks_laid = blocks_laid + 1
			local mid = (lo + hi) / 2
			local at = 1
			for r = 1, rows do
				local cnt = math.min(files, m - at + 1)
				local row = {}
				for i = 0, cnt - 1 do row[#row + 1] = list[at + i] end
				at = at + cnt
				table.sort(row, function(p, q) return p.u < q.u end)
				local v = rows >= 2 and (vc - depth / 2 + (r - 1) * sp) or vc
				for i, q in ipairs(row) do
					local u = mid - (cnt - 1) * pitch / 2 + (i - 1) * pitch
					local cdx, cdz = (u - q.u) * ax + (v - q.v) * bx, (u - q.u) * az + (v - q.v) * bz
					if cdx * cdx + cdz * cdz > 0.0025 then
						layout[q.key] = { cdx, cdz }
						listed = listed + 1
						-- where it ends up (the place cave 1's formula gives, and the correction), and how far that is from where it stands
						local p = places[q.key]
						local tx, tz = ox + q.u * ax + q.v * bx + cdx, oz + q.u * az + q.v * bz + cdz
						targets[q.key] = { tx, tz }
						move = math.max(move, math.sqrt((tx - p[1]) ^ 2 + (tz - p[3]) ^ 2))
					end
				end
			end
		end
	end
	if listed == 0 then return nil end
	lanes.layout, lanes.lay_move, lanes.lay_beyond = layout, move, beyond > 0.05 and beyond or nil
	local note = ('%d of %d models laid out in %d block(s): %d extra rank(s), the farthest goes %.1f m'):format(listed, total, blocks_laid, ranks_added, move)
	if s.gb then
		-- the outline in the unit's own axes (its bearing): where do the laid-out models end up?
		local fx, fz = math.sin(math.rad(s.gb)), math.cos(math.rad(s.gb))
		local ang = math.deg(math.acos(math.min(1, math.abs(bx * fx + bz * fz))))
		local umin, umax, dmin, dmax = 1e9, -1e9, 1e9, -1e9
		for _, p in pairs(places) do
			local u, d = p[1] * fz - p[3] * fx, p[1] * fx + p[3] * fz
			umin, umax, dmin, dmax = math.min(umin, u), math.max(umax, u), math.min(dmin, d), math.max(dmax, d)
		end
		local depth, width = dmax - dmin, math.max(umax - umin, 2 * (s.half or 0))
		local cu = (umin + umax) / 2
		local out_side, out_depth = 0, 0
		for _, tg in pairs(targets) do
			local u, d = tg[1] * fz - tg[2] * fx, tg[1] * fx + tg[2] * fz
			if math.abs(u - cu) > width / 2 + (lanes.share or TUNE.OUT_SHARE) * width + 0.3 then out_side = out_side + 1 end
			if d < dmin - (lanes.share or TUNE.OUT_SHARE) * depth - 0.3 or d > dmax + (lanes.share or TUNE.OUT_SHARE) * depth + 0.3 then out_depth = out_depth + 1 end
		end
		lanes.lay_oblique = ang > TUNE.LAY_OBLIQUE and ang or nil
		note = note .. ('; in the unit axes (depth %.1f m, width %.1f m, walker %.0f deg off its depth axis): %d beyond its sides, %d beyond its depth'):format(depth, width, ang, out_side, out_depth)
	end
	if lanes.lay_beyond then note = note .. ('; the extra ranks go %.1f m beyond the allowed share of its depth (nothing else fits)'):format(lanes.lay_beyond) end
	lanes.lay_note = note
	return note
end

-- room for the lanes of standing unit s: fills in lanes[9] (G) and lanes[10] (S0); returns a text for the log
local function make_room(s, w, lanes, own_places, no_layout)
	-- is standing unit o being crossed by walker w (not a neighbour of the lanes for it)?
	local function crossed_by(o, w)
		if (o.served and o.served[w]) or w.columns_for == o then return true end
		if o.lane > 0 then
			local ok, inside = pcall(still_inside, o, w)
			return ok and inside
		end
		return false
	end
	lanes.narrow_to = nil -- (a trial plan of the B loop must not leave its narrowing on a plan that is used)
	lanes.layout, lanes.lay_note, lanes.lay_move, lanes.lay_beyond, lanes.over = nil, nil, nil, nil, nil -- (085i: nor its layout of an earlier room)
	lanes[10] = lanes[8] ~= 0 and 50000 or 0
	lanes[9] = 0
	if lanes.art then return 'artillery: nobody steps out of its front' end
	local function project(places, list)
		for _, p in pairs(places) do
			local dx, dz = p[1] - lanes[1], p[3] - lanes[2]
			list[#list + 1] = { dx * w.dx + dz * w.dz, dx * lanes[3] + dz * lanes[4] - lanes[10] }
		end
	end
	local own, others, reach = {}, {}, model_radius(s)
	project(own_places or near_places(s), own)
	local wide = 0
	for _, o in ipairs(state.units) do
		-- (and not a standing unit the same walker passes through: it lies along its way, not beside - run 34: the 2nd unit
		-- of a row took the 1st one, already stepped aside, for a wall and closed up to 50 %)
		if o ~= s and o ~= w and not o.gone and not (o.walks and passing(o, s)) and not (o.stands and w.walks and w.dx and passing(w, o)) and not crossed_by(o, w)
			and o.x and s.x and math.sqrt((o.x - s.x) ^ 2 + (o.z - s.z) ^ 2) < s.half + (o.half or 0) + 25 then
			-- one unit that cannot be read must not spoil the whole check (run 18: "not readable" with neighbours)
			local ok, problem = pcall(function()
				project(near_places(o), others)
				wide = math.max(wide, (model_radius(o))) -- (only the first value: run 18, the second is nil for infantry)
			end)
			if not ok and not state.room_problem then
				state.room_problem = true
				log(('  (room check: %s not readable, left out: %s)'):format(o.label, tostring(problem)))
			end
		end
	end
	local need = lanes[8] ~= 0 and math.max(-lanes[8], 1 + lanes[8]) * lanes[6] or lanes[7] * lanes[6] -- (a grown lane: its wider side)
	-- 085i: every standing unit (a big walker's too) goes beyond its outline by TUNE.OUT_SHARE of its width per flank at most (guard mode: GUARD_OUT m if that is less),
	-- the rest comes from closing up (and the extra ranks of FIRE.layout)
	local out_max = TUNE.OUT_SHARE * 2 * (s.half or 0)
	if s.guard_now then out_max = math.min(out_max, TUNE.GUARD_OUT) end
	local g, left, right, sides, vfront, vback = room_of(own, others, need, reach + wide, out_max, s.guard_now)
	-- 085i review: TIERED share (not in guard mode): when nothing fits within the share, up to TUNE.OUT_SHARE_MAX per side (logged); beyond that it does not fit
	local share, beyond5 = TUNE.OUT_SHARE, nil
	if not s.guard_now and g <= -MAX_CLOSE_UP + 1e-6 then
		local stick5 = 0
		for _, sd in ipairs(sides) do stick5 = math.max(stick5, need + g * sd[2] - sd[1]) end
		if stick5 > 0.05 then
			local out2 = TUNE.OUT_SHARE_MAX * 2 * (s.half or 0)
			local g2, l2, r2, sides2, vf2, vb2 = room_of(own, others, need, reach + wide, out2, false)
			share, beyond5, out_max = TUNE.OUT_SHARE_MAX, stick5, out2
			g, left, right, sides, vfront, vback = g2, l2, r2, sides2, vf2, vb2
		end
	end
	local lo, hi, tlo, thi = nil, nil, nil, nil
	for _, p in ipairs(own) do
		if not lo or p[2] < lo then lo = p[2] end
		if not hi or p[2] > hi then hi = p[2] end
		if not tlo or p[1] < tlo then tlo = p[1] end
		if not thi or p[1] > thi then thi = p[1] end
	end
	local floor_text = ''
	if s.guard_now and lo then
		-- guard mode: it cannot close up further than its models allow (a rank is spacing apart, a model is 2 r wide); what is
		-- still too much is taken out of the lanes themselves (FIRE.begin_lanes narrows them, or makes ONE lane of them)
		local fok, files, ranks, spacing = pcall(function() return formation(own_places or near_places(s), w.dx, w.dz, s.men) end)
		if fok and files then
			local floor = math.min(0, -(1 - (2 * model_radius(s) + 0.1) / spacing))
			if g < floor then g = floor end
			floor_text = (', its models close up to %.0f %% at most'):format((1 + floor) * 100)
		end
		local narrow, per = nil, need / lanes[6]
		for _, edge in ipairs({ hi, -lo }) do
			if edge >= 1 and need + g * edge > out_max + 0.05 then
				local to = (out_max - g * edge) / per
				narrow = narrow and math.min(narrow, to) or to
			end
		end
		lanes.narrow_to = narrow
	end
	lanes[9] = g
	-- 085i: what still sticks out of the allowed share after closing up (the plans that must not stick out test this: lanes.over); the room for extra ranks in front of / behind
	local over = 0
	for _, sd in ipairs(sides) do over = math.max(over, need + g * sd[2] - sd[1]) end
	lanes.share, lanes.beyond5 = share, beyond5
	lanes.over = g <= -MAX_CLOSE_UP + 1e-6 and over or 0 -- (only a plan that closes up as far as it may and still sticks out does not fit; guard mode narrows its lanes instead)
	local depth = (thi and tlo) and (thi - tlo) or 0
	local function vroom(gap)
		local share = share * depth
		if s.guard_now then share = math.min(share, TUNE.GUARD_OUT) end
		return math.max(0, math.min(share, gap and (gap / 2 - ROOM_MARGIN) or share))
	end
	lanes.vfront, lanes.vback = vroom(vfront), vroom(vback)
	local lok, lerr = true, nil
	if not no_layout then lok, lerr = pcall(FIRE.layout, s, lanes, own_places or near_places(s)) end -- (85i review: a trial plan only needs `over`)
	if not lok then
		lanes.layout, lanes.lay_note, lanes.lay_move = nil, nil, nil
		if not state.layout_problem then state.layout_problem = true log(('  (layout of %s: %s)'):format(s.label, tostring(lerr))) end
	end
	local function gap(x) return x and ('%.1f m'):format(x) or 'free' end
	return ('beside it: %s / %s, %s; %s%s'):format(gap(left), gap(right), g < 0 and ('closes up to %.0f %% of its width'):format((1 + g) * 100) or 'room enough',
		s.guard_now and ('GUARD MODE: the lanes open inside its outline (out of it at most %.1f m per side)'):format(out_max) or ('out of its outline at most %.1f m per side'):format(out_max), floor_text .. (beyond5 and ('; beyond the 5 %% share by %.1f m (nothing else fits; up to %.0f %% per side)'):format(beyond5, TUNE.OUT_SHARE_MAX * 100) or '') .. (lanes.over > 0.05 and ('; still %.1f m too wide'):format(lanes.over) or '') .. (lanes.lay_note and ('; LAYOUT: ' .. lanes.lay_note) or ''))
end

-- the lanes of standing artillery: the gaps between its machines (gun_gaps); nil and why when there are none
local function artillery_lanes(s, cx, cz, ax, az, lane, min_lane, bare)
	local guns, seen, gun_r, crew_r = {}, {}, 0, 0.5
	local gun_of, crew = {}, {} -- crewman -> where its machine stands
	local n, models = models_of(s)
	for index = 0, (n or 0) - 1 do
		local model = mr.read_pointer(models, index * 8)
		if not mr.is_null(model) then
			crew_r = math.max(crew_r, math.min(1.0, plausible_radius(get_float(model, 0xa0)) or 0))
			local gun = mr.read_pointer(model, 0x2e0) -- a crewman's machine (run 11)
			local key = hex(gun)
			if not mr.is_null(gun) and hex(mr.read_pointer(gun, 0x2e8)) == s.key then
				local x, _, z = get_xyz(gun)
				if x and z and near_unit(s, x, z) then
					if not seen[key] then
						seen[key] = true
						guns[#guns + 1] = (x - cx) * ax + (z - cz) * az
						gun_r = math.max(gun_r, plausible_radius(get_float(gun, 0xa0)) or 1.5)
					end
					local mx, _, mz = get_xyz(model)
					gun_of[hex(model)] = { x, z }
					if mx and mz then crew[#crew + 1] = { mx, mz, x, z } end
				end
			end
		end
	end
	local origin, q, kl, kh, fit = gun_gaps(guns, lane, gun_r, min_lane, bare)
	if not origin then return nil, q end
	local lanes = { cx + ax * origin, cz + az * origin, ax, az, q - fit, fit, 0, 0, 0, 0 }
	lanes.art, lanes.q, lanes.kl, lanes.kh, lanes.crew_r, lanes.gun_r, lanes.gun_of = true, q, kl, kh, crew_r, gun_r, gun_of
	lanes.machines, lanes.wanted = #guns, lane
	-- the farthest a crewman goes (for the timing of the split)
	local far = 0.5
	for _, c in ipairs(crew) do
		local a, t = (c[1] - c[3]) * ax + (c[2] - c[4]) * az, (c[1] - c[3]) * az - (c[2] - c[4]) * ax
		local na, nt = into_strip(a, t, gun_r, crew_r)
		far = math.max(far, math.sqrt((na - a) ^ 2 + (nt - t) ^ 2))
	end
	lanes.shift = far
	return lanes
end

-- lanes per file for a walker that keeps its formation (085d): across = the sorted across-coordinates of its bodies (from cx, cz along
-- ax, az), files = the file count (an estimate), lane = the lane width. The file spacing comes from the bodies themselves ((amax -
-- amin) / (files - 1): the estimate of formation() drifted 0.3 m per file, 1.5 m over 5 files); every file's bodies must lie within
-- TUNE.VECTOR_CLUSTER of its place, and a standing file must fit between two lanes. Only the lanes that cross the standing unit are
-- planned (files beyond its front pass beside it): N lanes per side. Returns the lanes and a text, or nil and why.
function FIRE.vector_lanes(s, across, cx, cz, ax, az, files, lane, radius, keeps)
	local count = #across
	if files < 2 or count < 2 then return nil, 'fewer than 2 files' end
	local amin, amax = across[1], across[count]
	local spacing = (amax - amin) / (files - 1)
	local rs = model_radius(s)
	-- big walkers (radius >= BIG_RADIUS): the wider lane LANE_EXTRA_BIG, as far as a standing model still fits between two lanes (085d review 1)
	if radius and radius >= TUNE.BIG_RADIUS then lane = math.max(lane, math.min(2 * radius + TUNE.LANE_EXTRA_BIG, spacing - 2 * rs - 0.2)) end
	local block = spacing - lane
	if block < 2 * rs + 0.2 - 0.01 then
		return nil, ('block test failed: files %.2f m apart (from its bodies) - lane %.2f m = %.2f m of the standing unit between lanes < %.2f m (a model of it needs)'):format(spacing, lane, block, 2 * rs + 0.2)
	end
	local sum, num = {}, {}
	for _, a in ipairs(across) do
		local i = math.floor((a - amin) / spacing + 0.5)
		sum[i], num[i] = (sum[i] or 0) + a, (num[i] or 0) + 1
	end
	for i = 0, files - 1 do
		if not num[i] then return nil, ('files do not line up: file %d of %d has no body'):format(i + 1, files) end
		local off = sum[i] / num[i] - (amin + i * spacing)
		if not keeps and math.abs(off) > TUNE.VECTOR_CLUSTER then -- (chariots / artillery: uneven machines are normal, no cluster test - 085d review 3)
			return nil, ('files do not line up: file %d of %d lies %.2f m from its place (at most %.2f)'):format(i + 1, files, math.abs(off), TUNE.VECTOR_CLUSTER)
		end
	end
	-- the lanes at (k + 0.5) spacing from the origin, k = -N .. N-1: an even number of files is centred, an odd one moves the origin half a file
	local mid = (amin + amax) / 2
	local oa = files % 2 == 1 and mid - spacing / 2 or mid
	local lo, hi = nil, nil
	for _, p in pairs(near_places(s)) do
		local a = (p[1] - cx) * ax + (p[3] - cz) * az
		if not lo or a < lo then lo = a end
		if not hi or a > hi then hi = a end
	end
	local full = math.ceil(files / 2)
	local n, crossing = full, files
	if lo then
		n, crossing = 0, 0
		for i = 0, files - 1 do
			local c = amin + i * spacing
			if c + lane / 2 > lo - rs and c - lane / 2 < hi + rs then
				crossing = crossing + 1
				local k = math.floor((c - oa) / spacing + 1e-6)
				n = math.max(n, k >= 0 and k + 1 or -k)
			end
		end
		n = math.max(1, math.min(n, full))
	end
	local lanes = { cx + ax * oa, cz + az * oa, ax, az, block, lane, n, 0 }
	lanes.vector = true
	return lanes, ('spacing %.2f m from its bodies; %d of %d files cross the standing unit -> %d lanes per side (cap; all files would be %d)'):format(spacing, crossing, files, n, full)
end

-- one wide lane for the whole front of a walker that keeps its formation, centred on its bodies (across: their positions across its way, sorted, from the
-- line through cx, cz; lane: the width of one lane of it). 085h: also for the monstrous infantry / cavalry that is not narrowed (0.8 did this for all of them)
function FIRE.whole_front(s, across, lane, cx, cz, ax, az)
	local amin, amax = across[1], across[#across]
	local mid = (amin + amax) / 2
	local wide = math.min(LANE_WIDE_MAX, amax - amin + lane)
	local mx, mz = cx + ax * mid, cz + az * mid
	local lanes = { mx - ax * 50000, mz - az * 50000, ax, az, 100000, wide, 1, -0.5 }
	lanes.vector = true
	lanes.whole_front = s.half and wide >= 2 * s.half -- (the whole standing unit has to step aside)
	return lanes, wide
end

-- the lanes standing unit s would open for walker w (nil and why: none)
local function plan_lanes(s, w)
	-- the lanes run along the walker's way: the places move across it
	local ax, az = -w.dz, w.dx
	w.formation = nil -- (the text of an earlier plan must not show up in this one's log)
	-- where the walker's line crosses the standing unit. The line goes through the MIDDLE OF THE WALKER'S MODELS
	-- (measure_walker, this look): run 19 showed unit:position() 2.8-5.8 m to the side of it while marching, so the
	-- walker met one lane with nearly all its models (per lane 0 / 22, 0 / 28, 0 / 29: "always the left lane")
	local bx, bz = w.x, w.z
	if w.cm_tick == state.ticks and w.cx then bx, bz = w.cx, w.cz end
	local al = (s.x - bx) * w.dx + (s.z - bz) * w.dz
	local cx, cz = bx + w.dx * al, bz + w.dz * al
	local radius, carried = model_radius(w)
	-- 085e review 1: a walker that is ordered narrow keeps its ONE lane for the whole pass (its formation is read as the game re-forms it: columns or lanes per file
	-- would come out of that)
	if w.narrow and not w.narrow.restoring and s.kind ~= 'art' then
		local nl = NARROW.plan(s, w, radius, nil, nil, cx, cz, ax, az)
		if nl then
			w.formation = ('walker ordered narrow (%.1f m): ONE lane of %.1f m, no columns'):format(nl.narrow.order_w, nl.narrow.lane)
			return nl, radius, carried
		end
	end
	-- chariots, war machines, monstrous infantry / cavalry and artillery (KIND, from the game's tables) keep their
	-- formation: one lane per file of their walking bodies (the machines, not the crews), or, when the files are too close
	-- for a standing file between them, one wide lane for the whole walker
	local kind = w.kind
	local keeps = kind == 'veh' or kind == 'art'
	local artillery = s.kind == 'art'
	-- ONE walking body however many models ride it (run 28: Ikit Claw and the Doomwheel, 25 models in one wheel, got
	-- two lanes and columns): it passes like a single model
	local single = w.men <= SINGLE_MAX_MEN
	local is_one_body = false
	-- only models that really ride their carrier count as one body (an unreadable model list is not one body); one_body
	-- does not read the bodies of foot units or of riders on their own mounts
	if not single and one_body(w) then single, is_one_body = true, true end
	if artillery and (keeps or kind == 'one' or single) then
		return nil, 'artillery cannot move its machines aside for a walker that keeps its formation (it walks round as in the game)'
	end
	if keeps and not single then
		local ok, bodies = pcall(function()
			local list, seen = {}, {}
			local n, models = models_of(w)
			for index = 0, (n or 0) - 1 do
				local model = mr.read_pointer(models, index * 8)
				if not mr.is_null(model) then
					local carrier = mr.read_pointer(model, 0x2e0) -- riders / crews: their mount or machine walks
					local carried_by_it = not mr.is_null(carrier) and hex(mr.read_pointer(carrier, 0x2e8)) == w.key
					if carried_by_it then model = carrier end
					local key = hex(model)
					if (carried_by_it or not w.crewed) and not seen[key] then -- artillery: the machines only, not the crew
						local x, _, z = get_xyz(model)
						if x and z and near_unit(w, x, z) then
							seen[key] = true
							list[#list + 1] = { x - cx, 0, z - cz }
						end
					end
				end
			end
			return list
		end)
		if ok and #bodies >= 2 and #bodies <= 60 then
			local lane = math.max(LANE_MIN, math.min(LANE_MAX, 2 * radius + LANE_EXTRA))
			if w.crewed or w.machine then lane = math.max(lane, 2 * (carried or 1.5) + 2 * CREW_MARGIN) end
			local across = {}
			for _, b in ipairs(bodies) do across[#across + 1] = b[1] * ax + b[3] * az end
			table.sort(across)
			local amin, amax = across[1], across[#across]
			local files, ranks, spacing = nil, nil, nil
			if #bodies >= 4 then
				local fok, f, r, sp = pcall(formation, bodies, w.dx, w.dz, #bodies)
				if fok then files, ranks, spacing = f, r, sp end
			end
			if not files then -- 2-3 bodies (or not readable): files from the gaps across the way
				files = 1
				for k = 2, #across do
					if across[k] - across[k - 1] > math.max(1.0, radius) then files = files + 1 end
				end
				spacing = files > 1 and (amax - amin) / (files - 1) or 0
				ranks = math.ceil(#bodies / files)
			end
			-- lanes per file (085d: the spacing from the bodies, only the files that cross the standing unit, any number of files)
			local vlanes, vtext = FIRE.vector_lanes(s, across, cx, cz, ax, az, files, lane, radius, true)
			if vlanes then
				w.formation = ('%s walker of %d bodies: %d files x %d ranks, %.1f m apart (estimate) -> it keeps its formation, one lane per file; %s; no quick / half close for these lanes'):format(kind, #bodies, files, ranks, spacing, vtext)
				return vlanes, radius, carried
			end
			-- one wide lane for its whole front, centred on its bodies
			local lanes, wide = FIRE.whole_front(s, across, lane, cx, cz, ax, az)
			w.formation = ('%s walker of %d bodies: %d files %.1f m apart, too close for a standing file between them -> it keeps its formation, one lane of %.1f m for its whole front (%s)'):format(kind, #bodies, files, spacing or 0, wide, tostring(vtext))
			return lanes, radius, carried
		end
	end
	if kind == 'one' and not single then
		-- a unit of another mod without a vanilla caste: like a monster, ONE lane centred on its way, and the walker
		-- squeezes into one column (as many files as COLUMN_DEPTH times its ranks need)
		local ok, files, ranks, spacing = pcall(function() return formation(near_places(w), w.dx, w.dz, w.men) end)
		local lane
		if ok and files then
			local per = math.max(LANE_FILES, math.ceil(files / COLUMN_DEPTH))
			lane = (per - 1) * spacing + 2 * radius + LANE_EXTRA
			w.formation = ('walker of another mod, %d files x %d ranks, %.1f m apart -> one column %d files wide'):format(files, ranks, spacing, per)
		else
			lane = COLUMN_SHARE * 2 * w.half
			w.formation = 'walker of another mod, formation not readable -> one column 0.4 of its front'
		end
		lane = math.max(LANE_MIN, math.min(LANE_MAX, lane))
		local q = math.max(lane, 2 * w.half)
		local lanes = { cx - ax * 50000, cz - az * 50000, ax, az, 100000, lane, 1, -0.5 }
		lanes.one_column, lanes.q, lanes.cox, lanes.coz = true, q, cx - ax * q / 2, cz - az * q / 2
		if ok and files then lanes.wf, lanes.wsp = files, spacing end -- (the walker's files and spacing: the squeeze log, guard mode)
		return lanes, radius, carried
	end
	if single then
		-- one lane, centred on the way: everything left of it steps half a lane to the left, the rest to the right
		local lane = math.max(LANE_MIN, math.min(LANE_MAX, 2 * radius + LANE_EXTRA))
		local lanes = { cx - ax * 50000, cz - az * 50000, ax, az, 100000, lane, 1, -0.5 }
		if is_one_body then
			w.formation = ('one walking body carries its %d models -> one lane, no columns'):format(w.men)
			lanes.one_body = true
		end
		return lanes, radius, carried
	end
	-- the standing unit splits into blocks across the walker's way (3, or 5 / 7 for a very wide front); the middle
	-- block stays, the others step outwards: a lane opens between every two blocks, and the walker forms one column
	-- per lane (2 columns through 3 blocks). The middle of the walker always meets a block, never a lane.
	local front = 2 * s.half
	local n_front = front > WIDER_FRONT and 3 or (front > WIDE_FRONT and 2 or 1)
	local n, forced = n_front, true
	if state.cfg.lanes == '2' then n = 1 elseif state.cfg.lanes == '4' then n = 2 elseif state.cfg.lanes == '6' then n = 3 else forced = false end -- MCT
	-- the lane is as wide as the files a column needs: its unit's files shared among the lanes, so that no column
	-- gets deeper than COLUMN_DEPTH times the unit's ranks
	local ok, files, ranks, spacing = pcall(function()
		local places, bodies = FIRE.places(w)
		return formation(places, w.dx, w.dz, bodies and #places or w.men) -- (bodies: their count, not the unit's men)
	end)
	local big = radius >= TUNE.BIG_RADIUS -- (big bodies: wider lanes, LANE_EXTRA_BIG; run 52: -0.5 .. -1.4 m clearance for demigryphs)
	local extra = big and TUNE.LANE_EXTRA_BIG or LANE_EXTRA
	local function lane_for(nn)
		local ln, per = nil, nil
		if ok and files then
			per = math.max(LANE_FILES, math.ceil(files / (2 * nn * COLUMN_DEPTH)))
			ln = (per - 1) * spacing + 2 * radius + extra
		else
			ln = math.max(2 * radius * LANE_FILES + extra, COLUMN_SHARE * 2 * w.half / (2 * nn))
		end
		if w.crewed or w.machine then ln = math.max(ln, 2 * (carried or 1.5) + 2 * CREW_MARGIN) end
		return math.max(LANE_MIN, math.min(LANE_MAX, ln)), per
	end
	local lane, per = lane_for(n)
	if ok and files then
		w.formation = ('walker %d files x %d ranks, %.1f m apart -> columns %d files wide'):format(files, ranks, spacing, per)
	else
		w.formation = 'walker formation not readable -> lanes 0.4 of its front'
	end
	if artillery then
		local lanes, why = artillery_lanes(s, cx, cz, ax, az, lane, math.max(LANE_MIN, 2 * radius + LANE_EXTRA), 2 * radius + 0.2)
		if not lanes then return nil, why end
		if ok and files then lanes.wf, lanes.wsp = files, spacing end
		return lanes, radius, carried
	end
	-- equal blocks; but the outermost lanes must still lie inside the walker's front
	local function plan_for(nn, ln)
		local blk = math.max(BLOCK_MIN, math.min(front / (2 * nn + 1), w.half / (nn - 0.5) - ln))
		local pl = { cx, cz, ax, az, blk, ln, nn, 0 }
		if ok and files then pl.wf, pl.wsp = files, spacing end -- (kept on the lanes table: start_columns and guard mode read them)
		return pl
	end
	local plan = plan_for(n, lane)
	if ok and files and files >= 2 then
		-- 085d: can the files of this walker be squeezed into the lanes at all? The squeezed spacing against what a model needs (2 r + 0.2).
		-- Dense walkers (the standing file between two of their lanes would not fit: a cheap test) stay with the columns; the others are
		-- measured (run 52: demigryph files 4.6 m apart squeezed to 0.9 m, a model needs 2.4 m: they piled into the blocks)
		local real = 2 * radius + 0.2
		local pre = spacing - (2 * radius + LANE_EXTRA) >= 2 * model_radius(s) + 0.2 - 0.01
		local c_sp, why_not = nil, nil
		if big or pre then
			local gok, _, _, _, squeeze = pcall(FIRE.col_geometry, s, w, plan, radius, near_places(s))
			c_sp = gok and squeeze and squeeze * spacing or nil
		end
		if c_sp and c_sp < real - 1e-6 then
			if not pre then
				why_not = ('block test failed: files %.2f m apart (estimate) - lane %.2f m = %.2f m of the standing unit between lanes < %.2f m (a model of it needs)'):format(spacing, 2 * radius + LANE_EXTRA, spacing - (2 * radius + LANE_EXTRA), 2 * model_radius(s) + 0.2)
			elseif FIRE.guard_of(s) then
				why_not = 'guard mode: the lanes open inside the outline (no lanes per file)'
			else
				local across = {}
				for _, p in pairs(FIRE.places(w)) do across[#across + 1] = (p[1] - cx) * ax + (p[3] - cz) * az end
				table.sort(across)
				local vl, vtext = FIRE.vector_lanes(s, across, cx, cz, ax, az, files, math.max(LANE_MIN, math.min(LANE_MAX, 2 * radius + LANE_EXTRA)), radius)
				if vl then
					w.formation = ('%s walker of %d bodies: %d files x %d ranks, %.1f m apart (estimate) -> it keeps its formation, one lane per file; vector: squeezed %.2f m < needs %.2f m, block %.2f m >= %.2f m; %s; margin %.2f s; no quick / half close for these lanes'):format(
						kind, #across, files, ranks, spacing, c_sp, real, vl[5], 2 * model_radius(s) + 0.2, vtext, FIRE.open_margin(w))
					return vl, radius, carried
				end
				why_not = vtext
			end
			-- 085e A2: the player's walker is ordered NARROW instead (the game forms the deep column, ONE lane for it); the AI's keeps the columns
			if not forced then
				plan.narrow_tried = true
				local nl, nwhy = NARROW.plan(s, w, radius, files, spacing, cx, cz, ax, az)
				if nl then
					w.formation = ('%s walker %d files x %d ranks, %.1f m apart (estimate), squeezed %.2f m < needs %.2f m (%s) -> NARROWED: ordered %d files wide (%.1f m), the game forms the column; ONE lane of %.1f m, no columns'):format(
						kind, files, ranks, spacing, c_sp, real, why_not, nl.narrow.files, nl.narrow.order_w, nl.narrow.lane)
					return nl, radius, carried
				end
				if nwhy then why_not = why_not .. ' | narrowing: ' .. nwhy end
			end
			-- 085i: no whole-front lane for monstrous infantry / cavalry that is not narrowed (085h item 10): the columns squeeze instead (the standing unit stays in its outline)
			w.formation = w.formation .. ('; columns: %s (squeezed to %.2f m, a model needs %.2f m)'):format(why_not, c_sp, real)
			-- B (085d): more lanes per side when the squeezed files do not fit (only big bodies; infantry tolerates the squeeze; MCT forces its own count)
			if big and not forced and n < 3 then
				local best, clamp_seen = { n = n, c = c_sp, plan = plan, lane = lane, per = per, room = 'untested' }, false
				local cached = s.b_cache
				if cached and cached.w == w and cached.order_at == w.order_at and state.ticks - cached.tick < TUNE.B_CACHE_LOOKS and cached.base == n then
					-- 085d review 7: the n chosen in an earlier look of this order (only the plan itself is made again, no make_room per candidate)
					local ln, pr = lane_for(cached.n)
					best = { n = cached.n, c = cached.c, plan = cached.n == n and plan or plan_for(cached.n, ln), lane = cached.n == n and lane or ln, per = cached.n == n and per or pr, room = cached.room }
					clamp_seen = cached.clamp_seen
				else
					s.guard_now = FIRE.guard_of(s) -- (make_room reads it: the trial plans are made with the guard mode of this look)
					for nn = n + 1, 3 do
						local ln, pr = lane_for(nn)
						local pl = plan_for(nn, ln)
						local rok = pcall(make_room, s, w, pl, nil, true)
						local clamped = not rok or (pl.over or 0) > 0.05 -- (085i: it sticks out of the allowed share even closed up)
						local gok2, _, _, _, squeeze2 = pcall(FIRE.col_geometry, s, w, pl, radius, near_places(s))
						local c2 = gok2 and squeeze2 and squeeze2 * spacing or 0
						if clamped then clamp_seen = true end
						if not clamped and c2 >= TUNE.SQUEEZE_GAIN * best.c and c2 > best.c then best = { n = nn, c = c2, plan = pl, lane = ln, per = pr, room = 'ok' } end
						if c2 >= real then break end
					end
					s.b_cache = { w = w, order_at = w.order_at, tick = state.ticks, base = n, n = best.n, c = best.c, room = best.room, clamp_seen = clamp_seen }
				end
				if best.n ~= n then
					plan, lane, per = best.plan, best.lane, best.per
					if plan ~= nil and best.n ~= n then pcall(FIRE.layout, s, plan, near_places(s)) end -- (085i: the trial plans had no layout)
					w.formation = ('walker %d files x %d ranks, %.1f m apart -> columns %d files wide; columns: %s'):format(files, ranks, spacing, per, why_not)
					n = best.n
				end
				plan.n_note = ('n lanes per side: front %d, needed %d, room %s (squeezed spacing %.2f m of the %.2f m a model needs)'):format(n_front, n, best.n ~= n_front and best.room or (clamp_seen and 'clamped (no more lanes fit)' or 'ok (more lanes would not help)'), best.c, real)
			end
		end
	end
	if not plan.n_note then
		plan.n_note = ('n lanes per side: front %d, needed %d, room %s'):format(n_front, n, forced and 'forced by MCT' or 'ok')
	end
	-- 085e A2: a stander in guard mode (its lanes open inside its outline, room for one column at most): the player's walker is ordered narrow as well
	if ok and files and files > TUNE.NARROW_FILES and not forced and not plan.narrow_tried and FIRE.guard_of(s) then
		local nl = NARROW.plan(s, w, radius, files, spacing, cx, cz, ax, az)
		if nl then
			w.formation = ('walker %d files x %d ranks, %.1f m apart (estimate), %s is in guard mode -> NARROWED: ordered %d files wide (%.1f m), the game forms the column; ONE lane of %.1f m, no columns'):format(
			files, ranks, spacing, s.label, nl.narrow.files, nl.narrow.order_w, nl.narrow.lane)
			return nl, radius, carried
		end
	end
	return plan, radius, carried
end

-- when to split: seconds until the walker's front reaches the standing unit, and how long the split takes
function FIRE.timing_calc(s, w, lanes)
	local near = extent_near(s, w.x, w.z, w.dx, w.dz) -- nearest model of s along the walker's way
	local _, front = extent_near(w, w.x, w.z, w.dx, w.dz) -- the walker's front
	if not near or not front then return nil end
	local rs, rw = model_radius(s), model_radius(w)
	local gap = (near - rs) - (front + rw)
	-- the walker's speed from its own models (run 14: unit:position() moved 5.4 m/s for walking Ironsworn)
	local speed = ((w.csamples or 0) >= SPEED_SAMPLES and w.cmeasured and w.cmeasured > 0.3) and w.cmeasured
		or (((w.samples or 0) >= SPEED_SAMPLES and w.measured and w.measured > 0.3) and math.min(w.measured, w.speed) or w.speed)
	if state.ticks - (w.order_at or -1000) < TUNE.START_TICKS then speed = math.max(speed, w.speed) end
	local shift = farthest_shift(lanes)
	local wait, look = waits(s, WAIT_TICKS) * 0.1, math.max(state.dt, TICK_MS / 1000)
	local lag = look / steadiness(s) -- the delay of every step (the models cannot walk faster; a steady unit loses less time)
	-- the pace follows the walker: a running walker -> the models of s run to their lane places in one step; a walking one
	-- -> they walk, in steps under RUN_BEYOND, at the pace this unit showed at its last split (check_pace) times the
	-- game's walking speed. One look per step at least.
	local slow = s.unit:slow_speed()
	if type(slow) ~= 'number' or slow < 0.3 or slow > 40 then slow = 1.5 end
	local w_slow = w.unit:slow_speed()
	local running = yes(w.unit, 'is_moving_fast') or (type(w_slow) == 'number' and w_slow > 0.3 and speed > 1.3 * w_slow)
	local s_speed = slow * (s.pace or 1)
	local steps = math.max(1, math.ceil(shift / STEP_MAX - 1e-9))
	local step_time = (shift / steps) / s_speed + lag
	local t_split = wait + steps * step_time
	-- a running walker, or one that is already close (the standing unit had only just stopped; run 23: lanes opened
	-- 1-17 s after the walker was inside): everything in one step, at a run when it is further than RUN_BEYOND
	if (running or gap / speed < t_split + SPLIT_MARGIN) and steps > 1 then
		local fast = s.unit:fast_speed()
		if shift > RUN_BEYOND and type(fast) == 'number' and fast > 0.3 and fast < 40 then s_speed = fast end
		steps = 1
		step_time = shift / s_speed + lag
		t_split = wait + step_time
	end
	return gap / speed, t_split, gap, speed, s_speed, steps, step_time
end

-- (the same answer within one look for the same lanes / walker measurements: FIRE.quick.soon asks up to 3 times per candidate)
local function timing(s, w, lanes)
	local shift = farthest_shift(lanes)
	s.timing_memo = s.timing_memo or {}
	local m = s.timing_memo[w]
	if m and m.tick == state.ticks and m.lanes == lanes and m.shift == shift and m.pace == s.pace and m.cm == w.cmeasured and m.cs == w.csamples then
		local r = m.r
		return r[1], r[2], r[3], r[4], r[5], r[6], r[7]
	end
	local r = { FIRE.timing_calc(s, w, lanes) }
	s.timing_memo[w] = { tick = state.ticks, lanes = lanes, shift = shift, pace = s.pace, cm = w.cmeasured, cs = w.csamples, r = r }
	return r[1], r[2], r[3], r[4], r[5], r[6], r[7]
end

-- the MCT settings (when MCT is installed); in battle they are there when the fighting starts
-- 085e review 8: every COLLECT_EVERY looks the MCT option 'enabled' is read again; switched off in the battle -> the narrowed walkers get their orders back
function NARROW.mct_check()
	local ok, value = pcall(function()
		return get_mct():get_mod_by_key('military_drill'):get_option_by_key('military_drill_enabled'):get_finalized_setting()
	end)
	if ok and value == false and state.cfg.enabled then
		state.cfg.enabled = false
		log('settings: the mod was switched off in the Mod Configuration Tool during the battle: narrowed walkers get their orders back')
		pcall(NARROW.restore_all, 'the mod was switched off (MCT)')
		pcall(GHOST.refresh)
	end
end

local function read_settings()
	local cfg = state.cfg
	local ok, problem = pcall(function()
		local mct = get_mct and get_mct()
		local m = mct and mct:get_mod_by_key('military_drill')
		if not m then error('MCT not installed or the mod not registered', 0) end
		local function get(key)
			local o = m:get_option_by_key('military_drill_' .. key)
			if o then return o:get_finalized_setting() end
		end
		local v = get('enabled') if type(v) == 'boolean' then cfg.enabled = v end
		v = get('lanes') if v == 'auto' or v == '2' or v == '4' or v == '6' then cfg.lanes = v end
		v = get('expendable') if type(v) == 'boolean' then cfg.expendable = v end
		v = get('wavering') if type(v) == 'boolean' then cfg.wavering = v end
		v = get('fire_lanes') if type(v) == 'boolean' then cfg.fire = v end
		v = get('logging') if RELEASE.LEVELS[v] then cfg.logging = v end
	end)
	-- (release) from now on the MCT option decides; the lines that waited are written or dropped
	RELEASE.decide(RELEASE.LEVELS[cfg.logging] or 0)
	log(('settings%s: enabled %s (for every army), lanes %s, expendable units make way %s, wavering units make way %s, logging %s, clear the line of fire %s'):format(
		ok and ' (MCT)' or (' (defaults: ' .. tostring(problem) .. ')'), tostring(cfg.enabled),
		cfg.lanes, cfg.expendable and 'NO' or 'yes', cfg.wavering and 'NO' or 'yes (at half speed)', tostring(cfg.logging), cfg.fire and 'yes' or 'NO'))
end

-- the walker's speed measured from the middle of its models (once per look, only for walkers near a standing unit)
local function measure_walker(w)
	if w.cm_tick == state.ticks then return end
	local places = near_places(w)
	local n, sx, sz = 0, 0, 0
	for _, p in pairs(places) do n, sx, sz = n + 1, sx + p[1], sz + p[3] end
	if n == 0 then return end
	local cx, cz = sx / n, sz / n
	if w.cm_tick == state.ticks - 1 and w.cx and state.dt > 0 then
		local v = math.sqrt((cx - w.cx) ^ 2 + (cz - w.cz) ^ 2) / state.dt
		if v > 40 then
			w.cmeasured, w.csamples = nil, 0
		else
			w.cmeasured = w.cmeasured and (w.cmeasured * 0.5 + v * 0.5) or v
			w.csamples = (w.csamples or 0) + 1
		end
	elseif w.cm_tick ~= state.ticks - 1 then
		w.cmeasured, w.csamples = nil, 0
	end
	w.cx, w.cz, w.cm_tick = cx, cz, state.ticks
end

-- the walker's lanes as cave 4 sees them: origin, a lane every q metres, lanes kl .. kh (centres at (k + 0.5) q)
local function column_frame(lanes)
	if lanes.one_column then return lanes.cox, lanes.coz, lanes.q, 0, 0 end
	return lanes[1], lanes[2], lanes.q or (lanes[5] * (1 + (lanes[9] or 0)) + lanes[6]), lanes.kl or -lanes[7], lanes.kh or (lanes[7] - 1)
end

-- the geometry of the walker's columns through lanes (also asked at planning time): the across span of the standing unit as it stands
-- with the lanes open (sl .. sh, the radii added), the outer strip, and the squeeze of the walker's files (its outermost files land
-- inside their lane). places: the standing unit's places. Returns sl, sh, strip, squeeze, or nil
function FIRE.col_geometry(s, w, lanes, radius, places)
	local ox, oz, q, kl, kh = column_frame(lanes)
	local sl, sh = nil, nil
	for key, p in pairs(places) do
		local x, z = place_of(lanes, p[1], p[3], key)
		local a = (x - ox) * lanes[3] + (z - oz) * lanes[4]
		if not sl or a < sl then sl = a end
		if not sh or a > sh then sh = a end
	end
	if not sl then return nil end
	local reach = model_radius(s) + radius + LANE_EXTRA
	sl, sh = sl - reach, sh + reach
	-- the walker's outermost files across (cut to what is pulled): a walker wider than the standing unit has files beyond the
	-- outermost strip, which cave 4 clamps into it with the same squeeze (run 51: demigryph files landed in the blocks) - so the
	-- squeeze is worked out from the farthest file of the outer strips: it must land inside its lane
	local strip = q
	local wl, wh = nil, nil
	local pok, wplaces = pcall(FIRE.places, w)
	for _, p in pairs(pok and wplaces or {}) do
		local a = (p[1] - ox) * lanes[3] + (p[3] - oz) * lanes[4]
		if not wl or a < wl then wl = a end
		if not wh or a > wh then wh = a end
	end
	if wl then strip = 2 * math.max(q / 2, math.min(wh, sh) - (kh + 0.5) * q, (kl + 0.5) * q - math.max(wl, sl)) end
	return sl, sh, strip, math.max(0, math.min(0.5, (lanes[6] - 2 * radius) / strip))
end

local stop_columns -- (defined below)

-- the walking unit forms columns (cave 4): its points are pulled into the lanes while they cross the standing unit.
-- A second value true: it forms them for another unit still (the caller may try again later)
local function start_columns(s, w, lanes, radius, gap)
	if lanes.narrow then return 'no columns (the walker was ordered narrow: the game forms its column)' end
	if lanes.vector then return 'no columns (the walker keeps its formation: one lane per file)' end
	if not state.columns or (lanes[8] ~= 0 and not lanes.one_column) then return 'no columns' .. (state.columns and ' (one lane: not needed)' or ' (cave 4 is off)') end
	local lo, hi = nil, nil
	local ok, places = true, s.stood
	if not places then ok, places = pcall(near_places, s) end
	for _, p in pairs(ok and places or {}) do
		local t = (p[1] - lanes[1]) * w.dx + (p[3] - lanes[2]) * w.dz
		if not lo or t < lo then lo = t end
		if not hi or t > hi then hi = t end
	end
	if not lo then return 'no columns (standing unit not readable)' end
	if w.columns_for and w.columns_for ~= s then
		-- its columns are for another unit (the other line of two): handed over once at most a quarter of its bodies are
		-- still inside that unit's zone, else it meets this unit in its full formation. Bodies that have not reached the
		-- zone yet do not count when this unit is the nearer one (it takes over from the farther line that opened first)
		-- but do when it is the farther one (it waits until the walker is through the nearer line)
		local o = w.columns_for
		local refusal = 'no columns (the walker forms them for another unit)'
		local zone = o.col_zone
		local bok, bodies = pcall(bodies_of, w)
		if not zone or not bok or #bodies == 0 then return refusal, true end
		local nearer = (s.x - zone[1]) * zone[3] + (s.z - zone[2]) * zone[4] < zone[5] - COLUMN_POST
		local r, inside = (model_radius(w)), 0
		for _, b in ipairs(bodies) do
			local t = (b[1] - zone[1]) * zone[3] + (b[2] - zone[2]) * zone[4]
			if t - r <= zone[5] and (t >= zone[6] - r or not nearer) then inside = inside + 1 end
		end
		if inside * 4 > #bodies then return refusal, true end
		stop_columns(o)
		if nearer and o.lane > 0 and not o.closing then o.columns_wait = { w = w, radius = zone[7] } end -- (the farther line takes them back behind this one)
		log(("  COLUMNS %s: %s hands the walker's columns over to %s (%d of %d of its bodies still in its zone)"):format(w.label, o.label, s.label, inside, #bodies))
	end
	-- across the way: only the walker's points in front of the standing unit (as it stands with its lanes open) are
	-- pulled; files that pass beside it keep their places
	local sl, sh, strip, squeeze = FIRE.col_geometry(s, w, lanes, radius, ok and places or {})
	if not sl then return 'no columns (standing unit not readable)' end
	local ox, oz, q, kl, kh = column_frame(lanes)
	local outer = (lanes[7] - 0.5) * q -- the centre of the outermost lane
	local step = math.max(q / 2, w.half - outer) -- the widest sideways step of a walker model into its lane
	-- the walker starts forming its columns together with the split (not earlier: run 14, the walker's formation changed
	-- before the standing unit split): from where its front is now
	local t0 = lo - math.max(COLUMN_LEAD, gap or (COLUMN_LEAD + w.speed * FIRE.open_margin(w) + step))
	local t1 = hi + COLUMN_POST
	put_float(w.at + EW.OX, ox)
	put_float(w.at + EW.OZ, oz)
	put_float(w.at + EW.AX, lanes[3])
	put_float(w.at + EW.AZ, lanes[4])
	put_float(w.at + EW.BX, w.dx)
	put_float(w.at + EW.BZ, w.dz)
	put_float(w.at + EW.T0, t0)
	put_float(w.at + EW.T1, t1)
	put_float(w.at + EW.C, squeeze)
	put_float(w.at + EW.LAG, COLUMN_LAG)
	put_float(w.at + EW.MIN, COLUMN_MIN_AHEAD)
	put_float(w.at + EW.SL, sl)
	put_float(w.at + EW.SH, sh)
	mr.write(state.tbl, w.at + EW.KL, s32(kl)) -- -N (small-number bytes: see float_bytes)
	mr.write(state.tbl, w.at + EW.KH, mr.uint32(kh))
	put_float(w.at + E_WQ, q) -- last: this switches the columns on
	if not state.walker_checked then
		state.walker_checked = true
		log(('  walker fields read back: origin %s / %s (wrote %.2f / %.2f), across %s / %s (wrote %.2f / %.2f), lanes k %d .. %d'):format(
			tostring(mr.read_float(state.tbl, w.at + EW.OX)), tostring(mr.read_float(state.tbl, w.at + EW.OZ)), lanes[1], lanes[2],
			tostring(mr.read_float(state.tbl, w.at + EW.AX)), tostring(mr.read_float(state.tbl, w.at + EW.AZ)), lanes[3], lanes[4],
			signed_word(mr.read(state.tbl, w.at + EW.KL, 4)), mr.read_uint32(state.tbl, w.at + EW.KH)))
	end
	w.columns_for, s.columns_of = s, w
	local sok, shape = pcall(near_places, w) -- the shape it had before the columns (the shape check)
	w.shape_base = sok and shape or nil
	s.col_zone = { lanes[1], lanes[2], w.dx, w.dz, t1, t0, radius } -- the zone it wrote (t along the walker's way from the origin)
	return ('columns: %d columns every %.1f m, files squeezed to %.0f %% of their strip (outer strip %.1f m%s), from %.1f m before to %.1f m behind the standing unit, %.1f .. %.1f m across (beside it: free)'):format(
		kh - kl + 1, q, squeeze * 100, strip, lanes.wsp and (', files %.1f m apart across -> %.1f m, a model needs %.1f m'):format(lanes.wsp, lanes.wsp * squeeze, 2 * radius) or '', lo - t0, t1 - hi, sl, sh)
end

function stop_columns(s)
	s.col_zone = nil
	local w = s.columns_of
	if w and w.columns_for == s then
		mr.write(state.tbl, w.at + E_WQ, mr.uint32(0))
		w.cols_off_at, w.cols_off_for = state.ticks, s.label
		w.columns_for = nil
	end
	s.columns_of = nil
end

-- the lanes of s as far as step k of s.steps: lane width and closing up grow with the steps
local function step_lanes(s, k)
	local f = math.min(1, k / s.steps)
	local cur = {}
	for i, v in pairs(s.lanes) do cur[i] = v end
	local from = s.lanes.from
	if from then
		local dl = from[1] + (-s.lanes[8] * s.lanes[6] - from[1]) * f
		local dr = from[2] + ((1 + s.lanes[8]) * s.lanes[6] - from[2]) * f
		cur[6], cur[8], cur[9] = dl + dr, -dl / (dl + dr), from[3] + ((s.lanes[9] or 0) - from[3]) * f
	else
		cur[6], cur[9] = s.lanes[6] * f, (s.lanes[9] or 0) * f
	end
	cur.lay_f = f -- (085i: the layout's correction grows with the steps)
	return cur
end

-- the probe of the split's pace: before every further step, how far the models of s have come of what was planned. Its
-- next split is planned at the pace it showed; one that is behind finishes at a run now.
local function check_pace(s)
	if not s.stood or not s.shift or s.steps <= 1 or not s.sent_jobs then return end -- (not sent: nothing to measure)
	local planned = s.shift * s.step / s.steps -- where its farthest model should be by now
	if planned < 0.5 then return end
	local ok, now = pcall(model_places, s)
	if not ok then return end
	local farthest = 0
	for key, old in pairs(s.stood) do
		local new = now[key]
		if new then farthest = math.max(farthest, math.sqrt((new[1] - old[1]) ^ 2 + (new[3] - old[3]) ^ 2)) end
	end
	local ratio = math.max(0.2, math.min(1.5, farthest / planned))
	s.pace = math.max(0.5, math.min(1.5, (s.pace or 1) * (1 + ratio) / 2))
	if ratio < TUNE.PACE_BEHIND then
		log(('  HURRY %s: its models came %.1f of %.1f m by step %d of %d: the rest at once'):format(s.label, farthest, planned, s.step, s.steps))
		s.step = s.steps - 1 -- next_step makes the last one
	end
end

local function next_step(s, no_pace, prev)
	if not no_pace then pcall(check_pace, s) end
	s.step = s.step + 1
	local cur = step_lanes(s, s.step)
	put_float(s.at + E_G, cur[9] or 0)
	put_float(s.at + E_L, cur.art and 0 or cur[6]) -- artillery: its machines stay, the game's own places are left alone
	put_float(s.at + E_BIAS, cur[8])
	if not cur.art and not (prev and TUNE.FIRE_GAP_DIRTY == 0) then mr.write(state.tbl, s.at + E_DIRTY, mr.uint32(1)) end -- (a gap update: not, see TUNE.FIRE_GAP_DIRTY)
	s.step_at = state.game_seconds
	return send_stations(s, cur, 0, prev)
end

-- guard mode, and the room inside the outline is less than the planned lanes need: a walker of columns (lanes.wf files known) gets
-- ONE lane centred on its way, as wide as the free room, instead of narrower lanes whose columns keep their files and pile up (run 51:
-- two lanes of 1.8 - 2.9 m, every walker piled up). The room is worked out again for the one lane (make_room); the planned timing and
-- steps stay: the farthest model goes at most half the lanes' total, never more than the planned shift, so the split is not later than
-- planned. Works on `lanes` in place. Returns the room text, or nil and why when not even one file fits or the column would be
-- GUARD_DEPTH_MAX times as deep as the walker's ranks (the walker then goes round as in the game).
function FIRE.guard_one_lane(s, w, lanes, room)
	local r = model_radius(w)
	local least = 2 * r + LANE_EXTRA
	local files, spacing = lanes.wf, lanes.wsp
	local two = lanes[8] == 0 -- (2n lanes now; else the walker already has one column's lane)
	local cx, cz = lanes[1], lanes[2] -- where the walker's line crosses the standing unit
	if not two then cx, cz = lanes.cox + lanes[3] * lanes.q / 2, lanes.coz + lanes[4] * lanes.q / 2 end
	local one = { cx - lanes[3] * 50000, cz - lanes[4] * 50000, lanes[3], lanes[4], 100000, two and lanes[6] * lanes[7] * 2 or lanes[6], 1, -0.5 }
	one.one_column, one.wf, one.wsp = true, files, spacing
	local ok, text = pcall(make_room, s, w, one) -- fresh, for one lane: it needs half its width on each side
	if not ok then return nil, 'room for one lane not readable' end -- (085e G2: it used to count as success and open lanes one file wide)
	local avail = math.min(one[6], one.narrow_to or one[6])
	if avail < least - 0.05 then return nil, ('needs %.1f m for one file, has %.1f m'):format(least, avail) end
	local per = math.floor((avail - least) / spacing + 1e-6) + 1
	local deep = math.ceil(files / per - 1e-9)
	if deep > TUNE.GUARD_DEPTH_MAX then
		return nil, ('needs %.1f m for a column of %d files, has %.1f m: %d files per column would make it %d times as deep as the walker, at most %.1f'):format(
			(files - 1) * spacing + least, files, avail, per, deep, TUNE.GUARD_DEPTH_MAX)
	end
	local width = math.min(avail, (files - 1) * spacing + least, LANE_MAX)
	one[6] = width
	one.q = math.max(width, 2 * w.half)
	one.cox, one.coz = cx - lanes[3] * one.q / 2, cz - lanes[4] * one.q / 2
	ok, text = pcall(make_room, s, w, one) -- once more with the lane it gets: closing up and the split point S0
	if not ok then one[9], one[10], text = 0, 50000, 'beside it: not readable (' .. tostring(text) .. ')' end
	w.formation = ('guard mode: walker %d files, %.1f m apart -> ONE column %d files wide (%d times its depth)'):format(files, spacing, math.min(files, per), deep)
	for k in pairs(lanes) do lanes[k] = nil end
	for k, v in pairs(one) do lanes[k] = v end
	return ('%s; GUARD MODE: ONE lane of %.1f m instead of %s (the room inside its outline)'):format(text, width, two and 'narrower lanes' or 'a wider one')
end

-- the lanes of s open: their numbers go into the table, the first step is sent (the common core of the lanes for a walker and
-- for the line of fire; a field of FIRE because the main chunk is near the 200 locals of the game's Lua). w only gives the
-- direction the room beside s is worked out for. Returns the jobs sent, the template (or why not) and the text about the room.
function FIRE.begin_lanes(s, w, lanes, steps, step_time)
	local lane = lanes[6]
	local gok, guard = pcall(function() return s.unit:is_behaviour_active('defend') end) -- (guard mode: read once, at the opening)
	s.guard_now = gok and guard == true
	local t_room = diag.t()
	local prev_sw = diag.sw('plans')
	local room_ok, room = pcall(make_room, s, w, lanes) -- fresh: where the units beside it stand now
	diag.add('plans', t_room)
	diag.sw(prev_sw)
	if not room_ok then lanes[9], lanes[10], room = 0, lanes[8] ~= 0 and 50000 or 0, 'beside it: not readable (' .. tostring(room) .. ')' end
	-- 085f (F3): guard mode and no room inside the outline: the guard limit is dropped for this opening, the normal lanes open (the room afresh)
	local function unguard(why)
		s.guard_now, s.guard_dropped = false, why
		room_ok, room = pcall(make_room, s, w, lanes)
		if not room_ok then lanes[9], lanes[10], room = 0, lanes[8] ~= 0 and 50000 or 0, 'beside it: not readable (' .. tostring(room) .. ')' end
		room = ('GUARD MODE: no room inside the outline of %s for %s (%s) - normal lanes instead; %s'):format(s.label, w.label, why, tostring(room))
	end
	if room_ok and lanes.narrow and lanes.narrow_to and lanes.narrow_to < lanes[6] - 0.05 then
		local why = ('needs %.1f m for the narrowed column, has %.1f m'):format(lanes[6], lanes.narrow_to) -- (085e: the column is already as narrow as ordered)
		if not s.guard_now then return nil, why end
		unguard(why)
	end
	if room_ok and s.guard_now and lanes.wf and not w.gone and not lanes.vector and not lanes.art and lanes.narrow_to and lanes.narrow_to < lanes[6] - 0.05 then
		local text, why = FIRE.guard_one_lane(s, w, lanes, room)
		if text then
			room, lane = text, lanes[6]
		else
			unguard(why)
		end
	end
	if room_ok and lanes.narrow_to and not w.gone and not lanes.vector and not lanes.art and lanes.narrow_to < lanes[6] - 0.05 then
		-- guard mode and not enough room inside the outline: narrower lanes, never below one file of the walker
		local least = 2 * (model_radius(w)) + LANE_EXTRA
		local nl = math.max(lanes.narrow_to, least)
		if nl < lanes[6] - 0.05 then
			room = room .. ('; lanes narrowed from %.1f to %.1f m%s'):format(lanes[6], nl, nl > lanes.narrow_to + 0.05 and ' (one walker file at least)' or '')
			lanes[6], lane = nl, nl
		end
	end
	-- lanes per file (and lanes of their own) lie where the walker's files cross: closing up (G < 0) would draw them
	-- inwards, so the blocks get wider by as much (the period P (1 + G) + L stays)
	if lanes.vector and (lanes[9] or 0) < 0 then lanes[5] = lanes[5] / (1 + lanes[9]) end
	-- 085i: the layout once more for the lanes as they are now (narrowed, one lane, ...): the blocks that the closing up cramps get extra ranks
	local made_note = lanes.lay_note
	local lok, lerr = pcall(function() return FIRE.layout(s, lanes, near_places(s)) end)
	if not lok then lanes.layout, lanes.lay_note, lanes.lay_move = nil, nil, nil log(('  (layout of %s: %s)'):format(s.label, tostring(lerr))) end
	if lanes.lay_note then
		if lanes.lay_note ~= made_note then room = room .. '; LAYOUT (final lanes): ' .. lanes.lay_note end
		if lanes.lay_oblique then log(('LAYOUT OBLIQUE %s for %s: the walker comes %.0f deg off the depth axis of the standing unit (limit %d); the ranks grow along the lanes, the outline is checked in the unit axes'):format(s.label, w.label, lanes.lay_oblique, TUNE.LAY_OBLIQUE)) end
	end
	s.goal = { s.gx, s.gz, s.gw }
	put_float(s.at + E_OX, lanes[1])
	put_float(s.at + E_OZ, lanes[2])
	put_float(s.at + E_AX, lanes[3])
	put_float(s.at + E_AZ, lanes[4])
	put_float(s.at + E_P, lanes[5])
	mr.write(state.tbl, s.at + E_N, mr.uint32(lanes[7]))
	put_float(s.at + E_BIAS, lanes[8])
	put_float(s.at + E_S0, lanes[10] or 0)
	s.lane, s.lanes, s.quiet, s.open_ticks, s.col_logged, s.widened_for, s.widen_said, s.served, s.closing, s.parked = lane, lanes, 0, 0, false, {}, {}, {}, nil, nil
	s.steps, s.step, s.step_time, s.shift = steps or 1, 0, step_time or 0, farthest_shift(lanes)
	s.opened_at, s.columns_wait, s.near_walkers, s.lanes_full, s.half_closed, s.half_off = state.ticks, nil, nil, nil, nil, nil
	local ok, stood = pcall(near_places, s)
	s.stood = ok and stood or {}
	mr.write(state.tbl, s.at + E_WAIT, mr.uint32(waits(s, WAIT_TICKS))) -- cave 1 uses it for the game's own handing out
	-- step 1 now (the game's own handing out of places uses the lanes as far as the current step); the next steps
	-- follow every step_time seconds (update)
	s.step = 1
	local first = step_lanes(s, 1)
	put_float(s.at + E_G, first[9] or 0)
	put_float(s.at + E_L, lanes.art and 0 or first[6]) -- this opens the lanes for the game's own handing out of places
	if not lanes.art then mr.write(state.tbl, s.at + E_DIRTY, mr.uint32(1)) end -- (artillery: its own places stay)
	s.step_at, s.crew_post = state.game_seconds, state.game_seconds
	local sent, tpl = send_stations(s, first, waits(s, WAIT_TICKS)) -- and this sends every model to its first step now
	s.sent_jobs = sent > 0
	return sent, tpl, room
end

local function open_lanes(s, w, lanes, radius, carried, t_contact, t_split, gap, speed, s_speed, steps, step_time)
	local ax, az, lane = lanes[3], lanes[4], lanes[6]
	local sent, tpl, room = FIRE.begin_lanes(s, w, lanes, steps, step_time)
	if not sent then -- (085f: a guard-mode refusal opens normal lanes in begin_lanes) no lanes for this walker; it is left out (as a late one) and goes round as in the game
		s.late = s.late or {}
		s.late[w] = { w.order_at, s.gx, s.gz, math.sqrt((w.x - s.x) ^ 2 + (w.z - s.z) ^ 2), w.gx, w.gz, 'nolanes' }
		log(('no lanes: %s could not open for %s (%s) - it walks round as in the game'):format(s.label, w.label, tostring(tpl)))
		return false
	end
	lane = lanes[6] -- (the real width: guard one-lane / narrowed)
	-- the walker may be given new places by the game while it passes (a regroup that undoes the columns). Cave 1 counts
	-- every place the game hands out in a listed unit (E_SEQ): noted now, compared later
	s.walker_seen, s.walker_seq, s.walker_new = w, mr.read_uint32(state.tbl, w.at + E_SEQ), 0
	local t_cols = diag.t()
	local prev_sw = diag.sw('columns')
	local columns, busy = start_columns(s, w, lanes, radius, gap) -- the walker forms its columns from now on
	diag.add('columns', t_cols)
	diag.sw(prev_sw)
	if busy then s.columns_wait = { w = w, radius = radius } end -- (serve tries again once it is through the other unit)
	if (lanes[8] == 0 or lanes.vector or lanes.one_column or lanes.one_body or lanes.narrow) and w.formation then columns = w.formation .. '; ' .. columns end
	columns = columns .. ('; split in %d steps of %.1f m, one every %.1f s; walker heading from its %s; %s'):format(s.steps, s.shift / s.steps, s.step_time, tostring(w.heading), LEAD.note(s))
	if lanes.vector and not lanes.whole_front and not lanes.art then
		-- 085d review 8: the room of the lanes per file, as n_note says it for columns (make_room closes the standing unit up, at most MAX_CLOSE_UP)
		local need, out_max, g = (lanes[7] or 0) * lanes[6], TUNE.OUT_SHARE * 2 * (s.half or 0), lanes[9] or 0
		if s.guard_now then out_max = math.min(out_max, TUNE.GUARD_OUT) end
		local over = need + g * (s.half or 0) - out_max -- (what still sticks out of the outline after closing up)
		columns = columns .. ('; room: need %.1f m per side vs %.1f m out + closing up %.0f %%%s'):format(need, out_max, -g * 100,
			(g <= -MAX_CLOSE_UP + 1e-6 and over > 0.05) and ('; room clamped by %.1f m'):format(over) or '')
	end
	if s.open_note then columns = columns .. '; ' .. s.open_note end
	state.opened = state.opened + 1
	log(('OPEN  %s makes way for %s (%d models, radius %.2f%s): %s of %.1f m, across (%.2f, %.2f) | timing: fronts %.1f m apart, walker %.1f m/s -> contact in %.1f s; split takes %.1f s (models walk %.1f m/s) | standing unit: ordered bearing %.1f, front %.0f m wide, %d men, %s; %s%s | %s'):format(
		s.label, w.label, w.men, radius, carried and (', carried by a body of radius %.2f'):format(carried) or '',
		lanes.art and ('the gaps of its %d machines (the crews beside them step in to them): %d lanes every %.1f m, %s'):format(lanes.machines, lanes.kh - lanes.kl + 1, lanes.q,
			lanes[6] < lanes.wanted - 0.05 and ('the columns narrowed from %.1f m to lanes'):format(lanes.wanted) .. (lanes[6] < 2 * radius + LANE_EXTRA - 0.05 and ' (one file squeezed, no margins)' or '') or 'lanes')
			or (lanes[8] ~= 0 and 'ONE lane' or ('%d blocks of %.1f m, %d lanes'):format(2 * lanes[7] + 1, lanes[5], 2 * lanes[7])), lane, ax, az,
		gap or -1, speed or -1, t_contact or -1, t_split or -1, s_speed or -1, s.gb, s.half * 2, s.men, room,
		sent > 0 and ('%d models sent at once (facing word %d, kind source %d / %d / %d, extra %d)'):format(sent, tpl.facing, tpl.c63, tpl.c5c, tpl.k2c, tpl.d24)
			or (type(tpl) == 'table' and 'nobody had to move' or ('NOT sent at once: ' .. tostring(tpl) .. ' (the lanes open when the game next hands out places)')),
		(s.via_mount or 0) > 0 and (', %d of them through their mounts'):format(s.via_mount) or '', columns))
	log('  jobs for the models ' .. table.concat(s.job_models or {}, ' '))
	return true
end

local function close_lanes(s, why)
	local moved = moved_since(s)
	local other, stood = (s.fire and s.fire.g.label) or (s.walker_seen and s.walker_seen.label) or '?', s.stood -- (for the shape check)
	mr.write(state.tbl, s.at + E_PENDING, mr.uint32(0)) -- jobs still waiting (a crew's repeated ones) are void
	mr.write(state.tbl, s.at + E_L, mr.uint32(0))
	mr.write(state.tbl, s.at + CN.E_NPLACES, mr.uint32(0)) -- 085i: the layout table is off
	-- artillery: the game's places are never handed out anew (after a teleport its crew walked away from the guns when the
	-- closing had the game hand them out again; build57 left them alone only when nobody had been moved, but the crews
	-- step in to their machines at nearly every opening)
	local touched = s.crew_moved and next(s.crew_moved) ~= nil
	if not s.crewed then mr.write(state.tbl, s.at + E_DIRTY, mr.uint32(1)) end
	stop_columns(s)
	local sent, problem = 0, nil
	-- back to the places noted at the opening; the moved crewmen of artillery beside their machines as they stand now,
	-- also when it closes because it SHOT (artillery fires while its lanes are open; without E_DIRTY its crewmen would
	-- stay on their lane places until the game's own 5 s test) - not after a new order, melee, routing
	local shot = s.crewed and touched and not s.moving and not s.busy and state.ticks - s.shot_at <= SHOT_TICKS
	if (s.stands or shot) and (touched or not s.crewed) then sent, problem = send_stations(s, nil, waits(s, CLOSE_WAIT_TICKS)) end
	if s.crewed and touched and not (s.stands or shot) then problem = 'it no longer stands: ' .. (s.why_not or '?') end
	-- a walker that parked inside s (its order ended there) gets its places handed out anew too, so that both units re-form (not
	-- one that fights, has a crew or lanes of its own)
	for w in pairs(s.parked or {}) do
		if not w.gone and not w.walks and not w.busy and not w.crewed and w.lane == 0 then
			mr.write(state.tbl, w.at + E_DIRTY, mr.uint32(1))
			log(('  %s parked inside %s: its places are handed out anew too, both units re-form'):format(w.label, s.label))
		end
	end
	s.lane, s.lanes, s.stood, s.goal, s.closed_at, s.steps, s.step = 0, nil, nil, nil, state.ticks, nil, nil
	local regroup = s.walker_seen and ('; the walker was given %d places by the game meanwhile'):format(s.walker_new or 0) or ''
	s.walker_seen, s.widened_for, s.widen_said, s.served, s.closing, s.parked, s.crew_moved, s.crew_post = nil, nil, nil, nil, nil, nil, nil, nil
	s.opened_at, s.columns_wait, s.quick_gone, s.lanes_full, s.half_closed, s.half_off, s.cols_next, s.cols_done, s.guard_dropped = nil, nil, nil, nil, nil, nil, nil, nil, nil
	local closed = s.fire and 'FIRE CLOSE' or 'CLOSE'
	s.fire = nil
	diag.shape_arm(s, 'stander', other, why, stood)
	log(('%s %s (%s); open for %.1f s; %s%s; %s'):format(closed, s.label, why, (s.open_ticks or 0) * TICK_MS / 1000, moved, regroup,
		s.crewed and ((sent > 0 and (sent .. ' crewmen sent back beside their machines') or (touched and ('no crewman sent back' .. (type(problem) == 'string' and (' (' .. problem .. ')') or ''))
			or 'no crewman was moved')) .. ': the game\'s places are left alone') or (sent > 0 and (sent .. ' models sent back at once') or ('not sent back at once' .. (type(problem) == 'string' and (': ' .. problem) or '')))))
end

-- the band across the lanes (x * ax + z * az) that walker w sweeps while it crosses the standing unit (places: its
-- models): its bodies as they are now, carried along their way through the unit's depth (lanes placed where the walkers
-- were when the lanes opened missed a walker drifting 2 deg over 20 m), its radius added. Returns lo, hi, or nil and
-- why
local function band_of(s, w, ax, az, places)
	local dx1, dz1 = az, -ax -- along the lanes
	local tmin, tmax = nil, nil
	for _, q in pairs(places) do
		local t = q[1] * dx1 + q[3] * dz1
		if not tmin or t < tmin then tmin = t end
		if not tmax or t > tmax then tmax = t end
	end
	if not tmin then return nil, 'the standing unit cannot be read' end
	if not w.dx then return nil, 'its way is not known' end
	local k = w.dx * ax + w.dz * az -- across the lanes per metre of its way
	local k1 = w.dx * dx1 + w.dz * dz1 -- along them
	if math.abs(k1) < 0.25 then return nil, 'it walks nearly square across the lanes' end
	local ok, bodies = pcall(bodies_of, w)
	if not ok or #bodies == 0 then return nil, 'its bodies cannot be read' end
	local reach = model_radius(s)
	local list = {}
	for _, b in ipairs(bodies) do
		local tb = b[1] * dx1 + b[2] * dz1
		local u0, u1 = (tmin - reach - tb) / k1, (tmax + reach - tb) / k1
		if u0 > u1 then u0, u1 = u1, u0 end
		if u1 > 0 then list[#list + 1] = { b[1] * ax + b[2] * az, math.max(0, u0), u1 } end
	end
	local lo, hi = sweep(list, k, model_radius(w) / math.abs(k1))
	if not lo then return nil, 'already through' end
	return lo, hi
end

-- lanes of their own for single-body walkers side by side (all walking along the lanes, within 15 deg), evenly spaced
-- and far enough apart for a standing block between them: like the lanes per file of one big walker. Each lane holds
-- the band its walker sweeps (band_of); walkers whose bands overlap (one behind the other) share a lane. Up to
-- TUNE.OWN_MAX lanes. Returns the new lanes, or nil (then the one wide lane stays)
local function own_lanes(s, lanes, walkers, places)
	local ax, az = lanes[3], lanes[4]
	local bands = {}
	for _, w in ipairs(walkers) do
		if not w.dx or math.abs(w.dx * ax + w.dz * az) > 0.26 then return nil end
		local ok, bodies = pcall(bodies_of, w)
		if not ok or #bodies ~= 1 then return nil end
		local lo, hi = band_of(s, w, ax, az, places)
		if not lo then return nil end
		bands[#bands + 1] = { lo, hi }
	end
	table.sort(bands, function(a, b) return a[1] < b[1] end)
	local files = {}
	for _, b in ipairs(bands) do
		local last = files[#files]
		if last and b[1] < last[2] + LANE_EXTRA then last[2] = math.max(last[2], b[2]) else files[#files + 1] = { b[1], b[2] } end
	end
	local n = #files
	if n < 2 or n > TUNE.OWN_MAX then return nil end
	local lane, centres = 0, {}
	for i, f in ipairs(files) do
		centres[i] = (f[1] + f[2]) / 2
		lane = math.max(lane, f[2] - f[1] + LANE_EXTRA)
	end
	if lane > LANE_MAX then return nil end
	lane = math.max(LANE_MIN, lane)
	local spacing = (centres[n] - centres[1]) / (n - 1)
	for i = 2, n do
		if math.abs(centres[i] - centres[i - 1] - spacing) > GUN_GAP_SLACK * spacing then return nil end
	end
	local block = spacing - lane
	if block < 2 * model_radius(s) + 0.2 then return nil end
	-- the middle of the lanes, on the line through the split point along the lanes
	local px, pz = lanes[1] + ax * 50000, lanes[2] + az * 50000
	local mid = (centres[1] + centres[n]) / 2 - (px * ax + pz * az)
	local ox, oz = px + ax * mid, pz + az * mid
	if n % 2 == 1 then ox, oz = ox - ax * spacing / 2, oz - az * spacing / 2 end
	local own = { ox, oz, ax, az, block, lane, math.ceil(n / 2), 0 }
	own.vector, own.walkers, own.files = true, #walkers, n
	return own
end

-- a further walker while lanes of their own are open: does it fit one of them? (they do not grow)
local function fits_own(s, lanes, w)
	if not big_walker(w) then return 'not a big walker', true end
	local lo, hi = band_of(s, w, lanes[3], lanes[4], s.stood or near_places(s))
	if not lo then return hi, false end
	local q = lanes[5] * (1 + (lanes[9] or 0)) + lanes[6]
	local a = lanes[1] * lanes[3] + lanes[2] * lanes[4]
	local k = math.max(-lanes[7], math.min(lanes[7] - 1, math.floor(((lo + hi) / 2 - a) / q)))
	local c = a + (k + 0.5) * q
	if lo >= c - lanes[6] / 2 - 0.05 and hi <= c + lanes[6] / 2 + 0.05 then
		s.served[w] = true
		return 'it fits one of the lanes of their own'
	end
	return 'it fits none of the lanes of their own (they stay as they are)'
end

-- how far the gap of these lanes (one lane around a split) has to reach to hold walker w as well, for the places of the
-- standing unit given (several big units through one standing unit, also at an angle). Only one lane around a split
-- grows (single bodies, one wide lane); lanes per file, infantry columns and artillery gaps stay. Returns nl, nr and {
-- k, radius, dl, dr } (for the log); or nil and why, plus true when that is final (not one lane, not a big walker, too
-- wide); 'fits' / 'beside' / 'through' are looked at again (the walker may turn).
local function widen_plan(s, lanes, w, places)
	-- 085h: a walker that was narrowed and is not (any more: forgotten, the AI's own order, the game did not take it) drives wide into the ONE narrow lane: the lane grows (0.8's safety net)
	local stuck = NARROW.stuck(s, w)
	if not stuck and not big_walker(w) then return nil, 'not a big walker: it goes through the lanes as they are', true end
	if lanes.narrow and not stuck then return nil, 'the lane is narrowed for another walker: it goes through the lanes as they are', true end
	if lanes.art or lanes.one_column or lanes[8] == 0 then return nil, 'its lanes are not one lane around a split', true end
	local ax, az = lanes[3], lanes[4]
	local px, pz = lanes[1] + ax * 50000, lanes[2] + az * 50000 -- the split point
	local radius = model_radius(w)
	-- the standing unit across the lanes (smin .. smax) and along them (tmin .. tmax)
	local dx1, dz1 = az, -ax -- along the lanes: the first walker's way
	local smin, smax, tmin, tmax = nil, nil, nil, nil
	for _, q in pairs(places) do
		local a, t = (q[1] - px) * ax + (q[3] - pz) * az, (q[1] - px) * dx1 + (q[3] - pz) * dz1
		if not smin or a < smin then smin = a end
		if not smax or a > smax then smax = a end
		if not tmin or t < tmin then tmin = t end
		if not tmax or t > tmax then tmax = t end
	end
	if not smin then return nil, 'the standing unit cannot be read' end
	local k = w.dx * ax + w.dz * az -- across the lanes per metre of its way
	local k1 = w.dx * dx1 + w.dz * dz1 -- along them per metre
	if math.abs(k1) < 0.25 then return nil, 'it walks nearly square across the lanes (no lane along them can hold it)' end
	local ok, bodies = pcall(bodies_of, w)
	if not ok or #bodies == 0 then return nil, 'its bodies cannot be read' end
	-- each body: the stretch u0 .. u1 of its way that lies within the depth of the standing unit (its models' reach
	-- added), and only what is still ahead of it
	local reach = model_radius(s)
	local list = {}
	for _, b in ipairs(bodies) do
		local tb = (b[1] - px) * dx1 + (b[2] - pz) * dz1
		local u0, u1 = (tmin - reach - tb) / k1, (tmax + reach - tb) / k1
		if u0 > u1 then u0, u1 = u1, u0 end
		if u1 > 0 then list[#list + 1] = { (b[1] - px) * ax + (b[2] - pz) * az, math.max(0, u0), u1 } end
	end
	-- at an angle its band is wider across the lanes: radius / cos
	local lo, hi = sweep(list, k, radius / math.abs(k1))
	if not lo then return nil, 'already through' end
	if lo > smax + reach or hi < smin - reach then return nil, 'it passes beside it' end
	local dl, dr = -lanes[8] * lanes[6], (1 + lanes[8]) * lanes[6]
	local nl, nr = widen(dl, dr, lo, hi, LANE_EXTRA / 2, stuck and 1e9 or LANE_WIDE_MAX)
	if nl and stuck and nl + nr > LANE_WIDE_MAX then -- 085h review: a stuck walker wider than the most gets the most
		local f = LANE_WIDE_MAX / (nl + nr)
		nl, nr = nl * f, nr * f
	end
	if not nl then
		if nr == 'fits' then return nil, 'it fits the open lane' end
		return nil, nr, true
	end
	return nl, nr, { k = k, radius = radius, dl = dl, dr = dr }
end

local function angle_of(k)
	return math.deg(math.asin(math.min(1, math.abs(k))))
end

-- a further walker w while the lanes of s are open: does it fit them? If not, the gap grows to hold it as well.
-- Returns nil while it is too early to grow, else why, and true when that is final.
local function try_widen(s, w)
	local lanes = s.lanes
	if not lanes then return 'no lanes open', true end
	if lanes.walkers then return fits_own(s, lanes, w) end
	local nl, nr, info = widen_plan(s, lanes, w, s.stood or near_places(s))
	if not nl then
		if nr == 'it fits the open lane' then s.served[w] = true end
		if info == true and nr:sub(1, 13) == 'it would need' then log(('NO WIDER LANE %s for %s too: %s'):format(s.label, w.label, nr)) end
		return nr, info == true
	end
	local cur = step_lanes(s, s.step or s.steps or 1)
	local new = {}
	for i, v in pairs(lanes) do new[i] = v end
	new[6], new[8] = nl + nr, -nl / (nl + nr)
	new.from = { -cur[8] * cur[6], (1 + cur[8]) * cur[6], cur[9] or 0 }
	new.shift = math.max(0.5, nl - new.from[1], nr - new.from[2])
	local t_contact, t_split, _, _, _, steps, step_time = timing(s, w, new)
	-- the rule of open_for for a lane as wide as the whole front, here too (the guns' exclusion may drop for a look, a new
	-- order or a flicker of is_moving, and they must not widen a follower's lanes late)
	if s.half and new[6] >= 2 * s.half and t_contact and t_contact < TUNE.LATE_SHARE * t_split and not NARROW.stuck(s, w) then
		s.late = s.late or {}
		s.late[w] = { w.order_at, s.gx, s.gz, math.sqrt((w.x - s.x) ^ 2 + (w.z - s.z) ^ 2), w.gx, w.gz }
		-- (not final: s.late leaves it out for this order; a new order may bring it in time)
		return ('too late for a lane as wide as its whole front: contact in %.1f s, the split takes %.1f s'):format(t_contact, t_split), false
	end
	if t_contact and t_contact > t_split + FIRE.open_margin(w) + math.max(state.dt, TICK_MS / 1000) then return nil end -- not yet
	local room_ok, room = pcall(make_room, s, w, new, s.stood, true)
	if not room_ok then new[9], new[10] = 0, 50000 end
	-- 085i: the wider gap must not make the standing unit stick out of its outline (5 % per flank) even closed up as far as it may: then it stays as it is
	-- (a walker that is stuck in a narrowed lane still gets it: 085h's safety net)
	if room_ok and (new.over or 0) > 0.05 and not NARROW.stuck(s, w) then
		log(('NO WIDER LANE %s for %s too: a gap of %.1f m would stick out of its outline by %.1f m even closed up (10 %% per side at most)'):format(s.label, w.label, nl + nr, new.over))
		return ('the wider gap would stick out of the outline by %.1f m'):format(new.over), true
	end
	if room_ok then pcall(FIRE.layout, s, new, s.stood) end -- (accepted: now the layout of the wider gap)
	s.lanes, s.lane = new, new[6]
	s.steps, s.step, s.step_time, s.shift = steps or 1, 0, step_time or 0, new.shift
	s.served[w] = true
	log(('WIDER %s for %s too (%d models, radius %.2f, walking %.0f deg across the lanes): the gap %.1f / %.1f m to the sides of the split -> %.1f / %.1f m, ONE lane of %.1f m | %s | split in %d steps, one every %.1f s'):format(
		s.label, w.label, w.men, info.radius, angle_of(info.k), info.dl, info.dr, nl, nr, nl + nr,
		room_ok and tostring(room) or ('beside it: not readable (' .. tostring(room) .. ')'), s.steps, s.step_time))
	next_step(s)
	return 'widened', true
end

-- a walker the lanes of s were opened or grown for that is still inside it (the ranks closed up too fast behind the
-- Doomwheels), or nil
local function crossing_walker(s, skip) -- (skip: 085f, the walkers through at CLOSE_PASSED_FRAC - their stragglers do not hold the lanes)
	s.parked = s.parked or {}
	-- a served walker that still walks and whose ORDER line still crosses s ahead of its back rank (its heading turned to
	-- its movement for a moment, passing() failed and the lanes closed before it came: 00:02:43)
	local function still_coming(w)
		if not w.walks or not w.gx or not s.stood then return false end
		local dx, dz = w.gx - w.x, w.gz - w.z
		local length = math.sqrt(dx * dx + dz * dz)
		if length < 2 then return false end
		dx, dz = dx / length, dz / length
		local front, back, lo, hi = nil, nil, nil, nil
		for _, q in pairs(s.stood) do
			local t, a = (q[1] - w.x) * dx + (q[3] - w.z) * dz, (q[1] - w.x) * -dz + (q[3] - w.z) * dx
			if not front or t < front then front = t end
			if not back or t > back then back = t end
			if not lo or a < lo then lo = a end
			if not hi or a > hi then hi = a end
		end
		local r = model_radius(w) + model_radius(s)
		-- (its order must reach the unit, its back rank still lie ahead, and the line run through its width)
		return back ~= nil and front < length and back + CLEAR_MARGIN > 0 and lo - r < 0 and hi + r > 0
	end
	for w in pairs(s.served or {}) do
		local ok, inside = pcall(still_inside, s, w)
		if skip and skip[w] then ok = false end
		if ok and inside and w.walks then w.crossing_at = state.ticks end -- (parked below only when it really stopped: not while it moves, e.g. its files spreading out of columns)
		if ok and not inside then
			local cok, coming = pcall(still_coming, w)
			if cok and coming then return w end
		end
		if ok and inside then
			if w.walks then -- (not w.moving: that flickers while its models fight, and the lanes would stay open as long as it fights)
				s.parked[w] = nil
				return w
			end
			-- it stopped inside (a reserve parked in the line?): the lanes stay open PARKED_TICKS (5 s) at most
			s.parked[w] = s.parked[w] or state.ticks
			if state.ticks - s.parked[w] < PARKED_TICKS then return w end
		end
	end
	return nil
end

-- for the log: where a walker is when the lanes close
local function walker_note(s, w)
	local ok, text = pcall(function()
		local back, rear = nil, nil
		for _, q in pairs(s.stood or {}) do
			local t = q[1] * w.dx + q[3] * w.dz
			if not back or t > back then back = t end
		end
		local r = model_radius(w)
		for _, b in ipairs(bodies_of(w)) do
			local t = b[1] * w.dx + b[2] * w.dz - r
			if not rear or t < rear then rear = t end
		end
		return ('%s: its rear %.1f m beyond the back rank, %s'):format(w.label, rear - back, w.walks and 'walking' or 'not walking')
	end)
	return ok and text or (w.label .. ': not readable (' .. tostring(text) .. ')')
end

-- 085f (F1): the share of walker w's bodies beyond the back rank of s (as s stood at the opening) + the radii + CLEAR_MARGIN (the margin of still_inside), along
-- w's heading; and how many are not, and how many there are. 0 when it cannot be told
function FIRE.passed_frac(s, w)
	if w.gone or not w.dx or not s.stood then return 0, 0, 0 end
	local back = nil
	for _, q in pairs(s.stood) do
		local t = q[1] * w.dx + q[3] * w.dz
		if not back or t > back then back = t end
	end
	local bodies = bodies_of(w)
	if not back or #bodies == 0 then return 0, 0, 0 end
	local r, reach, behind = model_radius(w), model_radius(s), 0
	for _, b in ipairs(bodies) do
		if b[1] * w.dx + b[2] * w.dz - r <= back + reach + CLEAR_MARGIN then behind = behind + 1 end
	end
	return (#bodies - behind) / #bodies, behind, #bodies
end

-- 085h (as 0.8): a big walker (radius >= BIG_RADIUS) holds the lanes open until ALL its bodies are beyond the back rank; the others at CLOSE_PASSED_FRAC
function FIRE.close_frac(w)
	return model_radius(w) >= TUNE.BIG_RADIUS and 1 or TUNE.CLOSE_PASSED_FRAC
end

-- the arithmetic of closing up: the lanes now, how far the farthest model goes, the steps (a quick close: one) and the time of one step
function FIRE.close_plan(s, quick)
	local cur = step_lanes(s, math.min(s.step or 1, s.steps or 1))
	local far = cur.art and 0 or (cur[8] ~= 0 and math.max(-cur[8] * cur[6], (1 + cur[8]) * cur[6]) or cur[6] * cur[7])
	local steps = quick and 1 or math.max(1, math.ceil(far / STEP_MAX - 1e-9)) -- (a quick close: all at once, the models run when it is further than RUN_BEYOND)
	local slow = s.unit:slow_speed()
	if type(slow) ~= 'number' or slow < 0.3 or slow > 40 then slow = 1.5 end
	return cur, far, steps, (far / steps) / (slow * (s.pace or 1)) + math.max(state.dt, TICK_MS / 1000) / steadiness(s)
end

-- closing up in steps: the models walk back STEP_MAX at a time at their walking pace; the last step sends them to the
-- places noted at the opening (close_lanes)
local function closing_step(s)
	if FIRE.busy_sending(s) then return end -- (a whole step waits for the next look; its time stays due)
	local c = s.closing
	if c.group then -- 085f: a closing begun at CLOSE_PASSED_FRAC waits for walker models still inside, CLOSE_STRAGGLER_S in all, then the game's collision pushes them
		local inside, behind = false, 0
		for w in pairs(c.group) do
			if not w.gone then
				local ok, there = pcall(still_inside, s, w)
				if ok and there then
					inside = true
					local _, n = FIRE.passed_frac(s, w)
					behind = behind + n
				end
			end
		end
		if inside and (c.waited or 0) < TUNE.CLOSE_STRAGGLER_S then
			if c.waiting then c.waited = c.waited + state.game_seconds - c.wait_at end -- (the time of the waiting looks only, not of the steps between)
			c.waited, c.waiting, c.wait_at, c.wait_n = c.waited or 0, true, state.game_seconds, math.max(c.wait_n or 0, behind)
			return
		end
		c.waiting = nil
	end
	c.step = c.step + 1
	if c.step >= c.steps then
		if c.wait_n then
			log(('  CLOSE WAIT %s: %d walker models were still behind the back rank, the closing waited %.1f s for them'):format(s.label, c.wait_n, c.waited or 0))
		end
		local group = c.group
		for w in pairs(group or {}) do -- (085f review 1: a walker that stopped with models inside is re-formed with the unit, as for a parked one)
			if not w.gone and not w.walks then
				local ok, there = pcall(still_inside, s, w)
				if ok and there then
					s.parked = s.parked or {}
					s.parked[w] = s.parked[w] or state.ticks
				end
			end
		end
		close_lanes(s, c.why)
		if group then -- (they are through: not planned for again on this order, nor logged LATE; set after the close, which clears it)
			s.quick_gone = {}
			for w in pairs(group) do s.quick_gone[w] = w.order_at or -1 end
		end
		return
	end
	local f = 1 - c.step / c.steps
	local cur = {}
	for i, v in pairs(c.from) do cur[i] = v end
	cur[6], cur[9] = c.from[6] * f, (c.from[9] or 0) * f
	put_float(s.at + E_G, cur[9])
	put_float(s.at + E_L, cur.art and 0 or cur[6])
	put_float(s.at + E_BIAS, cur[8])
	if not cur.art then mr.write(state.tbl, s.at + E_DIRTY, mr.uint32(1)) end
	c.at = state.game_seconds
	send_stations(s, cur, 0)
end

local function start_closing(s, why, quick, group)
	if FIRE.busy_sending(s) then return false end -- (the next look: whatever made it close is still so then)
	local cur, far, steps, step_time = FIRE.close_plan(s, quick)
	s.closing = { steps = steps, step = 0, step_time = step_time, at = state.game_seconds, why = why, from = cur, group = group }
	local notes = {}
	for w in pairs(s.served or {}) do notes[#notes + 1] = walker_note(s, w) end
	log(('CLOSING %s (%s): back in %d steps of %.1f m, %s | %s; %s'):format(s.label, why, steps, far / steps, quick and 'all at once' or ('one every %.1f s'):format(step_time),
		#notes > 0 and table.concat(notes, '; ') or 'no walker noted', LEAD.note(s)))
	closing_step(s)
	return true
end

-- once per opening, while the walker crosses: how well its models keep to the lanes (for the log only)
local function log_columns(s)
	local w, lanes = s.columns_of, s.lanes
	if not w or not lanes then return true end
	local ox, oz, q, kl, kh = column_frame(lanes)
	local inside, near, far = 0, 0, 0
	local per_lane, per_block = {}, {} -- run 18: "funnelling" = most models in ONE lane, or in the middle block
	local lo, hi = nil, nil
	for _, p in pairs(s.stood or {}) do
		local t = (p[1] - ox) * w.dx + (p[3] - oz) * w.dz
		if not lo or t < lo then lo = t end
		if not hi or t > hi then hi = t end
	end
	if not lo then return true end
	local total = 0
	for _, p in pairs(FIRE.places(w)) do
		total = total + 1
		local t = (p[1] - ox) * w.dx + (p[3] - oz) * w.dz
		if t >= lo and t <= hi then
			inside = inside + 1
			local sx = (p[1] - ox) * lanes[3] + (p[3] - oz) * lanes[4]
			local k = math.max(kl, math.min(kh, math.floor(sx / q)))
			if math.abs(sx - (k + 0.5) * q) <= lanes[6] / 2 then
				near = near + 1
				per_lane[k] = (per_lane[k] or 0) + 1
			else
				far = far + 1
				local j = math.max(kl, math.min(kh + 1, math.floor(sx / q + 0.5)))
				per_block[j] = (per_block[j] or 0) + 1
			end
		end
	end
	if inside * 3 < total then return false end -- not yet a third of the walker inside the standing unit
	-- where the middle of the walker is across the lanes, and how wide each lane is now (from where the standing models
	-- stand): run 18, the walker always took the LEFT lane
	local wsum, wn = 0, 0
	for _, p in pairs(FIRE.places(w)) do
		wsum, wn = wsum + (p[1] - ox) * lanes[3] + (p[3] - oz) * lanes[4], wn + 1
	end
	local lanes_mid = (kl + kh + 1) * q / 2 -- (the middle of the lanes' centres (k + 0.5) q, k = kl .. kh; for one lane (kl = kh = 0) q / 2: the origin is half a lane aside - the old text measured from the origin)
	-- the standing models reaching into each lane now (the narrowest gap read one straggler as a closed lane)
	local in_lane, deep = {}, 0
	local rs = model_radius(s)
	local band = (not RELEASE.on) and model_radius(w) >= 1.0 -- (log only: a big walker's lane band at contact, before any change of LANE_EXTRA)
	local band_in, band_minus, band_plus = 0, nil, nil
	for _, p in pairs(near_places(s)) do
		local t = (p[1] - ox) * w.dx + (p[3] - oz) * w.dz
		if t >= lo - 1 and t <= hi + 1 then
			deep = deep + 1
			local a = (p[1] - ox) * lanes[3] + (p[3] - oz) * lanes[4]
			local k = math.max(kl, math.min(kh, math.floor(a / q)))
			local d = a - (k + 0.5) * q
			if math.abs(d) < lanes[6] / 2 + rs then in_lane[k] = (in_lane[k] or 0) + 1 end
			if band then
				local clear = math.abs(d) - lanes[6] / 2 - rs -- (< 0: the body reaches into the band the walker's models need)
				if clear < 0 then band_in = band_in + 1 end
				if d < 0 then band_minus = math.min(band_minus or clear, clear) else band_plus = math.min(band_plus or clear, clear) end
			end
		end
	end
	local gaps = {}
	for k = kl, kh do gaps[#gaps + 1] = tostring(in_lane[k] or 0) end
	local lanes_text, blocks_text = {}, {}
	for k = kl, kh do lanes_text[#lanes_text + 1] = tostring(per_lane[k] or 0) end
	for j = kl, kh + 1 do blocks_text[#blocks_text + 1] = tostring(per_block[j] or 0) end
	log(('  COLUMNS %s in %s: %d of %d walker models inside the standing unit, %d of them in a lane, %d between lanes | per lane %s | in the blocks %s (the middle block is the middle number) | walker middle %.1f m across from the middle of the lanes | standing models reaching into the lanes now %s (of %d within its depth; lanes planned %.1f m) | across axis (%.2f, %.2f), walking (%.2f, %.2f)'):format(
		w.label, s.label, inside, total, near, far, table.concat(lanes_text, ' / '), table.concat(blocks_text, ' / '), wn > 0 and wsum / wn - lanes_mid or 0, table.concat(gaps, ' / '), deep, lanes[6], lanes[3], lanes[4], w.dx, w.dz))
	if band then
		log(('  lane band at contact: %s: %d standing models inside the band (lane %.1f m, walker radius %.2f), clearance %s / %s m on the minus / plus side of the lane centres'):format(
			w.label, band_in, lanes[6], model_radius(w), band_minus and ('%.2f'):format(band_minus) or 'none', band_plus and ('%.2f'):format(band_plus) or 'none'))
	end
	return true
end

-- the battle's own clock (0.1 s per tick), read from any model: speeds and times are measured in game seconds, so
-- a faster battle speed (or a pause) does not change them
local function game_clock()
	for _, entry in ipairs(state.units) do
		local count, models = nil, nil
		if not entry.gone then count, models = models_of(entry) end
		if count and count > 0 then
			local model = mr.read_pointer(models, 0)
			if not mr.is_null(model) then
				local tick = battle_tick(model)
				if tick then return tick end
			end
		end
	end
	return nil
end

local function merged_walkers(merged)
	local list = {}
	for _, m in ipairs(merged) do list[#list + 1] = m.w end
	return list
end

-- how deep the front bodies of walker w are inside standing unit s along w's way (m; 0 or less: not inside): the nearest model of s in the
-- corridor the walker's bodies sweep (their span across, both radii added) against the walker's front body. Not the gap of timing(): at an
-- angle, or beside a wide unit, its nearest model along the way may lie far to the side of the walker
function FIRE.inside_depth(s, w)
	local ax, az = -w.dz, w.dx
	local r, rs = model_radius(w), model_radius(s)
	local front, lo, hi = nil, nil, nil
	for _, b in ipairs(bodies_of(w)) do
		local t, a = b[1] * w.dx + b[2] * w.dz, b[1] * ax + b[2] * az
		if not front or t > front then front = t end
		if not lo or a < lo then lo = a end
		if not hi or a > hi then hi = a end
	end
	if not front then return 0 end
	local near = nil
	for _, p in pairs(near_places(s)) do
		local a = p[1] * ax + p[3] * az
		if a >= lo - r - rs and a <= hi + r + rs then
			local t = p[1] * w.dx + p[3] * w.dz
			if not near or t < near then near = t end
		end
	end
	if not near then return 0 end
	return (front + r) - (near - rs), front, near
end

-- the lanes of s for the walkers passing it (nearest first): each one's own plan and timing; the one that needs them
-- soonest opens them, and the other big walkers are taken into its lane at once (the lane grown for a second Doomwheel
-- came too late, and a walker that was not the nearest one got its lane late). Returns the walker it opened for, or
-- nil.
local function open_for(s, cands)
	-- ONE body does not make way: the walker goes round it as in the game
	if one_body(s) then
		s.no_lanes_why = 'one body does not make way, walkers go round it'
		if not s.single_said then
			s.single_said = true
			log(('NO LANES %s: one body (%d models) does not make way, walkers go round it'):format(s.label, s.men))
		end
		return nil
	end
	-- far from opening (its last plan left more than TUNE.PLAN_FAR s): it plans again only every TUNE.PLAN_EVERY looks,
	-- while its nearest walker and that walker's order stay the same (a new walker or a new order: at once)
	local first = cands[1] and cands[1].w
	if s.plan_wait and state.ticks < s.plan_wait and s.plan_first == first and first and s.plan_order == first.order_at then
		s.no_lanes_why = ('not yet: far from opening for %s, it plans again in %d looks'):format(first.label, s.plan_wait - state.ticks)
		return nil
	end
	s.plan_wait, s.plan_first, s.plan_order = nil, first, first and first.order_at
	local look = math.max(state.dt, TICK_MS / 1000)
	local said, here = s.no_lanes_said or {}, {}
	s.no_lanes_said = said
	s.no_lanes_why = nil
	local primary = nil
	for i, c in ipairs(cands) do
		if i > MAX_CANDIDATES then break end
		here[c.w] = true
		-- a walker that is not big can never join another's lane: it is planned for only when it is the nearest
		if i == 1 or big_walker(c.w) then
			pcall(measure_walker, c.w)
			state.plans = (state.plans or 0) + 1
			local t_plan = diag.t()
			local prev_sw = diag.sw('plans')
			local ok, lanes, radius, carried = pcall(plan_lanes, s, c.w)
			diag.add('plans', t_plan)
			diag.sw(prev_sw)
			if not ok then
				lanes, radius = nil, 'its plan failed: ' .. tostring(lanes)
			end
			if not lanes then
				s.no_lanes_why = tostring(radius)
				if not said[c.w] then -- once per walker while it comes
					said[c.w] = true
					log(('NO LANES %s for %s: %s'):format(s.label, c.w.label, tostring(radius)))
				end
			else
				lanes[9], lanes[10] = 0, lanes[8] ~= 0 and 50000 or 0 -- the room beside it is worked out fresh at the opening
				t_plan = diag.t()
				prev_sw = diag.sw('plans')
				local tok, t_contact, t_split, t_gap = pcall(timing, s, c.w, lanes)
				diag.add('plans', t_plan)
				diag.sw(prev_sw)
				local inside, inside_at = nil, nil
				if tok and t_gap and t_gap < -math.max(TUNE.LATE_INSIDE, (c.w.speed or 0) * look) then -- (cheap first; then the corridor of its bodies)
					local dok, depth, front, near = pcall(FIRE.inside_depth, s, c.w)
					if dok and depth > math.max(TUNE.LATE_INSIDE, (c.w.speed or 0) * look) then inside, inside_at = depth, ('its front %.1f m, the nearest model %.1f m along its way, radii %.2f / %.2f'):format(front, near, model_radius(c.w), model_radius(s)) end
				end
				if inside then
					-- its front is already inside s (the unit began to stand with it there, or it stopped inside): nothing can form columns
					-- now - left to the game, as a parked walker is (this candidate only; s.late keeps it out and says it once per pair)
					s.late = s.late or {}
					s.late[c.w] = { c.w.order_at, s.gx, s.gz, math.sqrt((c.w.x - s.x) ^ 2 + (c.w.z - s.z) ^ 2), c.w.gx, c.w.gz, 'inside' }
					s.no_lanes_why = ('LATE: %s is already %.1f m inside it'):format(c.w.label, inside)
					log(('LATE %s already %.1f m inside %s - left to the game | %s'):format(c.w.label, inside, s.label, inside_at))
				elseif tok and t_contact then -- (no timing: none of its models near its middle yet, e.g. just teleported)
					c.lanes, c.radius, c.carried = lanes, radius, carried
					c.t_contact, c.t_split = t_contact, t_split
					c.urgency = t_contact - t_split
					if not primary or c.urgency < primary.urgency then primary = c end
				else
					s.no_lanes_why = ('no timing for %s (%s)'):format(c.w.label, tok and 'none of its models near its middle' or tostring(t_contact))
				end
			end
		end
	end
	for w in pairs(said) do if not here[w] then said[w] = nil end end -- gone by: it may be told again next time
	if not primary then
		s.no_lanes_why = s.no_lanes_why or 'no walker it could plan for'
		return nil
	end
	local lanes = primary.lanes
	if lanes.narrow then -- 085e A2: the walker is ordered narrow now, at the first look that plans the pair (it re-forms while it approaches)
		local nok, nproblem = pcall(NARROW.begin, s, primary.w, lanes, primary.t_contact, primary.t_split)
		if nok and nproblem == false then
			s.no_lanes_why = 'narrowing waits for the run state of ' .. primary.w.label -- (no narrow lane opens for a walker that is not narrowed)
			return nil
		end
		if not nok then
			primary.w.narrowed_order = primary.w.order_at -- (not again on this order: the next plan is the columns)
			s.no_lanes_why = 'the narrowing failed: ' .. tostring(nproblem)
			log(('NARROW ERROR %s for %s: %s; the normal way from the next look'):format(primary.w.label, s.label, tostring(nproblem)))
			return nil
		end
	end
	local places = near_places(s)
	local merged, fitting = {}, {}
	for i, c in ipairs(cands) do
		if i > MAX_CANDIDATES then break end
		if c ~= primary and not lanes.narrow and not (s.late and s.late[c.w]) then -- (not one left out as already inside; a narrowed lane is not grown for others)
			local ok, nl, nr, info = pcall(widen_plan, s, lanes, c.w, places)
			if ok and nl then
				lanes[6], lanes[8] = nl + nr, -nl / (nl + nr)
				lanes.shift = math.max(nl, nr)
				merged[#merged + 1] = { w = c.w, info = info }
			elseif ok and nr == 'it fits the open lane' then
				fitting[#fitting + 1] = c.w
			end
		end
	end
	-- walkers SIDE BY SIDE (two Doomwheels -> one lane of 20 m, half the unit 17 m aside): a lane of its own for each, the
	-- standing block between them stays, the outer parts step one lane aside
	local all = { primary.w }
	for _, w in ipairs(merged_walkers(merged)) do all[#all + 1] = w end
	for _, w in ipairs(fitting) do all[#all + 1] = w end
	local own = #merged > 0 and lanes[8] ~= 0 and own_lanes(s, lanes, all, places)
	if own then
		lanes = own
		primary.lanes = own
		primary.w.formation = ('%d walkers side by side -> a lane for each, %d lanes'):format(own.walkers, own.files)
	end
	-- when: as soon as any walker it holds needs it; the split is timed for the one that needs it most. A big walker
	-- needs more room ahead (run 30: "too late for the Hell Pit Abomination", radius 4.6 at 5.9 m/s)
	local t, worst, raw = nil, nil, nil
	local holds = { primary.w }
	for _, m in ipairs(merged) do holds[#holds + 1] = m.w end
	for _, w in ipairs(fitting) do holds[#holds + 1] = w end
	for _, w in ipairs(holds) do
		local ok, a, b, c3, d, e5, f, g = pcall(timing, s, w, lanes)
		if ok and a then
			local extra = ((d and d > 0.3) and math.max(0, model_radius(w) - 1) / d or 0) + (FIRE.open_margin(w) - SPLIT_MARGIN) -- (085d: big / fast walkers open BIG_MARGIN earlier)
			local margin = a - b - extra
			if not worst or margin < worst then worst, t = margin, { a, b, c3, d, e5, f, g } end
			if not raw or a - b < raw[1] then raw = { a - b, a, b, w } end -- (the latest one, without the extra margin)
		end
	end
	if not t or worst > SPLIT_MARGIN + look then
		if t and worst > SPLIT_MARGIN + look + TUNE.PLAN_FAR then s.plan_wait = state.ticks + TUNE.PLAN_EVERY end
		s.no_lanes_why = t and ('not yet: %.1f s of margin left, it opens at %.2f s'):format(worst, SPLIT_MARGIN + look) or 'no timing for the walkers it holds'
		return nil
	end
	-- a walker whose lane takes the whole standing front, too close to split in time (warp lightning cannons 41 m wide ran
	-- into 34 m of stormvermin 2.4 s after the opening, the split took 6.4 s): no lanes, the game's own way. It is left
	-- out until it is past or gets a new order (serve), so a unit following it still gets lanes and it is not planned for
	-- on every look (the width after the merges counts too, as in try_widen) (only when the contact comes before
	-- TUNE.LATE_SHARE of the split - bloodcrushers 5.0 s into a split of 8.3 s were refused by the old rule and then pushed
	-- through as vanilla)
	if (lanes.whole_front or (not own and lanes[8] ~= 0 and s.half and lanes[6] >= 2 * s.half)) and raw[2] < TUNE.LATE_SHARE * raw[3] then
		s.late = s.late or {}
		local lw = raw[4]
		s.late[lw] = { lw.order_at, s.gx, s.gz, math.sqrt((lw.x - s.x) ^ 2 + (lw.z - s.z) ^ 2), lw.gx, lw.gz }
		s.no_lanes_why = ('too late for a lane as wide as its whole front: contact in %.1f s, the split takes %.1f s'):format(raw[2], raw[3])
		log(('NO LANES %s for %s: %s; left out while it passes'):format(s.label, raw[4].label, s.no_lanes_why))
		return nil
	end
	local seen = s.seen_since and s.seen_since[primary.w]
	s.open_note = ('walker seen %.1f s before, the standing unit standing for %.1f s; opening margin %.2f s%s'):format(
		seen and (state.ticks - seen) * TICK_MS / 1000 or -1, s.still_since and (state.ticks - s.still_since) * TICK_MS / 1000 or -1, FIRE.open_margin(primary.w),
		FIRE.open_margin(primary.w) > SPLIT_MARGIN and (' (%.2f + big %.2f: radius %.2f, speed %.1f m/s)'):format(SPLIT_MARGIN, TUNE.BIG_MARGIN, model_radius(primary.w), primary.w.speed or 0) or '')
	if lanes.n_note then s.open_note = s.open_note .. '; ' .. lanes.n_note end
	if not open_lanes(s, primary.w, lanes, primary.radius, primary.carried, t[1], t[2], t[3], t[4], t[5], t[6], t[7]) then return nil end -- (guard mode: no room)
	for _, w in ipairs(holds) do s.served[w] = true end
	for _, m in ipairs(merged) do
		if own then
			log(('WIDER %s for %s too, at the opening (%d models, radius %.2f): a lane of its own, %d lanes of %.1f m with blocks of %.1f m between them'):format(
				s.label, m.w.label, m.w.men, m.info.radius, own.files, lanes[6], lanes[5]))
		else
			log(('WIDER %s for %s too, at the opening (%d models, radius %.2f, walking %.0f deg across the lanes): ONE lane of %.1f m, %.1f / %.1f m to the sides of the split'):format(
				s.label, m.w.label, m.w.men, m.info.radius, angle_of(m.info.k), lanes[6], -lanes[8] * lanes[6], (1 + lanes[8]) * lanes[6]))
		end
	end
	return primary.w
end

-- QUICK CLOSE behind ranged walkers: a missile unit or a gun that retreats through the line is covered at once. When every
-- walker the lanes of s were opened or grown for is a ranged one (unit:missile_range() > 0, not a lord or hero) and each has
-- its rearmost body past the back rank of s, and no other walker needs the lanes soon, s closes at once, in one step.
FIRE.quick = {} -- (a field of FIRE: the main chunk is near the 200 locals of the game's Lua)

function FIRE.quick.is(w)
	if w.quick_tick ~= state.ticks then -- (read at most once per look)
		w.quick, w.quick_tick = false, state.ticks
		if not w.character then
			local ok, range = pcall(function() return w.unit:missile_range() end)
			w.quick = ok and type(range) == 'number' and range > 0
		end
	end
	return w.quick
end

-- its rearmost body is past the back rank of s (as s stood at the opening) and the radii
function FIRE.quick.passed(s, w)
	return (FIRE.passed_frac(s, w)) >= 1 - 1e-9
end

-- does walker w2 (the middle of s `along` metres ahead of it) need the lanes soon: before s could close and open again? extra: seconds more (085f)
function FIRE.quick.soon(s, w2, along, extra)
	extra = extra or 0
	local look = math.max(state.dt, TICK_MS / 1000)
	-- by the timing of the opening for w2: it needs the lanes before s could close (shift / its walking pace) and open again
	local ok, t_contact, t_split = pcall(timing, s, w2, s.lanes)
	if ok and type(t_contact) == 'number' and type(t_split) == 'number' then
		local slow = s.unit:slow_speed()
		if type(slow) ~= 'number' or slow < 0.3 or slow > 40 then slow = 1.5 end
		return t_contact - t_split < (s.shift or 6) / (slow * (s.pace or 1)) + FIRE.open_margin(w2) + look + extra
	end
	local fast = s.unit:fast_speed()
	if type(fast) ~= 'number' or fast < 0.3 or fast > 40 then fast = 6 end
	return along / math.max(w2.speed or 0, 0.5) < 2 * ((s.shift or 6) / fast + look) + FIRE.open_margin(w2) + 1 + extra
end

-- a walker that went through on its order is not planned for again; this ends with a new order, or when it is well clear of s
function FIRE.quick.still_gone(s, w)
	if s.quick_gone[w] ~= (w.order_at or -1) or (w.x - s.x) ^ 2 + (w.z - s.z) ^ 2 > (s.half + (w.half or 0) + 10) ^ 2 then
		s.quick_gone[w] = nil
		return false
	end
	return true
end

-- the text for the log when s closes at once, else nil
function FIRE.quick.check(s, cands)
	local group, names = {}, {}
	if s.walker_seen then group[s.walker_seen] = true end
	for w in pairs(s.served or {}) do group[w] = true end
	for w in pairs(s.near_walkers or {}) do group[w] = true end -- (followers that came near while the lanes were open)
	for w in pairs(group) do
		if w.gone or not FIRE.quick.is(w) or not FIRE.quick.passed(s, w) then return nil end
		names[#names + 1] = w.label
	end
	if #names == 0 then return nil end
	for _, c in ipairs(cands) do
		if not group[c.w] and FIRE.quick.soon(s, c.w, c.along) then return nil end
	end
	table.sort(names)
	return ('quick: the ranged walker%s %s %s through'):format(#names > 1 and 's' or '', table.concat(names, ', '), #names > 1 and 'are' or 'is'), group
end

-- 085f (F1): the lanes may start closing because the walkers they were opened for are through at TUNE.CLOSE_PASSED_FRAC of their bodies. The group is
-- every walker the lanes opened / grew for or that came near; ready when each has passed_frac >= CLOSE_PASSED_FRAC and no other candidate needs the lanes
-- within the time the closing takes (the hold, the steps, the straggler wait) + CLOSE_FOLLOW_S (several units one after another through one unit: they
-- never meet half-closed lanes). Returns the group and the text for the log, or nil
function FIRE.through_ready(s, cands)
	if s.fire or not s.lanes or s.lanes.narrow or not s.stood then return nil end -- (a narrowed column keeps the rule of still_inside)
	local group = {}
	if s.walker_seen then group[s.walker_seen] = true end
	for _, set in ipairs({ s.served or {}, s.near_walkers or {}, s.widened_for or {} }) do
		for w in pairs(set) do group[w] = true end
	end
	local names, n = {}, 0
	local current = {}
	for _, c in ipairs(cands) do current[c.w] = true end
	for w in pairs(group) do
		if not w.gone and not current[w] then -- (085f review 2: one that is no candidate and not inside is no part of the group - a stale 'near once' or a walker re-ordered away)
			local ok, there = pcall(still_inside, s, w)
			if not (ok and there) and FIRE.passed_frac(s, w) < TUNE.CLOSE_PASSED_FRAC - 1e-9 then group[w] = nil end
		end
		if w.gone then
			group[w] = nil
		elseif group[w] then
			local frac = FIRE.passed_frac(s, w)
			if frac < FIRE.close_frac(w) - 1e-9 then return nil end
			n = n + 1
			names[#names + 1] = ('%.0f %% of %s'):format(frac * 100, w.label)
		end
	end
	if n == 0 then return nil end
	local _, _, steps, step_time = FIRE.close_plan(s)
	local extra = HOLD_TICKS * TICK_MS / 1000 + steps * step_time + TUNE.CLOSE_STRAGGLER_S + TUNE.CLOSE_FOLLOW_S
	for _, c in ipairs(cands) do
		if not group[c.w] and FIRE.quick.soon(s, c.w, c.along, extra) then return nil end
	end
	-- 085f review 4: a closing longer than the candidates' horizon (LOOK_SECONDS): a friendly walker further away than that, whose way crosses s, is expected too
	local slow = s.unit:slow_speed()
	if type(slow) ~= 'number' or slow < 0.3 or slow > 40 then slow = 1.5 end
	if extra + (s.shift or 6) / (slow * (s.pace or 1)) > LOOK_SECONDS then
		for _, w2 in ipairs(state.units) do
			if w2 ~= s and not w2.gone and not group[w2] and not current[w2] and w2.walks and not w2.stands and not w2.ethereal and w2.alliance == s.alliance and w2.dx and w2.x then
				local rx, rz = s.x - w2.x, s.z - w2.z
				local along, across = rx * w2.dx + rz * w2.dz, math.abs(rx * w2.dz - rz * w2.dx)
				if along > 0 and across <= s.half + w2.half * 0.5 and (along - s.half) / math.max(w2.speed or 0, 0.5) < extra + (s.shift or 6) / (slow * (s.pace or 1)) + FIRE.open_margin(w2) then return nil end
			end
		end
	end
	table.sort(names)
	return group, table.concat(names, ', ') .. ' through'
end

-- LANE-BY-LANE CLOSING (reduced form): ranged walkers (the quick group) through a plain 3-block plan (two lanes, no vector /
-- one-lane / whole-front / widened plan, not the line of fire). The bodies of each walker are assigned to a lane by cave 4's strips
-- (not by the lane width: bodies not yet in their column are outside the band). When every body in lane k is past the back rank of s
-- and the other lane is still in use, lane k closes alone: the table gets the one-lane frame (origin at the split between the blocks
-- that stay apart - 50000 a, P 100000, N 1, bias -1 / 0, S0 so that the G term stays), the models go to their new places in one
-- step. The original lanes wait in s.lanes_full: a further walker (or a widening) restores them at once.
FIRE.half = {}

-- the lanes (strips of cave 4) in which bodies of walker w have not passed the back rank of s yet: { [k] = true }, or nil
function FIRE.half.busy(s, w)
	local lanes = s.lanes
	local ox, oz, q, kl, kh = column_frame(lanes)
	local back = nil
	for _, p in pairs(s.stood or {}) do
		local t = p[1] * w.dx + p[3] * w.dz
		if not back or t > back then back = t end
	end
	local bodies = bodies_of(w)
	if not back or #bodies == 0 then return nil end
	local r, reach = model_radius(w), model_radius(s)
	local busy = {}
	for _, b in ipairs(bodies) do
		if b[1] * w.dx + b[2] * w.dz - r <= back + reach + CLEAR_MARGIN then
			local a = (b[1] - ox) * lanes[3] + (b[2] - oz) * lanes[4]
			busy[math.max(kl, math.min(kh, math.floor(a / q)))] = true -- (round(a / q - 0.5) = floor(a / q): the strip of cave 4)
		end
	end
	return busy
end

-- which lane (-1: the one on the minus side, 0: the other) closes alone now, and the walkers' names, or nil
function FIRE.half.check(s, cands)
	local lanes = s.lanes
	if s.half_closed or s.half_off or s.fire or not lanes or not s.steps or s.step < s.steps or lanes[8] ~= 0 or lanes[7] ~= 1 or lanes.from or lanes.vector or lanes.art
		or lanes.one_column or lanes.one_body or lanes.whole_front or s.columns_wait then return nil end
	local group, names, busy = {}, {}, {}
	if s.walker_seen then group[s.walker_seen] = true end
	for w in pairs(s.served or {}) do group[w] = true end
	for w in pairs(s.near_walkers or {}) do group[w] = true end
	for w in pairs(group) do
		if w.gone or not FIRE.quick.is(w) then return nil end
		local b = FIRE.half.busy(s, w)
		if not b then return nil end
		for k in pairs(b) do busy[k] = true end
		names[#names + 1] = w.label
	end
	if #names == 0 or busy[-1] == busy[0] then return nil end -- (both lanes in use: nothing yet; none: the quick close does it)
	for _, c in ipairs(cands) do
		if not group[c.w] and FIRE.quick.soon(s, c.w, c.along) then return nil end
	end
	table.sort(names)
	return busy[-1] and 0 or -1, table.concat(names, ', '), group
end

-- all the numbers of lanes into the table (as begin_lanes writes them) and every model to its place of this lanes at once
function FIRE.half.send(s, lanes)
	put_float(s.at + E_OX, lanes[1])
	put_float(s.at + E_OZ, lanes[2])
	put_float(s.at + E_AX, lanes[3])
	put_float(s.at + E_AZ, lanes[4])
	put_float(s.at + E_P, lanes[5])
	mr.write(state.tbl, s.at + E_N, mr.uint32(lanes[7]))
	put_float(s.at + E_BIAS, lanes[8])
	put_float(s.at + E_S0, lanes[10] or 0)
	mr.write(state.tbl, s.at + E_DIRTY, mr.uint32(1))
	return send_stations(s, step_lanes(s, math.min(s.step or 1, s.steps or 1)), 0)
end

function FIRE.half.close(s, k, names, group)
	local lanes = s.lanes
	-- the split between the blocks that stay apart (across, from the origin): lane -1 closed -> the blocks -1 and 0 join, the split
	-- lies between 0 and 1 (+ P / 2); lane 0 closed -> between -1 and 0 (- P / 2)
	local split = (k == -1) and lanes[5] / 2 or -lanes[5] / 2
	local new = {}
	for i, v in pairs(lanes) do new[i] = v end
	new[1], new[2] = lanes[1] + lanes[3] * (split - 50000), lanes[2] + lanes[4] * (split - 50000)
	new[5], new[7] = 100000, 1
	new[8] = (k == -1) and 0 or -1 -- (the one-lane frame: left of the split n = 0, right n = 1, d = (n + bias) L)
	new[10] = (lanes[10] or 0) + 50000 - split
	s.lanes_full, s.lanes, s.half_closed, s.half_group = lanes, new, k, group
	local sent, why = FIRE.half.send(s, new)
	log(('HALF CLOSE %s: lane %d (%s side) closed behind %s, the other stays open | %s'):format(s.label, k, k == -1 and 'minus' or 'plus', names,
		sent > 0 and ('%d models sent at once'):format(sent) or ('not sent at once: ' .. tostring(why))))
end

-- back to the original lanes (a further walker comes, or the lanes are widened)
function FIRE.half.restore(s)
	if not s.half_closed or not s.lanes_full then return end -- (never deferred, like a widening)
	s.lanes, s.lanes_full, s.half_closed, s.half_off = s.lanes_full, nil, nil, true -- (not again on this opening)
	local sent, why = FIRE.half.send(s, s.lanes)
	log(('HALF CLOSE %s: both lanes open again | %s'):format(s.label, sent > 0 and ('%d models sent at once'):format(sent) or ('not sent at once: ' .. tostring(why))))
end

-- does walker w come for standing unit s (the ONE test serve picks its candidates by AND the lane for the line of fire asks: run 52's
-- OPEN / CLOSE 'a walker needs it' after 0.0 s was two tests that differed)? Returns how far ahead of w the middle of s is, or nil
function FIRE.candidate(s, w)
	if w == s or w.gone or not w.walks or w.stands or w.ethereal or w.alliance ~= s.alliance or not w.dx then return nil end
	if s.late and s.late[w] then return nil end
	if s.quick_gone and s.quick_gone[w] and FIRE.quick.still_gone(s, w) then return nil end
	return passing(w, s)
end

-- (085d, D-c) a further walker through the open lanes of s gets columns of its own once the columns of the first walker are over: they ended
-- (the first walker stopped or was ordered elsewhere: look() sets s.cols_next) or the first walker is through (no body inside s, s behind it: its
-- columns go off here). The columns start at the next look through s.columns_wait (serve's retry, 'COLUMNS LATE'). Not for lanes per file (vector),
-- not for the lane of the line of fire.
function FIRE.columns_next(s, cands)
	local lanes = s.lanes
	if not lanes or lanes.vector or s.fire or s.columns_wait or s.half_closed then return end
	local w1 = s.columns_of
	local nxt = nil
	s.cols_done = s.cols_done or {} -- (the walkers whose columns in these lanes are over: they are through, whatever passing() says while they leave)
	for _, c in ipairs(cands) do
		if c.w ~= w1 and not c.w.columns_for and c.w.walks and not s.cols_done[c.w] and (not nxt or c.along < nxt.along) then nxt = c end
	end
	if not nxt then return end
	if w1 and not s.cols_next and w1.walks and not w1.gone then
		local ok, inside = pcall(still_inside, s, w1)
		if ok and not inside and (s.x - w1.x) * w1.dx + (s.z - w1.z) * w1.dz < 0 then
			stop_columns(s)
			s.cols_done[w1] = true
			log(('  COLUMNS %s: the columns of %s are over (it is through), %s is next'):format(s.label, w1.label, nxt.w.label))
			s.cols_next = true
		end
	end
	if s.cols_next and not s.columns_of then
		s.cols_next = nil
		s.cols_done[nxt.w] = true
		s.columns_wait = { w = nxt.w, radius = model_radius(nxt.w) }
	end
end

-- 085e (A2 / B1): BIG WALKERS THAT REALLY PASS. A walker (of any army: the AI's too) whose files cannot be squeezed into the standing unit's lanes (columns would
-- pile them into the blocks, run 53) or that passes a stander in guard mode is re-ordered NARROW: the same goal, bearing and run state, a width of
-- TUNE.NARROW_FILES files. The game forms the deep column with its own collision-free ranks; the standing unit opens ONE lane for it (no cave 4 columns,
-- WALKERS is never raised for it). The call is create_unitcontroller(unit):goto_location_angle_width(v(x, z), bearing, width, run) (the scenario's
-- own); take_control / release_control are never called. When the walker is through (or the pass ends otherwise) the saved order comes back. A
-- different order (the player's) forgets the narrowing: nothing is restored over it. State: w.narrow = { standers = { [s] = true }, orig = { x, z, w, b },
-- sent_w, read_w, lane, files, run, hold, own = { x, z, w, b } (what the mod wrote, compared at the next look), fresh, ... }.

-- a plain move order only: no attack order, no fight, no enemy near the walker or its goal. Returns nil, or why not
function NARROW.plain(w, gx, gz) -- (gx, gz: another goal point to test than the walker's own: the extended goal of NARROW.plan)
	gx, gz = gx or w.gx, gz or w.gz
	if w.busy then return 'it fights or routs' end
	local ok, target = pcall(function() return w.unit:current_target() end)
	if ok and (type(target) == 'table' or type(target) == 'userdata') then return 'it has an enemy target (an attack order)' end
	if FIRE.enemy_near(w, TUNE.FIRE_ENEMY_NEAR, TUNE.FIRE_ENEMY_SECONDS) then return 'an enemy is near it' end
	for _, e in ipairs(state.units) do
		if not e.gone and e.alliance ~= w.alliance and e.x and e.men >= 1 and (e.x - gx) ^ 2 + (e.z - gz) ^ 2 <= (TUNE.FIRE_ENEMY_NEAR + (e.half or 0)) ^ 2 then return 'an enemy is near its goal' end
	end
	return nil
end

-- the run state, checked live (the 2043 rule of timing(): fast move, or a measured speed over 1.3 x the walking speed - only once it moved), or nil: unknown
function NARROW.live_run(w)
	if yes(w.unit, 'is_moving_fast') then return true end
	if w.moving and (w.samples or 0) >= 1 and w.measured then
		local slow = w.unit:slow_speed()
		if type(slow) == 'number' and slow > 0.3 then return w.measured > 1.3 * slow end
	end
	return nil
end

-- the lanes of standing unit s for the narrowed walker w (called by plan_lanes where the columns would not fit): ONE lane centred on its way, as wide as the
-- column the game forms (front + 2 x LANE_EXTRA_BIG), bounded by the room beside the standing unit (guard mode: GUARD_OUT + closing up). Frozen once the
-- walker is narrowed (formation() would read the walker while it re-forms). Returns the lanes, or nil and why (nil: nothing to say)
function NARROW.plan(s, w, radius, files, spacing, cx, cz, ax, az)
	if not TUNE.NARROW or type(create_unitcontroller) ~= 'function' or type(v) ~= 'function' then return nil, nil end
	local nar = w.narrow
	local function lanes_of(lane, order_w, nf, pass_m, sp)
		local pl = { cx - ax * 50000, cz - az * 50000, ax, az, 100000, lane, 1, -0.5 }
		pl.narrow = { lane = lane, order_w = order_w, files = nf, pass_m = pass_m, spacing = sp }
		return pl
	end
	if nar and not nar.restoring then return lanes_of(nar.lane, nar.sent_w, nar.files, nar.pass_m, nar.spacing) end
	if w.narrowed_order == w.order_at then return nil, 'this order was narrowed already' end
	if w.nfg and w.nfg[s] and w.nfg[s].limit then return nil, 'too many orders of someone else in this pass: the normal columns until it ends' end
	if w.narrow_cool_until and state.game_seconds < w.narrow_cool_until then return nil, 'its narrowing was forgotten a moment ago (another order): the normal way for now' end
	if w.narrow_failed then return nil, 'the game did not take a narrowed order of it before' end
	if not (files and spacing and files > TUNE.NARROW_FILES) then return nil, 'its files are not known, or it has no more than ' .. TUNE.NARROW_FILES end
	local why = NARROW.plain(w)
	if why then return nil, why end
	local places = FIRE.places(w)
	local nb = 0
	for _ in pairs(places) do nb = nb + 1 end
	if nb < 2 then return nil, 'its bodies are not readable' end
	-- 085f (F2): the ORIGINAL (wide) order must clear the standing unit (the unit's MIDDLE beyond its back rank + the wide depth / 2); the narrowed column need
	-- not fit before the goal: its order then goes on along the heading (an extended goal) and the width comes back at TUNE.CLOSE_PASSED_FRAC through
	local back = nil
	for _, p in pairs(near_places(s)) do
		local t = (p[1] - w.x) * w.dx + (p[3] - w.z) * w.dz
		if not back or t > back then back = t end
	end
	if not back then return nil, 'the standing unit is not readable' end
	local goal_t = (w.gx - w.x) * w.dx + (w.gz - w.z) * w.dz
	local wide_depth = (math.ceil(nb / files) - 1) * spacing + 2 * radius
	local wide_m = back + wide_depth / 2 + model_radius(s) + radius + TUNE.NARROW_GOAL
	if goal_t < wide_m then
		return nil, ('its order ends %.1f m ahead, its own %d files (%.1f m deep) need %.1f m to be through'):format(goal_t, files, wide_depth, wide_m)
	end
	s.guard_now = FIRE.guard_of(s) -- (make_room reads it)
	local nfs = {}
	-- 085j (user rule): a cavalry walker of more than NARROW_CAV_MIN_MODELS bodies is never narrowed below NARROW_CAV_MIN_FILES files (no 2, 3, 1); LANE_MAX does not apply, the room test decides
	local cav_rule = (w.kind == 'cav' or w.kind == 'mc') and nb > TUNE.NARROW_CAV_MIN_MODELS
	if cav_rule then
		if files <= TUNE.NARROW_CAV_MIN_FILES then return nil, ('its %d files are not more than the %d files of the cavalry rule'):format(files, TUNE.NARROW_CAV_MIN_FILES) end
		nfs[1] = TUNE.NARROW_CAV_MIN_FILES
	else
		for nf = TUNE.NARROW_FILES, TUNE.NARROW_FILES_MAX do
			if nf < files then nfs[#nfs + 1] = nf end
		end
		nfs[#nfs + 1] = 1
	end
	local failed = nil -- (the narrowest file count that did not fit the room: wider ones cannot, 085f review 6)
	local first = nil -- (the first column that fits the room; it takes the extended goal when none clears the goal)
	for _, nf in ipairs(nfs) do
		-- 085g: the width SENT is nf x spacing + a pad (the game made one file of (nf - 1) x spacing, run 55)
		local order_w = nf == 1 and math.max(0, 2 * radius) or math.max(nf * spacing + TUNE.NARROW_WIDTH_PAD * spacing, 2 * radius) -- (one file: no pad, it would make two)
		local col_w = order_w + 2 * radius + TUNE.LANE_EXTRA_BIG -- 085h: the lane is planned from the width SENT (the game's read-back is never wider, run 56)
		if (col_w <= LANE_MAX or nf == 1 or cav_rule) and not (failed and nf > failed) then
			local lane = math.max(LANE_MIN, cav_rule and col_w or math.min(LANE_MAX, col_w)) -- (right for both meanings of the ordered width: between centres, or outer edges)
			local depth = (math.ceil(nb / nf) - 1) * spacing + 2 * radius
			local pass_m = back + depth / 2 + model_radius(s) + radius
			local pl = lanes_of(lane, order_w, nf, pass_m, spacing)
			local rok = pcall(make_room, s, w, pl, nil, true)
			local fits = rok and (pl.over or 0) <= 0.05 and not (pl.narrow_to and pl.narrow_to < lane - 0.05)
			if fits then
				pl[9], pl[10] = 0, 0 -- (the room is worked out fresh at the opening)
				if goal_t >= pass_m + TUNE.NARROW_GOAL then return pl end
				if not first then
					pl.narrow.extra = pass_m + TUNE.NARROW_GOAL - goal_t -- (m the narrowed order goes on beyond the goal)
					first = pl
				end
			else
				failed = failed or nf
			end
		end
	end
	if first then
		local extra = first.narrow.extra
		local why = NARROW.plain(w, w.gx + w.dx * extra, w.gz + w.dz * extra)
		if why then return nil, why end
		for _, o in ipairs(state.units) do -- 085f review 3: the extended goal must not lead the column into another friendly unit
			if o ~= s and o ~= w and not o.gone and o.alliance == w.alliance and o.x and o.half then
				local ex, ez = o.x - w.gx, o.z - w.gz
				local u = math.max(0, math.min(extra, ex * w.dx + ez * w.dz))
				if (ex - w.dx * u) ^ 2 + (ez - w.dz * u) ^ 2 < (o.half + (w.half or 0)) ^ 2 then return nil, 'a friendly unit stands beyond its goal: ' .. o.label end
			end
		end
		return first
	end
	if cav_rule then return nil, ('a column of %d files (cavalry rule) does not fit the room beside the standing unit'):format(TUNE.NARROW_CAV_MIN_FILES) end
	return nil, 'not even a column one file wide fits the room beside the standing unit'
end

-- 085h: a walker the narrow lane of s was opened for that is not narrowed any more (forgotten, the AI's own order, the game did not take it, restored): it drives wide into
-- the ONE narrow lane, which has to grow (widen_plan / try_widen)
function NARROW.stuck(s, w)
	return s.lanes ~= nil and s.lanes.narrow ~= nil and s.lane > 0 and s.walker_seen == w and s.served ~= nil and s.served[w] == true and (w.narrow == nil or w.narrow.restoring == true)
end

-- 085h: is walker w still in, or about to enter, one of the standing units its narrowing was for (those it still waits for and those it left)? From the CURRENT models of
-- the standing unit (not passed_frac / s.stood, run 56: 'through' one second after NARROWED): the rearmost body is not beyond the unit's back rank + radii + margin and
-- its bodies overlap the unit across the way. Returns whether, and the unit
function NARROW.inside(w)
	local nar = w.narrow
	if not nar or w.gone or not w.dx then return false end
	local bodies = bodies_of(w)
	if #bodies == 0 then return false end
	local r, ax, az = model_radius(w), -w.dz, w.dx
	local rear, lo, hi = nil, nil, nil
	for _, b in ipairs(bodies) do
		local t, a = b[1] * w.dx + b[2] * w.dz, b[1] * ax + b[2] * az
		if not rear or t < rear then rear = t end
		if not lo or a < lo then lo = a end
		if not hi or a > hi then hi = a end
	end
	local list = {}
	for s in pairs(nar.standers or {}) do list[#list + 1] = s end
	for s in pairs(nar.left or {}) do list[#list + 1] = s end
	for _, s in ipairs(list) do
		if not s.gone then
			local back, smin, smax = nil, nil, nil
			for _, p in pairs(near_places(s)) do
				local t, a = p[1] * w.dx + p[3] * w.dz, p[1] * ax + p[3] * az
				if not back or t > back then back = t end
				if not smin or a < smin then smin = a end
				if not smax or a > smax then smax = a end
			end
			local reach = model_radius(s)
			if back and rear - r <= back + reach + CLEAR_MARGIN and hi + r > smin - reach and lo - r < smax + reach then return true, s end
		end
	end
	return false
end

function NARROW.uc(w)
	if not w.uc then w.uc = create_unitcontroller(w.unit) end
	return w.uc
end

-- writes an order: the saved x / z / bearing with the width and run state given; what the mod wrote is noted for the next look (look -> NARROW.observe).
-- nowait: the game may show the same width as before (a re-send for more files): no "the game did not take it" check
function NARROW.send(w, nar, x, z, width, b, run, nowait)
	local uc = NARROW.uc(w)
	nar.before_w = w.gw
	nar.prev = { x = w.gx, z = w.gz }
	uc:goto_location_angle_width(v(x, z), b, width, run == true)
	nar.own = { x = x, z = z, w = width, sw = width, sx = x, sz = z, b = b } -- (x, z, w: the last values accepted; sw, sx, sz: what was sent)
	nar.fresh, nar.waited, nar.nowait = true, 0, nowait
	nar.settle_until = state.game_seconds + TUNE.NARROW_SETTLE
end

-- look() asks, for a walker with a narrowing, whether the order it reads now is the one the mod wrote. Returns whether it is the same order as at the
-- last look. 085g: while the game settles (TUNE.NARROW_SETTLE s after each order the mod wrote) its goal may drift within TUNE.NARROW_GOAL_SLACK of what was
-- sent or of the first read-back, and the width may be anything between the sent and the read one; later the goal is held to 0.5 m of the last accepted
-- one. A width between the sent and the read one is the mod's own at any time (a goal of the mod's and a narrow width are never foreign); the last
-- accepted values are followed. Foreign: another goal, another bearing (2 deg) or a width outside that range (the player's / the AI's own, usually the wide one)
function NARROW.observe(w, same)
	local nar = w.narrow
	local own = nar.own
	if not own then return same end
	local settling = state.game_seconds <= (nar.settle_until or -1)
	local tol = ((nar.ext and nar.fresh) or settling) and TUNE.NARROW_GOAL_SLACK or 0.5 -- (085f review 5: the game may move an extended goal a little)
	local function near_at(x, z) return x ~= nil and math.abs(w.gx - x) < tol and math.abs(w.gz - z) < tol end
	local turned = turn(w.gb, own.b)
	local near = (near_at(own.x, own.z) or near_at(own.sx, own.sz) or near_at(nar.rx, nar.rz)) and turned < 2
	if not near and nar.fresh and nar.prev and math.abs(w.gx - nar.prev.x) < 0.5 and math.abs(w.gz - nar.prev.z) < 0.5 and turned < 2 then
		near = true -- (085f: the game has not taken the order yet: it still shows the one before, as for the width below)
	end
	local lo, hi = math.min(own.w, own.sw), math.max(own.w, own.sw)
	if nar.read_w then lo, hi = math.min(lo, nar.read_w), math.max(hi, nar.read_w) end
	local width_ok = w.gw > lo - 0.5 and w.gw < hi + 0.5
	local function lost() -- the order is not the mod's: the values (for the FORGOTTEN line)
		local d = nil
		for _, p in ipairs({ { own.x, own.z }, { own.sx, own.sz }, { nar.rx, nar.rz } }) do
			if p[1] then
				local dd = math.sqrt((w.gx - p[1]) ^ 2 + (w.gz - p[2]) ^ 2)
				if not d or dd < d then d = dd end
			end
		end
		if nar.restoring then
			log(('  NARROW %s: the restored order is not tracked any more (nothing is forgotten, nothing is restored): goal %.1f m (slack %.1f), bearing %.1f deg, width %.1f (saved %.1f)'):format(w.label, d or 0, tol, turned, w.gw, own.sw))
			w.narrow = nil
			return
		end
		NARROW.forget(w, 'it was given another order', ('goal %.1f m (slack %.1f), bearing %.1f deg, width %.1f (own w %.1f, sent %.1f, read %.1f)'):format(d or 0, tol, turned, w.gw, own.w, own.sw, nar.read_w or 0))
	end
	if nar.fresh then
		if not near then
			lost()
			return same
		end
		if not nar.nowait and math.abs(w.gw - nar.before_w) < 0.5 and math.abs(own.w - nar.before_w) > 0.5 then -- the game has not taken it yet
			nar.waited = nar.waited + 1
			if nar.waited >= TUNE.NARROW_WAIT then
				NARROW.stats.gave_up = NARROW.stats.gave_up + 1
				if not nar.restoring then
					log(('NARROW GIVEN UP %s: the game did not take the order in %d looks'):format(w.label, nar.waited))
					w.narrow_failed = true -- (not again for this walker: a late order would be taken for its own)
					NARROW.restore(w, 'the game did not take the narrowed width')
					return true
				end
				w.narrow = nil
				return same
			end
			return true
		end
		nar.fresh, nar.nowait, own.w = false, nil, w.gw
		nar.rx, nar.rz = w.gx, w.gz -- (the first read-back goal: within the settling window it counts as the mod's too)
		if nar.ext then own.x, own.z = w.gx, w.gz end
		if nar.restoring then
			w.narrow = nil -- the saved order is back: nothing more to watch
		else
			nar.read_w, nar.read_new = w.gw, true
		end
		return true
	end
	if near and width_ok then
		own.x, own.z, own.w = w.gx, w.gz, w.gw -- (the drift is followed)
		return true
	end
	lost()
	return same
end

-- the walker has another order than the mod's: the narrowing is forgotten, nothing is restored
function NARROW.forget(w, why, detail)
	NARROW.stats.forgotten = NARROW.stats.forgotten + 1
	w.narrow_cool_until = state.game_seconds + TUNE.NARROW_COOLDOWN -- (the AI re-orders every few seconds: no narrowing again at once, no loop)
	log(('NARROW FORGOTTEN %s (%s): %s | the new order stays, nothing is restored; it is planned the normal way (columns) for %.0f s'):format(w.label, why, detail or '-', TUNE.NARROW_COOLDOWN))
	-- the forgets of this pass, per standing unit (w.nfg[s] = { n, limit, gone }): at TUNE.NARROW_FORGETS_MAX no narrowing for that unit until the pass ends (NARROW.sweep)
	w.nfg = w.nfg or {}
	for s in pairs(w.narrow.standers or {}) do
		local c = w.nfg[s] or { n = 0, gone = 0 }
		w.nfg[s] = c
		c.n = c.n + 1
		if c.n >= TUNE.NARROW_FORGETS_MAX and not c.limit then
			c.limit = true
			log(('NARROW LIMIT %s for %s: %d orders of someone else - normal columns until this pass ends'):format(w.label, s.label, c.n))
		end
	end
	w.narrow = nil
end

-- the saved order comes back (width, goal, bearing, and the run state seen last)
function NARROW.restore(w, why)
	local nar = w.narrow
	if not nar or nar.restoring then return end
	local o = nar.orig
	local ok, problem = pcall(NARROW.send, w, nar, o.x, o.z, o.w, o.b, nar.run)
	if not ok then
		log(('NARROW RESTORE FAILED %s: %s'):format(w.label, tostring(problem)))
		w.narrow = nil
		return
	end
	NARROW.stats.restored = NARROW.stats.restored + 1
	log(('RESTORED %s (width %.1f m, %s): %s'):format(w.label, o.w, nar.run and 'running' or 'walking', why))
	w.narrow = { restoring = true, own = nar.own, fresh = true, waited = 0, before_w = nar.before_w, prev = nar.prev, settle_until = nar.settle_until, standers = {} }
end

-- the forget counters of a walker end with the pass through their standing unit (not waiting for it any more for TUNE.NARROW_GONE looks, or through it)
function NARROW.sweep(w)
	for s, c in pairs(w.nfg) do
		local st = NARROW.pair(s, w)
		if st == 'through' then
			w.nfg[s] = nil
		elseif st == 'wait' then
			c.gone = 0
		else
			c.gone = c.gone + 1
			if c.gone >= TUNE.NARROW_GONE then w.nfg[s] = nil end
		end
	end
	if next(w.nfg) == nil then w.nfg = nil end
end

function NARROW.restore_all(why)
	for _, w in ipairs(state.units) do w.nfg = nil end
	for _, w in ipairs(state.units) do
		if w.narrow and not w.narrow.restoring then pcall(NARROW.restore, w, why) end
	end
end

-- an error in the narrowing of a walker: logged, its order comes back
function NARROW.fail(w, problem)
	log(('NARROW ERROR %s: %s; its order comes back'):format(w.label, tostring(problem)))
	w.narrow_failed = true -- (not again for this walker: no error loop)
	if w.narrow and not w.narrow.restoring then pcall(NARROW.restore, w, 'an error in the script') else w.narrow = nil end
end

-- the lane grows to hold the column the game really formed (read back): the standing unit's lanes widen from where they are; false (and the walker
-- gets its order back) when the room beside the standing unit does not allow it
function NARROW.widen(s, w, need)
	local lanes = s.lanes
	local cur = step_lanes(s, s.step or s.steps or 1)
	local new = {}
	for i, val in pairs(lanes) do new[i] = val end
	new[6], new[8] = need, -0.5
	new.narrow = { lane = need, order_w = lanes.narrow.order_w, files = lanes.narrow.files, pass_m = lanes.narrow.pass_m, spacing = lanes.narrow.spacing }
	new.from = { -cur[8] * cur[6], (1 + cur[8]) * cur[6], cur[9] or 0 }
	new.shift = math.max(0.5, need / 2 - new.from[1], need / 2 - new.from[2])
	s.guard_now = FIRE.guard_of(s) and not s.guard_dropped -- (085f: the guard limit dropped at the opening stays dropped)
	local rok = pcall(make_room, s, w, new, s.stood)
	if not rok then new[9], new[10] = 0, 50000 end
	if (new.over or 0) > 0.05 or (new.narrow_to and new.narrow_to < need - 0.05) then
		return false, ('a lane of %.1f m does not fit beside %s'):format(need, s.label)
	end
	local _, _, _, _, _, steps, step_time = timing(s, w, new)
	s.lanes, s.lane = new, need
	s.steps, s.step, s.step_time, s.shift = steps or 1, 0, step_time or 0, new.shift
	log(('NARROW WIDER %s: the lane grows to %.1f m for %s (the game formed the column %.1f m wide)'):format(s.label, need, w.label, need - 2 * model_radius(w) - TUNE.LANE_EXTRA_BIG))
	next_step(s)
	return true
end

-- would a lane of `need` m for walker w fit beside standing unit s that has not opened yet (the room beside it, guard mode included)?
function NARROW.fits(s, w, need)
	local ax, az = -w.dz, w.dx
	local bx, bz = w.x, w.z
	if w.cm_tick == state.ticks and w.cx then bx, bz = w.cx, w.cz end
	local al = (s.x - bx) * w.dx + (s.z - bz) * w.dz
	local cx, cz = bx + w.dx * al, bz + w.dz * al
	local pl = { cx - ax * 50000, cz - az * 50000, ax, az, 100000, need, 1, -0.5 }
	s.guard_now = FIRE.guard_of(s) and not s.guard_dropped
	local rok = pcall(make_room, s, w, pl, nil, true)
	if not rok or (pl.over or 0) > 0.05 or (pl.narrow_to and pl.narrow_to < need - 0.05) then
		return false, ('a lane of %.1f m does not fit beside %s'):format(need, s.label)
	end
	return true
end

-- the game made the column wider than ordered (clamped): the lane follows it, as far as it fits: the widest of `need` and half way to it (<= 2 make_room per standing
-- unit, LANE_MAX at most). 085h: never gives up - what does not fit stays narrow ('NARROW TOO WIDE?'); a wide order into a one-lane gap would be the worst of all
function NARROW.fit(w, nar, need)
	local old, best, why_last, want = nar.lane, nar.lane, nil, need
	need = math.min(need, nar.lane > LANE_MAX and LANE_WIDE_MAX or LANE_MAX) -- (085j: a 4-file cavalry lane is wider than LANE_MAX and may grow up to LANE_WIDE_MAX)
	for s in pairs(nar.standers) do
		local tries = {}
		if need > old + 0.05 then tries[#tries + 1] = need end
		if need - old > 0.1 then tries[#tries + 1] = (old + need) / 2 end
		for _, try in ipairs(tries) do
			local ok, fits, why = nil, nil, nil
			if s.lane > 0 and s.lanes and s.lanes.narrow and not s.closing and s.served and s.served[w] then
				ok, fits, why = pcall(NARROW.widen, s, w, try)
			elseif s.lane == 0 and not s.gone then
				ok, fits, why = pcall(NARROW.fits, s, w, try)
			end
			if ok == nil then break end
			if ok and fits then
				best = math.max(best, try)
				why_last = nil
				break
			end
			why_last = ok and tostring(why) or tostring(fits)
		end
	end
	nar.lane = best
	if best < need - 0.05 then
		log(('NARROW TOO WIDE? %s: needs %.1f m, lane %.1f m - kept narrow%s'):format(w.label, want, best, why_last and (' (' .. why_last .. ')') or ''))
	end
	return true
end

-- the narrowing starts at the FIRST look that plans the pair (the walker re-forms while it approaches): s is the standing unit, lanes the narrow plan
-- (primary of open_for), t_contact / t_split its timing (seconds). B1: a running walker whose contact comes before the split is done (plus the opening
-- margin) is ordered to WALK; the hold ends when the split time is over, at the latest after TUNE.HOLD_MAX seconds of extra time compared with running
-- (or at once when the walker is seen running: the player / the game switched it), and the order is given again running
function NARROW.begin(s, w, lanes, t_contact, t_split)
	local nar = w.narrow
	if nar then
		if not nar.restoring and not nar.standers[s] then
			nar.standers[s] = true
			log(('  NARROW %s: the narrowed column goes through %s too'):format(w.label, s.label))
		end
		return
	end
	-- 085e review 2: a new order is not yet known to be a run or a walk (the measured speed lags, is_moving_fast follows the order): for START_TICKS after it the
	-- walker is narrowed only when it is seen RUNNING; later the 2043 rule decides (it walks if it does not run)
	if state.ticks - (w.order_at or -1000) < TUNE.START_TICKS and NARROW.live_run(w) ~= true then return false end
	local info = lanes.narrow
	local o = { x = w.gx, z = w.gz, w = w.gw, b = w.gb }
	local run = NARROW.live_run(w) == true
	nar = { standers = { [s] = true }, gone = {}, orig = o, run = run, sent_w = info.order_w, lane = info.lane, files = info.files, pass_m = info.pass_m, spacing = info.spacing, idle = 0 }
	-- 085f: the narrowed order may go on beyond the goal (info.extra m along the heading, F2); the restore gives the original goal back
	nar.ext = info.extra ~= nil
	nar.sx, nar.sz = o.x + (w.dx or 0) * (info.extra or 0), o.z + (w.dz or 0) * (info.extra or 0)
	local speed = math.max(w.speed or 0, 1.5)
	nar.expires = state.game_seconds + math.max(TUNE.NARROW_MIN_S, TUNE.NARROW_MAX * (info.pass_m or 0) / speed)
	local hold = false
	if run and t_contact and t_split and t_split > 0.3 and t_contact < t_split + FIRE.open_margin(w) then
		local slow, fast = w.unit:slow_speed(), w.unit:fast_speed()
		local cap = TUNE.HOLD_MAX * 3
		if type(slow) == 'number' and type(fast) == 'number' and slow > 0.3 and fast > slow then cap = TUNE.HOLD_MAX / (1 - slow / fast) end
		nar.hold, nar.hold_until = true, state.game_seconds + math.min(t_split, cap)
		hold = true
	end
	w.narrow, w.narrowed_order = nar, w.order_at
	local ok, problem = pcall(NARROW.send, w, nar, nar.sx, nar.sz, nar.sent_w, o.b, run and not hold)
	if not ok then
		w.narrow = nil
		error(problem, 0)
	end
	NARROW.stats.narrowed = NARROW.stats.narrowed + 1
	log(('NARROWED %s from %.1f to %.1f m for %s: %d files wide, lane %.1f m | %s%s'):format(w.label, o.w, nar.sent_w, s.label, info.files, info.lane,
		(info.extra and ('its goal moved %.1f m on along the heading (the wide order does not clear the unit with the column; the width comes back at %.0f %% through) | '):format(info.extra, TUNE.CLOSE_PASSED_FRAC * 100) or '') .. (run and 'it runs' or 'it walks'), hold and (' - held at a walk for at most %.1f s (contact in %.1f s, the split takes %.1f s)'):format(nar.hold_until - state.game_seconds, t_contact, t_split) or ''))
end

-- (log) the run state changed: said once per pass
function NARROW.note_run(w, nar, text)
	if nar.run_said then return end
	nar.run_said = true
	log(('  NARROW %s: %s (the run state is checked at every look; the order is given again with the latest)'):format(w.label, text))
end

-- what became of the pair (walker w, standing unit s): 'wait', 'through' (its rearmost body is past the back rank) or 'ended'
function NARROW.pair(s, w)
	if s.gone or w.gone then return 'ended' end
	if s.late and s.late[w] then return 'ended' end
	if s.lane > 0 and s.served and s.served[w] then
		local ok, frac = pcall(FIRE.passed_frac, s, w) -- (085f: through at CLOSE_PASSED_FRAC of its bodies, not the rearmost; 085h: all of them for a big walker)
		return (ok and frac >= FIRE.close_frac(w) - 1e-9) and 'through' or 'wait'
	end
	if s.stands and FIRE.candidate(s, w) then return 'wait' end
	return 'ended'
end

-- 085h: the walker's bodies across its way: the sorted positions, their extent (10 % of the bodies trimmed at each side: a lagging body is no extent) and the
-- places without those bodies (for formation())
function NARROW.across(w)
	local ax, az = -w.dz, w.dx
	local list = {}
	for _, p in pairs(FIRE.places(w)) do list[#list + 1] = { p[1] * ax + p[3] * az, p } end
	if #list < 2 then return nil end
	table.sort(list, function(a, b) return a[1] < b[1] end)
	local k = math.floor(0.1 * #list)
	if #list - k <= k + 1 then k = 0 end
	local across, kept = {}, {}
	for i, e in ipairs(list) do
		across[i] = e[1]
		if i > k and i <= #list - k then kept[#kept + 1] = e[2] end
	end
	return across, across[#across - k] - across[k + 1], kept
end

-- 085g / 085h: the files the walker really formed, and the across extent of the bodies (log only). The files from formation() on the bodies; the fallback clusters the
-- positions across the way with a gap from the body size and the expected file spacing (read width / files wanted), not from the old spacing (run 56).
-- Returns files, extent; nil when the bodies are not readable
function NARROW.measure(w, nar)
	local list, extent, kept = NARROW.across(w)
	if not list then return nil end
	local fok, f = pcall(formation, kept, w.dx, w.dz, #kept)
	if fok and f then return f, extent end
	local r = model_radius(w)
	local sp = (nar.read_w or nar.sent_w or 0) / math.max(1, nar.files - 1)
	local gap = math.min(2 * r, math.max(r + 0.3, TUNE.NARROW_FILE_GAP * sp))
	local cl = { { lo = list[1], hi = list[1], n = 1 } } -- (the files: bodies less than `gap` apart across the way)
	for i = 2, #list do
		local c = cl[#cl]
		if list[i] - list[i - 1] >= gap then
			cl[#cl + 1] = { lo = list[i], hi = list[i], n = 1 }
		else
			c.hi, c.n = list[i], c.n + 1
		end
	end
	-- (085g review 4: a lagging body beside the column is no file: edge clusters with fewer bodies than a quarter of a file are dropped; a column wider than wanted has its files counted as they are)
	local least = math.max(2, math.ceil(0.25 * #list / math.max(nar.files or 1, #cl)))
	local a, z = 1, #cl
	while a <= z and cl[a].n < least do a = a + 1 end
	while z >= a and cl[z].n < least do z = z - 1 end
	if a > z then return nil end
	return z - a + 1, extent
end

-- 085h: one sample of the across extent per look while the count waits (<= 8 kept); steady when the extent changed less than NARROW_STEADY_FRAC (+ 0.2 m) over the last
-- NARROW_STEADY_S seconds (the column has re-formed)
function NARROW.sample(w, nar, now)
	local _, extent = NARROW.across(w)
	if not extent then return end
	nar.hist = nar.hist or {}
	nar.hist[#nar.hist + 1] = { t = now, e = extent }
	if #nar.hist > 8 then table.remove(nar.hist, 1) end
end

function NARROW.steady(nar, now)
	local h, i0 = nar.hist or {}, nil
	for i, p in ipairs(h) do
		if p.t <= now - TUNE.NARROW_STEADY_S + 1e-6 then i0 = i end
	end
	if not i0 then return false end
	local lo, hi = h[i0].e, h[i0].e
	for i = i0, #h do
		lo, hi = math.min(lo, h[i].e), math.max(hi, h[i].e)
	end
	return hi - lo < TUNE.NARROW_STEADY_FRAC * hi + 0.2
end

-- 085g / 085h: TUNE.NARROW_COUNT_S after the game showed the order, once the column is steady: the files wanted against the files got. Fewer: the order is sent ONCE more,
-- one spacing wider - not for a big walker (a wider column after contact, run 56). Never gives up (the lane follows the READ width, NARROW.fit, and stays); never steady
-- within NARROW_COUNT_MAX: no count. Returns true
function NARROW.count(w, nar, now)
	local spacing = nar.spacing or 1
	if not NARROW.steady(nar, now) then
		if now >= nar.count_until then
			nar.counted, nar.count_at = true, nil
			log(('  NARROW NOT SETTLED %s: its bodies were still re-forming %.1f s after the game showed the order (across extent %.1f m): not counted'):format(w.label, TUNE.NARROW_COUNT_MAX, (nar.hist and #nar.hist > 0) and nar.hist[#nar.hist].e or 0))
		end
		return true
	end
	local files, extent = NARROW.measure(w, nar)
	if files and files > nar.files and now < nar.count_until then return true end -- (085g review 3: re-forming from wide to narrow passes through MORE files: the next look)
	nar.count_at = nil
	if not files then
		nar.counted = true
		return true
	end
	if files < nar.files and not nar.resent and model_radius(w) < TUNE.BIG_RADIUS then
		local w2 = nar.sent_w + spacing
		nar.resent = true
		log(('  NARROW FILES %s: wanted %d, got %d (sent %.1f m, read %.1f m) -> sent %.1f m'):format(w.label, nar.files, files, nar.sent_w, nar.read_w or 0, w2))
		nar.sent_w = w2
		NARROW.send(w, nar, nar.sx, nar.sz, w2, nar.orig.b, nar.run == true and not nar.hold, true)
		return true -- (observe shows the read-back, which starts the second count)
	end
	nar.counted = true
	log(('  NARROW WIDTH %s: sent %.1f m read %.1f m, files wanted %d got %d%s, across extent %.1f m (lane %.1f m)'):format(w.label, nar.sent_w, nar.read_w or 0, nar.files, files, files < nar.files and ' (kept)' or '', extent, nar.lane))
	return true
end

-- once per look and narrowed walker (after the standing units): read-back, run state, hold, the pairs, the ways back
function NARROW.step(w)
	local nar = w.narrow
	if not nar or nar.restoring then return end
	local now = state.game_seconds
	if nar.read_new then -- the game showed the order: its files are counted TUNE.NARROW_COUNT_S later, once the column is steady (it re-forms)
		nar.read_new = nil
		nar.hist = nil
		if not nar.counted and not nar.count_at then nar.count_at, nar.count_until = now + TUNE.NARROW_COUNT_S, now + TUNE.NARROW_COUNT_MAX end
		-- 085h: the lane follows the READ width (a column wider than ordered: the game clamped it), never the measured extent
		local need = (nar.read_w or 0) + 2 * model_radius(w) + TUNE.LANE_EXTRA_BIG
		if need > nar.lane + 0.05 then NARROW.fit(w, nar, need) end
	end
	if nar.count_at and not nar.fresh then
		NARROW.sample(w, nar, now)
		if now >= nar.count_at then NARROW.count(w, nar, now) end
	end
	if nar.fresh then
		-- (the order was written this look or the last: the unit's run state is read once the game has shown the order back)
	elseif nar.hold then
		local fast = yes(w.unit, 'is_moving_fast')
		if not fast then nar.hold_walked = true end -- (running is believed only after the walker was seen walking once: is_moving_fast may lag the order)
		if fast and nar.hold_walked then -- the player / the game switched it to run: the hold ends, nothing is given again
			nar.hold, nar.run = nil, true
			NARROW.note_run(w, nar, 'seen running during the walk hold: the hold ends')
		elseif now >= nar.hold_until then
			nar.hold, nar.run = nil, true
			NARROW.send(w, nar, nar.sx, nar.sz, math.max(nar.sent_w, nar.read_w or 0), nar.orig.b, true, true) -- (085g: never narrower than what was asked for; nowait: the game may read the narrow width again, review 1)
			log(('  NARROW %s: the split is done, running again'):format(w.label))
		end
	else
		local live = NARROW.live_run(w)
		if live ~= nil and live ~= nar.run then
			nar.run = live
			NARROW.note_run(w, nar, live and 'it runs now' or 'it walks now')
		end
	end
	local remaining, ended, through_note = 0, nil, nil
	for s in pairs(nar.standers) do
		local st = NARROW.pair(s, w)
		if st == 'wait' then
			nar.gone[s] = 0
			remaining = remaining + 1
		elseif st == 'through' then
			nar.standers[s] = nil
			nar.left = nar.left or {}
			nar.left[s] = true
			ended = ended or 'through'
			-- 085h (run 56: 'through' one second after NARROWED): what the verdict rests on
			local pok, frac, _, total = pcall(FIRE.passed_frac, s, w)
			through_note = ('%s: %.0f %% of %d bodies beyond the back rank, its lanes stood %.1f s'):format(s.label, pok and frac * 100 or 0, pok and total or 0, s.opened_at and (state.ticks - s.opened_at) * TICK_MS / 1000 or 0)
		else
			nar.gone[s] = (nar.gone[s] or 0) + 1
			if nar.gone[s] >= TUNE.NARROW_GONE then
				nar.standers[s] = nil
				nar.left = nar.left or {}
				nar.left[s] = true
				ended = ended or 'ended'
			else
				remaining = remaining + 1
			end
		end
	end
	-- the wide order comes back for one of these reasons ...
	local why, at_goal = nil, false
	if remaining == 0 then
		why = ended == 'through' and ('%.0f %% of its bodies are past the last standing unit (%s)'):format(FIRE.close_frac(w) * 100, through_note or '-') or 'the pass ended (lanes closed, the standing unit moved, or the walker was left to the game)'
	elseif now >= nar.expires then
		why = 'time is up'
	else
		nar.idle = w.moving and 0 or nar.idle + 1
		if nar.idle >= TUNE.NARROW_IDLE then
			why = 'it stands (idle at its goal)'
			at_goal = (w.x - nar.orig.x) ^ 2 + (w.z - nar.orig.z) ^ 2 <= math.max(5, w.half or 0) ^ 2 -- (arrived: nothing to protect)
		end
	end
	if not why then
		nar.defer_at = nil
		return
	end
	-- ... but not while the walker is in, or about to enter, a standing unit it narrowed for (085h: a wide column into a one-lane gap is stuck), TUNE.NARROW_DEFER_MAX s at most
	if not at_goal then
		local iok, inside, which = pcall(NARROW.inside, w)
		if iok and inside and (not nar.defer_at or now - nar.defer_at < TUNE.NARROW_DEFER_MAX) then
			if not nar.defer_at then
				nar.defer_at = now
				log(('  NARROW %s: the wide order waits (%s) - it is still in / before %s; at most %.0f s'):format(w.label, why, which and which.label or '?', TUNE.NARROW_DEFER_MAX))
			end
			return
		end
		if nar.defer_at then why = why .. (' - after waiting %.1f s'):format(now - nar.defer_at) end
	end
	NARROW.restore(w, why)
end

-- the ghost list (cave 5): the Unit* of the ethereal units of this battle. Grow = the entries, then the count; shrink = the count first (the cave reads
-- at most `count` entries, never a 0 end). Refreshed at Deployed and every COLLECT_EVERY looks (summons, reinforcements); zeroed with switch_off
function GHOST.refresh()
	if GHOST.off or not TUNE.GHOST or not state.tbl then return end
	local list = {}
	if state.cfg.enabled then
		for _, e in ipairs(state.units) do
			if e.ethereal and not e.gone and e.pointer and #list < GHOST.MAX then list[#list + 1] = e end
		end
	end
	local old = GHOST.units or {}
	local same = #old == #list
	for i = 1, same and #list or 0 do
		if old[i] ~= list[i] then same = false end
	end
	if same then return end
	local function entries()
		for i, e in ipairs(list) do mr.write(state.tbl, GHOST.LIST + 8 * (i - 1), e.pointer) end
	end
	if #list >= #old then
		entries()
		mr.write(state.tbl, GHOST.COUNT, mr.uint32(#list))
	else
		mr.write(state.tbl, GHOST.COUNT, mr.uint32(#list))
		entries()
	end
	GHOST.units = list
	local names = {}
	for i, e in ipairs(list) do names[i] = e.label end
	log(('GHOST PASS: %d ethereal unit(s) go through friendly models: %s'):format(#list, #list > 0 and table.concat(names, ', ') or 'none'))
end

function GHOST.text()
	return GHOST.total >= 1000 and ('%.1f thousand'):format(GHOST.total / 1000) or tostring(GHOST.total)
end

-- every 5 s (report): the pairs the cave skipped since the last time, read and reset
function GHOST.report()
	if not GHOST.units or #GHOST.units == 0 then return end
	local n = mr.read_uint32(state.tbl, GHOST.SKIPS)
	if n > 0 then
		mr.write(state.tbl, GHOST.SKIPS, mr.uint32(0))
		GHOST.total = GHOST.total + n
		log(('GHOST: +%d ghost pairs passed in 5 s (%s in all)'):format(n, GHOST.text()))
	end
end

-- one standing unit, one look: its lanes open, grow, step, hold and close
local function serve(s)
	-- walkers too late for it stay left out until they are clear of it (further than when refused, + TUNE.LATE_CLEAR:
	-- past, or turned away), ordered elsewhere (their goal moved > SETTLED: the AI re-orders by a few metres all the time)
	-- while not overlapping it, or it stands elsewhere. Not on a flicker of w.walks, of passing() or of its own standing:
	-- run 43, the bloodcrushers' exclusion dropped while they were inside the ironsworn and the lanes opened round them
	-- (085c: one left out as already inside or by guard mode - late[7] 'inside' / 'nolanes' - also gets a fresh look on any new order: a new pass)
	for w, late in pairs(s.late or {}) do
		if w.gone or not (w.x and s.x and s.half and s.gx and w.gx and late[6]) then
			s.late[w] = nil
		else
			local dx, dz, near = w.x - s.x, w.z - s.z, s.half + (w.half or 0)
			local d2 = dx * dx + dz * dz
			local reordered = (w.gx - late[5]) ^ 2 + (w.gz - late[6]) ^ 2 > TUNE.SETTLED * TUNE.SETTLED
			if d2 > (math.max(near, late[4]) + TUNE.LATE_CLEAR) ^ 2 or (reordered and (d2 > near * near or late[7] == 'inside' or late[7] == 'nolanes'))
				or (s.gx - late[2]) ^ 2 + (s.gz - late[3]) ^ 2 > TUNE.SETTLED * TUNE.SETTLED then -- (its order: position() jitters)
				s.late[w] = nil
			end
		end
	end
	if s.lane > 0 and not s.stands then
		if not FIRE.busy_sending(s) then close_lanes(s, 'it no longer just stands') end
	elseif s.stands then
		local best, best_along = nil, nil
		local cands = {}
		diag.sw('cand') -- (test build: serve's time by sub-phase, see diag.sw)
		for _, w in ipairs(state.units) do
			local along = FIRE.candidate(s, w)
			if along then
				cands[#cands + 1] = { w = w, along = along }
				if not best_along or along < best_along then best, best_along = w, along end
			end
		end
		diag.sw('rest')
		s.seen_since = s.seen_since or {}
		local seen_now = {}
		for _, c in ipairs(cands) do
			seen_now[c.w] = true
			s.seen_since[c.w] = s.seen_since[c.w] or state.ticks
		end
		s.seen_last = s.seen_last or {}
		for w in pairs(seen_now) do s.seen_last[w] = state.ticks end
		for w in pairs(s.seen_since) do
			if not seen_now[w] and state.ticks - (s.seen_last[w] or 0) > TUNE.SEEN_KEEP then s.seen_since[w], s.seen_last[w] = nil, nil end
		end
		if s.lane > 0 and not s.fire then -- a walker near the open lanes goes through them too: the quick close waits for it
			diag.sw('checks')
			for _, c in ipairs(cands) do
				if FIRE.quick.soon(s, c.w, c.along) then
					s.near_walkers = s.near_walkers or {}
					s.near_walkers[c.w] = true
				end
			end
			diag.sw('rest')
		end
		s.through, s.through_text = nil, nil
		if s.lane > 0 and not s.fire and not s.closing then -- 085f (F1): the walkers are through at CLOSE_PASSED_FRAC: a few tangled models do not keep the lanes open
			diag.sw('inside')
			local tok, group, text = pcall(FIRE.through_ready, s, cands)
			diag.sw('rest')
			if tok then s.through, s.through_text = group, text end
		end
		if best and s.fire and s.lane > 0 then -- (the lane for the line of fire gives way at once; none opens again in s before that walker has passed)
			close_lanes(s, 'a walker needs it')
			s.fire_block = { w = best, at = state.ticks }
		end
		if best and s.lane == 0 then
			table.sort(cands, function(a, b) return a.along < b.along end)
			diag.sw('open')
			best = open_for(s, cands) -- nil: not yet, or no lanes
			diag.sw('rest')
		elseif best and s.closing then -- a walker comes while it closes up: open again, all at once
			local again, again_along = best, nil
			if s.closing.group then -- 085f: not for the walkers the closing began for (they are through; one that went back behind the unit counts as new), only for another that needs the lanes soon
				again = nil
				for _, c in ipairs(cands) do
					local pok, frac = true, 0
					if s.closing.group[c.w] then pok, frac = pcall(FIRE.passed_frac, s, c.w) end
					if (not s.closing.group[c.w] or (pok and frac < 0.5)) and (not again_along or c.along < again_along) and FIRE.quick.soon(s, c.w, c.along) then again, again_along = c.w, c.along end
				end
			end
			if again then
				log(('  %s opens again for %s while closing up'):format(s.label, again.label))
				if s.half_closed then pcall(FIRE.half.restore, s) end
				s.closing = nil
				s.step = s.steps - 1
				next_step(s, true) -- (its models are walking back: no pace to measure)
				s.served[again] = true -- (every walker that opened it again is served: a quick close waits for it too)
			end
		end
		if s.half_closed then -- a follower (a walker nobody planned for) wants the lanes: both are open again
			for _, c in ipairs(cands) do
				if not (s.half_group and s.half_group[c.w]) and s.half_closed and FIRE.quick.soon(s, c.w, c.along) then pcall(FIRE.half.restore, s) end
			end
		end
		if s.lane > 0 and s.widened_for and not s.closing then -- further walkers while the lanes are open
			diag.sw('widen')
			for _, w in ipairs(state.units) do
				if w ~= s and (w ~= s.walker_seen or NARROW.stuck(s, w)) and w.walks and not w.stands and not w.ethereal and w.alliance == s.alliance and not s.widened_for[w] and not (s.late and s.late[w]) and passing(w, s) then
					if s.half_closed and not (s.half_group and s.half_group[w]) and FIRE.quick.soon(s, w, passing(w, s)) then pcall(FIRE.half.restore, s) end
				local ok, why, final = pcall(try_widen, s, w)
					if not ok then
						log(('  WIDER %s for %s failed: %s'):format(s.label, w.label, tostring(why)))
						s.widened_for[w] = true
					else
						if why and why ~= 'widened' and s.widen_said[w] ~= why then -- each answer once in the log
							s.widen_said[w] = why
							log(('  further walker %s through %s: %s'):format(w.label, s.label, why))
						end
						if final then s.widened_for[w] = true end
					end
				end
			end
			diag.sw('rest')
		end
		if s.lane > 0 and not s.closing and not s.fire then
			diag.sw('checks')
			local qok, why, group = pcall(FIRE.quick.check, s, cands)
			diag.sw('rest')
			if qok and why then
				if start_closing(s, why, true) then -- (false: sends are used up this look, the next one tries again)
					s.quick_gone = {} -- (they are through: not planned for again on this order; set after the close, which clears it)
					for w in pairs(group) do s.quick_gone[w] = w.order_at or -1 end
				end
			end
		end
		if s.lane > 0 and not s.closing and not s.fire and not s.half_closed then
			diag.sw('checks')
			local hok, k, names, group = pcall(FIRE.half.check, s, cands)
			diag.sw('rest')
			if hok and k and not FIRE.busy_sending(s) then
				local cok, problem = pcall(FIRE.half.close, s, k, names, group)
				if not cok then log(('  HALF CLOSE %s failed: %s'):format(s.label, tostring(problem))) end
			end
		end
		if s.lane > 0 and not s.closing and (s.columns_of or s.cols_next) and #cands > 0 then pcall(FIRE.columns_next, s, cands) end
		if s.lane > 0 and not s.closing and s.columns_wait and not s.columns_of and not s.half_closed then
			-- the walker formed its columns for another unit when these lanes opened: try again at every look (they pass
			-- on once it is through that unit)
			local cw = s.columns_wait
			if cw.w.gone then
				s.columns_wait = nil
			elseif cw.w.walks and passing(cw.w, s) then -- (a look where it does not walk or pass is skipped, not the end of the wait)
				local t_cols = diag.t()
				local prev = diag.sw('columns')
				local ok, text, busy = pcall(start_columns, s, cw.w, s.lanes, cw.radius, nil)
				diag.add('columns', t_cols)
				diag.sw(prev)
				if not ok then
					log(('  COLUMNS LATE %s for %s failed: %s'):format(s.label, cw.w.label, tostring(text)))
					s.columns_wait = nil
				elseif s.columns_of == cw.w then
					log(('  COLUMNS LATE %s for %s, %.1f s after the opening: %s'):format(s.label, cw.w.label, (s.open_ticks or 0) * TICK_MS / 1000, text))
					s.columns_wait = nil
				elseif not busy then
					s.columns_wait = nil
				end
			end
		end
		if s.lane > 0 and not s.closing and s.steps and s.step < s.steps and state.game_seconds - s.step_at >= s.step_time - 1e-6 then next_step(s) end
		if s.lane > 0 and s.closing and state.game_seconds - s.closing.at >= s.closing.step_time - 1e-6 then closing_step(s) end
		if s.lane > 0 and s.crewed and s.lanes and s.lanes.art and not s.closing and state.game_seconds - (s.crew_post or 0) >= TUNE.CREW_REPOST - 1e-6 then
			s.crew_post = state.game_seconds
			send_stations(s, step_lanes(s, s.step or s.steps or 1), 0)
		end
		if s.lane > 0 and s.walker_seen then
			local seq = mr.read_uint32(state.tbl, s.walker_seen.at + E_SEQ)
			local new = (seq - s.walker_seq) % 4294967296
			if new > s.walker_new then
				log(('  REGROUP? %s was given %d new places by the game, %.1f s after the opening (%d in all so far)'):format(
					s.walker_seen.label, new - s.walker_new, (s.open_ticks or 0) * TICK_MS / 1000, new))
				s.walker_new = new
			end
		end
		if best then
			if s.through and s.through[best] then -- 085f: the candidate is one of the walkers that are through: the countdown runs
				s.quiet = s.quiet + 1
				if s.quiet >= HOLD_TICKS and not s.closing then start_closing(s, 'nobody is passing any more, ' .. s.through_text, nil, s.through) end
			else
				s.quiet = 0
			end
			s.open_ticks = (s.open_ticks or 0) + 1
			if s.lane > 0 and s.served and s.served[best] then -- (its heading stays its order while it crosses: look())
				diag.sw('inside')
				local ok, inside = pcall(still_inside, s, best)
				diag.sw('rest')
				if ok and inside then best.crossing_at = state.ticks end
			end
			if not s.col_logged and s.columns_of then
				local t_cols = diag.t()
				local prev = diag.sw('columns')
				local ok, done = pcall(log_columns, s) -- only a log line: a failure must not switch the mod off
				diag.add('columns', t_cols)
				diag.sw(prev)
				s.col_logged = (not ok) or done
				if not ok then log('  COLUMNS line failed: ' .. tostring(done)) end
			end
			if s.open_ticks == 15 then log(('  3 s after opening, %s: %s (the game says: moving %s, idle %s)'):format(s.label, moved_since(s), tostring(s.moving), tostring(s.idle))) end
		elseif s.lane > 0 then
			s.open_ticks = (s.open_ticks or 0) + 1
			diag.sw('inside')
			local crossing = not s.closing and crossing_walker(s, s.through)
			diag.sw('rest')
			if not s.closing and (crossing or (s.fire and state.ticks - s.fire.live <= TUNE.FIRE_GRACE)) then
				s.quiet = 0 -- a walker it opened for is still inside it
			else
				s.quiet = s.quiet + 1
				if s.quiet >= HOLD_TICKS and not s.closing then
					start_closing(s, s.fire and 'the line of fire is clear' or ('nobody is passing any more' .. (s.through and (', ' .. s.through_text) or '')), nil, s.through)
				end
			end
		end
	end
end

-- after an error in a unit: its lanes and columns shut, whatever state it was in (only the table is written)
local function force_closed(s)
	mr.write(state.tbl, s.at + E_PENDING, mr.uint32(0))
	mr.write(state.tbl, s.at + E_L, mr.uint32(0))
	-- (artillery: no E_DIRTY and no jobs here; moved crewmen stay on their lane places until the game's own 5 s test
	-- sends them back - this is the error path only)
	if not s.crewed then mr.write(state.tbl, s.at + E_DIRTY, mr.uint32(1)) end
	pcall(stop_columns, s)
	s.lane, s.lanes, s.stood, s.goal, s.closed_at, s.steps, s.step, s.closing = 0, nil, nil, nil, state.ticks, nil, nil, nil
	s.walker_seen, s.widened_for, s.widen_said, s.served, s.parked, s.crew_moved, s.crew_post = nil, nil, nil, nil, nil, nil, nil
	s.opened_at, s.columns_wait, s.fire, s.quick_gone, s.lanes_full, s.half_closed, s.half_off, s.cols_next, s.cols_done, s.guard_dropped = nil, nil, nil, nil, nil, nil, nil, nil, nil, nil
end

-- (log) a friendly walker whose middle is inside a unit that has no lanes open (run 36: an allied army was not passed
-- through, and the log said nothing about it):
-- once per pair and MISSED_KEEP looks, with the reason
function diag.missed()
	diag.run = (diag.run or 0) + 1
	local logged, more = 0, 0
	for _, w in ipairs(state.units) do
		if not w.gone and not w.ethereal and w.moving and w.x and w.men >= 1 and not w.busy and not yes(w.unit, 'is_currently_flying') then
			for _, s in ipairs(state.units) do
				-- (not lords / heroes, not units in melee or routing; standing by the SETTLED rule counts)
				if s ~= w and not s.gone and not s.character and not s.ethereal and not s.busy and s.alliance == w.alliance and s.lane == 0 and (s.stands or not s.moving)
					and s.x and s.gb and s.half and (w.x - s.x) ^ 2 + (w.z - s.z) ^ 2 < (s.half + TUNE.MISSED_DEPTH) ^ 2 then
					if s.face_run ~= diag.run then s.face_run, s.fx, s.fz = diag.run, math.sin(math.rad(s.gb)), math.cos(math.rad(s.gb)) end
					local rx, rz = w.x - s.x, w.z - s.z
					local across, along = math.abs(rx * s.fz - rz * s.fx), math.abs(rx * s.fx + rz * s.fz)
					s.missed_said = s.missed_said or {}
					if across < s.half and along < TUNE.MISSED_DEPTH and state.ticks - (s.missed_said[w] or -100000) > TUNE.MISSED_KEEP then
						if logged >= TUNE.MISSED_LINES then
							more = more + 1
						else
							logged = logged + 1
							s.missed_said[w] = state.ticks
							local why
							if not s.stands then
								why = 'it does not stand: ' .. (s.why_not or '?')
							elseif not w.walks then
								why = 'the walker does not count as walking' .. (w.lane > 0 and ' (it makes way itself)' or '')
							elseif s.late and s.late[w] then
								why = 'left out: ' .. (s.late[w][7] == 'nolanes' and 'no lanes (no room)' or (s.late[w][7] == 'inside' and 'it was already inside when it began to stand')
									or 'too late for a lane as wide as its whole front')
							elseif select(2, passing(w, s)) == 'leaving' then
								why = "it walks away from it (its middle is past the standing unit's middle and it does not overlap it)"
							elseif not passing(w, s) then
								why = ('its way does not cross it as the mod sees it (heading from its %s, %.1f m to go, %.1f m across of %.1f)'):format(
									w.heading or '?', w.way or -1, math.abs((s.x - w.x) * w.dz - (s.z - w.z) * w.dx), s.half + w.half * 0.5)
							else
								why = 'it sees the walker' .. (s.plan_first == w and ' (its nearest walker)'
									or (big_walker(w) and ' (not its nearest walker; a big one)' or ' (not its nearest walker and not a big one: not planned for)'))
									.. ' but did not open: ' .. (s.no_lanes_why or 'no reason recorded')
							end
							log(('MISSED? %s is inside %s (%.1f m from its middle across, %.1f m along; it faces %.2f / %.2f) and no lanes are open: %s'):format(
								w.label, s.label, across, along, s.fx, s.fz, why))
						end
					end
				end
			end
		end
	end
	if more > 0 then log(('MISSED? ... and %d more walkers inside units without lanes in this check'):format(more)) end
end

-- the line of fire ---------------------------------------------------------------------------------------------------
-- A direct-fire gun (cannon, bolt thrower, organ gun, ...: FIRE.guns, from the game's tables) that stands and has a target
-- shoots along the line gun -> target. A friendly unit that stands in that line opens ONE lane along it (the single-lane plan
-- of a walker), all at once, so the shot goes through the gap and the gun does not hold fire. Walkers come first. The Skaven
-- mod (its code cave and table, read only) overrides this for the gun / unit pairs it lets shoot through.
FIRE.lane = 4.0 -- m: width of the lane along the line of fire (tune later)
FIRE.front, FIRE.back = 3, 5 -- m: the line counts from 3 m in front of the gun to 5 m short of the target
FIRE.margin = 5 -- m: a target this much further than the gun's range still counts
FIRE.every = 2 -- looks between two checks (the cost is per gun and per unit near its line)
FIRE.turn = 10 -- degrees: a line that turns this much is planned anew
FIRE.replan_every = 10 -- looks between two plans of the same lane (085d: 5 -> 10, the gap is updated every 2 s)
FIRE.skv_every = 10 -- looks between two reads of the Skaven mod's table (it refreshes its lists every 2 s)
FIRE.asks = 150 -- looks with a loaded gun after which "no unit was ever returned" is said

-- the reading of the Skaven mod's table: its cave must be at its place in the game code and be its own (three parts)
function FIRE.skaven_refresh()
	local f, k = state.fire, FIRE.skv
	f.skv_at, f.skv_table = state.ticks, nil
	if f.skv_site == nil then -- its place in the game code is searched ONCE per battle
		local address, count = mr.find_pattern(k.site)
		if not address or count ~= 1 then
			f.skv_site = false
			log('FIRE: the place of the Skaven mod in the game code was found ' .. tostring(count) .. ' times: its override is not available')
		else
			f.skv_site = mr.add(address, k.site_off)
		end
	end
	if not f.skv_site then return end
	local info = mr.hook_info(f.skv_site)
	if not info or info.kind ~= 'cave' or not info.cave then return end -- nothing there, or something else: not active
	local sub = string.sub_lua or string.sub
	local length = #k.cave_a + 8 + #k.cave_b + 8 + #k.cave_c
	local bytes = mr.read(info.cave, 0, length)
	local b_from = #k.cave_a + 9
	local c_from = b_from + #k.cave_b + 8
	if #bytes ~= length or sub(bytes, 1, #k.cave_a) ~= k.cave_a or sub(bytes, b_from, b_from + #k.cave_b - 1) ~= k.cave_b or sub(bytes, c_from) ~= k.cave_c then return end
	local tbl = mr.read_pointer(info.cave, #k.cave_a)
	if mr.is_null(tbl) then return end
	local t = { enable = mr.read_uint32(tbl, k.off_enable) ~= 0, all = mr.read_uint32(tbl, k.off_all) ~= 0, shooters = {}, blockers = {} }
	for i = 0, k.max_shooters - 1 do
		local p = mr.read_pointer(tbl, k.off_shooters + 8 * i)
		if mr.is_null(p) then break end
		t.shooters[hex(p)] = true
	end
	for i = 0, k.max_blockers - 1 do
		local p = mr.read_pointer(tbl, k.off_blockers + 8 * i)
		if mr.is_null(p) then break end
		t.blockers[hex(p)] = true
	end
	f.skv_table = t
	if not f.skv_seen and t.enable then
		f.skv_seen = true
		log('FIRE: the Skaven mod is active: it decides for the guns and units it lists')
	end
end

-- does the Skaven mod let gun g shoot through unit s (then no lane is opened)?
function FIRE.skaven_covers(g, s)
	local t = state.fire.skv_table
	return t ~= nil and t.enable and t.shooters[g.key] == true and (t.all or t.blockers[s.key] == true)
end

-- the lines of fire: { g, t (entry of the target), gx, gz, dx, dz (unit direction), L } of every direct-fire gun that
-- stands still, is not limbered, has ammunition and a living enemy target within its range
function FIRE.lines()
	local f, lines = state.fire, {}
	for _, g in ipairs(state.units) do
		-- (stands still by the game's own idea: g.stands is false for a wavering or expendable gun under the MCT settings)
		if not g.gone and g.fire_gun and g.x and not g.moving and g.still_since and not g.busy and not yes(g.unit, 'is_limbered_artillery')
			and type(g.ammo) == 'number' and g.ammo > 0 then
			f.asked = f.asked + 1
			local ok, target = pcall(function() return g.unit:current_target() end)
			if ok and target ~= nil and not f.type_said then
				f.type_said = true
				log(('FIRE: current_target() of %s returned a %s (%s)'):format(g.label, type(target), tostring(target)))
			end
			local te = nil
			if ok and (type(target) == 'table' or type(target) == 'userdata') then
				local uok, uid = pcall(function() return target:unique_ui_id() end)
				if uok then
					for _, e in ipairs(state.units) do
						if not e.gone then
							if e.uid == nil then e.uid = e.unit:unique_ui_id() end
							if e.uid == uid then te = e break end
						end
					end
				end
			end
			if te and te ~= g and te.alliance ~= g.alliance and te.men >= 1 and te.x then
				f.got = true
				local rok, range = pcall(function() return g.unit:missile_range() end)
				local dx, dz = te.x - g.x, te.z - g.z
				local length = math.sqrt(dx * dx + dz * dz)
				if rok and type(range) == 'number' and range > 0 and length > FIRE.front + FIRE.back + 2 and length <= range + FIRE.margin then
					local ln = { g = g, t = te, gx = g.x, gz = g.z, dx = dx / length, dz = dz / length, L = length }
					FIRE.machines(ln)
					lines[#lines + 1] = ln
				end
			end
		end
	end
	if not f.got and not f.none_said and f.asked >= FIRE.asks then
		f.none_said = true
		log(('FIRE: current_target() returned no unit in %d looks of loaded guns so far: nothing to clear (so far)'):format(f.asked))
	end
	return lines
end

-- the machines of the battery that have a crew (models whose carrier, a machine of this unit, they work; loose crewmen and
-- machines without crew have no gap): ln.machines = { { off, x, z } } with off = across the line (a = (-dz, dx)) from the gun unit's
-- middle; ln.span = half the battery's width across the line. One gun, or machines not readable: one machine on the line.
function FIRE.machines(ln)
	local g, list, seen = ln.g, {}, {}
	-- (the places of the machines are read at most every FIRE.skv_every looks per gun unit: a standing battery does not move; only the
	-- offsets across the line are worked out on every look)
	if not g.mach_pos or state.ticks - g.mach_at >= FIRE.skv_every then
		local pos = {}
		pcall(function()
			local n, models = models_of(g)
			for index = 0, (n or 0) - 1 do
				local model = mr.read_pointer(models, index * 8)
				if not mr.is_null(model) then
					local carrier = mr.read_pointer(model, 0x2e0)
					if not mr.is_null(carrier) and hex(mr.read_pointer(carrier, 0x2e8)) == g.key and not seen[hex(carrier)] then
						seen[hex(carrier)] = true
						local x, _, z = get_xyz(carrier)
						if x and z and near_unit(g, x, z) and #pos < 60 then pos[#pos + 1] = { x, z } end
					end
				end
			end
		end)
		g.mach_pos, g.mach_at = pos, state.ticks
	end
	for _, p in ipairs(g.mach_pos) do list[#list + 1] = { off = -(p[1] - ln.gx) * ln.dz + (p[2] - ln.gz) * ln.dx, x = p[1], z = p[2] } end
	if #list == 0 then list = { { off = 0, x = ln.gx, z = ln.gz } } end
	local lo, hi = list[1].off, list[1].off
	for _, m in ipairs(list) do lo, hi = math.min(lo, m.off), math.max(hi, m.off) end
	ln.machines, ln.span = list, (hi - lo) / 2
end

-- is the line `ln` within reach of a model at one of `places` of unit s (4 m lane + the radius of a model; extra: half the
-- battery's width, as the pre-filter for all its machines)?
function FIRE.crosses(s, ln, places, extra)
	local a0, a1 = FIRE.front, ln.L - FIRE.back
	local reach = FIRE.lane / 2 + (model_radius(s)) + (extra or 0)
	for _, p in pairs(places) do
		local rx, rz = p[1] - ln.gx, p[3] - ln.gz
		local a, c = rx * ln.dx + rz * ln.dz, rx * ln.dz - rz * ln.dx
		local clamped = math.max(a0, math.min(a1, a))
		if (a - clamped) ^ 2 + c ^ 2 <= reach * reach then return true end
	end
	return false
end

-- (cheap) can the line pass near the models of s at all?
function FIRE.near(s, ln)
	local rx, rz = s.x - ln.gx, s.z - ln.gz
	local a, c = rx * ln.dx + rz * ln.dz, rx * ln.dz - rz * ln.dx
	local clamped = math.max(FIRE.front, math.min(ln.L - FIRE.back, a))
	local reach = s.half + 25 + ln.span
	return (a - clamped) ^ 2 + c ^ 2 <= reach * reach
end

-- the machines whose line (from 3 m in front of the machine to 5 m short of the target, converging on the target's middle:
-- its offset at distance t along the line is off * (1 - t / L)) comes within a lane and a model's radius of a model of s.
-- Returns the crossings at s (across the line) of those, sorted, and of all machines (projected), and the distance of s along the line.
function FIRE.hit_list(s, ln, places, along)
	local a0, a1 = FIRE.front, ln.L - FIRE.back
	local reach = FIRE.lane / 2 + (model_radius(s))
	local hits, all = {}, {}
	for _, m in ipairs(ln.machines) do
		local c0, c1 = m.off * (1 - a0 / ln.L), m.off * (1 - a1 / ln.L)
		local ex, ec = a1 - a0, c1 - c0
		local len2 = ex * ex + ec * ec
		local cross = m.off * (1 - along / ln.L)
		all[#all + 1] = cross
		for _, p in pairs(places) do
			local rx, rz = p[1] - ln.gx, p[3] - ln.gz
			local a, c = rx * ln.dx + rz * ln.dz, rz * ln.dx - rx * ln.dz -- (c across the line the way m.off is: a = (-dz, dx))
			local t = len2 > 0 and math.max(0, math.min(1, ((a - a0) * ex + (c - c0) * ec) / len2)) or 0
			if (a - (a0 + t * ex)) ^ 2 + (c - (c0 + t * ec)) ^ 2 <= reach * reach then
				hits[#hits + 1] = cross
				break
			end
		end
	end
	table.sort(hits)
	return hits, all
end

-- the lanes for the crossings (across the line, sorted) of the machines that hit s; cx, cz: the point of the line at the
-- middle of s, ax, az: across. One crossing: a lane of FIRE.lane there. Several, evenly spaced (GUN_GAP_SLACK) with room for a
-- block between the gaps: one gap each, as the lanes per file of a walker (period q, blocks q - lane). Else ONE wide lane over
-- them all (not over the whole front of s: then nil and why). Returns lanes and { kind, count, q, mid, wide, why }.
function FIRE.shape(s, hits, places, cx, cz, ax, az)
	local m, L = #hits, FIRE.lane
	local function single(width, mid)
		return { cx + ax * mid - ax * 50000, cz + az * mid - az * 50000, ax, az, 100000, width, 1, -0.5 }
	end
	if m == 1 then return single(L, hits[1]), { kind = 'one', count = 1, q = 0, mid = hits[1], wide = L } end
	local q = (hits[m] - hits[1]) / (m - 1)
	local even = true
	for i = 2, m do
		if math.abs(hits[i] - hits[i - 1] - q) > GUN_GAP_SLACK * q then even = false end
	end
	local mid = (hits[1] + hits[m]) / 2
	if even and q - L >= math.max(BLOCK_MIN, 2 * (model_radius(s)) + 0.2) then
		local n = math.ceil(m / 2)
		local o = mid
		if m % 2 == 1 then -- one gap more than needed: on the side with fewer models beyond the outermost needed gap
			local below, above = 0, 0
			for _, p in pairs(places) do
				local a = (p[1] - cx) * ax + (p[3] - cz) * az
				if a < mid - (m - 1) / 2 * q - L / 2 then below = below + 1 end
				if a > mid + (m - 1) / 2 * q + L / 2 then above = above + 1 end
			end
			o = below <= above and mid - q / 2 or mid + q / 2
		end
		local lanes = { cx + ax * o, cz + az * o, ax, az, q - L, L, n, 0 }
		lanes.vector, lanes.guns = true, m
		return lanes, { kind = 'gaps', count = m, q = q, mid = mid, wide = L }
	end
	local wide = math.min(LANE_WIDE_MAX, hits[m] - hits[1] + L)
	if wide >= 2 * s.half then return nil, { why = 'it would take the whole front' } end
	return single(wide, mid), { kind = 'wide', count = m, q = q, mid = mid, wide = wide,
		why = even and ('the gaps of %.1f m leave no block of %.1f m between them'):format(q, math.max(BLOCK_MIN, 2 * (model_radius(s)) + 0.2)) or 'the guns stand unevenly' }
end

-- the plan for line `ln` through unit s, whose models stand at `places`: lanes and the information about them, or nil and why.
-- Made once, then once more with the models shifted by it: a machine whose line now hits a shifted block joins.
function FIRE.plan(s, ln, places)
	local ax, az = -ln.dz, ln.dx
	local along = (s.x - ln.gx) * ln.dx + (s.z - ln.gz) * ln.dz
	local cx, cz = ln.gx + ln.dx * along, ln.gz + ln.dz * along
	local hits, all = FIRE.hit_list(s, ln, places, along)
	if #hits == 0 then return nil, nil, 'no machine line hits it' end
	local lanes, info = FIRE.shape(s, hits, places, cx, cz, ax, az)
	if lanes and #ln.machines > #hits then
		local moved = {}
		for key, p in pairs(places) do
			local x, z = shifted(lanes, p[1], p[3])
			moved[key] = { x, p[2], z }
		end
		local more = FIRE.hit_list(s, ln, moved, along)
		if #more > #hits then
			hits = more
			lanes, info = FIRE.shape(s, hits, places, cx, cz, ax, az)
		end
	end
	if not lanes then return nil, nil, info.why end
	info.along, info.all = along, all
	info.point = { cx + ax * info.mid, cz + az * info.mid } -- where the middle of the gaps lies on the line at s
	info.dir = { ln.dx, ln.dz }
	return lanes, info
end

-- the walker-like stand-in make_room works the room beside s out for (the direction of the shot)
function FIRE.walker(ln)
	return { dx = ln.dx, dz = ln.dz, x = ln.gx, z = ln.gz, gone = true, walks = false, label = 'the line of fire' }
end

-- is a walker about to need unit s? Then no lane for the line of fire
function FIRE.walker_needs(s)
	for _, w in ipairs(state.units) do
		-- (085d review 9: a late / quick-gone walker that still walks through s needs its lane as well)
		if FIRE.candidate(s, w) or (s.late and s.late[w] and w.walks and passing(w, s)) then return w end
	end
	return nil
end

-- the text about the lanes for the log
function FIRE.shape_text(info)
	if info.kind == 'gaps' then return ('%d gaps of %.1f m every %.1f m'):format(info.count, FIRE.lane, info.q) end
	if info.kind == 'wide' then return ('one wide lane of %.1f m (%s)'):format(info.wide, info.why) end
	return ('one lane of %.1f m'):format(FIRE.lane)
end

-- the lane for the line of fire: s.fire is set first, so that if anything fails on the way FIRE.fail knows the lane is ours
-- and closes it. Returns false when there is no lane to open (the reason is logged once per gun and unit).
function FIRE.open(s, ln)
	local f = state.fire
	local pok, places = pcall(near_places, s)
	if not pok then return false end
	local lanes, info, why = FIRE.plan(s, ln, places)
	local pair = ln.g.key .. s.key
	if not f.cross_said then f.cross_said = {} end
	if info and not f.cross_said[pair] then -- once per battery and unit: where each machine's line crosses it, if the game aims each gun at the target's middle (projected) or straight on (parallel)
		f.cross_said[pair] = true
		local projected, parallel = {}, {}
		for i, m in ipairs(ln.machines) do
			projected[i] = ('%.1f'):format(m.off * (1 - info.along / ln.L))
			parallel[i] = ('%.1f'):format(m.off)
		end
		log(('FIRE: %d machines of %s through %s, crossing it across the line at %s m projected on the target, %s m parallel to the line'):format(
			#ln.machines, ln.g.label, s.label, table.concat(projected, ' / '), table.concat(parallel, ' / ')))
	end
	if not lanes then
		if not f.said[pair .. 'plan'] then
			f.said[pair .. 'plan'] = true
			log(('FIRE: no lane in %s for the line of %s: %s'):format(s.label, ln.g.label, tostring(why)))
		end
		return false
	end
	s.fire = { g = ln.g, t = ln.t, plan = info, live = state.ticks, plan_at = state.ticks }
	local sent, tpl, room = FIRE.begin_lanes(s, FIRE.walker(ln), lanes, 1, 0)
	state.opened = state.opened + 1
	log(('FIRE OPEN %s clears the line of fire of %s at %s (%.0f m, lane %.1f m, crossing at %.0f m) | %s; %s; %s'):format(
		s.label, ln.g.label, ln.t.label, ln.L, FIRE.lane, info.along, FIRE.shape_text(info), room,
		sent > 0 and ('%d models sent at once'):format(sent) or (type(tpl) == 'table' and 'nobody had to move' or ('NOT sent at once: ' .. tostring(tpl)))))
	return true
end

-- all the fields of the new lanes, as begin_lanes writes them (the room beside s and the rescale for closing up included), and
-- the first step sent at once. gap (085d): only the gap moved - the models that must move for it are sent, nobody else (send_stations
-- with the lanes sent last as prev), and the game is not told to hand out all places (TUNE.FIRE_GAP_DIRTY). Returns the jobs sent and
-- the models looked at (0: all were sent)
function FIRE.rewrite(s, ln, lanes, gap)
	local prev = gap and not s.jobs_dropped and s.lanes or nil
	s.jobs_dropped = nil
	pcall(make_room, s, FIRE.walker(ln), lanes)
	if lanes.vector and (lanes[9] or 0) < 0 then lanes[5] = lanes[5] / (1 + lanes[9]) end
	s.lanes, s.lane = lanes, lanes[6]
	s.steps, s.step, s.step_time, s.shift = 1, 0, 0, farthest_shift(lanes)
	put_float(s.at + E_OX, lanes[1])
	put_float(s.at + E_OZ, lanes[2])
	put_float(s.at + E_AX, lanes[3])
	put_float(s.at + E_AZ, lanes[4])
	put_float(s.at + E_P, lanes[5])
	mr.write(state.tbl, s.at + E_N, mr.uint32(lanes[7]))
	put_float(s.at + E_BIAS, lanes[8])
	put_float(s.at + E_S0, lanes[10] or 0)
	local jobs = next_step(s, true, prev)
	return type(jobs) == 'number' and jobs or 0, s.send_seen or 0
end

-- the text about what a rewrite sent
function FIRE.sent_text(jobs, seen)
	if seen > 0 then return ('%d of %d models moved'):format(jobs, seen) end
	return ('%d models sent'):format(jobs)
end

-- is an enemy unit near standing unit s? Its centre distance less both halves is within `reach` m, or (seconds given) it closes in on s fast
-- enough to reach it in that time at its measured speed (only with SPEED_SAMPLES measurements). Routing enemies do not count. Returns the enemy
function FIRE.enemy_near(s, reach, seconds)
	for _, e in ipairs(state.units) do
		if e ~= s and not e.gone and e.alliance ~= s.alliance and e.x and e.men >= 1 then
			local dx, dz = e.x - s.x, e.z - s.z
			local d = math.sqrt(dx * dx + dz * dz)
			local gap = d - s.half - (e.half or 0)
			local near = gap <= reach
			if not near and seconds and e.moving and (e.samples or 0) >= SPEED_SAMPLES and e.vx and d > 0.1 then
				local closing = -(dx * e.vx + dz * e.vz) / d -- (m/s towards s)
				near = closing > 0.5 and gap <= closing * seconds
			end
			if near and not yes(e.unit, 'is_routing') then return e end
		end
	end
	return nil
end

-- an enemy near s (15 m or 3 s) holds the lane for the line of fire back until every enemy is further than 20 m (s.enemy_hold: no flapping at the edge)
function FIRE.enemy_blocks(s)
	if s.enemy_hold then
		if FIRE.enemy_near(s, TUNE.FIRE_ENEMY_OPEN, nil) then return true end
		s.enemy_hold = nil
		return false
	end
	if FIRE.enemy_near(s, TUNE.FIRE_ENEMY_NEAR, TUNE.FIRE_ENEMY_SECONDS) then
		s.enemy_hold = true
		return true
	end
	return false
end

-- the lane is open: the line (of the gun it was opened for, else of another) still hits s as it stood. The gap follows the line: a plan
-- that moved more than TUNE.FIRE_GAP_DRIFT, whose gaps changed their distance by more than a quarter lane, or whose line turned is written
-- at once (no closing and opening again; only the models that must move are sent), at most every FIRE.replan_every looks. The models are
-- re-formed in full only when the guns aim at ANOTHER UNIT (the plan for it is made, and the lane written, only after the same new target
-- was kept TUNE.FIRE_DEBOUNCE looks - run 52: the cannon switched target 16 x in 2 min); the old plan holds meanwhile. An enemy near closes it.
function FIRE.keep(s, lines)
	local fire = s.fire
	if FIRE.enemy_blocks(s) then
		start_closing(s, 'an enemy is near', true) -- (false: sends are used up this look, the next one tries again)
		return
	end
	local same, other = nil, nil -- the line on the target unit of the plan (this gun first) / the line of the guns' other target
	for _, ln in ipairs(lines) do
		if ln.g.alliance == s.alliance and ln.g ~= s and FIRE.near(s, ln) and FIRE.crosses(s, ln, s.stood or {}, ln.span) and not FIRE.skaven_covers(ln.g, s) then
			if ln.t == fire.t then
				if not same or ln.g == fire.g then same = ln end
			elseif not other or ln.g == fire.g then
				other = ln
			end
		end
	end
	if same then
		fire.pending = nil
		-- the same gun on the same target, planned less than FIRE.replan_every looks ago: only the cheap tests above, the plan stays
		if same.g == fire.g and state.ticks - (fire.plan_at or -1000) < FIRE.replan_every then
			fire.live = state.ticks
			return
		end
		fire.plan_at = state.ticks
		local lanes, info = FIRE.plan(s, same, s.stood or {})
		if not lanes then return end
		fire.live = state.ticks
		local old = fire.plan
		local ax, az = -old.dir[2], old.dir[1]
		local drift = math.abs((info.point[1] - old.point[1]) * ax + (info.point[2] - old.point[2]) * az)
		local turned = math.deg(math.acos(math.min(1, math.abs(info.dir[1] * old.dir[1] + info.dir[2] * old.dir[2]))))
		local form_new = info.count ~= old.count or info.kind ~= old.kind -- (a changed hit count / kind counts only once it stays 2 x replan_every looks: no replan spam when it flickers)
		local shape = false
		if form_new then
			if not fire.form_new or fire.form_new[1] ~= info.count or fire.form_new[2] ~= info.kind then fire.form_new = { info.count, info.kind, state.ticks } end
			shape = state.ticks - fire.form_new[3] >= 2 * FIRE.replan_every
		else
			fire.form_new = nil
			shape = math.abs(info.q - old.q) > FIRE.lane / 4
		end
		local other_gun = same.g ~= fire.g -- (another gun of the same battery on the same target: the line is another one, the gap moves)
		if state.ticks - (fire.replan_at or -1000) >= FIRE.replan_every and (other_gun or drift > TUNE.FIRE_GAP_DRIFT or turned > FIRE.turn or shape) then -- (085d review 4: another gun obeys the rate limit too)
			local jobs, seen = FIRE.rewrite(s, same, lanes, not (form_new and shape))
			log(('FIRE REPLAN %s: gap updated, the line of %s at %s moved %.1f m, turned %.0f deg (crossing at %.0f m): %s | %s'):format(
				s.label, same.g.label, same.t.label, drift, turned, info.along, FIRE.shape_text(info), FIRE.sent_text(jobs, seen)))
			fire.g, fire.plan, fire.replan_at, fire.form_new = same.g, info, state.ticks, nil
		end
	elseif other then
		-- another target unit: the old plan and lane stay until the same new target has been kept for TUNE.FIRE_DEBOUNCE looks
		local pd = fire.pending
		if not pd or pd.t ~= other.t or state.ticks - pd.last > 3 * FIRE.every then
			pd = { t = other.t, g = other.g, since = state.ticks }
			fire.pending = pd
		end
		pd.last = state.ticks
		if state.ticks - pd.since < TUNE.FIRE_DEBOUNCE then
			fire.live = state.ticks
			return
		end
		fire.plan_at = state.ticks
		local lanes, info = FIRE.plan(s, other, s.stood or {})
		if not lanes then return end
		fire.live = state.ticks
		local old = fire.plan
		local drift = math.abs((info.point[1] - old.point[1]) * -old.dir[2] + (info.point[2] - old.point[2]) * old.dir[1])
		local turned = math.deg(math.acos(math.min(1, math.abs(info.dir[1] * old.dir[1] + info.dir[2] * old.dir[2]))))
		local jobs, seen = FIRE.rewrite(s, other, lanes, false)
		log(('FIRE REPLAN %s: new target %s for %s (kept %.1f s, was %s), the line moved %.1f m, turned %.0f deg (crossing at %.0f m): %s, re-formed | %s'):format(
			s.label, other.t.label, other.g.label, (state.ticks - pd.since) * TICK_MS / 1000, fire.t.label, drift, turned, info.along, FIRE.shape_text(info), FIRE.sent_text(jobs, seen)))
		fire.g, fire.t, fire.plan, fire.replan_at, fire.form_new, fire.pending = other.g, other.t, info, state.ticks, nil, nil
	end
end

-- an error in the lane of s for the line of fire: it closes (the walkers' lanes are never touched), and after 3 errors the unit
-- is left out of the line of fire
function FIRE.fail(s, problem)
	s.fire_errors = (s.fire_errors or 0) + 1
	log(('ERROR %s in the line of fire (%d so far): %s; its lane closes%s'):format(s.label, s.fire_errors, tostring(problem), s.fire_errors >= 3 and ', it is left out of the line of fire' or ''))
	if s.lane > 0 or s.fire then
		local cok = pcall(close_lanes, s, 'an error in the line of fire')
		if not cok or s.lane > 0 or s.fire then pcall(force_closed, s) end
	end
	if s.fire_errors >= 3 then s.fire_off = true end
end

-- one standing unit and the lines of fire: the nearest gun unit whose machines hit it makes the lanes (one battery per unit)
function FIRE.unit(s, lines)
	local f = state.fire
	if s.fire then
		if not s.closing then FIRE.keep(s, lines) end
	elseif s.lane == 0 and #lines > 0 and state.ticks - (s.closed_at or -1000) >= HOLD_TICKS and s.men > SINGLE_MAX_MEN then
		local blk = s.fire_block -- (a lane for the line of fire gave way to a walker: none opens before that walker has passed)
		if blk then
			if not blk.w.gone and state.ticks - blk.at <= TUNE.FIRE_BLOCK_TICKS and (FIRE.candidate(s, blk.w) or state.ticks - (blk.seen or blk.at) < HOLD_TICKS) then
				if FIRE.candidate(s, blk.w) then blk.seen = state.ticks end
				return
			end
			if not blk.w.gone and state.ticks - blk.at <= TUNE.FIRE_BLOCK_TICKS then -- 085f review 7: while the walker's models are still inside, none opens right behind it
				local iok, inside = pcall(function() return overlaps(s, blk.w, near_places(s)) end)
				if iok and inside then return end
			end
			s.fire_block = nil
		end
		-- the cheap test first (FIRE.near): only a unit that a line passes near has its models read (run 51: near_places of EVERY standing
		-- unit on every second look, 12 of 25 looks over 4 ms in windows with no sends and no plans)
		local near = {}
		for _, ln in ipairs(lines) do
			if ln.g.alliance == s.alliance and ln.g ~= s and FIRE.near(s, ln) then near[#near + 1] = ln end
		end
		if #near == 0 then return end
		if not TUNE.FIRE_KINDS[s.kind] then -- (085f: only infantry clears a line of fire, every army's; a cavalry unit does not make way for artillery)
			local pair = near[1].g.key .. s.key
			if not f.said[pair .. 'kind'] then
				f.said[pair .. 'kind'] = true
				log(('FIRE: %s is not infantry: no lane for the line of fire of %s'):format(s.label, near[1].g.label))
			end
			return
		end
		local ok, one = pcall(one_body, s) -- (a monster or a chariot has no models to step aside)
		if ok and one then return end
		local pok, places = pcall(near_places, s)
		if not pok then return end
		local cands = {}
		for _, ln in ipairs(near) do
			if FIRE.crosses(s, ln, places, ln.span) then
				cands[#cands + 1] = { ln = ln, d = (s.x - ln.gx) ^ 2 + (s.z - ln.gz) ^ 2 }
			end
		end
		if #cands > 0 and FIRE.enemy_blocks(s) then return end -- (an enemy is near, or was not yet 20 m away)
		table.sort(cands, function(a, b) return a.d < b.d end)
		local opened = false
		for _, c in ipairs(cands) do
			local ln = c.ln
			local pair = ln.g.key .. s.key
			if opened then
				if not f.said[pair .. 'also'] then
					f.said[pair .. 'also'] = true
					log(('FIRE: the line of %s also crosses %s, which has a lane for another battery'):format(ln.g.label, s.label))
				end
			elseif FIRE.skaven_covers(ln.g, s) then
				if not f.said[pair] then
					f.said[pair] = true
					log(('FIRE: the Skaven mod shoots through %s for %s: no lane'):format(s.label, ln.g.label))
				end
			elseif not select(2, pcall(FIRE.walker_needs, s)) then
				opened = FIRE.open(s, ln)
			end
		end
	end
end

-- one look at the lines of fire: the lines every FIRE.every looks, each unit once per FIRE.every looks, spread over them by its place in the
-- list (all units on one look made every second look the long one, run 51)
function FIRE.run()
	if not state.cfg.fire or not state.cfg.enabled then return end
	state.fire = state.fire or { asked = 0, said = {} }
	local f = state.fire
	if state.ticks % FIRE.every == 0 then
		local any = false -- is there a direct-fire gun in the battle at all?
		for _, g in ipairs(state.units) do
			if not g.gone then
				if g.fire_gun == nil then g.fire_gun = FIRE.guns[tostring(g.unit:type())] == true end
				if g.fire_gun then any = true end
			end
		end
		if not any then
			f.lines = nil
			return
		end
		if not f.skv_at or state.ticks - f.skv_at >= FIRE.skv_every then
			local ok, problem = pcall(FIRE.skaven_refresh)
			if not ok then
				f.skv_table = nil
				if not f.skv_failed then f.skv_failed = true; log('FIRE: reading the Skaven mod failed: ' .. tostring(problem)) end
			end
		end
		f.lines = FIRE.lines()
	end
	local lines = f.lines
	if not lines then return end
	for _, s in ipairs(state.units) do
		if not s.gone and (state.ticks + math.floor(s.at / ENTRY)) % FIRE.every == 0 and not s.fire_off and s.stands and not s.character and s.kind ~= 'art' and not s.busy and s.x and s.half then
			local ok, problem = pcall(FIRE.unit, s, lines) -- each unit on its own, as in update()
			if not ok then FIRE.fail(s, problem) end
		end
	end
end

-- (085d) units that have just stopped and that a friendly walker already comes for stand after TUNE.STILL_FAST: a second pass after all looks (it needs
-- the walkers' headings). Never when the unit's position jumped (watch) or its models jumped (jumped_at) within STILL_TICKS (runs 24 / 25 crashed on jobs
-- sent to a unit that had just been teleported), when it holds no valid template (nothing could be sent), or when it is not at its ordered place
-- (TUNE.SETTLED, the same test as look()'s) - those keep the 4 s.
function FIRE.fast_stands()
	local need = math.ceil(TUNE.STILL_FAST * 1000 / TICK_MS - 1e-6)
	local cfg = state.cfg
	for _, e in ipairs(state.units) do
		if not e.gone and not e.stands and e.still_since and e.lane == 0 and not e.busy and e.x and e.gx and e.men >= MIN_MEN and e.alliance
			and state.ticks - e.still_since >= need and state.ticks - e.still_since < STILL_TICKS
			and not (e.watch_until and state.ticks <= e.watch_until)
			and not (e.jumped_at and (e.jumped_at >= e.still_since or state.ticks - e.jumped_at < STILL_TICKS))
			and not (e.character or e.ethereal or not cfg.enabled or (cfg.expendable and e.expendable))
			and (e.x - e.gx) ^ 2 + (e.z - e.gz) ^ 2 < TUNE.SETTLED * TUNE.SETTLED then
			local tok, tpl = pcall(template, e)
			if tok and tpl and not (cfg.wavering and yes(e.unit, 'is_wavering')) then
				for _, w in ipairs(state.units) do
					local pok, along = pcall(FIRE.candidate, e, w)
					if pok and along then
						e.fast_stand, e.stands, e.why_not = true, true, nil -- (why_not: the reason it did not stand yet is no longer true)
						log(('  %s stands after %.1f s (not %.1f): %s comes for it, %.1f m ahead'):format(e.label, (state.ticks - e.still_since) * TICK_MS / 1000, STILL_TICKS * TICK_MS / 1000, w.label, along))
						break
					end
				end
			end
		end
	end
end

local function update()
	local now = game_clock()
	if now and state.clock then
		state.dt = ((now - state.clock) % 4294967296) * 0.1
		if state.dt > 5 then state.dt = TICK_MS / 1000 end
	else
		state.dt = TICK_MS / 1000
	end
	state.clock = now
	state.game_seconds = (state.game_seconds or 0) + state.dt
	local jobs_left = false
	local t_look = diag.t()
	for _, entry in ipairs(state.units) do
		if not entry.gone then
			local ok, problem = pcall(look, entry)
			if not ok then retire(entry, 'not readable: ' .. tostring(problem)) end
		end
		if entry.shape_due then diag.run_check('shape', diag.shape_run, entry) end
		if entry.jobs_at then
			if mr.read_uint32(state.tbl, entry.at + E_PENDING) == 0 then
				entry.jobs_at = nil -- every job taken
			elseif state.ticks - entry.jobs_at > 15 then -- jobs nobody took within 3 s are dropped
				mr.write(state.tbl, entry.at + E_PENDING, mr.uint32(0))
				entry.jobs_at, entry.jobs_dropped = nil, true -- (085d review 6: its models may stand on an old place: the next gap rewrite sends everything)
			end
		end
		jobs_left = jobs_left or entry.jobs_at ~= nil
	end
	local fok, fproblem = pcall(FIRE.fast_stands)
	if not fok and not state.fast_said then state.fast_said = true; log('  the quick stand check failed (logged once): ' .. tostring(fproblem)) end
	diag.add('look', t_look)
	if state.jobs_any and not jobs_left then
		mr.write(state.tbl, CN.JOBS_ANY, mr.uint32(0))
		state.jobs_any = false
	end
	-- each unit on its own (one error used to switch the mod off for the whole battle): a unit whose look fails closes its
	-- lanes and is left alone for 5 s; after 5 errors it is left out
	local t_serve = diag.t()
	for _, s in ipairs(state.units) do
		if not s.gone and state.ticks >= (s.resume_at or 0) then
			diag.sw_begin()
			local ok, problem = pcall(serve, s)
			diag.sw_end()
			if not ok then
				s.errors = (s.errors or 0) + 1
				log(('ERROR %s (%d so far): %s; its lanes close, it is left alone for 5 s'):format(s.label, s.errors, tostring(problem)))
				if s.lane > 0 or s.columns_of then
					local cok = pcall(close_lanes, s, 'an error in the script')
					if not cok or s.lane > 0 then pcall(force_closed, s) end
				end
				s.resume_at = state.ticks + REPORT_EVERY
				if s.errors >= 5 then pcall(retire, s, 'five errors in the script') end
			end
		end
	end
	diag.add('serve', t_serve)
	for _, w in ipairs(state.units) do -- 085e: narrowed walkers (read-back, run state, hold, the ways back)
		if w.narrow and not w.gone then
			local nok, nproblem = pcall(NARROW.step, w)
			if not nok then pcall(NARROW.fail, w, nproblem) end
		end
		if w.nfg then
			if w.gone then w.nfg = nil else pcall(NARROW.sweep, w) end
		end
	end
	-- the lines of fire of the direct-fire guns (a unit that cannot be read must not stop the walkers' lanes)
	if state.fighting then
		local t_fire = diag.t()
		local fok, fproblem = pcall(FIRE.run)
		diag.add('fire', t_fire)
		if not fok then
			state.fire_errors = (state.fire_errors or 0) + 1
			if state.fire_errors <= 3 then log(('ERROR in the line of fire (%d so far): %s'):format(state.fire_errors, tostring(fproblem))) end
			if state.fire_errors >= 5 then state.cfg.fire = false; log('the line of fire is switched off for this battle (five errors)') end
		end
	end
	-- (not on the ticks of report() / collect_units(), multiples of 25 and 50)
	if state.ticks % 5 == 2 then diag.run_check('missed', diag.missed) end
	local walkers = 0
	for _, entry in ipairs(state.units) do
		if entry.columns_for then walkers = walkers + 1 end
	end
	if walkers ~= state.walkers then -- cave 4 looks at marching points only while a walker forms columns
		mr.write(state.tbl, CN.WALKERS, mr.uint32(walkers))
		state.walkers = walkers
	end
end

-- the finest step os.clock can show now (the game's Lua keeps 24 bits, so after 2.3 h of play the clock moves in 1 ms
-- steps, after 9 h in 4 ms steps; the times per look are rough)
local function clock_step(t)
	if state.single and t and t > 0 then
		local _, e = math.frexp(t)
		return ('%.2f ms (single precision: times per look under that are rough)'):format(2 ^ (e - 24) * 1000)
	end
	return 'fine'
end

-- (test build) a phase of the window in ms per look (the release build does not time)
function diag.per_look(name)
	return RELEASE.on and '-' or ('%.2f'):format((state.ph[name] or 0) * 1000 / REPORT_EVERY)
end

-- the phase that took most of this look (for the longest look of the window)
function diag.top(look)
	local best, name = 0, nil
	for k, v in pairs(look) do
		if k ~= 'serve' and k ~= 'plans' and k ~= 'sends' and k ~= 'columns' and v > best then best, name = v, k end -- (serve holds the sv_ sub-phases: the finer ones count; plans / sends / columns are also inside the line of fire and serve)
	end
	return name and ('%s %.1f ms'):format(name, best * 1000) or '-'
end

local function report()
	local delta = {}
	for _, offset in ipairs(COUNTERS) do
		local count = mr.read_uint32(state.tbl, offset)
		delta[offset] = (count - state.last[offset]) % 4294967296
		state.last[offset] = count
	end
	local open = 0
	for _, entry in ipairs(state.units) do
		if entry.lane > 0 then open = open + 1 end
	end
	log(('%d units with open lanes | in 5 s: models sent at once %d | places handed out by the game %d, shifted %d, left alone (on an object) %d | 5 s tests %d, hurried %d | marching points while columns are on %d, pulled into lanes %d (%d of them into a lane on the minus side) | places from the layout table %d, no record for a model %d | script cost %.2f ms per look (every %d ms), longest %.1f ms | game clock %s'):format(
		open, delta[CN.JOBS_DONE], delta[CN.REACHED], delta[COUNT], delta[CN.HUNG], delta[CN.CHECKS], delta[CN.FORCED], delta[CN.CARROT_SEEN], delta[CN.CARROTS], delta[CN.CARROTS_NEG], delta[CN.PLACE_HIT], delta[CN.PLACE_MISS], state.cost * 1000 / REPORT_EVERY, TICK_MS, state.longest * 1000,
		(state.clock and state.report_clock) and ('%.1f game s in the last %d looks'):format(((state.clock - state.report_clock) % 4294967296) * 0.1, REPORT_EVERY) or 'not readable'))
	pcall(GHOST.report)
	-- how the game keeps up: real seconds (os.clock is wall time on Windows, 1 ms steps) against game seconds, and the
	-- mod's own script time. A battle script cannot see the frame rate; the caves are not timed (they leave after one
	-- compare while there is nothing to do)
	local real_now = os.clock()
	local real = state.report_real and (real_now - state.report_real) or nil
	local game = (state.clock and state.report_clock) and ((state.clock - state.report_clock) % 4294967296) * 0.1 or nil
	local walkers, open = 0, 0
	for _, entry in ipairs(state.units) do
		if entry.columns_for then walkers = walkers + 1 end
		if entry.lane > 0 then open = open + 1 end
	end
	log(('PERF %s real s for %s game s (%s) | mod script %.1f ms in all = %s of the real time, %.2f ms per look (diagnostics %.1f ms in all), longest %.1f ms, looks over %d ms: %d | %d units listed, %d with open lanes, %d walkers forming columns, %d plans | closing sends put off %d | ms per look by phase: look %s, serve %s (cand %s, open %s, plans %s, sends %s, columns %s, near %s, inside %s, checks %s, widen %s, rest %s), the line of fire %s, diagnostics %s, log %s, collect / report %s; the longest look was in: %s | clock step %s'):format(
		real and ('%.2f'):format(real) or '?', game and ('%.1f'):format(game) or '?',
		(real and game and real > 0) and (('game speed %.0f %%'):format(100 * game / real) .. (game < 0.8 * real and ' - paused or the game cannot keep up' or '')) or 'speed unknown',
		state.cost * 1000, (real and real > 0) and ('%.2f %%'):format(100 * state.cost / real) or '?', state.cost * 1000 / REPORT_EVERY, (state.diag_cost or 0) * 1000, state.longest * 1000,
		SLOW_LOOK * 1000, state.slow_looks or 0, #state.units, open, walkers, state.plans or 0, state.deferred, diag.per_look('look'), diag.per_look('serve'),
		diag.per_look('sv_cand'), diag.per_look('sv_open'), diag.per_look('sv_plans'), diag.per_look('sv_sends'), diag.per_look('sv_columns'), diag.per_look('sv_near'),
		diag.per_look('sv_inside'), diag.per_look('sv_checks'), diag.per_look('sv_widen'), diag.per_look('sv_rest'), diag.per_look('fire'), diag.per_look('diag'), diag.per_look('log'), diag.per_look('collect'),
		state.longest_phase or '-', clock_step(real_now)))
	state.report_real, state.slow_looks, state.plans, state.diag_cost, state.ph, state.deferred, state.longest_phase = real_now, 0, 0, 0, {}, 0, nil
	state.cost, state.longest, state.report_clock = 0, 0, state.clock
end

-- the whole battle at the end: the same numbers in all
local function perf_summary()
	local p = state.perf
	if not p or not p.real0 then return end
	local real = os.clock() - p.real0
	log(('PERF BATTLE %.0f real s, %.0f game s with the mod on | mod script %.0f ms in all = %.2f %% of the real time, %.2f ms per look in %d looks, the longest %.1f ms (%.0f s after the fighting began), looks over %d ms: %d; the worst 5 s: %.2f ms per look; clock step %s'):format(
		real, state.game_seconds or 0, p.cost * 1000, real > 0 and 100 * p.cost / real or 0, p.looks > 0 and p.cost * 1000 / p.looks or 0, p.looks,
		p.longest * 1000, p.longest_at or 0, SLOW_LOOK * 1000, p.slow, p.worst * 1000, clock_step(os.clock())))
end

-- start -----------------------------------------------------------------------------------------------------------

local function guarded(name, action)
	return function()
		if state.dead then return end
		local ok, problem = pcall(action)
		if not ok then
			give_up(name .. ': ' .. tostring(problem))
			pcall(function() bm:remove_callback(TIMER) end)
		end
	end
end

-- where the current leadership lies in a Unit (state.lead_at); without it steadiness() stays x1 (wavering x0.5) and the
-- mod works as before
function LEAD.find()
	local function number(pattern, mark, name)
		local address, count = mr.find_pattern(pattern)
		if not address or count ~= 1 then error(('the %s code was found %s times, expected 1'):format(name, tostring(count)), 0) end
		local n = signed_word(mr.read_original(mr.add(address, mark), 0, 4))
		if n < 0x100 or n > 0xFFFF then error('the ' .. name .. ' code holds an odd number', 0) end
		return n
	end
	local block = number(LEAD.route, LEAD.route_at, 'is_routing')
	local waver = number(LEAD.waver, LEAD.waver_at, 'is_wavering')
	if waver ~= block + LEAD.state_in_block then error(('S 0x%x is not B 0x%x + 0x40'):format(waver, block), 0) end
	state.lead_at = block + LEAD.current_in_block
	return block
end

local function start()
	log('Military Drill --VERSION----BUILD_NOTE--, battle script loaded')
	mr = _G.memreader_plus
	if not mr then error('memreader Plus is not loaded', 0) end
	install()
	local lok, lead = pcall(LEAD.find)
	if lok then
		log(('leadership read at unit + 0x%x (morale block 0x%x); x1 = leadership %d'):format(state.lead_at, lead, LEAD.norm))
	else
		log('LEADERSHIP UNAVAILABLE (every unit counts as x1, wavering x0.5): ' .. tostring(lead))
	end
	log(('Lua numbers: %s precision'):format(state.single and 'SINGLE' or 'double'))
	log(('memreader Plus %s, table at %s; lanes are open %.2f s before the walker arrives, 3 blocks (2 lanes; 5 blocks from %.0f m front, 7 from %.0f m); walker columns %s'):format(tostring(mr.plus_version), hex(state.tbl), SPLIT_MARGIN, WIDE_FRONT, WIDER_FRONT, state.columns and 'ON' or 'OFF'))
	-- the units are listed already now (lanes closed, caves off): cave 1 notes what a place of each unit looks
	-- like when the game hands places out during deployment
	local early = pcall(collect_units)
	log(('listed before the fighting: %d units%s'):format(#state.units, early and '' or ' (not possible yet)'))
	bm:register_phase_change_callback('Deployed', guarded('deployed', function()
		read_settings()
		collect_units()
		mr.write(state.tbl, GHOST.SKIPS, mr.uint32(0)) -- (the counter starts at 0 for this battle)
		GHOST.total = 0
		pcall(GHOST.refresh)
		mr.write(state.tbl, ENABLE, mr.uint32(1))
		state.fighting = true
		state.perf = { real0 = os.clock(), looks = 0, cost = 0, longest = 0, slow = 0, worst = 0 }
		state.report_real = state.perf.real0
		log(('fighting starts, mod ON: %d units, %d of them not in the vanilla tables (other mods: kind from the script interface)'):format(#state.units, state.guessed or 0))
	end))
	bm:repeat_callback(guarded('tick', function()
		if not state.fighting then return end
		local started = os.clock()
		state.in_tick, state.sent_look, state.ph_look = true, 0, {} -- (the log lines of the look wait for its end; the sends of the look are counted)
		state.ticks = state.ticks + 1
		if state.ticks % COLLECT_EVERY == 0 then
			local t_collect = diag.t()
			collect_units()
			pcall(NARROW.mct_check)
			pcall(GHOST.refresh)
			diag.add('collect', t_collect)
		end
		state.report_clock = state.report_clock or state.clock
		update()
		local t_log = diag.t()
		RELEASE.flush() -- (the report below is written with the next look's lines: one file open per look)
		diag.add('log', t_log)
		local took = os.clock() - started
		state.cost = state.cost + took
		if took > state.longest then state.longest, state.longest_phase = took, diag.top(state.ph_look) end
		local p = state.perf
		p.looks, p.cost = p.looks + 1, p.cost + took
		if took > p.longest then p.longest, p.longest_at = took, state.game_seconds end
		if took > SLOW_LOOK then state.slow_looks, p.slow = (state.slow_looks or 0) + 1, p.slow + 1 end
		if state.ticks % REPORT_EVERY == 0 then
			p.worst = math.max(p.worst, state.cost / REPORT_EVERY)
			local t_report = diag.t()
			report()
			diag.add('collect', t_report) -- (in the next window: report() starts it)
		end
	end), TICK_MS, TIMER)
	bm:register_phase_change_callback('Complete', function()
		if state.dead then return end
		state.dead = true
		state.in_tick = false -- (the last lines are written at once, with whatever waits)
		pcall(perf_summary)
		pcall(diag.shape_summary)
		pcall(function() -- 085e: the totals; the narrowed walkers' state is only cleared (the battle is over: no orders)
			local n = mr.read_uint32(state.tbl, GHOST.SKIPS)
			GHOST.total = GHOST.total + n
			if GHOST.units and #GHOST.units > 0 then log(('GHOST: %s ghost pairs passed in all'):format(GHOST.text())) end
			local st = NARROW.stats
			if st.narrowed > 0 or st.forgotten > 0 or st.gave_up > 0 then log(('NARROW: %d walkers narrowed, %d restored, %d forgotten (re-ordered), %d given up'):format(st.narrowed, st.restored, st.forgotten, st.gave_up)) end
			for _, e in ipairs(state.units) do e.narrow, e.nfg = nil, nil end
		end)
		pcall(switch_off)
		pcall(function() bm:remove_callback(TIMER) end)
		log(('battle complete, mod off; lanes were opened %d times'):format(state.opened))
	end)
end

local ok, problem = pcall(start)
if not ok then give_up(problem) end
