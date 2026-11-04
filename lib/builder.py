"""Apply the selected patches to the OTA images and write the venus ROM."""
import importlib.util
import json
import os
import shutil

from . import bootimg, erofs, lpimg, sweetrom
from .util import REPO, step, info, warn, die, mkdirs, rmtree, human

PREBUILT = os.path.join(REPO, "prebuilt")
SUPER_PARTS = ["system", "vendor", "chip_prod", "sys_prod", "cust", "patch", "version", "preload"]
# venus (Mi 11, SM8350) on the LineageOS 5.4 QGKI kernel: the stock vendor_boot command line plus the hmos.* options
CMDLINE = ("hardware=venus default_boot_device=soc/1d84000.ufshc console=ttyMSM0,115200n8 "
           "loop.max_part=7 buildvariant=user enforcing=0 hmos.permissive=1 hmos.noreboot=1 "
           "printk.devkmsg=on audit=0 firmware_class.path=/vendor/firmware")
# no androidboot.hardware=qcom: it overrides hardware=venus and init then looks for fstab.qcom (no /data)
VENDOR_CMDLINE = ("androidboot.memcg=1 lpm_levels.sleep_disabled=1 msm_rtb.filter=0x237 "
                  "service_locator.enable=1 androidboot.usbcontroller=a600000.dwc3 swiotlb=0 "
                  "cgroup.memory=nokmem,nosocket pcie_ports=compat iptable_raw.raw_before_defrag=1 "
                  "ip6table_raw.raw_before_defrag=1")
KERNEL = os.path.join(PREBUILT, "kernel-venus")
# first-stage modules (LineageOS venus BOOT_KERNEL_MODULES minus touch): loaded by hmos-preinit from the vendor
# ramdisk. Touch (fts_touch_spi, xiaomi_touch) is second stage: it asks for st_fts_k2.ftb from /vendor/firmware at
# probe time and, with /vendor not mounted yet, held preinit for 2 x 60 s of firmware fallback.
BOOT_MODULES = ["adsp_loader_dlkm.ko", "apr_dlkm.ko", "hwid.ko", "mmhardware_sysfs_dlkm.ko",
                "msm_drm.ko", "q6_notifier_dlkm.ko", "q6_pdr_dlkm.ko", "qti_battery_charger_main.ko",
                "snd_event_dlkm.ko"]
# legacy gadget driver: binds the USB controller on load and would take it from hdc
SKIP_MODULES = {"g_webcam.ko"}
# the GPT super partition of venus (BOARD_SUPER_PARTITION_SIZE)
SUPER_SIZE = 9126805504
INIT_CFG = "etc/init/hmos2sweet.cfg"


def _modname(f):
    return os.path.basename(f)[:-3].replace("-", "_")


def _depends(path):
    """Dependencies from a module's .modinfo ("depends=a,b"), as module names."""
    data = open(path, "rb").read()
    i = data.find(b"\0depends=")
    if i < 0:
        return []
    dep = data[i + 9:data.index(b"\0", i + 9)].decode()
    return [d.replace("-", "_") for d in dep.split(",") if d]


def module_order(moddir, wanted):
    """File names of 'wanted' (plus what they depend on) with every dependency before its users."""
    files = {_modname(f): f for f in os.listdir(moddir) if f.endswith(".ko")}
    order, seen = [], set()

    def visit(name):
        if name in seen or name not in files:
            return
        seen.add(name)
        for d in _depends(os.path.join(moddir, files[name])):
            visit(d)
        order.append(files[name])

    for w in wanted:
        visit(_modname(w))
    return order


class Context:
    """What a patch hook gets: the trees being edited plus helpers."""

    def __init__(self, build, patch):
        self._b = build
        self.patch = patch
        self.vendor = build.vendor
        self.work = build.work
        self.out = build.out
        self.images = build.images
        self.blobs = build.blobs_dir
        self.info = info
        self.warn = warn

    def system(self):
        return self._b.system_tree()

    def image_file(self, part, path):
        """Read one file from an untouched OTA partition image (bytes or None)."""
        if part not in self.images:
            return None
        return erofs.read_file(self.images[part], path, os.path.join(self.work, "tmp"))

    def list_image_dir(self, part, path):
        """[(name, is_dir)] of a directory in an untouched OTA partition image."""
        if part not in self.images:
            return []
        return erofs.list_dir(self.images[part], path)

    def cat_image_file(self, part, path):
        """Small file from an untouched OTA partition image, read without extracting (bytes or None)."""
        if part not in self.images:
            return None
        return erofs.cat_file(self.images[part], path)

    def save_removed(self, rel_name, data):
        p = os.path.join(self.out, "removed", rel_name)
        mkdirs(os.path.dirname(p))
        with open(p, "wb") as fh:
            fh.write(data)


