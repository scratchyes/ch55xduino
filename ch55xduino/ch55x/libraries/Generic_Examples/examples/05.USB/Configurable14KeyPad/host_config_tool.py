#!/usr/bin/env python3
"""Host tool for Configurable14KeyPad CDC configuration protocol."""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import serial
from serial.tools import list_ports

try:
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk
except Exception:  # pylint: disable=broad-except
    tk = None
    ttk = None
    messagebox = None
    filedialog = None

KEY_COUNT = 14
TEXT_SLOT_COUNT = 7
TEXT_SLOT_SIZE = 8
CONFIG_IMAGE_SIZE = 128
CONFIG_MAGIC0 = 0x4D
CONFIG_MAGIC1 = 0x50
CONFIG_VERSION = 0x01
CONFIG_PAYLOAD_LEN = 123

ACTION_NONE = 0
ACTION_KEY = 1
ACTION_COMBO = 2
ACTION_TEXT = 3
ACTION_MEDIA = 4

MOD_CTRL = 0x01
MOD_SHIFT = 0x02
MOD_ALT = 0x04
MOD_GUI = 0x08

MOD_NAME_TO_MASK = {
    "ctrl": MOD_CTRL,
    "shift": MOD_SHIFT,
    "alt": MOD_ALT,
    "gui": MOD_GUI,
    "win": MOD_GUI,
    "cmd": MOD_GUI,
}

KEY_NAME_TO_VALUE = {
    "return": 0xB0,
    "enter": 0xB0,
    "esc": 0xB1,
    "escape": 0xB1,
    "backspace": 0xB2,
    "tab": 0xB3,
    "insert": 0xD1,
    "delete": 0xD4,
    "pageup": 0xD3,
    "pagedown": 0xD6,
    "home": 0xD2,
    "end": 0xD5,
    "up": 0xDA,
    "up_arrow": 0xDA,
    "down": 0xD9,
    "down_arrow": 0xD9,
    "left": 0xD8,
    "left_arrow": 0xD8,
    "right": 0xD7,
    "right_arrow": 0xD7,
    "capslock": 0xC1,
}

for index, value in enumerate(range(0xC2, 0xCC + 1), start=1):
    KEY_NAME_TO_VALUE[f"f{index}"] = value
for index, value in enumerate(range(0xF0, 0xFB + 1), start=13):
    KEY_NAME_TO_VALUE[f"f{index}"] = value

VALUE_TO_KEY_NAME = {value: name for name, value in KEY_NAME_TO_VALUE.items()}

MEDIA_NAME_TO_USAGE = {
    "play_pause": 0x00CD,
    "next": 0x00B5,
    "previous": 0x00B6,
    "stop": 0x00B7,
    "mute": 0x00E2,
    "volume_up": 0x00E9,
    "volume_down": 0x00EA,
}

USAGE_TO_MEDIA_NAME = {value: name for name, value in MEDIA_NAME_TO_USAGE.items()}


def crc8(data: bytes) -> int:
    crc = 0
    for byte in data:
        crc ^= byte
        for _ in range(8):
            if crc & 0x80:
                crc = ((crc << 1) ^ 0x07) & 0xFF
            else:
                crc = (crc << 1) & 0xFF
    return crc


def parse_int_range(value, name: str, minimum: int, maximum: int) -> int:
    if isinstance(value, int):
        parsed = value
    elif isinstance(value, str):
        parsed = int(value.strip(), 0)
    else:
        raise ValueError(f"{name} must be integer")
    if not minimum <= parsed <= maximum:
        raise ValueError(f"{name} must be {minimum}..{maximum}")
    return parsed


def key_value_to_byte(value) -> int:
    if isinstance(value, str):
        raw = value.strip()
        if len(raw) == 1:
            return ord(raw)
        lowered = raw.lower()
        if lowered in KEY_NAME_TO_VALUE:
            return KEY_NAME_TO_VALUE[lowered]
        return parse_int_range(raw, "key", 0, 255)
    return parse_int_range(value, "key", 0, 255)


def media_usage_to_int(value) -> int:
    if isinstance(value, str):
        raw = value.strip()
        lowered = raw.lower()
        if lowered in MEDIA_NAME_TO_USAGE:
            return MEDIA_NAME_TO_USAGE[lowered]
        return parse_int_range(raw, "media usage", 0, 0x03FF)
    return parse_int_range(value, "media usage", 0, 0x03FF)


def text_to_slot_bytes(text: str) -> bytes:
    raw = text.encode("ascii")
    if len(raw) > TEXT_SLOT_SIZE:
        raise ValueError(f"text slot exceeds {TEXT_SLOT_SIZE} bytes")
    return raw + b"\x00" * (TEXT_SLOT_SIZE - len(raw))


