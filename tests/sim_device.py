"""Simulated Firehawk (device side of RSMC + streams + services) for offline tests."""
import struct, threading, zlib
from firehawk.rsmc import Rsmc, crc16
from firehawk.stream import transport_header, F_SYN, F_DATA, F_KEEP, F_FIN, F_HB

class SimDevice:
    def __init__(self, table: bytes):
        self.table = table
        self.to_host = None          # set to host.feed
        self.link = Rsmc(lambda b: self.to_host(b), self._deliver)
        self.ports = {}              # remote(host) port -> state
        self.params = {}
        self.preset = 0
        self.log = []
        self.raw_frames = []
        import firehawk.services as _s
        st = _s.SymbolTable(table); self.names, self.index = st.names, st.index
        self.chain = {'Gate': 'Gate', 'Wah': 'Weeper', 'VolumePedal': 'VolumePedal', 'FX1': 'HD_ColorDrive',
                      'FX2': 'AnalogChorus', 'Amp': 'HD_AmpBlackfaceDoubleVib', 'StudioCab': '2x12BlackfaceStudio57OnAxis',
                      'FX3': 'DynamicDelay', 'FX4': 'RezSynth', 'FX5': 'HD_VerbzillaReverb63Spring', 'Looper': 'LooperFx500'}
        for k, v in list(self.chain.items()):
            if v not in self.index: self.chain[k] = next((n for n in self.names if n.startswith(v[:12])), v)
    def feed(self, b):
        self.raw_frames.append(b)
        self.link.feed(b)
    def _send(self, dst, src, flags, data=b''):
        st = self.ports[dst]
        # honour host credit like the real unit: queue until credit allows (FUN_0013128c)
        if data:
            st.setdefault('outq', b'')
            st['outq'] += data
            while st['outq']:
                room = st.get('credit', 0x1000) - st.get('sent', 0)
                if room <= 0:
                    return
                chunk, st['outq'] = st['outq'][:min(room, 200)], st['outq'][min(room, 200):]
                st['sent'] = st.get('sent', 0) + len(chunk)
                self._send_raw(dst, src, flags, chunk)
            return
        self._send_raw(dst, src, flags, data)
    def _send_raw(self, dst, src, flags, data=b''):
        st = self.ports[dst]
        st['seq'] = (st['seq'] + 1) & 0xff
        body = struct.pack('<II', (flags << 24) | (st['seq'] << 8), 0x1000) + data
        if flags & F_SYN:
            body = struct.pack('<I', 0xe4 << 16) + body
            self.link.send(transport_header(2, len(body), src, dst) + body)
        else:
            self.link.send(transport_header(1, len(body), src, dst) + body)
    def _deliver(self, payload):
        w0, w1 = struct.unpack_from('<II', payload, 0)
        t, ln = w0 >> 28, w0 & 0xffff
        src, dst = w1 & 0xffff, w1 >> 16
        body = payload[8:8 + ln]
        if t == 2: body = body[4:]
        wa, credit = struct.unpack_from('<II', body, 0)
        flags, seq = wa >> 24, (wa >> 8) & 0xff
        data = body[8:]
        if flags & F_SYN:
            self.ports[src] = {'seq': 0x40, 'buf': b'', 'mode': None if dst == 0x3ef else 'editor', 'last': seq}
            self._send(src, dst, F_SYN | F_HB); return
        st = self.ports[src]
        assert (seq - st['last']) & 0xff == 1, ('seq', st['last'], seq)
        st['last'] = seq
        if flags & 0x0c:                       # credit only from DATA/WND, like the firmware
            st['credit'] = credit
            if st.get('outq'):
                q, st['outq'] = st['outq'], b''
                self._send(src, dst, F_DATA, q)
        if flags & F_KEEP: self.log.append(('keep', src))
        if not data: return
        st['buf'] += data
        if st['mode'] is None and len(st['buf']) >= 4:
            st['mode'], = struct.unpack_from('<I', st['buf']); st['buf'] = st['buf'][4:]
            self._send(src, dst, F_DATA, struct.pack('<I', st['mode']))
        while len(st['buf']) >= 8:
            cmd, n = struct.unpack_from('<II', st['buf'])
            if len(st['buf']) < 8 + n: break
            args = st['buf'][8:8 + n]; st['buf'] = st['buf'][8 + n:]
            self.log.append((st['mode'], cmd, args))
            r = self._rpc(st['mode'], cmd, args, src, dst)
            self._send(src, dst, F_DATA, struct.pack('<II', 1, len(r)) + r)
    def _rpc(self, mode, cmd, a, src, dst):
        if cmd == 1: return struct.pack('<I', 0x1300000)
        if cmd == 2: return struct.pack('<I', len(self.table))
        if cmd == 3: return self.table
        if cmd == 4: return struct.pack('<i', 0 if struct.unpack('<I', a)[0] == zlib.crc32(self.table) else -1)
        if mode == 'editor' and cmd == 10:
            s, p, t, v = struct.unpack('<IIII', a); self.params[(s, p)] = (t, v)
            # echo a param-change event (type 4) like the unit does when a knob moves
            ev = a
            threading.Timer(0.01, lambda: self._send(src, dst, F_DATA, struct.pack('<II', 4, len(ev)) + ev)).start()
            return struct.pack('<i', 0)
        if mode == 'editor' and cmd == 9:
            s, p = struct.unpack('<II', a); t, v = self.params.get((s, p), (4, 0))
            return struct.pack('<iII', 0, t, v)
        if mode == 'editor' and cmd == 7:
            slot, = struct.unpack('<I', a); name = self.names[slot] if slot < len(self.names) else ''
            m = self.chain.get(name)
            if m is None or m not in self.index: return struct.pack('<iI', -4, 0)
            return struct.pack('<iI', 0, self.index[m])
        if mode == 1 and cmd == 5: return struct.pack('<I', 3)
        if mode == 1 and cmd == 6:
            i, = struct.unpack('<I', a); name = [b'Clean', b'Crunch', b'Lead'][i]
            return struct.pack('<iI', 0, len(name) + 1) + name.ljust(64, b'\0')
        if mode == 1 and cmd == 0xd:
            self.preset = struct.unpack('<I', a)[0]
            ed = [k for k, v in self.ports.items() if v['mode'] == 'editor']
            if ed:
                ev = struct.pack('<iiii', 2, self.preset, 2, 0)
                threading.Timer(0.01, lambda: self._send(ed[0], 0x3ed, F_DATA, struct.pack('<II', 3, 16) + ev)).start()
            return struct.pack('<i', 0)
        return struct.pack('<i', -4)
