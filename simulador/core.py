"""Núcleo da simulação: relógio virtual, barramento de eventos, canal com ruídos e fachada Simulation."""
import heapq
import itertools
import json
import random
from dataclasses import dataclass

from .fsm import FSMDefinition
from .protocols import TCPConfig, TCPEndpoint, UDPEndpoint

PORT_A, PORT_B = 49152, 80


# =====================================================================
# 1. Relógio virtual + agendador de eventos discretos (Scheduler)
# =====================================================================
class Scheduler:
    """Agendador de eventos discretos com tempo virtual em milissegundos."""

    def __init__(self):
        self.now = 0.0
        self._heap = []
        self._counter = itertools.count()

    def schedule(self, delay, fn, label=""):
        """Agenda fn para now+delay. Retorna um handle usado em cancel()."""
        ev = [self.now + max(0.0, delay), next(self._counter), fn, label, False]
        heapq.heappush(self._heap, ev)
        return ev

    @staticmethod
    def cancel(ev):
        if ev is not None:
            ev[4] = True

    def _purge(self):
        while self._heap and self._heap[0][4]:
            heapq.heappop(self._heap)

    def peek_time(self):
        self._purge()
        return self._heap[0][0] if self._heap else None

    def step(self):
        """Executa o próximo evento. Retorna False se não houver mais nenhum."""
        self._purge()
        if not self._heap:
            return False
        t, _, fn, _, _ = heapq.heappop(self._heap)
        self.now = max(self.now, t)
        fn()
        return True

    def run_until(self, t):
        """Executa todos os eventos com tempo <= t e avança o relógio até t."""
        while True:
            nxt = self.peek_time()
            if nxt is None or nxt > t:
                break
            self.step()
        self.now = max(self.now, t)

    def run(self, until=None, max_events=500_000):
        n = 0
        while n < max_events:
            nxt = self.peek_time()
            if nxt is None or (until is not None and nxt > until):
                break
            self.step()
            n += 1
        if until is not None:
            self.now = max(self.now, until)
        return n


# =====================================================================
# 2. Barramento de eventos (EventBus)
# =====================================================================
class EventBus:
    """Barramento de eventos: log, replay e assinatura (usado pela GUI e pelos testes)."""

    def __init__(self, clock):
        self.clock = clock
        self.log = []
        self._subs = []

    def subscribe(self, fn):
        self._subs.append(fn)

    def emit(self, kind, **data):
        rec = {"kind": kind, "t": round(self.clock.now, 3), **data}
        self.log.append(rec)
        for s in list(self._subs):
            s(rec)
        return rec

    def select(self, kind=None, host=None, **match):
        out = []
        for r in self.log:
            if kind and r["kind"] != kind:
                continue
            if host and r.get("host") != host:
                continue
            if any(r.get(k) != v for k, v in match.items()):
                continue
            out.append(r)
        return out

    def replay(self, callback):
        for r in self.log:
            callback(r)

    def save_json(self, path):
        def conv(o):
            if isinstance(o, (bytes, bytearray)):
                return o.hex()
            if isinstance(o, (set, frozenset)):
                return sorted(o)
            return str(o)

        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.log, f, default=conv, ensure_ascii=False, indent=1)


# =====================================================================
# 3. Canal simulado (Channel)
# =====================================================================
@dataclass
class ChannelConfig:
    loss: float = 0.0             # probabilidade 0..1
    delay_min: float = 20.0       # ms
    delay_max: float = 20.0       # ms
    corrupt: float = 0.0          # probabilidade 0..1
    duplicate: float = 0.0        # probabilidade 0..1
    reorder: float = 0.0          # probabilidade 0..1
    reorder_extra: float = 120.0  # ms de atraso extra aplicado a segmentos reordenados


class _Rule:
    def __init__(self, action, pred, count, extra):
        self.action, self.pred, self.remaining, self.extra = action, pred, count, extra


