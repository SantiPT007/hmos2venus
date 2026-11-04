"""Get the Mi 11 (venus) vendor blobs the port needs.

Accepted sources:
  * a MIUI/HyperOS fastboot ROM: its folder, its images/ folder or a super.img (sparse chunks
    super.img.0, .1, ... are joined); vendor (vendor_a) is taken from the super image;
  * a flashable ROM zip with block images (vendor.new.dat.br or vendor.new.dat + vendor.transfer.list);
  * a raw vendor.img (EROFS or ext4).

Only firmware and audio configuration files are taken (list in each patch's
"sweet_blobs"); they stay on your computer and are never part of the repo.
"""
import os
import re
import shutil
import subprocess
import zipfile

from .util import die, info, tool, run, mkdirs

BLOCK = 4096


def _brotli_decompress(src, dst):
    try:
        import brotli  # pip install brotli
        d = brotli.Decompressor()
        with open(src, "rb") as i, open(dst, "wb") as o:
            while True:
                b = i.read(1 << 20)
                if not b:
                    break
                o.write(d.process(b))
        return
    except ImportError:
        pass
    exe = shutil.which("brotli")
    if not exe:
        die("vendor.new.dat.br needs brotli: 'pip install brotli' or install the brotli tool")
    run([exe, "-d", "-f", "-o", dst, src])


def _rangeset(s):
    v = [int(x) for x in s.split(",")]
    return list(zip(v[1::2], v[2::2]))


def _sdat2img(transfer_list, new_dat, out_img):
    """Rebuild an image from a block-based OTA transfer list (versions 1-4)."""
    with open(transfer_list) as f:
        lines = [l.strip() for l in f if l.strip()]
    version = int(lines[0])
    cmds = lines[2:] if version == 1 else lines[4:]
    max_block = 0
    ops = []
    for line in cmds:
        parts = line.split(" ")
        if parts[0] in ("new", "zero", "erase"):
            rs = _rangeset(parts[1])
            ops.append((parts[0], rs))
            for _, e in rs:
                max_block = max(max_block, e)
    with open(new_dat, "rb") as src, open(out_img, "wb") as dst:
        dst.truncate(max_block * BLOCK)
        for op, rs in ops:
            if op != "new":
                continue
            for b, e in rs:
                dst.seek(b * BLOCK)
                left = (e - b) * BLOCK
                while left:
                    chunk = src.read(min(left, 16 << 20))
                    if not chunk:
                        die("vendor.new.dat shorter than its transfer list")
                    dst.write(chunk)
                    left -= len(chunk)


