#!/usr/bin/env python3
"""Executa os cenários SEM GUI e imprime aprovado/reprovado por cenário.
Uso:  python run_tests.py            (relatório no terminal + relatorio_testes.txt)
      python run_tests.py --pytest   (roda a suíte pytest completa)
      python -m unittest discover tests (roda via unittest nativo)"""
import json
import os
import sys

# Garante compatibilidade UTF-8 no terminal Windows (evita UnicodeEncodeError com setas e checks)
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from simulador import scenarios as S  # noqa: E402


def main():
    if "--pytest" in sys.argv:
        try:
            import pytest
            sys.exit(pytest.main([os.path.join(HERE, "tests"), "-v"]))
        except ImportError:
            print("Pytest não instalado. Executando suíte via unittest padrão...")
            import unittest
            suite = unittest.defaultTestLoader.discover(os.path.join(HERE, "tests"))
            runner = unittest.TextTestRunner(verbosity=2)
            res = runner.run(suite)
            sys.exit(0 if res.wasSuccessful() else 1)

    lines, ok_all = [], True
    lines.append("RELATÓRIO DE TESTES AUTOMATIZADOS — Máquina de Estado")
    lines.append("=" * 72)
    for sc in S.SCENARIOS:
        res = S.run_scenario(sc)
        ok_all &= res["passed"] or not sc.required
        lines.append(f"\n[{'APROVADO' if res['passed'] else 'REPROVADO'}] Cenário {sc.id} — {sc.protocol} — {sc.name}")
        lines.append(f"    esperado: {sc.expected}")
        for d, ok in res["checks"]:
            lines.append(f"      {'✔' if ok else '✘'} {d}")
        if sc.id == "6":
            lines.append("    economia da retransmissão rápida: " + json.dumps(S.fast_retransmit_savings()))
        if sc.id == "10":
            lines.append("\n" + S.format_report(S.compare_report(res["sims"])))
            lines.append(f"\n    (repetido com 20 seeds) {json.dumps(S.compare_many_seeds(20), ensure_ascii=False)}")
    lines.append("\n" + "=" * 72)
    req = [s for s in S.SCENARIOS if s.required]
    lines.append(f"Cenários obrigatórios: {len(req)} | resultado geral: {'APROVADO' if ok_all else 'REPROVADO'}")
    txt = "\n".join(lines)
    print(txt)
    with open(os.path.join(HERE, "relatorio_testes.txt"), "w", encoding="utf-8") as f:
        f.write(txt + "\n")
    sys.exit(0 if ok_all else 1)


if __name__ == "__main__":
    main()
