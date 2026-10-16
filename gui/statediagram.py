"""Diagrama de estados: grafo da FSM (lido do JSON) com o estado atual de A e B destacados."""
import math
import tkinter as tk

HOST_STYLE = {"A": ("#1f6feb", "#cfe3ff"), "B": ("#1a7f37", "#d4f4dd")}
RX, RY = 50, 17                     # raios do nó (espaço virtual)


class StateDiagram(tk.Canvas):
    def __init__(self, master, defn, **kw):
        super().__init__(master, bg="white", highlightthickness=1, highlightbackground="#bbbbbb", **kw)
        self._pulse = 0
        self._anim = False
        self.bind("<Configure>", lambda e: self.redraw())
        self.set_definition(defn)

    # ---------------------------------------------------------------- dados
    def set_definition(self, defn):
        self.defn = defn
        self.state = {"A": defn.initial, "B": defn.initial}
        self.last = {}                                  # host -> última Transition disparada
        xs = [p[0] for p in defn.layout.values()]
        ys = [p[1] for p in defn.layout.values()]
        self.bounds = (min(xs) - RX - 20, min(ys) - RY - 20, max(xs) + RX + 20, max(ys) + RY + 40)
        self.edges, self.loops = {}, []
        for t in defn.transitions:
            if not t.main:
                continue
            if t.src == t.dst:
                self.loops.append(t)
            else:
                self.edges.setdefault((t.src, t.dst), []).append(t)
        self.redraw()

    def set_state(self, host, state):
        if self.state.get(host) != state:
            self.state[host] = state
            self.redraw()

    def on_transition(self, host, tr):
        """Chamado a cada transição: guarda a última e anima (pulso) a seta correspondente."""
        self.last[host] = tr
        self.state[host] = tr.dst
        self._pulse = 8
        if not self._anim:
            self._anim = True
            self.after(1, self._animate)

    def _animate(self):
        if self._pulse > 0:
            self._pulse -= 1
            self.redraw()
            self.after(90, self._animate)
        else:
            self._anim = False
            self.redraw()

    # ------------------------------------------------------------- geometria
    def _tf(self):
        w, h = max(self.winfo_width(), 50), max(self.winfo_height(), 50)
        bx0, by0, bx1, by1 = self.bounds
        sc = min(w / (bx1 - bx0), h / (by1 - by0), 1.1)
        ox = (w - (bx1 - bx0) * sc) / 2
        oy = (h - (by1 - by0) * sc) / 2
        return sc, (lambda x, y: ((x - bx0) * sc + ox, (y - by0) * sc + oy))

    @staticmethod
    def _clip(c, d, rx, ry):
        """Ponto na borda da elipse de centro c na direção d."""
        dx, dy = d
        k = math.hypot(dx / rx, dy / ry) or 1
        return c[0] + dx / k, c[1] + dy / k

    def _edge_points(self, a, b, P, sc, shift):
        ax, ay = P(*self.defn.layout[a])
        bx, by = P(*self.defn.layout[b])
        dx, dy = bx - ax, by - ay
        n = math.hypot(dx, dy) or 1
        ox, oy = -dy / n * shift, dx / n * shift
        rx, ry = RX * sc, RY * sc
        p1 = self._clip((ax + ox, ay + oy), (dx, dy), rx, ry)
        p2 = self._clip((bx + ox, by + oy), (-dx, -dy), rx, ry)
        return p1, p2

    # ---------------------------------------------------------------- desenho
    def redraw(self):
        self.delete("all")
        sc, P = self._tf()
        self._sc = sc
        font = ("TkDefaultFont", max(6, int(8 * sc)))
        fbold = ("TkDefaultFont", max(7, int(9 * sc)), "bold")
        lay = self.defn.layout
        # setas principais
        for (a, b), trs in self.edges.items():
            shift = 7 * sc if (b, a) in self.edges else 0
            p1, p2 = self._edge_points(a, b, P, sc, shift)
            self.create_line(*p1, *p2, arrow=tk.LAST, fill="#8a8a8a", width=1.3)
            txt = "\n".join(f"{t.event}" + (f"[{t.guard}]" if t.guard else "") + f"\n{t.action}" for t in trs)
            mx, my = (p1[0] + p2[0]) / 2, (p1[1] + p2[1]) / 2
            tid = self.create_text(mx, my, text=txt, font=("TkDefaultFont", max(6, int(7 * sc))), fill="#333", justify="center")
            x0, y0, x1, y1 = self.bbox(tid)
            rid = self.create_rectangle(x0 - 1, y0, x1 + 1, y1, fill="white", outline="")
            self.tag_lower(rid, tid)
        # laços (UDP: listados abaixo do nó)
        for i, t in enumerate(self.loops):
            x, y = P(*lay[t.src])
            self.create_line(x + 0.2 * RX * sc, y - RY * sc, x + 0.2 * RX * sc, y - RY * sc - 24 * sc,
                             x + 0.9 * RX * sc, y - RY * sc - 24 * sc, x + 0.7 * RX * sc, y - RY * sc * 0.95,
                             smooth=True, arrow=tk.LAST, fill="#8a8a8a", width=1.3)
            self.create_text(x, y + RY * sc + (22 + 44 * i) * sc, justify="center", font=font, fill="#333", width=int(300 * sc),
                             text=f"{t.event}" + (f"[{t.guard}]" if t.guard else "") + f"\n/ {t.action}")
        # última transição de cada host (pulsa)
        for host, tr in self.last.items():
            col = HOST_STYLE[host][0]
            wdt = 4 if self._pulse % 2 == 0 and self._pulse > 0 else 2.5
            if tr.src == tr.dst:
                x, y = P(*lay[tr.src])
                self.create_oval(x - RX * sc - 4, y - RY * sc - 4, x + RX * sc + 4, y + RY * sc + 4,
                                 outline=col, width=wdt, dash=(3, 2))
            else:
                shift = (7 * sc if (tr.dst, tr.src) in self.edges else 0)
                p1, p2 = self._edge_points(tr.src, tr.dst, P, sc, shift)
                dash = () if (tr.src, tr.dst) in self.edges else (5, 3)   # não-principal: tracejada
                self.create_line(*p1, *p2, arrow=tk.LAST, fill=col, width=wdt, dash=dash)
        # nós
        for s in self.defn.states:
            x, y = P(*lay[s])
            a, b = self.state["A"] == s, self.state["B"] == s
            fill = "#fff3b0" if a and b else HOST_STYLE["A"][1] if a else HOST_STYLE["B"][1] if b else "#f6f6f6"
            out = "#b8860b" if a and b else HOST_STYLE["A"][0] if a else HOST_STYLE["B"][0] if b else "#666"
            self.create_oval(x - RX * sc, y - RY * sc, x + RX * sc, y + RY * sc, fill=fill, outline=out,
                             width=3 if (a or b) else 1.2)
            self.create_text(x, y, text=s, font=fbold if (a or b) else font)
            bx = x - RX * sc * 0.95
            for host, on in (("A", a), ("B", b)):
                if on:
                    self.create_oval(bx - 8, y - RY * sc - 9, bx + 8, y - RY * sc + 7, fill=HOST_STYLE[host][0], outline="")
                    self.create_text(bx, y - RY * sc - 1, text=host, fill="white", font=("TkDefaultFont", 8, "bold"))
                    bx += 18
        # legenda
        self.create_text(8, 8, anchor="nw", font=("TkDefaultFont", 8), fill="#444",
                         text=f"{self.defn.name}   ● A (azul)   ● B (verde)   seta destacada = última transição")
