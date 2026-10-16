"""Cenários de teste (10 obrigatórios + extras). Rodam sem GUI; a GUI reutiliza os mesmos 'build'."""
from dataclasses import dataclass
from typing import Callable

from .core import ChannelConfig, Simulation, make_payload
from .protocols import TCPConfig


MSS = 100


@dataclass
class Scenario:
    id: str
    protocol: str                      # "TCP", "UDP" ou "UDP vs TCP"
    name: str
    expected: str
    build: Callable                    # () -> [Simulation, ...] já com eventos agendados
    check: Callable                    # (sims) -> [(descrição, bool), ...]
    max_time: float = 120_000
    required: bool = True


def ck(desc, cond):
    return (desc, bool(cond))


def tcp_sim(seed=1, delay=50.0, tcp_a=None, tcp_b=None, **chan):
    ch = ChannelConfig(delay_min=delay, delay_max=delay, **chan)
    return Simulation("TCP", seed, ch, tcp_a, tcp_b)


def is_syn(seg, src):
    return src == "A" and "SYN" in seg.flags and "ACK" not in seg.flags


def is_synack(seg, src):
    return src == "B" and {"SYN", "ACK"} <= seg.flags


# ------------------------------------------------------------------ 1
def s1_build():
    s = tcp_sim()
    s.at(0, s.B.listen)
    s.at(10, s.A.connect)
    return [s]


def s1_check(sims):
    s = sims[0]
    syn = s.sent("A", has=["SYN"], hasnt=["ACK"])[0]["seg"]
    synack = s.sent("B", has=["SYN", "ACK"])[0]["seg"]
    return [
        ck("A: CLOSED→SYN_SENT→ESTABLISHED", s.state_path("A") == ["CLOSED", "SYN_SENT", "ESTABLISHED"]),
        ck("B: LISTEN→SYN_RCVD→ESTABLISHED", s.state_path("B") == ["CLOSED", "LISTEN", "SYN_RCVD", "ESTABLISHED"]),
        ck("SYN com SEQ=100", syn.seq == 100),
        ck("SYNACK com SEQ=300 e ACK=101", synack.seq == 300 and synack.ack == 101),
        ck("3º segmento: ACK=301", s.sent("A", has=["ACK"], hasnt=["SYN"])[0]["seg"].ack == 301),
    ]


# ------------------------------------------------------------------ 2
def s2_build():
    s = tcp_sim()
    s.channel.add_rule("drop", is_syn, 1)
    s.at(0, s.B.listen)
    s.at(10, s.A.connect)
    return [s]


def s2_check(sims):
    s = sims[0]
    syns = s.sent("A", has=["SYN"], hasnt=["ACK"])
    return [
        ck("o 1º SYN foi perdido pelo canal", syns[0]["lost"]),
        ck("SYN retransmitido (2 envios, o 2º marcado retx)", len(syns) == 2 and syns[1]["retx"]),
        ck("retransmissão após o timeout inicial (1000 ms)", abs((syns[1]["t"] - syns[0]["t"]) - 1000) < 1e-6),
        ck("evento 'timeout' disparado na FSM de A", len([t for t in s.transitions("A") if t["event"] == "timeout"]) == 1),
        ck("conexão estabelecida nos dois lados", s.A.state == "ESTABLISHED" and s.B.state == "ESTABLISHED"),
    ]


# ------------------------------------------------------------------ 3
def s3_build():
    s = tcp_sim()
    s.channel.add_rule("drop", is_synack, 1)
    s.at(0, s.B.listen)
    s.at(10, s.A.connect)
    return [s]


def s3_check(sims):
    s = sims[0]
    syns = s.sent("A", has=["SYN"], hasnt=["ACK"])
    synacks = s.sent("B", has=["SYN", "ACK"])
    return [
        ck("1º SYNACK perdido", synacks[0]["lost"]),
        ck("cliente retransmite o SYN", len(syns) == 2 and syns[1]["retx"]),
        ck("servidor reenvia o SYNACK ao receber o SYN duplicado", len(synacks) == 2),
        ck("B tratou SYN duplicado em SYN_RCVD",
           any(t["frm"] == "SYN_RCVD" and t["event"] == "rcv_SYN" for t in s.transitions("B"))),
        ck("A: CLOSED→SYN_SENT→ESTABLISHED", s.state_path("A") == ["CLOSED", "SYN_SENT", "ESTABLISHED"]),
        ck("B: LISTEN→SYN_RCVD→ESTABLISHED", s.state_path("B") == ["CLOSED", "LISTEN", "SYN_RCVD", "ESTABLISHED"]),
    ]