def _from_super(source, workdir):
    """vendor.img out of a fastboot ROM's super image (sparse chunks are joined by simg2img)."""
    d = source
    if os.path.isdir(os.path.join(d, "images")):
        d = os.path.join(d, "images")
    if os.path.isdir(d):
        chunks = sorted((f for f in os.listdir(d) if re.match(r"super\.img(\.\d+)?$", f)),
                        key=lambda f: int(f.rsplit(".", 1)[1]) if f[-1].isdigit() else -1)
        if not chunks:
            die("no super.img in %s: pass the fastboot ROM folder or a vendor.img" % source)
        chunks = [os.path.join(d, f) for f in chunks]
    else:
        chunks = [source]
    raw = os.path.join(workdir, "venus_super.raw")
    info("unpacking super (%d chunk%s)" % (len(chunks), "s" if len(chunks) > 1 else ""))
    if open(chunks[0], "rb").read(4) == b"\x3a\xff\x26\xed":  # sparse
        run([tool("simg2img")] + chunks + [raw])
    else:
        raw = chunks[0]
    tmp = mkdirs(os.path.join(workdir, "venus_super"))
    for name in ("vendor_a", "vendor"):
        p = subprocess.run([tool("lpunpack"), "-p", name, raw, tmp], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        if p.returncode == 0:
            break
    else:
        die("no vendor partition in the super image")
    if raw != chunks[0]:
        os.remove(raw)
    return os.path.join(tmp, name + ".img")


def vendor_image(source, workdir):
    """Return the path of a venus vendor image (EROFS or ext4) built from 'source'."""
    if not os.path.exists(source):
        die("venus ROM not found: %s" % source)
    out = os.path.join(workdir, "venus_vendor.img")
    if os.path.isfile(out):
        info("venus vendor image already unpacked")
        return out
    if os.path.isdir(source) or re.search(r"super\.img(\.\d+)?$", source):
        os.replace(_from_super(source, workdir), out)
        shutil.rmtree(os.path.join(workdir, "venus_super"), ignore_errors=True)
        return out
    if not zipfile.is_zipfile(source):
        return source  # raw vendor.img
    out = os.path.join(workdir, "venus_vendor.img")
    if os.path.isfile(out):
        info("venus vendor image already unpacked")
        return out
    z = zipfile.ZipFile(source)
    names = z.namelist()
    if "vendor.transfer.list" not in names:
        if "payload.bin" in names:
            die("payload.bin ROMs are not supported yet: extract vendor.img with "
                "payload-dumper and pass it with --venus-rom vendor.img")
        die("%s has no vendor.transfer.list / vendor.new.dat(.br)" % source)
    tmp = mkdirs(os.path.join(workdir, "venus_rom"))
    z.extract("vendor.transfer.list", tmp)
    tl = os.path.join(tmp, "vendor.transfer.list")
    if "vendor.new.dat.br" in names:
        info("unpacking vendor.new.dat.br")
        z.extract("vendor.new.dat.br", tmp)
        dat = os.path.join(tmp, "vendor.new.dat")
        _brotli_decompress(os.path.join(tmp, "vendor.new.dat.br"), dat)
        os.remove(os.path.join(tmp, "vendor.new.dat.br"))
    elif "vendor.new.dat" in names:
        z.extract("vendor.new.dat", tmp)
        dat = os.path.join(tmp, "vendor.new.dat")
    else:
        die("%s has no vendor.new.dat(.br)" % source)
    info("rebuilding vendor.img from the transfer list")
    _sdat2img(tl, dat, out + ".part")
    os.replace(out + ".part", out)
    shutil.rmtree(tmp, ignore_errors=True)
    return out


def _debugfs(img, cmd):
    p = subprocess.run([tool("debugfs"), "-R", cmd, img], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return p.stdout.decode(errors="replace"), p.stderr.decode(errors="replace")


def _is_erofs(img):
    with open(img, "rb") as f:
        f.seek(1024)
        return f.read(4) == b"\xe2\xe1\xf5\xe0"


def _erofs_tree(img):
    """EROFS vendor extracted once next to the image; returns its root."""
    root = img + ".tree"
    if not os.path.isdir(root):
        tmp = mkdirs(root + ".tmp")
        run([tool("extract.erofs"), "-i", img, "-x", "-o", tmp, "-s", "-f", "-T4"])
        sub = [d for d in os.listdir(tmp) if d != "config" and os.path.isdir(os.path.join(tmp, d))]
        if len(sub) != 1:
            die("extract.erofs failed for %s" % img)
        os.replace(os.path.join(tmp, sub[0]), root)
        shutil.rmtree(tmp, ignore_errors=True)
    return root


def check_vendor(img):
    if _is_erofs(img):
        if not os.path.isfile(os.path.join(_erofs_tree(img), "firmware", "a660_sqe.fw")):
            die("%s does not look like a venus vendor image (no /firmware/a660_sqe.fw)" % img)
        return
    out, err = _debugfs(img, "ls -p /firmware")
    if "a660_sqe.fw" not in out:
        die("%s does not look like a venus vendor image (no /firmware/a660_sqe.fw)\n%s" % (img, err[-500:]))


def _fetch_tree(root, blobs, outdir):
    got = []
    for b in blobs:
        src, dst = os.path.join(root, b["from"].rstrip("/")), os.path.join(outdir, b["to"])
        if b["from"].endswith("/"):
            if not os.path.isdir(src):
                die("blob directory /%s not found in the venus vendor image" % b["from"])
            shutil.copytree(src, dst, dirs_exist_ok=True)
            got += [os.path.join(b["to"], f) for f in os.listdir(src)]
            continue
        for _ in range(4):  # follow (vendor-relative) symlinks
            if not os.path.islink(src):
                break
            tgt = os.readlink(src)
            src = os.path.join(root, tgt[len("/vendor/"):]) if tgt.startswith("/vendor/") else \
                os.path.normpath(os.path.join(os.path.dirname(src), tgt))
        if not os.path.isfile(src):
            die("blob /%s not found in the venus vendor image" % b["from"])
        mkdirs(os.path.dirname(dst))
        shutil.copyfile(src, dst)
        got.append(b["to"])
    return got


def fetch(img, blobs, outdir):
    """Copy blobs ([{"from": "firmware/x", "to": "firmware/x"}], trailing / = directory)."""
    if _is_erofs(img):
        return _fetch_tree(_erofs_tree(img), blobs, outdir)
    got = []
    for b in blobs:
        src, dst = b["from"], os.path.join(outdir, b["to"])
        if src.endswith("/"):
            mkdirs(dst)
            # a fresh folder for every directory blob: the previous one may not be removable
            n = 0
            while os.path.exists(os.path.join(outdir, ".rdump%d" % n)):
                n += 1
            tmp = mkdirs(os.path.join(outdir, ".rdump%d" % n))
            _debugfs(img, "rdump /%s %s" % (src.rstrip("/"), tmp))
            name = os.path.basename(src.rstrip("/"))
            if not os.path.isdir(os.path.join(tmp, name)):
                die("blob directory /%s not found in the venus vendor image" % src)
            for f in sorted(os.listdir(os.path.join(tmp, name))):
                shutil.move(os.path.join(tmp, name, f), os.path.join(dst, f))
                got.append(os.path.join(b["to"], f))
            shutil.rmtree(tmp, ignore_errors=True)
        else:
            mkdirs(os.path.dirname(dst))
            path = "/" + src
            for _ in range(4):  # follow (vendor-relative) symlinks
                st, _ = _debugfs(img, "stat %s" % path)
                m = re.search(r'Fast link dest: "([^"]+)"', st)
                if not m:
                    break
                tgt = m.group(1)
                path = tgt[len("/vendor"):] if tgt.startswith("/vendor/") else \
                    os.path.normpath(os.path.join(os.path.dirname(path), tgt))
            _debugfs(img, "dump %s %s" % (path, dst))
            if not os.path.isfile(dst) or os.path.getsize(dst) == 0:
                die("blob /%s not found in the venus vendor image" % src)
            got.append(b["to"])
    return got
