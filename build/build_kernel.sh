#!/bin/bash
# Kernel: LineageOS android_kernel_xiaomi_sm8350 (lineage-23.2, 5.4 QGKI) + src/kernel/patches.
# Output: prebuilt/kernel-venus/{Image,venus.dtb,dtbo.img,modules/}
# Needs: clang/lld, aarch64-linux-gnu- and arm-linux-gnueabi- binutils, python3 (build/Dockerfile has them:
#   docker build -t hmos2venus-build build && docker run --rm -v "$PWD:$PWD" -w "$PWD" hmos2venus-build build/build_kernel.sh)
set -e
. "$(dirname "$0")/env.sh"
BASE=7ede20c8692e   # "arm64: configs: xiaomi: Enable CONFIG_EXFAT_FS" on lineage-23.2
if [ ! -d "$VENUS_KERNEL/.git" ]; then
  git clone -b lineage-23.2 https://github.com/LineageOS/android_kernel_xiaomi_sm8350 "$VENUS_KERNEL"
fi
cd "$VENUS_KERNEL"
if [ -z "$KEEP_TREE" ]; then
  git checkout -B hmos2venus "$BASE"
  git am "$REPO"/src/kernel/patches/*.patch
fi
M="O=out ARCH=arm64 LLVM=1 LLVM_IAS=1 CC=clang CLANG_TRIPLE=aarch64-linux-gnu- CROSS_COMPILE=aarch64-linux-gnu- CROSS_COMPILE_ARM32=arm-linux-gnueabi- TARGET_PRODUCT=venus"
make $M vendor/lahaina-qgki_defconfig vendor/debugfs.config vendor/xiaomi_QGKI.config vendor/venus_QGKI.config vendor/ohos.config
make $M -j"$(nproc)" Image dtbs modules
K=$REPO/prebuilt/kernel-venus; B=out/arch/arm64/boot
mkdir -p "$K/modules"
cp $B/Image "$K/"
cat $B/dts/vendor/qcom/*.dtb > "$K/venus.dtb"   # every lahaina dtb: ABL picks the one matching the board id
python3 "$REPO/build/mkdtboimg.py" "$K/dtbo.img" 4096 $B/dts/vendor/qcom/venus-sm8350-overlay.dtbo
make $M INSTALL_MOD_PATH=out/mods INSTALL_MOD_STRIP=1 modules_install
find "$K/modules" -name '*.ko' -delete
find out/mods -name '*.ko' -exec cp {} "$K/modules/" \;
echo "kernel -> $K ($(ls "$K/modules" | wc -l) modules)"
