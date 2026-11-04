"""Write a 'super' image with Android LP (logical partition) metadata, without lpmake.

venus (like negroni) gets the logical partitions in one piece: no fastbootd needed, the
whole super partition is flashed from the bootloader. The layout is the one hmos-preinit reads (and AOSP's
liblp writes): 4 KiB reserved, the geometry at 4096 and its backup at 8192, metadata slots from 12288
(primary slots, then backup slots), the partition data from `first_data` on. Extents are in 512-byte
sectors. Checksums are SHA-256 as in liblp, so other LP readers accept the image too.
"""
import hashlib
import os
import struct

from .util import die, info, human

LP_RESERVED = 4096
GEOMETRY_SIZE = 4096
GEOMETRY_MAGIC = 0x616c4467
HEADER_MAGIC = 0x414C5030
SECTOR = 512
ATTR_READONLY = 1
TARGET_LINEAR = 0


def _geometry(metadata_max, slots, block):
    body = struct.pack("<II32sIII", GEOMETRY_MAGIC, 52, b"\0" * 32, metadata_max, slots, block)
    csum = hashlib.sha256(body).digest()
    body = struct.pack("<II32sIII", GEOMETRY_MAGIC, 52, csum, metadata_max, slots, block)
    return body.ljust(GEOMETRY_SIZE, b"\0")


def _metadata(parts, super_size, first_data):
    """parts: list of (name, readonly, first_sector, num_sectors)"""
    part_tab = b"".join(struct.pack("<36sIIII", n.encode()[:35].ljust(36, b"\0"), ATTR_READONLY if ro else 0, i, 1, 0)
                        for i, (n, ro, _, _) in enumerate(parts))
    ext_tab = b"".join(struct.pack("<QIQI", count, TARGET_LINEAR, first, 0) for (_, _, first, count) in parts)
    group_tab = struct.pack("<36sIQ", b"default".ljust(36, b"\0"), 0, 0)
    # block device: first_logical_sector u64, alignment u32, alignment_offset u32, size u64, name[36], flags u32
    bdev_tab = struct.pack("<QIIQ36sI", first_data // SECTOR, 0, 0, super_size, b"super".ljust(36, b"\0"), 0)
    tables = part_tab + ext_tab + group_tab + bdev_tab
    header_size = 128
    off = 0
    descs = []
    for tab, esize in ((part_tab, 52), (ext_tab, 24), (group_tab, 48), (bdev_tab, 64)):
        descs.append((off, len(tab) // esize, esize))
        off += len(tab)

    def header(hcsum):
        h = struct.pack("<IHHI32sI32s", HEADER_MAGIC, 10, 0, header_size, hcsum, len(tables),
                        hashlib.sha256(tables).digest())
        for d in descs:
            h += struct.pack("<III", *d)
        return h.ljust(header_size, b"\0")

    h = header(b"\0" * 32)
    h = header(hashlib.sha256(h).digest())
    return h + tables


def build(out, super_size, images, metadata_max=65536, slots=2, align=1 << 20):
    """images: list of (name, path, readonly). Writes a sparse-friendly raw image of super_size bytes."""
    first_data = LP_RESERVED + 2 * GEOMETRY_SIZE + 2 * slots * metadata_max
    first_data = (first_data + align - 1) // align * align
    parts = []
    pos = first_data
    for name, path, ro in images:
        size = os.path.getsize(path)
        size = (size + align - 1) // align * align
        parts.append((name, ro, pos // SECTOR, size // SECTOR))
        pos += size
    if pos > super_size:
        die("the logical partitions need %s, super has %s" % (human(pos), human(super_size)))
    meta = _metadata(parts, super_size, first_data)
    if len(meta) > metadata_max:
        die("LP metadata too large")
    meta = meta.ljust(metadata_max, b"\0")
    geo = _geometry(metadata_max, slots, 4096)
    with open(out, "wb") as f:
        f.truncate(super_size)                 # sparse: only metadata and images are written
        f.seek(LP_RESERVED)
        f.write(geo + geo)
        for _ in range(2 * slots):             # primary slots, then the backup slots
            f.write(meta)
        for (name, path, _), (_, _, first, _) in zip(images, parts):
            f.seek(first * SECTOR)
            with open(path, "rb") as src:
                while True:
                    chunk = src.read(16 << 20)
                    if not chunk:
                        break
                    f.write(chunk)
    for name, _, first, count in parts:
        info("%-10s at %s, %s" % (name, human(first * SECTOR), human(count * SECTOR)))
    return parts
