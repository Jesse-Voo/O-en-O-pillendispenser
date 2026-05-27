#!/usr/bin/env python3
"""
Thelma Medicijndispenser Simulator – Escape Room Edition

Flow:
  1. Start screen → caregiver presses START
  2. Sends HTTP trigger to other escape-room device
  3. Countdown ticks down (replaces clock)
  4. Other device sends back: patient name + schedule
  5. At 00:00 → auto-dispense
  6. Caregiver must have loaded the roll; presses button, takes sachet → done!

Network:
  - Listens on :listen_port for incoming JSON POSTs at /trigger
  - POSTs to other_ip:other_port/trigger on game events

Edit config.json to configure.
"""

import tkinter as tk
from tkinter import font as tkfont
from datetime import datetime
import threading
import traceback
import time
import queue
import json
import os
import socket
import urllib.request
from http.server import HTTPServer, BaseHTTPRequestHandler

# ── Config ─────────────────────────────────────────────────────────────────────
_DIR = os.path.dirname(os.path.abspath(__file__))

CONFIG = {
    "other_ip":          "172.16.0.2",
    "other_port":        3000,
    "listen_port":       5000,
    "countdown_seconds": 900,
    "auto_dispense_at_zero": True,
}
_cfg_path = os.path.join(_DIR, "config.json")
if os.path.exists(_cfg_path):
    try:
        with open(_cfg_path) as _f:
            CONFIG.update(json.load(_f))
    except Exception:
        pass

# ── Thread-safe event queue (HTTP thread → tkinter main thread) ────────────────
_event_q: queue.Queue = queue.Queue()
_http_server = None


class _Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        try:
            cl = self.headers.get("Content-Length")
            te = self.headers.get("Transfer-Encoding", "")
            if cl is not None:
                body = self.rfile.read(int(cl))
            elif "chunked" in te.lower():
                body = b""
                while True:
                    line = self.rfile.readline().strip()
                    if not line:
                        break
                    size = int(line, 16)
                    if size == 0:
                        break
                    body += self.rfile.read(size)
                    self.rfile.read(2)
            else:
                body = b""
            try:
                data = json.loads(body)
                print(f"[HTTP] ontvangen: {data}")
                _event_q.put(data)
            except Exception:
                print(f"[HTTP] kon body niet parsen: {body!r}")
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"OK")
        except Exception as e:
            print(f"[HTTP POST] fout: {e}")
            traceback.print_exc()

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        import json as _json
        self.wfile.write(_json.dumps({
            "status": "ok",
            "screen": "thelma",
            "escape_active": state.escape_active,
            "roll_loaded": state.roll_loaded,
            "patient_info_ready": state.patient_info_ready,
            "countdown": state.countdown_remaining,
        }).encode())

    def log_message(self, *_):
        pass


class _ReuseServer(HTTPServer):
    allow_reuse_address = True


def _start_server():
    global _http_server
    port = CONFIG["listen_port"]
    try:
        _http_server = _ReuseServer(("0.0.0.0", port), _Handler)
        t = threading.Thread(target=_http_server.serve_forever)
        t.daemon = False  # blijft draaien ook als tkinter crasht
        t.start()
        print(f"[HTTP] Luistert op 0.0.0.0:{port}")
    except OSError as e:
        print(f"[HTTP] Kon poort {port} niet openen: {e}")
    except Exception as e:
        print(f"[HTTP] {e}")


