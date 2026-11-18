#!/usr/bin/env python3
"""Control the RGB LED strip of the ACEMAGIC / ACEMAGICIAN AM08 Pro mini PC.

The strip is driven by the ITE IT5570/IT5571 embedded controller (EC). The
vendor's Windows tool (CYX_RGB_LED_Tool, 2024-04-11) sets the mode by writing
EC RAM through the ITE "I2EC" side channel on I/O ports 0x4E/0x4F. This script
performs the same register writes, derived from disassembling that tool:

    EC 0x2000/0x2001  chip ID, must read 0x55 0x70 or 0x55 0x71
    EC 0x1060 <- 0x80 one-time setup the tool does at start-up
    EC 0xC4BD <- 0x00 written before every mode change
    EC 0xC4F6 <- mode 0=auto 1=off 2=rainbow 3=breathing 4=colorcycle
    EC 0xC430         performance-knob level (1..3), read-only sanity check

Needs root (raw port I/O via /dev/port). The EC forgets the setting when the
machine loses power, so use "install" to re-apply it at boot and after resume.
"""

import argparse
import os
import shutil
import subprocess
import sys

MODES = {"auto": 0, "off": 1, "rainbow": 2, "breathing": 3, "colorcycle": 4}
MODE_NAMES = {v: k for k, v in MODES.items()}

PORT_INDEX = 0x4E
PORT_DATA = 0x4F

REG_CHIP_ID1 = 0x2000
REG_CHIP_ID2 = 0x2001
REG_SETUP = 0x1060
SETUP_VALUE = 0x80
RAM_BASE = 0xC400
OFF_POWER_LEVEL = 0x30
OFF_PRE_MODE = 0xBD
OFF_MODE = 0xF6

INSTALL_PATH = "/usr/local/sbin/am08pro-led"
UNIT_NAME = "am08pro-led.service"
UNIT_PATH = "/etc/systemd/system/" + UNIT_NAME
SLEEP_TARGETS = "suspend.target hibernate.target hybrid-sleep.target suspend-then-hibernate.target"


class PortIO:
    """Byte-wide x86 port I/O through /dev/port."""

    def __init__(self, path="/dev/port"):
        self.fd = os.open(path, os.O_RDWR)

    def outb(self, port, value):
        os.pwrite(self.fd, bytes([value & 0xFF]), port)

    def inb(self, port):
        return os.pread(self.fd, 1, port)[0]

    def close(self):
        os.close(self.fd)


class EC:
    """ITE I2EC access: index 0x2E/0x2F select sub-register 0x11 (address
    high), 0x10 (address low) and 0x12 (data)."""

    def __init__(self, io):
        self.io = io

    def _sub(self, reg):
        self.io.outb(PORT_INDEX, 0x2E)
        self.io.outb(PORT_DATA, reg)
        self.io.outb(PORT_INDEX, 0x2F)

    def _address(self, addr):
        self._sub(0x11)
        self.io.outb(PORT_DATA, (addr >> 8) & 0xFF)
        self._sub(0x10)
        self.io.outb(PORT_DATA, addr & 0xFF)

    def read(self, addr):
        self._address(addr)
        self._sub(0x12)
        return self.io.inb(PORT_DATA)

    def write(self, addr, value):
        self._address(addr)
        self._sub(0x12)
        self.io.outb(PORT_DATA, value)


class Refused(Exception):
    pass


def check_machine(force):
    try:
        with open("/sys/class/dmi/id/bios_version") as f:
            bios = f.read().strip()
    except OSError:
        bios = "?"
    if not bios.startswith("AM08Pro"):
        if not force:
            raise Refused(f"BIOS version {bios!r} does not look like an AM08 Pro "
                          "(expected 'AM08Pro...'). Use --force to override.")
        print(f"warning: unexpected BIOS version {bios!r}, continuing (--force)")
    return bios


def check_chip(ec):
    """The chip ID check is never skippable: other ITE chips use another layout."""
    id1, id2 = ec.read(REG_CHIP_ID1), ec.read(REG_CHIP_ID2)
    if id1 != 0x55 or id2 not in (0x70, 0x71):
        raise Refused(f"EC chip ID reads {id1:02x}{id2:02x}, expected 5570 or 5571. "
                      "Nothing was written.")
    return f"IT{id1:02x}{id2:02x}"


def setup(ec):
    before = ec.read(REG_SETUP)
    if before != SETUP_VALUE:
        ec.write(REG_SETUP, SETUP_VALUE)
    return before


