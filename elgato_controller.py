#!/usr/bin/env python3
"""
Elgato Key Light Controller
Requires: pip install zeroconf requests
Run with: python3 elgato_controller.py
"""

import tkinter as tk
from tkinter import ttk, messagebox
import threading
import requests
import json
import time
from zeroconf import ServiceBrowser, Zeroconf, ServiceStateChange


# ── Elgato API helpers ────────────────────────────────────────────────────────

def get_light_state(ip, port=9123):
    url = f"http://{ip}:{port}/elgato/lights"
    r = requests.get(url, timeout=3)
    r.raise_for_status()
    data = r.json()
    return data["lights"][0]  # first light in the group


def set_light_state(ip, on=None, brightness=None, temperature=None, port=9123):
    url = f"http://{ip}:{port}/elgato/lights"
    light = {}
    if on is not None:
        light["on"] = 1 if on else 0
    if brightness is not None:
        light["brightness"] = int(brightness)
    if temperature is not None:
        light["temperature"] = int(temperature)
    payload = {"numberOfLights": 1, "lights": [light]}
    r = requests.put(url, json=payload, timeout=3)
    r.raise_for_status()


def get_light_info(ip, port=9123):
    url = f"http://{ip}:{port}/elgato/accessory-info"
    r = requests.get(url, timeout=3)
    r.raise_for_status()
    return r.json()


# ── mDNS Discovery ────────────────────────────────────────────────────────────

class LightDiscovery:
    SERVICE_TYPE = "_elg._tcp.local."

    def __init__(self, callback):
        self.callback = callback
        self.zeroconf = Zeroconf()
        self.browser = None

    def start(self):
        self.browser = ServiceBrowser(
            self.zeroconf, self.SERVICE_TYPE, handlers=[self._on_service_change]
        )

    def _on_service_change(self, zeroconf, service_type, name, state_change):
        if state_change is ServiceStateChange.Added:
            info = zeroconf.get_service_info(service_type, name)
            if info:
                ip = ".".join(str(b) for b in info.addresses[0])
                port = info.port
                self.callback(name, ip, port)

    def stop(self):
        self.zeroconf.close()


# ── Per-light panel ───────────────────────────────────────────────────────────

