-- Units Pass Through - LEADERSHIP PROBE --VERSION-- (test pack). Only reads, changes nothing.
-- Looks for the place of a unit's current leadership in memory: the script interface has only is_wavering /
-- is_routing. Every number in the unit's object (and in the objects it points to) that could be a leadership value
-- is watched for the whole battle; a candidate scores when it
--   * falls as the unit loses men (correlation with the men alive),
--   * is lower while the unit wavers or routs than while it is steady,
--   * equals the leadership number the game's own unit panel shows (when the player selects units),
--   * and does not jump about like a timer or a position.
-- The same place in different units counts together. Log: units_leader_probe_log.txt (a summary every 60 s and at
-- the end of the battle).

local LOG_FILE = 'units_leader_probe_log.txt'
local TIMER = 'units_leader_probe_tick'
local TICK_MS = 100 -- one unit is read per tick
local UNIT_BYTES = 0x1800 -- read of the unit object
local POINTER_BYTES = 0x400 -- of the unit object, these first bytes are looked at for pointers to further objects
local SUB_BYTES = 0x400 -- read of each such object
local MAX_SUBS = 16 -- run 15: 48 gave 52000 places, 3 ms per tick and 40-50 ms hitches
local LOW, HIGH = -150, 300 -- a leadership value lies in here
local UI_EVERY = 0 -- off since run 16: the panel shows leadership only as a bar (run 15: bar_morale, no number), and the walk made hitches
local UI_MAX_NODES = 6000
local SUMMARY_EVERY = 600 -- ticks (60 s)
local TOP = 25
local AURA_NEAR, AURA_FAR = 20, 60 -- metres from the unit's own general: in / out of his leadership aura
local P_UID = '--P_UID--'
local UID_OFF = --UID_OFF--
local IMAGE_SIZE = --IMAGE_SIZE--

local mr = nil
local state = { started = false, dead = false, units = {}, ticks = 0, next = 1, paths = {}, ui = {}, ui_numbers = {}, ui_ids = {}, base = nil, cost = 0, longest = 0 }

local function log(message)
	local file = io.open(LOG_FILE, state.started and 'a' or 'w')
	state.started = true
	if not file then return end
	file:write(os.date('%H:%M:%S') .. ' ' .. tostring(message) .. '\n')
	file:close()
end

-- the 4 bytes at i (1-based) of a string: unsigned number and float (nil when not an ordinary float)
local function dword(raw, i)
	local a, b, c, d = raw:byte(i, i + 3)
	if not d then return nil, nil end
	local n = a + b * 256 + c * 65536 + d * 16777216
	local exponent = math.floor(n / 8388608) % 256
	if exponent == 255 then return n, nil end
	local fraction = n % 8388608
	local value
	if exponent == 0 then value = math.ldexp(fraction, -149) else value = math.ldexp(fraction + 8388608, exponent - 150) end
	if n >= 2147483648 then value = -value end
	return n, value
end

-- a candidate's value: floats only for "f" places, whole numbers for "i" places
local function value_of(raw, i, kind)
	local n, f = dword(raw, i)
	if not n then return nil end
	if kind == 'i' then
		if n >= 2147483648 then n = n - 4294967296 end
		return n
	end
	return f
end

local function plausible_float(f)
	return f and f >= LOW and f <= HIGH and f ~= 0 and (math.abs(f) >= 0.01)
end

local function plausible_int(n)
	return n and n >= 1 and n <= HIGH
end

local function read_block(pointer, size)
	local ok, raw = pcall(mr.read, pointer, 0, size)
	if ok and type(raw) == 'string' and #raw == size then return raw end
	-- try a smaller block (the object may end before the size)
	ok, raw = pcall(mr.read, pointer, 0, 0x200)
	if ok and type(raw) == 'string' and #raw == 0x200 then return raw end
	return nil
end

