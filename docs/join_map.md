# Join map: Home Assistant light entities to Crestron analog joins

Source: the Control4 project (Room + Device properties of each Crestron child driver) matched to the 41 names the processor dumps. Join rule: `analog join = 0x100 + (name id + 1) / 2`.

**Verified on the real lights: only the 7 Theater joins (0x124-0x12A).** All other rows follow the verified rule and the Control4 project; spot-check them after the first cutover. The Family Room fan (0x106) has never responded under Control4 either.

The same table lives in `custom_components/crestron_pac2m/const.py` (`LIGHTS`) - change it there if a row is wrong.

| HA entity | Control4 id | Type | Control4 room / device | Crestron label | Join |
|---|---|---|---|---|---|
| light.dimmer_garage_floods | 319 | Dimmer | Exterior / Garage_&_Portico_Flood | Garage & Portico Flood | 0x101 |
| light.dimmer_back_floods | 317 | Dimmer | Exterior / Back_Porch_&_End_House_Flood | Back Porch & End House Flood | 0x102 |
| light.crestron_familyroom_rope | 259 | Dimmer | Family / Rope_Light | Rope Light | 0x103 |
| light.interior_familyroom_art_lights | 257 | Dimmer | Family / Mini_Lights | Mini Lights | 0x104 |
| light.dimmer_downlights | 251 | Dimmer | Family / downlight | downlight | 0x105 |
| light.relay_ceiling_fan_family_room | 325 | Relay | Family / Ceiling_Fan | Fan | 0x106 |
| light.relay_floor_outlet | 255 | Relay | Family / Floor_Recp | Floor Recp | 0x107 |
| light.interior_diningroom_chandelier | 253 | Dimmer | Family / Kitchen_Area | Kitchen Area | 0x108 |
| light.interior_foyer_chandelier | 264 | Dimmer | Foyer / Chandeliers_&_stairs | Chandeliers & stairs | 0x109 |
| light.interior_foyer_staircase_sconces | 266 | Dimmer | Foyer / Foyer_Sconce | Foyer Sconce | 0x10A |
| light.interior_foyer_recessed | 268 | Dimmer | Foyer / Hall_Can | Hall Can | 0x10B |
| light.dimmer_pendant | 270 | Dimmer | Front_Porch / Front_Porch_Can | Front Porch Can | 0x10C |
| light.dimmer_sconces_3 | 273 | Dimmer | Front_Porch / Front_Porch_Wall | Front Porch Wall | 0x10D |
| light.interior_gallery_chandeliers | 276 | Dimmer | Gallery / Chandeliers_(3) | Chandeliers (3) | 0x10E |
| light.interior_kitchen_counter_recessed | 282 | Dimmer | Kitchen / Downlights | Downlights | 0x112 |
| light.interior_kitchen_chandelier | 280 | Dimmer | Kitchen / Chandeliers | Chandeliers | 0x113 |
| light.dimmer_breakfast_chandelier | 288 | Dimmer | Kitchen / Nook_Chandelier | Nook Chandelier | 0x114 |
| light.interior_kitchen_dining_recessed | 290 | Dimmer | Kitchen / Nook_Mini | Nook Mini | 0x115 |
| light.interior_kitchen_top_recessed | 284 | Dimmer | Kitchen / Downlights_2 | Downlights 2 | 0x116 |
| light.dimmer_stove | 286 | Dimmer | Kitchen / Kitchen_stove | Kitchen stove | 0x117 |
| light.interior_diningroom_wallsconces | 292 | Dimmer | Living_&_Dining / Living_Wall_Sconce | Living Wall Sconce | 0x118 |
| light.interior_livingroom_art_lights | 294 | Dimmer | Living_&_Dining / Living_Fireplace | Living Fireplace | 0x119 |
| light.interior_livingroom_chandelier | 296 | Dimmer | Living_&_Dining / Living_Chandelier | Living Chandelier | 0x11A |
| light.interior_kitchen_entry_recessed | 300 | Dimmer | Living_&_Dining / Bnook | Bnook | 0x11B |
| light.crestron_livingroom_rope | 302 | Relay | Living_&_Dining / Living_Rope_Lights | Living Rope Lights | 0x11C |
| light.interior_diningroom_downlights | 298 | Dimmer | Living_&_Dining / Dining_Mini | Dining Mini | 0x11D |
| light.dimmer_sconce | 314 | Dimmer | Master_Bath / Vanity_Sconce | Vanity Sconce | 0x11E |
| light.relay_sink | 304 | Relay | Master_Bath / Bath_Vanity | Bath Vanity | 0x11F |
| light.dimmer_vanity | 306 | Dimmer | Master_Bath / Dressing_Lights | Dressing Lights | 0x120 |
| light.dimmer_tub | 312 | Dimmer | Master_Bath / Tub_Can | Tub Can | 0x121 |
| light.dimmer_chandelier_5 | 310 | Dimmer | Master_Bath / Chandeliers | Chandeliers | 0x122 |
| light.dimmer_shower | 308 | Dimmer | Master_Bath / Shower | Shower | 0x123 |
| light.dimmer_theater_front_recessed | 237 | Dimmer | Theater / Downlights | Downlights | 0x124 |
| light.dimmer_rear_downlights | 239 | Dimmer | Theater / Downlights_2 | Downlights 2 | 0x125 |
| light.dimmer_bar_pendants | 245 | Dimmer | Theater / Sconces | Sconces | 0x126 |
| light.dimmer_sconces | 247 | Dimmer | Theater / Sconces_2 | Sconces 2 | 0x127 |
| light.dimmer_bar_lights | 241 | Dimmer | Theater / Mini | Mini | 0x128 |
| light.dimmer_step_lights | 243 | Dimmer | Theater / Rope_Lights | Rope Lights | 0x129 |
| light.interior_foyer_staircase_basement_recessed | 249 | Dimmer | Theater / Stair_Lights | Stair Lights | 0x12A |
| light.dimmer_art_lights_dining_room | 402 | Dimmer | Living_&_Dining / Dining_Art_Lights | Dining Art Lights | 0x12C |

Unmapped Crestron entries: 'All Room Lights' (name id 0x1f, join 0x110) and analog join 0x12d (level 0x8000) have no Control4 child and are ignored.
