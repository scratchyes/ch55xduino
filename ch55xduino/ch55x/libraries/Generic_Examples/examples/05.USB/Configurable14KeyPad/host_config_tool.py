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


def key_value_to_byte(value) -> int:
    if isinstance(value, int):
        if not 0 <= value <= 255:
            raise ValueError("key integer must be 0..255")
        return value
    if isinstance(value, str) and len(value) == 1:
        return ord(value)
    raise ValueError("key must be integer HID value or single character string")


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
            usage = int(item["usage"])
            if not 0 <= usage <= 0x03FF:
                raise ValueError("media usage must be 0..1023")
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


def list_serial_ports() -> None:
    for p in list_ports.comports():
        desc = p.description or ""
        print(f"{p.device}\t{desc}")


def cmd_get(args) -> int:
    with open_port(args.port) as ser:
        image = get_config_image(ser)
    cfg = decode_json_config(image)
    print(json.dumps(cfg, ensure_ascii=False, indent=2))
    return 0


def cmd_save(args) -> int:
    cfg = json.loads(Path(args.json).read_text(encoding="utf-8"))
    image = encode_json_config(cfg)
    with open_port(args.port) as ser:
        response = send_command(ser, "SAVE " + image.hex().upper())
    if response != "OK":
        raise RuntimeError(f"save failed: {response}")
    print("OK")
    return 0


def cmd_reset(args) -> int:
    with open_port(args.port) as ser:
        response = send_command(ser, "RESET")
    if response != "OK":
        raise RuntimeError(f"reset failed: {response}")
    print("OK")
    return 0


def cmd_upgrade(args) -> int:
    with open_port(args.port) as ser:
        response = send_command(ser, "UPGRADE")
    if response != "OK":
        raise RuntimeError(f"upgrade failed: {response}")
    print("OK")
    return 0


def cmd_dump_raw(args) -> int:
    with open_port(args.port) as ser:
        image = get_config_image(ser)
    print(image.hex().upper())
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
    except Exception as exc:  # pylint: disable=broad-except
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    parser.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
