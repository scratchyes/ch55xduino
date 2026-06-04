/*
  Configurable14KeyPad

  CH552T 14-key configurable keypad:
  - USB CDC serial protocol for configuration (GET/SAVE/RESET/UPGRADE)
  - USB HID keyboard + consumer(media) reports
  - DataFlash persistence (128 bytes)

  cli board options: usb_settings=user266
*/

#ifndef USER_USB_RAM
#error "This example needs to be compiled with a USER USB setting"
#endif

#include <string.h>
#include "src/Configurable14KeyPad/USBCDC.h"
#include "src/Configurable14KeyPad/USBHIDKeyboard.h"

#define KEY_COUNT 14
#define TEXT_SLOT_COUNT 7
#define TEXT_SLOT_SIZE 8
#define CONFIG_IMAGE_SIZE 128
#define CONFIG_MAGIC0 0x4D
#define CONFIG_MAGIC1 0x50
#define CONFIG_VERSION 0x01
#define CONFIG_PAYLOAD_LEN 123
#define SCAN_INTERVAL_MS 5
#define DEBOUNCE_MS 20

#define ACTION_NONE 0
#define ACTION_KEY 1
#define ACTION_COMBO 2
#define ACTION_TEXT 3
#define ACTION_MEDIA 4

#define MOD_CTRL 0x01
#define MOD_SHIFT 0x02
#define MOD_ALT 0x04
#define MOD_GUI 0x08

typedef struct {
  uint8_t type;
  uint8_t arg0;
  uint8_t arg1;
  uint8_t arg2;
} KeyAction;

__code uint8_t keyPins[KEY_COUNT] = {32, 14, 15, 16, 17, 10, 11,
                                     31, 30, 12, 13, 35, 34, 33};

__xdata KeyAction keyActions[KEY_COUNT];
__xdata uint8_t textSlots[TEXT_SLOT_COUNT][TEXT_SLOT_SIZE];

__xdata uint8_t stablePressed[KEY_COUNT];
__xdata uint8_t lastReadPressed[KEY_COUNT];
__xdata uint32_t lastChangeMs[KEY_COUNT];

__xdata char cmdBuffer[320];
__xdata uint16_t cmdLen = 0;

uint32_t previousScanMillis = 0;

static uint8_t crc8(const uint8_t *__xdata data, uint8_t len) {
  uint8_t crc = 0;
  for (uint8_t i = 0; i < len; i++) {
    crc ^= data[i];
    for (uint8_t b = 0; b < 8; b++) {
      if (crc & 0x80) {
        crc = (crc << 1) ^ 0x07;
      } else {
        crc <<= 1;
      }
    }
  }
  return crc;
}

static uint8_t nibbleToHex(uint8_t v) {
  return (v < 10) ? ('0' + v) : ('A' + (v - 10));
}

static int8_t hexToNibble(uint8_t c) {
  if ((c >= '0') && (c <= '9')) {
    return (int8_t)(c - '0');
  }
  if ((c >= 'a') && (c <= 'f')) {
    return (int8_t)(10 + c - 'a');
  }
  if ((c >= 'A') && (c <= 'F')) {
    return (int8_t)(10 + c - 'A');
  }
  return -1;
}

static void serialWriteLine(const char *msg) {
  USBSerial_println(msg);
  USBSerial_flush();
}

static void applyModifiers(uint8_t mask, uint8_t pressed) {
  if (mask & MOD_CTRL) {
    if (pressed) {
      Keyboard_press(KEY_LEFT_CTRL);
    } else {
      Keyboard_release(KEY_LEFT_CTRL);
    }
  }
  if (mask & MOD_SHIFT) {
    if (pressed) {
      Keyboard_press(KEY_LEFT_SHIFT);
    } else {
      Keyboard_release(KEY_LEFT_SHIFT);
    }
  }
  if (mask & MOD_ALT) {
    if (pressed) {
      Keyboard_press(KEY_LEFT_ALT);
    } else {
      Keyboard_release(KEY_LEFT_ALT);
    }
  }
  if (mask & MOD_GUI) {
    if (pressed) {
      Keyboard_press(KEY_LEFT_GUI);
    } else {
      Keyboard_release(KEY_LEFT_GUI);
    }
  }
}

static void executeActionPress(uint8_t keyIndex) {
  __xdata KeyAction *action = &keyActions[keyIndex];
  if (action->type == ACTION_KEY) {
    Keyboard_press(action->arg0);
  } else if (action->type == ACTION_COMBO) {
    applyModifiers(action->arg0, 1);
    Keyboard_press(action->arg1);
  } else if (action->type == ACTION_TEXT) {
    uint8_t slot = action->arg0;
    if (slot < TEXT_SLOT_COUNT) {
      for (uint8_t i = 0; i < TEXT_SLOT_SIZE; i++) {
        uint8_t c = textSlots[slot][i];
        if (c == 0) {
          break;
        }
        Keyboard_write(c);
      }
    }
  } else if (action->type == ACTION_MEDIA) {
    uint16_t mediaCode = ((uint16_t)action->arg1 << 8) | action->arg0;
    Consumer_press(mediaCode);
  }
}