class Build:
    def __init__(self, ota, sweet_rom, work, out, patches):
        self.ota = ota
        self.sweet_rom = sweet_rom
        self.work = mkdirs(work)
        self.out = mkdirs(out)
        self.patches = patches
        self.images = {}
        self.vendor = None
        self._system = None
        self.blobs_dir = os.path.join(self.work, "sweet_blobs")

    # ------------------------------------------------------------- inputs
    def extract_ota(self):
        step("Extracting the HarmonyOS OTA (%s)" % (self.ota.version or os.path.basename(self.ota.path)))
        names = [n for n in SUPER_PARTS + ["ramdisk"] if self.ota.has(n)]
        for need in ("system", "vendor", "ramdisk"):
            if need not in names:
                die("the OTA has no %s image: not a full HarmonyOS phone package?" % need)
        self.images = self.ota.extract(names, mkdirs(os.path.join(self.work, "ota")))

    def fetch_blobs(self):
        blobs = []
        for p in self.patches:
            blobs += p["sweet_blobs"] or []
        if not blobs:
            return
        step("Collecting firmware and audio configs from the venus ROM")
        if not self.sweet_rom:
            die("the selected patches need files from a Mi 11 ROM: pass --venus-rom")
        img = sweetrom.vendor_image(self.sweet_rom, self.work)
        sweetrom.check_vendor(img)
        rmtree(self.blobs_dir)
        got = sweetrom.fetch(img, blobs, mkdirs(self.blobs_dir))
        info("%d files" % len(got))

    # ------------------------------------------------------------ vendor
    def build_vendor(self):
        step("Building vendor.img")
        self.vendor = erofs.Tree(self.images["vendor"], os.path.join(self.work, "vendor_tree"), "vendor")
        self.vendor.extract()
        init = {"jobs": [], "services": []}
        ueventd = []
        for p in self.patches:
            info("[%s] %s" % (p.id, p.name))
            self._apply_patch(p, init, ueventd)
        self._add_modules(init)
        self._write_init(init)
        self._write_ueventd(ueventd)
        return self.vendor.build(os.path.join(self.out, "vendor.img"))

    def _apply_patch(self, p, init, ueventd):
        v = self.vendor
        for d in p["vendor_dirs"] or []:
            v.add_dir(d["dst"], d.get("mode", "0755"), label_from=d.get("label_from", "firmware"))
        for f in p["vendor_files"] or []:
            if f.get("only_if_exists") and not v.exists(f["dst"]):
                info("  skip %s (not in this OTA)" % f["dst"])
                continue
            v.add_file(p.file(f["src"]), f["dst"], f.get("mode", "0644"), label_from=f.get("label_from"),
                       uid=f.get("uid", 0), gid=f.get("gid", 2000))
        for l in p["vendor_symlinks"] or []:
            v.add_symlink(l["dst"], l["target"])
        for rel in p["vendor_remove"] or []:
            if v.remove(rel):
                info("  removed /vendor/%s" % rel)
        for b in p["sweet_blobs"] or []:
            src = os.path.join(self.blobs_dir, b["to"])
            if os.path.isdir(src):
                for root, dirs, files in os.walk(src):
                    dirs.sort()
                    rel = os.path.relpath(root, self.blobs_dir).replace(os.sep, "/")
                    for f in sorted(files):
                        v.add_file(os.path.join(root, f), rel + "/" + f, "0644")
            else:
                v.add_file(src, b["to"], "0644")
        for m in p["markers"] or []:
            v.write(m, b"")
        frag = p["init"] or {}
        for job in frag.get("jobs", []):
            init["jobs"].append(dict(job))
        init["services"] += frag.get("services", [])
        ueventd += p["ueventd"] or []
        if p["hook"]:
            spec = importlib.util.spec_from_file_location("hook_" + p.id.replace("-", "_"), p.file(p["hook"]))
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            mod.apply(Context(self, p))
        for rel in p["system_remove"] or []:
            s = self.system_tree()
            data_dir = os.path.join(self.out, "removed", "system", rel)  # removed/<partition>/<path>
            src = s.path(rel)
            if os.path.lexists(src):
                if os.path.isdir(src):
                    shutil.copytree(src, data_dir, symlinks=True, dirs_exist_ok=True)
                else:
                    mkdirs(os.path.dirname(data_dir))
                    shutil.copy2(src, data_dir)
                s.remove(rel)
                info("  removed /%s from system (saved in removed/)" % rel)
            else:
                warn("  /%s not in this system image" % rel)

    def _write_init(self, init):
        jobs = []
        index = {}
        for j in init["jobs"]:
            if j["name"] in index:
                index[j["name"]]["cmds"] += j["cmds"]
            else:
                j = {"name": j["name"], **({"condition": j["condition"]} if "condition" in j else {}),
                     "cmds": list(j["cmds"])}
                index[j["name"]] = j
                jobs.append(j)
        cfg = {"jobs": jobs, "services": init["services"]}
        self.vendor.write(INIT_CFG, json.dumps(cfg, indent=4) + "\n")
        info("init: %d jobs, %d services in /vendor/%s" % (len(jobs), len(init["services"]), INIT_CFG))

    def _write_ueventd(self, lines):
        if not lines:
            return
        rel = "etc/ueventd.config"
        if not self.vendor.exists(rel):
            warn("no /vendor/etc/ueventd.config: device node permissions left to init")
            return
        cur = self.vendor.read(rel).decode(errors="replace")
        add = [l for l in lines if l.startswith("#") or l.split()[0] not in cur]
        if add:
            self.vendor.write(rel, (cur.rstrip("\n") + "\n\n" + "\n".join(add) + "\n").encode())

    # ------------------------------------------------------------ system
    def system_tree(self):
        if self._system is None:
            self._system = erofs.Tree(self.images["system"], os.path.join(self.work, "system_tree"), "system")
            self._system.extract()
        return self._system

    def build_system(self):
        if self._system is None:
            return None
        step("Building system.img")
        return self._system.build(os.path.join(self.out, "system.img"))

    # -------------------------------------------------------------- boot
    def _modules(self):
        d = os.path.join(KERNEL, "modules")
        if not os.path.isdir(d):
            die("%s missing: build the kernel first (build/build_kernel.sh)" % d)
        return d

    def _add_modules(self, init):
        """The second-stage modules go to /vendor/lib/modules; init loads them in dependency order at the end of
        pre-init, after the patches mounted the firmware partitions (QCA6390 takes amss.bin/bdwlan from modem_a)."""
        d = self._modules()
        first = set(module_order(d, BOOT_MODULES))
        rest = [f for f in module_order(d, sorted(set(os.listdir(d)) - SKIP_MODULES)) if f not in first]
        self.vendor.add_dir("lib/modules", "0755")
        for f in rest:
            self.vendor.add_file(os.path.join(d, f), "lib/modules/" + f, "0644")
        init["jobs"].append({"name": "pre-init", "cmds": ["insmod /vendor/lib/modules/" + f for f in rest]})
        info("%d kernel modules in /vendor/lib/modules" % len(rest))

    def build_boot(self):
        step("Building boot.img, vendor_boot.img, dtbo.img, vbmeta.img")
        rd = bootimg.build_ramdisk(self.images["ramdisk"], os.path.join(PREBUILT, "ramdisk", "init"))
        d = self._modules()
        order = module_order(d, BOOT_MODULES)
        vrd = bootimg.vendor_ramdisk({f: os.path.join(d, f) for f in order}, order)
        bootimg.build_v3(os.path.join(KERNEL, "Image"), rd, vrd, os.path.join(KERNEL, "venus.dtb"), CMDLINE,
                         VENDOR_CMDLINE, self.out)
        shutil.copyfile(os.path.join(KERNEL, "dtbo.img"), os.path.join(self.out, "dtbo.img"))
        bootimg.empty_vbmeta(os.path.join(self.out, "vbmeta.img"))

    def build_super(self):
        """All logical partitions in one super image, flashed from the bootloader (lib/lpimg.py)."""
        step("Building super.img")
        parts = [(n, os.path.join(self.out, n + ".img"), True) for n in SUPER_PARTS
                 if os.path.isfile(os.path.join(self.out, n + ".img"))]
        raw = os.path.join(self.work, "super.raw")
        layout = lpimg.build(raw, SUPER_SIZE, parts)
        # LP metadata + every partition extent, written explicitly (zeros too) so no old data shows through
        ranges = [(0, layout[0][2] * lpimg.SECTOR)] + [(first * lpimg.SECTOR, count * lpimg.SECTOR)
                                                       for _, _, first, count in layout]
        for f in os.listdir(self.out):
            if f.startswith("super.img"):
                os.unlink(os.path.join(self.out, f))
        pieces = lpimg.sparse_pieces(raw, ranges, os.path.join(self.out, "super.img"))
        os.unlink(raw)
        for n, path, _ in parts:
            os.unlink(path)
        info("super.img.0..%d (%s, sparse pieces flashed one after the other)" % (
            len(pieces) - 1, human(sum(os.path.getsize(p) for p in pieces))))

    # ------------------------------------------------------------ others
    def copy_untouched(self):
        step("Copying the unchanged partitions")
        for n in SUPER_PARTS:
            if n in self.images and not os.path.exists(os.path.join(self.out, n + ".img")):
                dst = os.path.join(self.out, n + ".img")
                try:
                    os.link(self.images[n], dst)
                except OSError:
                    shutil.copyfile(self.images[n], dst)
                info("%-10s %s" % (n, human(os.path.getsize(dst))))
