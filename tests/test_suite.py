"""Suíte de testes completa (Unitários + 10 Cenários do Trabalho).
Compatível tanto com o unittest padrão do Python (sem instalar nada) quanto com pytest:
    python -m unittest discover tests -v
    python run_tests.py
    pytest tests -v
"""
import math
import os
import random
import sys
import time
import unittest

ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, ROOT)

from simulador import (  # noqa: E402
    ChannelConfig,
    EventBus,
    FSMDefinition,
    FSMDefinitionError,
    FSMEngine,
    RTTEstimator,
    Scheduler,
    Segment,
    Simulation,
    TCPConfig,
    internet_checksum,
    make_payload,
    ones_complement_sum16,
    verify,
)
from simulador import scenarios as S  # noqa: E402


# =====================================================================
# 1. Testes de Checksum e Segmento
# =====================================================================
class TestChecksumAndSegment(unittest.TestCase):
    def test_checksum_exemplo_do_livro(self):
        """Exemplo clássico da seção 3.3 do livro Kurose & Ross."""
        data = bytes.fromhex("6666" "5555" "0F0F")
        self.assertEqual(ones_complement_sum16(data), 0xCACA)
        self.assertEqual(internet_checksum(data), 0x3535)
        self.assertTrue(verify(data + (0x3535).to_bytes(2, "big")))

    def test_checksum_vai_um_circular(self):
        self.assertEqual(ones_complement_sum16(bytes.fromhex("FFFF0001")), 0x0001)

    def test_qualquer_bit_invertido_e_detectado(self):
        seg = Segment("UDP", 1, 2, payload=make_payload(1, 100)).seal()
        self.assertTrue(seg.verify())
        rng = random.Random(0)
        for _ in range(300):
            self.assertFalse(seg.corrupted(rng).verify())


# =====================================================================
# 2. Testes do Motor de FSM Genérico
# =====================================================================
class TestFSMEngine(unittest.TestCase):
    def _engine(self, path, bus=None):
        clock = Scheduler()
        bus = bus or EventBus(clock)
        return FSMEngine(FSMDefinition.from_file(os.path.join(ROOT, "fsm_defs", path)), "X", bus), bus

    def test_motor_rejeita_evento_invalido_e_registra_motivo(self):
        sim = Simulation("TCP")
        self.assertTrue(sim.B.engine.fire("rcv_DATA", {"seg": Segment("TCP", 1, 2)}))
        self.assertEqual(len(sim.sent("B", has=["RST"])), 1)
        sim.B.listen()
        ok = sim.B.engine.fire("close_inexistente")
        self.assertFalse(ok)
        rej = sim.bus.select("reject", host="B")
        self.assertTrue(rej and "inválido no estado LISTEN" in rej[-1]["reason"])

    def test_nova_fsm_somente_editando_json(self):
        """rdt 1.0 roda no mesmo motor genérico sem nenhuma linha a mais no motor."""
        snd, bus = self._engine("rdt10_sender.json")
        rcv, _ = self._engine("rdt10_receiver.json", bus)
        wire, out = [], []
        snd.register_action("make_pkt", lambda a, c: c.update(pkt=("pkt", c["data"])))
        snd.register_action("udt_send", lambda a, c: wire.append(c["pkt"]))
        rcv.register_action("extract", lambda a, c: c.update(data=c["pkt"][1]))
        rcv.register_action("deliver_data", lambda a, c: out.append(c["data"]))
        snd.validate()
        rcv.validate()
        for d in ("a", "b", "c"):
            snd.fire("rdt_send", {"data": d})
        for p in wire:
            rcv.fire("rdt_rcv", {"pkt": p})
        self.assertEqual(out, ["a", "b", "c"])

    def test_definicao_com_primitiva_ausente_falha_cedo(self):
        eng, _ = self._engine("rdt10_sender.json")
        with self.assertRaises(FSMDefinitionError):
            eng.validate()

    def test_guarda_sem_correspondencia_gera_reject(self):
        sim = Simulation("TCP")
        sim.A.engine.fire("active_open")
        bad = Segment("TCP", 80, 49152, seq=300, ack=999, flags=frozenset({"SYN", "ACK"}))
        sim.A.engine.fire("rcv_SYNACK", {"seg": bad})
        self.assertIn("nenhuma condição satisfeita", sim.bus.select("reject", host="A")[-1]["reason"])


