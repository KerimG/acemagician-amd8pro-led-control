# am08pro-led

Turn off (or change the mode of) the RGB LED strip on the ACEMAGIC / ACEMAGICIAN
AM08 Pro mini PC from Linux.

The vendor only ships a Windows tool. This is a single Python script with no
dependencies beyond the standard library that performs the same register
writes.

## Usage

Root is required, because the script does raw port I/O through `/dev/port`.

```
sudo ./am08pro-led.py status       # show chip ID, current mode, knob level
sudo ./am08pro-led.py off          # turn the strip off
sudo ./am08pro-led.py auto         # or: rainbow, breathing, colorcycle
```

The embedded controller forgets the setting when the machine loses power. To
reapply a mode at every boot and after resume from suspend:

```
sudo ./am08pro-led.py install      # defaults to "off"
sudo ./am08pro-led.py install breathing
sudo ./am08pro-led.py uninstall
```

`install` copies the script to `/usr/local/sbin/am08pro-led` and enables a
systemd unit, `am08pro-led.service`.

## Tested on

| Hardware | BIOS | Result |
| --- | --- | --- |
| AM08 Pro, Ryzen 7 7735HS | `AM08ProP01_P5C5V06` (2023-04-14) | `status`, `off` and the boot service work |

On this unit the controller keeps the mode across a warm reboot by itself.
After a full power loss it resets to `auto`, so the strip is lit during
firmware start-up and early boot until the service switches it off, about
eight seconds after the kernel starts.

Resume from suspend and the modes other than `off` use the same code path but
have not been confirmed on hardware yet.

## How it works

The strip is driven by the board's embedded controller, an ITE IT5570/IT5571.
The vendor's Windows tool (`CYX_RGB_LED_Tool`, build 2024-04-11) reaches the
controller's memory through the ITE "I2EC" side channel on I/O ports
`0x4E`/`0x4F`. The values below come from disassembling that tool.

| EC address | Access | Meaning |
| --- | --- | --- |
| `0x2000`, `0x2001` | read | Chip ID, must be `0x55 0x70` or `0x55 0x71` |
| `0x1060` | write `0x80` | Setup the tool does once at start-up |
| `0xC4BD` | write `0x00` | Written before every mode change |
| `0xC4F6` | write mode | `0` auto, `1` off, `2` rainbow, `3` breathing, `4` colorcycle |
| `0xC430` | read | Performance-knob level, `1` to `3` |

One EC access is three index/data pairs on the ports: sub-register `0x11`
takes the address high byte, `0x10` the low byte, and `0x12` is the data byte.

```
out 0x4E, 0x2E ; out 0x4F, 0x11 ; out 0x4E, 0x2F ; out 0x4F, <addr high>
out 0x4E, 0x2E ; out 0x4F, 0x10 ; out 0x4E, 0x2F ; out 0x4F, <addr low>
out 0x4E, 0x2E ; out 0x4F, 0x12 ; out 0x4E, 0x2F ; out/in 0x4F  <data>
```

## Safety checks

The script stops before changing the LED registers if:

- the BIOS version does not start with `AM08Pro`,
- the chip ID is not IT5570/IT5571,
- the current mode is not `0` to `4` or the knob level is not `1` to `3`, which
  would suggest a firmware with a different memory layout.

`--force` skips the first and third check. The chip ID check cannot be skipped.
After writing, the mode is read back and a mismatch is reported.

The writes go to controller RAM, which resets when the machine loses power.

## Requirements

- Python 3
- A kernel with `/dev/port` (`CONFIG_DEVPORT`) and no kernel lockdown. Secure
  Boot usually enables lockdown, which blocks `/dev/port`.

## If it does not work on your unit

Some AM08 Pro variants ship different controller firmware, and owners report
that even the vendor tool does nothing on them. The hardware fallback is to
unplug the "LOGO LED" connector on the top side of the motherboard.

## License

MIT, see [LICENSE](LICENSE).
