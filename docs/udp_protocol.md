# Crestron PAC2M CIP-over-UDP protocol (reverse-engineered, Oct 2026)

Processor: PAC2M (2-Series, firmware 4.001.1012). Console: telnet 23, no login. Not Crestron Home OS, so the
bwcon `crestron_home` integration cannot work. The processor's IP table entry (CIP_ID 6, type CIP, port 41794) is an
outbound peer: it connects to that IP and only accepts commands from it.

## Framing (UDP, processor port 41794; the client sends from any port and listens on 41794)
- Data: `0b <seq> 00 00 00 <len> 00 06 <payload>`   (len = payload length + 2)
- Ack:  `0c a7 a2 cc 80 a7 00 06 <seq of the acked message>` - every data message in either direction is acked with the same seq
- Sequence numbers are per sender; the processor does NOT enforce them (a 0-255 sweep made no difference).

## Payloads
- Analog (16-bit, 0000 off / ffff full): `04 <join hi> <join lo> <level hi> <level lo>`
- Digital: `03 00 <join lo> <join hi>` (join hi bit 7 set = LOW)
- Ping: `01 00`   Hello (processor to client): `02 03 00`   Time: `08 08 ...`
- Name entry (processor to client): `<len> 12 <id> <text>`; analog join = `0x100 + (id + 1) / 2`

## The keep-alive (the key discovery)
Every ~30 s the client must send a ping (`01 00`) followed by digital join 4000 high (`03 00 a0 0f`).
Without them the processor acks commands but silently ignores them. With them: hello + full state dump
(names, analog levels, digital joins), `ipt` shows ONLINE, commands are applied, and the processor sends feedback
(`04 01 <join> <level>` plus an on/off digital pair) whenever a level changes. No feedback when the level does not change.

## Session start
1. Client sends ping + join 4000 high. 2. Processor acks both and sends hello, time, digital joins, analog levels, 41 names (~112 frames in 3 s).
3. Client acks every frame. 4. Client repeats the keep-alive every 30 s. If the processor itself starts the session (after a reboot) the client just acks the hello and starts the keep-alive.

## IP table (console)
`ipt` shows it. `REMSlave 6 <ip>` / `ADDSlave 6 <ip>` edit it but **only take effect after `reboot`** (until then: blank type, `NOT REG`).
After the reboot the row goes `CIP OFFLINE`, then `CIP ONLINE` once the peer answers. See `lessons_learned.md`.
