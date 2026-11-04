# hmos2venus

**HarmonyOS 5 (OpenHarmony) for the Xiaomi Mi 11 (venus).**

A port of [hmos2sweet](https://github.com/tsryzenstech/hmos2sweet) (Redmi Note 10 Pro) to the Mi 11. It takes a
**HarmonyOS full OTA package** (`update_full_base.zip`) and turns it into a ROM you can flash on venus. The
userspace stays Huawei's. What changes is underneath it:

- the LineageOS 5.4 QGKI kernel for sm8350 with the OpenHarmony patches forward-ported from hmos2sweet;
- a set of **selectable patches** that replace the Kirin drivers with Snapdragon 888 equivalents.

> Experimental and **not yet booted on hardware**. No Huawei or Xiaomi image is included in this repository:
> the script takes them from the packages you provide. Use the resulting ROM for personal use only.

## Status

| Feature | Status |
|---|---|
| Kernel: LineageOS sm8350 5.4 + OpenHarmony patches (access token, binder, DRM auth, hmdfs/sharefs emulation, fs-verity stub, memcg, no-reboot) | ✅ builds |
| Boot chain: header v3 boot + vendor_boot, all lahaina dtbs, dtbo, empty vbmeta, super with LP metadata | ✅ images verified with AOSP unpack_bootimg / lpdump |
| First-stage modules (display, touch, battery, ADSP loader) from the vendor ramdisk, the rest from /vendor/lib/modules | 🧪 untested on the phone |
| Display 1440x3200 (msm_drm, DSI), GPU Adreno 660 (Mesa freedreno on kgsl) | 🧪 untested |
| WiFi QCA6390 (cnss2 + qcacld, firmware from modem_a), modem/ADSP bring-up, RIL, GPS, sensors, USB, thermal | 🧪 untested |
| Audio (cs35l41 + wcd938x), Bluetooth (QCA6390), haptics (aw8697), NFC, camera | 🔴 off: not ported yet |
| Fingerprint | 🔴 not yet |

## Requirements

- **Linux** with Python 3.8+, `simg2img`, `img2simg`, `lpunpack`, `e2fsprogs` (`debugfs`), `unzip`, `curl`.
- About **40 GB free**: extracted images, the unpacked venus super, temporary trees and output.
- A **HarmonyOS full OTA** of the Huawei Pura 70 (`update_full_base.zip`). `fetchfw` (below) can find one.
- **A Mi 11 fastboot ROM** (MIUI/HyperOS, the folder with `images/super.img.*`), or an extracted venus
  `vendor.img`: it provides the Adreno 660, IPA, WiFi and sensor firmware.
- A Mi 11 with an **unlocked bootloader** and `fastboot` in PATH.

## Getting the firmware: fetchfw

`fetchfw` looks for every HarmonyOS package available for the **Huawei Pura 70**, shows the list, downloads
the one you pick (resumable, sha256-checked) and then starts `hmos2venus.py` on it:

```sh
python3 fetchfw.py                                        # search, choose, download, port
python3 fetchfw.py -- --venus-rom ~/venus_fastboot_rom --patches defaults
python3 fetchfw.py --list                                 # only list
python3 fetchfw.py --url <package URL>                    # check and add a package you found
python3 fetchfw.py --offline                              # only the catalogs on this computer
```

On Windows `fetchfw.bat` does the same. Where the packages come from:

- the shared catalog of this repository (`lib/fetchfw_catalog.json`, also read online from GitHub, so new
  entries reach every copy of the tool);
- public pages that list Huawei full packages (their `update_full_base.zip` links are collected);
- Huawei's update services (the HarmonyOS 5 `hota/version-query` one and the older `Check.action`): they
  normally answer only requests signed by a real phone, so they rarely add anything;
- your own list (`~/.hmos2sweet_fw.json`), filled automatically with every package found or added with `--url`.

Huawei's CDN paths contain a random id, so the packages cannot be enumerated directly. **Every candidate is
verified before it is offered**: its `filelist.xml` and the headers inside the zip (`packageinfo.mbn`,
`VERSION.mbn`, read with HTTP range requests) must describe a full BASE package of a Pura 70 (`ADY-*`);
packages of other phones are skipped. To share a new Pura 70 package with everybody, add it to
`lib/fetchfw_catalog.json` with a pull request.

## Usage

```sh
git clone https://github.com/SantiPT007/hmos2venus && cd hmos2venus
tools/fetch_tools.sh                     # downloads the EROFS tools into tools/bin
python3 hmos2venus.py --ota ~/update_full_base.zip --venus-rom ~/venus_fastboot_rom --out out
```

The patch menu and the `--patches` / `--list` options are the same as in hmos2sweet
(`--patches defaults`, `--patches all,-60`, `--no-setup-wizard`).

## Installation

The output folder contains `super.img` (all HarmonyOS logical partitions with LP metadata), `boot.img`,
`vendor_boot.img`, `dtbo.img`, `vbmeta.img` (verification off) and the scripts:

| File | |
|---|---|
| `install_clean.bat` / `.sh` | **first install**: flashes everything from the bootloader and formats userdata |
| `update_keep_data.bat` / `.sh` | update from a previous hmos2venus build, **data is kept** |
| `get_logs.bat` / `.sh` | from a recovery: copies the kernel log and hilog snapshots that hmos-preinit keeps in `/hmos` on userdata |

1. Phone in fastboot (Power + Volume down).
2. `install_clean.bat` (Windows) or `./install_clean.sh` (Linux). It checks the phone is a venus and asks for `YES`.
3. The first boot takes a few minutes.

The scripts only write `super`, `boot_a/b`, `vendor_boot_a/b`, `dtbo_a/b`, `vbmeta*_a/b` and `userdata`. They never
touch `xbl`, `abl`, `tz`, `persist` or `modemst`. To go back to Android, flash the Mi 11 fastboot ROM.

## Repository layout

```
hmos2venus.py          converter (patch menu, OTA extraction, image build, flash scripts)
fetchfw.py             finds/downloads Pura 70 firmware and runs hmos2venus.py (lib/fetchfw.py)
lib/                   Python modules: Huawei OTA, EROFS, boot images, super (lpimg), venus ROM, patches, scripts, fetchfw
patches/NN-name/       one folder per patch: patch.json (+ hook.py) + files/ with the binaries
prebuilt/              kernel-venus (Image, dtbs, dtbo.img, modules) and hmos-preinit
src/                   sources of everything in patches/ and prebuilt/
  kernel/patches/        patches on LineageOS android_kernel_xiaomi_sm8350 (lineage-23.2, 5.4 QGKI)
  preinit/               hmos-preinit (/init: maps super, logs, charger mode, then HarmonyOS /bin/init_early)
  display/               DRM/KMS composer + dma-buf allocator (OpenHarmony VDI)
  mesa/                  Mesa 26.1 patches: OHOS EGL platform, YUV import
  audio/                 audio VDI HAL on tinyalsa
  userauth/ huks/ iam/   user_auth stub without TEE, software HUKS, OpenHarmony REE user_auth/pin_auth
  remoteproc/            sweet-rproc + patches to rmtfs/tqftpserv/pd-mapper (linux-msm)
  sensors/               hexagonrpcd for the ADSP + sensors HAL (SSC over QRTR/QMI)
  bluetooth/             Bluetooth vendor library for WCN3990
  ril/ gnss/ ir/         RIL over QMI (calls, SMS, data, supplementary services, USSD, IMS), GNSS over QMI LOC, IR
  camera/                camera: Android HAL bridge (cambridge), namespace launcher, OpenHarmony camera host (WIP)
  oobe/ idle/ stubs/     sweet-oobedone (skip setup), placeholders for Kirin daemons, link stubs
build/                 scripts to rebuild the kernel and the binaries (see docs/BUILDING.md)
tools/                 fetch_tools.sh (erofs-utils), gen_patch_docs.py
flash/                 log scripts
docs/                  architecture, patches, building, known issues
```

Every patch is described in [docs/PATCHES.md](docs/PATCHES.md). How the port works is explained in
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md); rebuilding the binaries in [docs/BUILDING.md](docs/BUILDING.md).

## Licenses

The scripts in this repository are Apache-2.0. The individual parts have different licenses:

- kernel and kernel patches, hmos-preinit: GPL-2.0;
- code derived from OpenHarmony: Apache-2.0;
- Mesa: MIT;
- tinyalsa and linux-msm: BSD-3-Clause;
- sqlite: public domain.

Details are in [NOTICE.md](NOTICE.md). HarmonyOS, Huawei and Xiaomi are trademarks of their respective
owners. This project is not affiliated with any of them.
