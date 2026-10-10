"""
Maintain manifest.json in Flasher_repo (the files the Fireplace Flasher downloads).

Every file is checked before it is added, and published files are never
overwritten (versioned names are immutable - the flasher caches by name).
This script only edits files in the working tree; it never commits or pushes.

Usage (run from the Flasher_repo folder):
  python tools/release.py boot   <key> <file>                      add a shared boot file
  python tools/release.py model  "<Model>" --slug <slug> --display-bootloader <boot key>
                                 [--mainboard-bootloader <boot key>] [--note "..."]
  python tools/release.py firmware "<Model>" display|mainboard <version> <firmware.bin>
  python tools/release.py config "<Model>" "<config name>" <config.json> [--default] [--force]
  python tools/release.py note   "<Model>" "<text>"               (empty text removes the note)
  python tools/release.py check                                   verify every entry against the files

Boot keys: "partitions", "boot_app0", and bootloaders (any other key) such as
"esp32-dio-40m", "esp32-dio-80m", "esp32s3-dio-80m".

Board firmware = the PlatformIO app image: <repo>/.pio/build/<env>/firmware.bin
(NOT a merged/factory image). Boot files come from .pio/build/<env>/bootloader.bin,
partitions.bin and the framework's boot_app0.bin.
"""
import argparse
import datetime
import hashlib
import json
import os
import re
import struct
import sys
import zlib

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MANIFEST = os.path.join(ROOT, "manifest.json")

CHIP_IDS = {"display": 0, "mainboard": 9}            # ESP image header byte 12: 0 = ESP32, 9 = ESP32-S3
CHIP_NAMES = {"display": "ESP32", "mainboard": "ESP32-S3"}
APP_OFFSET = 0x10000
APP_PARTITION_SIZE = 0x330000                         # app0 in Arduino default_8MB
BLE_SNAPSHOT_LIMIT = 4096                             # display BLE config snapshot buffer
SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
VERSION_RE = re.compile(r"^[0-9A-Za-z][0-9A-Za-z._-]*$")


class CheckError(Exception):
    pass


def die(msg):
    sys.exit(f"ERROR: {msg}")


def load():
    if os.path.exists(MANIFEST):
        with open(MANIFEST, encoding="utf-8") as f:
            return json.load(f)
    return {"schemaVersion": 1, "updated": "", "boot": {}, "models": {}}


def save(m):
    m["updated"] = datetime.date.today().isoformat()
    with open(MANIFEST, "w", encoding="utf-8", newline="\n") as f:
        json.dump(m, f, indent=2, ensure_ascii=False)
        f.write("\n")
    print(f"manifest.json updated ({m['updated']})")


def read_input(path):
    if not os.path.isfile(path):
        die(f"file not found: {path}")
    with open(path, "rb") as f:
        return f.read()


def hashes(data):
    return {"size": len(data), "crc32": "%08x" % (zlib.crc32(data) & 0xFFFFFFFF),
            "md5": hashlib.md5(data).hexdigest()}


def safe_rel(rel):
    """Same rule as the flasher: relative, forward slashes, no '..', no drive/root."""
    if not rel or rel.startswith(("/", "\\")) or ":" in rel or "\\" in rel or \
            any(p in ("", ".", "..") for p in rel.split("/")):
        raise CheckError(f"unsafe path {rel!r}")
    return rel


def place(data, rel):
    """Write data to ROOT/rel. Refuses to replace an existing different file."""
    dst = os.path.join(ROOT, *safe_rel(rel).split("/"))
    if os.path.exists(dst):
        with open(dst, "rb") as f:
            if f.read() != data:
                die(f"{rel} already exists with DIFFERENT content - published files are immutable. "
                    f"Use a new version/name.")
        print(f"{rel} already present (identical)")
        return
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    with open(dst, "wb") as f:
        f.write(data)
    print(f"added {rel}")


def read_rel(rel):
    with open(os.path.join(ROOT, *safe_rel(rel).split("/")), "rb") as f:
        return f.read()