def encode_json_config(cfg: dict) -> bytes:
    keys = cfg.get("keys", [])
    texts = cfg.get("texts", [])

    if len(keys) != KEY_COUNT:
        raise ValueError(f"keys length must be {KEY_COUNT}")
    if len(texts) > TEXT_SLOT_COUNT:
        raise ValueError(f"texts length must be <= {TEXT_SLOT_COUNT}")

    image = bytearray(CONFIG_IMAGE_SIZE)
    image[0] = CONFIG_MAGIC0
    image[1] = CONFIG_MAGIC1
    image[2] = CONFIG_VERSION
    image[3] = CONFIG_PAYLOAD_LEN

    for index, item in enumerate(keys):
        if not isinstance(item, dict):
            raise ValueError(f"keys[{index}] must be object")
        action_type = item.get("type", "none").lower()
        base = 4 + index * 4
        if action_type == "none":
            image[base] = ACTION_NONE
        elif action_type == "key":
            image[base] = ACTION_KEY
            image[base + 1] = key_value_to_byte(item["key"])
        elif action_type == "combo":
            image[base] = ACTION_COMBO
            mods = item.get("mods", [])
            mask = 0
            for mod in mods:
                mod_name = str(mod).lower()
                if mod_name not in MOD_NAME_TO_MASK:
                    raise ValueError(f"unsupported modifier: {mod}")
                mask |= MOD_NAME_TO_MASK[mod_name]
            image[base + 1] = mask
            image[base + 2] = key_value_to_byte(item["key"])
        elif action_type == "text":
            image[base] = ACTION_TEXT
            slot = int(item.get("slot", 0))
            if not 0 <= slot < TEXT_SLOT_COUNT:
                raise ValueError(f"text slot must be 0..{TEXT_SLOT_COUNT - 1}")
            image[base + 1] = slot
        elif action_type == "media":
            image[base] = ACTION_MEDIA
            usage = media_usage_to_int(item["usage"])
            image[base + 1] = usage & 0xFF
            image[base + 2] = (usage >> 8) & 0xFF
        else:
            raise ValueError(f"unsupported action type: {action_type}")

    for slot, text in enumerate(texts):
        text_bytes = text_to_slot_bytes(str(text))
        start = 60 + slot * TEXT_SLOT_SIZE
        image[start:start + TEXT_SLOT_SIZE] = text_bytes

    image[127] = crc8(image[4:4 + CONFIG_PAYLOAD_LEN])
    return bytes(image)


def decode_json_config(image: bytes) -> dict:
    if len(image) != CONFIG_IMAGE_SIZE:
        raise ValueError("invalid image length")
    if image[0] != CONFIG_MAGIC0 or image[1] != CONFIG_MAGIC1:
        raise ValueError("invalid magic")
    if image[2] != CONFIG_VERSION:
        raise ValueError("unsupported version")
    if image[3] != CONFIG_PAYLOAD_LEN:
        raise ValueError("invalid payload length")
    if image[127] != crc8(image[4:4 + CONFIG_PAYLOAD_LEN]):
        raise ValueError("checksum mismatch")

    keys = []
    for index in range(KEY_COUNT):
        base = 4 + index * 4
        t = image[base]
        a0 = image[base + 1]
        a1 = image[base + 2]
        action = {"type": "none"}
        if t == ACTION_KEY:
            action = {"type": "key", "key": a0}
        elif t == ACTION_COMBO:
            mods = []
            for name, bit in (("ctrl", MOD_CTRL), ("shift", MOD_SHIFT), ("alt", MOD_ALT), ("gui", MOD_GUI)):
                if a0 & bit:
                    mods.append(name)
            action = {"type": "combo", "mods": mods, "key": a1}
        elif t == ACTION_TEXT:
            action = {"type": "text", "slot": a0}
        elif t == ACTION_MEDIA:
            action = {"type": "media", "usage": a0 | (a1 << 8)}
        keys.append(action)

    texts = []
    for slot in range(TEXT_SLOT_COUNT):
        start = 60 + slot * TEXT_SLOT_SIZE
        raw = bytes(image[start:start + TEXT_SLOT_SIZE])
        text = raw.split(b"\x00", 1)[0].decode("ascii", errors="ignore")
        texts.append(text)

    return {"keys": keys, "texts": texts}


def open_port(port: str, baud: int = 115200, timeout: float = 2.0) -> serial.Serial:
    ser = serial.Serial(port=port, baudrate=baud, timeout=timeout, write_timeout=timeout)
    time.sleep(0.1)
    ser.reset_input_buffer()
    ser.reset_output_buffer()
    return ser


