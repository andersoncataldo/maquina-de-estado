"""Aplicação Tkinter: seletor de protocolo/cenário, controles, painel do canal, diagramas e log."""
import os
import tkinter as tk
from tkinter import messagebox, ttk

from simulador import (
    ChannelConfig,
    FSMDefinition,
    Simulation,
    TCPConfig,
    make_payload,
)
from simulador import scenarios as SC


from .seqdiagram import SequenceDiagram
from .statediagram import StateDiagram

DEFS = os.path.join(os.path.dirname(__file__), "..", "fsm_defs")
MODES = ["TCP", "UDP", "Comparar UDP × TCP"]
LIVRE = "(modo livre / manual)"


class View(ttk.Frame):
    """Um simulador visual: diagrama de estados + diagrama de sequência."""

    def __init__(self, master, sim, horizontal):
        super().__init__(master)
        self.sim = sim
        defn = sim.A.engine.defn
        self.state = StateDiagram(self, defn)
        self.seq = SequenceDiagram(self, sim, title=f"Diagrama de sequência — {sim.protocol}")
        if horizontal:
            self.state.pack(side="left", fill="both", expand=True, padx=(0, 3))
            self.seq.pack(side="left", fill="both", expand=True)
        else:
            self.state.configure(height=210)
            self.state.pack(side="top", fill="both", expand=True, pady=(0, 3))
            self.seq.pack(side="top", fill="both", expand=True)
        sim.bus.subscribe(self._on_event)

    def _on_event(self, r):
        if r["kind"] == "transition":
            self.state.on_transition(r["host"], self.sim.hosts[r["host"]].engine.last)


