"""Motor de FSM genérico dirigido por dados (JSON).

Cada transição é a tupla  (estado, evento[condição]) -> (ação, novo estado),
como as setas do livro: evento[condição] acima da linha, ação abaixo.

Formato de uma transição no JSON:
  {"from": "ESTADO" | ["E1","E2"] | "*",
   "event": "evento" | ["e1","e2"],
   "guard": "cond" | "!cond" | "a & !b" | null,       # condição (opcional)
   "action": "texto exibido (ação abaixo da linha)",
   "do": ["primitiva", "primitiva:arg"],              # o que o motor executa
   "to": "NOVO_ESTADO" | "SAME",                      # SAME = permanece no estado
   "main": true}                                      # desenhar no diagrama

Guardas e primitivas são registradas pelo "dono" da FSM (host) com
register_guard() / register_action(); o motor não conhece nenhum protocolo.
"""
import json
from dataclasses import dataclass


class FSMDefinitionError(Exception):
    pass


@dataclass(frozen=True)
class Transition:
    src: str
    event: str
    guard: str
    action: str
    do: tuple
    dst: str
    main: bool = False

    def label(self):
        g = f"[{self.guard}]" if self.guard else ""
        return f"{self.event}{g} / {self.action or '-'}"


class FSMDefinition:
    def __init__(self, data):
        self.name = data["name"]
        self.description = data.get("description", "")
        self.initial = data["initial"]
        self.states = list(data["states"])
        self.layout = {k: tuple(v) for k, v in data.get("layout", {}).items()}
        self.transitions = []
        self._index = {}
        for raw in data["transitions"]:
            srcs = self.states if raw["from"] == "*" else (
                raw["from"] if isinstance(raw["from"], list) else [raw["from"]])
            evs = raw["event"] if isinstance(raw["event"], list) else [raw["event"]]
            same = raw["to"] in ("SAME", "=")          # auto-laço: permanece no estado de origem
            if not same and raw["to"] not in self.states:
                raise FSMDefinitionError(f"estado destino desconhecido: {raw['to']}")
            for s in srcs:
                if s not in self.states:
                    raise FSMDefinitionError(f"estado de origem desconhecido: {s}")
                for e in evs:
                    t = Transition(s, e, raw.get("guard") or "", raw.get("action", ""),
                                   tuple(raw.get("do", [])), s if same else raw["to"],
                                   bool(raw.get("main", False)))
                    self.transitions.append(t)
                    self._index.setdefault((s, e), []).append(t)
        if self.initial not in self.states:
            raise FSMDefinitionError("estado inicial desconhecido")

    @classmethod
    def from_file(cls, path):
        with open(path, encoding="utf-8") as f:
            return cls(json.load(f))

    def lookup(self, state, event):
        return self._index.get((state, event), [])

    def events_for(self, state):
        return sorted({e for (s, e) in self._index if s == state})

    def guard_names(self):
        names = set()
        for t in self.transitions:
            for part in t.guard.split("&"):
                part = part.strip().lstrip("!")
                if part:
                    names.add(part)
        return names

    def action_names(self):
        return {d.split(":", 1)[0] for t in self.transitions for d in t.do}


class FSMEngine:
    def __init__(self, definition, host_name, bus, proto=None):
        self.defn = definition
        self.host_name = host_name
        self.bus = bus
        self.proto = proto or definition.name
        self.state = definition.initial
        self.guards = {}
        self.actions = {}
        self.after_hook = None          # chamado após cada transição: hook(prev, new, transition, ctx)
        self.last = None

    def register_guard(self, name, fn):
        self.guards[name] = fn

    def register_action(self, name, fn):
        self.actions[name] = fn

    def validate(self):
        """Falha cedo se a definição referencia guardas/ações que o host não implementa."""
        miss_g = self.defn.guard_names() - set(self.guards)
        miss_a = self.defn.action_names() - set(self.actions)
        if miss_g or miss_a:
            raise FSMDefinitionError(f"guardas ausentes: {sorted(miss_g)}; ações ausentes: {sorted(miss_a)}")

    def _guard_ok(self, expr, ctx):
        if not expr:
            return True
        for part in expr.split("&"):
            part = part.strip()
            neg = part.startswith("!")
            val = bool(self.guards[part.lstrip("!")](ctx))
            if val == neg:
                return False
        return True

    def reset(self):
        self.state = self.defn.initial

    def fire(self, event, ctx=None):
        """Dispara um evento. Retorna True se alguma transição ocorreu."""
        cands = self.defn.lookup(self.state, event)
        if not cands:
            self.bus.emit("reject", host=self.host_name, proto=self.proto, state=self.state, event=event,
                          reason=f"evento '{event}' inválido no estado {self.state}")
            return False
        chosen = next((t for t in cands if self._guard_ok(t.guard, ctx)), None)
        if chosen is None:
            self.bus.emit("reject", host=self.host_name, proto=self.proto, state=self.state, event=event,
                          reason=f"nenhuma condição satisfeita em {self.state}/{event}: "
                                 + ", ".join(t.guard for t in cands))
            return False
        prev = self.state
        for d in chosen.do:                                  # ação (abaixo da seta)
            name, _, arg = d.partition(":")
            self.actions[name](arg, ctx)
        self.state = chosen.dst                              # novo estado
        self.last = chosen
        self.bus.emit("transition", host=self.host_name, proto=self.proto, frm=prev, event=event,
                      guard=chosen.guard, action=chosen.action, to=chosen.dst, main=chosen.main)
        if self.after_hook:
            self.after_hook(prev, self.state, chosen, ctx)
        return True
