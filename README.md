# Simulador de Máquina de Estado — Projeto de Redes Convergentes (Unidade 2, Trabalho 2)

Aplicação em **Python + Tkinter** que executa, visualiza e testa as máquinas de estado finito do UDP e do
TCP sobre um canal com falhas configuráveis, verificando automaticamente se cada FSM se comporta como o livro prevê.

## Instalação e execução
```bash
# Python 3.9+ (testado em 3.12). Tkinter vem com o Python no Windows/macOS.
# Linux (Debian/Ubuntu):  sudo apt install python3-tk
pip install -r requirements.txt

python main.py              # GUI (Tkinter)
python run_tests.py         # 10 cenários + extras SEM GUI -> aprovado/reprovado (grava relatorio_testes.txt)
python -m unittest discover tests  # suíte completa de testes unitários + cenários (sem dependências externas)
python run_tests.py --pytest   # (ou: pytest tests -v) suíte via pytest (opcional)
```

## Arquitetura (núcleo independente da GUI)
```
fsm_defs/   tcp_connection.json  udp.json  rdt10_sender.json  rdt10_receiver.json   <- FSMs como DADOS
simulador/  (NÚCLEO, 100% desacoplado de interface gráfica)
  fsm.py        Motor genérico de FSM: (estado, evento[condição]) -> (ação, novo estado)
  core.py       Infraestrutura: Relógio virtual (Scheduler), Barramento (EventBus), Canal e Fachada Simulation
  protocols.py  Protocolos: Checksum (16 bits), RTT/DevRTT, Segmentos, UDPEndpoint e TCPEndpoint (11 estados)
  scenarios.py  Os 10 cenários obrigatórios do trabalho + 3 extras, com validação por asserções
gui/        (Interface visual em Tkinter)
  app.py          Janela principal, controles, painel do canal e tabela de log filtrável
  statediagram.py Diagrama de estados: Grafo da FSM com realce e animação de transição
  seqdiagram.py   Diagrama de sequência: Linhas de tempo de A e B, setas rotuladas e régua dinâmica
tests/      
  test_suite.py   Suíte completa (testes unitários de checksum, RTT, motor e validação dos cenários)
```
Fluxo: `GUI -> Simulation -> (Host A <-> Canal <-> Host B)`; tudo que acontece é publicado no barramento de eventos,
que alimenta a GUI, o log e os testes.

## Como adicionar uma nova FSM (ex.: rdt 2.1) só editando JSON
Cada transição do arquivo é uma seta do livro:
```json
{"from": "ESTADO", "event": "rcv_ACK", "guard": "acks_fin & !time_wait_off",
 "action": "texto exibido (ação abaixo da seta)", "do": ["send:ACK", "start_timer:2msl"], "to": "OUTRO", "main": true}
```
`from`/`event` aceitam lista ou `"*"`; `"to": "SAME"` = auto-laço; `guard` aceita `!` e `&`; `layout` define a posição dos
estados no diagrama; `main` marca as setas desenhadas. Guardas e primitivas (`do`) são registradas pelo host
(`engine.register_guard / register_action`) — o motor não conhece nenhum protocolo. `tests/test_suite.py`
valida o rdt 1.0 rodando no mesmo motor genérico, e `engine.validate()` falha cedo se o JSON citar guarda/ação inexistente.

## Mapa: requisito → onde está
| Critério | Onde |
|---|---|
| FSM de conexão TCP completa (11 estados, 3 vias, 4 vias, CLOSING, TIME_WAIT 2×MSL, RST) | `fsm_defs/tcp_connection.json`, `simulador/protocols.py` |
| Transferência confiável (timer, RTT, retransmissão rápida) | `protocols.py`, cenário 6 em `scenarios.py` |
| UDP e checksum de 16 bits | `protocols.py`, cenários 8, 9, E1 |
| Canal e determinismo | `core.py` (seed, perdas, atrasos, relógio virtual) |
| Interface gráfica | `gui/` (`app.py`, `statediagram.py`, `seqdiagram.py`) |
| Testes (10 cenários) | `simulador/scenarios.py`, `tests/test_suite.py`, `run_tests.py` |

## Usando a GUI
* **Protocolo** (TCP / UDP / Comparar lado a lado) e **Cenário** (os 10 + extras, ou modo livre). Escolher um cenário carrega
  canal e ações da aplicação prontos; use **Iniciar**, **Passo** (um evento por vez), **Pausar**, **Velocidade** e **Reset**.
* **Canal**: sliders de perda, atraso mín/máx, corrupção, duplicação, reordenação (valem ao vivo); seed (vale no Reset);
  **Descartar/Corromper próximo segmento** (testes determinísticos com um clique).
* **Modo livre (TCP)**: `B: listen` → `A: connect` → `A → B enviar` → `A: close` / `B: close`. `A: close` e `B: close` no mesmo
  instante (clique em sequência com a simulação pausada e depois Passo) produzem o fechamento simultâneo; o cenário 5 faz isso automaticamente.
* **Diagrama de estados**: A em azul, B em verde, seta da última transição destacada (pulsa); **Diagrama de sequência**: setas rotuladas,
  perdido = vermelho com ✕, retransmitido = laranja, corrompido = roxo, duplicata = tracejado; régua em ms com lacunas comprimidas.
* **Log** filtrável (host, texto, só rejeições/descartes) com tempo, host, estado anterior, evento, ação e novo estado.
* **Rodar testes** executa todos os cenários dentro da GUI; **📷 PNG** salva captura da janela (use o print do sistema se falhar).

## Decisões de projeto / simplificações (documente no relatório)
* Tempo em **ms virtuais**; RTO inicial 1000 ms, piso prático de 200 ms, backoff dobrando a cada timeout, amostras de RTT com regra de Karn.
* TIME_WAIT = 2×MSL com MSL configurável (padrão 1000 ms → 2 s; acelerável).
* O SYN-ACK não tem timer próprio: é reenviado quando chega SYN duplicado (cenário 3); SYN, FIN e dados usam o timer de retransmissão.
* ACK imediato (sem ACK atrasado); receptor guarda segmentos fora de ordem; FIN fora de ordem é descartado (o remetente retransmite).
* Sem persist timer: janela zero é tratada enviando um segmento de sondagem quando nada está em voo.
