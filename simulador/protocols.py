"""Protocolos de transporte (UDP e TCP), cálculo de checksum, RTT e estruturas de segmento."""
import os
import struct
from dataclasses import dataclass, replace

from .fsm import FSMDefinition, FSMEngine

DEFS = os.path.join(os.path.dirname(__file__), "..", "fsm_defs")


# =====================================================================
# 1. Checksum da Internet (complemento de 1 de 16 bits - Seção 3.3)
# =====================================================================
def ones_complement_sum16(data: bytes) -> int:
    """Soma em complemento de 1 de palavras de 16 bits com vai-um circular (wrap-around)."""
    if len(data) % 2:
        data += b"\x00"
    total = 0
    for i in range(0, len(data), 2):
        total += (data[i] << 8) | data[i + 1]
        total = (total & 0xFFFF) + (total >> 16)
    return total & 0xFFFF


def internet_checksum(data: bytes) -> int:
    """Complemento de 1 da soma de 16 bits."""
    return (~ones_complement_sum16(data)) & 0xFFFF


def verify(data_with_checksum: bytes) -> bool:
    """Verificação do receptor: soma incluindo o checksum deve ser 0xFFFF."""
    return ones_complement_sum16(data_with_checksum) == 0xFFFF


# =====================================================================
# 2. Estimativa de RTT e Timeout (Seção 3.5.3)
# =====================================================================
class RTTEstimator:
    """Estimativa de RTT e TimeoutInterval com média ponderada exponencial e Karn."""
    ALPHA = 0.125
    BETA = 0.25

    def __init__(self, initial_timeout=1000.0, min_rto=200.0, max_rto=60000.0):
        self.estimated = None
        self.dev = None
        self.initial = initial_timeout
        self.min_rto = min_rto
        self.max_rto = max_rto
        self.timeout = initial_timeout

    def sample(self, r):
        if self.estimated is None:
            self.estimated = r
            self.dev = r / 2
        else:
            self.dev = (1 - self.BETA) * self.dev + self.BETA * abs(r - self.estimated)
            self.estimated = (1 - self.ALPHA) * self.estimated + self.ALPHA * r
        self.timeout = self.formula()
        return self.timeout

    def formula(self):
        """TimeoutInterval = EstimatedRTT + 4*DevRTT (com piso e teto práticos)."""
        if self.estimated is None:
            return self.initial
        return min(self.max_rto, max(self.min_rto, self.estimated + 4 * self.dev))

    def backoff(self):
        """Em caso de timeout, o intervalo é dobrado."""
        self.timeout = min(self.max_rto, self.timeout * 2)
        return self.timeout

    def reset_to_formula(self):
        self.timeout = self.formula()


# =====================================================================
# 3. Segmento de Rede (Datagrama UDP / Segmento TCP)
# =====================================================================
FLAG_BITS = {"FIN": 0x01, "SYN": 0x02, "RST": 0x04, "ACK": 0x10}


