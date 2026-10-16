# Relatório Prático 2 — Máquina de Estado
**Universidade de Fortaleza — UNIFOR**  
**Projeto de Redes Convergentes | Atividade Avaliativa 2 (Trabalho Prático 2)**  
**Disciplina:** Projeto de Redes Convergentes | **Professora:** Ana Maria  
**Aluno:** Anderson Viana - 2410492 | **Vídeo Demonstrativo (Item 7.5):** [https://youtu.be/ycelCxeP2pM](https://youtu.be/ycelCxeP2pM)  

---

## 1. Introdução e Objetivo
O capítulo 3 do livro *Redes de Computadores e a Internet* (Kurose & Ross) modela o transporte confiável com Máquinas de Estados Finitos (FSMs). Este trabalho implementa um simulador com interface gráfica (Tkinter) e núcleo desacoplado, dirigido por definições em JSON, capaz de executar, visualizar e validar formalmente as FSMs de UDP e TCP sobre um canal com falhas configuráveis.

---

## 2. Arquitetura da Solução Desenvolvida
A solução segue estritamente o padrão **MVC (Model-View-Controller)**:
- **Núcleo em Memória (`simulador/core.py` e `protocols.py`):** Relógio virtual (`Scheduler`), barramento de eventos (`EventBus`), canal com falhas determinísticas (`Channel`), estruturas de segmentos com serialização binária (`struct.pack`), checksum da Internet de 16 bits e endpoints TCP/UDP.
- **Motor de FSM Orientado a Dados (`simulador/fsm.py`):** Lê os arquivos JSON da pasta `fsm_defs/` com tuplas `(estado, evento[condição]) -> (ação, novo_estado)`. Rejeita qualquer evento inválido e registra o motivo.
- **Interface Gráfica (`gui/`):** Diagrama de estados interativo com animação de pulsos na última transição, diagrama de sequência com setas rotuladas e régua temporal não-linear, controles de passo e sliders do canal.
- **Suíte de Testes Automatizados (`run_tests.py` e `tests/test_suite.py`):** Validação estrita sem interface gráfica dos 10 cenários obrigatórios e 3 extras.

![Arquitetura da Solução](relatorio_imagens/arquitetura_solucao.png)

---

## 3. Validação Visual na GUI: Cenários de Conexão

### 3.1 Cenário 3: Perda do SYN-ACK
- Host A envia `SYN (SEQ=100)`.
- Host B responde `SYN+ACK (SEQ=300, ACK=101)`, que é descartado pelo canal.
- Ao estourar o timeout em $t=1010$ ms, A retransmite o SYN.
- B, em `SYN_RCVD`, trata o SYN duplicado reenviando o SYN-ACK.
- A conexão é estabelecida nos dois lados (`ESTABLISHED`).

![Cenário 3 - Perda do SYN-ACK](relatorio_imagens/cenario_3_perda_synack.png)

### 3.2 Cenário 5: Fechamento Simultâneo (CLOSING)
- Ambas as pontas chamam `close()` em $t=1000$ ms.
- Os FINs se cruzam na rede.
- Ao receber o FIN antes do ACK do seu próprio FIN, ambos transitam de `FIN_WAIT_1` para `CLOSING` e respondem com ACK.
- Ao receberem os ACKs cruzados, ambos transitam para `TIME_WAIT` ($2 \times MSL = 2000$ ms) e encerram em `CLOSED`.

![Cenário 5 - Fechamento Simultâneo](relatorio_imagens/cenario_5_fechamento_simultaneo.png)

---

## 4. Transferência Confiável: Cenário 6 e Fast Retransmit
No Cenário 6, A envia 10 segmentos de 100 bytes em $t=500$ ms. O 5º segmento (`SEQ=501`) é descartado pelo canal.
- Os segmentos subsequentes geram 3 ACKs duplicados (`ACK=501`).
- Em $t=600$ ms, ao receber o 3º ACK duplicado, A dispara imediatamente a **Retransmissão Rápida** do `SEQ=501`.
- A retransmissão ocorre em $t=600$ ms (vs $850$ ms se dependesse apenas de timeout).
- **Economia comprovada:** 250 ms (recuperação 71,4% mais rápida).

![Cenário 6 - Retransmissão Rápida](relatorio_imagens/cenario_6_retransmissao_rapida.png)

---

## 5. Análise Comparativa: UDP × TCP (Cenário 10)
Carga de 50 blocos sob canal com 10% de perda, 20-60 ms de atraso, 3% corrupção, 5% duplicação e 10% reordenação (repetido com 20 seeds):

| Métrica | UDP (Média 20 Seeds) | TCP (Média 20 Seeds) |
|---|---|---|
| **Blocos Entregues Íntegros** | 43,4 (86,8%) | **50,0 (100%)** |
| **Pacotes Perdidos** | 6,55 | **0** |
| **Pacotes Duplicados Entregues** | 1,95 | **0** |
| **Pacotes Fora de Ordem** | 15,05 | **0** |
| **Sequência Perfeita** | 0 de 20 | **20 de 20** |
| **Tempo Total Médio** | **555,8 ms** | 4.010,2 ms |

![Cenário 10 - UDP vs TCP](relatorio_imagens/cenario_10_udp_vs_tcp.png)

---

## 6. Respostas Formais às Perguntas do Relatório (Seção 9)
1. **TIME_WAIT e Cenário 4:** Garante entrega confiável do último ACK (se perdido, B retransmite FIN e A responde) e drena segmentos duplicados antigos da rede. Sem TIME_WAIT, B recebe RST no reenvio do FIN e encerra com erro (Cenários E2 vs E3).
2. **UDP e FSM:** UDP não tem estado compartilhado (sem SEQ, ACK, timers). Sua FSM tem 1 único estado (ATIVO). Recuperação de falhas, ordenação e integridade sobram para a aplicação.
3. **Economia no Cenário 6:** 250 ms economizados (retransmissão aos 600 ms vs 850 ms por timeout), recuperação em 1 RTT em vez de 1 RTT + RTO.
4. **Transições difíceis:** Fechamento simultâneo (necessidade da guarda `acks_fin`), reenvio de SYN-ACK em `SYN_RCVD` (omitido no livro) e eventos implícitos de timeout/RST.

---

## 7. Quadro dos 10 Cenários Mínimos
Todos os 10 cenários obrigatórios e 3 extras foram formalmente validados e aprovados por asserções em código (`run_tests.py` e `tests/test_suite.py`).
