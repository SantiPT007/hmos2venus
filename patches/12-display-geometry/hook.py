"""Display geometry for the venus panel (1440x3200, 76 px punch hole top left, centre 166,99; LineageOS overlay).

Takes the window manager config shipped in sys_prod and rewrites the values
that describe the screen, so other keys of newer HarmonyOS versions are kept.
"""
import re

PATH = "etc/window/resources/display_manager_config.xml"
VALUES = {
    "dpi": "560",
    "cutoutArea": "128 61 76 76",
    "defaultDisplayCutoutPath": "M128 61 L204 61 v 76 h -76 Z",
    "curvedScreenBoundary": "0 0 0 0",
}


def apply(ctx):
    xml = ctx.image_file("sys_prod", PATH)
    if xml is None:
        ctx.warn("  /sys_prod/%s not found: geometry patch skipped" % PATH)
        return
    s = xml.decode("utf-8")
    for tag, val in VALUES.items():
        if re.search(r"<%s>.*?</%s>" % (tag, tag), s, re.S):
            s = re.sub(r"<%s>.*?</%s>" % (tag, tag), "<%s>%s</%s>" % (tag, val, tag), s, flags=re.S)
        else:
            s = s.replace("</Configs>", "  <%s>%s</%s>\n</Configs>" % (tag, val, tag))
    s = re.sub(r'<isWaterfallDisplay enable="true"', '<isWaterfallDisplay enable="false"', s)
    s = s.replace("<Configs>", "<!-- hmos2venus: Xiaomi Mi 11 (venus) geometry -->\n<Configs>", 1)
    ctx.vendor.write("etc/sweet/display_manager_config.xml", s.encode("utf-8"))
    ctx.info("  display_manager_config.xml: dpi 560, top-left cutout")