class LightPanel(ttk.LabelFrame):
    # Elgato temperature range: 143 (6500K cool) to 344 (2900K warm)
    TEMP_MIN = 143
    TEMP_MAX = 344

    def __init__(self, parent, name, ip, port, on_change=None, **kwargs):
        super().__init__(parent, text=name, padding=12, **kwargs)
        self.ip = ip
        self.port = port
        self.on_change = on_change
        self._updating = False

        # State vars
        self.power_var = tk.BooleanVar()
        self.brightness_var = tk.IntVar(value=50)
        self.temp_var = tk.IntVar(value=200)

        self._build_ui()
        self.refresh()

    def _build_ui(self):
        # Power toggle
        power_row = ttk.Frame(self)
        power_row.pack(fill="x", pady=(0, 10))
        ttk.Label(power_row, text="Power", width=12).pack(side="left")
        self.power_btn = ttk.Checkbutton(
            power_row, text="On / Off", variable=self.power_var,
            command=self._on_power_toggle, style="Toggle.TCheckbutton"
        )
        self.power_btn.pack(side="left")

        # Brightness
        bri_row = ttk.Frame(self)
        bri_row.pack(fill="x", pady=4)
        ttk.Label(bri_row, text="Brightness", width=12).pack(side="left")
        self.bri_slider = ttk.Scale(
            bri_row, from_=0, to=100, orient="horizontal",
            variable=self.brightness_var, length=200,
            command=self._on_slider
        )
        self.bri_slider.pack(side="left", padx=(0, 8))
        self.bri_label = ttk.Label(bri_row, text="50%", width=5)
        self.bri_label.pack(side="left")

        # Color temperature
        temp_row = ttk.Frame(self)
        temp_row.pack(fill="x", pady=4)
        ttk.Label(temp_row, text="Temperature", width=12).pack(side="left")
        self.temp_slider = ttk.Scale(
            temp_row, from_=self.TEMP_MIN, to=self.TEMP_MAX, orient="horizontal",
            variable=self.temp_var, length=200,
            command=self._on_slider
        )
        self.temp_slider.pack(side="left", padx=(0, 8))
        self.temp_label = ttk.Label(temp_row, text="5000K", width=7)
        self.temp_label.pack(side="left")

        # Warm / Cool labels under slider
        range_row = ttk.Frame(self)
        range_row.pack(fill="x", padx=(100, 0))
        ttk.Label(range_row, text="Cool 6500K", foreground="#aaa", font=("", 8)).pack(side="left")
        ttk.Label(range_row, text="Warm 2900K", foreground="#aaa", font=("", 8)).pack(side="right", padx=(0, 40))

        # Status
        self.status_label = ttk.Label(self, text="", foreground="gray")
        self.status_label.pack(anchor="w", pady=(6, 0))

    # Elgato uses a "mired-like" value; convert to Kelvin for display
    def _temp_to_kelvin(self, t):
        return int(1_000_000 / t)

    def _kelvin_to_temp(self, k):
        return int(1_000_000 / k)

    def _on_power_toggle(self):
        self._send(on=self.power_var.get())

    def _on_slider(self, _=None):
        if self._updating:
            return
        bri = self.brightness_var.get()
        temp = self.temp_var.get()
        self.bri_label.config(text=f"{bri}%")
        self.temp_label.config(text=f"{self._temp_to_kelvin(temp)}K")
        self._send(brightness=bri, temperature=temp)
        if self.on_change:
            self.on_change(bri, temp)

    def _send(self, **kwargs):
        def do():
            try:
                set_light_state(self.ip, port=self.port, **kwargs)
                self.status_label.config(text="✓ Updated", foreground="green")
            except Exception as e:
                self.status_label.config(text=f"Error: {e}", foreground="red")
        threading.Thread(target=do, daemon=True).start()

    def refresh(self):
        def do():
            try:
                state = get_light_state(self.ip, self.port)
                self._updating = True
                self.power_var.set(bool(state.get("on", 0)))
                bri = int(state.get("brightness", 50))
                temp = int(state.get("temperature", 200))
                self.brightness_var.set(bri)
                self.temp_var.set(temp)
                self.bri_label.config(text=f"{bri}%")
                self.temp_label.config(text=f"{self._temp_to_kelvin(temp)}K")
                self.status_label.config(text="✓ Connected", foreground="green")
                self._updating = False
            except Exception as e:
                self.status_label.config(text=f"Cannot reach light: {e}", foreground="red")
                self._updating = False
        threading.Thread(target=do, daemon=True).start()

    def apply_master(self, brightness, temperature):
        self._updating = True
        self.brightness_var.set(brightness)
        self.temp_var.set(temperature)
        self.bri_label.config(text=f"{brightness}%")
        self.temp_label.config(text=f"{self._temp_to_kelvin(temperature)}K")
        self._updating = False
        self._send(brightness=brightness, temperature=temperature)