static void executeActionRelease(uint8_t keyIndex) {
  __xdata KeyAction *action = &keyActions[keyIndex];
  if (action->type == ACTION_KEY) {
    Keyboard_release(action->arg0);
  } else if (action->type == ACTION_COMBO) {
    Keyboard_release(action->arg1);
    applyModifiers(action->arg0, 0);
  } else if (action->type == ACTION_MEDIA) {
    uint16_t mediaCode = ((uint16_t)action->arg1 << 8) | action->arg0;
    Consumer_release(mediaCode);
  }
}

static void loadFactoryDefaultConfig() {
  memset(keyActions, 0, sizeof(keyActions));
  memset(textSlots, 0, sizeof(textSlots));

  keyActions[0] = (KeyAction){ACTION_KEY, 'a', 0, 0};
  keyActions[1] = (KeyAction){ACTION_KEY, 'b', 0, 0};
  keyActions[2] = (KeyAction){ACTION_KEY, 'c', 0, 0};
  keyActions[3] = (KeyAction){ACTION_KEY, 'd', 0, 0};
  keyActions[4] = (KeyAction){ACTION_KEY, 'e', 0, 0};
  keyActions[5] = (KeyAction){ACTION_KEY, 'f', 0, 0};
  keyActions[6] = (KeyAction){ACTION_KEY, 'g', 0, 0};
  keyActions[7] = (KeyAction){ACTION_KEY, 'h', 0, 0};
  keyActions[8] = (KeyAction){ACTION_COMBO, MOD_CTRL, 'c', 0};
  keyActions[9] = (KeyAction){ACTION_COMBO, MOD_CTRL, 'v', 0};
  keyActions[10] = (KeyAction){ACTION_COMBO, MOD_CTRL, 'x', 0};
  keyActions[11] = (KeyAction){ACTION_TEXT, 0, 0, 0};
  keyActions[12] = (KeyAction){ACTION_MEDIA, (uint8_t)(MEDIA_VOL_DOWN & 0xFF),
                               (uint8_t)(MEDIA_VOL_DOWN >> 8), 0};
  keyActions[13] = (KeyAction){ACTION_MEDIA, (uint8_t)(MEDIA_VOL_UP & 0xFF),
                               (uint8_t)(MEDIA_VOL_UP >> 8), 0};

  textSlots[0][0] = 'H';
  textSlots[0][1] = 'E';
  textSlots[0][2] = 'L';
  textSlots[0][3] = 'L';
  textSlots[0][4] = 'O';
}

static void encodeConfigImage(uint8_t *__xdata outImage) {
  memset(outImage, 0, CONFIG_IMAGE_SIZE);
  outImage[0] = CONFIG_MAGIC0;
  outImage[1] = CONFIG_MAGIC1;
  outImage[2] = CONFIG_VERSION;
  outImage[3] = CONFIG_PAYLOAD_LEN;

  for (uint8_t i = 0; i < KEY_COUNT; i++) {
    uint8_t base = 4 + i * 4;
    outImage[base + 0] = keyActions[i].type;
    outImage[base + 1] = keyActions[i].arg0;
    outImage[base + 2] = keyActions[i].arg1;
    outImage[base + 3] = keyActions[i].arg2;
  }

  for (uint8_t s = 0; s < TEXT_SLOT_COUNT; s++) {
    for (uint8_t j = 0; j < TEXT_SLOT_SIZE; j++) {
      outImage[60 + s * TEXT_SLOT_SIZE + j] = textSlots[s][j];
    }
  }

  outImage[127] = crc8(&outImage[4], CONFIG_PAYLOAD_LEN);
}

static uint8_t validateConfigImage(const uint8_t *__xdata image) {
  if (image[0] != CONFIG_MAGIC0) {
    return 0;
  }
  if (image[1] != CONFIG_MAGIC1) {
    return 0;
  }
  if (image[2] != CONFIG_VERSION) {
    return 0;
  }
  if (image[3] != CONFIG_PAYLOAD_LEN) {
    return 0;
  }
  if (image[127] != crc8(&image[4], CONFIG_PAYLOAD_LEN)) {
    return 0;
  }
  return 1;
}

static void decodeConfigImage(const uint8_t *__xdata image) {
  for (uint8_t i = 0; i < KEY_COUNT; i++) {
    uint8_t base = 4 + i * 4;
    keyActions[i].type = image[base + 0];
    keyActions[i].arg0 = image[base + 1];
    keyActions[i].arg1 = image[base + 2];
    keyActions[i].arg2 = image[base + 3];
  }

  for (uint8_t s = 0; s < TEXT_SLOT_COUNT; s++) {
    for (uint8_t j = 0; j < TEXT_SLOT_SIZE; j++) {
      textSlots[s][j] = image[60 + s * TEXT_SLOT_SIZE + j];
    }
  }
}

static void saveConfigToFlash() {
  __xdata uint8_t image[CONFIG_IMAGE_SIZE];
  encodeConfigImage(image);
  for (uint8_t i = 0; i < CONFIG_IMAGE_SIZE; i++) {
    eeprom_write_byte(i, image[i]);
  }
}

