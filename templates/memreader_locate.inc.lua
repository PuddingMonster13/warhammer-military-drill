-- Finding the game code by pattern (from TW3_Shared/templates/memreader_locate.inc.lua) -----------------------------
-- Put the SAME block into the battle script and the main menu check (frontend_check.lua.tpl), so both judge the game
-- build the same way. The build script fills SITES (value name SITES, from wh3tools.exe.lua_sites) and NEEDED (the
-- memreader functions the mod calls), one SITES entry per place:
--   { name = 'line of fire check', pattern = '8B 55 6F ?? ...', mark = 132, expect = '\072\139\070' },
-- pattern: memreader Plus find_pattern syntax; wildcards ONLY for struct offsets, data addresses and call targets.
--   Opcodes, registers, stack slots and every jump distance fixed, so a match means the control flow is the analysed one.
-- mark: byte offset of the wanted address in the pattern ('|' in wh3tools.exe.parse); expect: the exact original bytes
-- there (read_original, so another mod's patch does not hide them).
-- NEVER use fixed addresses (RVAs): they move with every game build (Units Pass Through 0.6 broke on build 25961564).
local SITES = {
--SITES--
}
local NEEDED = { --NEEDED-- }
local plain_sub = string.sub_lua or string.sub -- CA replaced string.sub with a UTF-8 version; the plain one is kept under this name

-- nil when this memreader can do everything the mod needs, else the reason as text
local function memreader_problem(mr)
	for _, name in ipairs(NEEDED) do
		if type(mr[name]) ~= 'function' then return 'this memreader has no ' .. name .. ' (memreader Plus 0.10.0 or newer is needed)' end
	end
	return nil
end

local function one_match(mr, pattern, name)
	local address, count = mr.find_pattern(pattern)
	if not address or count ~= 1 then error(('the %s code was found %s times, expected 1'):format(name, tostring(count)), 0) end
	return address
end

-- Finds every place the mod works with. Only reads; raises an error (text) when this game build does not fit.
-- Returns { [name] = address }.
local function locate(mr)
	local problem = memreader_problem(mr)
	if problem then error(problem, 0) end
	local found = {}
	for _, site in ipairs(SITES) do
		local at = mr.add(one_match(mr, site.pattern, site.name), site.mark or 0)
		if site.expect and mr.read_original(at, 0, #site.expect) ~= site.expect then
			error('unexpected code at the ' .. site.name, 0)
		end
		found[site.name] = at
	end
	return found
end
-- end of the shared part ----------------------------------------------------------------------------------------------
