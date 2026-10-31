"""Constants and settings for the Crestron PAC2M lighting integration.

ALL tunable values live in this file (project rule: variables at the top).
"""
from __future__ import annotations

DOMAIN = "crestron_pac2m"

# ---- network settings ----
CONF_HOST = "host"
DEFAULT_HOST = "192.168.104.111"        # Crestron PAC2M
CRESTRON_PORT = 41794                   # the processor listens here (commands and acks)
LISTEN_PORT = 41794                     # we listen here for the processor's hello/feedback (IP table port)
SOURCE_PORT = 49638                     # source port Control4 used for commands; falls back to a random port if busy

# ---- session timing (seconds) ----
DUMP_SETTLE_SECONDS = 8                 # after a hello, joins with no reported level are taken as off (the dump leaves out lights at 0)
KEEPALIVE_SECONDS = 30                  # Control4 sends ping + join 4000 high this often; the processor needs it
RETRY_SECONDS = 15                      # while the link is down, retry the keep-alive this often
SESSION_TIMEOUT_SECONDS = 90            # no frame (data or ack) for this long = unavailable
COMMAND_SPACING_SECONDS = 0.05          # pause between commands so a scene does not flood the 2-series processor
COMMAND_ACK_TIMEOUT_SECONDS = 1.0       # a light command the processor has not acked after this long is sent once more
RX_DUPLICATE_SECONDS = 5.0              # same sequence number and payload again within this window = a retransmit (acked, not handled twice)
KEEPALIVE_DIGITAL_JOIN = 4000           # digital join the keep-alive sets high
LOOP_TICK_SECONDS = 1.0                 # how often the session loop wakes up (keep-alive, timeout and resync checks)

# ---- operating mode ----
CONF_MODE = "mode"
CONF_CONTROL4_HOST = "control4_host"    # optional manual override when the Control4 integration cannot be found
CONF_HA_HOST = "ha_host"                # optional manual override for Home Assistant's own IP
MODE_CONTROL4 = "control4"              # processor points to Control4; this integration stays passive (sends nothing)
MODE_STANDALONE = "standalone"          # processor points to Home Assistant; this integration controls the lights
DEFAULT_MODE = MODE_CONTROL4            # adding the integration must never change the processor
CONTROL4_DOMAIN = "control4"            # domain of the Control4 integration whose host we reuse

# ---- processor console (IP table edits) ----
CONSOLE_PORT = 23                       # Crestron telnet console (no password)
CIP_ID = "6"                            # IP table entry that Control4 uses (hex id as shown by ipt)
CONSOLE_CONNECT_TIMEOUT = 5             # seconds to open the console
CONSOLE_COMMAND_WAIT = 3.0              # seconds to wait for a command to finish
CONSOLE_PROMPT_REGEX = r"[A-Za-z0-9_-]+>\s*$"   # e.g. "PAC2M>"
REBOOT_SETTLE_SECONDS = 20              # pause after sending reboot before reconnecting
CONSOLE_BACK_SECONDS = 240              # how long to wait for the console after the reboot
WAIT_READY_SECONDS = 180                # how long to wait for the new peer to show ONLINE after the reboot
POLL_SECONDS = 10                       # how often to re-check while waiting
AUTO_FAILBACK = True                    # standalone switch that never comes up is undone (back to the previous IP)
DISABLE_DELAY_SECONDS = 5               # after "return to Control4", disable the integration this many seconds later

# ---- label verification ----
LABEL_CHECK_DELAY_SECONDS = 8           # after the link comes up, wait for the name dump, then compare the labels
LABEL_STATUS_PENDING = "pending"        # Link sensor attribute label_check: not run yet
LABEL_STATUS_NO_DUMP = "no_dump"        # no name dump since the processor's last reboot (normal after a Home Assistant restart)
LABEL_STATUS_OK = "ok"
LABEL_STATUS_MISMATCH = "mismatch"

# ---- address check (Home Assistant's IP changed) ----
ADDRESS_CHECK_DELAY_SECONDS = 60        # stand-alone and the link down this long: read the IP table and compare it with HA's address
ADDRESS_NOTIFICATION_ID = "crestron_pac2m_address"
LABEL_NOTIFICATION_ID = "crestron_pac2m_labels"
SIGNAL_LABELS_UPDATED = "crestron_pac2m_labels_updated"   # dispatcher signal: refresh the Link sensor attributes