# ---------- content checks ----------
def check_bootloader(data, board=None):
    if data[:1] != b"\xE9" or len(data) < 24:
        raise CheckError("bootloader is not an ESP image")
    chip = data[12] | (data[13] << 8)
    if chip not in CHIP_IDS.values():
        raise CheckError(f"bootloader chip id {chip} is neither ESP32 (0) nor ESP32-S3 (9)")
    if board is not None and chip != CHIP_IDS[board]:
        raise CheckError(f"bootloader is for chip id {chip}, the {board} is {CHIP_NAMES[board]} (id {CHIP_IDS[board]})")


def check_partitions(data):
    if len(data) != 3072 or data[:2] != b"\xAA\x50":
        raise CheckError("partition table must be a 3072-byte file starting with AA 50")
    parts, i = [], 0
    while data[i:i + 2] == b"\xAA\x50":
        parts.append(struct.unpack_from("<BBII", data, i + 2))
        i += 32
    if data[i:i + 2] != b"\xEB\xEB" or hashlib.md5(data[:i]).digest() != data[i + 16:i + 32]:
        raise CheckError("partition table MD5 entry missing or wrong")
    if not any(t == 0 and off == APP_OFFSET for t, s, off, size in parts):
        raise CheckError("partition table has no app partition at 0x10000")
    if not any(t == 1 and s in (0x82, 0x83) for t, s, off, size in parts):
        raise CheckError("partition table has no spiffs/littlefs data partition")


def check_boot_entry(key, data):
    if key == "partitions":
        check_partitions(data)
    elif key == "boot_app0":
        if len(data) != 8192:
            raise CheckError("boot_app0 must be 8192 bytes")
    else:
        check_bootloader(data)


def check_app_image(data, board, boot_data):
    if data[:1] != b"\xE9" or len(data) < 24:
        raise CheckError("not an ESP app image (first byte is not 0xE9)")
    chip = data[12] | (data[13] << 8)
    if chip != CHIP_IDS[board]:
        raise CheckError(f"image is for chip id {chip}, the {board} needs {CHIP_NAMES[board]} (id {CHIP_IDS[board]})")
    if len(data) > APP_PARTITION_SIZE:
        raise CheckError(f"image is {len(data)} bytes, larger than the app partition ({APP_PARTITION_SIZE})")
    check_bootloader(boot_data, board)
    if data[3] != boot_data[3]:
        raise CheckError(f"image flash size/speed byte 0x{data[3]:02X} does not match the bootloader "
                         f"(0x{boot_data[3]:02X}) - wrong bootloader for this build (40 vs 80 MHz?)")


def boot_data(m, key):
    if key not in m["boot"]:
        raise CheckError(f"boot key {key!r} not in manifest")
    return read_rel(m["boot"][key]["file"])


def verify_published(m, only_model=None):
    """Re-check every published firmware against the (possibly changed) bootloader mapping."""
    for name, e in m["models"].items():
        if only_model and name != only_model:
            continue
        for board in ("display", "mainboard"):
            if board in e:
                try:
                    check_app_image(read_rel(e[board]["file"]), board, boot_data(m, e["bootloaders"][board]))
                except (CheckError, OSError, KeyError) as ex:
                    die(f"this change would break published {name} {board} {e[board].get('version')}: {ex}")


def model_entry(m, model):
    if model not in m["models"]:
        die(f"unknown model {model!r} - add it first with: release.py model ...")
    return m["models"][model]


# ---------- commands ----------
def cmd_boot(m, a):
    data = read_input(a.file)
    try:
        check_boot_entry(a.key, data)
    except CheckError as ex:
        die(str(ex))
    rel = f"boot/{a.name or os.path.basename(a.file)}"
    try:
        safe_rel(rel)
    except CheckError as ex:
        die(str(ex))
    # Every published firmware that uses this bootloader key must still match it
    for name, e in m["models"].items():
        for board in ("display", "mainboard"):
            if board in e and e.get("bootloaders", {}).get(board) == a.key:
                try:
                    check_app_image(read_rel(e[board]["file"]), board, data)
                except (CheckError, OSError) as ex:
                    die(f"this change would break published {name} {board} {e[board].get('version')}: {ex}")
    place(data, rel)
    m["boot"][a.key] = {"file": rel, **hashes(data)}
    save(m)


