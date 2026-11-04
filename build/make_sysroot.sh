#!/bin/bash
# Build the HarmonyOS sysroot used by ohos-clang:
#   * headers (musl, libc++ with _LIBCPP_ABI_NAMESPACE=__h, Linux UAPI, libdrm)
#     and crt objects from build/sysroot-base.tar.xz (open source);
#   * the HarmonyOS shared libraries to link against, copied from the OTA
#     images extracted by hmos2venus.py (<work>/ota/system.img, vendor.img).
# usage: build/make_sysroot.sh <hmos2sweet work folder>
set -e
. "$(dirname "$0")/env.sh"
W=${1:?usage: make_sysroot.sh <hmos2sweet work folder with ota/system.img and ota/vendor.img>}
X="$REPO/tools/bin/extract.erofs"
rm -rf "$SYSROOT"; mkdir -p "$SYSROOT"
tar -xJf "$REPO/build/sysroot-base.tar.xz" -C "$SYSROOT"
LIBS="ld-musl-aarch64.so.1 libc.so libc++.so libbegetutil.z.so libcrypto_openssl.z.so libdisplay_buffer_hdi_impl.z.so
 libdisplay_composer_hdi_impl_1.2.z.so libdmabufheap.z.so libdrm.so libgraphic_utils.z.so libhdf_ipc_adapter.z.so
 libhdf_utils.z.so libhdi.z.so libhilog.so libhitrace_meter.so libipc_core.z.so libipc_single.z.so libnative_window.so
 libsamgr_proxy.z.so libsec_shared.z.so libshared_libz.z.so libsurface.z.so libsync_fence.z.so libutils.z.so
 libmetadata.z.so libbuffer_producer_sequenceable_1.0.z.so"
T="$WORK/sysroot_src"; rm -rf "$T"; mkdir -p "$T"
for p in system vendor; do "$X" -i "$W/ota/$p.img" -x -o "$T/$p" -s -f >/dev/null 2>&1 || true; done
for l in $LIBS; do
  f=$(find "$T" -name "$l" \( -type f -o -type l \) | head -1)
  [ -n "$f" ] || { echo "missing $l"; continue; }
  cp -L "$f" "$SYSROOT/usr/lib/$l"
done
for l in libc.so libz.so libm.so libdl.so libpthread.so librt.so; do
  [ -e "$SYSROOT/usr/lib/$l" ] || ln -sf libc.so "$SYSROOT/usr/lib/$l"
done
[ -e "$SYSROOT/usr/lib/libz.so" ] && [ ! -s "$SYSROOT/usr/lib/libz.so" ] && cp "$SYSROOT/usr/lib/libshared_libz.z.so" "$SYSROOT/usr/lib/libz.so"
chmod -R u+w "$T"; rm -rf "$T"
echo "sysroot ready: $SYSROOT"