@dataclass
class Segment:
    """Segmento UDP/TCP com cabeçalho binário real e cálculo de checksum."""
    proto: str                       # "UDP" ou "TCP"
    src_port: int
    dst_port: int
    seq: int = 0
    ack: int = 0
    flags: frozenset = frozenset()
    window: int = 0
    payload: bytes = b""
    checksum: int = 0
    # metadados de simulação
    tx_id: int = 0
    retx: bool = False

    def _header(self, checksum):
        if self.proto == "UDP":
            return struct.pack("!HHHH", self.src_port, self.dst_port, 8 + len(self.payload), checksum)
        fl = 0
        for f in self.flags:
            fl |= FLAG_BITS[f]
        return struct.pack("!HHIIBxHHH", self.src_port, self.dst_port, self.seq & 0xFFFFFFFF,
                           self.ack & 0xFFFFFFFF, fl, self.window & 0xFFFF, len(self.payload), checksum)

    def to_bytes(self):
        return self._header(self.checksum) + self.payload

    def compute_checksum(self):
        c = internet_checksum(self._header(0) + self.payload)
        if self.proto == "UDP" and c == 0:
            c = 0xFFFF                        # convenção UDP
        return c

    def seal(self):
        self.checksum = self.compute_checksum()
        return self

    def verify(self):
        return verify(self.to_bytes())

    @property
    def length(self):
        return 8 + len(self.payload) if self.proto == "UDP" else len(self.payload)

    @property
    def seqlen(self):
        """Espaço de sequência ocupado (SYN e FIN contam 1 byte)."""
        return len(self.payload) + ("SYN" in self.flags) + ("FIN" in self.flags)

    def label(self):
        if self.proto == "UDP":
            return f"UDP {self.src_port}→{self.dst_port} len={self.length}"
        fl = ",".join(f for f in ("SYN", "FIN", "RST", "ACK") if f in self.flags)
        if self.payload:
            fl = "DATA" + ("" if self.flags <= {"ACK"} else "," + fl)
        parts = [fl or "-"]
        if self.payload or "SYN" in self.flags or "FIN" in self.flags:
            parts.append(f"SEQ={self.seq}")
        if "ACK" in self.flags:
            parts.append(f"ACK={self.ack}")
        if self.payload:
            parts.append(f"len={len(self.payload)}")
        return " ".join(parts)

    def copy(self):
        return replace(self)

    def corrupted(self, rng):
        """Retorna uma cópia com 1 bit invertido para teste de detecção de erro."""
        raw = bytearray(self.payload + struct.pack("!H", self.checksum))
        bit = rng.randrange(len(raw) * 8)
        raw[bit // 8] ^= 1 << (bit % 8)
        c = self.copy()
        c.payload = bytes(raw[:-2])
        c.checksum = struct.unpack("!H", bytes(raw[-2:]))[0]
        return c


# =====================================================================
# 4. UDP Endpoint (FSM de Estado Único)
# =====================================================================
class UDPEndpoint:
    """UDP: sem conexão, sem handshake, sem retransmissão — apenas multiplexação e checksum."""

    def __init__(self, name, port, sim, definition=None):
        self.name, self.port, self.sim = name, port, sim
        self.bus, self.channel = sim.bus, sim.channel
        self.peer_port = None
        self.engine = FSMEngine(definition or FSMDefinition.from_file(os.path.join(DEFS, "udp.json")),
                                name, sim.bus, "UDP")
        self.engine.register_guard("checksum_ok", lambda ctx: ctx["seg"].verify())
        self.engine.register_guard("checksum_bad", lambda ctx: not ctx["seg"].verify())
        self.engine.register_action("send_datagram", self._a_send)
        self.engine.register_action("deliver", self._a_deliver)
        self.engine.register_action("discard", self._a_discard)
        self.engine.validate()
        self.received = []          # (t, bytes) entregues à aplicação
        self.sent_count = 0
        sim.channel.attach(name, self.on_segment)

    @property
    def state(self):
        return self.engine.state

    def send(self, data: bytes):
        self.engine.fire("app_send", {"data": data})

    def _a_send(self, arg, ctx):
        seg = Segment("UDP", self.port, self.peer_port, payload=ctx["data"]).seal()
        self.sent_count += 1
        self.channel.transmit(self.name, seg)

    def _a_deliver(self, arg, ctx):
        seg = ctx["seg"]
        self.received.append((self.sim.clock.now, seg.payload))
        self.bus.emit("deliver", host=self.name, data=seg.payload, seq=None)

    def _a_discard(self, arg, ctx):
        # Descarte silencioso sem notificar o remetente
        self.bus.emit("drop", host=self.name, reason="checksum inválido (descartado sem notificar o remetente)",
                      label=ctx["seg"].label())

    def on_segment(self, seg):
        if seg.dst_port != self.port:
            self.bus.emit("drop", host=self.name, reason=f"porta {seg.dst_port} sem socket", label=seg.label())
            return
        self.bus.emit("rx", host=self.name, label=seg.label(), ok=seg.verify())
        self.engine.fire("rcv_datagram", {"seg": seg})


# =====================================================================
# 5. TCP Endpoint (11 Estados + Transferência Confiável)
# =====================================================================
SYNC_STATES = ("ESTABLISHED", "CLOSE_WAIT")


@dataclass
class TCPConfig:
    mss: int = 100
    isn: int = 100
    recv_buffer: int = 6400          # tamanho do buffer de recepção (rwnd máximo)
    initial_rto: float = 1000.0      # ms (valor inicial recomendado pelo livro: 1 s)
    min_rto: float = 200.0           # piso prático para o TimeoutInterval
    msl: float = 1000.0              # ms; TIME_WAIT dura 2*MSL (acelerável)
    max_retx: int = 8                # timeouts consecutivos antes de abortar
    fast_retransmit: bool = True
    dupack_threshold: int = 3
    congestion_control: bool = False  # opcional: slow start + AIMD
    time_wait: bool = True            # False = remove o TIME_WAIT (experimento do relatório)


class TCPEndpoint:
    """TCP: FSM de 11 estados, SEQ/ACK reais, RTT/RTO, ACK cumulativo e Fast Retransmit."""

    def __init__(self, name, port, peer_port, sim, cfg=None, definition=None):
        self.name, self.port, self.peer_port, self.sim = name, port, peer_port, sim
        self.cfg = cfg or TCPConfig()
        self.clock, self.bus, self.channel = sim.clock, sim.bus, sim.channel
        self.engine = FSMEngine(definition or FSMDefinition.from_file(os.path.join(DEFS, "tcp_connection.json")),
                                name, sim.bus, "TCP")
        e = self.engine
        for g in ("ack_of_syn", "acks_fin", "time_wait_on", "time_wait_off", "retx_ok", "retx_exhausted"):
            e.register_guard(g, getattr(self, "_g_" + g))
        for a in ("send", "send_rst", "init_active", "init_passive", "set_irs", "process_ack", "process_data",
                  "process_fin", "start_timer", "restart_timer", "retransmit", "delete_tcb"):
            e.register_action(a, getattr(self, "_a_" + a))
        e.validate()
        e.after_hook = self._after
        self.channel.attach(name, self.on_segment)
        self.send_data = bytearray()
        self.sent_off = 0
        self.received = bytearray()
        self.close_requested = False
        self.timers = {}
        self.retx_log = []
        self.stats = dict(retx_timeout=0, retx_fast=0, dupacks=0, dup_segments=0, ooo_segments=0)
        self._pending = []
        self._reset_tcb()

    def _reset_tcb(self):
        for n in list(self.timers):
            self._stop_timer(n, quiet=True)
        self.iss = self.snd_una = self.snd_nxt = 0
        self.snd_wnd = self.cfg.mss
        self.irs = self.rcv_nxt = 0
        self.ooo = {}
        self.queue = []
        self.rtt_probe = None
        self.rtt = RTTEstimator(self.cfg.initial_rto, self.cfg.min_rto)
        self.dupacks = 0
        self.consecutive_timeouts = 0
        self.fin_sent = False
        self.fin_seq = None
        self.cwnd = self.cfg.mss
        self.ssthresh = 65535
        self.in_recovery = False

    @property
    def state(self):
        return self.engine.state

    def rwnd(self):
        return max(0, self.cfg.recv_buffer - sum(len(d) for d in self.ooo.values()))

    # ---- API da aplicação ----
    def listen(self):
        self.engine.fire("passive_open")

    def connect(self):
        self.engine.fire("active_open")

    def send(self, data: bytes):
        self.send_data += data
        self._try_send()

    def close(self):
        self.close_requested = True
        self._maybe_close()

    def _maybe_close(self):
        if not self.close_requested:
            return
        if self.state in SYNC_STATES and self.sent_off < len(self.send_data):
            return                       # FIN só depois de enviar todos os dados pendentes
        self.close_requested = False
        self.engine.fire("close")

    # ---- Envio de segmentos ----
    def _tx(self, seq, flags, payload=b"", retx=False):
        seg = Segment("TCP", self.port, self.peer_port, seq=seq,
                      ack=self.rcv_nxt if "ACK" in flags else 0, flags=frozenset(flags),
                      window=self.rwnd(), payload=payload, retx=retx).seal()
        self.channel.transmit(self.name, seg)
        return seg

    def _eff_window(self):
        w = self.snd_wnd
        return min(w, self.cwnd) if self.cfg.congestion_control else w

    def _try_send(self):
        if self.state not in SYNC_STATES:
            return
        while self.sent_off < len(self.send_data):
            n = min(self.cfg.mss, len(self.send_data) - self.sent_off)
            inflight = self.snd_nxt - self.snd_una
            if inflight > 0 and self._eff_window() - inflight < n:
                break
            payload = bytes(self.send_data[self.sent_off:self.sent_off + n])
            seq = self.snd_nxt
            self.queue.append(dict(seq=seq, seqlen=n, flags={"ACK"}, payload=payload, count=0))
            self.snd_nxt += n
            self.sent_off += n
            self._tx(seq, {"ACK"}, payload)
            if self.rtt_probe is None:
                self.rtt_probe = (seq + n, self.clock.now)
            if "retx" not in self.timers:
                self._start_timer("retx", self.rtt.timeout)
        self._maybe_close()

    # ---- Timers ----
    def _start_timer(self, name, delay):
        self._stop_timer(name, quiet=True)
        self.timers[name] = self.clock.schedule(delay, lambda: self._on_timer(name), f"{self.name}:{name}")
        self.bus.emit("timer", host=self.name, name=name, op="start", duration=round(delay, 1))

    def _stop_timer(self, name, quiet=False):
        ev = self.timers.pop(name, None)
        if ev is not None:
            self.clock.cancel(ev)
            if not quiet:
                self.bus.emit("timer", host=self.name, name=name, op="stop")

    def _on_timer(self, name):
        self.timers.pop(name, None)
        self.bus.emit("timer", host=self.name, name=name, op="expire")
        if name == "retx":
            if not self.queue:
                return
            self.consecutive_timeouts += 1
            self.engine.fire("timeout")
        elif name == "2msl":
            self.engine.fire("timeout_2msl")

    # ---- Guardas da FSM ----
    def _g_ack_of_syn(self, ctx):
        s = ctx["seg"]
        return "ACK" in s.flags and s.ack == self.iss + 1

    def _g_acks_fin(self, ctx):
        s = ctx["seg"]
        return self.fin_sent and "ACK" in s.flags and s.ack >= self.fin_seq + 1

    def _g_time_wait_on(self, ctx):
        return self.cfg.time_wait

    def _g_time_wait_off(self, ctx):
        return not self.cfg.time_wait

    def _g_retx_ok(self, ctx):
        return self.consecutive_timeouts <= self.cfg.max_retx

    def _g_retx_exhausted(self, ctx):
        return self.consecutive_timeouts > self.cfg.max_retx

    # ---- Ações da FSM ----
    def _a_init_active(self, arg, ctx):
        self._reset_tcb()
        self.iss = self.snd_una = self.snd_nxt = self.cfg.isn

    def _a_init_passive(self, arg, ctx):
        seg = ctx["seg"]
        self._reset_tcb()
        self.peer_port = seg.src_port
        self.iss = self.snd_una = self.cfg.isn
        self.snd_nxt = self.iss + 1
        self.irs, self.rcv_nxt = seg.seq, seg.seq + 1
        self.snd_wnd = seg.window

    def _a_set_irs(self, arg, ctx):
        seg = ctx["seg"]
        self.irs, self.rcv_nxt = seg.seq, seg.seq + 1

    def _a_send(self, arg, ctx):
        if arg == "SYN":
            self.queue.append(dict(seq=self.iss, seqlen=1, flags={"SYN"}, payload=b"", count=0))
            self.snd_nxt = self.iss + 1
            self._tx(self.iss, {"SYN"})
            self.rtt_probe = (self.iss + 1, self.clock.now)
            self._start_timer("retx", self.rtt.timeout)
        elif arg == "SYNACK":
            self._tx(self.iss, {"SYN", "ACK"})
        elif arg == "ACK":
            self._tx(self.snd_nxt, {"ACK"})
        elif arg == "FIN":
            self.fin_seq = self.snd_nxt
            self.fin_sent = True
            self.queue.append(dict(seq=self.fin_seq, seqlen=1, flags={"FIN", "ACK"}, payload=b"", count=0))
            self.snd_nxt += 1
            self._tx(self.fin_seq, {"FIN", "ACK"})
            if "retx" not in self.timers:
                self._start_timer("retx", self.rtt.timeout)

    def _a_send_rst(self, arg, ctx):
        seg = ctx["seg"]
        port = seg.src_port
        if "ACK" in seg.flags:
            rst = Segment("TCP", self.port, port, seq=seg.ack, flags=frozenset({"RST"}))
        else:
            rst = Segment("TCP", self.port, port, seq=0, ack=seg.seq + seg.seqlen, flags=frozenset({"RST", "ACK"}))
        self.channel.transmit(self.name, rst.seal())

    def _a_process_ack(self, arg, ctx):
        seg = ctx["seg"]
        if "ACK" not in seg.flags:
            return
        ack = seg.ack
        self.snd_wnd = seg.window
        if ack > self.snd_nxt:
            return
        if ack > self.snd_una:
            acked = ack - self.snd_una
            self.snd_una = ack
            self.queue = [r for r in self.queue if r["seq"] + r["seqlen"] > ack]
            if self.rtt_probe and ack >= self.rtt_probe[0]:
                r = self.clock.now - self.rtt_probe[1]
                self.rtt.sample(r)
                self.bus.emit("rtt", host=self.name, sample=round(r, 2), est=round(self.rtt.estimated, 2),
                              dev=round(self.rtt.dev, 2), timeout=round(self.rtt.timeout, 2))
            self.rtt_probe = None if (self.rtt_probe and ack >= self.rtt_probe[0]) else self.rtt_probe
            self.dupacks = 0
            self.consecutive_timeouts = 0
            self.rtt.reset_to_formula()
            self._cc_new_ack(acked)
            if self.queue:
                self._start_timer("retx", self.rtt.timeout)
            else:
                self._stop_timer("retx", quiet=True)
        elif (ack == self.snd_una and self.queue and not seg.payload
              and not ({"SYN", "FIN"} & seg.flags)):
            self.dupacks += 1
            self.stats["dupacks"] += 1
            self.bus.emit("dupack", host=self.name, ack=ack, count=self.dupacks)
            if self.cfg.fast_retransmit and self.dupacks == self.cfg.dupack_threshold:
                self._pending.append("dupack3")
            elif self.cfg.congestion_control and self.in_recovery and self.dupacks > self.cfg.dupack_threshold:
                self.cwnd += self.cfg.mss

    def _a_process_data(self, arg, ctx):
        seg = ctx["seg"]
        data = seg.payload
        if not data:
            return
        if seg.seq == self.rcv_nxt:
            self._deliver(seg.seq, data)
            self.rcv_nxt += len(data)
            while self.rcv_nxt in self.ooo:
                d = self.ooo.pop(self.rcv_nxt)
                self._deliver(self.rcv_nxt, d)
                self.rcv_nxt += len(d)
        elif seg.seq > self.rcv_nxt:
            self.stats["ooo_segments"] += 1
            if seg.seq not in self.ooo and len(data) <= self.rwnd():
                self.ooo[seg.seq] = data
            self.bus.emit("info", host=self.name, msg=f"segmento fora de ordem (SEQ={seg.seq}, esperado {self.rcv_nxt})")
        else:
            self.stats["dup_segments"] += 1
            self.bus.emit("info", host=self.name, msg=f"segmento duplicado (SEQ={seg.seq}) descartado")
        self._tx(self.snd_nxt, {"ACK"})

    def _deliver(self, seq, data):
        self.received += data
        self.bus.emit("deliver", host=self.name, seq=seq, data=data)

    def _a_process_fin(self, arg, ctx):
        seg = ctx["seg"]
        if seg.seq == self.rcv_nxt:
            self.rcv_nxt += 1

    def _a_start_timer(self, arg, ctx):
        self._start_timer(arg, 2 * self.cfg.msl if arg == "2msl" else self.rtt.timeout)

    _a_restart_timer = _a_start_timer

    def _a_retransmit(self, arg, ctx):
        if not self.queue:
            return
        rec = self.queue[0]
        if arg == "timeout":
            self.stats["retx_timeout"] += 1
            self.rtt.backoff()
            self._cc_loss("timeout")
        else:
            self.stats["retx_fast"] += 1
            self._cc_loss("fast")
        rec["count"] += 1
        seg = self._tx(rec["seq"], rec["flags"], rec["payload"], retx=True)
        self.rtt_probe = None
        self.retx_log.append(dict(t=self.clock.now, reason=arg, seq=rec["seq"], tx_id=seg.tx_id))
        self.bus.emit("retransmit", host=self.name, reason=arg, seq=rec["seq"], tx_id=seg.tx_id)
        self._start_timer("retx", self.rtt.timeout)

    def _a_delete_tcb(self, arg, ctx):
        self._reset_tcb()
        self.bus.emit("info", host=self.name, msg="TCB removido")

    # ---- Congestion Control (Opcional) ----
    def _cc_new_ack(self, acked):
        if not self.cfg.congestion_control:
            return
        mss = self.cfg.mss
        if self.in_recovery:
            self.cwnd, self.in_recovery = self.ssthresh, False
        elif self.cwnd < self.ssthresh:
            self.cwnd += min(acked, mss)
        else:
            self.cwnd += max(1, mss * mss // self.cwnd)
        self.bus.emit("cwnd", host=self.name, cwnd=self.cwnd, ssthresh=self.ssthresh)

    def _cc_loss(self, kind):
        if not self.cfg.congestion_control:
            return
        mss = self.cfg.mss
        self.ssthresh = max((self.snd_nxt - self.snd_una) // 2, 2 * mss)
        if kind == "timeout":
            self.cwnd, self.in_recovery = mss, False
        else:
            self.cwnd, self.in_recovery = self.ssthresh + 3 * mss, True
        self.bus.emit("cwnd", host=self.name, cwnd=self.cwnd, ssthresh=self.ssthresh)

    def _after(self, prev, new, tr, ctx):
        while self._pending:
            self.engine.fire(self._pending.pop(0))
        self._try_send()
        self._maybe_close()

    @staticmethod
    def classify(seg):
        f = seg.flags
        if "RST" in f:
            return "rcv_RST"
        if "SYN" in f:
            return "rcv_SYNACK" if "ACK" in f else "rcv_SYN"
        if "FIN" in f:
            return "rcv_FIN"
        if seg.payload:
            return "rcv_DATA"
        return "rcv_ACK"

    def on_segment(self, seg):
        ok = seg.verify()
        self.bus.emit("rx", host=self.name, label=seg.label(), ok=ok)
        if not ok:
            self.bus.emit("drop", host=self.name, reason="checksum inválido", label=seg.label())
            return
        if seg.dst_port != self.port:
            self.bus.emit("drop", host=self.name, reason=f"porta {seg.dst_port} sem socket", label=seg.label())
            return
        if ("FIN" in seg.flags and self.state not in ("CLOSED", "LISTEN", "SYN_SENT")
                and seg.seq > self.rcv_nxt):
            self.bus.emit("drop", host=self.name, reason="FIN fora de ordem (faltam dados antes dele)",
                          label=seg.label())
            self._tx(self.snd_nxt, {"ACK"})
            return
        self.engine.fire(self.classify(seg), {"seg": seg})