def read_state(ec):
    return {
        "level": ec.read(RAM_BASE + OFF_POWER_LEVEL),
        "pre": ec.read(RAM_BASE + OFF_PRE_MODE),
        "mode": ec.read(RAM_BASE + OFF_MODE),
    }


def describe(state):
    mode = state["mode"]
    return (f"mode=0x{mode:02x} ({MODE_NAMES.get(mode, 'unknown')})  "
            f"reg 0xBD=0x{state['pre']:02x}  knob level=0x{state['level']:02x}")


def sane(state):
    """Evidence that 0xC4xx really is the RAM layout the vendor tool expects."""
    return state["mode"] in MODE_NAMES and 1 <= state["level"] <= 3


def run(ec, mode, force, out=print):
    """mode is None for a status read. Returns the final state."""
    out(f"EC chip: {check_chip(ec)}")
    before_setup = setup(ec)
    out(f"EC 0x1060 was 0x{before_setup:02x}"
        + ("" if before_setup == SETUP_VALUE else f", set to 0x{SETUP_VALUE:02x}"))
    state = read_state(ec)
    out("current: " + describe(state))
    if mode is None:
        if not sane(state):
            out("warning: these values are outside what the vendor tool expects "
                "(mode 0..4, knob level 1..3); the EC firmware may use another layout.")
        return state
    if not sane(state):
        if not force:
            raise Refused("Values are outside what the vendor tool expects (mode 0..4, "
                          "knob level 1..3), so this EC firmware may use a different "
                          "layout. Nothing was changed in the LED registers. "
                          "Use --force to write anyway.")
        out("warning: unexpected values, writing anyway (--force)")
    ec.write(RAM_BASE + OFF_PRE_MODE, 0x00)
    ec.write(RAM_BASE + OFF_MODE, MODES[mode])
    state = read_state(ec)
    out("now:     " + describe(state))
    if state["mode"] != MODES[mode]:
        raise Refused(f"Read-back mismatch: wrote 0x{MODES[mode]:02x}, "
                      f"EC reports 0x{state['mode']:02x}.")
    out(f"LED mode set to '{mode}'.")
    return state


def install(mode):
    src = os.path.realpath(__file__)
    if src != INSTALL_PATH:
        shutil.copyfile(src, INSTALL_PATH)
    os.chmod(INSTALL_PATH, 0o755)
    unit = f"""[Unit]
Description=Set AM08 Pro RGB LED strip to '{mode}'
After={SLEEP_TARGETS}

[Service]
Type=oneshot
ExecStart={INSTALL_PATH} {mode}

[Install]
WantedBy=multi-user.target {SLEEP_TARGETS}
"""
    with open(UNIT_PATH, "w") as f:
        f.write(unit)
    subprocess.run(["systemctl", "daemon-reload"], check=True)
    subprocess.run(["systemctl", "enable", UNIT_NAME], check=True)
    print(f"Installed {INSTALL_PATH} and enabled {UNIT_NAME} (mode '{mode}' at boot and after resume).")


def uninstall():
    subprocess.run(["systemctl", "disable", UNIT_NAME], check=False)
    for path in (UNIT_PATH, INSTALL_PATH):
        if os.path.exists(path):
            os.remove(path)
    subprocess.run(["systemctl", "daemon-reload"], check=False)
    print("Removed the service and the installed script.")


def main(argv=None):
    p = argparse.ArgumentParser(description="Control the AM08 Pro RGB LED strip.")
    p.add_argument("command", choices=["status", *MODES, "install", "uninstall"],
                   help="status: show current state; a mode name: apply it; "
                        "install: apply a mode now and at every boot/resume")
    p.add_argument("install_mode", nargs="?", choices=list(MODES), default="off",
                   help="mode for 'install' (default: off)")
    p.add_argument("--force", action="store_true",
                   help="skip the BIOS-name and plausibility checks (not the chip ID check)")
    args = p.parse_args(argv)

    if os.geteuid() != 0:
        sys.exit("This needs root for raw port I/O. Run it with sudo.")
    if args.command == "uninstall":
        uninstall()
        return

    mode = {"status": None, "install": args.install_mode}.get(args.command, args.command)
    try:
        print(f"BIOS: {check_machine(args.force)}")
        io = PortIO()
        try:
            run(EC(io), mode, args.force)
        finally:
            io.close()
    except Refused as e:
        sys.exit(f"Stopped: {e}")
    if args.command == "install":
        install(mode)


if __name__ == "__main__":
    main()
