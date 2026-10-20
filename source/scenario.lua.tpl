-- Units Pass Through - AUTOMATIC TEST SCENARIO --VERSION-- (!units_scenario_debug.pack), test pack. Start a custom
-- battle (open, flat map) whose first army has: two infantry units of 20+ men (ideally the same type), one cavalry
-- unit, one single big monster (not the general), one artillery piece. Press "Start battle" and only watch. When the
-- fighting starts, this script takes control of every unit (also the enemy's: they are halted, nobody shoots), and runs
-- ROUNDS: in each round several units walk through others AT THE SAME TIME, 90 m apart; units that take no part wait in
-- a row behind the line. Passes of single models (K'daai) are off (INCLUDE_SINGLES) until something about them changes.
-- At the end it gives the control back. Log: units_scenario_log.txt in the game folder (one line per step), next to the
-- logs of the lanes build and the diagnostic probe. After the scenario the script only WRITES DOWN your own orders (one
-- line per new destination of any of your units, and one when it stops) until the battle ends; it gives no orders any
-- more.

local LOG_FILE = 'units_scenario_log.txt'
local TIMER = 'units_scenario_tick'
local TICK_MS = 200
local START_TICKS = 50 -- step 0: everybody stands for 10 s
local SETTLE_TICKS = 75 -- after the teleports of a step: 15 s before the walk order (the lanes build lets a teleported unit
-- make way only after it stood 4 s)
local STILL_TICKS = 10 -- the walker counts as arrived after 2 s without moving
local AFTER_TICKS = 25 -- then 5 s for the lanes to close before the next step
local TIMEOUT_TICKS, SLOW_TIMEOUT_TICKS = 600, 900 -- 120 s for a walk (Ironsworn need more than 60 s for 110 m, run 13), 180 s for artillery
local SPREAD = 90 -- metres between passes of the same round
local NEIGHBOUR_GAP = 4 -- round 3: two standing units this far apart, a walker through each (they must not push into each other)
local INCLUDE_SINGLES = false -- K'daai / single models: their passes work; on again when they are changed
local INCLUDE_AURA = false -- the lord's aura round for the leadership probe: off
local PARK_GAP = 10 -- metres between the sides of the waiting units behind the line (their widths count
-- the units of a big battle stood inside each other with 35 m between their middles, daemons are 42 m wide)
local ROW_GAP = 40 -- further rows of waiting units (a long army, further armies) stand this much further back
local MAX_ROW = 900 -- metres: a row of waiting units is at most this long
-- further rows of one side: the first at REAR, then one nearer to the line, then further back ones (run 38, another
-- map: the game crashed in the second the scenario teleported 117 units to rows up to ~550 m in front of our middle;
-- the old single rows at 150 m behind our line and 350 / 385 m in front of it never crashed): in ROW_GAP steps
local ROW_SEQ = { 0, 1, -1, -2, -3, -4, -5, -6 }
-- kinds of units from the game's tables (built into the pack): 'char', 'art', 'veh', 'mi', 'mc', 'inf', 'cav', 'mon'
local KIND = --KINDS--
local ENEMY_AHEAD = 200 -- the enemy army's passes run on a line this far in front of ours (test the AI's race too)
-- a SECOND army of the enemy alliance (Tomb Kings with skeleton spearmen and a chariot) gets the prefix 'T' and its own
-- line between the two (a chariot walks through its spearmen)
local THIRD_AHEAD = 100
local BACK = 45 -- a walker starts this far from the unit it walks through (60 until run 17)
local BEYOND = 40 -- and stops this far on the other side (50 until run 17)
local SLOW_BACK, SLOW_BEYOND = 40, 35 -- the same for artillery (it is slow)
local REAR = 150 -- units that take no part wait this far behind the line
local AURA_TICKS = 125 -- step 8: the lord stands 25 s near unit A, then 25 s far from it, twice (for the leadership probe)
local AURA_NEAR, AURA_FAR = 8, 100 -- metres behind A
local AURA = { 'near', 'far', 'near', 'far' }
local ORDER_TIMER = 'units_scenario_orders'
local ORDER_DELAY = 2 -- an order is written down 0.4 s after it was seen, when walking / running shows

local state = { dead = false, started = false, ticks = 0, roles = {}, units = {}, ucs = {}, widths = {}, park = {}, round = 0, phase = 'start', wait = START_TICKS }

local function log(message)
	local file = io.open(LOG_FILE, state.started and 'a' or 'w')
	state.started = true
	if not file then return end
	file:write(os.date('%H:%M:%S') .. ' ' .. tostring(message) .. '\n')
	file:close()
end

local function yes(unit, method)
	local ok, value = pcall(function() return unit[method](unit) end)
	return ok and value == true
end

local function uc_of(unit)
	local key = unit:unique_ui_id()
	if not state.ucs[key] then state.ucs[key] = create_unitcontroller(unit) end
	return state.ucs[key]
end

local function xz(unit)
	local p = unit:position()
	return p:get_x(), p:get_z()
end

local function bearing_of(dx, dz) -- degrees, as the game's bearing (0 = +z, 90 = +x)
	return math.deg(math.atan2(dx, dz)) % 360
end

-- a point: base + f * along + r * across
local function at(base, along, across)
	return { base[1] + state.f[1] * along + state.r[1] * across, base[2] + state.f[2] * along + state.r[2] * across }
end

local function teleport(unit, point, bearing)
	uc_of(unit):teleport_to_location(v(point[1], point[2]), bearing, state.widths[unit:unique_ui_id()] or unit:ordered_width())
end

-- the walkers RUN (the default of the test passes)
local RUN = true
local function walk(unit, point, bearing)
	uc_of(unit):goto_location_angle_width(v(point[1], point[2]), bearing, state.widths[unit:unique_ui_id()] or unit:ordered_width(), RUN)
end

local function name_of(role)
	local unit = state.roles[role]
	return unit and (role .. ' (' .. tostring(unit:type()) .. ', ' .. unit:number_of_men_alive() .. ' men)') or (role .. ' (missing)')
end

-- the five test units of the first army
-- the test roles of one army: prefix '' for ours, 'E' for the enemy's
local function classify(units, prefix)
	local infantry, monstrous = {}, {}
	-- flying units get no role but the lord's: they fly over the others (run 36: the roles C and MC were flying Plague
	-- Drones, pass 2b showed nothing)
	for _, unit in ipairs(units) do
		state.widths[unit:unique_ui_id()] = unit:ordered_width()
	end
	local ground = {}
	for _, unit in ipairs(units) do
		if not yes(unit, 'can_fly') or yes(unit, 'is_commanding_unit') then ground[#ground + 1] = unit end
	end
	for _, unit in ipairs(ground) do
		local men = unit:number_of_men_alive()
		local kind = KIND[tostring(unit:type())]
		local function role(name) state.roles[prefix .. name] = state.roles[prefix .. name] or unit end
		if yes(unit, 'is_commanding_unit') then
			role('G')
		elseif kind == 'art' or (not kind and yes(unit, 'is_artillery')) then
			role('R') -- artillery (it walks as a machine in pass 1c, stands with its crew in 2c)
		elseif kind == 'veh' or (not kind and (yes(unit, 'is_war_machine') or yes(unit, 'is_chariot'))) then
			role('V') -- a chariot or war machine (walks in round 4)
		elseif kind == 'char' then
			-- another character: no test role
		elseif kind == 'mc' then
			role('MC') -- monstrous cavalry (walks in round 4; also the cavalry of rounds 1 / 2 if there is no other)
			role('C')
		elseif yes(unit, 'is_cavalry') then
			role('C')
		elseif kind == 'inf' then
			infantry[#infantry + 1] = unit -- infantry by type, however many models
		elseif kind == 'mi' or (not kind and men > 3 and men < 20) then
			monstrous[#monstrous + 1] = unit -- monstrous infantry (or another small unit of big models)
		elseif men <= 3 then
			if not yes(unit, 'can_fly') then state.roles[prefix .. 'M'] = state.roles[prefix .. 'M'] or unit end
		elseif men >= 20 then
			infantry[#infantry + 1] = unit
		end
	end
	-- two pairs of infantry units, each of the same type when possible
	local function pair(list)
		for i = 1, #list do
			for j = i + 1, #list do
				if list[i]:type() == list[j]:type() then
					local second = table.remove(list, j)
					return table.remove(list, i), second
				end
			end
		end
		local first = table.remove(list, 1)
		return first, table.remove(list, 1)
	end
	state.roles[prefix .. 'A'], state.roles[prefix .. 'W'] = pair(infantry)
	state.roles[prefix .. 'A2'], state.roles[prefix .. 'W2'] = pair(infantry)
	-- monstrous infantry: the smallest unit walks in round 4 (MI); the others fill the second pair when there is too
	-- little infantry (Ogres: 2 x Ogre Bulls)
	table.sort(monstrous, function(a, b) return a:number_of_men_alive() < b:number_of_men_alive() end)
	state.roles[prefix .. 'MI'] = table.remove(monstrous, 1)
	if not state.roles[prefix .. 'W2'] and #monstrous >= 2 then
		if state.roles[prefix .. 'A2'] then table.insert(infantry, state.roles[prefix .. 'A2']) end
		state.roles[prefix .. 'A2'], state.roles[prefix .. 'W2'] = pair(monstrous)
	end
	-- no monstrous cavalry: another monstrous infantry unit walks in its place (round 4)
	state.roles[prefix .. 'MC'] = state.roles[prefix .. 'MC'] or table.remove(monstrous, 1)
end

-- the roles of further armies of an alliance (a big battle, allies of another race and a faction of another mod): their
-- 2nd and 3rd army
local ALLY_PREFIX = { 'L', 'K' }
local ENEMY_PREFIX = { 'T', 'H' }
local ROLE_NAMES = { 'A', 'W', 'A2', 'W2', 'C', 'MC', 'MI', 'R', 'V', 'M', 'G' }

local function pick_roles()
	local armies = bm.battle:alliances():item(1):armies()
	local first, rest = {}, {}
	state.allies = {}
	for b = 1, armies:count() do
		local list = armies:item(b):units()
		local army = {}
		for index = 1, list:count() do
			state.units[#state.units + 1] = list:item(index)
			army[#army + 1] = list:item(index)
			if b == 1 then first[#first + 1] = list:item(index) else rest[#rest + 1] = list:item(index) end
		end
		if b >= 2 then state.allies[#state.allies + 1] = army end
	end
	-- our roles from our own army; a role it lacks from the allies (run 36: our Skaven had no cavalry)
	classify(first, '')
	classify(rest, '~')
	for _, name in ipairs(ROLE_NAMES) do
		state.roles[name] = state.roles[name] or state.roles['~' .. name]
	end
	-- (MC: the allies' monstrous infantry when they have no other and ours went to MI)
	if not state.roles.MC and state.roles['~MI'] ~= state.roles.MI then state.roles.MC = state.roles['~MI'] end
	for _, name in ipairs(ROLE_NAMES) do state.roles['~' .. name] = nil end
	for i, army in ipairs(state.allies) do
		if ALLY_PREFIX[i] then classify(army, ALLY_PREFIX[i]) end
	end
end

-- (log) the area of the planned waiting places, from our middle
local function note_place(p)
	local b = state.plan
	if not b then return end
	local rx, rz = p[1] - state.o[1], p[2] - state.o[2]
	local along, across = rx * state.f[1] + rz * state.f[2], rx * state.r[1] + rz * state.r[2]
	b.alo, b.ahi = math.min(b.alo or along, along), math.max(b.ahi or along, along)
	b.clo, b.chi = math.min(b.clo or across, across), math.max(b.chi or across, across)
end

-- the waiting units side by side, their widths and PARK_GAP between them, in rows of at most MAX_ROW m at <base> +
-- <along>; further rows by ROW_SEQ (toward: +1 / -1, the direction towards the line), counted on in <seq> so that
-- further armies of the same side go on with the next rows
local function park_rows(units, base, along, toward, seq)
	local lines, line, length = {}, {}, 0
	for _, unit in ipairs(units) do
		local w = (state.widths[unit:unique_ui_id()] or 30) + PARK_GAP
		if #line > 0 and length + w > MAX_ROW then
			lines[#lines + 1], line, length = line, {}, 0
		end
		line[#line + 1], length = unit, length + w
	end
	if #line > 0 then lines[#lines + 1] = line end
	for k, row in ipairs(lines) do
		local total = 0
		for _, unit in ipairs(row) do total = total + (state.widths[unit:unique_ui_id()] or 30) + PARK_GAP end
		local x = -(total - PARK_GAP) / 2
		for _, unit in ipairs(row) do
			local w = state.widths[unit:unique_ui_id()] or 30
			local p = at(base, along + toward * (ROW_SEQ[seq.n + k] or (seq.n + k)) * ROW_GAP, x + w / 2)
			state.park[unit:unique_ui_id()] = p
			note_place(p)
			x = x + w + PARK_GAP
		end
	end
	seq.n = seq.n + #lines
end

-- where everything stands: the middle of the first army, facing the enemy
local function layout()
	local sx, sz, n = 0, 0, 0
	for _, unit in ipairs(state.units) do
		local x, z = xz(unit)
		sx, sz, n = sx + x, sz + z, n + 1
	end
	state.o = { sx / n, sz / n }
	local ex, ez, m = 0, 0, 0
	local armies = bm.battle:alliances():item(2):armies()
	state.third, state.foes = {}, {}
	for b = 1, armies:count() do
		local list = armies:item(b):units()
		local army = {}
		for index = 1, list:count() do
			local x, z = xz(list:item(index))
			ex, ez, m = ex + x, ez + z, m + 1
			if b == 1 then
				state.enemy = state.enemy or {}
				state.enemy[#state.enemy + 1] = list:item(index)
			else
				state.third[#state.third + 1] = list:item(index) -- further armies of the enemy alliance
				army[#army + 1] = list:item(index)
			end
		end
		if b >= 2 then state.foes[#state.foes + 1] = army end
	end
	local dx, dz = 0, 1
	if m > 0 then dx, dz = ex / m - state.o[1], ez / m - state.o[2] end
	local length = math.sqrt(dx * dx + dz * dz)
	if length < 1 then dx, dz, length = 0, 1, 1 end
	state.f = { dx / length, dz / length } -- towards the enemy
	state.r = { state.f[2], -state.f[1] } -- to the right
	state.b0 = bearing_of(state.f[1], state.f[2])
	-- where the units of both sides stand at the start (on the map for sure): the waiting places stay near it
	local box = {}
	local all = {}
	for _, unit in ipairs(state.units) do all[#all + 1] = unit end
	for _, unit in ipairs(state.enemy or {}) do all[#all + 1] = unit end
	for _, unit in ipairs(state.third) do all[#all + 1] = unit end
	for _, unit in ipairs(all) do
		local x, z = xz(unit)
		local rx, rz = x - state.o[1], z - state.o[2]
		local along, across = rx * state.f[1] + rz * state.f[2], rx * state.r[1] + rz * state.r[2]
		box.alo, box.ahi = math.min(box.alo or along, along), math.max(box.ahi or along, along)
		box.clo, box.chi = math.min(box.clo or across, across), math.max(box.chi or across, across)
	end
	if box.alo then state.box = box end
	state.plan = {}
	state.spot = { A = at(state.o, 0, 0) } -- the lord's aura round
	state.eo = at(state.o, ENEMY_AHEAD, 0) -- the line of the enemy army's passes
	classify(state.enemy or {}, 'E')
	local foe_rows = { n = 0 }
	park_rows(state.enemy or {}, state.eo, REAR, -1, foe_rows)
	state.to = at(state.o, THIRD_AHEAD, 0) -- the line of the further enemy armies' passes
	for i, army in ipairs(state.foes) do -- each waits in a row behind the enemy's row
		if ENEMY_PREFIX[i] then classify(army, ENEMY_PREFIX[i]) end
		park_rows(army, state.eo, REAR, -1, foe_rows)
	end
	park_rows(state.units, state.o, -REAR, 1, { n = 0 })
	local b, p = state.box, state.plan
	if b and p.alo then -- (written before any teleport: a crash right after them still leaves this line)
		log(('ROW PLAN: at the start the units stood %.0f .. %.0f m along (towards the enemy) and %.0f .. %.0f m across from our middle; the waiting places are planned %.0f .. %.0f m along and %.0f .. %.0f m across'):format(
			b.alo, b.ahi, b.clo, b.chi, p.alo, p.ahi, p.clo, p.chi))
	end
end

-- one pass: <walker> walks through <stander>; side 'behind' (towards the enemy), 'front' (away from it) or 'diagonal';
-- where: metres from the middle of the line (across), or 'left' / 'right' (two neighbours, NEIGHBOUR_GAP apart).
-- off: its way runs this many metres beside the stander's middle (square to its way); lag: it starts this much
-- further back (two walkers through ONE stander: side by side, one after the other, at an angle)
local function pass(name, walker, stander, side, where, slow, off, lag)
	return { name = name, walker = walker, stander = stander, side = side, where = where, slow = slow, off = off or 0, lag = lag or 0 }
end

-- a pass whose stander may be missing: then one of these stand-ins (their army had no second infantry unit EA2, so the
-- chariot's only pass was skipped)
local function or_through(p, alts)
	p.alts = alts
	return p
end

local ROUNDS = {
	-- passes 'e..' are the enemy army's (another race), on its own line ENEMY_AHEAD in front of ours, AT THE SAME TIME
	{ number = 1, pass('1a', 'W', 'A', 'behind', -SPREAD), pass('1b', 'W2', 'C', 'behind', 0), pass('1c', 'R', 'A2', 'behind', SPREAD, true),
		pass('1d', 'EW', 'EA', 'behind', -SPREAD), pass('1e', 'EW2', 'EC', 'behind', 0), pass('1f', 'ER', 'EA2', 'behind', SPREAD, true),
		pass('1g', 'TV', 'TA', 'behind', 0) }, -- the second enemy army: its chariot through its infantry
	{ number = 2, pass('2a', 'W2', 'A', 'diagonal', -SPREAD), pass('2b', 'C', 'A2', 'behind', 0), pass('2c', 'W', 'R', 'behind', SPREAD),
		pass('2d', 'EW2', 'EA', 'diagonal', -SPREAD), pass('2e', 'EC', 'EA2', 'behind', 0), pass('2f', 'EW', 'ER', 'behind', SPREAD) },
	{ number = 3, pass('3a', 'W', 'A', 'behind', 'left'), pass('3b', 'W2', 'A2', 'behind', 'right'),
		pass('3c', 'EW', 'EA', 'behind', 'left'), pass('3d', 'EW2', 'EA2', 'behind', 'right') },
	-- few big bodies walk through standing infantry (chariots, monstrous infantry / cavalry, war machines, artillery):
	-- they keep their formation, one lane per file (or one wide lane for a single body)
	{ number = 4, pass('4a', 'MI', 'A', 'behind', -SPREAD), pass('4b', 'V', 'A2', 'behind', 0), pass('4c', 'MC', 'W', 'behind', SPREAD),
		pass('4d', 'EMI', 'EA', 'behind', -SPREAD), or_through(pass('4e', 'EV', 'EA2', 'behind', 0), { 'EA', 'EW' }), pass('4f', 'EMC', 'EW', 'behind', SPREAD),
		pass('4g', 'TV', 'TA', 'diagonal', 0) },
	{ number = 5, singles = true, pass('5a', 'M', 'A', 'front', -SPREAD), pass('5b', 'EM', 'EA', 'front', -SPREAD) },
	{ number = 6, kind = 'aura' }, -- the lord's leadership aura on A, for the leadership probe
	-- TWO big walkers through ONE standing unit (Ikit Claw on his Doomwheel and a generic Doomwheel, also at an angle):
	-- the lane opened for the first one has to grow for the second
	{ number = 7, pass('7a', 'G', 'A', 'behind', 0, false, -7), pass('7b', 'V', 'A', 'behind', 0, false, 7) }, -- side by side
	{ number = 8, pass('8a', 'G', 'A', 'behind', 0), pass('8b', 'V', 'A', 'behind', 0, false, 0, 25) }, -- one after the other
	{ number = 9, pass('9a', 'G', 'A', 'behind', 0), pass('9b', 'V', 'A', 'diagonal', 0, false, 0, 15) }, -- crossing at 45 deg
	{ number = 10, pass('10a', 'G', 'A', 'diagonal', 0, false, -7), pass('10b', 'V', 'A', 'diagonal', 0, false, 7) }, -- both at 45 deg
	-- ALLIED ARMIES (a big battle with allies of another race and a faction of another mod): their units through ours,
	-- ours through theirs, one ally's through the other's; and the third enemy army ('H') with the second ('T') on the
	-- line between
	{ number = 11, pass('11a', 'LW', 'A', 'behind', -1.5 * SPREAD), pass('11b', 'W', 'LA', 'behind', -0.5 * SPREAD),
		or_through(pass('11c', 'KW', 'LA2', 'behind', 0.5 * SPREAD), { 'KA', 'A2' }), or_through(pass('11d', 'LW2', 'KA', 'behind', 1.5 * SPREAD), { 'KA2', 'A2' }),
		pass('11e', 'HW', 'TA', 'behind', -SPREAD) }, -- (the other way round: 12f)
	-- the allies' big bodies (chariots, war machines, monstrous infantry, cavalry) through ours and through each other
	{ number = 12, pass('12a', 'LV', 'A2', 'behind', -1.5 * SPREAD), pass('12b', 'KV', 'A', 'behind', -0.5 * SPREAD),
		or_through(pass('12c', 'LMI', 'KA', 'behind', 0.5 * SPREAD), { 'KA2', 'W' }), or_through(pass('12d', 'KC', 'LA', 'behind', 1.5 * SPREAD), { 'LA2', 'W2' }),
		or_through(pass('12e', 'HV', 'TA', 'behind', -SPREAD), { 'TW' }), or_through(pass('12f', 'TV', 'HA', 'behind', SPREAD), { 'HA2' }) },
}

local function stand_at(p)
	local base = p.base or state.o
	if p.where == 'left' or p.where == 'right' then
		local half = (state.widths[state.roles[p.stander]:unique_ui_id()] or 30) / 2
		return at(base, 0, (p.where == 'left' and -1 or 1) * (half + NEIGHBOUR_GAP / 2))
	end
	return at(base, 0, p.where)
end

local function setup_pass(p)
	local walker, stander = state.roles[p.walker], state.roles[p.stander]
	if not walker or not stander then
		log(('PASS %s skipped: %s walks through %s - a unit is missing'):format(p.name, name_of(p.walker), name_of(p.stander)))
		return false
	end
	local s = stand_at(p)
	local d = { state.f[1], state.f[2] }
	if p.side == 'front' then d = { -d[1], -d[2] } end
	if p.side == 'diagonal' then d = { (state.f[1] + state.r[1]) / math.sqrt(2), (state.f[2] + state.r[2]) / math.sqrt(2) } end
	local b = bearing_of(d[1], d[2])
	local back, beyond = (p.slow and SLOW_BACK or BACK) + p.lag, p.slow and SLOW_BEYOND or BEYOND
	local ox, oz = d[2] * p.off, -d[1] * p.off -- beside the stander's middle, square to the way
	p.start = { s[1] - d[1] * back + ox, s[2] - d[2] * back + oz }
	p.goal = { s[1] + d[1] * beyond + ox, s[2] + d[2] * beyond + oz }
	p.bearing = b
	p.walked, p.still, p.done = 0, 0, false
	teleport(stander, s, state.b0) -- the standing unit stands in good order where it belongs
	teleport(walker, p.start, b)
	log(('PASS %s: %s walks through %s (%s%s): from %.0f / %.0f to %.0f / %.0f, bearing %.0f; %s stands at %.0f / %.0f, bearing %.0f'):format(p.name,
		name_of(p.walker), name_of(p.stander), p.side,
		(p.off ~= 0 and (', %.0f m beside its middle'):format(p.off) or '') .. (p.lag ~= 0 and (', %.0f m further back'):format(p.lag) or ''),
		p.start[1], p.start[2], p.goal[1], p.goal[2], b, p.stander, s[1], s[2], state.b0))
	return true
end

-- every unit that takes no part in the round goes to its place in the row behind its line, under the script's control
-- again and halted (allied Fiends of Slaanesh ran about the whole scenario, enemy units fought; control was taken and
-- the enemy halted only once, at the start)
local function hold(unit)
	local uc = uc_of(unit)
	pcall(function() uc:take_control() end)
	pcall(function() uc:halt() end)
	teleport(unit, state.park[unit:unique_ui_id()], state.b0)
end

local function park_all_but(used)
	for _, unit in ipairs(state.units) do
		if not used[unit] then hold(unit) end
	end
	for _, unit in ipairs(state.enemy or {}) do
		if not used[unit] then hold(unit) end
	end
	for _, unit in ipairs(state.third or {}) do
		if not used[unit] then hold(unit) end
	end
end

local function setup_round(r)
	if r.singles and not INCLUDE_SINGLES then
		log(('ROUND %d skipped: passes of single models are off (INCLUDE_SINGLES)'):format(r.number))
		return false
	end
	local used = {}
	if r.kind == 'aura' and not INCLUDE_AURA then
		log(('ROUND %d skipped: the lord aura round is off (INCLUDE_AURA)'):format(r.number))
		return false
	end
	if r.kind == 'aura' then
		local lord, a = state.roles.G, state.roles.A
		if not lord or not a then
			log(('ROUND %d skipped: the lord or A is missing'):format(r.number))
			return false
		end
		used[lord], used[a] = true, true
		park_all_but(used)
		teleport(a, state.spot.A, state.b0)
		teleport(lord, at(state.spot.A, -AURA_FAR, 0), state.b0)
		log(('ROUND %d: the lord %s stands near %s and far from it, %d s each, twice (leadership aura for the leadership probe)'):format(r.number, name_of('G'), name_of('A'), AURA_TICKS * TICK_MS / 1000))
		return true
	end
	-- a missing stander: the first stand-in that no other pass of this round uses
	for _, p in ipairs(r) do
		if p.alts and state.roles[p.walker] and not state.roles[p.stander] then
			for _, alt in ipairs(p.alts) do
				local taken = false
				for _, q in ipairs(r) do
					if q ~= p and state.roles[q.walker] and state.roles[q.stander] and (q.stander == alt or q.walker == alt) then taken = true end
				end
				if state.roles[alt] and not taken then
					log(('PASS %s: %s is missing, %s walks through %s instead'):format(p.name, p.stander, name_of(p.walker), name_of(alt)))
					p.stander = alt
					break
				end
			end
		end
	end
	local walkers = {}
	for _, p in ipairs(r) do
		local w, s = state.roles[p.walker], state.roles[p.stander]
		-- (one stander for two walkers is meant: rounds 7 - 10)
		if w and s and (used[w] or walkers[s] or w == s) then
			log(('PASS %s skipped: %s walks through %s - a unit is in another pass of this round'):format(p.name, name_of(p.walker), name_of(p.stander)))
			p.clash = true
		elseif w and s then
			used[w], used[s], walkers[w] = true, true, true
			p.clash = nil
		end
		local army = p.walker:sub(1, 1)
		p.base = (army == 'E' and state.eo) or ((army == 'T' or army == 'H') and state.to) or state.o
	end
	park_all_but(used)
	r.active = {}
	for _, p in ipairs(r) do
		if not p.clash and setup_pass(p) then r.active[#r.active + 1] = p end
	end
	if #r.active == 0 then return false end
	log(('ROUND %d: %d passes at the same time'):format(r.number, #r.active))
	return true
end

local function release_all()
	for _, uc in pairs(state.ucs) do pcall(function() uc:release_control() end) end
end

local function begin()
	pick_roles()
	layout()
	log(('roles: %s, %s, %s, %s, %s, %s, %s, %s'):format(name_of('A'), name_of('W'), name_of('A2'), name_of('W2'), name_of('C'), name_of('R'), name_of('M'), name_of('G')))
	log(('enemy roles: %s, %s, %s, %s, %s, %s, %s'):format(name_of('EA'), name_of('EW'), name_of('EA2'), name_of('EW2'), name_of('EC'), name_of('ER'), name_of('EM')))
	log(('big bodies: %s, %s, %s; enemy %s, %s, %s'):format(name_of('MI'), name_of('MC'), name_of('V'), name_of('EMI'), name_of('EMC'), name_of('EV')))
	log(('second enemy army (%d units): infantry %s, chariot / war machine %s'):format(#(state.foes[1] or {}), name_of('TA'), name_of('TV')))
	if state.foes[2] then
		log(('third enemy army (%d units): %s, %s, %s, %s'):format(#state.foes[2], name_of('HA'), name_of('HW'), name_of('HA2'), name_of('HV')))
	end
	for i, army in ipairs(state.allies) do
		local x = ALLY_PREFIX[i]
		if x then
			log(('allied army %d (%d units): %s, %s, %s, %s, %s, %s, %s'):format(i + 1, #army, name_of(x .. 'A'), name_of(x .. 'W'), name_of(x .. 'A2'), name_of(x .. 'W2'),
				name_of(x .. 'V'), name_of(x .. 'MI'), name_of(x .. 'C')))
		end
	end
	log(('line middle %.0f / %.0f, facing %.0f (towards the enemy)'):format(state.o[1], state.o[2], state.b0))
	-- every unit is the script's now; the enemy stands still
	local quiet = 0
	local halted = {}
	for _, unit in ipairs(state.enemy or {}) do halted[#halted + 1] = unit end
	for _, unit in ipairs(state.third or {}) do halted[#halted + 1] = unit end
	for _, unit in ipairs(halted) do
		local uc = uc_of(unit)
		uc:take_control()
		uc:halt()
		-- the enemy does not shoot either (run 16: it shot A from 94 to 53 men during the scenario)
		if pcall(function() uc:change_behaviour_active('fire_at_will', false) end) then quiet = quiet + 1 end
		teleport(unit, state.park[unit:unique_ui_id()], state.b0)
	end
	for _, unit in ipairs(state.units) do
		uc_of(unit):take_control()
		pcall(function() uc_of(unit):halt() end) -- (allied armies' AI too: run 39)
		-- no shooting during the test (fire at will off); units without it just ignore this
		if pcall(function() uc_of(unit):change_behaviour_active('fire_at_will', false) end) then quiet = quiet + 1 end
		teleport(unit, state.park[unit:unique_ui_id()], state.b0)
	end
	log(('STEP 0: all %d units wait in a row %d m behind the line, %d enemy units halted, fire at will off for %d units of both sides; everybody stands for %d s'):format(#state.units, REAR, #halted, quiet, START_TICKS * TICK_MS / 1000))
end

-- after the scenario: your own orders ----------------------------------------------------------------------------

local function own_units()
	if #state.units > 0 then return state.units end
	local armies = bm.battle:alliances():item(1):armies()
	for b = 1, armies:count() do
		local list = armies:item(b):units()
		for index = 1, list:count() do state.units[#state.units + 1] = list:item(index) end
	end
	return state.units
end

local function label_of(unit)
	for role, r in pairs(state.roles) do
		if r == unit then return role .. ' ' .. tostring(unit:type()) end
	end
	return tostring(unit:type()) .. ' #' .. tostring(unit:unique_ui_id())
end

local function turn(a, b)
	local d = math.abs(a - b) % 360
	return d > 180 and 360 - d or d
end

-- every 0.2 s: a new destination (place, width or facing) = an order; a unit that stops = arrived
local function watch_orders()
	for _, unit in ipairs(state.orders.units) do
		local key = unit:unique_ui_id()
		local seen = state.orders.seen[key]
		local g = unit:ordered_position()
		local now = { x = g:get_x(), z = g:get_z(), width = unit:ordered_width(), bearing = unit:ordered_bearing(), moving = unit:is_moving() }
		if seen then
			local changed = math.sqrt((now.x - seen.x) ^ 2 + (now.z - seen.z) ^ 2) > 1 or math.abs(now.width - seen.width) > 1 or turn(now.bearing, seen.bearing) > 5
			if changed then
				local x, z = xz(unit)
				state.orders.pending[key] = { unit = unit, wait = ORDER_DELAY, text = ('ORDER %s: from %.0f / %.0f to %.0f / %.0f (%.0f m), width %.0f, facing %.0f'):format(label_of(unit), x, z,
					now.x, now.z, math.sqrt((now.x - x) ^ 2 + (now.z - z) ^ 2), now.width, now.bearing) }
			end
			if seen.moving and not now.moving and not state.orders.pending[key] then
				local x, z = xz(unit)
				log(('STOPPED %s at %.0f / %.0f (%.1f m from its destination)'):format(label_of(unit), x, z, math.sqrt((now.x - x) ^ 2 + (now.z - z) ^ 2)))
			end
		end
		state.orders.seen[key] = now
	end
	for key, p in pairs(state.orders.pending) do
		p.wait = p.wait - 1
		if p.wait <= 0 then
			log(p.text .. (p.unit:is_moving() and (p.unit:is_moving_fast() and ', running' or ', walking') or ', not moving yet'))
			state.orders.pending[key] = nil
		end
	end
end

local function start_order_log()
	state.orders = { units = own_units(), seen = {}, pending = {} }
	log(('from now on your orders to your %d units are written down here (ORDER / STOPPED lines)'):format(#state.orders.units))
	bm:repeat_callback(function()
		if state.orders_dead then return end
		local ok, problem = pcall(watch_orders)
		if not ok then
			state.orders_dead = true
			log('ORDER LOG STOPPED by an error: ' .. tostring(problem))
			pcall(function() bm:remove_callback(ORDER_TIMER) end)
		end
	end, TICK_MS, ORDER_TIMER)
end

local function finish(why)
	if state.dead then return end
	state.dead = true
	release_all()
	pcall(function() bm:remove_callback(TIMER) end)
	log('SCENARIO FINISHED (' .. why .. ') - control is back with you; play on with the mouse or leave the battle')
	_G.units_scenario_finished = (tonumber(_G.units_scenario_finished) or 0) + 1 -- the leadership probe writes its ranking now
	if why ~= 'the battle ended' then
		local ok, problem = pcall(start_order_log)
		if not ok then log('ORDER LOG not started: ' .. tostring(problem)) end
	end
end

-- after step 0: where the waiting units of one side really stand (units stood inside each other; for the rows that wrap
-- a long army): rows, units inside another, the farthest one from its place
local function check_rows(name, units)
	local spots, far = {}, 0
	for _, unit in ipairs(units) do
		local x, z = xz(unit)
		local rx, rz = x - state.o[1], z - state.o[2]
		local along, across = rx * state.f[1] + rz * state.f[2], rx * state.r[1] + rz * state.r[2]
		local w = state.widths[unit:unique_ui_id()] or 30
		spots[#spots + 1] = { unit = unit, along = along, across = across, w = w }
		local place = state.park[unit:unique_ui_id()]
		if place then far = math.max(far, math.sqrt((place[1] - x) ^ 2 + (place[2] - z) ^ 2)) end
	end
	-- rows: units whose places along the line lie within ROW_GAP / 2 of the one before
	local sorted = {}
	for i, spot in ipairs(spots) do sorted[i] = spot end
	table.sort(sorted, function(a, b) return a.along < b.along end)
	local count, length, lo, hi, last = 0, 0, nil, nil, nil
	for _, spot in ipairs(sorted) do
		if not last or spot.along - last > ROW_GAP / 2 then
			if lo then length = math.max(length, hi - lo) end
			count, lo, hi = count + 1, spot.across - spot.w / 2, spot.across + spot.w / 2
		else
			lo, hi = math.min(lo, spot.across - spot.w / 2), math.max(hi, spot.across + spot.w / 2)
		end
		last = spot.along
	end
	if lo then length = math.max(length, hi - lo) end
	local inside = {}
	for i = 1, #spots do
		for j = i + 1, #spots do
			local a, b = spots[i], spots[j]
			if math.abs(a.along - b.along) < ROW_GAP / 2 and math.abs(a.across - b.across) < (a.w + b.w) / 2 then
				inside[#inside + 1] = ('%s / %s'):format(label_of(a.unit), label_of(b.unit))
			end
		end
	end
	log(('ROW CHECK %s: %d units in %d rows (the longest %.0f m, at most %d m planned), %d pairs inside each other%s; the farthest from its place %.1f m'):format(
		name, #spots, count, length, MAX_ROW, #inside, #inside > 0 and (' (' .. table.concat(inside, ', ', 1, math.min(5, #inside)) .. ')') or '', far))
end

local function tick()
	state.ticks = state.ticks + 1
	if state.phase == 'start' then
		begin()
		state.phase, state.wait = 'pause', START_TICKS
	elseif state.phase == 'pause' then
		state.wait = state.wait - 1
		if state.wait <= 0 then
			if state.round == 0 then
				check_rows('our side', state.units)
				local foes = {}
				for _, unit in ipairs(state.enemy or {}) do foes[#foes + 1] = unit end
				for _, unit in ipairs(state.third or {}) do foes[#foes + 1] = unit end
				check_rows('enemy side', foes)
			end
			repeat
				state.round = state.round + 1
				local r = ROUNDS[state.round]
				if not r then return finish('all rounds done') end
				state.current = r
			until setup_round(r)
			state.phase, state.wait = 'settle', SETTLE_TICKS
		end
	elseif state.phase == 'settle' then
		state.wait = state.wait - 1
		if state.wait <= 0 then
			local r = state.current
			if r.kind == 'aura' then
				state.phase, state.aura_i, state.wait = 'aura', 0, 0
			else
				for _, p in ipairs(r.active) do walk(state.roles[p.walker], p.goal, p.bearing) end -- all at once
				state.phase = 'walking'
			end
		end
	elseif state.phase == 'aura' then
		state.wait = state.wait - 1
		if state.wait <= 0 then
			state.aura_i = state.aura_i + 1
			local where = AURA[state.aura_i]
			local number = state.current.number
			if not where then
				_G.units_lead_phase = nil
				log(('ROUND %d done: lord aura near / far finished'):format(number))
				state.phase, state.wait = 'pause', AFTER_TICKS
			else
				teleport(state.roles.G, at(state.spot.A, where == 'near' and -AURA_NEAR or -AURA_FAR, 0), state.b0)
				_G.units_lead_phase = where
				log(('ROUND %d: lord %s A (%d m behind it) for %d s'):format(number, where == 'near' and 'NEAR' or 'FAR from', where == 'near' and AURA_NEAR or AURA_FAR, AURA_TICKS * TICK_MS / 1000))
				state.wait = AURA_TICKS
			end
		end
	elseif state.phase == 'walking' then
		local walking = 0
		for _, p in ipairs(state.current.active) do
			if not p.done then
				local walker = state.roles[p.walker]
				p.walked = p.walked + 1
				if walker:is_moving() then p.still = 0 else p.still = p.still + 1 end
				local x, z = xz(walker)
				local left = math.sqrt((p.goal[1] - x) ^ 2 + (p.goal[2] - z) ^ 2)
				local timeout = p.slow and SLOW_TIMEOUT_TICKS or TIMEOUT_TICKS
				if (p.walked > 15 and p.still >= STILL_TICKS) or p.walked >= timeout then
					p.done = true
					log(('PASS %s %s after %.1f s: %s is %.1f m from its goal'):format(p.name, p.walked >= timeout and 'TIMED OUT' or 'done',
						p.walked * TICK_MS / 1000, p.walker, left))
				else
					walking = walking + 1
				end
			end
		end
		if walking == 0 then state.phase, state.wait = 'pause', AFTER_TICKS end
	end
end

local function start()
	log('units scenario --VERSION-- TEST PACK, battle script loaded')
	bm:register_phase_change_callback('Deployed', function()
		bm:repeat_callback(function()
			if state.dead then return end
			local ok, problem = pcall(tick)
			if not ok then
				log('SCENARIO ERROR: ' .. tostring(problem))
				pcall(finish, 'stopped by an error')
			end
		end, TICK_MS, TIMER)
	end)
	bm:register_phase_change_callback('Complete', function()
		if not state.dead then pcall(finish, 'the battle ended') end
		if state.orders and not state.orders_dead then
			state.orders_dead = true
			pcall(function() bm:remove_callback(ORDER_TIMER) end)
			log('battle ended: order log closed')
		end
	end)
end

local ok, problem = pcall(start)
if not ok then log('SCENARIO ERROR at load: ' .. tostring(problem)) end