class App(ttk.Frame):
    def __init__(self, root):
        super().__init__(root)
        self.root = root
        root.title("Simulador de Máquina de Estado — Projeto de Redes Convergentes")
        root.geometry("1440x900")
        self.pack(fill="both", expand=True)
        self.sims, self.views = [], []
        self.running = False
        self.rows = []
        self.rtt_txt = tk.StringVar(value="RTT: –")
        self._build_ui()
        self.load_mode()
        self.after(30, self._tick)

    # ================================================================== UI
    def _build_ui(self):
        top = ttk.Frame(self, padding=4)
        top.pack(fill="x")
        ttk.Label(top, text="Protocolo:").pack(side="left")
        self.mode = tk.StringVar(value="TCP")
        cb = ttk.Combobox(top, textvariable=self.mode, values=MODES, width=20, state="readonly")
        cb.pack(side="left", padx=4)
        cb.bind("<<ComboboxSelected>>", lambda e: (self.scn.set(LIVRE), self.load_mode()))
        ttk.Label(top, text="Cenário:").pack(side="left", padx=(10, 0))
        self.scn = tk.StringVar(value=LIVRE)
        names = [LIVRE] + [f"{s.id} · {s.protocol} · {s.name}" for s in SC.SCENARIOS]
        sc = ttk.Combobox(top, textvariable=self.scn, values=names, width=44, state="readonly")
        sc.pack(side="left", padx=4)
        sc.bind("<<ComboboxSelected>>", lambda e: self.load_mode())
        ttk.Separator(top, orient="vertical").pack(side="left", fill="y", padx=8)
        self.btn_play = ttk.Button(top, text="▶ Iniciar", command=self.toggle)
        self.btn_play.pack(side="left", padx=2)
        ttk.Button(top, text="⏭ Passo", command=self.step).pack(side="left", padx=2)
        ttk.Button(top, text="⟲ Reset", command=self.load_mode).pack(side="left", padx=2)
        ttk.Label(top, text="  Velocidade:").pack(side="left")
        self.speed = tk.DoubleVar(value=0.3)
        tk.Scale(top, from_=0.02, to=5, resolution=0.02, orient="horizontal", variable=self.speed, length=90,
                 showvalue=True).pack(side="left")
        ttk.Button(top, text="Rodar testes", command=self.run_tests).pack(side="right", padx=2)
        ttk.Button(top, text="📷 PNG", command=self.save_png).pack(side="right", padx=2)
        ttk.Button(top, text="Exportar log", command=self.export_log).pack(side="right", padx=2)

        body = ttk.Frame(self)
        body.pack(fill="both", expand=True)
        left = ttk.Frame(body, padding=4, width=300)
        left.pack(side="left", fill="y")
        left.pack_propagate(False)
        self._build_left(left)
        # Painel direito rolável com barra de rolagem lateral na direita
        self.right_container = ttk.Frame(body)
        self.right_container.pack(side="left", fill="both", expand=True)

        self.right_sb = ttk.Scrollbar(self.right_container, orient="vertical")
        self.right_sb.pack(side="right", fill="y")

        self.right_canvas = tk.Canvas(
            self.right_container,
            highlightthickness=0,
            yscrollcommand=self.right_sb.set
        )
        self.right_canvas.pack(side="left", fill="both", expand=True)
        self.right_sb.configure(command=self.right_canvas.yview)

        right = ttk.Frame(self.right_canvas)
        self.right_frame = right
        self.right_win_id = self.right_canvas.create_window((0, 0), window=right, anchor="nw")

        def _update_right_scroll(event=None):
            self.right_canvas.update_idletasks()
            w = self.right_canvas.winfo_width()
            h_can = self.right_canvas.winfo_height()
            h_req = right.winfo_reqheight()
            h = max(h_can, h_req)
            self.right_canvas.itemconfig(self.right_win_id, width=w, height=h)
            self.right_canvas.configure(scrollregion=(0, 0, w, h_req))

        self.right_canvas.bind("<Configure>", _update_right_scroll)
        right.bind("<Configure>", _update_right_scroll)

        # Suporte a rolagem com a roda do mouse (MouseWheel)
        def _on_mousewheel(event):
            w = event.widget
            in_right = False
            while w is not None:
                if w == self.right_container:
                    in_right = True
                if isinstance(w, ttk.Treeview) or getattr(w, "is_seq_canvas", False):
                    return
                w = getattr(w, "master", None)
            if in_right and right.winfo_reqheight() > self.right_canvas.winfo_height():
                self.right_canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

        def _on_mousewheel_up(event):
            w = event.widget
            in_right = False
            while w is not None:
                if w == self.right_container:
                    in_right = True
                if isinstance(w, ttk.Treeview) or getattr(w, "is_seq_canvas", False):
                    return
                w = getattr(w, "master", None)
            if in_right and right.winfo_reqheight() > self.right_canvas.winfo_height():
                self.right_canvas.yview_scroll(-3, "units")

        def _on_mousewheel_down(event):
            w = event.widget
            in_right = False
            while w is not None:
                if w == self.right_container:
                    in_right = True
                if isinstance(w, ttk.Treeview) or getattr(w, "is_seq_canvas", False):
                    return
                w = getattr(w, "master", None)
            if in_right and right.winfo_reqheight() > self.right_canvas.winfo_height():
                self.right_canvas.yview_scroll(3, "units")

        self.root.bind_all("<MouseWheel>", _on_mousewheel)
        self.root.bind_all("<Button-4>", _on_mousewheel_up)
        self.root.bind_all("<Button-5>", _on_mousewheel_down)

        self.status = ttk.Label(right, textvariable=self.rtt_txt, anchor="w", font=("TkDefaultFont", 9, "bold"),
                                padding=(4, 2))
        self.status.pack(fill="x")
        self.center = ttk.Frame(right)
        self.center.pack(fill="both", expand=True)
        self._build_log(right)

    def _build_left(self, f):
        ch = ttk.LabelFrame(f, text="Canal simulado", padding=4)
        ch.pack(fill="x")
        self.v = {}
        spec = [("loss", "Perda (%)", 0, 100, 1, 0), ("dmin", "Atraso mín (ms)", 0, 500, 5, 50),
                ("dmax", "Atraso máx (ms)", 0, 500, 5, 50), ("corrupt", "Corrupção de bits (%)", 0, 100, 1, 0),
                ("dup", "Duplicação (%)", 0, 100, 1, 0), ("reorder", "Reordenação (%)", 0, 100, 1, 0)]
        for key, lab, lo, hi, res, dv in spec:
            self.v[key] = tk.DoubleVar(value=dv)
            tk.Scale(ch, from_=lo, to=hi, resolution=res, orient="horizontal", label=lab, variable=self.v[key],
                     command=lambda _=None: self.apply_channel(), length=280, font=("TkDefaultFont", 8)).pack(fill="x")
        row = ttk.Frame(ch)
        row.pack(fill="x", pady=2)
        ttk.Label(row, text="Seed:").pack(side="left")
        self.seed = tk.StringVar(value="1")
        ttk.Entry(row, textvariable=self.seed, width=8).pack(side="left", padx=4)
        ttk.Label(row, text="(Reset aplica)").pack(side="left")
        ttk.Button(ch, text="✂ Descartar próximo segmento", command=lambda: self.chan("drop")).pack(fill="x", pady=1)
        ttk.Button(ch, text="⚡ Corromper próximo segmento", command=lambda: self.chan("corrupt")).pack(fill="x")

        tp = ttk.LabelFrame(f, text="Opções do TCP (Reset aplica)", padding=4)
        tp.pack(fill="x", pady=4)
        self.o_fast = tk.BooleanVar(value=True)
        self.o_tw = tk.BooleanVar(value=True)
        self.o_cc = tk.BooleanVar(value=False)
        self.o_msl = tk.StringVar(value="1000")
        ttk.Checkbutton(tp, text="Retransm. rápida (3 dupACKs)", variable=self.o_fast).pack(anchor="w")
        ttk.Checkbutton(tp, text="Estado TIME_WAIT", variable=self.o_tw).pack(anchor="w")
        ttk.Checkbutton(tp, text="Congestionamento (SS/AIMD)", variable=self.o_cc).pack(anchor="w")
        r = ttk.Frame(tp)
        r.pack(fill="x")
        ttk.Label(r, text="MSL (ms); TIME_WAIT=2×MSL:").pack(side="left")
        ttk.Entry(r, textvariable=self.o_msl, width=7).pack(side="left", padx=3)

        ap = ttk.LabelFrame(f, text="Aplicação (ações manuais)", padding=4)
        ap.pack(fill="x")
        g = ttk.Frame(ap)
        g.pack(fill="x")
        for i, (txt, cmd) in enumerate([("B: listen", "listen"), ("A: connect", "connect"),
                                        ("A: close", "closeA"), ("B: close", "closeB")]):
            ttk.Button(g, text=txt, command=lambda c=cmd: self.app(c)).grid(row=i // 2, column=i % 2, sticky="ew", padx=1, pady=1)
        g.columnconfigure((0, 1), weight=1)
        r = ttk.Frame(ap)
        r.pack(fill="x", pady=2)
        self.nsend = tk.IntVar(value=10)
        ttk.Button(r, text="A → B enviar", command=lambda: self.app("send")).pack(side="left")
        ttk.Spinbox(r, from_=1, to=200, textvariable=self.nsend, width=5).pack(side="left", padx=4)
        ttk.Label(r, text="blocos de 100 B").pack(side="left")

        st = ttk.LabelFrame(f, text="Estado atual", padding=4)
        st.pack(fill="x", pady=4)
        self.lbl = {h: tk.StringVar(value="-") for h in "AB"}
        self.lblt = tk.StringVar(value="t = 0 ms")
        for h in "AB":
            ttk.Label(st, textvariable=self.lbl[h], font=("TkDefaultFont", 9, "bold"),
                      foreground="#1f6feb" if h == "A" else "#1a7f37", wraplength=280, justify="left").pack(anchor="w")
        ttk.Label(st, textvariable=self.lblt).pack(anchor="w")
        self.last_tr = tk.StringVar(value="Última transição: -")
        ttk.Label(st, textvariable=self.last_tr, wraplength=280, justify="left").pack(anchor="w", pady=(3, 0))

    def _build_log(self, parent):
        lf = ttk.LabelFrame(parent, text="Log", padding=3)
        lf.pack(fill="x")
        bar = ttk.Frame(lf)
        bar.pack(fill="x")
        ttk.Label(bar, text="Host:").pack(side="left")
        self.f_host = tk.StringVar(value="Todos")
        h = ttk.Combobox(bar, textvariable=self.f_host, values=["Todos", "A", "B"], width=7, state="readonly")
        h.pack(side="left", padx=3)
        h.bind("<<ComboboxSelected>>", lambda e: self.refilter())
        ttk.Label(bar, text="Filtrar texto:").pack(side="left", padx=(8, 0))
        self.f_txt = tk.StringVar()
        e = ttk.Entry(bar, textvariable=self.f_txt, width=28)
        e.pack(side="left", padx=3)
        e.bind("<KeyRelease>", lambda ev: self.refilter())
        self.f_rej = tk.BooleanVar(value=False)
        ttk.Checkbutton(bar, text="só rejeições/descartes", variable=self.f_rej, command=self.refilter).pack(side="left", padx=6)
        cols = ("t", "host", "proto", "prev", "event", "action", "new")
        heads = ("tempo (ms)", "host", "proto", "estado anterior", "evento[condição]", "ação", "novo estado")
        widths = (80, 45, 50, 110, 190, 480, 110)
        self.tree = ttk.Treeview(lf, columns=cols, show="headings", height=8)
        for c, hd, w in zip(cols, heads, widths):
            self.tree.heading(c, text=hd)
            self.tree.column(c, width=w, anchor="w")
        sb = ttk.Scrollbar(lf, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side="left", fill="x", expand=True)
        sb.pack(side="left", fill="y")
        self.tree.bind("<MouseWheel>", lambda e: (self.tree.yview_scroll(int(-1 * (e.delta / 120)), "units"), "break")[1])
        self.tree.tag_configure("reject", foreground="#c0392b")
        self.tree.tag_configure("retx", foreground="#e67e22")

    # ============================================================ simulação
    def _num(self, var, default):
        try:
            return float(var.get())
        except (ValueError, tk.TclError):
            return default

    def _channel_cfg(self):
        v = self.v
        return ChannelConfig(loss=v["loss"].get() / 100, delay_min=v["dmin"].get(), delay_max=max(v["dmin"].get(), v["dmax"].get()),
                             corrupt=v["corrupt"].get() / 100, duplicate=v["dup"].get() / 100, reorder=v["reorder"].get() / 100)

    def _tcp_cfgs(self):
        kw = dict(fast_retransmit=self.o_fast.get(), time_wait=self.o_tw.get(),
                  congestion_control=self.o_cc.get(), msl=self._num(self.o_msl, 1000.0))
        return TCPConfig(isn=100, **kw), TCPConfig(isn=300, **kw)

    def load_mode(self):
        self.running = False
        self.btn_play.config(text="▶ Iniciar")
        self.rows.clear()
        self.tree.delete(*self.tree.get_children())
        for w in self.center.winfo_children():
            w.destroy()
        name = self.scn.get()
        if name != LIVRE:
            sc = SC.get(name.split(" · ")[0])
            self.sims = sc.build()
            self.current_scn = sc
            cfg = self.sims[0].channel_cfg
            for k, val in (("loss", cfg.loss * 100), ("dmin", cfg.delay_min), ("dmax", cfg.delay_max),
                           ("corrupt", cfg.corrupt * 100), ("dup", cfg.duplicate * 100), ("reorder", cfg.reorder * 100)):
                self.v[k].set(val)
            self.mode.set("Comparar UDP × TCP" if len(self.sims) == 2 else sc.protocol)
        else:
            self.current_scn = None
            try:
                seed = int(self.seed.get())
            except ValueError:
                seed = 1
            mode = self.mode.get()
            a, b = self._tcp_cfgs()
            protos = ["UDP", "TCP"] if mode.startswith("Comparar") else [mode]
            self.sims = [Simulation(p, seed, self._channel_cfg(), a if p == "TCP" else None,
                                    b if p == "TCP" else None) for p in protos]
        horizontal = len(self.sims) == 1
        container = ttk.Frame(self.center)
        container.pack(fill="both", expand=True)
        self.views = []
        for i, s in enumerate(self.sims):
            vw = View(container, s, horizontal)
            if horizontal:
                vw.pack(fill="both", expand=True)
            else:
                vw.pack(side="left", fill="both", expand=True, padx=2)
            self.views.append(vw)
            s.bus.subscribe(lambda r, s=s: self._on_bus(s, r))
        self._refresh()
        self.after(40, self._update_scroll_geometry)

    def _update_scroll_geometry(self):
        self.right_canvas.update_idletasks()
        w = self.right_canvas.winfo_width()
        h_can = self.right_canvas.winfo_height()
        h_req = self.right_frame.winfo_reqheight()
        h = max(h_can, h_req)
        self.right_canvas.itemconfig(self.right_win_id, width=w, height=h)
        self.right_canvas.configure(scrollregion=(0, 0, w, h_req))
        self.right_canvas.yview_moveto(0)

    def apply_channel(self):
        for s in getattr(self, "sims", []):
            c, n = s.channel_cfg, self._channel_cfg()
            c.loss, c.delay_min, c.delay_max, c.corrupt, c.duplicate, c.reorder = (
                n.loss, n.delay_min, n.delay_max, n.corrupt, n.duplicate, n.reorder)

    def chan(self, what):
        for s in self.sims:
            (s.channel.drop_next if what == "drop" else s.channel.corrupt_next)()

    def app(self, cmd):
        for s in self.sims:
            if cmd == "send":
                n = max(1, self.nsend.get())
                if s.protocol == "UDP":
                    for i in range(n):
                        s.at(s.now + i * 10, lambda i=i, s=s: s.A.send(make_payload(i + 1, 100)[-100:]))
                else:
                    s.A.send(make_payload(n, 100))
            elif s.protocol == "TCP":
                {"listen": s.B.listen, "connect": s.A.connect, "closeA": s.A.close, "closeB": s.B.close}[cmd]()
        self.running = True
        self.btn_play.config(text="⏸ Pausar")
        self._refresh()

    # ----------------------------------------------------------- controles
    def toggle(self):
        self.running = not self.running
        self.btn_play.config(text="⏸ Pausar" if self.running else "▶ Iniciar")

    def step(self):
        self.running = False
        self.btn_play.config(text="▶ Iniciar")
        times = [t for t in (s.next_time() for s in self.sims) if t is not None]
        if not times:
            return
        for s in self.sims:
            s.run_until(min(times))
        self._refresh()

    def _tick(self):
        if self.running:
            if all(s.next_time() is None for s in self.sims):
                self.running = False
                self.btn_play.config(text="▶ Iniciar")
            else:
                target = max(s.now for s in self.sims) + 30 * self.speed.get()
                for s in self.sims:
                    s.run_until(target)
            self._refresh()
        self.after(30, self._tick)

    def _refresh(self):
        for v in self.views:
            v.seq.redraw()
        for s in self.sims:
            for h in "AB":
                st = s.hosts[h].state
                if len(self.sims) == 1:
                    self.lbl[h].set(f"Host {h}: {st}")
                else:
                    self.lbl[h].set(" | ".join(f"{x.protocol}: {x.hosts[h].state}" for x in self.sims) + f"   (host {h})")
        self.lblt.set(f"t = {max(s.now for s in self.sims):.1f} ms (virtual)")

    # ---------------------------------------------------------------- log
    def _on_bus(self, sim, r):
        k, tag = r["kind"], sim.protocol
        g = lambda: f"[{r['guard']}]" if r.get("guard") else ""
        if k == "transition":
            row = (r["t"], r["host"], tag, r["frm"], r["event"] + g(), r["action"], r["to"], ())
            self.last_tr.set(f"Última transição ({tag} {r['host']}): {r['frm']} --{r['event']}{g()}/ {r['action']}--> {r['to']}")
        elif k == "reject":
            row = (r["t"], r["host"], tag, r["state"], r["event"], "✘ REJEITADO: " + r["reason"], r["state"], ("reject",))
        elif k == "drop":
            row = (r["t"], r["host"], tag, "-", "descarte", "✘ " + r["reason"] + " — " + r.get("label", ""), "-", ("reject",))
        elif k == "retransmit":
            row = (r["t"], r["host"], tag, "-", "retransmissão", f"retransmissão ({r['reason']}) SEQ={r['seq']}", "-", ("retx",))
        elif k == "rtt":
            self.rtt_txt.set(f"{tag} {r['host']}  RTT amostra={r['sample']} ms | EstimatedRTT={r['est']} | "
                             f"DevRTT={r['dev']} | TimeoutInterval={r['timeout']} ms")
            return
        elif k == "cwnd":
            self.rtt_txt.set(self.rtt_txt.get().split("  ||")[0] + f"  || cwnd={r['cwnd']} B ssthresh={r['ssthresh']} B")
            return
        else:
            return
        self.rows.append(row)
        if self._match(row):
            self._insert(row)

    def _match(self, row):
        if self.f_host.get() != "Todos" and row[1] != self.f_host.get():
            return False
        if self.f_rej.get() and "reject" not in row[7]:
            return False
        t = self.f_txt.get().strip().lower()
        return not t or t in " ".join(str(c) for c in row[:7]).lower()

    def _insert(self, row):
        self.tree.insert("", "end", values=(f"{row[0]:.1f}",) + tuple(row[1:7]), tags=row[7])
        self.tree.yview_moveto(1)

    def refilter(self):
        self.tree.delete(*self.tree.get_children())
        for r in self.rows:
            if self._match(r):
                self._insert(r)

    # ------------------------------------------------------------- extras
    def run_tests(self):
        win = tk.Toplevel(self.root)
        win.title("Testes automatizados (sem GUI)")
        win.geometry("820x600")
        txt = tk.Text(win, font=("Courier", 10))
        txt.pack(fill="both", expand=True)
        txt.tag_configure("ok", foreground="#1a7f37")
        txt.tag_configure("bad", foreground="#c0392b")
        for sc in SC.SCENARIOS:
            res = SC.run_scenario(sc)
            txt.insert("end", f"[{'APROVADO' if res['passed'] else 'REPROVADO'}] Cenário {sc.id} — {sc.name}\n",
                       "ok" if res["passed"] else "bad")
            for d, ok in res["checks"]:
                txt.insert("end", f"     {'✔' if ok else '✘'} {d}\n", "ok" if ok else "bad")
            if sc.id == "10":
                txt.insert("end", "\n" + SC.format_report(SC.compare_report(res["sims"])) + "\n\n")

    def export_log(self):
        from tkinter import filedialog
        path = filedialog.asksaveasfilename(defaultextension=".json", initialfile="log_simulacao.json")
        if path:
            self.sims[0].bus.save_json(path)

    def save_png(self):
        from tkinter import filedialog
        path = filedialog.asksaveasfilename(defaultextension=".png", initialfile="captura_gui.png")
        if not path:
            return
        try:
            from PIL import ImageGrab
            self.root.update()
            x, y = self.root.winfo_rootx(), self.root.winfo_rooty()
            ImageGrab.grab(bbox=(x, y, x + self.root.winfo_width(), y + self.root.winfo_height())).save(path)
        except Exception as exc:                                  # Pillow ausente ou sem suporte no SO
            messagebox.showinfo("Captura", f"Não foi possível salvar automaticamente ({exc}).\n"
                                           "Use a captura de tela do sistema (Win+Shift+S / Cmd+Shift+4 / PrtSc).")


def main():
    root = tk.Tk()
    App(root)
    root.mainloop()
