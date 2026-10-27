-- Units Pass Through - LEADERSHIP PROBE --VERSION-- (test pack). Only reads, changes nothing.
-- Looks for the place of a unit's current leadership in memory: the script interface has only is_wavering /
-- is_routing. Versions 2 and 3 are for a NORMAL battle (no scenario script): fight, let units take losses, waver and
-- rout. Version 3 watches both alliances (the enemy routs more often), reads far less per sample (run 46: 69-114 KB,
-- a 67 ms hitch), keeps the log of all battles (appended, a header line per battle) and prints less.
--   * MORALE anchor: the base leadership of each unit comes from the game's tables (land_units.morale). At deployment
--     the current leadership is the base plus lord / skill bonuses, so it lies within [base - 1, base + 40].
--   * Deep scan, once per unit at setup: the unit's object (all plausible numbers), the objects its first bytes point
--     to and the objects those point to (from there on only numbers within the anchor range of the base).
--   * A candidate scores when it
--       - falls at the moments the unit loses men or hit points (and is penalised when it rises then),
--       - is lower when the unit starts to waver and higher again after it recovers (+2),
--       - is lower at the first rout than at the start (+2),
--       - starts within the anchor range (+1.5),
--       - falls with the men alive (correlation), is lower while the unit wavers or routs than while it is steady,
--         is higher near the unit's own general than far from him,
--       - and does not jump about like a timer or a position, nor equals the number of men.
-- The same place in different units counts together. Log: units_leader_probe_log_1.txt = the newest fought battle ...
-- _5 = the oldest (rotated when a battle really starts, see open_battle; a short summary every 60 s and a longer one at
-- the end of the battle, with a trace per unit for the top places).

local LOG_NAME = 'units_leader_probe_log' -- files LOG_NAME_1.txt (newest battle) ... LOG_NAME_5.txt (oldest)
local LOG_FILES = 5
local TIMER = 'units_leader_probe_tick'
local TICK_MS = 100 -- one unit is read per tick
local ALLIANCES = 2 -- how many alliances are watched, the player's first (run 16: 22 units cost 1.5 ms per tick)
local UNIT_BYTES = 0x1800 -- read of the unit object
local POINTER_BYTES = 0x800 -- of the unit object, these first bytes are looked at for pointers to further objects
local SUB_POINTER_BYTES = 0x200 -- the same for those objects
local SUB_BYTES = 0x400 -- read of each such object
local MAX_SUBS = 48 -- objects pointed to by the unit object (run 15: 48 subs with all numbers gave 52000 places; now only anchored ones are kept)
local MAX_SUBSUBS = 192 -- objects pointed to by those
local MAX_CANDS = 4000 -- candidates per unit (run 15: 52000 places gave hitches); anchored ones first, then level 1 by offset
local MAX_SAMPLE_BYTES = 16384 -- bytes read per sample of a unit (run 46: 69-114 KB gave 2 ms per tick and a 67 ms hitch)
local LOW, HIGH = -150, 300 -- a leadership value lies in here
local ANCHOR_BELOW, ANCHOR_ABOVE = 1, 40 -- at deployment leadership is within [base - 1, base + 40]
local SUMMARY_EVERY = 600 -- ticks (60 s)
local TOP, FINAL_TOP = 5, 15 -- places in the 60 s summary / the final summary
local TRACE_TOP = 5 -- the places that get a trace per unit (final summary only)
local TRACE_UNITS = 3
local AURA_NEAR, AURA_FAR = 20, 60 -- metres from the unit's own general: in / out of his leadership aura
local P_UID = '--P_UID--'
local UID_OFF = --UID_OFF--
local IMAGE_SIZE = --IMAGE_SIZE--
local MORALE = --MORALE-- -- main unit key (unit:type()) -> base morale, from the game's tables

local mr = nil
local state = { battle_open = false, dead = false, units = {}, ticks = 0, next = 1, paths = {}, base = nil, cost = 0, longest = 0 }
local pending = {} -- lines logged before the battle has started (script loaded, start problems); small