# =====================================================================
# 3. Testes de RTT, Determinismo e Relógio Virtual
# =====================================================================
class TestRTTAndClock(unittest.TestCase):
    def test_estimativa_de_rtt(self):
        r = RTTEstimator(min_rto=0)
        self.assertEqual(r.timeout, 1000)
        r.sample(100)
        self.assertEqual((r.estimated, r.dev, r.timeout), (100, 50, 300))
        r.sample(200)
        self.assertTrue(math.isclose(r.dev, 62.5, abs_tol=1e-5))
        self.assertTrue(math.isclose(r.estimated, 112.5, abs_tol=1e-5))
        self.assertTrue(math.isclose(r.timeout, 112.5 + 4 * 62.5, abs_tol=1e-5))
        r.backoff()
        self.assertTrue(math.isclose(r.timeout, 2 * (112.5 + 250), abs_tol=1e-5))

    def _sig(self, seed):
        sims = S.build_s10(seed)
        for s in sims:
            s.run()
        return [(r["kind"], r["t"], r.get("label")) for s in sims for r in s.bus.log if r["kind"] in ("tx", "deliver")]

    def test_mesma_seed_reproduz_exatamente(self):
        self.assertEqual(self._sig(5), self._sig(5))
        self.assertNotEqual(self._sig(5), self._sig(6))

    def test_relogio_virtual_e_rapido(self):
        t0 = time.time()
        s = Simulation("TCP", 1, ChannelConfig(delay_min=50, delay_max=50), TCPConfig(msl=30000), TCPConfig(msl=30000))
        s.at(0, s.B.listen)
        s.at(1, s.A.connect)
        s.at(300, s.A.close)
        s.at(600, s.B.close)
        s.run()
        self.assertGreaterEqual(s.now, 60000)      # 2xMSL = 60s virtuais
        self.assertLess(time.time() - t0, 2)       # executados em frações de segundo reais


# =====================================================================
# 4. Testes de Estados TCP e Confiabilidade
# =====================================================================
class TestTCPProtocol(unittest.TestCase):
    def test_todos_os_11_estados_sao_visitados(self):
        visited = set()
        for sid in ("1", "4", "5", "7"):
            for s in S.run_scenario(S.get(sid))["sims"]:
                for h in "AB":
                    visited |= set(s.state_path(h))
        self.assertEqual(
            visited,
            {
                "CLOSED",
                "LISTEN",
                "SYN_SENT",
                "SYN_RCVD",
                "ESTABLISHED",
                "FIN_WAIT_1",
                "FIN_WAIT_2",
                "CLOSING",
                "TIME_WAIT",
                "CLOSE_WAIT",
                "LAST_ACK",
            },
        )

    def test_dados_com_perda_aleatoria_sempre_chegam_completos(self):
        for seed in range(1, 6):
            s = Simulation("TCP", seed, ChannelConfig(delay_min=20, delay_max=60, loss=0.15, corrupt=0.05,
                                                      duplicate=0.05, reorder=0.1))
            s.at(0, s.B.listen)
            s.at(5, s.A.connect)
            s.at(5, lambda: s.A.send(make_payload(20, 100)))
            s.run(until=400_000)
            self.assertEqual(bytes(s.B.received), make_payload(20, 100))

    def test_controle_de_congestionamento_opcional(self):
        cfg = TCPConfig(isn=100, congestion_control=True)
        s = Simulation("TCP", 2, ChannelConfig(delay_min=30, delay_max=30), cfg, TCPConfig(isn=300))
        s.at(0, s.B.listen)
        s.at(5, s.A.connect)
        s.at(5, lambda: s.A.send(make_payload(40, 100)))
        s.run()
        cw = [r["cwnd"] for r in s.bus.select("cwnd", host="A")]
        self.assertEqual(bytes(s.B.received), make_payload(40, 100))
        self.assertTrue(cw and max(cw) > 100 * cfg.mss // 100 * 3)


# =====================================================================
# 5. Validação dos 10 Cenários Mínimos do Trabalho + Extras
# =====================================================================
class TestCenariosTrabalho(unittest.TestCase):
    def test_todos_os_cenarios_passam(self):
        for sc in S.SCENARIOS:
            with self.subTest(cenario=sc.id, nome=sc.name):
                res = S.run_scenario(sc)
                falhas = [d for d, ok in res["checks"] if not ok]
                self.assertFalse(falhas, f"Cenário {sc.id} ({sc.name}) reprovado nas verificações: {falhas}")


if __name__ == "__main__":
    unittest.main()