# ------------------------------------------------------------------ 4
def s4_build():
    s = tcp_sim()
    s.at(0, s.B.listen)
    s.at(10, s.A.connect)
    s.at(1000, s.A.close)
    s.at(1500, s.B.close)
    return [s]


def s4_check(sims):
    s = sims[0]
    return [
        ck("A: …ESTABLISHED→FIN_WAIT_1→FIN_WAIT_2→TIME_WAIT→CLOSED",
           s.state_path("A") == ["CLOSED", "SYN_SENT", "ESTABLISHED", "FIN_WAIT_1", "FIN_WAIT_2", "TIME_WAIT", "CLOSED"]),
        ck("B: …ESTABLISHED→CLOSE_WAIT→LAST_ACK→CLOSED",
           s.state_path("B") == ["CLOSED", "LISTEN", "SYN_RCVD", "ESTABLISHED", "CLOSE_WAIT", "LAST_ACK", "CLOSED"]),
        ck("TIME_WAIT durou 2×MSL (2000 ms)", _time_wait_duration(s, "A") == 2 * s.A.cfg.msl),
    ]


def _time_wait_duration(s, host):
    tr = s.transitions(host)
    t0 = next(t["t"] for t in tr if t["to"] == "TIME_WAIT")
    t1 = next(t["t"] for t in tr if t["frm"] == "TIME_WAIT" and t["to"] == "CLOSED")
    return t1 - t0


# ------------------------------------------------------------------ 5
def s5_build():
    s = tcp_sim()
    s.at(0, s.B.listen)
    s.at(10, s.A.connect)
    s.at(1000, s.A.close)
    s.at(1000, s.B.close)               # mesmo instante => FINs se cruzam
    return [s]


def s5_check(sims):
    s = sims[0]
    tail = ["ESTABLISHED", "FIN_WAIT_1", "CLOSING", "TIME_WAIT", "CLOSED"]
    return [
        ck("A: FIN_WAIT_1→CLOSING→TIME_WAIT", s.state_path("A")[-5:] == tail),
        ck("B: FIN_WAIT_1→CLOSING→TIME_WAIT", s.state_path("B")[-5:] == tail),
        ck("nenhum lado passou por CLOSE_WAIT/LAST_ACK",
           not ({"CLOSE_WAIT", "LAST_ACK"} & (set(s.state_path("A")) | set(s.state_path("B"))))),
    ]


# ------------------------------------------------------------------ 6
def s6_build(fast=True):
    cfg_a = TCPConfig(isn=100, fast_retransmit=fast)
    s = tcp_sim(tcp_a=cfg_a)
    target = 100 + 1 + 4 * MSS                       # 5º segmento de dados (no meio dos 10)
    s.channel.add_rule("drop", lambda seg, src: src == "A" and len(seg.payload) > 0 and seg.seq == target
                       and not seg.retx, 1)
    s.at(0, s.B.listen)
    s.at(10, s.A.connect)
    s.at(500, lambda: s.A.send(make_payload(10, MSS)))
    s.at(4000, s.A.close)
    s.at(4500, s.B.close)
    return [s]


def _delivery_done(s):
    return max(r["t"] for r in s.bus.select("deliver", host="B"))


def fast_retransmit_savings():
    """Executa o cenário 6 com e sem retransmissão rápida e compara."""
    sf = s6_build(True)[0]; sf.run()
    st = s6_build(False)[0]; st.run()
    r_fast = next(r for r in sf.A.retx_log if r["reason"] == "fast")
    r_to = next(r for r in st.A.retx_log if r["reason"] == "timeout")
    sent_t = next(r["t"] for r in sf.sent("A") if r["seg"].seq == r_fast["seq"] and r["lost"])
    rto = [e["duration"] for e in sf.bus.select("timer", host="A", name="retx", op="start")
           if e["t"] <= r_fast["t"]][-1]                      # RTO vigente quando a retransmissão rápida ocorreu
    return dict(t_envio_original=sent_t, t_retx_rapida=r_fast["t"], t_retx_timeout=r_to["t"],
                economia_na_retransmissao=r_to["t"] - r_fast["t"],
                conclusao_com_fast=_delivery_done(sf), conclusao_so_timeout=_delivery_done(st),
                economia_total=_delivery_done(st) - _delivery_done(sf),
                rto_no_momento=rto)


