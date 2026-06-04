# Configurable14KeyPad

CH552T 14-key keypad example with:

- USB HID keyboard + media (consumer) report
- USB CDC configuration protocol
- DataFlash 128-byte config persistence
- Bootloader jump command (`UPGRADE`)

## Pin map

- K01 P3.2 (32)
- K02 P1.4 (14)
- K03 P1.5 (15)
- K04 P1.6 (16)
- K05 P1.7 (17)
- K06 P1.0 (10)
- K07 P1.1 (11)
- K08 P3.1 (31)
- K09 P3.0 (30)
- K10 P1.2 (12)
- K11 P1.3 (13)
- K12 P3.5 (35)
- K13 P3.4 (34)
- K14 P3.3 (33)

## CDC protocol

Text commands (newline terminated):

- `GET` → `CFG <256 HEX chars>`
- `SAVE <256 HEX chars>` → `OK` or `ERR ...`
- `RESET` → restore default config
- `UPGRADE` → jump to USB ISP bootloader

## Host tool

`host_config_tool.py` uses pyserial.

- `python3 host_config_tool.py list-ports`
- `python3 host_config_tool.py get --port COMx`
- `python3 host_config_tool.py save --port COMx --json config.json`
- `python3 host_config_tool.py reset --port COMx`
- `python3 host_config_tool.py upgrade --port COMx`

JSON format:

- `keys`: 14 entries
  - `{"type":"key","key":"a"}`
  - `{"type":"combo","mods":["ctrl"],"key":"c"}`
  - `{"type":"text","slot":0}`
  - `{"type":"media","usage":233}`
- `texts`: up to 7 strings, each max 8 bytes ASCII
