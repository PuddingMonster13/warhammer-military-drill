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
local VECTOR_MAX_FILES = 8
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
	PLAN_FAR = 3.0, -- s: a standing unit whose last plan left more margin than this plans again only every
	PLAN_EVERY = 3, -- this many looks (while its nearest walker and that one's order stay the same)
	LATE_SHARE = 0.5, -- a walker whose lane takes the whole standing front gets none when its contact comes before this
	-- share of the split (refuse only when hopeless - run-42 guns 2.4 / 6.4 s refused, bloodcrushers 5.0 / 8.3 s open)
	LATE_CLEAR = 10.0, -- m: a walker refused as too late stays left out until it is this far clear of the unit (run 43)
	TURN_COS = 0.01, -- a walker whose cos to its order line grows by more than this per look is turning onto it
	PROBE_EVERY = 5, -- looks: a unit's first model is read this often as a baseline for teleports (see look)
	WATCH_TICKS = 10, -- looks: after unit:position() jumped, its first model is read on each look this long
	CREW_EVERY = 25, -- (log) looks: how far the crewmen of artillery stand from their machines is looked at this often
	CREW_AWAY = 8.0, -- (log) m: a crewman this far from its machine counts as away from it
	MISSED_DEPTH = 6.0, -- (log) m: a walker whose middle is this near a standing unit's middle line is inside it
	MISSED_KEEP = 150, -- (log) looks: the same walker inside the same unit is logged again after this long
	MISSED_LINES = 10, -- (log) MISSED lines at most per check (then "N more")
	HELD_TICKS = 20, -- a unit that does not move but is not idle either (the AI holds it) stands after this many looks
	HELD_DRIFT = 5.0, -- m: ... while its position stays this near where it stopped (unit:position() jitters 2-9 m)
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
local E_OX, E_OZ, E_AX, E_AZ, E_P, E_L, E_N, E_DIRTY, E_WAIT, E_BIAS = --E_OX--, --E_OZ--, --E_AX--, --E_AZ--, --E_P--, --E_L--, --E_N--, --E_DIRTY--, --E_WAIT--, --E_BIAS--
local E_TPL, E_C5C, E_C63, E_SEQ, E_PENDING, E_JOBS, E_NJOBS = --E_TPL--, --E_C5C--, --E_C63--, --E_SEQ--, --E_PENDING--, --E_JOBS--, --E_NJOBS--
local JOB, JOB_RECORD, CAP, POOL_SIZE = --JOB--, --JOB_RECORD--, --CAP--, --POOL_SIZE--
local CARROT_RET, HALF = --CARROT_RET--, --HALF--
CN.CARROTS, CN.CARROT_SEEN, CN.CARROTS_NEG = --CARROTS--, --CARROT_SEEN--, --CARROTS_NEG--
local E_WQ = --E_WQ--
local EW = { OX = --E_WOX--, OZ = --E_WOZ--, AX = --E_WAX--, AZ = --E_WAZ--, BX = --E_WBX--, BZ = --E_WBZ--, T0 = --E_WT0--, T1 = --E_WT1--,
	C = --E_WC--, KL = --E_WKL--, KH = --E_WKH--, SL = --E_WSL--, SH = --E_WSH--, LAG = --E_WLAG--, MIN = --E_WMIN-- }
local E_G, E_S0 = --E_G--, --E_S0--
local WALKERS, JOBS_ANY = --WALKERS--, --JOBS_ANY--
local CARROT_SITE = --CARROT_SITE--
local P_UID = '--P_UID--'
local UID_OFF = --UID_OFF--
-- end of generated part -------------------------------------------------------------------------------------------

--FIND_SITE--

local NEEDED = { 'find_pattern', 'hook_info', 'code_cave', 'game_alloc', 'alloc', 'read', 'read_original', 'write', 'read_pointer', 'read_uint32', 'read_float', 'ud_topointer', 'add', 'is_null', 'tostring', 'uint32' }
local COUNTERS = { COUNT, CN.REACHED, CN.HUNG, CN.CHECKS, CN.FORCED, CN.JOBS_DONE, CN.CARROTS, CN.CARROT_SEEN, CN.CARROTS_NEG }

local mr = nil
local state = { dead = false, tbl = nil, pool = nil, uid_offset = nil, buffer = nil, started = false, units = {}, slots = 0, ticks = 0, cfg = { enabled = true, lanes = 'auto', expendable = false, wavering = true, logging = 'off' }, clock = nil, dt = TICK_MS / 1000, fighting = false, last = {}, opened = 0, cost = 0, longest = 0 }

-- the release build writes no file of its own: the MCT option "Logging" (level 0 off, the default / 1 problems / 2
-- basic) sends lines to the game's own mod log (lua_mod_log.txt). Problems: the start, switched off and why, script
-- errors, units left out. Basic: also the settings, every opening / closing / refusal / miss as one short line, and the
-- battle's cost at the end. The full log is the test build (lanes.py build without --release). Lines before the
-- settings are read wait in a short buffer.
local RELEASE = { on = --RELEASE--, level = 0, decided = false, buffer = {},
	keep = { { 'Military Drill', 1 }, { 'MOD OFF', 1 }, { 'ERROR', 1 }, { 'LEFT OUT', 1 }, { 'WALKER COLUMNS OFF', 1 },
		{ 'memreader Plus', 2 }, { 'settings', 2 }, { 'fighting starts', 2 }, { 'unit of another mod', 2 }, { 'OPEN', 2, true },
		{ 'CLOSE ', 2, true }, { 'NO LANES', 2, true }, { 'WIDER', 2, true }, { 'MISSED?', 2, true }, { 'PERF BATTLE', 2 } },
	LEVELS = { off = 0, problems = 1, basic = 2 } }

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
	local file = io.open(LOG_FILE, state.started and 'a' or 'w')
	state.started = true
	if not file then return end
	file:write(os.date('%H:%M:%S') .. ' ' .. tostring(message) .. '\n')
	file:close()
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
	mr.write(state.tbl, WALKERS, mr.uint32(0))
	mr.write(state.tbl, JOBS_ANY, mr.uint32(0))
	for slot = 0, MAX_UNITS - 1 do
		mr.write(state.tbl, LIST + ENTRY * slot + E_L, mr.uint32(0))
		mr.write(state.tbl, LIST + ENTRY * slot + E_PENDING, mr.uint32(0))
		mr.write(state.tbl, LIST + ENTRY * slot + E_WQ, mr.uint32(0))
	end
end

local function give_up(reason)
	state.dead = true
	pcall(switch_off)
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
		mr.write(state.tbl, WALKERS, mr.uint32(0))
		mr.write(state.tbl, JOBS_ANY, mr.uint32(0))
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
	mr.write(state.tbl, CARROT_RET, carrot_ret)
	put_float(HALF, 0.5)
	state.columns = true
	for _, site in ipairs(SITES) do
		if site.skip then state.columns = false end
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
	entry.gone, entry.stands, entry.walks, entry.lane, entry.jobs_at = true, false, false, 0, nil
	mr.write(state.tbl, entry.at + E_L, mr.uint32(0))
	mr.write(state.tbl, entry.at + E_PENDING, mr.uint32(0))
	mr.write(state.tbl, entry.at + E_WQ, mr.uint32(0))
	mr.write(state.tbl, entry.at, '\1\0\0\0\0\0\0\0')
	if entry.columns_for then entry.columns_for.columns_of, entry.columns_for = nil, nil end
	local w = entry.columns_of
	if w then
		mr.write(state.tbl, w.at + E_WQ, mr.uint32(0))
		w.columns_for, entry.columns_of = nil, nil
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
			local list = armies:item(b):units()
			for index = 1, list:count() do
				local unit = list:item(index)
				local ok, pointer = pcall(unit_pointer, unit)
				if ok and pointer and not known[hex(pointer)] and state.slots < MAX_UNITS then
					local entry = { key = hex(pointer), unit = unit, pointer = pointer, alliance = a, shot_at = -1000, lane = 0, quiet = 0, closed_at = -100000,
						label = ('A%d.%d.%d %s'):format(a, b, index, tostring(unit:type())), at = LIST + ENTRY * state.slots,
						jobs = mr.add(state.pool, state.slots * CAP * JOB), machine = yes(unit, 'is_war_machine'),
						expendable = EXPENDABLE[tostring(unit:type())] == true or yes(unit, 'is_expendable'), kind = KIND[tostring(unit:type())] }
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

-- one check under pcall: its time counts apart (PERF "diagnostics"), its first error is logged once (a check that fails
-- silently looks like a check that found nothing)
function diag.run_check(name, check, arg)
	if RELEASE.on and not (name == 'missed' and RELEASE.level >= 2) then return end
	local started = os.clock()
	local ok, problem = pcall(check, arg)
	state.diag_cost = (state.diag_cost or 0) + (os.clock() - started)
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
	entry.stands = still and not busy and state.ticks - entry.still_since >= STILL_TICKS and entry.men >= MIN_MEN
	-- the settings (MCT): who makes way at all
	local cfg = state.cfg
	-- by type: lords and heroes never make way, every other unit does, however few models it has left
	if entry.stands and (entry.character or not cfg.enabled or (cfg.expendable and entry.expendable) or (cfg.wavering and yes(unit, 'is_wavering'))) then
		entry.stands = false
		-- (once per unit, for the log: run 34's Gnoblars never made way for the Ironblaster, and nothing said why)
		why = entry.character and 'a lord or hero (they never make way)' or (not cfg.enabled and 'the mod is switched off (MCT)')
			or ((cfg.expendable and entry.expendable) and 'it is expendable (MCT: expendable units make no way)') or 'it is wavering (MCT: wavering units make no way)'
		if not entry.character and not entry.cfg_said then
			entry.cfg_said = true
			log(('  %s makes no way: %s'):format(entry.label, not cfg.enabled and 'the mod is switched off (MCT)'
				or ((cfg.expendable and entry.expendable) and 'it is expendable, and the MCT setting says expendable units make no way'
				or 'it is wavering (MCT: wavering units make no way)')))
		end
	end
	-- a walker may have models in a fight (a few caught by enemies): it walks if it really moves (below)
	entry.walks = entry.moving and not routing and entry.men >= 1 and entry.lane == 0 and not yes(unit, 'is_currently_flying')
	if entry.columns_for and not entry.walks then -- it stopped or was ordered elsewhere: no columns
		mr.write(state.tbl, entry.at + E_WQ, mr.uint32(0))
		entry.columns_for.columns_of, entry.columns_for = nil, nil
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
			entry.still_since, entry.stands = state.ticks, false -- it jumped (a teleport): it has to stand STILL_TICKS from now
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
			why = ('it has stood only %.1f s of %.1f'):format((state.ticks - entry.still_since) * TICK_MS / 1000, STILL_TICKS * TICK_MS / 1000)
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
		if melee and not (measured and v >= MELEE_WALK) then
			entry.walks = false -- in a fight and not going anywhere (its models chase their enemies)
		elseif length >= 2 and (not measured or state.ticks - (entry.order_at or -1000) < TUNE.TURN_TICKS or entry.columns_for
			or state.ticks - (entry.crossing_at or -1000) <= 1 or (entry.off_order or 0) < TUNE.OFF_LOOKS) then
			entry.dx, entry.dz, entry.way, entry.heading = dx / length, dz / length, length, 'order'
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

-- where the models of a unit stand now: { model = { x, y, z } } (models that cannot be read are left out)
local function model_places(entry)
	if entry.places_tick == state.ticks and entry.places then return entry.places end -- the models do not move within a look
	local places = {}
	local count, models = models_of(entry)
	if not count then return places end
	for index = 0, count - 1 do
		local model = mr.read_pointer(models, index * 8)
		if not mr.is_null(model) then
			local x, y, z = get_xyz(model)
			if x and y and z then places[hex(model)] = { x, y, z } end
		end
	end
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

-- the bodies of a unit that really walk: a rider's or crewman's carrier (mount, wheel, machine) instead of the model.
-- Returns { { x, z } } and how many models ride a carrier (once per look)
local function bodies_of(w)
	if w.bodies_tick == state.ticks and w.bodies then return w.bodies, w.riders end
	local list, seen, riders = {}, {}, 0
	local n, models = models_of(w)
	for index = 0, (n or 0) - 1 do
		local model = mr.read_pointer(models, index * 8)
		if not mr.is_null(model) then
			local carrier = mr.read_pointer(model, 0x2e0)
			if not mr.is_null(carrier) and hex(mr.read_pointer(carrier, 0x2e8)) == w.key then model, riders = carrier, riders + 1 end
			local key = hex(model)
			if not seen[key] then
				seen[key] = true
				local x, _, z = get_xyz(model)
				if x and z and near_unit(w, x, z) then list[#list + 1] = { x, z } end
			end
		end
	end
	w.bodies, w.riders, w.bodies_tick = list, riders, state.ticks
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
	if w.kind == 'veh' or w.kind == 'art' or w.kind == 'mi' or w.kind == 'mc' then return true end
	return one_body(w)
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
local function place_of(lanes, x, z, key)
	if not lanes.art then return shifted(lanes, x, z) end
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
	return lanes.shift or (lanes[8] ~= 0 and lanes[6] / 2 or lanes[6] * lanes[7])
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
local function send_stations(s, lanes, wait)
	mr.write(state.tbl, s.at + E_PENDING, mr.uint32(0)) -- older jobs are void; nobody looks at the jobs while they are written
	local tpl, why = template(s)
	if not tpl then return 0, why end
	local count, models = models_of(s)
	if not count then return 0, 'its models cannot be read' end
	if not s.base then
		if not lanes then return 0, 'no places noted' end
		s.base, s.base_men = {}, s.men
	end
	local jobs, tick = 0, nil
	local job_models = {}
	s.job_models = job_models
	s.via_mount = 0
	local given = {} -- one job per walking body: the riders of one carrier share it
	for index = 0, count - 1 do
		if jobs >= CAP then break end
		local model = mr.read_pointer(models, index * 8)
		local key = nil
		if not mr.is_null(model) then
			-- a rider does not walk by itself (its tick skips the station code): its mount gets the job (run 11)
			local carrier = mr.read_pointer(model, 0x2e0)
			local mounted = not s.crewed and not mr.is_null(carrier) and hex(mr.read_pointer(carrier, 0x2e8)) == s.key
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
			if base and lanes then x, z = place_of(lanes, x, z, key) end
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
	mr.write(state.tbl, s.at + E_NJOBS, mr.uint32(jobs))
	mr.write(state.tbl, s.at + E_PENDING, mr.uint32(jobs))
	if jobs > 0 and not state.jobs_any then -- cave 3 looks for jobs from now on (update switches it back)
		mr.write(state.tbl, JOBS_ANY, mr.uint32(1))
		state.jobs_any = true
	end
	s.jobs_at = state.ticks
	return jobs, tpl
end

-- is walker w about to pass through standing unit s? Returns how far ahead of w the middle of s is, or nil
local function passing(w, s)
	local rx, rz = s.x - w.x, s.z - w.z
	local along = rx * w.dx + rz * w.dz
	local across = math.abs(rx * w.dz - rz * w.dx)
	if across > s.half + w.half * 0.5 then return nil end -- walks past it
	if along < -(s.half + 5) then return nil end -- already behind the walker
	if along > w.way + s.half then return nil end -- the walker stops before it
	if along > s.half + w.speed * LOOK_SECONDS + 5 then return nil end -- still far away
	return along
end

-- how steady a unit is: x1 normally. The script interface has no leadership value (only is_wavering / is_routing);
-- a wavering unit counts as x0.5. TODO: read the real leadership (current / 100) once its place in memory is known.
local function steadiness(entry)
	if yes(entry.unit, 'is_wavering') then return 0.5 end
	return 1.0
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
-- Each unit may use half the gap to its neighbour. Returns G (0 .. -MAX_CLOSE_UP) and the two gaps (nil = none).
local function room_of(own, others, need, reach)
	local lo, hi, tlo, thi = nil, nil, nil, nil
	for _, p in ipairs(own) do
		if not lo or p[2] < lo then lo = p[2] end
		if not hi or p[2] > hi then hi = p[2] end
		if not tlo or p[1] < tlo then tlo = p[1] end
		if not thi or p[1] > thi then thi = p[1] end
	end
	if not lo then return 0, nil, nil end
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
	local g = 0
	for _, side in ipairs({ { right, hi }, { left, -lo } }) do
		local gap, edge = side[1], side[2]
		if gap and edge and edge >= 1 then
			local free = math.max(0, gap / 2 - ROOM_MARGIN)
			if free < need then g = math.min(g, (free - need) / edge) end
		end
	end
	return math.max(-MAX_CLOSE_UP, g), left, right
end
-- [room part: tested on its own by the mock test]

-- room for the lanes of standing unit s: fills in lanes[9] (G) and lanes[10] (S0); returns a text for the log
local function make_room(s, w, lanes, own_places)
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
		if o ~= s and o ~= w and not o.gone and not (o.walks and passing(o, s)) and not (o.stands and w.walks and w.dx and passing(w, o))
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
	local g, left, right = room_of(own, others, need, reach + wide)
	lanes[9] = g
	local function gap(x) return x and ('%.1f m'):format(x) or 'free' end
	return ('beside it: %s / %s, %s'):format(gap(left), gap(right), g < 0 and ('closes up to %.0f %% of its width'):format((1 + g) * 100) or 'room enough')
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
	-- chariots, war machines, monstrous infantry / cavalry and artillery (KIND, from the game's tables) keep their
	-- formation: one lane per file of their walking bodies (the machines, not the crews), or, when the files are too close
	-- for a standing file between them, one wide lane for the whole walker
	local kind = w.kind
	local keeps = kind == 'veh' or kind == 'art' or kind == 'mi' or kind == 'mc'
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
			local block = files >= 2 and (spacing - lane) or -1
			if files >= 2 and files <= VECTOR_MAX_FILES and block >= 2 * model_radius(s) + 0.2 then
				-- the files are spacing apart, centred on the walker's middle: lanes at +-(k + 0.5) spacing (even
				-- number of files), or at k * spacing (odd: the origin moves half a file to the minus side)
				local n = math.ceil(files / 2)
				local ox, oz = cx, cz
				if files % 2 == 1 then ox, oz = cx - ax * spacing / 2, cz - az * spacing / 2 end
				w.formation = ('%s walker of %d bodies: %d files x %d ranks, %.1f m apart -> it keeps its formation, one lane per file'):format(kind, #bodies, files, ranks, spacing)
				local lanes = { ox, oz, ax, az, block, lane, n, 0 }
				lanes.vector = true
				return lanes, radius, carried
			end
			-- one wide lane for its whole front, centred on its bodies
			local mid = (amin + amax) / 2
			local wide = math.min(LANE_WIDE_MAX, amax - amin + lane)
			w.formation = ('%s walker of %d bodies: %d files %.1f m apart, too close for a standing file between them -> it keeps its formation, one lane of %.1f m for its whole front'):format(kind, #bodies, files, spacing or 0, wide)
			local mx, mz = cx + ax * mid, cz + az * mid
			local lanes = { mx - ax * 50000, mz - az * 50000, ax, az, 100000, wide, 1, -0.5 }
			lanes.vector = true
			lanes.whole_front = s.half and wide >= 2 * s.half -- (the whole standing unit has to step aside)
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
	local n = front > WIDER_FRONT and 3 or (front > WIDE_FRONT and 2 or 1)
	if state.cfg.lanes == '2' then n = 1 elseif state.cfg.lanes == '4' then n = 2 elseif state.cfg.lanes == '6' then n = 3 end -- MCT
	-- the lane is as wide as the files a column needs: its unit's files shared among the lanes, so that no column
	-- gets deeper than COLUMN_DEPTH times the unit's ranks
	local lane
	local ok, files, ranks, spacing = pcall(function() return formation(near_places(w), w.dx, w.dz, w.men) end)
	if ok and files then
		local per = math.max(LANE_FILES, math.ceil(files / (2 * n * COLUMN_DEPTH)))
		lane = (per - 1) * spacing + 2 * radius + LANE_EXTRA
		w.formation = ('walker %d files x %d ranks, %.1f m apart -> columns %d files wide'):format(files, ranks, spacing, per)
	else
		lane = math.max(2 * radius * LANE_FILES + LANE_EXTRA, COLUMN_SHARE * 2 * w.half / (2 * n))
		w.formation = 'walker formation not readable -> lanes 0.4 of its front'
	end
	if w.crewed or w.machine then lane = math.max(lane, 2 * (carried or 1.5) + 2 * CREW_MARGIN) end
	lane = math.max(LANE_MIN, math.min(LANE_MAX, lane))
	if artillery then
		local lanes, why = artillery_lanes(s, cx, cz, ax, az, lane, math.max(LANE_MIN, 2 * radius + LANE_EXTRA), 2 * radius + 0.2)
		if not lanes then return nil, why end
		return lanes, radius, carried
	end
	-- equal blocks; but the outermost lanes must still lie inside the walker's front
	local block = math.max(BLOCK_MIN, math.min(front / (2 * n + 1), w.half / (n - 0.5) - lane))
	return { cx, cz, ax, az, block, lane, n, 0 }, radius, carried
end

-- when to split: seconds until the walker's front reaches the standing unit, and how long the split takes
local function timing(s, w, lanes)
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
	-- the pace follows the walker: a running walker -> the models of s run to their lane places in one step; a walking one
	-- -> they walk, in steps under RUN_BEYOND, at the pace this unit showed at its last split (check_pace) times the
	-- game's walking speed. One look per step at least.
	local slow = s.unit:slow_speed()
	if type(slow) ~= 'number' or slow < 0.3 or slow > 40 then slow = 1.5 end
	local w_slow = w.unit:slow_speed()
	local running = yes(w.unit, 'is_moving_fast') or (type(w_slow) == 'number' and w_slow > 0.3 and speed > 1.3 * w_slow)
	local s_speed = slow * (s.pace or 1)
	local steps = math.max(1, math.ceil(shift / STEP_MAX - 1e-9))
	local step_time = (shift / steps) / s_speed + look
	local t_split = wait + steps * step_time
	-- a running walker, or one that is already close (the standing unit had only just stopped; run 23: lanes opened
	-- 1-17 s after the walker was inside): everything in one step, at a run when it is further than RUN_BEYOND
	if (running or gap / speed < t_split + SPLIT_MARGIN) and steps > 1 then
		local fast = s.unit:fast_speed()
		if shift > RUN_BEYOND and type(fast) == 'number' and fast > 0.3 and fast < 40 then s_speed = fast end
		steps = 1
		step_time = shift / s_speed + look
		t_split = wait + step_time
	end
	return gap / speed, t_split, gap, speed, s_speed, steps, step_time
end

-- the MCT settings (when MCT is installed); in battle they are there when the fighting starts
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
		v = get('logging') if RELEASE.LEVELS[v] then cfg.logging = v end
	end)
	-- (release) from now on the MCT option decides; the lines that waited are written or dropped
	RELEASE.decide(RELEASE.LEVELS[cfg.logging] or 0)
	log(('settings%s: enabled %s (for every army), lanes %s, expendable units make way %s, wavering units make way %s, logging %s'):format(
		ok and ' (MCT)' or (' (defaults: ' .. tostring(problem) .. ')'), tostring(cfg.enabled),
		cfg.lanes, cfg.expendable and 'NO' or 'yes', cfg.wavering and 'NO' or 'yes (at half speed)', tostring(cfg.logging)))
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

-- the walking unit forms columns (cave 4): its points are pulled into the lanes while they cross the standing unit
local function start_columns(s, w, lanes, radius, gap)
	if lanes.vector then return 'no columns (the walker keeps its formation: one lane per file)' end
	if not state.columns or (lanes[8] ~= 0 and not lanes.one_column) then return 'no columns' .. (state.columns and ' (one lane: not needed)' or ' (cave 4 is off)') end
	if w.columns_for and w.columns_for ~= s then return 'no columns (the walker forms them for another unit)' end
	local lo, hi = nil, nil
	local ok, places = true, s.stood
	if not places then ok, places = pcall(near_places, s) end
	for _, p in pairs(ok and places or {}) do
		local t = (p[1] - lanes[1]) * w.dx + (p[3] - lanes[2]) * w.dz
		if not lo or t < lo then lo = t end
		if not hi or t > hi then hi = t end
	end
	if not lo then return 'no columns (standing unit not readable)' end
	-- across the way: only the walker's points in front of the standing unit (as it stands with its lanes open) are
	-- pulled; files that pass beside it keep their places
	local fx, fz = column_frame(lanes)
	local sl, sh = nil, nil
	for key, p in pairs(ok and places or {}) do
		local x, z = place_of(lanes, p[1], p[3], key)
		local a = (x - fx) * lanes[3] + (z - fz) * lanes[4]
		if not sl or a < sl then sl = a end
		if not sh or a > sh then sh = a end
	end
	local reach = model_radius(s) + radius + LANE_EXTRA
	sl, sh = sl - reach, sh + reach
	local ox, oz, q, kl, kh = column_frame(lanes)
	local squeeze = math.max(0, math.min(0.5, (lanes[6] - 2 * radius) / q))
	local outer = (lanes[7] - 0.5) * q -- the centre of the outermost lane
	local step = math.max(q / 2, w.half - outer) -- the widest sideways step of a walker model into its lane
	-- the walker starts forming its columns together with the split (not earlier: run 14, the walker's formation changed
	-- before the standing unit split): from where its front is now
	local t0 = lo - math.max(COLUMN_LEAD, gap or (COLUMN_LEAD + w.speed * SPLIT_MARGIN + step))
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
	return ('columns: %d columns every %.1f m, files squeezed to %.0f %% of their strip, from %.1f m before to %.1f m behind the standing unit, %.1f .. %.1f m across (beside it: free)'):format(kh - kl + 1, q, squeeze * 100, lo - t0, t1 - hi, sl, sh)
end

local function stop_columns(s)
	local w = s.columns_of
	if w and w.columns_for == s then
		mr.write(state.tbl, w.at + E_WQ, mr.uint32(0))
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

local function next_step(s, no_pace)
	if not no_pace then pcall(check_pace, s) end
	s.step = s.step + 1
	local cur = step_lanes(s, s.step)
	put_float(s.at + E_G, cur[9] or 0)
	put_float(s.at + E_L, cur.art and 0 or cur[6]) -- artillery: its machines stay, the game's own places are left alone
	put_float(s.at + E_BIAS, cur[8])
	if not cur.art then mr.write(state.tbl, s.at + E_DIRTY, mr.uint32(1)) end
	s.step_at = state.game_seconds
	return send_stations(s, cur, 0)
end

local function open_lanes(s, w, lanes, radius, carried, t_contact, t_split, gap, speed, s_speed, steps, step_time)
	local ax, az, lane = lanes[3], lanes[4], lanes[6]
	local room_ok, room = pcall(make_room, s, w, lanes) -- fresh: where the units beside it stand now
	if not room_ok then lanes[9], lanes[10], room = 0, lanes[8] ~= 0 and 50000 or 0, 'beside it: not readable (' .. tostring(room) .. ')' end
	-- lanes per file (and lanes of their own) lie where the walker's files cross: closing up (G < 0) would draw them
	-- inwards, so the blocks get wider by as much (the period P (1 + G) + L stays)
	if lanes.vector and (lanes[9] or 0) < 0 then lanes[5] = lanes[5] / (1 + lanes[9]) end
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
	-- the walker may be given new places by the game while it passes (a regroup that undoes the columns). Cave 1 counts
	-- every place the game hands out in a listed unit (E_SEQ): noted now, compared later
	s.walker_seen, s.walker_seq, s.walker_new = w, mr.read_uint32(state.tbl, w.at + E_SEQ), 0
	local columns = start_columns(s, w, lanes, radius, gap) -- the walker forms its columns from now on
	if (lanes[8] == 0 or lanes.vector or lanes.one_column or lanes.one_body) and w.formation then columns = w.formation .. '; ' .. columns end
	columns = columns .. ('; split in %d steps of %.1f m, one every %.1f s; walker heading from its %s'):format(s.steps, s.shift / s.steps, s.step_time, tostring(w.heading))
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
end

local function close_lanes(s, why)
	local moved = moved_since(s)
	mr.write(state.tbl, s.at + E_PENDING, mr.uint32(0)) -- jobs still waiting (a crew's repeated ones) are void
	mr.write(state.tbl, s.at + E_L, mr.uint32(0))
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
	s.lane, s.lanes, s.stood, s.goal, s.closed_at, s.steps, s.step = 0, nil, nil, nil, state.ticks, nil, nil
	local regroup = s.walker_seen and ('; the walker was given %d places by the game meanwhile'):format(s.walker_new or 0) or ''
	s.walker_seen, s.widened_for, s.widen_said, s.served, s.closing, s.parked, s.crew_moved, s.crew_post = nil, nil, nil, nil, nil, nil, nil, nil
	log(('CLOSE %s (%s); open for %.1f s; %s%s; %s'):format(s.label, why, (s.open_ticks or 0) * TICK_MS / 1000, moved, regroup,
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
	if not big_walker(w) then return nil, 'not a big walker: it goes through the lanes as they are', true end
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
	local nl, nr = widen(dl, dr, lo, hi, LANE_EXTRA / 2, LANE_WIDE_MAX)
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
	if s.half and new[6] >= 2 * s.half and t_contact and t_contact < TUNE.LATE_SHARE * t_split then
		s.late = s.late or {}
		s.late[w] = { w.order_at, s.gx, s.gz, math.sqrt((w.x - s.x) ^ 2 + (w.z - s.z) ^ 2), w.gx, w.gz }
		-- (not final: s.late leaves it out for this order; a new order may bring it in time)
		return ('too late for a lane as wide as its whole front: contact in %.1f s, the split takes %.1f s'):format(t_contact, t_split), false
	end
	if t_contact and t_contact > t_split + SPLIT_MARGIN + math.max(state.dt, TICK_MS / 1000) then return nil end -- not yet
	local room_ok, room = pcall(make_room, s, w, new, s.stood)
	if not room_ok then new[9], new[10] = 0, 50000 end
	s.lanes, s.lane = new, new[6]
	s.steps, s.step, s.step_time, s.shift = steps or 1, 0, step_time or 0, new.shift
	s.served[w] = true
	log(('WIDER %s for %s too (%d models, radius %.2f, walking %.0f deg across the lanes): the gap %.1f / %.1f m to the sides of the split -> %.1f / %.1f m, ONE lane of %.1f m | %s | split in %d steps, one every %.1f s'):format(
		s.label, w.label, w.men, info.radius, angle_of(info.k), info.dl, info.dr, nl, nr, nl + nr,
		room_ok and tostring(room) or ('beside it: not readable (' .. tostring(room) .. ')'), s.steps, s.step_time))
	next_step(s)
	return 'widened', true
end

-- is walker w still inside standing unit s: one of its bodies between the front of s and CLEAR_MARGIN beyond its back
-- rank (as s stood at the opening), and within its width? Moving or not: one that stops inside keeps the lanes open.
local function still_inside(s, w)
	if w.gone or not w.dx or not s.stood then return false end
	local ax, az = -w.dz, w.dx
	local front, back, smin, smax = nil, nil, nil, nil
	for _, q in pairs(s.stood) do
		local t, a = q[1] * w.dx + q[3] * w.dz, q[1] * ax + q[3] * az
		if not front or t < front then front = t end
		if not back or t > back then back = t end
		if not smin or a < smin then smin = a end
		if not smax or a > smax then smax = a end
	end
	if not front then return false end
	local r, reach = model_radius(w), model_radius(s)
	local margin = w.walks and CLEAR_MARGIN or 0 -- one that stopped holds them only while it really overlaps
	for _, b in ipairs(bodies_of(w)) do
		local t, a = b[1] * w.dx + b[2] * w.dz, b[1] * ax + b[2] * az
		if t + r > front - reach and t - r < back + reach + margin and a + r > smin - reach and a - r < smax + reach then return true end
	end
	return false
end

-- a walker the lanes of s were opened or grown for that is still inside it (the ranks closed up too fast behind the
-- Doomwheels), or nil
local function crossing_walker(s)
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
		if ok and inside and w.walks then w.crossing_at = state.ticks end
		if ok and not inside then
			local cok, coming = pcall(still_coming, w)
			if cok and coming then return w end
		end
		if ok and inside then
			if w.walks then
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

-- closing up in steps: the models walk back STEP_MAX at a time at their walking pace; the last step sends them to the
-- places noted at the opening (close_lanes)
local function closing_step(s)
	local c = s.closing
	c.step = c.step + 1
	if c.step >= c.steps then return close_lanes(s, c.why) end
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

local function start_closing(s, why)
	local cur = step_lanes(s, math.min(s.step or 1, s.steps or 1))
	local far = cur.art and 0 or (cur[8] ~= 0 and math.max(-cur[8] * cur[6], (1 + cur[8]) * cur[6]) or cur[6] * cur[7])
	local steps = math.max(1, math.ceil(far / STEP_MAX - 1e-9))
	local slow = s.unit:slow_speed()
	if type(slow) ~= 'number' or slow < 0.3 or slow > 40 then slow = 1.5 end
	local step_time = (far / steps) / (slow * (s.pace or 1)) + math.max(state.dt, TICK_MS / 1000)
	s.closing = { steps = steps, step = 0, step_time = step_time, at = state.game_seconds, why = why, from = cur }
	local notes = {}
	for w in pairs(s.served or {}) do notes[#notes + 1] = walker_note(s, w) end
	log(('CLOSING %s (%s): back in %d steps of %.1f m, one every %.1f s | %s'):format(s.label, why, steps, far / steps, step_time,
		#notes > 0 and table.concat(notes, '; ') or 'no walker noted'))
	closing_step(s)
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
	for _, p in pairs(near_places(w)) do
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
	for _, p in pairs(near_places(w)) do
		wsum, wn = wsum + (p[1] - ox) * lanes[3] + (p[3] - oz) * lanes[4], wn + 1
	end
	-- the standing models reaching into each lane now (the narrowest gap read one straggler as a closed lane)
	local in_lane, deep = {}, 0
	local rs = model_radius(s)
	for _, p in pairs(near_places(s)) do
		local t = (p[1] - ox) * w.dx + (p[3] - oz) * w.dz
		if t >= lo - 1 and t <= hi + 1 then
			deep = deep + 1
			local a = (p[1] - ox) * lanes[3] + (p[3] - oz) * lanes[4]
			local k = math.max(kl, math.min(kh, math.floor(a / q)))
			if math.abs(a - (k + 0.5) * q) < lanes[6] / 2 + rs then in_lane[k] = (in_lane[k] or 0) + 1 end
		end
	end
	local gaps = {}
	for k = kl, kh do gaps[#gaps + 1] = tostring(in_lane[k] or 0) end
	local lanes_text, blocks_text = {}, {}
	for k = kl, kh do lanes_text[#lanes_text + 1] = tostring(per_lane[k] or 0) end
	for j = kl, kh + 1 do blocks_text[#blocks_text + 1] = tostring(per_block[j] or 0) end
	log(('  COLUMNS %s in %s: %d of %d walker models inside the standing unit, %d of them in a lane, %d between lanes | per lane %s | in the blocks %s (the middle block is the middle number) | walker middle %.1f m across from the middle of the lanes | standing models reaching into the lanes now %s (of %d within its depth; lanes planned %.1f m) | across axis (%.2f, %.2f), walking (%.2f, %.2f)'):format(
		w.label, s.label, inside, total, near, far, table.concat(lanes_text, ' / '), table.concat(blocks_text, ' / '), wn > 0 and wsum / wn or 0, table.concat(gaps, ' / '), deep, lanes[6], lanes[3], lanes[4], w.dx, w.dz))
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
			local ok, lanes, radius, carried = pcall(plan_lanes, s, c.w)
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
				local tok, t_contact, t_split = pcall(timing, s, c.w, lanes)
				if tok and t_contact then -- (no timing: none of its models near its middle yet, e.g. just teleported)
					c.lanes, c.radius, c.carried = lanes, radius, carried
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
	local places = near_places(s)
	local merged, fitting = {}, {}
	for i, c in ipairs(cands) do
		if i > MAX_CANDIDATES then break end
		if c ~= primary then
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
			local extra = (d and d > 0.3) and math.max(0, model_radius(w) - 1) / d or 0
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
	s.open_note = ('walker seen %.1f s before, the standing unit standing for %.1f s'):format(
		seen and (state.ticks - seen) * TICK_MS / 1000 or -1, s.still_since and (state.ticks - s.still_since) * TICK_MS / 1000 or -1)
	open_lanes(s, primary.w, lanes, primary.radius, primary.carried, t[1], t[2], t[3], t[4], t[5], t[6], t[7])
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

-- one standing unit, one look: its lanes open, grow, step, hold and close
local function serve(s)
	-- walkers too late for it stay left out until they are clear of it (further than when refused, + TUNE.LATE_CLEAR:
	-- past, or turned away), ordered elsewhere (their goal moved > SETTLED: the AI re-orders by a few metres all the time)
	-- while not overlapping it, or it stands elsewhere. Not on a flicker of w.walks, of passing() or of its own standing:
	-- run 43, the bloodcrushers' exclusion dropped while they were inside the ironsworn and the lanes opened round them
	for w, late in pairs(s.late or {}) do
		if w.gone or not (w.x and s.x and s.half and s.gx and w.gx and late[6]) then
			s.late[w] = nil
		else
			local dx, dz, near = w.x - s.x, w.z - s.z, s.half + (w.half or 0)
			local d2 = dx * dx + dz * dz
			local reordered = (w.gx - late[5]) ^ 2 + (w.gz - late[6]) ^ 2 > TUNE.SETTLED * TUNE.SETTLED
			if d2 > (math.max(near, late[4]) + TUNE.LATE_CLEAR) ^ 2 or (reordered and d2 > near * near)
				or (s.gx - late[2]) ^ 2 + (s.gz - late[3]) ^ 2 > TUNE.SETTLED * TUNE.SETTLED then -- (its order: position() jitters)
				s.late[w] = nil
			end
		end
	end
	if s.lane > 0 and not s.stands then
		close_lanes(s, 'it no longer just stands')
	elseif s.stands then
		local best, best_along = nil, nil
		local cands = {}
		for _, w in ipairs(state.units) do
			if w ~= s and w.walks and w.alliance == s.alliance and not (s.late and s.late[w]) then
				local along = passing(w, s)
				if along then
					cands[#cands + 1] = { w = w, along = along }
					if not best_along or along < best_along then best, best_along = w, along end
				end
			end
		end
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
		if best and s.lane == 0 then
			table.sort(cands, function(a, b) return a.along < b.along end)
			best = open_for(s, cands) -- nil: not yet, or no lanes
		elseif best and s.closing then -- a walker comes while it closes up: open again, all at once
			log(('  %s opens again for %s while closing up'):format(s.label, best.label))
			s.closing = nil
			s.step = s.steps - 1
			next_step(s, true) -- (its models are walking back: no pace to measure)
			if big_walker(best) then s.served[best] = true end
		end
		if s.lane > 0 and s.widened_for and not s.closing then -- further walkers while the lanes are open
			for _, w in ipairs(state.units) do
				if w ~= s and w ~= s.walker_seen and w.walks and w.alliance == s.alliance and not s.widened_for[w] and not (s.late and s.late[w]) and passing(w, s) then
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
			s.quiet = 0
			s.open_ticks = (s.open_ticks or 0) + 1
			if s.lane > 0 and s.served and s.served[best] then -- (its heading stays its order while it crosses: look())
				local ok, inside = pcall(still_inside, s, best)
				if ok and inside then best.crossing_at = state.ticks end
			end
			if not s.col_logged and s.columns_of then
				local ok, done = pcall(log_columns, s) -- only a log line: a failure must not switch the mod off
				s.col_logged = (not ok) or done
				if not ok then log('  COLUMNS line failed: ' .. tostring(done)) end
			end
			if s.open_ticks == 15 then log(('  3 s after opening, %s: %s (the game says: moving %s, idle %s)'):format(s.label, moved_since(s), tostring(s.moving), tostring(s.idle))) end
		elseif s.lane > 0 then
			s.open_ticks = (s.open_ticks or 0) + 1
			if not s.closing and crossing_walker(s) then
				s.quiet = 0 -- a walker it opened for is still inside it
			else
				s.quiet = s.quiet + 1
				if s.quiet >= HOLD_TICKS and not s.closing then start_closing(s, 'nobody is passing any more') end
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
end

-- (log) a friendly walker whose middle is inside a unit that has no lanes open (run 36: an allied army was not passed
-- through, and the log said nothing about it):
-- once per pair and MISSED_KEEP looks, with the reason
function diag.missed()
	diag.run = (diag.run or 0) + 1
	local logged, more = 0, 0
	for _, w in ipairs(state.units) do
		if not w.gone and w.moving and w.x and w.men >= 1 and not w.busy and not yes(w.unit, 'is_currently_flying') then
			for _, s in ipairs(state.units) do
				-- (not lords / heroes, not units in melee or routing; standing by the SETTLED rule counts)
				if s ~= w and not s.gone and not s.character and not s.busy and s.alliance == w.alliance and s.lane == 0 and (s.stands or not s.moving)
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
								why = 'left out: too late for a lane as wide as its whole front'
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
	for _, entry in ipairs(state.units) do
		if not entry.gone then
			local ok, problem = pcall(look, entry)
			if not ok then retire(entry, 'not readable: ' .. tostring(problem)) end
		end
		if entry.jobs_at then
			if mr.read_uint32(state.tbl, entry.at + E_PENDING) == 0 then
				entry.jobs_at = nil -- every job taken
			elseif state.ticks - entry.jobs_at > 15 then -- jobs nobody took within 3 s are dropped
				mr.write(state.tbl, entry.at + E_PENDING, mr.uint32(0))
				entry.jobs_at = nil
			end
		end
		jobs_left = jobs_left or entry.jobs_at ~= nil
	end
	if state.jobs_any and not jobs_left then
		mr.write(state.tbl, JOBS_ANY, mr.uint32(0))
		state.jobs_any = false
	end
	-- each unit on its own (one error used to switch the mod off for the whole battle): a unit whose look fails closes its
	-- lanes and is left alone for 5 s; after 5 errors it is left out
	for _, s in ipairs(state.units) do
		if not s.gone and state.ticks >= (s.resume_at or 0) then
			local ok, problem = pcall(serve, s)
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
	-- (not on the ticks of report() / collect_units(), multiples of 25 and 50)
	if state.ticks % 5 == 2 then diag.run_check('missed', diag.missed) end
	local walkers = 0
	for _, entry in ipairs(state.units) do
		if entry.columns_for then walkers = walkers + 1 end
	end
	if walkers ~= state.walkers then -- cave 4 looks at marching points only while a walker forms columns
		mr.write(state.tbl, WALKERS, mr.uint32(walkers))
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
	log(('%d units with open lanes | in 5 s: models sent at once %d | places handed out by the game %d, shifted %d, left alone (on an object) %d | 5 s tests %d, hurried %d | marching points while columns are on %d, pulled into lanes %d (%d of them into a lane on the minus side) | script cost %.2f ms per look (every %d ms), longest %.1f ms | game clock %s'):format(
		open, delta[CN.JOBS_DONE], delta[CN.REACHED], delta[COUNT], delta[CN.HUNG], delta[CN.CHECKS], delta[CN.FORCED], delta[CN.CARROT_SEEN], delta[CN.CARROTS], delta[CN.CARROTS_NEG], state.cost * 1000 / REPORT_EVERY, TICK_MS, state.longest * 1000,
		(state.clock and state.report_clock) and ('%.1f game s in the last %d looks'):format(((state.clock - state.report_clock) % 4294967296) * 0.1, REPORT_EVERY) or 'not readable'))
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
	log(('PERF %s real s for %s game s (%s) | mod script %.1f ms in all = %s of the real time, %.2f ms per look (diagnostics %.1f ms in all), longest %.1f ms, looks over %d ms: %d | %d units listed, %d with open lanes, %d walkers forming columns, %d plans | clock step %s'):format(
		real and ('%.2f'):format(real) or '?', game and ('%.1f'):format(game) or '?',
		(real and game and real > 0) and (('game speed %.0f %%'):format(100 * game / real) .. (game < 0.8 * real and ' - paused or the game cannot keep up' or '')) or 'speed unknown',
		state.cost * 1000, (real and real > 0) and ('%.2f %%'):format(100 * state.cost / real) or '?', state.cost * 1000 / REPORT_EVERY, (state.diag_cost or 0) * 1000, state.longest * 1000,
		SLOW_LOOK * 1000, state.slow_looks or 0, #state.units, open, walkers, state.plans or 0, clock_step(real_now)))
	state.report_real, state.slow_looks, state.plans, state.diag_cost = real_now, 0, 0, 0
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

local function start()
	log('Military Drill --VERSION----BUILD_NOTE--, battle script loaded')
	mr = _G.memreader_plus
	if not mr then error('memreader Plus is not loaded', 0) end
	install()
	log(('Lua numbers: %s precision'):format(state.single and 'SINGLE' or 'double'))
	log(('memreader Plus %s, table at %s; lanes are open %.2f s before the walker arrives, 3 blocks (2 lanes; 5 blocks from %.0f m front, 7 from %.0f m); walker columns %s'):format(tostring(mr.plus_version), hex(state.tbl), SPLIT_MARGIN, WIDE_FRONT, WIDER_FRONT, state.columns and 'ON' or 'OFF'))
	-- the units are listed already now (lanes closed, caves off): cave 1 notes what a place of each unit looks
	-- like when the game hands places out during deployment
	local early = pcall(collect_units)
	log(('listed before the fighting: %d units%s'):format(#state.units, early and '' or ' (not possible yet)'))
	bm:register_phase_change_callback('Deployed', guarded('deployed', function()
		read_settings()
		collect_units()
		mr.write(state.tbl, ENABLE, mr.uint32(1))
		state.fighting = true
		state.perf = { real0 = os.clock(), looks = 0, cost = 0, longest = 0, slow = 0, worst = 0 }
		state.report_real = state.perf.real0
		log(('fighting starts, mod ON: %d units, %d of them not in the vanilla tables (other mods: kind from the script interface)'):format(#state.units, state.guessed or 0))
	end))
	bm:repeat_callback(guarded('tick', function()
		if not state.fighting then return end
		local started = os.clock()
		state.ticks = state.ticks + 1
		if state.ticks % COLLECT_EVERY == 0 then collect_units() end
		state.report_clock = state.report_clock or state.clock
		update()
		local took = os.clock() - started
		state.cost = state.cost + took
		if took > state.longest then state.longest = took end
		local p = state.perf
		p.looks, p.cost = p.looks + 1, p.cost + took
		if took > p.longest then p.longest, p.longest_at = took, state.game_seconds end
		if took > SLOW_LOOK then state.slow_looks, p.slow = (state.slow_looks or 0) + 1, p.slow + 1 end
		if state.ticks % REPORT_EVERY == 0 then
			p.worst = math.max(p.worst, state.cost / REPORT_EVERY)
			report()
		end
	end), TICK_MS, TIMER)
	bm:register_phase_change_callback('Complete', function()
		if state.dead then return end
		state.dead = true
		pcall(perf_summary)
		pcall(switch_off)
		pcall(function() bm:remove_callback(TIMER) end)
		log(('battle complete, mod off; lanes were opened %d times'):format(state.opened))
	end)
end

local ok, problem = pcall(start)
if not ok then give_up(problem) end