def cmd_model(m, a):
    if not SLUG_RE.match(a.slug):
        die("slug must be lowercase letters/digits separated by '-' (e.g. denver-f6)")
    for other, oe in m["models"].items():
        if other != a.model and oe.get("slug") == a.slug:
            die(f"slug {a.slug!r} is already used by {other!r}")
    e = m["models"].get(a.model, {})
    if e.get("slug") and e["slug"] != a.slug:
        die(f"{a.model!r} already has slug {e['slug']!r} - slugs cannot change (published file paths use them)")
    boots = {"display": a.display_bootloader, "mainboard": a.mainboard_bootloader or "esp32s3-dio-80m"}
    for board, key in boots.items():
        try:
            check_bootloader(boot_data(m, key), board)
        except (CheckError, OSError) as ex:
            die(f"{board} bootloader {key!r}: {ex}")
    e["slug"] = a.slug
    e["bootloaders"] = boots
    e.setdefault("configs", [])
    if a.note is not None:
        e["note"] = a.note
    m["models"][a.model] = e
    verify_published(m, a.model)
    save(m)


def cmd_firmware(m, a):
    e = model_entry(m, a.model)
    if not VERSION_RE.match(a.version):
        die("version may only contain letters, digits, '.', '_' and '-'")
    data = read_input(a.file)
    try:
        check_app_image(data, a.board, boot_data(m, e["bootloaders"][a.board]))
    except CheckError as ex:
        die(str(ex))
    if a.version.encode() not in data:
        print(f"WARNING: version string {a.version!r} not found inside the image - is this the right build?")
    rel = f"models/{e['slug']}/{a.board}/{e['slug']}-{a.board}-{a.version}.bin"
    place(data, rel)
    e[a.board] = {"version": a.version, "file": rel, **hashes(data)}
    save(m)


def cmd_config(m, a):
    e = model_entry(m, a.model)
    raw = read_input(a.file)
    try:
        doc = json.loads(raw.decode("utf-8-sig"))
    except Exception as ex:
        die(f"not valid JSON: {ex}")
    if not isinstance(doc, dict):
        die("config.json must be a JSON object")
    if doc.get("fireplaceModel") != a.model and not a.force:
        die(f"config fireplaceModel is {doc.get('fireplaceModel')!r}, expected {a.model!r} (use --force to override)")
    if len(raw) > BLE_SNAPSHOT_LIMIT:
        print(f"WARNING: {len(raw)} bytes > {BLE_SNAPSHOT_LIMIT} - the phone app's config snapshot would be cut off")
    slugname = re.sub(r"[^a-z0-9]+", "-", a.name.lower()).strip("-")
    if not slugname:
        die("config name must contain letters or digits")
    rel = f"models/{e['slug']}/config/{e['slug']}-config-{slugname}.json"
    clash = next((c for c in e["configs"] if c["file"] == rel and c["name"] != a.name), None)
    if clash:
        die(f"config name {a.name!r} maps to the same file as existing config {clash['name']!r} - choose another name")
    place(raw, rel)
    entry = {"name": a.name, "file": rel, **hashes(raw)}
    existing = next((i for i, c in enumerate(e["configs"]) if c["name"] == a.name), None)
    if existing is not None:
        if e["configs"][existing].get("default"):
            entry["default"] = True                       # keep its default flag and position
        e["configs"][existing] = entry
    else:
        e["configs"].append(entry)
    if a.default:
        for c in e["configs"]:
            c.pop("default", None)
        entry["default"] = True
    save(m)


def cmd_note(m, a):
    e = model_entry(m, a.model)
    if a.text:
        e["note"] = a.text
    else:
        e.pop("note", None)
    save(m)


