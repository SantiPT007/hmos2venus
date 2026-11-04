"""USB gadget for venus.

/system/etc/init.usb.cfg imports /vendor/etc/init.${ohos.boot.hardware}.usb.cfg.
The OTA only has the Kirin one (init.<chip>.usb.cfg), so with hardware=venus no
USB gadget was ever set up: no hdc, no file transfer. This writes
init.venus.usb.cfg from the OTA's own file with the sm8350 controller
(a600000.dwc3) and without the Huawei-only functions.
"""
import os
import re

CONTROLLER = "a600000.dwc3"
HUAWEI_ONLY = ("hw_acm", "fxfer", "port_mode", "/sys/devices/platform/mt_usb", "start_atdiag",
               "${ohos.boot.sn}")  # empty on venus: keeps the fixed serial below


def apply(ctx):
    v = ctx.vendor
    etc = v.path("etc")
    cands = sorted(f for f in os.listdir(etc) if re.match(r"init\..+\.usb\.cfg$", f) and f != "init.venus.usb.cfg")
    if not cands:
        ctx.warn("  no init.<hardware>.usb.cfg in this OTA: USB gadget not configured")
        return
    src = cands[0]
    lines = v.read("etc/" + src).decode("utf-8").splitlines()
    out = []
    for l in lines:
        if any(h in l for h in HUAWEI_ONLY):
            continue
        l = re.sub(r"(setparam sys\.usb\.controller )\S+?(\"?,?)$", r"\g<1>%s\2" % CONTROLLER, l)
        l = l.replace("manufacturer HISILICON", "manufacturer Xiaomi")
        # serial: /proc/bootdevice/cid does not exist on venus, keep a fixed one
        # unless the bootloader passes ohos.boot.sn
        l = l.replace('"copy /proc/bootdevice/cid /config/usb_gadget/g1/strings/0x409/serialnumber"',
                      '"write /config/usb_gadget/g1/strings/0x409/serialnumber venus0001"')
        out.append(l)
    text = "\n".join(out) + "\n"
    # drop the trailing commas left by removed array items
    text = re.sub(r",(\s*\n\s*\])", r"\1", text)
    import json
    try:
        json.loads(text)
    except ValueError as e:
        ctx.warn("  %s could not be adapted (%s): USB gadget not configured" % (src, e))
        return
    if CONTROLLER not in text:
        ctx.warn("  %s sets no sys.usb.controller: adding it" % src)
    v.write("etc/init.venus.usb.cfg", text.encode("utf-8"))
    ctx.info("  init.venus.usb.cfg from %s (controller %s)" % (src, CONTROLLER))
