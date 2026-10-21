# Flasher_repo

Firmware, startup files and configs for the **Fireplace Flasher** USB tool
(the program itself lives in the separate repo `swdeco/fireplace-flasher`).

The tool downloads `manifest.json` and the files it lists from this repo
(`https://raw.githubusercontent.com/swdeco/Flasher_repo/main/`), so this repo must stay
**public**. It contains only compiled `.bin` files and config templates - no source code,
no MQTT/WiFi credentials.

The phone app's OTA updates use the separate `swdeco/firmwares` repo - nothing here affects it.

## What is in the tool

| Model (exact `fireplaceModel`) | Display | Mainboard | config.json templates |
|---|---|---|---|
| Logicwood | 80 MHz bootloader | S3 | `Logicwood 510` (default = base), `Logicwood 720`, `Logicwood 920`, `Logicwood 1120` |
| Denver F2 | 80 MHz bootloader | S3 | `Base (firmware default)` (default) |
| Rais Visio | 40 MHz bootloader | S3 | `visio 4-9-2026` (default), `Base (firmware default)` |
| Rais CVNQ | 40 MHz bootloader | S3 | `CVNQ` (default), `Swiss (CH) - reduced output`, `Base (firmware default)` |

**Base (firmware default)** = the display repo's `data/config.json` (`LogicWood_display`,
`Display_F2`, `Visio_Display`, `Rais-display`), byte-identical to the default the firmware
builds in (`DEFAULT_CONFIG_JSON`) - what a board falls back to if its config is lost. The
display copy is used because the display holds the master config (with `fireplaceModel`)
and pushes it to the mainboard on every save. When a repo's `data/config.json` changes,
publish it as a NEW config name (published files never change), e.g. `Base 2026-11`.

**Logicwood per length** (the number = fireplace length in mm): built from the service team's
files with heating window 60-120 °C, call service (`evapMaxTemp`) 125 °C, the standard LED
setup (heartbeat 4 charcoal, fade speed 50 ms), placeholder serial `123456` and zeroed
counters; 510 uses the landscape layout, the others portrait. `Logicwood 510` is the
Logicwood base, so Logicwood has no separate `Base (firmware default)` entry any more (its
file stays for cached copies). The display AND burner firmware's built-in default (used if a
board's config is lost) = the 510 with portrait layout and smart home off - from the NEXT
published Logicwood firmware (the published 4.3.2 / 0.6.2 bins still have the old default).

**Rais CVNQ:** `CVNQ` = the service team's `CVNQ.json` (2026-10-21) as-is, only
`"fireplaceModel": "Rais CVNQ"` added so the app/OTA knows the model; `Swiss (CH) - reduced
output` = their `CVNQ SWISS.json` byte for byte.

The tool shows the config list EMPTY after choosing a model - the technician must pick the
config (length) before anything is written. The `default` flag only marks the base config.

The model order in the tool = the order in manifest.json (`release.py order ...`).
When the tool writes a config it always keeps the board's Bluetooth code (`serialNumber`),
so the code in a template only matters for brand-new boards.

## Layout

```
manifest.json      index the tool reads (models, versions, sizes, crc32, md5)
boot/              shared startup files (same for every model)
  esp32-bootloader-dio-40m-8MB.bin     displays built with f_flash 40 MHz (Rais CVNQ, Rais Visio)
  esp32-bootloader-dio-80m-8MB.bin     displays built with f_flash 80 MHz (Logicwood, Denver F2, Hybrid mist)
  esp32s3-bootloader-dio-80m-8MB.bin   all mainboards
  partitions-default_8MB.bin           Arduino default_8MB - used by ALL boards
  boot_app0.bin
models/<slug>/display/<slug>-display-<version>.bin       display firmware (app image, 0x10000)
models/<slug>/mainboard/<slug>-mainboard-<version>.bin   mainboard firmware
models/<slug>/config/<slug>-config-<name>.json            config.json templates
tools/release.py   adds files to manifest.json (with checks)
.gitattributes     '* -text' - keeps every file byte-for-byte (see below)
```

## Golden rules

1. **Never overwrite or edit a published file.** Always add a new version. The tool caches
   by file name and checks every download against the manifest checksums.
2. **Never edit manifest.json by hand** - use `tools/release.py`, then `release.py check`.
3. **Keep `.gitattributes` (`* -text`).** Git on Windows (`core.autocrlf=true`) would
   otherwise change line endings in the config files on commit, and the tool would reject
   them as "failed its checksum". `release.py check` verifies the file is there.
4. **Firmware = the PlatformIO app image** `<repo>\.pio\build\esp32dev\firmware.bin`,
   not a merged/factory image.
5. **Build release firmware on Windows** (or pin `board_build.partitions = default_8MB.csv`
   with exact case). The display repos contain a `default_8mb.csv` that Windows builds
   ignore (case-insensitive name match picks the framework's default_8MB.csv). On
   Linux/macOS that file would be used and the layout would not match `boot/partitions-default_8MB.bin`.

## Publishing a new firmware

Bump `firmwareVersion` in the board's `src/main.cpp`, build in VS Code, then from this folder:

```
python tools/release.py firmware "Rais Visio" display 4.1.7 C:\Users\...\Visio_Display\.pio\build\esp32dev\firmware.bin
python tools/release.py firmware "Rais Visio" mainboard 0.4.4 C:\Users\...\Visio_main\.pio\build\esp32dev\firmware.bin
python tools/release.py check
```

Then review the changes and commit + push with GitHub Desktop. The tool sees the new
version within about 5 minutes (GitHub's raw-file cache).

`release.py` refuses:
- an image for the wrong chip (display = ESP32, mainboard = ESP32-S3),
- an image bigger than the app partition (0x330000),
- firmware whose flash speed does not match the model's bootloader (40 vs 80 MHz),
- replacing an existing file with different content,
- a config whose `fireplaceModel` differs from the model (unless `--force`),
- unsafe slugs/versions, duplicate slugs, bootloader changes that would break published firmware.

## Other commands

```
python tools/release.py config "Rais Visio" "Standard 2026-10" C:\...\Visio_Display\data\config.json --default
python tools/release.py note   "Rais CVNQ" "text shown in red under the model in the tool"
python tools/release.py note   "Rais CVNQ" ""                     (removes the note)
python tools/release.py model  "Denver F6" --slug denver-f6 --display-bootloader esp32-dio-80m
python tools/release.py boot   esp32-dio-80m C:\...\.pio\build\esp32dev\bootloader.bin --name esp32-bootloader-dio-80m-8MB.bin
python tools/release.py check
```

Use the **display** repo's `data\config.json` as the model config: the display holds the
master copy (with `fireplaceModel`) and pushes it to the mainboard when settings are saved.
Config templates contain the generic calibration password (also compiled into every
firmware); MQTT credentials (`mqttSet.json`) are never stored here.