def send_trigger(data: dict):
    """Fire-and-forget POST to the other escape-room device."""
    def _go():
        try:
            req = urllib.request.Request(
                f"http://{CONFIG['other_ip']}:{CONFIG['other_port']}/trigger",
                data=json.dumps(data).encode(),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            urllib.request.urlopen(req, timeout=3)
        except Exception:
            pass
    threading.Thread(target=_go, daemon=True).start()


def local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "?"


# ── Colors ─────────────────────────────────────────────────────────────────────
BG         = "#FFFFFF"
GREEN      = "#5BB87A"
ORANGE     = "#EEA219"
RED        = "#DC3545"
BLUE_BTN   = "#3D5A8C"
DARK_TEXT  = "#1A1A1A"
GREY_TEXT  = "#9E9E9E"
LIGHT_GREY = "#F5F5F5"
BORDER     = "#E8E8E8"
NAV_ARROW  = "#6B8CC7"
DEVICE_BG  = "#2A2A2A"


# ── Shared state ───────────────────────────────────────────────────────────────
class AppState:
    def __init__(self):
        self.roll_loaded         = False
        self.patient_info_ready  = False       # hidden until other device triggers
        self.signal              = 4

        # ── Preset patiëntgegevens ─────────────────────────────────────────────
        self.patient_name = "Rayan Echebechi"
        self.schedule = [
            {"time": "14:30", "taken": False,
             "medicines": ["Paracetamol 1000mg", "Pantoprazol 40mg",
                           "Simvastatine 20mg"]},
        ]
        self.dispense_state      = "idle"      # idle | green | yellow | red | ready
        self.dispense_index      = None

        # Escape-room
        self.escape_active       = False
        self.countdown_total     = CONFIG["countdown_seconds"]
        self.countdown_remaining = CONFIG["countdown_seconds"]
        self.escape_complete     = False

        self._listeners          = []

    def notify(self):
        for cb in self._listeners:
            cb()

    def on_change(self, cb):
        self._listeners.append(cb)

    @property
    def taken_count(self):
        return sum(1 for s in self.schedule if s.get("taken"))


state = AppState()


# ── Drawing helpers ────────────────────────────────────────────────────────────

def _darken(hex_c, amt=20):
    r = max(0, int(hex_c[1:3], 16) - amt)
    g = max(0, int(hex_c[3:5], 16) - amt)
    b = max(0, int(hex_c[5:7], 16) - amt)
    return f"#{r:02x}{g:02x}{b:02x}"


def _countdown_str(seconds):
    seconds = max(0, int(seconds))
    return f"{seconds // 60:02d}:{seconds % 60:02d}"


def _countdown_hm(slot_time):
    try:
        h, m = map(int, slot_time.split(":"))
        now = datetime.now()
        target = now.replace(hour=h, minute=m, second=0, microsecond=0)
        diff = int(abs((target - now).total_seconds()))
        return diff // 3600, (diff % 3600) // 60
    except Exception:
        return 0, 0


def draw_rrect(canvas, x1, y1, x2, y2, r, fill):
    for ax, ay, start in [
        (x1,      y1,      90),
        (x2-2*r,  y1,       0),
        (x1,      y2-2*r, 180),
        (x2-2*r,  y2-2*r, 270),
    ]:
        canvas.create_arc(ax, ay, ax+2*r, ay+2*r,
                          start=start, extent=90, fill=fill, outline=fill)
    canvas.create_rectangle(x1+r, y1, x2-r, y2, fill=fill, outline=fill)
    canvas.create_rectangle(x1, y1+r, x2, y2-r, fill=fill, outline=fill)


def pill_btn(parent, text, bg_color, fg_color, font_obj, cmd,
             width=320, height=54):
    c = tk.Canvas(parent, width=width, height=height,
                  bg=parent.cget("bg"), highlightthickness=0, cursor="hand2")
    r = height // 2

    def draw(hover=False):
        c.delete("all")
        col = _darken(bg_color, 18) if hover else bg_color
        c.create_arc(0, 0, 2*r, height, start=90, extent=180, fill=col, outline=col)
        c.create_arc(width-2*r, 0, width, height, start=270, extent=180, fill=col, outline=col)
        c.create_rectangle(r, 0, width-r, height, fill=col, outline=col)
        c.create_text(width//2, height//2, text=text, font=font_obj,
                      fill=fg_color, anchor="center")

    draw()
    c.bind("<Button-1>", lambda _: cmd())
    c.bind("<Enter>",    lambda _: draw(True))
    c.bind("<Leave>",    lambda _: draw(False))
    return c


def checkmark_icon(parent, size=48, row_bg=GREEN):
    c = tk.Canvas(parent, width=size, height=size,
                  bg=row_bg, highlightthickness=0)
    c.create_oval(0, 0, size, size, fill="white", outline="white")
    c.create_text(size//2, size//2 + 1, text="✓",
                  font=("Helvetica", size//2, "bold"), fill=GREEN, anchor="center")
    return c


def pill_icon(parent, size=48, row_bg=LIGHT_GREY):
    c = tk.Canvas(parent, width=size, height=size,
                  bg=row_bg, highlightthickness=0)
    c.create_oval(0, 0, size, size, fill=ORANGE, outline=ORANGE)
    c.create_text(size//2, size//2, text="💊",
                  font=("Helvetica", size//3), anchor="center")
    return c


# ══════════════════════════════════════════════════════════════════════════════
# THELMA DEVICE WINDOW
# ══════════════════════════════════════════════════════════════════════════════

class ThelmaWindow:
    W  = 430
    H  = 870
    BW = 20    # bezel width
    CR = 26    # corner radius

    def __init__(self, root):
        self.root = root
        root.title("Thelma")
        root.configure(bg=DEVICE_BG)
        root.attributes("-fullscreen", True)
        root.attributes("-topmost", True)
        root.config(cursor="none")   # verberg muiscursor op touchscreen

        self.IW = self.W - 2 * self.BW
        self.IH = self.H - 2 * self.BW

        # Fonts
        self.f_time   = tkfont.Font(family="Helvetica", size=58, weight="bold")
        self.f_h2     = tkfont.Font(family="Helvetica", size=28, weight="bold")
        self.f_h3     = tkfont.Font(family="Helvetica", size=20, weight="bold")
        self.f_body   = tkfont.Font(family="Helvetica", size=16)
        self.f_body_b = tkfont.Font(family="Helvetica", size=16, weight="bold")
        self.f_small  = tkfont.Font(family="Helvetica", size=13)
        self.f_small_b= tkfont.Font(family="Helvetica", size=13, weight="bold")
        self.f_tiny   = tkfont.Font(family="Helvetica", size=11)
        self.f_btn    = tkfont.Font(family="Helvetica", size=15, weight="bold")
        self.f_logo   = tkfont.Font(family="Helvetica", size=17, weight="bold")
        self.f_start  = tkfont.Font(family="Helvetica", size=22, weight="bold")

        # Canvas: dark device body + white rounded screen
        self.cv = tk.Canvas(root, width=self.W, height=self.H,
                            bg=DEVICE_BG, highlightthickness=0)
        self.cv.pack()
        draw_rrect(self.cv, self.BW, self.BW,
                   self.W - self.BW, self.H - self.BW, self.CR, BG)

        self.frame = tk.Frame(self.cv, bg=BG, width=self.IW, height=self.IH)
        self.cv.create_window(self.BW, self.BW, anchor="nw", window=self.frame)

        self.current_screen  = None
        self._urgency_color  = GREEN
        self._countdown_job  = None

        state.on_change(self._on_state_change)

        # Show escape room start screen immediately
        self._show_escape_start()

        # Poll network events every 100 ms
        root.after(100, self._poll_events)

    # ── Internal helpers ───────────────────────────────────────────────────────

    def _clear(self):
        for w in self.frame.winfo_children():
            w.destroy()

    def _sep(self, parent, color=BORDER):
        tk.Frame(parent, bg=color, height=1).pack(fill="x")

    def _on_state_change(self):
        if   self.current_screen == "home":     self._refresh_home()
        elif self.current_screen == "dispense": self._show_dispense()
        elif self.current_screen == "escape_alarm" and state.roll_loaded:
            self._on_countdown_zero()

    def _poll_events(self):
        """Check for incoming network events and apply them."""
        try:
            while True:
                event = _event_q.get_nowait()
                try:
                    self._handle_network_event(event)
                except Exception as e:
                    print(f"[EVENT] fout bij verwerken {event}: {e}")
                    traceback.print_exc()
        except queue.Empty:
            pass
        self.root.after(100, self._poll_events)

    def _handle_network_event(self, data: dict):
        ev = data.get("event", "")

        if ev == "reset":
            self._reset_escape()
        elif ev == "success":
            if not state.patient_info_ready:
                state.patient_info_ready = True
                state.notify()
                if self.current_screen == "home":
                    self._show_home()
        elif ev == "load_roll":
            if not state.roll_loaded:
                self.load_roll()
        elif ev == "press_button":
            self.simulate_press_button()
        elif ev == "take_sachet":
            self.simulate_take_sachet()
        elif ev in ("dispense_green", "dispense_yellow", "dispense_red"):
            self.trigger_dispense(ev.split("_", 1)[1])

    # ── ESCAPE START screen ────────────────────────────────────────────────────

    def _show_escape_start(self):
        self.current_screen = "escape_start"
        self._clear()
        f = self.frame
        f.pack_propagate(False)

        # Logo
        tk.Label(f, text="Thelma", font=self.f_logo, bg=BG, fg=DARK_TEXT).pack(pady=(70, 4))
        tk.Label(f, text="E S C A P E   R O O M", font=self.f_small_b,
                 bg=BG, fg=GREY_TEXT).pack()

        # Device icon
        tk.Label(f, text="💊", font=("Helvetica", 70), bg=BG).pack(pady=(40, 40))

        tk.Label(f, text="Druk op START om\nde simulatie te beginnen",
                 font=self.f_small, bg=BG, fg=GREY_TEXT, justify="center").pack()

        # START button
        btn_f = tk.Frame(f, bg=BG)
        btn_f.pack(pady=(30, 0))
        pill_btn(btn_f, "▶   START", GREEN, "white",
                 self.f_start, self._start_escape, width=280, height=66).pack()

        # Bottom branding block
        bottom_f = tk.Frame(f, bg=BG)
        bottom_f.pack(side="bottom", pady=(0, 22))

        # TSC logo
        logo = tk.Frame(bottom_f, bg=BG)
        logo.pack()
        tk.Label(logo, text=" TSC ", font=self.f_small_b,
                 bg=ORANGE, fg="white", padx=6, pady=5).pack(side="left")
        tk.Label(logo, text="  Connected\n  Care",
                 font=self.f_small, bg=BG, fg=DARK_TEXT, justify="left").pack(side="left", padx=6)

        # Separator
        tk.Frame(bottom_f, bg=BORDER, height=1).pack(fill="x", pady=(10, 6))

        # Sponsor
        tk.Label(bottom_f, text="Mede mogelijk gemaakt door",
                 font=self.f_tiny, bg=BG, fg=GREY_TEXT).pack()
        tk.Label(bottom_f, text="✚  Apotheek Pijnacker Centrum",
                 font=self.f_small_b, bg=BG, fg="#2E7D32").pack(pady=(2, 0))

    def _start_escape(self):
        state.escape_active       = True
        state.countdown_remaining = state.countdown_total
        state.patient_info_ready  = False   # hidden until other device triggers
        state.roll_loaded         = False
        state.dispense_state      = "idle"
        state.dispense_index      = None
        state.escape_complete     = False
        state.notify()

        # Send trigger to other device
        send_trigger({"event": "game_start"})

        self._show_home()
        self._tick_countdown()

    # ── HOME (escape room countdown mode) ─────────────────────────────────────

    def _show_home(self):
        self.current_screen = "home"
        self._clear()
        f = self.frame
        f.pack_propagate(False)

        # Signal bar top-right
        top = tk.Frame(f, bg=BG)
        top.pack(fill="x", padx=18, pady=(14, 0))
        tk.Label(top, text="▂▄▆█", font=self.f_tiny, bg=BG, fg=DARK_TEXT).pack(side="right")

        # ── Countdown (replaces clock) ──
        cd = _countdown_str(state.countdown_remaining)
        col = RED if state.countdown_remaining <= 30 else \
              ORANGE if state.countdown_remaining <= 60 else DARK_TEXT
        self._cd_lbl = tk.Label(f, text=cd, font=self.f_time, bg=BG, fg=col)
        self._cd_lbl.pack()

        # Patient name (or "Laden..." spinner)
        name_text = state.patient_name if state.patient_info_ready else "Laden..."
        name_fg   = DARK_TEXT if state.patient_info_ready else GREY_TEXT
        self._name_lbl = tk.Label(f, text=name_text, font=self.f_body,
                                  bg=BG, fg=name_fg)
        self._name_lbl.pack()

        if state.patient_info_ready and state.roll_loaded and state.schedule:
            # Progress bar
            pb = tk.Frame(f, bg=BG)
            pb.pack(fill="x", padx=18, pady=(10, 0))
            tk.Label(pb, text="💊", font=("Helvetica", 14), bg=BG).pack(side="left")
            taken, total = state.taken_count, len(state.schedule)
            bar_bg = tk.Frame(pb, bg="#E0E0E0", height=10)
            bar_bg.pack(side="left", fill="x", expand=True, padx=8)
            bar_bg.pack_propagate(False)
            if total:
                tk.Frame(bar_bg, bg=GREEN, height=10).place(
                    x=0, y=0, relwidth=taken/total, relheight=1)
            tk.Label(pb, text=f"{taken}/{total}", font=self.f_tiny,
                     bg=BG, fg=DARK_TEXT).pack(side="left")

        # Day nav
        nav = tk.Frame(f, bg=BG)
        nav.pack(fill="x", padx=14, pady=(10, 0))
        tk.Label(nav, text="‹", font=self.f_body_b, bg=BG, fg=NAV_ARROW).pack(side="left")
        tk.Label(nav, text="Vandaag", font=self.f_body, bg=BG, fg=DARK_TEXT).pack(side="left", expand=True)
        tk.Label(nav, text="›", font=self.f_body_b, bg=BG, fg=NAV_ARROW).pack(side="right")

        self._sep(f)
        tk.Frame(f, bg=BG, height=4).pack()

        if not state.patient_info_ready:
            self._home_loading(f)
        elif not state.roll_loaded:
            self._home_no_roll(f)
        else:
            for i, slot in enumerate(state.schedule):
                self._schedule_row(f, i, slot)

        # MENU
        bottom = tk.Frame(f, bg=BG)
        bottom.pack(side="bottom", pady=20)
        pill_btn(bottom, "≡   MENU", BLUE_BTN, "white",
                 self.f_btn, self._show_menu, width=290, height=58).pack()

    def _refresh_home(self):
        """Lightweight refresh: update countdown label and name without full redraw."""
        if self.current_screen != "home":
            return
        try:
            cd = _countdown_str(state.countdown_remaining)
            col = RED if state.countdown_remaining <= 30 else \
                  ORANGE if state.countdown_remaining <= 60 else DARK_TEXT
            if hasattr(self, "_cd_lbl") and self._cd_lbl.winfo_exists():
                self._cd_lbl.config(text=cd, fg=col)
            if hasattr(self, "_name_lbl") and self._name_lbl.winfo_exists():
                name_text = state.patient_name if state.patient_info_ready else "Laden..."
                name_fg   = DARK_TEXT if state.patient_info_ready else GREY_TEXT
                self._name_lbl.config(text=name_text, fg=name_fg)
        except tk.TclError:
            pass

    def _home_loading(self, parent):
        tk.Label(parent, text="\n⏳",
                 font=("Helvetica", 52), bg=BG).pack(pady=(8, 0))
        tk.Label(parent, text="Wacht op informatie...",
                 font=self.f_h3, bg=BG, fg=GREY_TEXT, justify="center").pack(pady=(6, 4))
        tk.Label(parent, text="Het andere apparaat stuurt\nde patiëntgegevens.",
                 font=self.f_small, bg=BG, fg=GREY_TEXT, justify="center").pack()

    def _home_no_roll(self, parent):
        tk.Label(parent, text="\n🧻", font=("Helvetica", 52), bg=BG).pack(pady=(8, 0))
        tk.Label(parent, text="Geen medicatierol\naanwezig",
                 font=self.f_h3, bg=BG, fg=DARK_TEXT, justify="center").pack(pady=(6, 4))
        tk.Label(parent, text="Plaats een nieuwe medicatierol\nin de Thelma",
                 font=self.f_small, bg=BG, fg=GREY_TEXT, justify="center").pack()
        btn_f = tk.Frame(parent, bg=BG)
        btn_f.pack(pady=(24, 0))
        pill_btn(btn_f, "Deksel ontgrendelen", ORANGE, DARK_TEXT,
                 self.f_btn, self._unlock_lid, width=300, height=54).pack()

    def _schedule_row(self, parent, i, slot):
        taken  = slot.get("taken", False)
        row_bg = GREEN if taken else LIGHT_GREY
        fg     = "white" if taken else DARK_TEXT
        sub_fg = "white" if taken else GREY_TEXT

        row = tk.Frame(parent, bg=row_bg, cursor="hand2")
        row.pack(fill="x")
        inner = tk.Frame(row, bg=row_bg)
        inner.pack(fill="x")

        ico = checkmark_icon(inner, size=48, row_bg=row_bg) if taken \
              else pill_icon(inner, size=48, row_bg=row_bg)
        ico.pack(side="left", padx=(14, 10), pady=16)

        info = tk.Frame(inner, bg=row_bg)
        info.pack(side="left", fill="both", expand=True)
        tk.Label(info, text=slot["time"], font=self.f_h2,
                 bg=row_bg, fg=fg).pack(anchor="w", pady=(14, 0))
        dh, dm = _countdown_hm(slot["time"])
        tk.Label(info, text=f"⊙ {dh} Uur {dm} Min",
                 font=self.f_tiny, bg=row_bg, fg=sub_fg).pack(anchor="w")
        tk.Frame(info, bg=row_bg, height=14).pack()

        tk.Label(inner, text="›", font=self.f_h3,
                 bg=row_bg, fg=sub_fg, padx=12).pack(side="right")

        for w in (row, inner, ico, info):
            w.bind("<Button-1>", lambda e, idx=i: self._show_detail(idx))

        tk.Frame(parent, bg="white" if taken else BORDER, height=1).pack(fill="x")

    # ── Countdown timer ────────────────────────────────────────────────────────

    def _tick_countdown(self):
        if not state.escape_active or state.escape_complete:
            return
        if state.countdown_remaining > 0:
            state.countdown_remaining -= 1
            self._refresh_home()
            # Colour warning: rebuild home when crossing thresholds to change text colour
            if state.countdown_remaining in (60, 30):
                if self.current_screen == "home":
                    self._show_home()
            self._countdown_job = self.root.after(1000, self._tick_countdown)
        else:
            self._on_countdown_zero()

    def _on_countdown_zero(self):
        """At 00:00 — auto-dispense if possible, otherwise alarm."""
        if not state.roll_loaded:
            self._show_escape_alarm()
            return
        idx = next((i for i, s in enumerate(state.schedule) if not s.get("taken")), None)
        if idx is None and state.schedule:
            idx = 0
        if idx is None:
            # No schedule yet — still dispense as "slot 0"
            state.schedule = [{"time": "00:00", "medicines": [], "taken": False}]
            idx = 0
        state.dispense_index = idx
        state.dispense_state = "green"
        self._urgency_color  = GREEN
        state.notify()
        self._show_dispense()

    def _show_escape_alarm(self):
        """Roll not loaded when countdown hit zero."""
        self.current_screen = "escape_alarm"
        self._clear()
        f = self.frame
        f.pack_propagate(False)
        tk.Label(f, text="⚠", font=("Helvetica", 72), bg=BG, fg=RED).pack(pady=(80, 10))
        tk.Label(f, text="Geen medicatierol\naanwezig!",
                 font=self.f_h2, bg=BG, fg=RED, justify="center").pack()
        tk.Label(f, text="Laad de medicatierol\nom de medicatie uit te geven.",
                 font=self.f_small, bg=BG, fg=GREY_TEXT, justify="center").pack(pady=8)
        # Alarm flash
        self._flash_alarm(f, True)

    def _flash_alarm(self, frame, red_bg):
        try:
            if self.current_screen != "escape_alarm":
                return
            col = "#FFE0E0" if red_bg else BG
            frame.config(bg=col)
            for w in frame.winfo_children():
                try: w.config(bg=col)
                except Exception: pass
            self.root.after(600, lambda: self._flash_alarm(frame, not red_bg))
        except tk.TclError:
            pass

    # ── DETAIL ─────────────────────────────────────────────────────────────────

    def _show_detail(self, idx):
        self.current_screen = "detail"
        self._clear()
        f = self.frame
        f.pack_propagate(False)
        slot = state.schedule[idx]

        top = tk.Frame(f, bg=BG)
        top.pack(fill="x", padx=18, pady=(14, 0))
        tk.Label(top, text="▂▄▆█", font=self.f_tiny, bg=BG, fg=DARK_TEXT).pack(side="right")
        tk.Label(f, text=_countdown_str(state.countdown_remaining),
                 font=self.f_time, bg=BG, fg=DARK_TEXT).pack()
        tk.Label(f, text=state.patient_name, font=self.f_body, bg=BG, fg=DARK_TEXT).pack()

        pb = tk.Frame(f, bg=BG)
        pb.pack(fill="x", padx=18, pady=(8, 0))
        tk.Label(pb, text="💊", font=("Helvetica", 13), bg=BG).pack(side="left")
        tk.Label(pb, text=f"  {state.taken_count}/{len(state.schedule)}",
                 font=self.f_tiny, bg=BG, fg=DARK_TEXT).pack(side="left")

        self._sep(f)
        tk.Frame(f, bg=BG, height=4).pack()

        row = tk.Frame(f, bg=LIGHT_GREY)
        row.pack(fill="x")
        c = tk.Canvas(row, width=42, height=42, bg=LIGHT_GREY, highlightthickness=0)
        c.pack(side="left", padx=(14, 10), pady=14)
        c.create_oval(0, 0, 42, 42, fill=GREEN, outline=GREEN)
        c.create_text(21, 21, text="⬇", font=("Helvetica", 17, "bold"),
                      fill="white", anchor="center")
        dh, dm = _countdown_hm(slot["time"])
        inf = tk.Frame(row, bg=LIGHT_GREY)
        inf.pack(side="left")
        tk.Label(inf, text=slot["time"], font=self.f_h2,
                 bg=LIGHT_GREY, fg=DARK_TEXT).pack(anchor="w", pady=(10, 0))
        tk.Label(inf, text=f"⊙ {dh} Uur {dm} Min",
                 font=self.f_tiny, bg=LIGHT_GREY, fg=GREY_TEXT).pack(anchor="w")
        tk.Frame(inf, bg=LIGHT_GREY, height=10).pack()

        self._sep(f)
        med_f = tk.Frame(f, bg=BG)
        med_f.pack(fill="x", padx=18, pady=(14, 0))
        for med in slot.get("medicines", []):
            tk.Label(med_f, text=med, font=self.f_small,
                     bg=BG, fg=DARK_TEXT).pack(anchor="w", pady=1)

        bottom = tk.Frame(f, bg=BG)
        bottom.pack(side="bottom", pady=20)
        pill_btn(bottom, "SLUITEN", BLUE_BTN, "white",
                 self.f_btn, self._go_home, width=210, height=52).pack()

    # ── MENU ───────────────────────────────────────────────────────────────────

    def _show_menu(self):
        self.current_screen = "menu"
        self._clear()
        f = self.frame
        f.pack_propagate(False)
        tk.Frame(f, bg=BG).pack(expand=True)
        for label, color, cmd in [
            ("Deksel ontgrendelen",       DARK_TEXT, self._unlock_lid),
            ("Vroegtijdige uitgifte",     DARK_TEXT, self._early_dispense),
            ("Apparaatinformatie",        DARK_TEXT, self._show_device_info),
            ("Geavanceerde instellingen", RED,       self._show_advanced),
        ]:
            self._sep(f)
            btn = tk.Label(f, text=label, font=self.f_body_b,
                           bg=BG, fg=color, pady=22, cursor="hand2")
            btn.pack(fill="x")
            btn.bind("<Button-1>", lambda e, c=cmd: c())
        self._sep(f)
        tk.Frame(f, bg=BG).pack(expand=True)
        bottom = tk.Frame(f, bg=BG)
        bottom.pack(side="bottom", pady=20)
        pill_btn(bottom, "SLUITEN", BLUE_BTN, "white",
                 self.f_btn, self._go_home, width=210, height=52).pack()

    def _unlock_lid(self):
        self.current_screen = "unlock"
        self._clear()
        f = self.frame
        f.pack_propagate(False)
        tk.Label(f, text="🔓", font=("Helvetica", 60), bg=BG).pack(pady=(130, 16))
        tk.Label(f, text="Deksel ontgrendeld", font=self.f_h3, bg=BG, fg=DARK_TEXT).pack()
        tk.Label(f, text="Sluit het deksel na het plaatsen\nvan de medicatierol.",
                 font=self.f_small, bg=BG, fg=GREY_TEXT, justify="center").pack(pady=8)
        bottom = tk.Frame(f, bg=BG)
        bottom.pack(side="bottom", pady=20)
        pill_btn(bottom, "SLUITEN", BLUE_BTN, "white",
                 self.f_btn, self._go_home, width=210, height=52).pack()

    def _early_dispense(self):
        if not state.roll_loaded:
            self._go_home(); return
        idx = next((i for i, s in enumerate(state.schedule) if not s.get("taken")), None)
        if idx is None:
            self._go_home(); return
        state.dispense_index = idx
        state.dispense_state = "green"
        self._urgency_color  = GREEN
        state.notify()
        self._show_dispense()

    def _show_device_info(self):
        self.current_screen = "info"
        self._clear()
        f = self.frame
        f.pack_propagate(False)
        tk.Label(f, text="Apparaatinformatie", font=self.f_h3,
                 bg=BG, fg=DARK_TEXT).pack(pady=(50, 16))
        my_ip = local_ip()
        for k, v in [("Serienummer","TH-2024-00421"),("Firmware","3.1.4"),
                     ("Batterij","100%"),("Verbinding","4G  ▂▄▆█"),
                     ("IP-adres", my_ip),
                     ("Luistert op",f":{CONFIG['listen_port']}")]:
            self._sep(f)
            r = tk.Frame(f, bg=LIGHT_GREY)
            r.pack(fill="x")
            tk.Label(r, text=k, font=self.f_small, bg=LIGHT_GREY,
                     fg=GREY_TEXT, anchor="w", padx=18, pady=11, width=16).pack(side="left")
            tk.Label(r, text=v, font=self.f_small_b, bg=LIGHT_GREY,
                     fg=DARK_TEXT, padx=18).pack(side="right")
        self._sep(f)
        bottom = tk.Frame(f, bg=BG)
        bottom.pack(side="bottom", pady=20)
        pill_btn(bottom, "SLUITEN", BLUE_BTN, "white",
                 self.f_btn, self._go_home, width=210, height=52).pack()

    def _show_advanced(self):
        self.current_screen = "advanced"
        self._clear()
        f = self.frame
        f.pack_propagate(False)
        tk.Label(f, text="⚠", font=("Helvetica", 56), bg=BG, fg=RED).pack(pady=(90, 10))
        tk.Label(f, text="Geavanceerde\ninstellingen",
                 font=self.f_h3, bg=BG, fg=RED, justify="center").pack()
        tk.Label(f, text="Alleen voor technisch personeel.",
                 font=self.f_small, bg=BG, fg=GREY_TEXT).pack(pady=8)
        bottom = tk.Frame(f, bg=BG)
        bottom.pack(side="bottom", pady=20)
        pill_btn(bottom, "SLUITEN", BLUE_BTN, "white",
                 self.f_btn, self._go_home, width=210, height=52).pack()

    # ── DISPENSE ───────────────────────────────────────────────────────────────

    def _show_dispense(self):
        self.current_screen = "dispense"
        self._clear()
        f = self.frame
        f.pack_propagate(False)

        ds   = state.dispense_state
        idx  = state.dispense_index
        slot = (state.schedule[idx] if idx is not None and idx < len(state.schedule)
                else {"time": "00:00", "medicines": []})
        slot_time = slot["time"]
        dh, dm = _countdown_hm(slot_time)
        uc = self._urgency_color

        if ds == "ready":
            tk.Label(f, text="Medicatie\nuitgifte",
                     font=self.f_h2, bg=BG, fg=DARK_TEXT, justify="center").pack(pady=(28, 14))
            card = tk.Frame(f, bg=LIGHT_GREY)
            card.pack(fill="x", padx=20, pady=(0, 6))
            c = tk.Canvas(card, width=42, height=42, bg=LIGHT_GREY, highlightthickness=0)
            c.pack(side="left", padx=(14, 10), pady=12)
            c.create_oval(0, 0, 42, 42, fill=GREEN, outline=GREEN)
            c.create_text(21, 21, text="⬇", font=("Helvetica", 17, "bold"),
                          fill="white", anchor="center")
            inf = tk.Frame(card, bg=LIGHT_GREY)
            inf.pack(side="left")
            tk.Label(inf, text=slot_time, font=self.f_h2,
                     bg=LIGHT_GREY, fg=DARK_TEXT).pack(anchor="w", pady=(8, 0))
            tk.Label(inf, text=f"⊙ {dh} Uur {dm} Min",
                     font=self.f_tiny, bg=LIGHT_GREY, fg=GREY_TEXT).pack(anchor="w")
            tk.Frame(inf, bg=LIGHT_GREY, height=10).pack()
            bot = tk.Frame(f, bg=uc)
            bot.pack(fill="both", expand=True)
            tk.Label(bot, text="Pak uw medicatie",
                     font=self.f_body_b, bg=uc, fg="white").pack(pady=(28, 0))
            tk.Label(bot, text="⬇", font=("Helvetica", 72), bg=uc, fg="white").pack()
        else:
            uc = {"green": GREEN, "yellow": ORANGE, "red": RED}.get(ds, GREEN)
            self._urgency_color = uc
            label_map = {"green":  "Druk op de groene knop",
                         "yellow": "Druk op de gele knop",
                         "red":    "Druk op de rode knop"}
            top = tk.Frame(f, bg=uc, height=360)
            top.pack(fill="x")
            top.pack_propagate(False)
            tk.Label(top, text=label_map.get(ds, ""),
                     font=self.f_small_b, bg=uc, fg="white").pack(pady=(28, 0))
            tk.Label(top, text="⬆", font=("Helvetica", 100), bg=uc, fg="white").pack(expand=True)
            bot = tk.Frame(f, bg=BG)
            bot.pack(fill="both", expand=True)
            tk.Label(bot, text="Medicatie\nuitgifte",
                     font=self.f_h2, bg=BG, fg=DARK_TEXT, justify="center").pack(pady=(16, 8))
            card = tk.Frame(bot, bg=LIGHT_GREY)
            card.pack(fill="x", padx=20)
            c2 = tk.Canvas(card, width=42, height=42, bg=LIGHT_GREY, highlightthickness=0)
            c2.pack(side="left", padx=(14, 10), pady=12)
            c2.create_oval(0, 0, 42, 42, fill=ORANGE, outline=ORANGE)
            c2.create_text(21, 21, text="💊", font=("Helvetica", 14), anchor="center")
            inf2 = tk.Frame(card, bg=LIGHT_GREY)
            inf2.pack(side="left")
            tk.Label(inf2, text=slot_time, font=self.f_h2,
                     bg=LIGHT_GREY, fg=DARK_TEXT).pack(anchor="w", pady=(8, 0))
            tk.Label(inf2, text=f"⊙ {dh} Uur {dm} Min",
                     font=self.f_tiny, bg=LIGHT_GREY, fg=GREY_TEXT).pack(anchor="w")
            tk.Frame(inf2, bg=LIGHT_GREY, height=10).pack()

    def _cancel_dispense(self):
        state.dispense_state = "idle"
        state.dispense_index = None
        state.notify()
        self._go_home()

    def _confirm_taken(self):
        idx = state.dispense_index
        if idx is not None and idx < len(state.schedule):
            state.schedule[idx]["taken"] = True
        state.dispense_state  = "idle"
        state.dispense_index  = None
        state.escape_complete = True
        state.notify()
        send_trigger({"event": "game_complete"})
        self._show_escape_complete()

    def _show_escape_complete(self):
        self.current_screen = "complete"
        self._clear()
        f = self.frame
        f.pack_propagate(False)
        c = tk.Canvas(f, width=100, height=100, bg=BG, highlightthickness=0)
        c.pack(pady=(90, 14))
        c.create_oval(2, 2, 98, 98, fill=GREEN, outline=GREEN)
        c.create_text(50, 50, text="✓", font=("Helvetica", 44, "bold"),
                      fill="white", anchor="center")
        tk.Label(f, text="Medicatie\nsuccesvol ingenomen!",
                 font=self.f_h2, bg=BG, fg=GREEN, justify="center").pack(pady=(0, 10))
        tk.Label(f, text="🎉  Escape room voltooid!  🎉",
                 font=self.f_body_b, bg=BG, fg=DARK_TEXT).pack()
        tk.Label(f, text="Goed gedaan!",
                 font=self.f_small, bg=BG, fg=GREY_TEXT).pack(pady=6)
        bottom = tk.Frame(f, bg=BG)
        bottom.pack(side="bottom", pady=24)
        pill_btn(bottom, "↺   Opnieuw starten", BLUE_BTN, "white",
                 self.f_btn, self._reset_escape, width=270, height=52).pack()

    def _reset_escape(self):
        if self._countdown_job:
            self.root.after_cancel(self._countdown_job)
            self._countdown_job = None
        state.escape_active       = False
        state.escape_complete     = False
        state.countdown_remaining = state.countdown_total
        state.patient_info_ready  = False   # hidden again on reset
        state.roll_loaded         = False
        state.dispense_state      = "idle"
        state.dispense_index      = None
        state.notify()
        self._show_escape_start()

    # ── Navigation helpers ─────────────────────────────────────────────────────

    def _go_home(self): self._show_home()

    # ── Public API (for admin) ─────────────────────────────────────────────────

    def load_roll(self):
        state.roll_loaded = True
        state.notify()
        self._show_roll_success()

    def _show_roll_success(self):
        prev = self.current_screen
        self.current_screen = "roll_success"
        self._clear()
        f = self.frame
        f.pack_propagate(False)
        c = tk.Canvas(f, width=90, height=90, bg=BG, highlightthickness=0)
        c.pack(pady=(130, 12))
        c.create_oval(2, 2, 88, 88, fill=GREEN, outline=GREEN)
        c.create_text(45, 45, text="✓", font=("Helvetica", 36, "bold"),
                      fill="white", anchor="center")
        tk.Label(f, text="Medicatierol\nsuccesvol geplaatst",
                 font=self.f_h3, bg=BG, fg=DARK_TEXT, justify="center").pack()
        # If we were waiting on the alarm, auto-dispense now
        if prev == "escape_alarm":
            self.root.after(1500, self._on_countdown_zero)
        else:
            self.root.after(2000, self._go_home)

    def trigger_dispense(self, urgency="green"):
        if not state.roll_loaded: return
        idx = next((i for i, s in enumerate(state.schedule) if not s.get("taken")), None)
        if idx is None and state.schedule:
            idx = 0
        if idx is None:
            state.schedule = [{"time": "00:00", "medicines": [], "taken": False}]
            idx = 0
        state.dispense_index = idx
        state.dispense_state = urgency
        self._urgency_color  = {"green": GREEN, "yellow": ORANGE, "red": RED}.get(urgency, GREEN)
        state.notify()
        self._show_dispense()

    def simulate_press_button(self):
        if state.dispense_state in ("green", "yellow", "red"):
            state.dispense_state = "ready"
            state.notify()
            self._show_dispense()

    def simulate_take_sachet(self):
        if state.dispense_state == "ready":
            self._confirm_taken()

    def reveal_patient_info(self):
        """Called from admin panel to simulate the other device trigger."""
        _event_q.put({"event": "success"})


# ══════════════════════════════════════════════════════════════════════════════
# ADMIN / BACKEND WINDOW
# ══════════════════════════════════════════════════════════════════════════════

class AdminWindow:
    def __init__(self, root, thelma: ThelmaWindow):
        self.root   = root
        self.thelma = thelma
        root.title("Admin – Thelma Beheer")
        root.geometry("500x830+530+40")
        root.resizable(False, False)
        root.configure(bg="#1C1C2E")

        self.f_title  = tkfont.Font(family="Helvetica", size=14, weight="bold")
        self.f_label  = tkfont.Font(family="Helvetica", size=11)
        self.f_label_b= tkfont.Font(family="Helvetica", size=11, weight="bold")
        self.f_cd     = tkfont.Font(family="Helvetica", size=20, weight="bold")
        self.f_log    = tkfont.Font(family="Courier New", size=10)

        self._build()
        state.on_change(self._refresh)
        self._tick_admin()

    def _build(self):
        r = self.root
        tk.Label(r, text="Thelma Beheer Panel",
                 font=self.f_title, bg="#1C1C2E", fg="white").pack(pady=(12, 4))
        tk.Frame(r, bg="#3A3A5C", height=1).pack(fill="x", padx=14)

        # ── Escape room status ─────────────────────────────────────────────────
        ef = tk.LabelFrame(r, text=" Escape Room Status ", font=self.f_label,
                           bg="#1C1C2E", fg="#AAAACC", bd=1, relief="groove")
        ef.pack(fill="x", padx=14, pady=8)
        erow = tk.Frame(ef, bg="#1C1C2E")
        erow.pack(fill="x", padx=12, pady=6)
        self._status_lbl = tk.Label(erow, text="Wacht op start",
                                    font=self.f_label_b, bg="#1C1C2E", fg=GREY_TEXT)
        self._status_lbl.pack(side="left")
        self._cd_admin_lbl = tk.Label(erow, text="--:--",
                                      font=self.f_cd, bg="#1C1C2E", fg="white")
        self._cd_admin_lbl.pack(side="right")
        # Network info
        nrow = tk.Frame(ef, bg="#1C1C2E")
        nrow.pack(fill="x", padx=12, pady=(0, 8))
        my_ip = local_ip()
        tk.Label(nrow, text=f"Mijn IP: {my_ip}:{CONFIG['listen_port']}",
                 font=self.f_label, bg="#1C1C2E", fg="#7A8ACC").pack(side="left")
        tk.Label(nrow, text=f"→ {CONFIG['other_ip']}:{CONFIG['other_port']}",
                 font=self.f_label, bg="#1C1C2E", fg="#7A8ACC").pack(side="right")

        # ── Trigger van andere device ──────────────────────────────────────────
        pf = tk.LabelFrame(r, text=" Simuleer: Andere Device → Thelma ",
                           font=self.f_label, bg="#1C1C2E", fg="#AAAACC",
                           bd=1, relief="groove")
        pf.pack(fill="x", padx=14, pady=6)
        prow = tk.Frame(pf, bg="#1C1C2E")
        prow.pack(fill="x", padx=12, pady=8)
        tk.Label(prow, text="Andere device meldt 'gelukt':",
                 font=self.f_label, bg="#1C1C2E", fg="white").pack(side="left")
        tk.Button(prow, text="✅  Stuur trigger",
                  font=self.f_label_b, bg="#4A7C59", fg="white",
                  relief="flat", padx=10, pady=6, bd=0,
                  command=self._send_reveal_trigger).pack(side="right")

        # ── Status lights ──────────────────────────────────────────────────────
        lf = tk.LabelFrame(r, text=" Status Lampjes ", font=self.f_label,
                           bg="#1C1C2E", fg="#AAAACC", bd=1, relief="groove")
        lf.pack(fill="x", padx=14, pady=6)
        self._lights = {}
        self._extra  = {}
        for key, name, col in [("roll","Medicatierol","#555555"),
                                ("dispense","Uitgifte status","#555555"),
                                ("patient","Patiëntinfo","#555555"),
                                ("conn","Verbinding",GREEN)]:
            row = tk.Frame(lf, bg="#1C1C2E")
            row.pack(fill="x", padx=12, pady=3)
            cv = tk.Canvas(row, width=22, height=22, bg="#1C1C2E", highlightthickness=0)
            cv.pack(side="left")
            oid = cv.create_oval(2, 2, 20, 20, fill=col, outline="#111133")
            self._lights[key] = (cv, oid)
            tk.Label(row, text=name, font=self.f_label,
                     bg="#1C1C2E", fg="white").pack(side="left", padx=8)
            lbl = tk.Label(row, text="", font=self.f_label, bg="#1C1C2E", fg="#AAAACC")
            lbl.pack(side="right")
            self._extra[key] = lbl

        # ── Rol laden ──────────────────────────────────────────────────────────
        rf = tk.LabelFrame(r, text=" Medicatierol ", font=self.f_label,
                           bg="#1C1C2E", fg="#AAAACC", bd=1, relief="groove")
        rf.pack(fill="x", padx=14, pady=6)
        rrow = tk.Frame(rf, bg="#1C1C2E")
        rrow.pack(fill="x", padx=12, pady=8)
        tk.Label(rrow, text="Simuleer plaatsen medicatierol:",
                 font=self.f_label, bg="#1C1C2E", fg="white").pack(side="left")
        tk.Button(rrow, text="🧻  Rol Plaatsen",
                  font=self.f_label_b, bg=ORANGE, fg=DARK_TEXT,
                  relief="flat", padx=10, pady=6, bd=0,
                  command=self._load_roll).pack(side="right")

        # ── Uitgifte triggeren ─────────────────────────────────────────────────
        df = tk.LabelFrame(r, text=" Uitgifte Triggeren (handmatig) ",
                           font=self.f_label, bg="#1C1C2E", fg="#AAAACC",
                           bd=1, relief="groove")
        df.pack(fill="x", padx=14, pady=6)
        for label, urgency, col in [
            ("🟢  Groene knop (op tijd)",   "green",  GREEN),
            ("🟡  Gele knop (10 min laat)", "yellow", ORANGE),
            ("🔴  Rode knop (30 min laat)", "red",    RED),
        ]:
            tk.Button(df, text=label, font=self.f_label_b,
                      bg=col, fg="white", relief="flat", padx=10, pady=6, bd=0,
                      command=lambda u=urgency: self._trigger(u)
                      ).pack(fill="x", padx=12, pady=2)

        # ── Fysieke acties ─────────────────────────────────────────────────────
        af = tk.LabelFrame(r, text=" Fysieke Acties (patiënt) ", font=self.f_label,
                           bg="#1C1C2E", fg="#AAAACC", bd=1, relief="groove")
        af.pack(fill="x", padx=14, pady=6)
        arow = tk.Frame(af, bg="#1C1C2E")
        arow.pack(fill="x", padx=12, pady=8)
        tk.Button(arow, text="🔘  Druk knop",
                  font=self.f_label_b, bg="#4A7C59", fg="white",
                  relief="flat", padx=10, pady=8, bd=0,
                  command=self._press_button
                  ).pack(side="left", fill="x", expand=True, padx=(0, 4))
        tk.Button(arow, text="📦  Pak zakje",
                  font=self.f_label_b, bg=BLUE_BTN, fg="white",
                  relief="flat", padx=10, pady=8, bd=0,
                  command=self._take_sachet
                  ).pack(side="left", fill="x", expand=True, padx=(4, 0))
        # Reset
        tk.Button(af, text="↺  Reset / Opnieuw starten",
                  font=self.f_label, bg="#3A3A5C", fg="white",
                  relief="flat", padx=8, pady=4, bd=0,
                  command=self._reset).pack(pady=(0, 8))

        # ── Log ───────────────────────────────────────────────────────────────
        logf = tk.LabelFrame(r, text=" Logboek ", font=self.f_label,
                             bg="#1C1C2E", fg="#AAAACC", bd=1, relief="groove")
        logf.pack(fill="both", expand=True, padx=14, pady=6)
        self._log_box = tk.Text(logf, font=self.f_log, bg="#0D0D1A",
                                fg="#00FF88", height=6, bd=0,
                                relief="flat", state="disabled")
        self._log_box.pack(fill="both", expand=True, padx=4, pady=4)

        self._refresh()
        self._log(f"Admin panel gestart  |  IP: {local_ip()}:{CONFIG['listen_port']}")

    def _log(self, msg):
        ts = datetime.now().strftime("%H:%M:%S")
        self._log_box.config(state="normal")
        self._log_box.insert("end", f"[{ts}] {msg}\n")
        self._log_box.see("end")
        self._log_box.config(state="disabled")

    def _set_light(self, key, color):
        cv, oid = self._lights[key]
        cv.itemconfig(oid, fill=color)

    def _tick_admin(self):
        """Update countdown display in admin panel every second."""
        try:
            if state.escape_active and not state.escape_complete:
                self._cd_admin_lbl.config(
                    text=_countdown_str(state.countdown_remaining),
                    fg=RED if state.countdown_remaining <= 30 else
                       ORANGE if state.countdown_remaining <= 60 else "white")
            elif state.escape_complete:
                self._cd_admin_lbl.config(text="✓ Klaar!", fg=GREEN)
            else:
                self._cd_admin_lbl.config(text="--:--", fg="white")
        except tk.TclError:
            return
        self.root.after(500, self._tick_admin)

    def _refresh(self):
        # Status label
        if state.escape_complete:
            status, scol = "✓ Voltooid!", GREEN
        elif state.escape_active and state.countdown_remaining == 0:
            status, scol = "⏰ Uitgifte!", RED
        elif state.escape_active:
            status, scol = "⏱ Countdown actief", ORANGE
        else:
            status, scol = "Wacht op start", GREY_TEXT
        self._status_lbl.config(text=status, fg=scol)

        # Lights
        self._set_light("roll", GREEN if state.roll_loaded else "#555555")
        self._extra["roll"].config(
            text="geladen ✓" if state.roll_loaded else "niet geladen",
            fg=GREEN if state.roll_loaded else "#AAAACC")

        ds = state.dispense_state
        dc = {"idle":"#555555","green":GREEN,"yellow":ORANGE,
              "red":RED,"ready":GREEN}.get(ds,"#555555")
        self._set_light("dispense", dc)
        self._extra["dispense"].config(
            text={"idle":"inactief","green":"▶ groene knop","yellow":"▶ gele knop",
                  "red":"▶ rode knop","ready":"⬇ klaar om te pakken"}.get(ds, ds),
            fg=dc)

        self._set_light("patient", GREEN if state.patient_info_ready else "#555555")
        self._extra["patient"].config(
            text=state.patient_name if state.patient_info_ready else "niet ontvangen",
            fg=GREEN if state.patient_info_ready else "#AAAACC")

    def _send_reveal_trigger(self):
        """Simulate the other device sending its success signal to Thelma."""
        _event_q.put({"event": "success"})
        self._log("✅ Trigger ontvangen van andere device — patiëntgegevens zichtbaar.")

    def _load_roll(self):
        if state.roll_loaded:
            self._log("Medicatierol al geladen."); return
        self.thelma.root.after(0, self.thelma.load_roll)
        self._log("🧻 Medicatierol geplaatst.")

    def _trigger(self, urgency):
        if not state.roll_loaded:
            self._log("⚠ Geen medicatierol geladen!"); return
        self.thelma.root.after(0, lambda: self.thelma.trigger_dispense(urgency))
        nl = {"green":"groene","yellow":"gele","red":"rode"}[urgency]
        self._log(f"▶ Uitgifte → {nl} knop.")

    def _press_button(self):
        if state.dispense_state not in ("green", "yellow", "red"):
            self._log("⚠ Geen actieve uitgifte."); return
        self.thelma.root.after(0, self.thelma.simulate_press_button)
        self._log("🔘 Knop ingedrukt — zakje uitgegeven.")

    def _take_sachet(self):
        if state.dispense_state != "ready":
            self._log("⚠ Geen zakje klaar om te pakken."); return
        self.thelma.root.after(0, self.thelma.simulate_take_sachet)
        self._log("📦 Zakje gepakt — medicatie ingenomen. 🎉")

    def _reset(self):
        self.thelma.root.after(0, self.thelma._reset_escape)
        self._log("↺ Escape room gereset.")


# ══════════════════════════════════════════════════════════════════════════════

def main():
    _start_server()
    while True:
        try:
            root = tk.Tk()
            ThelmaWindow(root)
            root.mainloop()
            break  # normaal afgesloten via venster-sluitknop
        except Exception as e:
            print(f"[CRASH] tkinter: {e}")
            traceback.print_exc()
            print("[INFO] Herstarten over 3 seconden...")
            time.sleep(3)
    if _http_server:
        _http_server.shutdown()


if __name__ == "__main__":
    main()
