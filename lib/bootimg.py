"""boot images for venus: LineageOS kernel + HarmonyOS ramdisk with hmos-preinit as /init.

The OTA's ramdisk.img is a "HARMONY!" container around a gzip'd newc cpio.
HarmonyOS keeps its real first-stage init at /bin/init_early; hmos-preinit is
added as /init (it maps the super partition, starts the kernel log keeper and
then execs /bin/init_early).
"""
import gzip
import hashlib
import io
import os
import struct
import zlib

from .util import die, info

CPIO_MAGIC = b"070701"


def unpack_ramdisk(data):
    """Return the cpio archive inside a HarmonyOS ramdisk.img (or a plain gzip/cpio)."""
    if data.startswith(CPIO_MAGIC):
        return data
    start = data.find(b"\x1f\x8b\x08")
    if start < 0 or start > 1 << 20:
        die("ramdisk.img: no gzip payload found (unknown format)")
    d = zlib.decompressobj(16 + zlib.MAX_WBITS)
    cpio = d.decompress(data[start:])
    if not cpio.startswith(CPIO_MAGIC):
        die("ramdisk.img: payload is not a newc cpio archive")
    return cpio


def read_cpio(cpio):
    """Parse a newc archive into a list of [name, mode, ino-fields..., data]."""
    entries = []
    p = 0
    while True:
        if cpio[p:p + 6] != CPIO_MAGIC:
            die("corrupt cpio at %d" % p)
        f = [int(cpio[p + 6 + 8 * i:p + 14 + 8 * i], 16) for i in range(13)]
        namesize, filesize = f[11], f[6]
        name = cpio[p + 110:p + 110 + namesize - 1].decode()
        p = (p + 110 + namesize + 3) & ~3
        body = cpio[p:p + filesize]
        p = (p + filesize + 3) & ~3
        if name == "TRAILER!!!":
            break
        entries.append({"name": name, "mode": f[1], "uid": f[2], "gid": f[3], "nlink": f[4],
                        "mtime": f[5], "rdevmajor": f[9], "rdevminor": f[10], "data": body})
    return entries


def write_cpio(entries):
    out = io.BytesIO()
    ino = 300000
    for e in sorted(entries, key=lambda e: e["name"]) + [{"name": "TRAILER!!!", "mode": 0, "uid": 0, "gid": 0,
                                                          "nlink": 1, "mtime": 0, "rdevmajor": 0,
                                                          "rdevminor": 0, "data": b""}]:
        name = e["name"].encode() + b"\0"
        ino += 1
        hdr = CPIO_MAGIC + b"".join(b"%08X" % v for v in (
            ino, e["mode"], 0, 0, e.get("nlink", 1), 0, len(e["data"]), 0, 0,
            e.get("rdevmajor", 0), e.get("rdevminor", 0), len(name), 0))
        out.write(hdr + name)
        out.write(b"\0" * ((-out.tell()) % 4))
        out.write(e["data"])
        out.write(b"\0" * ((-out.tell()) % 4))
    return out.getvalue()


def build_ramdisk(ota_ramdisk, preinit):
    entries = read_cpio(unpack_ramdisk(open(ota_ramdisk, "rb").read()))
    names = {e["name"] for e in entries}
    if "bin/init_early" not in names:
        die("the OTA ramdisk has no /bin/init_early: unsupported HarmonyOS ramdisk layout")
    entries = [e for e in entries if e["name"] not in ("init", "hmoslog")]
    entries.append({"name": "init", "mode": 0o100755, "uid": 0, "gid": 0, "nlink": 1, "mtime": 0,
                    "rdevmajor": 0, "rdevminor": 0, "data": open(preinit, "rb").read()})
    entries.append({"name": "hmoslog", "mode": 0o040755, "uid": 0, "gid": 0, "nlink": 2, "mtime": 0,
                    "rdevmajor": 0, "rdevminor": 0, "data": b""})
    info("ramdisk: %d entries, /init = hmos-preinit" % len(entries))
    return gzip.compress(write_cpio(entries), 9, mtime=0)