local function hex_number(pointer)
	return tonumber(mr.tostring(pointer), 16)
end

local function unit_pointer(unit)
	local pointer = mr.read_pointer(mr.ud_topointer(unit), 8)
	if mr.is_null(pointer) or mr.read_uint32(pointer, state.uid_offset) ~= unit:unique_ui_id() then return nil end
	return pointer
end

-- the blocks of a unit: its object, and the objects its first bytes point to (heap, not the game's image)
local function blocks_of(entry)
	local blocks = { { name = '', pointer = entry.pointer, size = UNIT_BYTES } }
	local raw = read_block(entry.pointer, UNIT_BYTES)
	if not raw then return blocks end
	blocks[1].size = #raw
	local subs = 0
	for offset = 0, math.min(POINTER_BYTES, #raw) - 8, 8 do
		local lo = dword(raw, offset + 1)
		local hi = dword(raw, offset + 5)
		if lo and hi and hi >= 1 and hi < 0x8000 and lo % 8 == 0 then
			local address = hi * 4294967296 + lo
			if not (address >= state.base and address < state.base + IMAGE_SIZE) and subs < MAX_SUBS then
				local ok, pointer = pcall(mr.read_pointer, entry.pointer, offset)
				if ok and not mr.is_null(pointer) and read_block(pointer, SUB_BYTES) then
					subs = subs + 1
					blocks[#blocks + 1] = { name = ('[+0x%x]'):format(offset), pointer = pointer, size = SUB_BYTES }
				end
			end
		end
	end
	return blocks
end

-- the candidates of a unit: every dword of its blocks that could be a leadership value now
local function setup(entry)
	entry.blocks = blocks_of(entry)
	entry.cands = {}
	for b, block in ipairs(entry.blocks) do
		local raw = read_block(block.pointer, block.size)
		if raw then
			block.size = #raw
			for i = 1, #raw - 3, 4 do
				local n, f = dword(raw, i)
				local kind = nil
				if plausible_float(f) then kind = 'f' elseif plausible_int(n) then kind = 'i' end
				if kind then
					local v = value_of(raw, i, kind)
					local path = ('%s+0x%x %s'):format(block.name, i - 1, kind == 'f' and 'float' or 'int')
					entry.cands[#entry.cands + 1] = { b = b, i = i, kind = kind, path = path, first = v, last = v, min = v, max = v, changes = 0,
						n = 0, sx = 0, sxx = 0, sy = 0, syy = 0, sxy = 0, nw = 0, sw = 0, ns = 0, ss = 0 }
				end
			end
		end
	end
end

local function sample(entry)
	local unit = entry.unit
	local men = unit:number_of_men_alive()
	local function yes(method)
		local ok, value = pcall(function() return unit[method](unit) end)
		return ok and value == true
	end
	local shaken = yes('is_wavering') or yes('is_routing')
	-- distance to the army's general (his leadership aura)
	local near, far = false, false
	if entry.general then
		local ok, d = pcall(function()
			local a, b = unit:position(), entry.general:position()
			return math.sqrt((a:get_x() - b:get_x()) ^ 2 + (a:get_z() - b:get_z()) ^ 2)
		end)
		if ok and d then near, far = d < AURA_NEAR, d > AURA_FAR end
		if near then entry.near = (entry.near or 0) + 1 elseif far then entry.far = (entry.far or 0) + 1 end
	end
	entry.min_men = math.min(entry.min_men or men, men)
	if shaken then entry.shaken = (entry.shaken or 0) + 1 end
	local raws = {}
	for b, block in ipairs(entry.blocks) do raws[b] = read_block(block.pointer, block.size) end
	for _, c in ipairs(entry.cands) do
		local raw = raws[c.b]
		local v = raw and value_of(raw, c.i, c.kind)
		-- the number of men itself (and copies of it) is not leadership (run 16: +0xa4, +0x184 ranked first)
		if v == men then c.same_as_men = (c.same_as_men or 0) + 1 end
		if v and v == v and v > -1e6 and v < 1e6 then
			if v ~= c.last then c.changes = c.changes + 1 end
			c.last = v
			if v < c.min then c.min = v end
			if v > c.max then c.max = v end
			c.n, c.sx, c.sxx, c.sy, c.syy, c.sxy = c.n + 1, c.sx + v, c.sxx + v * v, c.sy + men, c.syy + men * men, c.sxy + v * men
			if shaken then c.nw, c.sw = c.nw + 1, c.sw + v else c.ns, c.ss = c.ns + 1, c.ss + v end
			if near then c.nn, c.sn = (c.nn or 0) + 1, (c.sn or 0) + v elseif far then c.nf, c.sf = (c.nf or 0) + 1, (c.sf or 0) + v end
			-- the number the unit panel shows
			for number in pairs(state.ui_numbers) do
				if math.abs(v - number) < 0.5 then
					local hits = state.ui[c.path] or { count = 0, values = {} }
					state.ui[c.path] = hits
					if not hits.values[number] then hits.values[number], hits.count = true, hits.count + 1 end
				end
			end
		end
	end
	entry.samples = (entry.samples or 0) + 1
end

-- the unit panel: components whose name says morale / leadership, and the number in their text
local function look_at_ui()
	local ok, root = pcall(function() return core:get_ui_root() end)
	if not ok or not root then return end
	local numbers, seen, texts = {}, 0, {}
	local function walk(component, depth, path)
		if seen >= UI_MAX_NODES or depth > 30 then return end
		seen = seen + 1
		local id = tostring(component:Id())
		local low = id:lower()
		if (low:find('morale') or low:find('leadership')) and not state.ui_ids[id] then
			state.ui_ids[id] = true
			log(('UI component seen: %s/%s (visible %s)'):format(path, id, tostring(component:Visible())))
		end
		if low:find('morale') or low:find('leadership') then
			local text_ok, text = pcall(function() return component:GetStateText() end)
			if text_ok and type(text) == 'string' and text ~= '' and component:Visible() then
				local number = tonumber(text:match('(-?%d+)'))
				texts[#texts + 1] = path .. '/' .. id .. ' = "' .. text .. '"'
				if number then numbers[number] = true end
			end
		end
		for index = 0, component:ChildCount() - 1 do
			local child = UIComponent(component:Find(index))
			if child then walk(child, depth + 1, depth < 3 and (path .. '/' .. id) or path) end
		end
	end
	pcall(walk, root, 0, '')
	state.ui_numbers = numbers
	local line = table.concat(texts, ' | ')
	if line ~= '' and line ~= state.ui_line then log('UI ' .. line) end
	state.ui_line = line
end

local function corr(c)
	if c.n < 5 then return nil end
	local vx = c.n * c.sxx - c.sx * c.sx
	local vy = c.n * c.syy - c.sy * c.sy
	if vx <= 1e-9 or vy <= 1e-9 then return nil end
	return (c.n * c.sxy - c.sx * c.sy) / math.sqrt(vx * vy)
end

-- how much a candidate looks like leadership in its unit (0 .. about 4)
local function score(entry, c)
	if c.changes == 0 or c.n < 5 then return 0, nil end
	if (c.same_as_men or 0) * 2 > c.n then return 0, nil end -- it is the number of men
	if c.changes > c.n * 0.5 then return 0, nil end -- a timer, a position, an animation value
	local s = 0
	local r = corr(c)
	if r and r > 0 then s = s + r end -- falls with the men
	if c.nw > 0 and c.ns > 0 then
		local shaken, steady = c.sw / c.nw, c.ss / c.ns
		if shaken < steady then s = s + 1 end
	end
	if (c.nn or 0) >= 3 and (c.nf or 0) >= 3 and c.sn / c.nn > c.sf / c.nf + 0.01 then s = s + 1.5 end -- higher near the lord
	return s, r
end

local function summary(final, title)
	local by_path = {}
	local units_used = 0
	for _, entry in ipairs(state.units) do
		if entry.cands and (entry.samples or 0) >= 5 then
			units_used = units_used + 1
			for _, c in ipairs(entry.cands) do
				local s, r = score(entry, c)
				local p = by_path[c.path] or { path = c.path, score = 0, units = 0, examples = {} }
				by_path[c.path] = p
				p.score = p.score + s
				if s > 0 then p.units = p.units + 1 end
				if #p.examples < 3 and c.changes > 0 then
					p.examples[#p.examples + 1] = ('%s: %g -> %g (min %g, max %g, %d changes%s%s)'):format(entry.label, c.first, c.last, c.min, c.max, c.changes,
						r and (', corr. with men %.2f'):format(r) or '',
						((c.nw > 0 and c.ns > 0) and (', shaken %.1f vs steady %.1f'):format(c.sw / c.nw, c.ss / c.ns) or '')
						.. (((c.nn or 0) > 0 and (c.nf or 0) > 0) and (', lord near %.2f vs far %.2f'):format(c.sn / c.nn, c.sf / c.nf) or ''))
				end
			end
		end
	end
	local list = {}
	for path, p in pairs(by_path) do
		local hits = state.ui[path]
		p.ui = hits and hits.count or 0
		p.total = p.score + 2 * p.ui
		if p.total > 0 then list[#list + 1] = p end
	end
	table.sort(list, function(a, b) return a.total > b.total end)
	local shaken = {}
	for _, entry in ipairs(state.units) do
		if (entry.shaken or 0) > 0 or (entry.min_men and entry.start_men and entry.min_men < entry.start_men) or ((entry.near or 0) > 0 and (entry.far or 0) > 0) then
			shaken[#shaken + 1] = ('%s %d -> %d men%s%s'):format(entry.label, entry.start_men or -1, entry.min_men or -1, (entry.shaken or 0) > 0 and (', shaken in %d samples'):format(entry.shaken) or '',
				((entry.near or 0) > 0 and (entry.far or 0) > 0) and (', lord near in %d / far in %d samples'):format(entry.near, entry.far) or '')
		end
	end
	log(('%s: %d units read, %d places that changed like leadership; units that lost men or wavered: %s'):format(title or (final and 'FINAL SUMMARY' or 'summary'), units_used, #list,
		#shaken > 0 and table.concat(shaken, '; ') or 'none yet (fight, take losses, waver)'))
	for rank = 1, math.min(TOP, #list) do
		local p = list[rank]
		log(('  %2d. %-24s total %.2f = score %.2f in %d units + panel numbers matched %d | %s'):format(rank, p.path, p.total, p.score, p.units, p.ui, table.concat(p.examples, '; ')))
	end
	log(('  script cost %.2f ms per tick, longest %.1f ms'):format(state.cost * 1000 / math.max(1, state.cost_ticks or 1), state.longest * 1000))
	state.cost, state.cost_ticks, state.longest = 0, 0, 0
end

local function collect()
	local alliances = bm.battle:alliances()
	for a = 1, math.min(1, alliances:count()) do -- the player's army only (run 16: 22 units cost 1.5 ms per tick)
		local armies = alliances:item(a):armies()
		for b = 1, armies:count() do
			local list = armies:item(b):units()
			local general = nil
			for index = 1, list:count() do
				local ok, is = pcall(function() return list:item(index):is_commanding_unit() end)
				if ok and is == true then general = list:item(index) end
			end
			for index = 1, list:count() do
				local unit = list:item(index)
				local ok, pointer = pcall(unit_pointer, unit)
				if ok and pointer then
					local entry = { unit = unit, pointer = pointer, label = ('A%d.%d.%d %s'):format(a, b, index, tostring(unit:type())), start_men = unit:number_of_men_alive(),
						general = (general ~= unit) and general or nil }
					local set_ok, problem = pcall(setup, entry)
					if set_ok then
						state.units[#state.units + 1] = entry
						log(('%s: %d men, %d blocks read, %d candidate places'):format(entry.label, entry.start_men, #entry.blocks, #entry.cands))
					else
						log(entry.label .. ': not readable: ' .. tostring(problem))
					end
				end
			end
		end
	end
end

local function tick()
	if state.dead then return end
	local started = os.clock()
	state.ticks = state.ticks + 1
	if #state.units > 0 then
		local entry = state.units[state.next]
		state.next = state.next % #state.units + 1
		local ok, problem = pcall(sample, entry)
		if not ok and not entry.failed then
			entry.failed = true
			log(entry.label .. ': sample failed: ' .. tostring(problem))
		end
	end
	if UI_EVERY > 0 and state.ticks % UI_EVERY == 0 then pcall(look_at_ui) end
	-- the scenario pack gave the control back: the ranking so far, at once (the probe goes on watching)
	local finished = tonumber(_G.units_scenario_finished) or 0
	if finished ~= (state.scenario_seen or 0) then
		state.scenario_seen = finished
		summary(false, 'RANKING AT SCENARIO END')
	end
	if state.ticks % SUMMARY_EVERY == 0 then
		summary(false)
		-- places that change in more than half the looks are timers, positions, animation: not looked at any more
		local before, after = 0, 0
		for _, entry in ipairs(state.units) do
			local keep = {}
			for _, c in ipairs(entry.cands or {}) do
				before = before + 1
				if c.n < 5 or c.changes <= c.n * 0.5 then keep[#keep + 1] = c end
			end
			after = after + #keep
			entry.cands = keep
		end
		log(('  dropped %d places that change all the time; %d places still watched'):format(before - after, after))
	end
	local took = os.clock() - started
	state.cost, state.cost_ticks = state.cost + took, (state.cost_ticks or 0) + 1
	if took > state.longest then state.longest = took end
end

local function start()
	log('Units Pass Through LEADERSHIP PROBE --VERSION-- TEST PACK, battle script loaded (only reads memory)')
	mr = _G.memreader_plus
	if not mr then error('memreader Plus is not loaded', 0) end
	for _, name in ipairs({ 'find_pattern', 'read', 'read_pointer', 'read_uint32', 'ud_topointer', 'add', 'is_null', 'tostring' }) do
		if type(mr[name]) ~= 'function' then error('this memreader has no ' .. name, 0) end
	end
	state.base = hex_number(mr.base)
	local address, count = mr.find_pattern(P_UID)
	if not address or count ~= 1 then error('unit id code found ' .. tostring(count) .. ' times', 0) end
	state.uid_offset = mr.read_uint32(mr.add(address, UID_OFF), 0)
	bm:register_phase_change_callback('Deployed', function()
		local ok, problem = pcall(collect)
		if not ok then
			state.dead = true
			log('PROBE OFF: ' .. tostring(problem))
			return
		end
		log(('watching %d units; summary every %d s'):format(#state.units, SUMMARY_EVERY * TICK_MS / 1000))
		bm:repeat_callback(function()
			local ok2, problem2 = pcall(tick)
			if not ok2 then
				state.dead = true
				log('PROBE OFF: ' .. tostring(problem2))
				pcall(function() bm:remove_callback(TIMER) end)
			end
		end, TICK_MS, TIMER)
	end)
	bm:register_phase_change_callback('Complete', function()
		if state.dead then return end
		state.dead = true
		pcall(function() bm:remove_callback(TIMER) end)
		local ok, problem = pcall(summary, true)
		if not ok then log('summary failed: ' .. tostring(problem)) end
	end)
end

local ok, problem = pcall(start)
if not ok then log('PROBE OFF: ' .. tostring(problem)) end