# ---- refresh from the processor (fresh state dump after a Home Assistant restart) ----
REFRESH_PAUSE_SECONDS = 180             # go silent this long so the processor drops the session (UNVERIFIED how long it needs)
REFRESH_PAUSE_MAX_SECONDS = 900
REFRESH_WAIT_SECONDS = 60               # after the pause, wait this long for the hello + state dump
REFRESH_NOTIFICATION_ID = "crestron_pac2m_refresh"
REFRESH_IDLE = "idle"                   # Link sensor attribute refresh
REFRESH_PAUSED = "paused"               # silent: no keep-alive, no acks, light commands refused
REFRESH_WAITING = "waiting"             # keep-alive resumed, waiting for a hello
REFRESH_DUMP_RECEIVED = "dump_received"
REFRESH_NO_DUMP = "no_dump"

# ---- diagnostics ----
FRAME_HISTORY = 100                     # recent frames kept for Download diagnostics

# ---- wall keypads ----
# digital join -> button name. Each entry becomes an event entity (press / release). Fill in after capturing a press under
# Developer Tools > Events > crestron_pac2m_digital; every other join still shows on the "Digital join" event entity.
KEYPAD_BUTTONS: dict[int, str] = {}

# ---- notifications ----
CONF_NOTIFY_SERVICE = "notify_service"   # option: notify.<this> gets a short message too (empty = persistent notification only)
NOTIFY_SERVICE = "mobile_app_iphone_rg"  # default for CONF_NOTIFY_SERVICE
NOTIFICATION_ID = "crestron_pac2m_switch"

# ---- light behaviour ----
TURN_ON_RESTORES_LAST_LEVEL = True      # "turn on" without brightness returns to the last non-zero level (else 100%)

# ---- entity-id adoption (cutover / rollback services) ----
LEGACY_PLATFORM = "control4"            # platform of the old light entities
LEGACY_SUFFIX = "_control4_old"         # old Control4 entity ids are renamed to <entity_id> + this suffix
RELEASED_SUFFIX = "_crestron"           # on rollback the Crestron entity ids are renamed to <entity_id> + this suffix
ADOPT_DRY_RUN_DEFAULT = True            # the services only describe what they would do unless dry_run is set to false

# ---- entity kinds ----
KIND_DIMMER = "Dimmer"
KIND_RELAY = "Relay"

