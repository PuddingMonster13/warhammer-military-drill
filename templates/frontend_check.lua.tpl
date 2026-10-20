-- --TITLE-- --VERSION--: main menu check (script/frontend/mod/--MODKEY--_check.lua).
-- Looks (read-only) whether this game build has the code the mod works with. If not, the mod stays off in battle anyway;
-- this script only tells the player so, with one message box per game start. Nothing in the game is changed here.
-- Taken from the Skaven mod (ran in game 2026-10-12). Needs memreader_locate.inc.lua and values TITLE, VERSION, MODKEY.

local MOD = '--MODKEY--'
local VERSION = '--VERSION--'
local KEY = MOD .. '_alert'
local SHOWN = KEY .. '_shown'

--INCLUDE:memreader_locate.inc.lua--

local function log(message)
	if type(ModLog) == 'function' then pcall(ModLog, '[' .. MOD .. '] ' .. tostring(message)) end
end

local function guarded(callback)
	return function(...)
		local ok, result = pcall(callback, ...)
		if ok then return result end
		log('main menu check: ' .. tostring(result))
		return false
	end
end

-- nil when the mod can work, else the text for the message box
local function problem_text()
	local mr = _G.memreader_plus
	local head = '--TITLE-- ' .. VERSION
	local tail = '\n\nThe mod is switched off and changes nothing in the game. Everything else works as normal.'
	if not mr then return head .. ' needs the mod memreader Plus, which is not loaded.' .. tail .. '\n\nEnable memreader Plus in the launcher.' end
	local old = memreader_problem(mr)
	if old then return head .. ' needs a newer memreader Plus.' .. tail .. '\n\nReason: ' .. old end
	local ok, reason = pcall(locate, mr)
	if ok then return nil end
	return head .. ' does not match this version of the game.' .. tail .. '\n\nIt needs an update for the new game version.\nReason: ' .. tostring(reason)
end

local function find_box()
	return find_uicomponent(core:get_ui_root(), KEY)
end

local function close()
	core:remove_listener(KEY .. '_click')
	local box = find_box()
	if not box then return end
	box:UnLockPriority()
	box:Destroy()
end

local function owns(context)
	return find_box() ~= nil and uicomponent_descended_from(UIComponent(context.component), KEY)
end

local function show(text)
	local box = UIComponent(core:get_ui_root():CreateComponent(KEY, 'UI/Common UI/dialogue_box'))
	find_uicomponent(box, 'DY_text'):SetStateText(text, KEY)
	find_uicomponent(box, 'both_group'):SetVisible(false)
	find_uicomponent(box, 'ok_group'):SetVisible(true)
	box:PropagatePriority(99)
	box:LockPriority()
	core:add_listener(KEY .. '_click', 'ComponentLClickUp', guarded(owns), guarded(close), true)
end

local function check()
	local text = problem_text()
	if not text then return end
	log(string.gsub(text, '\n+', ' | '))
	if core:svr_load_bool(SHOWN) then return end -- once per game start, not on every return to the main menu
	core:svr_save_bool(SHOWN, true)
	show(text)
end

local function later()
	core:get_tm():real_callback(guarded(check), 500)
end

if core:is_frontend() then core:add_ui_created_callback(guarded(later)) end