def cmd_check(m, a):
    problems = 0

    def bad(msg):
        nonlocal problems
        print(msg)
        problems += 1

    def verify(rel, info, what):
        try:
            data = read_rel(rel)
        except CheckError as ex:
            bad(f"BAD      {what}: {ex}")
            return None
        except OSError:
            bad(f"MISSING  {what}: {rel}")
            return None
        real = hashes(data)
        diff = [k for k in ("size", "crc32", "md5") if info.get(k) != real[k]]
        if diff:
            bad(f"MISMATCH {what}: {rel} ({', '.join(diff)})")
        return data

    for k, b in m.get("boot", {}).items():
        data = verify(b.get("file", ""), b, f"boot {k}")
        if data is not None:
            try:
                check_boot_entry(k, data)
            except CheckError as ex:
                bad(f"BAD      boot {k}: {ex}")
    for key in ("partitions", "boot_app0"):
        if key not in m.get("boot", {}):
            bad(f"MISSING  boot {key} entry")
    try:
        with open(os.path.join(ROOT, ".gitattributes"), encoding="utf-8") as f:
            if not re.search(r"^\*\s+-text\s*$", f.read(), re.M):
                raise OSError
    except OSError:
        bad("BAD      .gitattributes must contain '* -text' - otherwise git changes line endings in the "
            "config files on commit and the flasher rejects them (checksum mismatch)")
    slugs = {}
    for name, e in m.get("models", {}).items():
        slug = e.get("slug", "")
        if not SLUG_RE.match(slug):
            bad(f"BAD      {name}: invalid slug {slug!r}")
        elif slug in slugs:
            bad(f"BAD      {name}: slug {slug!r} also used by {slugs[slug]!r}")
        slugs[slug] = name
        for board in ("display", "mainboard"):
            if board not in e:
                print(f"note     {name}: no {board} firmware published")
                continue
            data = verify(e[board].get("file", ""), e[board], f"{name} {board}")
            if data is not None:
                try:
                    check_app_image(data, board, boot_data(m, e.get("bootloaders", {}).get(board)))
                except (CheckError, OSError) as ex:
                    bad(f"BAD      {name} {board}: {ex}")
        defaults = 0
        for c in e.get("configs", []):
            data = verify(c.get("file", ""), c, f"{name} config {c.get('name')!r}")
            defaults += bool(c.get("default"))
            if data is not None:
                try:
                    doc = json.loads(data.decode("utf-8-sig"))
                    if not isinstance(doc, dict):
                        raise ValueError("not a JSON object")
                    if doc.get("fireplaceModel") != name:
                        print(f"note     {name} config {c.get('name')!r}: fireplaceModel is {doc.get('fireplaceModel')!r}")
                except ValueError as ex:
                    bad(f"BAD      {name} config {c.get('name')!r}: {ex}")
        if e.get("configs") and defaults != 1:
            print(f"note     {name}: {defaults} default configs (expected 1)")
    print("OK - all entries verified" if not problems else f"{problems} problem(s)")
    sys.exit(1 if problems else 0)


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("boot"); s.add_argument("key"); s.add_argument("file"); s.add_argument("--name")
    s = sub.add_parser("model"); s.add_argument("model"); s.add_argument("--slug", required=True)
    s.add_argument("--display-bootloader", required=True); s.add_argument("--mainboard-bootloader")
    s.add_argument("--note")
    s = sub.add_parser("firmware"); s.add_argument("model"); s.add_argument("board", choices=["display", "mainboard"])
    s.add_argument("version"); s.add_argument("file")
    s = sub.add_parser("config"); s.add_argument("model"); s.add_argument("name"); s.add_argument("file")
    s.add_argument("--default", action="store_true"); s.add_argument("--force", action="store_true")
    s = sub.add_parser("note"); s.add_argument("model"); s.add_argument("text")
    sub.add_parser("check")
    a = p.parse_args()
    m = load()
    {"boot": cmd_boot, "model": cmd_model, "firmware": cmd_firmware, "config": cmd_config,
     "note": cmd_note, "check": cmd_check}[a.cmd](m, a)


if __name__ == "__main__":
    main()