# Mapping built from the Control4 project export (see crestron_join_map.md).
# (analog join, legacy Home Assistant entity_id, kind, room, Crestron label)
# Only the Theater joins (0x124-0x12A) are verified on the real lights; the rest follow the verified join rule.
LIGHTS: tuple[tuple[int, str, str, str, str], ...] = (
    (0x0101, "light.dimmer_garage_floods", "Dimmer", "Exterior", "Garage & Portico Flood"),  # Exterior / Garage Floods (Control4 id 319)
    (0x0102, "light.dimmer_back_floods", "Dimmer", "Exterior", "Back Porch & End House Flood"),  # Exterior / Back Floods (Control4 id 317)
    (0x0103, "light.crestron_familyroom_rope", "Dimmer", "Family", "Rope Light"),  # Family / Rope Lights (Control4 id 259)
    (0x0104, "light.interior_familyroom_art_lights", "Dimmer", "Family", "Mini Lights"),  # Family / Art Lights (Control4 id 257)
    (0x0105, "light.dimmer_downlights", "Dimmer", "Family", "downlight"),  # Family / Downlights (Control4 id 251)
    (0x0106, "light.relay_ceiling_fan_family_room", "Relay", "Family", "Fan"),  # Family / Fan (Control4 id 325)
    (0x0107, "light.relay_floor_outlet", "Relay", "Family", "Floor Recp"),  # Family / Floor Outlet (Control4 id 255)
    (0x0108, "light.interior_diningroom_chandelier", "Dimmer", "Family", "Kitchen Area"),  # Family / Chandelier (Control4 id 253)
    (0x0109, "light.interior_foyer_chandelier", "Dimmer", "Foyer", "Chandeliers & stairs"),  # Foyer / Chandelier (Control4 id 264)
    (0x010A, "light.interior_foyer_staircase_sconces", "Dimmer", "Foyer", "Foyer Sconce"),  # Foyer / Sconces (Control4 id 266)
    (0x010B, "light.interior_foyer_recessed", "Dimmer", "Foyer", "Hall Can"),  # Foyer / Hall Downlight (Control4 id 268)
    (0x010C, "light.dimmer_pendant", "Dimmer", "Front Porch", "Front Porch Can"),  # Front Porch / Pendant (Control4 id 270)
    (0x010D, "light.dimmer_sconces_3", "Dimmer", "Front Porch", "Front Porch Wall"),  # Front Porch / Sconces (Control4 id 273)
    (0x010E, "light.interior_gallery_chandeliers", "Dimmer", "Gallery", "Chandeliers (3)"),  # Gallery / Gallery (Control4 id 276)
    (0x0112, "light.interior_kitchen_counter_recessed", "Dimmer", "Kitchen", "Downlights"),  # Kitchen / Counter Downlights (Control4 id 282)
    (0x0113, "light.interior_kitchen_chandelier", "Dimmer", "Kitchen", "Chandeliers"),  # Kitchen / Chandelier (Control4 id 280)
    (0x0114, "light.dimmer_breakfast_chandelier", "Dimmer", "Kitchen", "Nook Chandelier"),  # Kitchen / Breakfast Chandelier (Control4 id 288)
    (0x0115, "light.interior_kitchen_dining_recessed", "Dimmer", "Kitchen", "Nook Mini"),  # Kitchen / Breakfast Downlights (Control4 id 290)
    (0x0116, "light.interior_kitchen_top_recessed", "Dimmer", "Kitchen", "Downlights 2"),  # Kitchen / Downlights (Control4 id 284)
    (0x0117, "light.dimmer_stove", "Dimmer", "Kitchen", "Kitchen stove"),  # Kitchen / Stove (Control4 id 286)
    (0x0118, "light.interior_diningroom_wallsconces", "Dimmer", "Living & Dining", "Living Wall Sconce"),  # Living & Dining / Wall Sconces (Control4 id 292)
    (0x0119, "light.interior_livingroom_art_lights", "Dimmer", "Living & Dining", "Living Fireplace"),  # Living & Dining / Fireplace (Control4 id 294)
    (0x011A, "light.interior_livingroom_chandelier", "Dimmer", "Living & Dining", "Living Chandelier"),  # Living & Dining / Chandelier (Control4 id 296)
    (0x011B, "light.interior_kitchen_entry_recessed", "Dimmer", "Living & Dining", "Bnook"),  # Living & Dining / Entry Downlights (Control4 id 300)
    (0x011C, "light.crestron_livingroom_rope", "Relay", "Living & Dining", "Living Rope Lights"),  # Living & Dining / Rope Lights (Control4 id 302)
    (0x011D, "light.interior_diningroom_downlights", "Dimmer", "Living & Dining", "Dining Mini"),  # Living & Dining / Table Downlights (Control4 id 298)
    (0x011E, "light.dimmer_sconce", "Dimmer", "Master Bath", "Vanity Sconce"),  # Master Bath / Sconce (Control4 id 314)
    (0x011F, "light.relay_sink", "Relay", "Master Bath", "Bath Vanity"),  # Master Bath / Sink (Control4 id 304)
    (0x0120, "light.dimmer_vanity", "Dimmer", "Master Bath", "Dressing Lights"),  # Master Bath / Vanity (Control4 id 306)
    (0x0121, "light.dimmer_tub", "Dimmer", "Master Bath", "Tub Can"),  # Master Bath / Tub (Control4 id 312)
    (0x0122, "light.dimmer_chandelier_5", "Dimmer", "Master Bath", "Chandeliers"),  # Master Bath / Chandelier (Control4 id 310)
    (0x0123, "light.dimmer_shower", "Dimmer", "Master Bath", "Shower"),  # Master Bath / Shower (Control4 id 308)
    (0x0124, "light.dimmer_theater_front_recessed", "Dimmer", "Theater", "Downlights"),  # Theater / Front Downlights (Control4 id 237)
    (0x0125, "light.dimmer_rear_downlights", "Dimmer", "Theater", "Downlights 2"),  # Theater / Rear Downlights (Control4 id 239)
    (0x0126, "light.dimmer_bar_pendants", "Dimmer", "Theater", "Sconces"),  # Theater / Bar Pendants (Control4 id 245)
    (0x0127, "light.dimmer_sconces", "Dimmer", "Theater", "Sconces 2"),  # Theater / Sconces (Control4 id 247)
    (0x0128, "light.dimmer_bar_lights", "Dimmer", "Theater", "Mini"),  # Theater / Bar Lights (Control4 id 241)
    (0x0129, "light.dimmer_step_lights", "Dimmer", "Theater", "Rope Lights"),  # Theater / Step Lights (Control4 id 243)
    (0x012A, "light.interior_foyer_staircase_basement_recessed", "Dimmer", "Theater", "Stair Lights"),  # Theater / Stair Lights (Control4 id 249)
    (0x012C, "light.dimmer_art_lights_dining_room", "Dimmer", "Living & Dining", "Dining Art Lights"),  # Living & Dining / Art Lights (Control4 id 402)
)

EVENT_DIGITAL = "crestron_pac2m_digital"   # fired on the HA bus for every digital join change (keypad groundwork)
