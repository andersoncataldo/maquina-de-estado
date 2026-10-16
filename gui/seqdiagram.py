"""Diagrama de sequência: linhas de tempo de A e B, setas rotuladas, animação e rolagem.

A régua de tempo é NÃO linear: lacunas longas (timeouts, TIME_WAIT) são comprimidas para que
rajadas de segmentos continuem legíveis. Os valores em ms são mostrados na régua.
"""
import math
import tkinter as tk
from tkinter import ttk

COL = dict(normal="#1f6feb", retx="#e67e22", lost="#d32f2f", corrupt="#8e44ad", dup="#888888")
HOST_COLOR = {"A": "#1f6feb", "B": "#1a7f37"}
STAGGER = 13          # px entre setas que partem no mesmo instante
MIN_GAP, MAX_GAP, PX_PER_MS = 34, 80, 0.6


class SequenceDiagram(ttk.Frame):
    def __init__(self, master, sim, title="", **kw):
        super().__init__(master)
        self.title = title
        self.head = tk.Canvas(self, height=62, bg="white", highlightthickness=0)
        self.head.pack(fill="x")
        row = ttk.Frame(self)
        row.pack(fill="both", expand=True)
        self.cv = tk.Canvas(row, bg="white", highlightthickness=1, highlightbackground="#bbbbbb")
        self.cv.is_seq_canvas = True
        sb = ttk.Scrollbar(row, orient="vertical", command=self.cv.yview)
        self.cv.configure(yscrollcommand=sb.set)
        self.cv.pack(side="left", fill="both", expand=True)
        sb.pack(side="left", fill="y")
        self.cv.bind("<Configure>", lambda e: self.redraw())
        self.cv.bind("<MouseWheel>", lambda e: (self.cv.yview_scroll(-1 * (e.delta // 120 or (1 if e.delta > 0 else -1)), "units"), "break")[1])
        self.cv.bind("<Button-4>", lambda e: (self.cv.yview_scroll(-3, "units"), "break")[1])
        self.cv.bind("<Button-5>", lambda e: (self.cv.yview_scroll(3, "units"), "break")[1])
        self.follow = True
        self.attach(sim)

    def attach(self, sim):
        self.sim = sim
        self.txs, self.marks = [], []
        sim.bus.subscribe(self._on_event)
        for r in sim.bus.log:
            self._on_event(r)

    def _on_event(self, r):
        k = r["kind"]
        if k == "tx":
            self.txs.append(r)
        elif k == "transition" and r["frm"] != r["to"]:
            self.marks.append((r["t"], r["host"], r["to"], HOST_COLOR[r["host"]], True))
        elif k == "timer" and r["op"] == "expire":
            self.marks.append((r["t"], r["host"], "⏱ " + r["name"], "#c0392b", False))

    # ------------------------------------------------------------ mapeamento tempo → y
    def _build_axis(self):
        mean_delay = (self.sim.channel_cfg.delay_min + self.sim.channel_cfg.delay_max) / 2
        times, burst = {0.0}, {}
        for e in self.txs:
            times.add(e["t"])
            burst[(e["host"], e["t"])] = burst.get((e["host"], e["t"]), 0) + 1
            times.update(e["arrivals"])
            if e["lost"]:
                times.add(e["t"] + mean_delay * 0.6)
        times.update(m[0] for m in self.marks)
        ts = sorted(times)
        slot = {t: 0 for t in ts}
        for (h, t), n in burst.items():
            slot[t] = max(slot[t], STAGGER * (n - 1))
        ys, y = {}, 20.0
        prev = None
        for t in ts:
            if prev is not None:
                y += slot[prev] + min(MAX_GAP, max(MIN_GAP, (t - prev) * PX_PER_MS))
            ys[t] = y
            prev = t
        self._ts, self._ys, self._slot = ts, ys, slot
        self._last_y = y + (slot[ts[-1]] if ts else 0)

    def Y(self, t):
        ts, ys = self._ts, self._ys
        if t <= ts[0]:
            return ys[ts[0]]
        if t >= ts[-1]:
            return ys[ts[-1]] + min(MAX_GAP, (t - ts[-1]) * PX_PER_MS)
        import bisect
        i = bisect.bisect_right(ts, t) - 1
        if ts[i] == t:
            return ys[t]
        f = (t - ts[i]) / (ts[i + 1] - ts[i])
        return ys[ts[i]] + f * (ys[ts[i + 1]] - ys[ts[i]] - self._slot[ts[i]])

    # ---------------------------------------------------------------- desenho
    def redraw(self):
        w, h = max(self.cv.winfo_width(), 200), max(self.cv.winfo_height(), 100)
        xa, xb = 90, w - 110
        self.head.delete("all")
        self.head.create_text(w / 2, 2, text=self.title, anchor="n", font=("TkDefaultFont", 9, "bold"))
        for x, name, c in ((xa, "A (cliente)", HOST_COLOR["A"]), (xb, "B (servidor)", HOST_COLOR["B"])):
            self.head.create_text(x, 50, text=name, fill=c, font=("TkDefaultFont", 9, "bold"))
        cv = self.cv
        cv.delete("all")
        self._build_axis()
        now = self.sim.now
        total = max(self._last_y + 40, h)
        for x, c in ((xa, HOST_COLOR["A"]), (xb, HOST_COLOR["B"])):
            cv.create_line(x, 0, x, total, fill=c, width=2)
        # régua (ms) com rótulos espaçados
        last_lab = -99
        for t in self._ts:
            yy = self._ys[t]
            if yy - last_lab >= 15:
                cv.create_line(xa - 4, yy, xa + 4, yy, fill="#888")
                cv.create_text(4, yy, text=f"{round(t, 1):g}", anchor="w", font=("TkDefaultFont", 7), fill="#777")
                last_lab = yy
        ynow = self.Y(now)
        cv.create_line(xa, ynow, xb, ynow, fill="#cccccc", dash=(2, 3))
        cv.create_text(4, ynow - 7, text="agora", anchor="w", font=("TkDefaultFont", 7, "italic"), fill="#999")
        # setas
        mean_delay = (self.sim.channel_cfg.delay_min + self.sim.channel_cfg.delay_max) / 2
        k_at = {}
        for e in self.txs:
            src, t0 = e["host"], e["t"]
            k = k_at.get((src, t0), 0)
            k_at[(src, t0)] = k + 1
            off = k * STAGGER
            x0, x1 = (xa, xb) if src == "A" else (xb, xa)
            y0 = self.Y(t0) + off
            if e["lost"]:
                tend = t0 + mean_delay * 0.6
                self._arrow(x0, y0, x0 + (x1 - x0) * 0.6, self.Y(tend) + off, now, t0, tend, COL["lost"], e, lost=True)
            else:
                for i, arr in enumerate(e["arrivals"]):
                    col = COL["dup"] if i > 0 else COL["corrupt"] if e["corrupted"] else COL["retx"] if e["retx"] else COL["normal"]
                    self._arrow(x0, y0, x1, self.Y(arr) + off, now, t0, arr, col, e, dash=(4, 3) if i > 0 else ())
        # marcas de estado / timers
        for t, host, text, col, is_state in self.marks:
            x = xa if host == "A" else xb
            yy = self.Y(t)
            cv.create_oval(x - 4, yy - 4, x + 4, yy + 4, fill=col, outline="")
            cv.create_text(x - 8 if host == "A" else x + 8, yy, text=text, fill=col,
                           anchor="e" if host == "A" else "w", font=("TkDefaultFont", 8, "bold" if is_state else "italic"))
        cv.configure(scrollregion=(0, 0, w, total))
        if total > h and self.follow:
            cv.yview_moveto(max(0.0, (ynow - h * 0.75) / total))
        # legenda
        self.head.create_text(6, 17, anchor="nw", font=("TkDefaultFont", 7), fill="#555",
                              text="régua em ms (lacunas longas comprimidas)")
        lx = 6
        for lab, c in (("normal", COL["normal"]), ("retransmitido", COL["retx"]), ("perdido ✕", COL["lost"]),
                       ("corrompido", COL["corrupt"]), ("duplicata", COL["dup"])):
            self.head.create_text(lx, 29, text="━" + lab, anchor="nw", fill=c, font=("TkDefaultFont", 7))
            lx += 12 + 6 * len(lab)

    def _arrow(self, x0, y0, x1, y1, now, t_start, t_end, col, e, lost=False, dash=()):
        cv = self.cv
        arrived = now >= t_end
        if not arrived:                                  # segmento em voo: desenha até o instante atual
            if now < t_start:
                return
            f = (now - t_start) / max(t_end - t_start, 1e-9)
            x1, y1 = x0 + (x1 - x0) * f, y0 + (y1 - y0) * f
        cv.create_line(x0, y0, x1, y1, fill=col, width=2, arrow=tk.LAST if (arrived and not lost) else None, dash=dash)
        if lost and arrived:
            cv.create_line(x1 - 6, y1 - 6, x1 + 6, y1 + 6, fill=col, width=3)
            cv.create_line(x1 - 6, y1 + 6, x1 + 6, y1 - 6, fill=col, width=3)
        mx, my = (x0 + x1) / 2, (y0 + y1) / 2
        lab = e["label"] + (" ⚡" if e["corrupted"] else "") + (" (retx)" if e["retx"] else "")
        if dash:
            lab = "(dup) " + lab
        ang = -math.degrees(math.atan2(y1 - y0, x1 - x0))
        if x1 < x0:
            ang += 180
        try:
            cv.create_text(mx, my - 6, text=lab, fill=col, font=("TkDefaultFont", 7), angle=ang)
        except tk.TclError:
            cv.create_text(mx, my - 6, text=lab, fill=col, font=("TkDefaultFont", 7))
