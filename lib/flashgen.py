"""Flash scripts written next to the generated images (Windows .bat and Linux .sh).

venus: everything is written from the bootloader (fastboot mode), no fastbootd: super.img holds all the
logical partitions with their LP metadata (lib/lpimg.py), boot/vendor_boot/dtbo go to both slots so the phone
boots whatever slot is active, vbmeta has verification off. Never touches xbl/abl/tz/persist/modemst.
"""
import os
import shutil

from .util import REPO, info

FLASH = [
    ("super", "super.img"),
    ("boot_a", "boot.img"), ("boot_b", "boot.img"),
    ("vendor_boot_a", "vendor_boot.img"), ("vendor_boot_b", "vendor_boot.img"),
    ("dtbo_a", "dtbo.img"), ("dtbo_b", "dtbo.img"),
]
VBMETA = ["vbmeta_a", "vbmeta_b", "vbmeta_system_a", "vbmeta_system_b"]


def _bat(lines):
    return "\r\n".join(lines) + "\r\n"


def _sh(lines):
    return "\n".join(lines) + "\n"


def _bat_flash(wipe):
    b = ['fastboot getvar product 2>&1 | findstr /r /c:"^product: *venus" >nul || (echo Not a Mi 11 ^(venus^). & exit /b 1)']
    b += ['fastboot flash %s "%%OUT%%%s" || goto :err' % (p, f) for p, f in FLASH]
    b += ['fastboot --disable-verity --disable-verification flash %s "%%OUT%%vbmeta.img" || goto :err' % p
          for p in VBMETA]
    if wipe:
        b.append("fastboot format:f2fs userdata || goto :err")
    b += ["fastboot set_active a || goto :err", "fastboot reboot"]
    return b


def _sh_flash(wipe):
    s = ['fastboot getvar product 2>&1 | grep -q "^product: *venus" || { echo "Not a Mi 11 (venus)."; exit 1; }']
    s += ['fastboot flash %s "$OUT/%s"' % (p, f) for p, f in FLASH]
    s += ['fastboot --disable-verity --disable-verification flash %s "$OUT/vbmeta.img"' % p for p in VBMETA]
    if wipe:
        s.append("fastboot format:f2fs userdata")
    s += ["fastboot set_active a", "fastboot reboot"]
    return s


def write(out, summary):
    # ---------------------------------------------------------------- clean install
    b = ["@echo off", "setlocal",
         "REM hmos2venus - CLEAN INSTALL: erases Android/HarmonyOS and ALL user data.",
         "REM Phone in fastboot mode (Power + Volume Down), bootloader unlocked.",
         "set OUT=%~dp0",
         "echo This ERASES the phone (super, userdata). Back up first.",
         "set /p OK=Type YES to continue: ",
         'if /I not "%OK%"=="YES" exit /b 1']
    b += _bat_flash(True)
    b += ["echo Done. First boot takes a few minutes.", "exit /b 0",
          ":err", "echo FAILED at the previous step.", "exit /b 1"]
    open(os.path.join(out, "install_clean.bat"), "w", newline="").write(_bat(b))

    s = ["#!/bin/sh", "# hmos2venus - CLEAN INSTALL: erases Android/HarmonyOS and ALL user data.",
         "# Phone in fastboot mode (Power + Volume Down), bootloader unlocked.",
         'set -e; OUT=$(dirname "$0")',
         'printf "This ERASES the phone. Type YES to continue: "; read OK; [ "$OK" = YES ] || exit 1']
    s += _sh_flash(True)
    s += ['echo "Done. First boot takes a few minutes."']
    open(os.path.join(out, "install_clean.sh"), "w").write(_sh(s))

    # ---------------------------------------------------------------- update, keeps data
    b = ["@echo off", "setlocal",
         "REM hmos2venus - UPDATE: reflashes the ROM, user data is KEPT.",
         "REM Only for a phone that already runs a hmos2venus build. Phone in fastboot mode.",
         "set OUT=%~dp0"]
    b += _bat_flash(False)
    b += ["exit /b 0", ":err", "echo FAILED at the previous step.", "exit /b 1"]
    open(os.path.join(out, "update_keep_data.bat"), "w", newline="").write(_bat(b))
    s = ["#!/bin/sh", "# hmos2venus - UPDATE: reflashes the ROM, user data is KEPT.", 'set -e; OUT=$(dirname "$0")']
    s += _sh_flash(False)
    open(os.path.join(out, "update_keep_data.sh"), "w").write(_sh(s))

    for f in ("get_logs.bat", "get_logs.sh"):
        shutil.copyfile(os.path.join(REPO, "flash", f), os.path.join(out, f))
    for f in os.listdir(out):
        if f.endswith(".sh"):
            os.chmod(os.path.join(out, f), 0o755)

    with open(os.path.join(out, "BUILD_INFO.txt"), "w") as fh:
        fh.write(summary)
    info("install_clean.(bat|sh), update_keep_data.(bat|sh), get_logs.(bat|sh), BUILD_INFO.txt")
