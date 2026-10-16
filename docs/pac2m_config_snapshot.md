# Crestron PAC2M configuration snapshot (2026-10-16)

Repository: https://github.com/stargazer992/crestron-pac2m-ha
Captured read-only from the processor console (telnet 23). Purpose: everything needed to recognise, restore or replace the processor. The processor itself is at 192.168.104.111.

## Processor
| Item | Value |
|---|---|
| Model / firmware | PAC2M Cntrl Eng v4.001.1012 (Feb 17 2009) |
| Serial / PnP address | #003921A4 (00 39 21 A4) |
| Host name | PAC2 (system name in the program: Moraco_Tony) |
| IP table (at capture) | CIP_ID 6, CIP, port 41794, 192.168.104.120 (Home Assistant), ONLINE. Control4 was 192.168.104.25 |
| Boot mode | MASTER; IP table format DIP Level 2; max 252 entries; SSL, web server, USB supported |
| Processor clock | runs in UTC (20:44 when local time was 16:44) |
| `ipconfig` | not a valid command on this firmware (static vs DHCP address still to be confirmed) |

## Program loaded on the processor
| Item | Value |
|---|---|
| Boot directory | \SIMPL\ |
| Program file | Moraco_Tony.smw (SIMPL Windows v4.11.06) |
| Source path (installer's PC) | C:\Users\maston.richard\Desktop\Moraco_Tony\Programs\Moraco_Tony |
| Programmer | D3 Pro 3.03.020 (the lighting design tool) |
| Compiled | 10/30/2019 4:41 PM, compiler rev 3.03, config rev 22 |
| Libraries | CrestronDB 79.00.003.00, DeviceDB 105.07.001.00, SYMLIB 1089, IOLIB 1089, IOPCFG 2.1.176, Include4_2Series.dat 1.80 |
| Program Retrieve | **Yes** - the processor reports it can give the compiled program back (see recovery below) |

## Cresnet devices (from `reportcresnet`)
| Cresnet ID | Device | Firmware | Serial |
|---|---|---|---|
| 0B | CLX-2DIM8 | 1.15.00 | #0038867A |
| 0C | CLX-2DIM8 | 1.15.00 | #0038861D |
| 0D | CLX-2DIM8 | 1.15.00 | #00388639 |
| 0E | CLX-2DIM8 | 1.15.00 | #00388653 |
| 0F | C2N-MNETGW 2-Way Gateway | 2.000.0010 | #0039382A |
| 1A | C2N-DB6 keypad | 1.00 | #0038838E |
| 1B | C2N-DB6 keypad | 1.00 | #00388380 |
| 1C | C2N-DB6 keypad | 1.00 | #00388368 |
| 1D | C2N-DB6 keypad | 1.00 | #00386755 |
| 1E | C2N-DB12 keypad | 1.00 | #003546D7 |
| 1F | C2N-DB12 keypad | 1.00 | #003546E3 |
| 2A | C2N-DB12 keypad | 1.00 | #003546D5 |
| 2B | C2N-DB12 keypad | 1.00 | #003546FD |
| 2C | C2N-DB12 keypad | 1.00 | #003546E0 |
| 2D | C2N-DB6 keypad | 1.00 | #0038836C |
| 2E | C2N-DB12 keypad | 1.00 | #003546EB |
| 2F | C2N-DB6 keypad | 1.00 | #0037EAE8 |
| 66 | CLX-1DIM4 | 1.15.00 | #00385E11 |
| 6A | CLX-4HSW4 (4 relays) | 1.14.00 | #00384087 |
| 73 | C2N-DB6 keypad | 1.00 | #00388383 |

20 Cresnet devices in total: 4 x CLX-2DIM8, 1 x CLX-1DIM4, 1 x CLX-4HSW4, 1 gateway, 13 keypads.

## What a replacement processor needs
1. The compiled program (`Moraco_Tony`), loaded to the new PAC2M. It defines every join number this integration uses.
2. The same Cresnet IDs on the dimmer/relay/keypad devices (they keep their own IDs, so normally nothing to change).
3. The IP table entry (CIP_ID 6, port 41794) pointing at Home Assistant, then a reboot.
4. The processor's own IP (192.168.104.111) set the same, or the integration host changed.

## Recovery options for the program (do this while the processor still works)
* Crestron Toolbox can retrieve the program from this processor ("Program Retrieve: Yes"). Save that file somewhere safe.
* Ask the installer ("Extra Vegetables" per the Control4 driver) for the SIMPL Windows project (`Moraco_Tony.smw` and its compiled .lpz/.spz) and the D3 Pro lighting design file.
* Keep this snapshot and docs/join_map.md next to them.

## Not captured
* Whether the processor address is static or DHCP.
* The processor's console password setting, if any (the console currently needs none).
* The program's source (the .smw) - only the compiled program and its comments are on the processor.