def send_command(ser: serial.Serial, command: str) -> str:
    ser.write((command + "\n").encode("ascii"))
    ser.flush()
    line = ser.readline().decode("ascii", errors="ignore").strip()
    if not line:
        raise RuntimeError("no response from device")
    return line


def get_config_image(ser: serial.Serial) -> bytes:
    line = send_command(ser, "GET")
    if not line.startswith("CFG "):
        raise RuntimeError(f"unexpected response: {line}")
    hex_data = line[4:]
    if len(hex_data) != CONFIG_IMAGE_SIZE * 2:
        raise RuntimeError("invalid config hex length")
    try:
        image = bytes.fromhex(hex_data)
    except ValueError as exc:
        raise RuntimeError("invalid hex in device response") from exc
    return image


def load_cfg_from_device(port: str) -> dict:
    with open_port(port) as ser:
        image = get_config_image(ser)
    return decode_json_config(image)


def save_cfg_to_device(port: str, cfg: dict) -> None:
    image = encode_json_config(cfg)
    with open_port(port) as ser:
        response = send_command(ser, "SAVE " + image.hex().upper())
    if response != "OK":
        raise RuntimeError(f"save failed: {response}")


def reset_device(port: str) -> None:
    with open_port(port) as ser:
        response = send_command(ser, "RESET")
    if response != "OK":
        raise RuntimeError(f"reset failed: {response}")


def upgrade_device(port: str) -> None:
    with open_port(port) as ser:
        response = send_command(ser, "UPGRADE")
    if response != "OK":
        raise RuntimeError(f"upgrade failed: {response}")


def list_serial_ports() -> None:
    for p in list_ports.comports():
        desc = p.description or ""
        print(f"{p.device}\t{desc}")


def get_port_list() -> list[str]:
    return [p.device for p in list_ports.comports()]


def _display_key(value: int) -> str:
    if value in VALUE_TO_KEY_NAME:
        return VALUE_TO_KEY_NAME[value]
    if 32 <= value <= 126:
        return chr(value)
    return str(value)


def _display_media(value: int) -> str:
    return USAGE_TO_MEDIA_NAME.get(value, str(value))


def _parse_mods(mods_text: str) -> list[str]:
    if not mods_text.strip():
        return []
    mods = []
    for token in mods_text.replace("+", ",").split(","):
        mod = token.strip().lower()
        if not mod:
            continue
        if mod not in MOD_NAME_TO_MASK:
            raise ValueError(f"unsupported modifier: {mod}")
        normalized = "gui" if mod in ("win", "cmd") else mod
        if normalized not in mods:
            mods.append(normalized)
    return mods