def build_boot(kernel, ramdisk, dtb, cmdline, out, pagesize=4096, os_version=0x220001aa):
    """Android boot image header v2, the layout the Xiaomi ABL on sweet boots."""
    k = open(kernel, "rb").read()
    d = open(dtb, "rb").read()
    r = ramdisk
    cmd = cmdline.encode()
    main, extra = cmd[:511], cmd[511:]
    if len(extra) >= 1024:
        die("kernel command line too long")
    sha = hashlib.sha1()
    for blob in (k, r, b"", b"", d):
        sha.update(blob)
        sha.update(struct.pack("<I", len(blob)))
    hdr = struct.pack("<8s10I16s512s32s1024sIQIIQ", b"ANDROID!", len(k), 0x8000, len(r), 0x01000000, 0, 0,
                      0x100, pagesize, 2, os_version, b"", main, sha.digest(), extra, 0, 0, 1660, len(d),
                      0x01f00000)

    def pad(b):
        return b + b"\0" * ((-len(b)) % pagesize)

    with open(out, "wb") as o:
        o.write(pad(hdr) + pad(k) + pad(r) + pad(d))
    info("boot.img: kernel %d, ramdisk %d, dtb %d bytes" % (len(k), len(r), len(d)))


def _entry(name, mode, data=b""):
    return {"name": name, "mode": mode, "uid": 0, "gid": 0, "nlink": 2 if mode & 0o040000 else 1, "mtime": 0,
            "rdevmajor": 0, "rdevminor": 0, "data": data}


def vendor_ramdisk(modules, load_order):
    """gzip'd cpio with /lib/modules/<name>.ko and /lib/modules/modules.load (hmos-preinit loads them in order)."""
    entries = [_entry("lib", 0o040755), _entry("lib/modules", 0o040755),
               _entry("lib/modules/modules.load", 0o100644, "".join(n + "\n" for n in load_order).encode())]
    for n in load_order:
        entries.append(_entry("lib/modules/" + n, 0o100644, open(modules[n], "rb").read()))
    info("vendor ramdisk: %d first-stage modules" % len(load_order))
    return gzip.compress(write_cpio(entries), 9, mtime=0)


def _pad(b, page=4096):
    return b + b"\0" * ((-len(b)) % page)


def build_v3(kernel, ramdisk, vramdisk, dtb, cmdline, vendor_cmdline, outdir, os_version=0x1a000179):
    """venus (Xiaomi ABL, boot header v3): boot.img (kernel + generic ramdisk) and vendor_boot.img
    (vendor ramdisk + dtb), with the load addresses of the stock vendor_boot (base 0)."""
    k = open(kernel, "rb").read()
    d = open(dtb, "rb").read()
    if len(cmdline) >= 1536 or len(vendor_cmdline) >= 2048:
        die("kernel command line too long")
    hdr = struct.pack("<8s4I4II1536s", b"ANDROID!", len(k), len(ramdisk), os_version, 1580, 0, 0, 0, 0, 3,
                      cmdline.encode())
    with open(os.path.join(outdir, "boot.img"), "wb") as o:
        o.write(_pad(hdr) + _pad(k) + _pad(ramdisk))
    vhdr = struct.pack("<8s5I2048sI16sIIQ", b"VNDRBOOT", 3, 4096, 0x8000, 0x01000000, len(vramdisk),
                       vendor_cmdline.encode(), 0x100, b"", 2112, len(d), 0x01f00000)
    with open(os.path.join(outdir, "vendor_boot.img"), "wb") as o:
        o.write(_pad(vhdr) + _pad(vramdisk) + _pad(d))
    info("boot.img: kernel %d, ramdisk %d; vendor_boot.img: ramdisk %d, dtb %d bytes" % (
        len(k), len(ramdisk), len(vramdisk), len(d)))


def empty_vbmeta(out):
    """vbmeta with no descriptors and verification + verity disabled (flags 3), like
    'avbtool make_vbmeta_image --flags 3': the ABL of an unlocked phone then boots the unsigned images."""
    hdr = struct.pack(">4sIIQQI10QQII48s80s", b"AVB0", 1, 0, 0, 0, 0, *([0] * 10), 0, 3, 0, b"", b"")
    assert len(hdr) == 256
    with open(out, "wb") as o:
        o.write(_pad(hdr))