def s6_check(sims):
    s = sims[0]
    target = 100 + 1 + 4 * MSS
    fast = [r for r in s.A.retx_log if r["reason"] == "fast"]
    tout = [r for r in s.A.retx_log if r["reason"] == "timeout"]
    sv = fast_retransmit_savings()
    dups = [d for d in s.bus.select("dupack", host="A")]
    return [
        ck("B recebeu os 1000 bytes completos e em ordem", bytes(s.B.received) == make_payload(10, MSS)),
        ck("o segmento do meio foi perdido pelo canal",
           any(r["lost"] and r["seg"].seq == target for r in s.sent("A"))),
        ck("≥3 ACKs duplicados recebidos por A", len(dups) >= 3),
        ck("exatamente 1 retransmissão rápida do SEQ perdido", len(fast) == 1 and fast[0]["seq"] == target),
        ck("nenhuma retransmissão por timeout", len(tout) == 0),
        ck("retransmissão rápida ocorreu antes do timeout teria expirado", sv["t_retx_rapida"] < sv["t_retx_timeout"]),
        ck("transferência terminou mais cedo que só com timeout", sv["economia_total"] > 0),
    ]


# ------------------------------------------------------------------ 7
def s7_build():
    s = tcp_sim()                     # B permanece em CLOSED (ninguém chamou listen)
    s.at(10, s.A.connect)
    return [s]


def s7_check(sims):
    s = sims[0]
    rsts = s.sent("B", has=["RST"])
    return [
        ck("B respondeu com RST", len(rsts) == 1),
        ck("RST confirma o SYN (ACK=101)", rsts and rsts[0]["seg"].ack == 101),
        ck("B permaneceu em CLOSED", s.state_path("B") == ["CLOSED"]),
        ck("A: CLOSED→SYN_SENT→CLOSED (conexão recusada)", s.state_path("A") == ["CLOSED", "SYN_SENT", "CLOSED"]),
        ck("SYN não foi retransmitido", len(s.sent("A", has=["SYN"])) == 1),
    ]


# ------------------------------------------------------------------ 8
def udp_sim(seed=1, delay=50.0, **chan):
    return Simulation("UDP", seed, ChannelConfig(delay_min=delay, delay_max=delay, **chan))


def delivered_idx(s):
    return [int.from_bytes(r["data"][:4], "big") for r in s.bus.select("deliver", host="B")]


def s8_build():
    s = udp_sim(seed=3, loss=0.30)
    for i in range(20):
        s.at(i * 10, lambda i=i: s.A.send(make_payload(i + 1, 100)[-100:]))     # bloco i (índice nos 4 1ºs bytes)
    return [s]


def s8_check(sims):
    s = sims[0]
    got = delivered_idx(s)
    lost = 20 - len(set(got))
    sent_idx = [int.from_bytes(r["seg"].payload[:4], "big") for r in s.sent("A")]
    return [
        ck("20 datagramas enviados", len(sent_idx) == 20),
        ck("houve perdas (≈30%)", lost > 0),
        ck("entregues + perdidos = 20 (perdidos NÃO foram recuperados)", len(set(got)) + lost == 20 and len(got) < 20),
        ck("nenhuma retransmissão: cada datagrama enviado uma única vez", sorted(sent_idx) == list(range(20))),
        ck("receptor não enviou nada de volta", len(s.sent("B")) == 0),
    ]


# ------------------------------------------------------------------ 9
def s9_build():
    s = udp_sim()
    s.channel.add_rule("corrupt", lambda seg, src: int.from_bytes(seg.payload[:4], "big") == 1, 1)
    for i in range(3):
        s.at(i * 10, lambda i=i: s.A.send(make_payload(i + 1, 100)[-100:]))
    return [s]


def s9_check(sims):
    s = sims[0]
    drops = s.bus.select("drop", host="B")
    return [
        ck("datagrama corrompido foi descartado (checksum)", len(drops) == 1 and "checksum" in drops[0]["reason"]),
        ck("só 2 de 3 chegaram à aplicação", delivered_idx(s) == [0, 2]),
        ck("nenhuma notificação ao remetente", len(s.sent("B")) == 0),
        ck("o checksum do receptor realmente falhou", [r["ok"] for r in s.bus.select("rx", host="B")] == [True, False, True]),
    ]


# ------------------------------------------------------------------ 10
N10, SEED10 = 50, 1
CH10 = dict(loss=0.10, corrupt=0.03, duplicate=0.05, reorder=0.10)


def s10_build():
    return build_s10(SEED10)


def analyze(sim, n_sent):
    got = delivered_idx(sim)
    seen, dup, mx, inord, reord = set(), 0, -1, 0, 0
    for i in got:
        if i in seen:
            dup += 1
            continue
        seen.add(i)
        if i > mx:
            inord, mx = inord + 1, i
        else:
            reord += 1
    return dict(enviados=n_sent, entregues_a_aplicacao=len(got), unicos=len(seen), perdidos=n_sent - len(seen),
                duplicados=dup, em_ordem=inord, fora_de_ordem=reord,
                sequencia_perfeita=(got == list(range(n_sent))),
                tempo_total_ms=round(max((r["t"] for r in sim.bus.select("deliver", host="B")), default=0), 1),
                canal=dict(sim.channel.stats))