local function log_name(i)
	return LOG_NAME .. '_' .. i .. '.txt'
end

local function write_file(i, mode, text)
	local file = io.open(log_name(i), mode)
	if file then
		file:write(text)
		file:close()
	end
end

local function log(message)
	local line = os.date('%H:%M:%S') .. ' ' .. tostring(message) .. '\n'
	if state.battle_open then
		write_file(1, 'a', line)
	elseif #pending < 50 then
		pending[#pending + 1] = line
	end
end

-- a battle really starts (phase Deployed): the older logs move up one place (_5 is dropped), the new log is _1
local function open_battle(header)
	for i = LOG_FILES - 1, 1, -1 do
		local src = io.open(log_name(i), 'r')
		if src then
			local content = src:read('*a')
			src:close()
			write_file(i + 1, 'w', content)
		end
	end
	write_file(1, 'w', header .. '\n' .. table.concat(pending))
	pending = {}
	state.battle_open = true
end

-- the script failed before the battle started: the lines so far go to the end of _1 without a rotation
local function dump_unstarted()
	if state.battle_open or #pending == 0 then return end
	write_file(1, 'a', '==== not started ' .. os.date('%Y-%m-%d %H:%M:%S') .. '\n' .. table.concat(pending))
	pending = {}
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

-- the heap objects the first bytes of raw (the memory at owner) point to: { offset, pointer }
local function pointers_in(raw, limit, owner)
	local found = {}
	for offset = 0, math.min(limit, #raw) - 8, 8 do
		local lo = dword(raw, offset + 1)
		local hi = dword(raw, offset + 5)
		if lo and hi and hi >= 1 and hi < 0x8000 and lo % 8 == 0 then
			local address = hi * 4294967296 + lo
			if not (address >= state.base and address < state.base + IMAGE_SIZE) then
				local ok, pointer = pcall(mr.read_pointer, owner, offset)
				if ok and not mr.is_null(pointer) then found[#found + 1] = { offset = offset, pointer = pointer } end
			end
		end
	end
	return found
end

-- the blocks of a unit: its object (level 1), the objects its first bytes point to (level 2) and those objects'
-- pointers (level 3); heap only, not the game's image; each address once. A block keeps its bytes until setup() used them.
local function blocks_of(entry)
	local raw = read_block(entry.pointer, UNIT_BYTES)
	local blocks = { { name = '', pointer = entry.pointer, size = UNIT_BYTES, level = 1, raw = raw } }
	if not raw then return blocks end
	blocks[1].size = #raw
	local seen = { [mr.tostring(entry.pointer)] = true }
	local function add(name, pointer, level)
		local key = mr.tostring(pointer)
		if seen[key] then return nil end
		local sub_raw = read_block(pointer, SUB_BYTES)
		if not sub_raw then return nil end
		seen[key] = true
		local block = { name = name, pointer = pointer, size = #sub_raw, level = level, raw = sub_raw }
		blocks[#blocks + 1] = block
		return block
	end
	local subs, subsubs = {}, 0
	for _, p in ipairs(pointers_in(raw, POINTER_BYTES, entry.pointer)) do
		if #subs >= MAX_SUBS then break end
		local block = add(('[+0x%x]'):format(p.offset), p.pointer, 2)
		if block then subs[#subs + 1] = block end
	end
	for _, parent in ipairs(subs) do
		for _, p in ipairs(pointers_in(parent.raw, SUB_POINTER_BYTES, parent.pointer)) do
			if subsubs >= MAX_SUBSUBS then break end
			if add(('%s[+0x%x]'):format(parent.name, p.offset), p.pointer, 3) then subsubs = subsubs + 1 end
		end
	end
	entry.subs, entry.subsubs = #subs, subsubs
	return blocks
end

-- a number near the unit's base leadership
local function anchored_float(entry, f)
	return f ~= nil and entry.base ~= nil and f >= entry.base - ANCHOR_BELOW and f <= entry.base + ANCHOR_ABOVE
end

local function anchored_int(entry, n)
	return entry.base ~= nil and n >= 1 and n >= entry.base - ANCHOR_BELOW and n <= entry.base + ANCHOR_ABOVE
end

-- keeps only the blocks that still have candidates, and of each only the span from its lowest to its highest
-- candidate (+4): that is all sample() reads. The index c.i is relative to the span.
local function compact(entry)
	local lo, hi = {}, {}
	for _, c in ipairs(entry.cands) do
		if not lo[c.b] or c.i < lo[c.b] then lo[c.b] = c.i end
		if not hi[c.b] or c.i > hi[c.b] then hi[c.b] = c.i end
	end
	local blocks, map = {}, {}
	entry.bytes = 0
	for b, block in ipairs(entry.blocks) do
		if lo[b] then
			block.start, block.len = block.start + lo[b] - 1, hi[b] - lo[b] + 4
			entry.bytes = entry.bytes + block.len
			blocks[#blocks + 1] = block
			map[b] = #blocks
		end
	end
	for _, c in ipairs(entry.cands) do
		c.i = c.i - lo[c.b] + 1
		c.b = map[c.b]
	end
	entry.blocks = blocks
end

-- the bytes sample() would read for these candidates
local function span_bytes(list, count)
	local lo, hi = {}, {}
	for k = 1, count do
		local c = list[k]
		if not lo[c.b] or c.i < lo[c.b] then lo[c.b] = c.i end
		if not hi[c.b] or c.i > hi[c.b] then hi[c.b] = c.i end
	end
	local total = 0
	for b, first in pairs(lo) do total = total + hi[b] - first + 4 end
	return total
end

-- the bytes of a kept block (its span)
local function read_span(block)
	local ok, raw = pcall(mr.read, block.pointer, block.start, block.len)
	if ok and type(raw) == 'string' and #raw == block.len then return raw end
	return nil
end

-- the candidates of a unit: level 1 every dword that could be a leadership value now, levels 2 and 3 only anchored ones
local function setup(entry)
	local started = os.clock()
	entry.blocks = blocks_of(entry)
	entry.cands = {}
	for b, block in ipairs(entry.blocks) do
		local raw = block.raw
		block.raw = nil
		if raw then
			block.start, block.len = 0, #raw
			for i = 1, #raw - 3, 4 do
				local n, f = dword(raw, i)
				local kind, anchored = nil, false
				if anchored_float(entry, f) then
					kind, anchored = 'f', true
				elseif n and anchored_int(entry, n) then
					kind, anchored = 'i', true
				elseif block.level == 1 then
					if plausible_float(f) then kind = 'f' elseif plausible_int(n) then kind = 'i' end
				end
				if kind then
					local v = value_of(raw, i, kind)
					local path = ('%s+0x%x %s'):format(block.name, i - 1, kind == 'f' and 'float' or 'int')
					entry.cands[#entry.cands + 1] = { b = b, i = i, kind = kind, path = path, level = block.level, anchored = anchored, first = v, last = v, min = v, max = v, changes = 0,
						n = 0, sx = 0, sxx = 0, sy = 0, syy = 0, sxy = 0, nw = 0, sw = 0, ns = 0, ss = 0, ev = 0, drops = 0, wav = 0 }
				end
			end
		end
	end
	if #entry.cands > MAX_CANDS then
		local keep = {}
		for _, c in ipairs(entry.cands) do
			if c.anchored and #keep < MAX_CANDS then keep[#keep + 1] = c end
		end
		for _, c in ipairs(entry.cands) do
			if not c.anchored and #keep < MAX_CANDS then keep[#keep + 1] = c end
		end
		entry.cut = #entry.cands - #keep
		entry.cands = keep
	end
	-- the read budget: anchored candidates first (by block, offset), then the level-1 ones by offset; the largest
	-- prefix that fits is kept (the spans only grow with more candidates, so a binary search finds it)
	if span_bytes(entry.cands, #entry.cands) > MAX_SAMPLE_BYTES then
		local order = {}
		for _, c in ipairs(entry.cands) do
			if c.anchored then order[#order + 1] = c end
		end
		for _, c in ipairs(entry.cands) do
			if not c.anchored then order[#order + 1] = c end
		end
		local low, high = 0, #order
		while low < high do
			local mid = math.floor((low + high + 1) / 2)
			if span_bytes(order, mid) <= MAX_SAMPLE_BYTES then low = mid else high = mid - 1 end
		end
		entry.budget_cut = #order - low
		local keep = {}
		for k = 1, low do keep[k] = order[k] end
		entry.cands = keep
	end
	entry.blocks_read = #entry.blocks
	compact(entry)
	entry.scan_ms = (os.clock() - started) * 1000
end

local function sample(entry)
	local unit = entry.unit
	local men = unit:number_of_men_alive()
	local function yes(method)
		local ok, value = pcall(function() return unit[method](unit) end)
		return ok and value == true
	end
	local wavering, routing = yes('is_wavering'), yes('is_routing')
	local shaken = wavering or routing
	-- hit points (the method may not exist: then the men alone tell a loss)
	local hp = nil
	if not entry.no_hp then
		local hp_ok, value = pcall(function() return unit:unary_hitpoints() end)
		if hp_ok and type(value) == 'number' then hp = value else entry.no_hp = true end
	end
	local lost = men < (entry.prev_men or men) or (hp ~= nil and entry.prev_hp ~= nil and hp < entry.prev_hp)
	if lost then entry.loss_events = (entry.loss_events or 0) + 1 end
	entry.prev_men, entry.prev_hp = men, hp
	local onset = shaken and not entry.was_shaken
	local recovery = entry.was_shaken and not shaken
	local rout_onset = routing and not entry.was_routing
	entry.was_shaken, entry.was_routing = shaken, routing
	if wavering and not entry.waver_logged then
		entry.waver_logged = true
		log(('%s starts to waver (%d of %d men left)'):format(entry.label, men, entry.start_men))
	end
	if routing and not entry.rout_logged then
		entry.rout_logged = true
		log(('%s routs (%d of %d men left)'):format(entry.label, men, entry.start_men))
	end
	if recovery and not entry.rally_logged then
		entry.rally_logged = true
		log(('%s rallies (%d of %d men left)'):format(entry.label, men, entry.start_men))
	end
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
	for b, block in ipairs(entry.blocks) do raws[b] = read_span(block) end
	for _, c in ipairs(entry.cands) do
		local raw = raws[c.b]
		local v = raw and value_of(raw, c.i, c.kind)
		-- the number of men itself (and copies of it) is not leadership (run 16: +0xa4, +0x184 ranked first)
		if v == men then c.same_as_men = (c.same_as_men or 0) + 1 end
		if v and v == v and v > -1e6 and v < 1e6 then
			-- losses: leadership falls with casualties
			if lost then
				if v < c.last then c.ev, c.drops = c.ev + 1, c.drops + 1 elseif v > c.last then c.ev = c.ev - 0.5 end
			end
			-- wavering: lower at the onset than at the start, higher again after the recovery
			if onset then
				c.at_onset = v
				if not c.onset then c.onset = v end
			elseif recovery and c.at_onset then
				if c.at_onset < c.first and v > c.at_onset then c.wav = c.wav + 2 end
				c.at_onset = nil
			end
			if rout_onset and not c.rout then c.rout = v end
			if v ~= c.last then c.changes = c.changes + 1 end
			c.last = v
			if v < c.min then c.min = v end
			if v > c.max then c.max = v end
			c.n, c.sx, c.sxx, c.sy, c.syy, c.sxy = c.n + 1, c.sx + v, c.sxx + v * v, c.sy + men, c.syy + men * men, c.sxy + v * men
			if shaken then c.nw, c.sw = c.nw + 1, c.sw + v else c.ns, c.ss = c.ns + 1, c.ss + v end
			if near then c.nn, c.sn = (c.nn or 0) + 1, (c.sn or 0) + v elseif far then c.nf, c.sf = (c.nf or 0) + 1, (c.sf or 0) + v end
		end
	end
	entry.samples = (entry.samples or 0) + 1
end

local function corr(c)
	if c.n < 5 then return nil end
	local vx = c.n * c.sxx - c.sx * c.sx
	local vy = c.n * c.syy - c.sy * c.sy
	if vx <= 1e-9 or vy <= 1e-9 then return nil end
	return (c.n * c.sxy - c.sx * c.sy) / math.sqrt(vx * vy)
end

-- how much a candidate looks like leadership in its unit
local function score(entry, c)
	if c.changes == 0 or c.n < 5 then return 0, nil end
	if (c.same_as_men or 0) * 2 > c.n then return 0, nil end -- it is the number of men
	if c.changes > c.n * 0.5 then return 0, nil end -- a timer, a position, an animation value
	local s = c.ev + c.wav -- falls at losses (+1 / -0.5 each), lower at the wavering onset and higher after the recovery (+2)
	if c.anchored then s = s + 1.5 end
	if c.rout and c.rout < c.first then s = s + 2 end -- lower at the first rout than at the start
	local r = corr(c)
	if r and r > 0 then s = s + r end -- falls with the men
	if c.nw > 0 and c.ns > 0 then
		local shaken, steady = c.sw / c.nw, c.ss / c.ns
		if shaken < steady then s = s + 1 end
	end
	if (c.nn or 0) >= 3 and (c.nf or 0) >= 3 and c.sn / c.nn > c.sf / c.nf + 0.01 then s = s + 1.5 end -- higher near the lord
	return s, r
end

-- one unit's course of a place: start, lowest, at the first wavering, at the first rout, last, loss events followed / seen
local function trace(entry, c)
	return ('%s: %g min %g wav %s rout %s last %g ev %d/%d'):format(entry.label, c.first, c.min,
		c.onset and ('%g'):format(c.onset) or '-', c.rout and ('%g'):format(c.rout) or '-', c.last, c.drops, entry.loss_events or 0)
end

local function summary(final, title)
	local by_path = {}
	local units_used = 0
	local examples_per_place = final and 3 or 2
	for _, entry in ipairs(state.units) do
		if entry.cands and (entry.samples or 0) >= 5 then
			units_used = units_used + 1
			for _, c in ipairs(entry.cands) do
				local s, r = score(entry, c)
				local p = by_path[c.path] or { path = c.path, score = 0, units = 0, examples = {}, seen = {} }
				by_path[c.path] = p
				p.score = p.score + s
				if s > 0 then
					p.units = p.units + 1
					if final then p.seen[#p.seen + 1] = { s = s, entry = entry, c = c, shaken = (entry.shaken or 0) > 0 } end
				end
				if #p.examples < examples_per_place and c.changes > 0 then
					p.examples[#p.examples + 1] = ('%s %g -> %g (%g..%g, %d ch%s%s%s)'):format(entry.label, c.first, c.last, c.min, c.max, c.changes,
						r and (', r %.2f'):format(r) or '',
						(c.nw > 0 and c.ns > 0) and (', w %.1f/%.1f'):format(c.sw / c.nw, c.ss / c.ns) or '',
						((c.nn or 0) > 0 and (c.nf or 0) > 0) and (', l %.1f/%.1f'):format(c.sn / c.nn, c.sf / c.nf) or '')
				end
			end
		end
	end
	local list = {}
	for path, p in pairs(by_path) do
		if p.score > 0 then list[#list + 1] = p end
	end
	table.sort(list, function(a, b) return a.score > b.score end)
	local shaken, lost, wavered, routed = {}, 0, 0, 0
	for _, entry in ipairs(state.units) do
		local lost_men = entry.min_men and entry.start_men and entry.min_men < entry.start_men
		if lost_men then lost = lost + 1 end
		if entry.waver_logged then wavered = wavered + 1 end
		if entry.rout_logged then routed = routed + 1 end
		if (entry.shaken or 0) > 0 or lost_men or ((entry.near or 0) > 0 and (entry.far or 0) > 0) then
			shaken[#shaken + 1] = ('%s %d -> %d men%s%s'):format(entry.label, entry.start_men or -1, entry.min_men or -1, (entry.shaken or 0) > 0 and (', shaken in %d samples'):format(entry.shaken) or '',
				((entry.near or 0) > 0 and (entry.far or 0) > 0) and (', lord near in %d / far in %d samples'):format(entry.near, entry.far) or '')
		end
	end
	local name = title or (final and 'FINAL SUMMARY' or 'summary')
	if final then
		log(('%s: %d units read, %d places that changed like leadership; units that lost men or wavered: %s'):format(name, units_used, #list,
			#shaken > 0 and table.concat(shaken, '; ') or 'none (fight, take losses, waver)'))
	else
		log(('%s: %d units read, %d places; %d units lost men, %d wavered, %d routed'):format(name, units_used, #list, lost, wavered, routed))
	end
	for rank = 1, math.min(final and FINAL_TOP or TOP, #list) do
		local p = list[rank]
		log(('%d. %s %.1f x%d | %s'):format(rank, p.path, p.score, p.units, table.concat(p.examples, '; ')))
		if final and rank <= TRACE_TOP then
			-- units that wavered or routed first, then by score
			table.sort(p.seen, function(a, b)
				if a.shaken ~= b.shaken then return a.shaken end
				return a.s > b.s
			end)
			for k = 1, math.min(TRACE_UNITS, #p.seen) do
				log('  tr ' .. trace(p.seen[k].entry, p.seen[k].c))
			end
		end
	end
	log(('  cost %.2f ms per tick, longest %.1f ms'):format(state.cost * 1000 / math.max(1, state.cost_ticks or 1), state.longest * 1000))
	state.cost, state.cost_ticks, state.longest = 0, 0, 0
end

local function collect()
	local alliances = bm.battle:alliances()
	for a = 1, math.min(ALLIANCES, alliances:count()) do
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
						general = (general ~= unit) and general or nil, base = MORALE[tostring(unit:type())] }
					entry.prev_men = entry.start_men
					local set_ok, problem = pcall(setup, entry)
					if set_ok then
						state.units[#state.units + 1] = entry
						log(('%s: %d men, base leadership %s, %d blocks kept of %d read (%d sub, %d sub-sub objects), %d candidate places%s, %d bytes read per sample, scan took %.0f ms'):format(entry.label, entry.start_men,
							entry.base and tostring(entry.base) or 'unknown', #entry.blocks, entry.blocks_read or 0, entry.subs or 0, entry.subsubs or 0, #entry.cands,
							(entry.cut and (' (cap %d cut %d)'):format(MAX_CANDS, entry.cut) or '') .. (entry.budget_cut and (' (budget cut %d)'):format(entry.budget_cut) or ''), entry.bytes or 0, entry.scan_ms or 0))
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
			compact(entry)
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
		local counts = {}
		pcall(function()
			local alliances = bm.battle:alliances()
			for a = 1, alliances:count() do
				local armies = alliances:item(a):armies()
				local n = 0
				for b = 1, armies:count() do n = n + armies:item(b):units():count() end
				counts[#counts + 1] = ('A%d %d'):format(a, n)
			end
		end)
		open_battle(('==== BATTLE %s, probe --VERSION--, units %s'):format(os.date('%Y-%m-%d %H:%M:%S'), table.concat(counts, ', ')))
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
if not ok then
	log('PROBE OFF: ' .. tostring(problem))
	dump_unstarted()
end