class GuiApp:
    def __init__(self, default_port: str | None = None):
        if tk is None or ttk is None or messagebox is None or filedialog is None:
            raise RuntimeError("Tkinter is not available in this Python environment")

        self.root = tk.Tk()
        self.root.title("Configurable14KeyPad Host Tool")

        self.port_var = tk.StringVar(value=default_port or "")
        self.status_var = tk.StringVar(value="Ready")
        self.key_rows = []
        self.text_vars = []

        self._build_ui()
        self.refresh_ports()

    def _build_ui(self) -> None:
        top = ttk.Frame(self.root, padding=8)
        top.pack(fill="x")

        ttk.Label(top, text="Serial port:").grid(row=0, column=0, sticky="w")
        self.port_combo = ttk.Combobox(top, textvariable=self.port_var, width=24, state="readonly")
        self.port_combo.grid(row=0, column=1, padx=4, sticky="w")
        ttk.Button(top, text="Refresh", command=self.refresh_ports).grid(row=0, column=2, padx=4)
        ttk.Button(top, text="Read from device", command=self.read_device).grid(row=0, column=3, padx=4)
        ttk.Button(top, text="Save to device", command=self.save_device).grid(row=0, column=4, padx=4)

        key_frame = ttk.LabelFrame(self.root, text="Key actions", padding=8)
        key_frame.pack(fill="x", padx=8, pady=4)

        headers = ["Key", "Type", "Key Value", "Mods", "Text Slot", "Media Usage"]
        for col, header in enumerate(headers):
            ttk.Label(key_frame, text=header).grid(row=0, column=col, sticky="w", padx=4, pady=2)

        for idx in range(KEY_COUNT):
            ttk.Label(key_frame, text=f"K{idx + 1:02d}").grid(row=idx + 1, column=0, sticky="w", padx=4)

            type_var = tk.StringVar(value="none")
            type_combo = ttk.Combobox(
                key_frame,
                textvariable=type_var,
                values=("none", "key", "combo", "text", "media"),
                width=8,
                state="readonly",
            )
            type_combo.grid(row=idx + 1, column=1, padx=4, pady=2)

            key_var = tk.StringVar(value="")
            mods_var = tk.StringVar(value="")
            slot_var = tk.StringVar(value="0")
            media_var = tk.StringVar(value="")

            ttk.Entry(key_frame, textvariable=key_var, width=14).grid(row=idx + 1, column=2, padx=4, pady=2)
            ttk.Entry(key_frame, textvariable=mods_var, width=14).grid(row=idx + 1, column=3, padx=4, pady=2)
            ttk.Spinbox(key_frame, from_=0, to=TEXT_SLOT_COUNT - 1, textvariable=slot_var, width=5).grid(
                row=idx + 1, column=4, padx=4, pady=2
            )
            ttk.Entry(key_frame, textvariable=media_var, width=16).grid(row=idx + 1, column=5, padx=4, pady=2)

            self.key_rows.append(
                {
                    "type": type_var,
                    "key": key_var,
                    "mods": mods_var,
                    "slot": slot_var,
                    "media": media_var,
                }
            )

        text_frame = ttk.LabelFrame(self.root, text="Text slots (ASCII, max 8 chars each)", padding=8)
        text_frame.pack(fill="x", padx=8, pady=4)

        for idx in range(TEXT_SLOT_COUNT):
            ttk.Label(text_frame, text=f"Slot {idx}").grid(row=idx, column=0, sticky="w", padx=4, pady=2)
            var = tk.StringVar(value="")
            ttk.Entry(text_frame, textvariable=var, width=24).grid(row=idx, column=1, sticky="w", padx=4, pady=2)
            self.text_vars.append(var)

        button_frame = ttk.Frame(self.root, padding=8)
        button_frame.pack(fill="x")
        ttk.Button(button_frame, text="Load JSON", command=self.load_json_file).pack(side="left", padx=4)
        ttk.Button(button_frame, text="Save JSON", command=self.save_json_file).pack(side="left", padx=4)
        ttk.Button(button_frame, text="Reset device", command=self.reset_device).pack(side="left", padx=4)
        ttk.Button(button_frame, text="Enter bootloader", command=self.upgrade_device).pack(side="left", padx=4)

        ttk.Label(self.root, textvariable=self.status_var, anchor="w").pack(fill="x", padx=12, pady=(0, 8))

    def _selected_port(self) -> str:
        port = self.port_var.get().strip()
        if not port:
            raise ValueError("please select a serial port")
        return port

    def refresh_ports(self) -> None:
        ports = get_port_list()
        self.port_combo["values"] = ports
        if self.port_var.get() not in ports:
            self.port_var.set(ports[0] if ports else "")

    def gui_to_cfg(self) -> dict:
        keys = []
        for idx, row in enumerate(self.key_rows):
            action_type = row["type"].get().strip().lower() or "none"
            if action_type == "none":
                keys.append({"type": "none"})
            elif action_type == "key":
                keys.append({"type": "key", "key": key_value_to_byte(row["key"].get())})
            elif action_type == "combo":
                keys.append(
                    {
                        "type": "combo",
                        "mods": _parse_mods(row["mods"].get()),
                        "key": key_value_to_byte(row["key"].get()),
                    }
                )
            elif action_type == "text":
                keys.append({"type": "text", "slot": parse_int_range(row["slot"].get(), f"slot[{idx}]", 0, 6)})
            elif action_type == "media":
                keys.append({"type": "media", "usage": media_usage_to_int(row["media"].get())})
            else:
                raise ValueError(f"unsupported type on key {idx + 1}: {action_type}")

        texts = [var.get() for var in self.text_vars]
        return {"keys": keys, "texts": texts}

    def cfg_to_gui(self, cfg: dict) -> None:
        keys = cfg.get("keys", [])
        texts = cfg.get("texts", [])
        if len(keys) != KEY_COUNT:
            raise ValueError(f"keys length must be {KEY_COUNT}")

        for idx, row in enumerate(self.key_rows):
            item = keys[idx]
            action_type = str(item.get("type", "none")).lower()
            row["type"].set(action_type)
            row["key"].set("")
            row["mods"].set("")
            row["slot"].set("0")
            row["media"].set("")
            if action_type == "key":
                row["key"].set(_display_key(int(item.get("key", 0))))
            elif action_type == "combo":
                row["mods"].set(",".join([str(mod).lower() for mod in item.get("mods", [])]))
                row["key"].set(_display_key(int(item.get("key", 0))))
            elif action_type == "text":
                row["slot"].set(str(int(item.get("slot", 0))))
            elif action_type == "media":
                row["media"].set(_display_media(int(item.get("usage", 0))))

        for idx in range(TEXT_SLOT_COUNT):
            self.text_vars[idx].set(str(texts[idx]) if idx < len(texts) else "")

    def _do_action(self, action, success_message: str | None = None) -> None:
        try:
            action()
            if success_message:
                self.status_var.set(success_message)
        except Exception as exc:  # pylint: disable=broad-except
            self.status_var.set(f"Error: {exc}")
            messagebox.showerror("Error", str(exc))

    def read_device(self) -> None:
        def action() -> None:
            cfg = load_cfg_from_device(self._selected_port())
            self.cfg_to_gui(cfg)

        self._do_action(action, "Loaded config from device")

    def save_device(self) -> None:
        def action() -> None:
            cfg = self.gui_to_cfg()
            save_cfg_to_device(self._selected_port(), cfg)

        self._do_action(action, "Saved config to device")

    def reset_device(self) -> None:
        def action() -> None:
            reset_device(self._selected_port())
            cfg = load_cfg_from_device(self._selected_port())
            self.cfg_to_gui(cfg)

        self._do_action(action, "Device reset to default config")

    def upgrade_device(self) -> None:
        def action() -> None:
            upgrade_device(self._selected_port())

        self._do_action(action, "Device switched to bootloader mode")

    def load_json_file(self) -> None:
        path = filedialog.askopenfilename(
            title="Load config JSON",
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")],
        )
        if not path:
            return

        def action() -> None:
            cfg = json.loads(Path(path).read_text(encoding="utf-8"))
            encode_json_config(cfg)
            self.cfg_to_gui(cfg)

        self._do_action(action, f"Loaded JSON from {path}")

    def save_json_file(self) -> None:
        path = filedialog.asksaveasfilename(
            title="Save config JSON",
            defaultextension=".json",
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")],
        )
        if not path:
            return

        def action() -> None:
            cfg = self.gui_to_cfg()
            encode_json_config(cfg)
            Path(path).write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

        self._do_action(action, f"Saved JSON to {path}")

    def run(self) -> None:
        self.root.mainloop()