def compare_report(sims):
    u, t = sims
    ru, rt = analyze(u, N10), analyze(t, N10)
    rt["retransmissoes"] = dict(timeout=t.A.stats["retx_timeout"], rapidas=t.A.stats["retx_fast"])
    return dict(UDP=ru, TCP=rt)


def format_report(rep):
    rows = [("enviados", "enviados"), ("entregues à aplicação", "entregues_a_aplicacao"),
            ("únicos", "unicos"), ("perdidos", "perdidos"), ("duplicados", "duplicados"),
            ("em ordem", "em_ordem"), ("fora de ordem", "fora_de_ordem"),
            ("sequência perfeita", "sequencia_perfeita"), ("tempo total (ms)", "tempo_total_ms")]
    out = [f"{'métrica':<24}{'UDP':>10}{'TCP':>10}", "-" * 44]
    for lab, k in rows:
        out.append(f"{lab:<24}{str(rep['UDP'][k]):>10}{str(rep['TCP'][k]):>10}")
    out.append("-" * 44)
    for proto in ("UDP", "TCP"):
        out.append(f"canal ({proto}): {rep[proto]['canal']}")
    out.append(f"TCP retransmissões: {rep['TCP']['retransmissoes']}")
    return "\n".join(out)


def build_s10(seed):
    ch = lambda: ChannelConfig(delay_min=20, delay_max=60, **CH10)
    u = Simulation("UDP", seed, ch())
    for i in range(N10):
        u.at(i * 10, lambda i=i: u.A.send(make_payload(i + 1, MSS)[-MSS:]))
    t = Simulation("TCP", seed, ch())
    t.at(0, t.B.listen)
    t.at(10, t.A.connect)
    t.at(10, lambda: t.A.send(make_payload(N10, MSS)))
    return [u, t]


def compare_many_seeds(n=20):
    """Repete o cenário 10 com n seeds (1..n) e devolve médias — evita tirar conclusões de uma única seed."""
    import statistics as st
    rows = []
    for seed in range(1, n + 1):
        sims = build_s10(seed)
        for s in sims:
            s.run(until=300_000)
        rows.append(compare_report(sims))
    def agg(proto, key):
        vals = [r[proto][key] for r in rows]
        return dict(media=round(st.mean(vals), 2), mediana=st.median(vals), minimo=min(vals), maximo=max(vals))
    keys = ("perdidos", "duplicados", "fora_de_ordem", "tempo_total_ms")
    return dict(n=n, **{p: {k: agg(p, k) for k in keys} for p in ("UDP", "TCP")},
                tcp_entregou_tudo=sum(r["TCP"]["sequencia_perfeita"] for r in rows),
                udp_entregou_tudo=sum(r["UDP"]["perdidos"] == 0 for r in rows))


def s10_check(sims):
    rep = compare_report(sims)
    u, t = rep["UDP"], rep["TCP"]
    return [
        ck("mesma carga: 50 blocos de 100 bytes nos dois protocolos", u["enviados"] == t["enviados"] == N10),
        ck("TCP entregou tudo, sem lacunas", t["perdidos"] == 0 and t["unicos"] == N10),
        ck("TCP entregou na ordem exata, sem duplicatas", t["sequencia_perfeita"] and t["duplicados"] == 0),
        ck("UDP perdeu datagramas (sem recuperação)", u["perdidos"] > 0),
        ck("UDP deixou passar duplicatas e/ou reordenação", u["duplicados"] + u["fora_de_ordem"] > 0),
        ck("TCP gastou mais tempo que o UDP (custo da confiabilidade)", t["tempo_total_ms"] > u["tempo_total_ms"]),
    ]


# ------------------------------------------------------------------ extras
def e1_build():
    s = udp_sim()
    s.channel.add_rule("duplicate", lambda seg, src: int.from_bytes(seg.payload[:4], "big") == 1, 1)
    s.channel.add_rule("delay", lambda seg, src: int.from_bytes(seg.payload[:4], "big") == 2, 1, extra=250)
    for i in range(5):
        s.at(i * 20, lambda i=i: s.A.send(make_payload(i + 1, 100)[-100:]))
    return [s]


