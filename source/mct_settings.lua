-- Military Drill: settings for the Mod Configuration Tool (MCT).
-- MCT reads this file (script/mct/settings/military_drill.lua) if MCT is installed. Without MCT the mod uses its
-- defaults; the battle script never requires MCT. The battle script reads the settings when the fighting starts.
local MOD = 'military_drill'

local mct = get_mct()
local mct_mod = mct:register_mod(MOD)
mct_mod:set_title('Military Drill')
mct_mod:set_author('PuddingMonster13')
mct_mod:set_description('A standing friendly unit opens lanes for a unit that walks through it, like in Medieval II; the walking unit forms columns and both reform afterwards.')

mct_mod:add_new_section('main', 'Settings')

local enabled = mct_mod:add_new_option(MOD .. '_enabled', 'checkbox')
enabled:set_default_value(true)
enabled:set_text('Enabled')
enabled:set_tooltip_text('Switches making way on or off for the next battle.')

local lanes = mct_mod:add_new_option(MOD .. '_lanes', 'dropdown')
lanes:set_text('Blocks and columns')
lanes:set_tooltip_text('How a standing unit splits for a unit of many models: into blocks with a lane between every two, and the walking unit forms one column per lane. Automatic: 3 blocks / 2 columns, or 5 / 4 for a front wider than 45 m, or 7 / 6 for one wider than 80 m. A single big model always gets one wide lane; a unit of few bodies (chariots, monsters) keeps its formation and gets one lane per file.')
lanes:add_dropdown_value('auto', 'Automatic', 'By the width of the standing unit.', true)
lanes:add_dropdown_value('2', '3 blocks / 2 columns', 'The standing unit splits into 3 blocks, the walking unit into 2 columns.', false)
lanes:add_dropdown_value('4', '5 blocks / 4 columns', 'The standing unit splits into 5 blocks, the walking unit into 4 columns.', false)
lanes:add_dropdown_value('6', '7 blocks / 6 columns', 'The standing unit splits into 7 blocks, the walking unit into 6 columns.', false)
lanes:set_default_value('auto')

local expendable = mct_mod:add_new_option(MOD .. '_expendable', 'checkbox')
expendable:set_default_value(false)
expendable:set_text('Expendable units do not make way')
expendable:set_tooltip_text('Units with the Expendable attribute (Skavenslaves, Gnoblars, ...) stay where they are; others push through them.')

local wavering = mct_mod:add_new_option(MOD .. '_wavering', 'checkbox')
wavering:set_default_value(true)
wavering:set_text('Wavering units do not make way')
wavering:set_tooltip_text('Units that are wavering (low leadership) stay where they are. Switched off, they make way at half speed. Routing units never make way.')

local fire = mct_mod:add_new_option(MOD .. '_fire_lanes', 'checkbox')
fire:set_default_value(true)
fire:set_text('Clear the line of fire')
fire:set_tooltip_text('A friendly unit that stands in the line of fire of a cannon, bolt thrower or other direct-fire gun opens a narrow lane for the shot, so the gun does not hold fire or shoot into its own men. Units that a walking unit needs always come first.')

local logging = mct_mod:add_new_option(MOD .. '_logging', 'dropdown')
logging:set_text('Logging')
logging:set_tooltip_text('Lines in lua_mod_log.txt in the game folder, for a bug report or for your own changes to the mod.')
logging:add_dropdown_value('off', 'Off', 'The mod writes nothing.', true)
logging:add_dropdown_value('problems', 'Problems', 'The start, switched off and why (for example a game update the mod does not fit), script errors.', false)
logging:add_dropdown_value('basic', 'Basic', 'Problems, the settings, and one short line for every unit that makes way, closes up, cannot make way, or is walked through without making way.', false)
logging:set_default_value('off')