def cmd_get(args) -> int:
    cfg = load_cfg_from_device(args.port)
    print(json.dumps(cfg, ensure_ascii=False, indent=2))
    return 0


def cmd_save(args) -> int:
    cfg = json.loads(Path(args.json).read_text(encoding="utf-8"))
    save_cfg_to_device(args.port, cfg)
    print("OK")
    return 0


def cmd_reset(args) -> int:
    reset_device(args.port)
    print("OK")
    return 0


def cmd_upgrade(args) -> int:
    upgrade_device(args.port)
    print("OK")
    return 0


def cmd_dump_raw(args) -> int:
    with open_port(args.port) as ser:
        image = get_config_image(ser)
    print(image.hex().upper())
    return 0


def cmd_gui(args) -> int:
    app = GuiApp(default_port=args.port)
    app.run()
    return 0


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Configurable14KeyPad host tool")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("list-ports", help="List serial ports")

    p_get = sub.add_parser("get", help="Read device config and print JSON")
    p_get.add_argument("--port", required=True)

    p_save = sub.add_parser("save", help="Save JSON config to device")
    p_save.add_argument("--port", required=True)
    p_save.add_argument("--json", required=True)

    p_reset = sub.add_parser("reset", help="Restore factory defaults")
    p_reset.add_argument("--port", required=True)

    p_upgrade = sub.add_parser("upgrade", help="Request bootloader upgrade mode")
    p_upgrade.add_argument("--port", required=True)

    p_raw = sub.add_parser("dump-raw", help="Read and print raw 128-byte hex image")
    p_raw.add_argument("--port", required=True)

    p_gui = sub.add_parser("gui", help="Open GUI configurator")
    p_gui.add_argument("--port", default=None)

    return parser


def main() -> int:
    parser = build_arg_parser()
    args = parser.parse_args()
    try:
        if args.cmd == "list-ports":
            list_serial_ports()
            return 0
        if args.cmd == "get":
            return cmd_get(args)
        if args.cmd == "save":
            return cmd_save(args)
        if args.cmd == "reset":
            return cmd_reset(args)
        if args.cmd == "upgrade":
            return cmd_upgrade(args)
        if args.cmd == "dump-raw":
            return cmd_dump_raw(args)
        if args.cmd == "gui":
            return cmd_gui(args)
    except Exception as exc:  # pylint: disable=broad-except
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    parser.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
