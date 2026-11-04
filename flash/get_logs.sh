#!/bin/sh
# Pull the hmos2venus boot logs while the phone is in recovery (TWRP/OrangeFox, adb enabled).
DEST="$(dirname "$0")/logs_$(date +%Y%m%d_%H%M)"
mkdir -p "$DEST"
adb wait-for-recovery
# 1) kernel log, hilog snapshots and screenshots kept by hmos-preinit in /hmos on userdata (venus has no cache)
adb shell "mkdir -p /tmp/c && (mount -t f2fs /dev/block/by-name/userdata /tmp/c 2>/dev/null || mount -t f2fs /dev/block/bootdevice/by-name/userdata /tmp/c)"
adb pull /tmp/c/hmos "$DEST"
adb shell "rm -f /tmp/c/hmos/boot_inflight /tmp/c/hmos/PREVIOUS_BOOT_CRASHED; umount /tmp/c"
# 2) pstore (last kernel console before a reboot)
adb pull /sys/fs/pstore "$DEST/pstore"
# 3) HarmonyOS hilog / faultlog from /data (f2fs, not encrypted)
adb shell "mkdir -p /tmp/d && (mount -t f2fs /dev/block/by-name/userdata /tmp/d 2>/dev/null || mount -t f2fs /dev/block/bootdevice/by-name/userdata /tmp/d)"
adb shell "cd /tmp/d && tar czf /tmp/hmos_data_log.tgz log 2>/dev/null"
adb pull /tmp/hmos_data_log.tgz "$DEST/data_log.tgz"
adb shell "umount /tmp/d"
echo "Logs saved in $DEST"