static uint8_t loadConfigFromFlash() {
  __xdata uint8_t image[CONFIG_IMAGE_SIZE];
  for (uint8_t i = 0; i < CONFIG_IMAGE_SIZE; i++) {
    image[i] = eeprom_read_byte(i);
  }

  if (!validateConfigImage(image)) {
    return 0;
  }

  decodeConfigImage(image);
  return 1;
}

static void resetToFactoryDefaultAndSave() {
  loadFactoryDefaultConfig();
  saveConfigToFlash();
}

static uint8_t decodeHexImage(const char *hex, uint16_t hexLen,
                              uint8_t *__xdata outImage) {
  if (hexLen != (CONFIG_IMAGE_SIZE * 2)) {
    return 0;
  }

  for (uint8_t i = 0; i < CONFIG_IMAGE_SIZE; i++) {
    int8_t hi = hexToNibble(hex[i * 2]);
    int8_t lo = hexToNibble(hex[i * 2 + 1]);
    if ((hi < 0) || (lo < 0)) {
      return 0;
    }
    outImage[i] = (uint8_t)((hi << 4) | lo);
  }
  return 1;
}

static void sendConfigImageHex() {
  __xdata uint8_t image[CONFIG_IMAGE_SIZE];
  encodeConfigImage(image);
  USBSerial_print("CFG ");
  for (uint8_t i = 0; i < CONFIG_IMAGE_SIZE; i++) {
    USBSerial_write((char)nibbleToHex((uint8_t)(image[i] >> 4)));
    USBSerial_write((char)nibbleToHex((uint8_t)(image[i] & 0x0F)));
  }
  USBSerial_write('\n');
  USBSerial_flush();
}

static void jumpToBootloader() {
  USBSerial_flush();
  delay(20);

#if BOOT_LOAD_ADDR == 0x3800
  USB_CTRL = 0;
  EA = 0;
  TMOD = 0;
  delay(50);
  __asm__("lcall #0x3800");
#elif defined(CH559) && (BOOT_LOAD_ADDR == 0xF400)
  USB_CTRL = 0;
  EA = 0;
  delay(50);
  __asm__("lcall #0xF400");
#endif

  while (1) {
  }
}

static void handleCommand(const char *cmdLine) {
  if (strcmp(cmdLine, "GET") == 0) {
    sendConfigImageHex();
    return;
  }

  if (strncmp(cmdLine, "SAVE ", 5) == 0) {
    __xdata uint8_t image[CONFIG_IMAGE_SIZE];
    const char *hex = cmdLine + 5;
    uint16_t hexLen = strlen(hex);
    if (!decodeHexImage(hex, hexLen, image)) {
      serialWriteLine("ERR BADHEX");
      return;
    }
    if (!validateConfigImage(image)) {
      serialWriteLine("ERR BADCFG");
      return;
    }
    decodeConfigImage(image);
    saveConfigToFlash();
    serialWriteLine("OK");
    return;
  }

  if (strcmp(cmdLine, "RESET") == 0) {
    resetToFactoryDefaultAndSave();
    serialWriteLine("OK");
    return;
  }

  if (strcmp(cmdLine, "UPGRADE") == 0) {
    serialWriteLine("OK");
    jumpToBootloader();
    return;
  }

  serialWriteLine("ERR UNKNOWN");
}

static void pollSerialProtocol() {
  while (USBSerial_available()) {
    char c = USBSerial_read();
    if ((c == '\r') || (c == '\n')) {
      if (cmdLen > 0) {
        cmdBuffer[cmdLen] = '\0';
        handleCommand(cmdBuffer);
        cmdLen = 0;
      }
      continue;
    }

    if (cmdLen < (sizeof(cmdBuffer) - 1)) {
      cmdBuffer[cmdLen++] = c;
    } else {
      cmdLen = 0;
      serialWriteLine("ERR TOOLONG");
    }
  }
}

static void scanKeysAndDispatch() {
  uint32_t now = millis();
  if ((uint32_t)(now - previousScanMillis) < SCAN_INTERVAL_MS) {
    return;
  }
  previousScanMillis = now;

  for (uint8_t i = 0; i < KEY_COUNT; i++) {
    uint8_t pressed = !digitalRead(keyPins[i]);
    if (pressed != lastReadPressed[i]) {
      lastReadPressed[i] = pressed;
      lastChangeMs[i] = now;
    }

    if ((pressed != stablePressed[i]) &&
        ((uint32_t)(now - lastChangeMs[i]) >= DEBOUNCE_MS)) {
      stablePressed[i] = pressed;
      if (pressed) {
        executeActionPress(i);
      } else {
        executeActionRelease(i);
      }
    }
  }
}

void setup() {
  USBInit();

  for (uint8_t i = 0; i < KEY_COUNT; i++) {
    pinMode(keyPins[i], INPUT_PULLUP);
    stablePressed[i] = 0;
    lastReadPressed[i] = 0;
    lastChangeMs[i] = 0;
  }

  if (!loadConfigFromFlash()) {
    resetToFactoryDefaultAndSave();
  }
}

void loop() {
  pollSerialProtocol();
  scanKeysAndDispatch();
}
