-- Units Pass Through - AUTOMATIC TEST SCENARIO --VERSION-- (!units_scenario_debug.pack), test pack. Start a custom
-- battle (open, flat map) whose armies together have: two infantry pairs (20+ men, ideally the same type), two missile
-- infantry units, one cavalry unit, a direct-fire battery (cannons / bolt throwers / organ gun: one unit of several guns),
-- one single big monster (not the general), a fast monstrous cavalry unit (demigryph or great stag knights), a monstrous
-- infantry unit and one ethereal unit (Hexwraiths / Cairn Wraiths / Spirit Host); the enemy alliance the same where
-- possible. No faction has all of them: a role the first army lacks is taken from the next army of the same alliance.
-- Press "Start battle" and only watch. When the
-- fighting starts, this script takes control of every unit (also the enemy's: they are halted, nobody shoots), and runs
-- ROUNDS: in each round several units walk through others AT THE SAME TIME, 90 m apart; units that take no part wait in
-- a row behind the line. Passes of single models (K'daai) are off (INCLUDE_SINGLES) until something about them changes.
-- Round 23 (artillery fires through a standing unit) needs our artillery piece R to be a DIRECT-FIRE gun (a cannon, bolt
-- thrower or organ gun type; a catapult or mortar does not shoot along a line). Round 24 (ranged walkers, the standing unit
-- closes right behind them) needs two missile infantry units (handgunners, archers, ...) per side. At the end it gives the control back. Log: units_scenario_log.txt in the game folder (one line per step), next to the
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
local INCLUDE_SINGLES = true -- K'daai / single models (all earlier rounds run again, so that no old bug comes back)
local INCLUDE_AURA = false -- the lord's aura round for the leadership probe: off
-- only these rounds run (nil = all rounds, as the user wants: every earlier case stays in the test)
local ONLY_ROUNDS = nil
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
-- ethereal units (ghosts: no lanes, no split) and the run speed in m/s of every unit, from the game's tables
local ETHEREAL = --ETHEREAL--
-- direct-fire guns (cannons, bolt throwers, organ guns), from the game's tables: the artillery role R prefers one (round 23)
local FIRE_GUNS = --FIRE_GUNS--
local SPEED = --SPEED--
local ENEMY_AHEAD = 200 -- the enemy army's passes run on a line this far in front of ours (test the AI's race too)
-- a SECOND army of the enemy alliance (Tomb Kings with skeleton spearmen and a chariot) gets the prefix 'T' and its own
-- line between the two (a chariot walks through its spearmen)
local THIRD_AHEAD = 100
local BACK = 45 -- a walker starts this far from the unit it walks through (60 until run 17)
local BEYOND = 40 -- and stops this far on the other side (50 until run 17)
local FIRE_BACK, FIRE_RANGE, FIRE_TICKS = 40, 150, 150 -- round 23: the standing unit 40 m in front of the gun, the target 150 m, fire for 30 s
local FLIP_TICKS = { 25, 30, 35, 40, 45 } -- round 29: the gun is ordered at the second target, back, second, back (1 s each: no new target counts), second (kept: counts)
local INTRUDER_TICK = 80 -- round 29: the enemy unit starts to walk at the stander with the lane for the line of fire (16 s into the round)
local AWAY_START = 12 -- a walker that walks AWAY starts this far from the stander's middle (rounds 19, away())
local SLOW_BACK, SLOW_BEYOND = 40, 35 -- the same for artillery (it is slow)
local REAR = 150 -- units that take no part wait this far behind the line
local AURA_TICKS = 125 -- step 8: the lord stands 25 s near unit A, then 25 s far from it, twice (for the leadership probe)
local AURA_NEAR, AURA_FAR = 8, 100 -- metres behind A
local AURA = { 'near', 'far', 'near', 'far' }
local ORDER_TIMER = 'units_scenario_orders'
local ORDER_DELAY = 2 -- an order is written down 0.4 s after it was seen, when walking / running shows
local TARGET_TIMER = 'units_scenario_targets'
local TARGET_EVERY = 5 -- the artillery's target is looked at every 5th look (1 s)
local TARGET_LIMIT = 200 -- lines of the target log per battle
-- artillery crews walk away from their guns after a teleport (known since run 37: 20 - 29 m): a few looks after it the unit is
-- ordered to its own spot (0.5 m back, with its bearing) so that the crew comes back, and halted again
local REGROUP_WAIT, REGROUP_HALT, REGROUP_STEP = 3, 15, 0.5
local ATTACK_TICKS = 60 -- a pass whose walker attacks (react 'attack') goes on this long (12 s) after the order

local state = { dead = false, started = false, ticks = 0, roles = {}, units = {}, ucs = {}, widths = {}, park = {}, regroup = {}, direct = {}, round = 0, phase = 'start', wait = START_TICKS }

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

-- a unit with a crew of its own (artillery, a crewed war machine)
local function crewed(unit)
	local kind = KIND[tostring(unit:type())]
	return kind == 'art' or yes(unit, 'is_artillery') or yes(unit, 'is_war_machine')
end

local function teleport(unit, point, bearing)
	uc_of(unit):teleport_to_location(v(point[1], point[2]), bearing, state.widths[unit:unique_ui_id()] or unit:ordered_width())
	if crewed(unit) then state.regroup[unit:unique_ui_id()] = { unit = unit, point = point, bearing = bearing, wait = REGROUP_WAIT } end
end

-- every look: the crews of teleported artillery rejoin their guns (an order to the unit's own spot, then a halt)
local function regroup_tick()
	for key, g in pairs(state.regroup) do
		g.wait = g.wait - 1
		if g.wait <= 0 and not g.ordered then
			g.ordered, g.wait = true, REGROUP_HALT
			local role = ''
			for name, r in pairs(state.roles) do
				if r == g.unit and (role == '' or #name < #role) then role = name end
			end
			local back = { g.point[1] - math.sin(math.rad(g.bearing)) * REGROUP_STEP, g.point[2] - math.cos(math.rad(g.bearing)) * REGROUP_STEP }
			local ok, problem = pcall(function()
				uc_of(g.unit):goto_location_angle_width(v(back[1], back[2]), g.bearing, state.widths[key] or g.unit:ordered_width(), false)
			end)
			log(('REGROUP %s (%s): ordered %.1f m back to its own spot at %.0f / %.0f, bearing %.0f, so that its crew rejoins the guns%s; halted again in %.0f s'):format(
				role ~= '' and role or '?', tostring(g.unit:type()), REGROUP_STEP, g.point[1], g.point[2], g.bearing, ok and '' or (' (FAILED: ' .. tostring(problem) .. ')'), REGROUP_HALT * TICK_MS / 1000))
		elseif g.wait <= 0 then
			pcall(function() uc_of(g.unit):halt() end)
			state.regroup[key] = nil
		end
	end
end

-- the walkers RUN (the default of the test passes)
local RUN = true
local function walk(unit, point, bearing)
	state.regroup[unit:unique_ui_id()] = nil -- a newer order: no regroup halt / goto over it
	uc_of(unit):goto_location_angle_width(v(point[1], point[2]), bearing, state.widths[unit:unique_ui_id()] or unit:ordered_width(), RUN)
end

local function name_of(role)
	local unit = state.roles[role]
	return unit and (role .. ' (' .. tostring(unit:type()) .. ', ' .. unit:number_of_men_alive() .. ' men)') or (role .. ' (missing)')
end

-- the five test units of the first army
-- the test roles of one army: prefix '' for ours, 'E' for the enemy's
local function shoots(unit) -- does it shoot (missile_range() > 0)?
	local ok, range = pcall(function() return unit:missile_range() end)
	return ok and type(range) == 'number' and range > 0
end

local function classify(units, prefix)
	local infantry, monstrous, missile = {}, {}, {}
	-- flying units get no role but the lord's: they fly over the others (run 36: the roles C and MC were flying Plague
	-- Drones, pass 2b showed nothing)
	for _, unit in ipairs(units) do
		state.widths[unit:unique_ui_id()] = unit:ordered_width()
	end
	local ground = {}
	local fastest = {} -- the fastest monstrous cavalry / infantry unit (a tie: the first)
	for _, unit in ipairs(units) do
		if not yes(unit, 'can_fly') or yes(unit, 'is_commanding_unit') then ground[#ground + 1] = unit end
	end
	for _, unit in ipairs(ground) do
		local men = unit:number_of_men_alive()
		local kind = KIND[tostring(unit:type())]
		local function role(name) state.roles[prefix .. name] = state.roles[prefix .. name] or unit end
		local speed = SPEED[tostring(unit:type())] or 0
		if kind == 'mc' or kind == 'mi' then
			if not fastest[kind] or speed > fastest[kind].speed then fastest[kind] = { unit = unit, speed = speed } end
		end
		if yes(unit, 'is_commanding_unit') then
			role('G')
		elseif ETHEREAL[tostring(unit:type())] then
			role('X') -- a ghost unit: only this role, so that no other pass changes
		elseif kind == 'art' or (not kind and yes(unit, 'is_artillery')) then
			-- artillery (it walks as a machine in pass 1c, stands with its crew in 2c); a direct-fire gun beats another one
			if FIRE_GUNS[tostring(unit:type())] and not state.direct[prefix .. 'R'] then
				state.roles[prefix .. 'R'], state.direct[prefix .. 'R'] = unit, true
			else
				role('R')
			end
		elseif kind == 'veh' or (not kind and (yes(unit, 'is_war_machine') or yes(unit, 'is_chariot'))) then
			role('V') -- a chariot or war machine (walks in round 4)
		elseif kind == 'char' then
			-- another character: no test role
		elseif kind == 'mc' then
			role('MC') -- monstrous cavalry (walks in round 4; also the cavalry of rounds 1 / 2 if there is no other)
			role('C')
		elseif yes(unit, 'is_cavalry') then
			role('C')
		elseif kind == 'inf' and shoots(unit) then
			missile[#missile + 1] = unit -- missile infantry (round 24)
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
	state.roles[prefix .. 'MS'], state.roles[prefix .. 'MS2'] = missile[1], missile[2]
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
	-- the fastest big bodies (extra roles: the unit may hold MC / MI as well)
	state.roles[prefix .. 'FMC'] = fastest.mc and fastest.mc.unit
	state.roles[prefix .. 'FMI'] = fastest.mi and fastest.mi.unit
end

-- the roles of further armies of an alliance (a big battle, allies of another race and a faction of another mod): their
-- 2nd and 3rd army
local ALLY_PREFIX = { 'L', 'K' }
local ENEMY_PREFIX = { 'T', 'H' }
local ROLE_NAMES = { 'A', 'W', 'A2', 'W2', 'C', 'MC', 'MI', 'R', 'V', 'M', 'G', 'X', 'FMC', 'FMI', 'MS', 'MS2' }

-- the roles of one alliance: its first army gets them; no faction has every kind of unit, so a role the first army lacks comes
-- from the next army of the same alliance (any order, reinforcements included) that has a unit for it. A unit lent to a role of
-- the first army is NOT also a role of its own army: the further armies get their roles after the lending, from what is left.
-- prefixes: the role prefixes of the further armies (their own passes), label: for the log
local LEND_ORDER = { 'A', 'W', 'A2', 'W2', 'C', 'MI', 'MC', 'R', 'V', 'M', 'G', 'X', 'FMC', 'FMI', 'MS', 'MS2' }
local INFANTRY = { 'A', 'W', 'A2', 'W2' }
local SAME_UNIT = { C = { MC = true, FMC = true }, MC = { C = true, FMC = true }, FMC = { C = true, MC = true }, MI = { FMI = true }, FMI = { MI = true } }

local function pick_alliance(first, armies, prefix, prefixes, label)
	classify(first, prefix)
	for i, army in ipairs(armies) do classify(army, '~' .. i) end -- what each further army would have
	local lent = {}
	local function held(unit, name) -- does the first army's set hold this unit for another kind of role?
		for _, other in ipairs(ROLE_NAMES) do
			if other ~= name and state.roles[prefix .. other] == unit and not (SAME_UNIT[name] and SAME_UNIT[name][other]) then return true end
		end
		return false
	end
	for _, name in ipairs(LEND_ORDER) do
		local mine = state.roles[prefix .. name]
		if not mine or (name == 'R' and not state.direct[prefix .. 'R']) then -- (a gun that is not a direct-fire one gives way to such a gun)
			for i, army in ipairs(armies) do
				local cands = { state.roles['~' .. i .. name] }
				for _, other in ipairs(INFANTRY) do
					if name ~= other and (name == 'A' or name == 'W' or name == 'A2' or name == 'W2') and state.roles['~' .. i .. other] then cands[#cands + 1] = state.roles['~' .. i .. other] end
				end
				if name == 'MC' and state.roles['~' .. i .. 'MI'] then cands[#cands + 1] = state.roles['~' .. i .. 'MI'] end
				local took = false
				for _, unit in ipairs(cands) do
					if not held(unit, name) and (not mine or state.direct['~' .. i .. 'R']) and not took then
						took = true
						state.roles[prefix .. name] = unit
						if name == 'R' then state.direct[prefix .. 'R'] = state.direct['~' .. i .. 'R'] end
						lent[unit:unique_ui_id()] = true
						log(('role %s from %s %d: %s (%s, %d men)'):format(prefix .. name, label, i + 1, name, tostring(unit:type()), unit:number_of_men_alive()))
					end
				end
				if took then break end
			end
		end
	end
	for i, army in ipairs(armies) do
		for _, name in ipairs(ROLE_NAMES) do state.roles['~' .. i .. name] = nil end
		state.direct['~' .. i .. 'R'] = nil
	end
	for i, army in ipairs(armies) do
		if prefixes[i] then
			local left = {}
			for _, unit in ipairs(army) do
				if not lent[unit:unique_ui_id()] then left[#left + 1] = unit end
			end
			classify(left, prefixes[i])
		end
	end
end

local function pick_roles()
	local armies = bm.battle:alliances():item(1):armies()
	local first = {}
	state.allies = {}
	for b = 1, armies:count() do
		local list = armies:item(b):units()
		local army = {}
		for index = 1, list:count() do
			state.units[#state.units + 1] = list:item(index)
			army[#army + 1] = list:item(index)
			if b == 1 then first[#first + 1] = list:item(index) end
		end
		if b >= 2 then state.allies[#state.allies + 1] = army end
	end
	-- our roles from our own army; a role it lacks from the allies (run 36: our Skaven had no cavalry), then the allies' own
	pick_alliance(first, state.allies, '', ALLY_PREFIX, 'allied army')
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
	pick_alliance(state.enemy or {}, state.foes, 'E', ENEMY_PREFIX, 'enemy army') -- (the roles of the enemy's further armies too)
	local foe_rows = { n = 0 }
	park_rows(state.enemy or {}, state.eo, REAR, -1, foe_rows)
	state.to = at(state.o, THIRD_AHEAD, 0) -- the line of the further enemy armies' passes
	for i, army in ipairs(state.foes) do -- each waits in a row behind the enemy's row
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

-- a pass whose stander stands in guard mode (the 'defend' behaviour); all others have it switched off
local function guarded(p)
	p.guard = true
	return p
end

-- a second walker through the same stander as an earlier pass of walker role `first` (ordered first_off m beside the stander's middle), which has
-- stood at ITS goal since (085d, run 52: MS2 ended inside MS, which had stood only 3.2 s: MISSED?). The goal lies `lag` m short of that one's
-- (as its start lies lag m further back) and across by that walker's width + 4 m from it, so that it does not end inside it
local function beside(p, first, first_off)
	p.beside, p.beside_off = first, first_off
	return p
end

-- a pass whose stander may be missing: then one of these stand-ins (their army had no second infantry unit EA2, so the
-- chariot's only pass was skipped)
local function or_through(p, alts)
	p.alts = alts
	return p
end

-- a pass whose walker is ordered to stop SHORT of the stander ('short': its front <gap> m before the stander's front), PAST it
-- ('past': its rear <gap> m behind the stander's back rank) or INSIDE it ('inside': its middle at the stander's middle).
-- Depths are estimated from the men and the width (the script interface knows no depth).
local function stops(p, kind, gap)
	p.stop = { kind = kind, gap = gap }
	return p
end

-- a pass in which something changes while the lanes are open: 'sideways' = the walker is ordered 30 m to the side (90 deg)
-- when its front is inside the stander; 'stander_back' = the stander is ordered 20 m back when the walker is 30 m from it;
-- 'attack' = the walker has stopped inside the stander, then an enemy unit is put 12 m beyond it and the walker is ordered to attack it
-- (both units must re-form after ~5 s, also while it fights)
local function reacts(p, what)
	p.react = what
	return p
end

-- about how deep a unit is: its ranks of 1.8 m (files of 1.8 m across its width)
local function depth_of(unit)
	local men, width = unit:number_of_men_alive(), state.widths[unit:unique_ui_id()] or unit:ordered_width()
	return math.max(2, math.ceil(men / math.max(1, width / 1.8)) * 1.8)
end

-- a pass whose walker walks AWAY from the stander: it starts AWAY_START m from its middle on the side it would come from,
-- facing away, and walks BEYOND m further away; it never crosses the stander
local function away(p)
	p.away = true
	return p
end

-- a pass whose walker walks at the stander as usual and is ordered back to its start when it is within <metres> of the
-- stander's middle
local function turn_back(p, metres)
	p.turn_back = metres
	return p
end

-- a pass through TWO standing units: <stander2> stands middle_gap m behind the first along the way, the walker's goal lies
-- BEYOND m past the second
local function two_lines(p, stander2, middle_gap)
	p.stander2, p.gap2 = stander2, middle_gap
	return p
end

-- a pass in which the artillery piece <walker> (role) stands 40 m behind the stander and fires at the unit <target> (role)
-- 110 m beyond it, 150 m from the gun, for 30 s: the stander stands in the line of fire (round 23)
local function fires_at(p, target)
	p.target = target
	return p
end

-- round 29 (085d): the same, and (target2) a second enemy unit stands beside the target - the gun is ordered at it and back at the first one
-- three times a second apart (the lane for the line of fire must not re-form for that), then kept on it - and (intruder) an enemy unit walks at the
-- stander from beside the target after 16 s: the lane must close when it is near (15 m)
local function flips_and_intrudes(p, target2, intruder)
	p.target2, p.intruder = target2, intruder
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
	-- FAST big bodies (the fastest monstrous cavalry / infantry run through standing infantry): today they keep their
	-- formation and need one wide lane, the standers open too late or not at all; the goal is columns + normal lanes in time
	{ number = 13, or_through(pass('13a', 'FMC', 'A', 'behind', -SPREAD), { 'W' }), or_through(pass('13b', 'FMI', 'A2', 'behind', 0), { 'W2' }),
		or_through(pass('13c', 'EFMC', 'EA', 'behind', -SPREAD), { 'EW' }), or_through(pass('13d', 'EFMI', 'EA2', 'behind', 0), { 'EW2' }) },
	-- the same from a close start (30 m instead of 45) and at an angle: the standers have even less time
	{ number = 14, or_through(pass('14a', 'FMC', 'A2', 'diagonal', -SPREAD, false, 0, -15), { 'W2', 'W' }),
		or_through(pass('14b', 'FMI', 'A', 'behind', 0, false, 0, -15), { 'W', 'W2' }),
		or_through(pass('14c', 'EFMC', 'EA2', 'diagonal', -SPREAD, false, 0, -15), { 'EW2', 'EW' }),
		or_through(pass('14d', 'EFMI', 'EA', 'behind', 0, false, 0, -15), { 'EW', 'EW2' }) },
	-- GUARD MODE standers: the models of a unit in guard mode stay inside its outline, so the lanes are narrower and the
	-- walker narrows its columns
	{ number = 15, guarded(pass('15a', 'W', 'A', 'behind', -SPREAD)), guarded(pass('15b', 'FMC', 'A2', 'behind', 0)),
		or_through(guarded(pass('15c', 'C', 'W2', 'behind', SPREAD)), { 'A', 'W' }),
		guarded(pass('15d', 'EW', 'EA', 'behind', -SPREAD)), guarded(pass('15e', 'EFMC', 'EA2', 'behind', 0)) },
	-- guard mode with two neighbours 4 m apart (round 3 again): they must not push into each other, the lanes stay narrow
	{ number = 16, guarded(pass('16a', 'W', 'A', 'behind', 'left')), guarded(pass('16b', 'W2', 'A2', 'behind', 'right')),
		guarded(pass('16c', 'EW', 'EA', 'behind', 'left')), guarded(pass('16d', 'EW2', 'EA2', 'behind', 'right')) },
	-- ETHEREAL units (ghosts): no lanes, no split, no columns; the ghost keeps its formation, whoever walks through whom
	{ number = 17, pass('17a', 'X', 'A', 'behind', 0), pass('17c', 'EX', 'EA', 'behind', 0) }, -- a ghost walks through infantry
	{ number = 18, pass('18a', 'W', 'X', 'behind', 0), pass('18b', 'EW', 'EX', 'behind', 0) }, -- infantry walks through a standing ghost unit
	-- a walker that walks AWAY from a standing unit (12 m from its middle) must not open lanes, nor make the unit step aside
	{ number = 19, away(pass('19a', 'W', 'A', 'behind', -SPREAD)), away(pass('19b', 'W2', 'A2', 'front', 0)),
		away(pass('19c', 'EW', 'EA', 'behind', -SPREAD)), away(pass('19d', 'EW2', 'EA2', 'front', 0)) },
	-- a walker that turns back 15 / 20 m before the standing unit: the lanes close soon after
	{ number = 20, turn_back(pass('20a', 'W', 'A', 'behind', -SPREAD), 15), turn_back(pass('20b', 'C', 'A2', 'behind', 0), 20),
		turn_back(pass('20c', 'EW', 'EA', 'behind', -SPREAD), 15), turn_back(pass('20d', 'EC', 'EA2', 'behind', 0), 20) },
	-- TWO lines one behind the other (middles 30 m apart): the walker's columns pass on from the first to the second
	{ number = 21, two_lines(pass('21a', 'W', 'A', 'behind', 0), 'A2', 30), two_lines(pass('21b', 'EW', 'EA', 'behind', 0), 'EA2', 30) },
	-- the same with the lines close together (14 m), cavalry
	{ number = 22, two_lines(pass('22a', 'C', 'A', 'behind', 0), 'A2', 14), two_lines(pass('22b', 'EC', 'EA', 'behind', 0), 'EA2', 14) },
	-- the line of fire: our gun R fires at the enemy EA with A standing in the line; the enemy's ER at our A2 through EA2
	{ number = 23, fires_at(pass('23a', 'R', 'A', 'behind', -SPREAD), 'EA'), fires_at(pass('23b', 'ER', 'EA2', 'behind', SPREAD), 'A2') },
	-- RANGED walkers (missile infantry, artillery that walks) go through a standing unit, one after another (the second 10 m
	-- behind the first): the standing unit closes right behind the last of them
	{ number = 24, pass('24a', 'MS', 'A', 'behind', -SPREAD), beside(pass('24a2', 'MS2', 'A', 'behind', -SPREAD, false, 0, 10), 'MS', 0), pass('24b', 'R', 'A2', 'behind', SPREAD, true),
		pass('24c', 'EMS', 'EA', 'behind', -SPREAD), beside(pass('24c2', 'EMS2', 'EA', 'behind', -SPREAD, false, 0, 10), 'EMS', 0), pass('24d', 'ER', 'EA2', 'behind', SPREAD, true) },
	-- ORDERS that end short of / inside / past a standing unit, and orders that change while the lanes are open (the player report
	-- "troops line up in long lines and do not recover"): every pass logs where the walker's order ends relative to the stander
	{ number = 25, stops(pass('25a', 'W', 'A', 'behind', -SPREAD), 'short', 5), stops(pass('25b', 'W2', 'A2', 'behind', 0), 'past', 3),
		or_through(stops(pass('25c', 'C', 'MS', 'behind', SPREAD), 'inside', 0), { 'MS2', 'MI' }),
		reacts(pass('25d', 'MS2', 'MI', 'behind', 1.5 * SPREAD), 'sideways'), reacts(pass('25e', 'G', 'MC', 'behind', -1.5 * SPREAD), 'stander_back'),
		stops(pass('25f', 'EW', 'EA', 'behind', -SPREAD), 'short', 5), stops(pass('25g', 'EW2', 'EA2', 'behind', 0), 'past', 3),
		or_through(stops(pass('25h', 'EC', 'EMS', 'behind', SPREAD), 'inside', 0), { 'EMS2', 'EMI' }),
		reacts(pass('25i', 'EMS2', 'EMI', 'behind', 1.5 * SPREAD), 'sideways'), reacts(pass('25j', 'EG', 'EMC', 'behind', -1.5 * SPREAD), 'stander_back') },	-- a walker PARKED inside a standing unit, then a fight (an enemy unit is put beside it and attacked): both units must re-form
	-- ~5 s after it stopped moving, also while it fights (the lanes close and both get their places handed out anew)
	{ number = 26, reacts(stops(pass('26a', 'W', 'A', 'behind', -SPREAD), 'inside', 0), 'attack'), reacts(stops(pass('26b', 'EW', 'EA', 'behind', -SPREAD), 'inside', 0), 'attack') },
	-- FAST monstrous cavalry (columns like infantry) OFF-CENTRE through a stander in GUARD mode (27a: 9 m to the right of its middle),
	-- and through a normal one 9 m to the left (27b): the lanes of an off-centre crossing, narrowed in guard mode
	{ number = 27, guarded(or_through(pass('27a', 'FMC', 'A', 'behind', -SPREAD, false, 9), { 'W' })), or_through(pass('27b', 'EFMC', 'EA', 'behind', -SPREAD, false, -9), { 'EW' }) },
	-- TWO missile units SIDE BY SIDE (their middles 24 m apart) through ONE stander, the second starting 10 m later: each walks in its own
	-- lane, the one through first lets its lane close (HALF CLOSE) while the other is still crossing
	{ number = 28, pass('28a', 'MS', 'A', 'behind', 0, false, -12), beside(pass('28a2', 'MS2', 'A', 'behind', 0, false, 12, 10), 'MS', -12),
		pass('28b', 'EMS', 'EA', 'behind', 0, false, -12), beside(pass('28b2', 'EMS2', 'EA', 'behind', 0, false, 12, 10), 'EMS', -12) },
	-- the line of fire with a cannon that changes its target and an enemy unit that walks at the stander (085d)
	{ number = 29, flips_and_intrudes(fires_at(pass('29a', 'R', 'A', 'behind', -SPREAD), 'EA'), 'EW2', 'EW'),
		flips_and_intrudes(fires_at(pass('29b', 'ER', 'EA2', 'behind', SPREAD), 'A2'), 'W2', 'W') },
}

local function stand_at(p)
	local base = p.base or state.o
	if p.where == 'left' or p.where == 'right' then
		local half = (state.widths[state.roles[p.stander]:unique_ui_id()] or 30) / 2
		return at(base, 0, (p.where == 'left' and -1 or 1) * (half + NEIGHBOUR_GAP / 2))
	end
	return at(base, 0, p.where)
end

-- round 23: the gun stands FIRE_BACK m behind the stander (towards its own side), the target FIRE_RANGE - FIRE_BACK m beyond it
local function setup_fire(p)
	local gun, stander, target = state.roles[p.walker], state.roles[p.stander], state.roles[p.target]
	local s = stand_at(p)
	local d = { state.f[1], state.f[2] }
	if p.walker:sub(1, 1) == 'E' then d = { -d[1], -d[2] } end -- the enemy's gun fires towards us
	local b = bearing_of(d[1], d[2])
	local gun_at = { s[1] - d[1] * FIRE_BACK, s[2] - d[2] * FIRE_BACK }
	local target_at = { s[1] + d[1] * (FIRE_RANGE - FIRE_BACK), s[2] + d[2] * (FIRE_RANGE - FIRE_BACK) }
	p.walked, p.done, p.gun = 0, false, gun
	teleport(stander, s, b)
	pcall(function() uc_of(stander):change_behaviour_active('defend', false) end)
	teleport(target, target_at, bearing_of(-d[1], -d[2]))
	teleport(gun, gun_at, b)
	local second, intruder = p.target2 and state.roles[p.target2], p.intruder and state.roles[p.intruder]
	p.second, p.intruder_unit = second, intruder
	if second then -- beside the target (its width apart), the gun flips between them
		local half = ((state.widths[target:unique_ui_id()] or 30) + (state.widths[second:unique_ui_id()] or 30)) / 2 + 4
		teleport(second, { target_at[1] + d[2] * half, target_at[2] - d[1] * half }, bearing_of(-d[1], -d[2]))
	end
	if intruder then -- 70 m beyond the stander and 45 m to the side of the line: out of the way until it is sent
		teleport(intruder, { s[1] + d[1] * 70 - d[2] * 45, s[2] + d[2] * 70 + d[1] * 45 }, bearing_of(-d[1], -d[2]))
		p.intruder_goal, p.intruder_bearing = { s[1] + d[1] * 8, s[2] + d[2] * 8 }, bearing_of(-d[1], -d[2])
	end
	log(('PASS %s: %s fires at %s through %s (%s fires at %s, %s stands in its line at %.0f / %.0f, %.0f m from the gun; the gun at %.0f / %.0f, the target at %.0f / %.0f, %.0f m from the gun)'):format(
		p.name, p.walker, p.target, p.stander, name_of(p.walker), name_of(p.target), name_of(p.stander), s[1], s[2], FIRE_BACK, gun_at[1], gun_at[2],
		target_at[1], target_at[2], FIRE_RANGE))
	return true
end

-- the gun fires: fire at will on and an order to attack the target (what works is written down)
local function start_fire(p)
	local uc = uc_of(p.gun)
	state.regroup[p.gun:unique_ui_id()] = nil -- the gun's order comes after any regroup halt: no halt over it
	local on = pcall(function() uc:change_behaviour_active('fire_at_will', true) end)
	local ordered, why = pcall(function() uc:attack_unit(state.roles[p.target]) end)
	log(('PASS %s: %s fires for %d s: fire at will on %s, attack_unit %s%s; ammo %s, target %s men'):format(p.name, p.walker, FIRE_TICKS * TICK_MS / 1000, on and 'worked' or 'FAILED',
		ordered and 'worked' or 'FAILED', ordered and '' or (' (' .. tostring(why) .. ')'), tostring(p.gun:ammo_left()), tostring(state.roles[p.target]:number_of_men_alive())))
end

local function stop_fire(p)
	pcall(function() uc_of(p.gun):change_behaviour_active('fire_at_will', false) end)
	pcall(function() uc_of(p.gun):halt() end)
	log(('PASS %s done after %.1f s: fire at will off again; ammo %s, target %s men'):format(p.name, p.walked * TICK_MS / 1000, tostring(p.gun:ammo_left()), tostring(state.roles[p.target]:number_of_men_alive())))
end

local function setup_pass(p)
	local walker, stander = state.roles[p.walker], state.roles[p.stander]
	if not walker or not stander then
		log(('PASS %s skipped: %s %s %s - a unit is missing'):format(p.name, name_of(p.walker), p.target and 'fires through' or 'walks through', name_of(p.stander)))
		return false
	end
	if p.target then return setup_fire(p) end
	local s = stand_at(p)
	local d = { state.f[1], state.f[2] }
	if p.side == 'front' then d = { -d[1], -d[2] } end
	if p.side == 'diagonal' then d = { (state.f[1] + state.r[1]) / math.sqrt(2), (state.f[2] + state.r[2]) / math.sqrt(2) } end
	local b = bearing_of(d[1], d[2])
	local back, beyond = (p.slow and SLOW_BACK or BACK) + p.lag, p.slow and SLOW_BEYOND or BEYOND
	local ox, oz = d[2] * p.off, -d[1] * p.off -- beside the stander's middle, square to the way
	local stander2 = p.stander2 and state.roles[p.stander2]
	local s2 = stander2 and { s[1] + d[1] * p.gap2, s[2] + d[2] * p.gap2 } -- the second line, behind the first along the way
	local last = s2 or s
	if p.away then -- starts AWAY_START m from the stander's middle and walks away from it
		p.start = { s[1] - d[1] * AWAY_START + ox, s[2] - d[2] * AWAY_START + oz }
		p.goal = { p.start[1] - d[1] * beyond, p.start[2] - d[2] * beyond }
		b = bearing_of(-d[1], -d[2])
	else
		p.start = { s[1] - d[1] * back + ox, s[2] - d[2] * back + oz }
		p.goal = { last[1] + d[1] * beyond + ox, last[2] + d[2] * beyond + oz }
	end
	p.stop_note = ''
	if p.beside and not p.away and not p.stop then
		local first = state.roles[p.beside]
		local fw = first and (state.widths[first:unique_ui_id()] or first:ordered_width()) or 0
		local goal_off, goal_beyond = p.beside_off + fw + 4, beyond - p.lag
		p.goal = { last[1] + d[1] * goal_beyond + d[2] * goal_off, last[2] + d[2] * goal_beyond - d[1] * goal_off }
		p.stop_note = ("; its goal lies %.0f m short of %s's and %.0f m across from it (that walker is %.0f m wide)"):format(p.lag, p.beside, goal_off - p.beside_off, fw)
	end
	if p.stop and not p.away then -- the order ends before / inside / past the stander
		local ds, dw = depth_of(stander), depth_of(walker)
		local along = p.stop.kind == 'short' and -(ds / 2 + p.stop.gap + dw / 2) or (p.stop.kind == 'past' and (ds / 2 + p.stop.gap + dw / 2) or 0)
		p.goal = { s[1] + d[1] * along + ox, s[2] + d[2] * along + oz }
		p.stop_note = ("; the walker is ordered to stop %.0f m %s the stander's middle (%s; estimated depths: stander %.0f m, walker %.0f m)"):format(
			math.abs(along), along < 0 and 'before' or (along > 0 and 'behind' or 'at'),
			p.stop.kind == 'short' and ('its front %d m before the stander\'s front'):format(p.stop.gap) or (p.stop.kind == 'past' and ('its rear %d m behind the stander\'s back rank'):format(p.stop.gap) or "its middle at the stander's middle"), ds, dw)
	end
	p.dir = d
	p.reacted = false
	p.bearing = b
	p.back_bearing = bearing_of(-d[1], -d[2]) -- facing its start again (turn_back)
	p.mid = s
	p.walked, p.still, p.done, p.turned = 0, 0, false, false
	teleport(stander, s, state.b0) -- the standing unit stands in good order where it belongs
	if s2 then
		teleport(stander2, s2, state.b0)
		pcall(function() uc_of(stander2):change_behaviour_active('defend', p.guard == true) end)
	end
	-- guard mode ('defend') is set explicitly in every pass, so that a unit in guard mode by default does not skew the others
	pcall(function() uc_of(stander):change_behaviour_active('defend', p.guard == true) end)
	local read_ok, read_value = pcall(function() return stander:is_behaviour_active('defend') end)
	local guard_read = (read_ok and (read_value == true and 'yes' or (read_value == false and 'no' or 'unknown'))) or 'unknown'
	teleport(walker, p.start, b)
	log(('PASS %s: %s walks through %s (%s%s): from %.0f / %.0f to %.0f / %.0f, bearing %.0f; %s stands at %.0f / %.0f, bearing %.0f, guard mode %s (read back: %s)%s'):format(p.name,
		name_of(p.walker), name_of(p.stander), p.side,
		(p.off ~= 0 and (', %.0f m beside its middle'):format(p.off) or '') .. (p.lag ~= 0 and (', %.0f m further back'):format(p.lag) or ''),
		p.start[1], p.start[2], p.goal[1], p.goal[2], b, p.stander, s[1], s[2], state.b0, p.guard and 'on' or 'off', guard_read,
		p.stop_note .. (p.away and ('; walks AWAY from it, starting %d m from its middle'):format(AWAY_START) or '')
		.. (p.turn_back and ('; turns back %.0f m before its middle'):format(p.turn_back) or '')
		.. (s2 and ('; second line %s (%s, %d men) %.0f m behind its middle, at %.0f / %.0f'):format(p.stander2, tostring(stander2:type()), stander2:number_of_men_alive(), p.gap2, s2[1], s2[2]) or '')))
	return true
end

-- every unit that takes no part in the round goes to its place in the row behind its line, under the script's control
-- again and halted (allied Fiends of Slaanesh ran about the whole scenario, enemy units fought; control was taken and
-- the enemy halted only once, at the start)
local function hold(unit)
	local uc = uc_of(unit)
	pcall(function() uc:take_control() end)
	pcall(function() uc:halt() end)
	pcall(function() uc:change_behaviour_active('defend', false) end) -- (parked units: no guard mode)
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
	if ONLY_ROUNDS and not ONLY_ROUNDS[r.number] then
		log(('ROUND %d skipped: not in ONLY_ROUNDS'):format(r.number))
		return false
	end
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
		local s2 = (p.stander2 or p.target) and state.roles[p.stander2 or p.target] -- (the second line of two_lines, or the target of a gun)
		-- (one stander for two walkers is meant: rounds 7 - 10)
		if w and s and (p.stander2 or p.target) and not s2 then
			log(('PASS %s skipped: %s walks through %s and %s - a unit is missing'):format(p.name, name_of(p.walker), name_of(p.stander), name_of(p.stander2 or p.target)))
			p.clash = true
		elseif w and s and (used[w] or walkers[s] or w == s or (s2 and (used[s2] or s2 == w or s2 == s))) then
			log(('PASS %s skipped: %s walks through %s - a unit is in another pass of this round'):format(p.name, name_of(p.walker), name_of(p.stander)))
			p.clash = true
		elseif w and s then
			used[w], used[s], walkers[w] = true, true, true
			if s2 then used[s2] = true end
			for _, extra in ipairs({ p.target2 or false, p.intruder or false }) do
				if extra and state.roles[extra] and not used[state.roles[extra]] then used[state.roles[extra]] = true end
			end
			p.clash = nil
		end
		local army = p.walker:sub(1, 1)
		p.base = (army == 'E' and state.eo) or ((army == 'T' or army == 'H') and state.to) or state.o
	end
	park_all_but(used)
	r.used = used
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
	local function speed_of(role)
		local unit = state.roles[role]
		local speed = unit and SPEED[tostring(unit:type())]
		return name_of(role) .. (speed and (' %.1f m/s'):format(speed) or '')
	end
	log(('fast big bodies / ethereal: %s, %s, %s; enemy %s, %s, %s'):format(speed_of('FMC'), speed_of('FMI'), speed_of('X'), speed_of('EFMC'), speed_of('EFMI'), speed_of('EX')))
	log(('missile infantry: %s, %s; enemy %s, %s'):format(name_of('MS'), name_of('MS2'), name_of('EMS'), name_of('EMS2')))
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

-- after the scenario: what the artillery shoots at (read only, for the line-of-fire idea) ------------------------------
-- every 1 s, for every artillery unit of both sides: unit:current_target(); only CHANGES are written down

local function is_art(unit)
	if yes(unit, 'is_artillery') then return true end
	local ok, class = pcall(function() return unit:unit_class() end)
	return ok and type(class) == 'string' and string.sub(class, 1, 3) == 'art'
end

-- one line of the target log; the last one says that the limit is reached (and the timer stops)
local function target_line(text)
	local t = state.targets
	if t.full then return end
	if t.count >= TARGET_LIMIT then
		t.full = true
		log('TARGET LOG: limit reached')
		pcall(function() bm:remove_callback(TARGET_TIMER) end)
		return
	end
	t.count = t.count + 1
	log(text)
end

local function watch_targets()
	local t = state.targets
	t.looks = t.looks + 1
	if t.looks % TARGET_EVERY ~= 0 then return end
	for _, unit in ipairs(t.units) do
		local key = unit:unique_ui_id()
		local ok, result = pcall(unit.current_target, unit) -- (a missing or failing method is an answer too)
		local kind = ok and type(result) or 'error'
		local uid, text = nil, ''
		if ok and (kind == 'table' or kind == 'userdata') then
			local uok, id = pcall(function() return result:unique_ui_id() end)
			if uok then
				uid = id
				local nok, name = pcall(function() return result:type() end)
				local dok, dist = pcall(function()
					local x, z = xz(unit)
					local tx, tz = xz(result)
					return math.sqrt((tx - x) ^ 2 + (tz - z) ^ 2)
				end)
				text = (', target #%s %s, %s'):format(tostring(uid), nok and tostring(name) or '?', dok and ('%.0f m'):format(dist) or 'distance unknown')
			end
		elseif not ok then
			text = ' (' .. tostring(result) .. ')'
		end
		local sig = kind .. ':' .. tostring(uid)
		if t.seen[key] ~= sig then
			t.seen[key] = sig
			local aok, ammo = pcall(function() return unit:ammo_left() end)
			local rok, range = pcall(function() return unit:missile_range() end)
			target_line(('TARGET %s: %s%s; ammo %s, missile range %s'):format(label_of(unit), kind, text, aok and tostring(ammo) or 'n/a', rok and tostring(range) or 'n/a'))
		end
		if ok and result ~= nil and not t.raw[key] then -- the first answer that is not nil, as the game prints it
			t.raw[key] = true
			target_line(('TARGET %s: first result %s'):format(label_of(unit), tostring(result)))
		end
	end
end

local function start_target_log()
	local list = {}
	local alliances = bm.battle:alliances()
	for a = 1, alliances:count() do
		local armies = alliances:item(a):armies()
		for b = 1, armies:count() do
			local units = armies:item(b):units()
			for index = 1, units:count() do
				if is_art(units:item(index)) then list[#list + 1] = units:item(index) end
			end
		end
	end
	state.targets = { units = list, seen = {}, raw = {}, looks = 0, count = 0 }
	log(('from now on the targets of the %d artillery units of both sides are written down (TARGET lines, changes only, at most %d)'):format(#list, TARGET_LIMIT))
	bm:repeat_callback(function()
		if state.targets_dead then return end
		local ok, problem = pcall(watch_targets)
		if not ok then
			state.targets_dead = true
			log('TARGET LOG STOPPED by an error: ' .. tostring(problem))
			pcall(function() bm:remove_callback(TARGET_TIMER) end)
		end
	end, TICK_MS, TARGET_TIMER)
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
		local tok, tproblem = pcall(start_target_log)
		if not tok then log('TARGET LOG not started: ' .. tostring(tproblem)) end
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
	regroup_tick()
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
				for _, p in ipairs(r.active) do -- all at once
					if p.target then start_fire(p) else walk(state.roles[p.walker], p.goal, p.bearing) end
				end
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
			if not p.done and p.target then -- a gun: it fires for FIRE_TICKS, then fire at will goes off again
				p.walked = p.walked + 1
				if p.second then -- (round 29) the gun's target changes: second, first, second, first, second (kept)
					for i, tick in ipairs(FLIP_TICKS) do
						if p.walked == tick then
							local aim = (i % 2 == 1) and p.second or state.roles[p.target]
							local ok = pcall(function() uc_of(p.gun):attack_unit(aim) end)
							log(('PASS %s: %s ordered to attack %s (%s)'):format(p.name, p.walker, (i % 2 == 1) and p.target2 or p.target, ok and 'worked' or 'FAILED'))
						end
					end
				end
				if p.intruder_unit and p.walked == INTRUDER_TICK then -- (round 29) an enemy unit walks at the stander
					walk(p.intruder_unit, p.intruder_goal, p.intruder_bearing)
					log(('PASS %s: %s sent to walk at %s (the lane for the line of fire must close when it is 15 m away)'):format(p.name, p.intruder, p.stander))
				end
				if p.walked >= FIRE_TICKS then
					p.done = true
					stop_fire(p)
				else
					walking = walking + 1
				end
			elseif not p.done then
				local walker = state.roles[p.walker]
				p.walked = p.walked + 1
				if walker:is_moving() then p.still = 0 else p.still = p.still + 1 end
				local x, z = xz(walker)
				local left = math.sqrt((p.goal[1] - x) ^ 2 + (p.goal[2] - z) ^ 2)
				if p.turn_back and not p.turned then
					local near = math.sqrt((p.mid[1] - x) ^ 2 + (p.mid[2] - z) ^ 2)
					if near <= p.turn_back then -- close enough: back to where it came from
						p.turned, p.still = true, 0
						walk(walker, p.start, p.back_bearing)
						log(("PASS %s: %s TURNED BACK %.1f m from %s's middle"):format(p.name, p.walker, near, p.stander))
					end
				end
				if p.react and not p.reacted then
					local near = math.sqrt((p.mid[1] - x) ^ 2 + (p.mid[2] - z) ^ 2)
					local stander = state.roles[p.stander]
					if p.react == 'sideways' and near <= depth_of(stander) / 2 + depth_of(walker) / 2 + 1 then
						p.reacted, p.still = true, 0
						p.goal = { x + state.r[1] * 30, z + state.r[2] * 30 } -- (the pass ends there)
						walk(walker, p.goal, bearing_of(state.r[1], state.r[2]))
						log(("PASS %s: %s RE-ORDERED 30 m to the side (90 deg) while its front is inside %s (%.1f m from its middle)"):format(p.name, p.walker, p.stander, near))
					elseif p.react == 'stander_back' and near <= 30 then
						p.reacted = true
						walk(stander, { p.mid[1] - p.dir[1] * 20, p.mid[2] - p.dir[2] * 20 }, state.b0)
						log(("PASS %s: %s (the stander) ORDERED 20 m back while the walker %s is %.1f m from its middle"):format(p.name, p.stander, p.walker, near))
					elseif p.react == 'attack' and p.walked > 15 and p.still >= STILL_TICKS then -- parked inside: a foe comes and it fights
						local foes = p.walker:sub(1, 1) == 'E' and state.units or (state.enemy or {})
						local foe = nil
						for _, unit in ipairs(foes) do
							if not foe and not state.current.used[unit] then foe = unit end
						end
						p.reacted, p.reacted_at, p.still = true, p.walked, 0
						if foe then
							teleport(foe, { p.mid[1] + p.dir[1] * 12, p.mid[2] + p.dir[2] * 12 }, bearing_of(-p.dir[1], -p.dir[2]))
							state.regroup[walker:unique_ui_id()] = nil
							local ok, why = pcall(function() uc_of(walker):attack_unit(foe) end)
							log(("PASS %s: %s parked inside %s, an enemy unit (%s, %d men) put 12 m beyond it, attack_unit %s%s"):format(p.name, p.walker, p.stander, tostring(foe:type()), foe:number_of_men_alive(),
								ok and 'worked' or 'FAILED', ok and '' or (' (' .. tostring(why) .. ')')))
						else
							log(("PASS %s: %s parked inside %s, no enemy unit left to attack"):format(p.name, p.walker, p.stander))
						end
					end
				end
				local timeout = p.slow and SLOW_TIMEOUT_TICKS or TIMEOUT_TICKS
				local holding = p.react == 'attack' and (not p.reacted or p.walked - p.reacted_at < ATTACK_TICKS) -- (the fight goes on 12 s)
				if (p.walked > 15 and p.still >= STILL_TICKS and not holding) or p.walked >= timeout then
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
		pcall(function() bm:remove_callback(TARGET_TIMER) end)
		if state.orders and not state.orders_dead then
			state.orders_dead = true
			pcall(function() bm:remove_callback(ORDER_TIMER) end)
			log('battle ended: order log closed')
		end
	end)
end

local ok, problem = pcall(start)
if not ok then log('SCENARIO ERROR at load: ' .. tostring(problem)) end