class Channel:
    """Canal com atraso, perda, corrupção, duplicação e descarte determinístico."""

    def __init__(self, clock, bus, cfg, rng):
        self.clock, self.bus, self.cfg, self.rng = clock, bus, cfg, rng
        self.ends = {}
        self._rules = []
        self._manual = []           # ações manuais da GUI: "drop" / "corrupt"
        self._tx = 0
        self.stats = dict(sent=0, lost=0, corrupted=0, duplicated=0, reordered=0, delivered=0)

    def attach(self, name, handler):
        self.ends[name] = handler

    def drop_next(self, n=1):
        self._manual += ["drop"] * n

    def corrupt_next(self, n=1):
        self._manual += ["corrupt"] * n

    def add_rule(self, action, pred, count=1, extra=0.0):
        """action: drop | corrupt | duplicate | delay. pred(seg, src) -> bool."""
        self._rules.append(_Rule(action, pred, count, extra))

    def transmit(self, src, seg):
        dst = next(n for n in self.ends if n != src)
        c = self.cfg
        self._tx += 1
        seg.tx_id = self._tx
        self.stats["sent"] += 1
        # sorteios determinísticos pela seed
        u_loss, u_cor, u_dup, u_reo = (self.rng.random() for _ in range(4))
        delay = self.rng.uniform(c.delay_min, c.delay_max)
        extra_reo = self.rng.uniform(0.5, 1.0) * c.reorder_extra

        lost = u_loss < c.loss
        corrupt = u_cor < c.corrupt
        dup = u_dup < c.duplicate
        reorder = u_reo < c.reorder
        extra_delay = extra_reo if reorder else 0.0
        forced = None
        if self._manual:
            forced = self._manual.pop(0)
        else:
            for r in self._rules:
                if r.remaining > 0 and r.pred(seg, src):
                    r.remaining -= 1
                    forced = r.action
                    if r.action == "delay":
                        extra_delay, reorder = r.extra, True
                    break
        if forced == "drop":
            lost = True
        elif forced == "corrupt":
            corrupt = True
        elif forced == "duplicate":
            dup = True

        arrivals = []
        if lost:
            self.stats["lost"] += 1
        else:
            copy = seg.corrupted(self.rng) if corrupt else seg.copy()
            arrivals.append(delay + extra_delay)
            self.clock.schedule(delay + extra_delay, lambda: self._arrive(dst, copy), "arrive")
            if corrupt:
                self.stats["corrupted"] += 1
            if reorder:
                self.stats["reordered"] += 1
            if dup:
                d2 = delay + extra_delay + self.rng.uniform(5, 40)
                copy2 = copy.copy()
                arrivals.append(d2)
                self.stats["duplicated"] += 1
                self.clock.schedule(d2, lambda: self._arrive(dst, copy2), "arrive-dup")

        self.bus.emit("tx", host=src, dst=dst, seg=seg.copy(), label=seg.label(), tx_id=seg.tx_id,
                      lost=lost, corrupted=corrupt and not lost, duplicated=dup and not lost,
                      reordered=reorder and not lost, retx=seg.retx,
                      arrivals=[round(self.clock.now + a, 3) for a in arrivals])

    def _arrive(self, dst, seg):
        self.stats["delivered"] += 1
        self.ends[dst](seg)


# =====================================================================
# 4. Fachada Simulation e utilitários
# =====================================================================
class Simulation:
    """Fachada do núcleo: relógio virtual + canal + Host A (cliente) e Host B (servidor)."""

    def __init__(self, protocol="TCP", seed=1, channel=None, tcp_a=None, tcp_b=None, fsm_def=None):
        self.protocol = protocol
        self.seed = seed
        self.clock = Scheduler()
        self.bus = EventBus(self.clock)
        self.rng = random.Random(seed)                       # simulação determinística
        self.channel_cfg = channel or ChannelConfig()
        self.channel = Channel(self.clock, self.bus, self.channel_cfg, self.rng)
        if protocol == "UDP":
            self.A = UDPEndpoint("A", PORT_A, self, fsm_def)
            self.B = UDPEndpoint("B", PORT_B, self, fsm_def)
            self.A.peer_port, self.B.peer_port = PORT_B, PORT_A
        else:
            cfg_a = tcp_a or TCPConfig(isn=100)
            cfg_b = tcp_b or TCPConfig(isn=300)
            self.A = TCPEndpoint("A", PORT_A, PORT_B, self, cfg_a, fsm_def)
            self.B = TCPEndpoint("B", PORT_B, PORT_A, self, cfg_b, fsm_def)
        self.hosts = {"A": self.A, "B": self.B}

    @property
    def now(self):
        return self.clock.now

    def at(self, t, fn):
        """Agenda uma ação da aplicação no instante virtual t (ms)."""
        self.clock.schedule(max(0.0, t - self.clock.now), fn, "app")

    def step(self):
        return self.clock.step()

    def run(self, until=None):
        return self.clock.run(until)

    def run_until(self, t):
        self.clock.run_until(t)

    def next_time(self):
        return self.clock.peek_time()

    def state_path(self, host):
        """Sequência de estados visitados (ignora auto-laços)."""
        path = [self.hosts[host].engine.defn.initial]
        for r in self.bus.select("transition", host=host):
            if r["to"] != r["frm"]:
                path.append(r["to"])
        return path

    def transitions(self, host):
        return self.bus.select("transition", host=host)

    def sent(self, host, **flt):
        """Segmentos transmitidos por 'host' (eventos tx)."""
        out = []
        for r in self.bus.select("tx", host=host):
            s = r["seg"]
            if all(f in s.flags for f in flt.get("has", ())) and not any(f in s.flags for f in flt.get("hasnt", ())):
                out.append(r)
        return out


def make_payload(n_chunks, size=100):
    """Gera n_chunks blocos de 'size' bytes; os 4 primeiros bytes de cada bloco são o índice."""
    return b"".join(i.to_bytes(4, "big") + bytes([65 + i % 26]) * (size - 4) for i in range(n_chunks))