def e1_check(sims):
    got = delivered_idx(sims[0])
    return [ck("duplicata (1 aparece 2x) chega à aplicação", got.count(1) == 2),
            ck("reordenação (2 chega depois de 3 e 4) chega à aplicação sem correção",
               got.index(2) > got.index(4)),
            ck("nada foi corrigido: sequência entregue = " + str(got), got != sorted(set(got)))]


def _lost_last_ack_build(time_wait):
    s = tcp_sim(tcp_a=TCPConfig(isn=100, time_wait=time_wait))
    s.channel.add_rule("drop", lambda seg, src: src == "A" and s.now > 1500 and seg.flags == {"ACK"}, 1)
    s.at(0, s.B.listen)
    s.at(10, s.A.connect)
    s.at(1000, s.A.close)
    s.at(1500, s.B.close)
    return [s]


def e2_check(sims):
    s = sims[0]
    return [ck("sem TIME_WAIT: A vai direto a CLOSED", s.state_path("A")[-2:] == ["FIN_WAIT_2", "CLOSED"]),
            ck("ACK final perdido → B retransmite FIN", any(r["reason"] == "timeout" for r in s.B.retx_log)),
            ck("A (já CLOSED) responde RST ao FIN retransmitido", len(s.sent("A", has=["RST"])) == 1),
            ck("B encerra de forma anormal (rcv_RST)", any(t["event"] == "rcv_RST" for t in s.transitions("B")))]


def e3_check(sims):
    s = sims[0]
    return [ck("com TIME_WAIT: A passa por TIME_WAIT", "TIME_WAIT" in s.state_path("A")),
            ck("A reenvia o ACK ao receber o FIN retransmitido", len(s.sent("A", has=["ACK"], hasnt=["SYN", "FIN"])) >= 2),
            ck("nenhum RST", len(s.sent("A", has=["RST"])) == 0),
            ck("B termina de forma limpa (LAST_ACK→CLOSED por ACK)",
               any(t["frm"] == "LAST_ACK" and t["event"] == "rcv_ACK" and t["to"] == "CLOSED" for t in s.transitions("B")))]


SCENARIOS = [
    Scenario("1", "TCP", "Handshake sem falhas", "A: CLOSED→SYN_SENT→ESTABLISHED; B: LISTEN→SYN_RCVD→ESTABLISHED", s1_build, s1_check),
    Scenario("2", "TCP", "Perda do SYN", "Timeout e retransmissão do SYN", s2_build, s2_check),
    Scenario("3", "TCP", "Perda do SYN-ACK", "Cliente retransmite o SYN; servidor reenvia o SYN-ACK", s3_build, s3_check),
    Scenario("4", "TCP", "Encerramento normal", "A: FIN_WAIT_1, FIN_WAIT_2, TIME_WAIT; B: CLOSE_WAIT, LAST_ACK", s4_build, s4_check),
    Scenario("5", "TCP", "Fechamento simultâneo", "Ambos: FIN_WAIT_1→CLOSING→TIME_WAIT", s5_build, s5_check),
    Scenario("6", "TCP", "Perda de 1 segmento de dados (de 10)", "3 ACKs duplicados e retransmissão rápida, sem esperar o timeout", s6_build, s6_check),
    Scenario("7", "TCP", "Conexão a uma porta fechada", "Resposta RST", s7_build, s7_check),
    Scenario("8", "UDP", "20 datagramas com 30% de perda", "Perdidos não são recuperados; nenhuma retransmissão", s8_build, s8_check),
    Scenario("9", "UDP", "Datagrama com checksum corrompido", "Descartado pelo receptor, sem notificação", s9_build, s9_check),
    Scenario("10", "UDP vs TCP", "Mesma carga, mesmo canal ruidoso", "Relatório: entregues, perdidos, duplicados, em ordem, tempo total", s10_build, s10_check),
    Scenario("E1", "UDP", "[extra] Duplicação e reordenação no UDP", "Chegam à aplicação sem correção", e1_build, e1_check, required=False),
    Scenario("E2", "TCP", "[extra] SEM TIME_WAIT + último ACK perdido", "B recebe RST (encerramento anormal)",
             lambda: _lost_last_ack_build(False), e2_check, required=False),
    Scenario("E3", "TCP", "[extra] COM TIME_WAIT + último ACK perdido", "A reenvia ACK; encerramento limpo",
             lambda: _lost_last_ack_build(True), e3_check, required=False),
]


def get(sid):
    return next(s for s in SCENARIOS if s.id == str(sid))


def run_scenario(sc):
    sims = sc.build()
    for s in sims:
        s.run(until=sc.max_time)
    checks = sc.check(sims)
    return dict(scenario=sc, sims=sims, checks=checks, passed=all(ok for _, ok in checks))
