import datetime
import struct
import sys

path = sys.argv[1]
with open(path, 'rb') as fh:
    data = fh.read()
magic = data[:4]
end = '<' if magic in (b'\xd4\xc3\xb2\xa1', b'\x4d\x3c\xb2\xa1') else '>'
nano = magic in (b'\x4d\x3c\xb2\xa1', b'\xa1\xb2\x3c\x4d')
off = 24
t0 = None
n = 0
while off + 16 <= len(data):
    sec, frac, caplen, origlen = struct.unpack(end + 'IIII', data[off:off+16])
    off += 16
    pkt = data[off:off+caplen]; off += caplen
    ts = sec + frac / (1e9 if nano else 1e6)
    if t0 is None: t0 = ts
    n += 1
    if len(pkt) < 14: continue
    et = struct.unpack('>H', pkt[12:14])[0]
    if et != 0x0800:
        print(f"{n:3d} +{ts-t0:7.3f}s ethertype {et:#06x} len {len(pkt)}")
        continue
    ip = pkt[14:]
    ihl = (ip[0] & 0xf) * 4
    proto = ip[9]
    src = '.'.join(map(str, ip[12:16])); dst = '.'.join(map(str, ip[16:20]))
    l4 = ip[ihl:]
    clock = datetime.datetime.fromtimestamp(ts).strftime('%H:%M:%S.%f')[:-3]
    if proto == 17:
        sp, dp, ln = struct.unpack('>HHH', l4[:6])
        pl = l4[8:ln]
        print(f"{n:3d} {clock} +{ts-t0:7.3f}s {src}:{sp} -> {dst}:{dp} UDP {len(pl)}B  {pl.hex(' ')}")
    elif proto == 6:
        sp, dp = struct.unpack('>HH', l4[:4]); fl = l4[13]; doff = (l4[12] >> 4) * 4
        pl = l4[doff:]
        print(f"{n:3d} {clock} +{ts-t0:7.3f}s {src}:{sp} -> {dst}:{dp} TCP fl={fl:#04x} {len(pl)}B {pl.hex(' ')}")
    else:
        print(f"{n:3d} proto {proto} {src}->{dst}")