# ── Main window ───────────────────────────────────────────────────────────────

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Elgato Key Light Controller")
        self.resizable(False, False)
        self.configure(padx=16, pady=16)

        self.panels: dict[str, LightPanel] = {}
        self._master_updating = False

        self._build_ui()
        self._start_discovery()

    def _build_ui(self):
        # ── Header ──
        hdr = ttk.Frame(self)
        hdr.pack(fill="x", pady=(0, 12))
        ttk.Label(hdr, text="Elgato Key Light Controller", font=("", 14, "bold")).pack(side="left")
        self.scan_btn = ttk.Button(hdr, text="Scan again", command=self._rescan)
        self.scan_btn.pack(side="right")

        # ── Scanning notice ──
        self.scan_label = ttk.Label(self, text="Scanning for lights…", foreground="gray")
        self.scan_label.pack(anchor="w")

        # ── Manual add ──
        manual = ttk.LabelFrame(self, text="Add light manually", padding=10)
        manual.pack(fill="x", pady=(8, 0))
        row = ttk.Frame(manual)
        row.pack(fill="x")
        ttk.Label(row, text="IP:").pack(side="left")
        self.ip_entry = ttk.Entry(row, width=16)
        self.ip_entry.pack(side="left", padx=4)
        ttk.Label(row, text="Port:").pack(side="left")
        self.port_entry = ttk.Entry(row, width=6)
        self.port_entry.insert(0, "9123")
        self.port_entry.pack(side="left", padx=4)
        ttk.Button(row, text="Add", command=self._add_manual).pack(side="left", padx=4)

        # ── Master control ──
        self.master_frame = ttk.LabelFrame(self, text="Master control (sync all lights)", padding=12)

        bri_row = ttk.Frame(self.master_frame)
        bri_row.pack(fill="x", pady=4)
        ttk.Label(bri_row, text="Brightness", width=12).pack(side="left")
        self.master_bri = tk.IntVar(value=50)
        ttk.Scale(
            bri_row, from_=0, to=100, orient="horizontal",
            variable=self.master_bri, length=200,
            command=self._on_master
        ).pack(side="left", padx=(0, 8))
        self.master_bri_lbl = ttk.Label(bri_row, text="50%", width=5)
        self.master_bri_lbl.pack(side="left")

        temp_row = ttk.Frame(self.master_frame)
        temp_row.pack(fill="x", pady=4)
        ttk.Label(temp_row, text="Temperature", width=12).pack(side="left")
        self.master_temp = tk.IntVar(value=200)
        ttk.Scale(
            temp_row, from_=LightPanel.TEMP_MIN, to=LightPanel.TEMP_MAX, orient="horizontal",
            variable=self.master_temp, length=200,
            command=self._on_master
        ).pack(side="left", padx=(0, 8))
        self.master_temp_lbl = ttk.Label(temp_row, text="5000K", width=7)
        self.master_temp_lbl.pack(side="left")

        # All on / all off
        btn_row = ttk.Frame(self.master_frame)
        btn_row.pack(fill="x", pady=(8, 0))
        ttk.Button(btn_row, text="All On",  command=lambda: self._all_power(True)).pack(side="left", padx=(0, 6))
        ttk.Button(btn_row, text="All Off", command=lambda: self._all_power(False)).pack(side="left")

        # ── Light panels container ──
        self.lights_frame = ttk.Frame(self)
        self.lights_frame.pack(fill="both", expand=True, pady=(8, 0))

    def _on_master(self, _=None):
        if self._master_updating:
            return
        bri = self.master_bri.get()
        temp = self.master_temp.get()
        self.master_bri_lbl.config(text=f"{bri}%")
        self.master_temp_lbl.config(text=f"{int(1_000_000/temp)}K")
        for panel in self.panels.values():
            panel.apply_master(bri, temp)

    def _all_power(self, state):
        for panel in self.panels.values():
            panel.power_var.set(state)
            panel._send(on=state)

    def _add_light(self, name, ip, port):
        if ip in self.panels:
            return
        self.scan_label.config(text=f"Found {len(self.panels)+1} light(s)")
        panel = LightPanel(
            self.lights_frame, name, ip, port,
            on_change=self._sync_master_labels
        )
        panel.pack(fill="x", pady=(0, 8))
        self.panels[ip] = panel

        # Show master control once we have 2+ lights, then sync its state
        if len(self.panels) >= 2:
            self.master_frame.pack(fill="x", pady=(8, 0), before=self.lights_frame)
            # Wait for all panel refresh() calls to complete, then init master
            self.after(1500, self._init_master_from_lights)

    def _init_master_from_lights(self):
        """Set master sliders to the average of all lights' current live state."""
        values = [
            (p.brightness_var.get(), p.temp_var.get())
            for p in self.panels.values()
        ]
        if not values:
            return
        avg_bri  = round(sum(v[0] for v in values) / len(values))
        avg_temp = round(sum(v[1] for v in values) / len(values))
        self._master_updating = True
        self.master_bri.set(avg_bri)
        self.master_temp.set(avg_temp)
        self.master_bri_lbl.config(text=f"{avg_bri}%")
        self.master_temp_lbl.config(text=f"{int(1_000_000 / avg_temp)}K")
        self._master_updating = False

    def _sync_master_labels(self, bri, temp):
        """Keep master slider labels in sync when individual sliders move."""
        self._master_updating = True
        self.master_bri.set(bri)
        self.master_temp.set(temp)
        self.master_bri_lbl.config(text=f"{bri}%")
        self.master_temp_lbl.config(text=f"{int(1_000_000/temp)}K")
        self._master_updating = False

    def _add_manual(self):
        ip = self.ip_entry.get().strip()
        port_str = self.port_entry.get().strip()
        if not ip:
            messagebox.showwarning("Missing IP", "Please enter an IP address.")
            return
        try:
            port = int(port_str)
        except ValueError:
            port = 9123
        self._add_light(f"Key Light ({ip})", ip, port)

    def _start_discovery(self):
        def cb(name, ip, port):
            # Friendly name: strip mDNS suffix
            friendly = name.replace("._elg._tcp.local.", "").strip(".")
            self.after(0, lambda: self._add_light(friendly, ip, port))

        self.discovery = LightDiscovery(cb)
        self.discovery.start()
        # Stop scanning notice after 8 s
        self.after(8000, lambda: self.scan_label.config(
            text="Scan complete. Use 'Scan again' or add manually."
        ))

    def _rescan(self):
        self.discovery.stop()
        self.scan_label.config(text="Scanning…")
        self._start_discovery()

    def on_close(self):
        self.discovery.stop()
        self.destroy()


if __name__ == "__main__":
    app = App()
    app.protocol("WM_DELETE_WINDOW", app.on_close)
    app.mainloop()
