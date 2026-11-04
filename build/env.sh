# Settings for the build scripts. Edit or export before running build/*.sh.
#
#   VENUS_KERNEL  LineageOS sm8350 kernel checkout (created by build_kernel.sh)
#   OH_SRC        OpenHarmony 5.1 sources (created by fetch_oh_sources.sh)
#   SYSROOT       HarmonyOS sysroot (created by make_sysroot.sh)
#   MESA_SRC      Mesa 26.1 source tree (any 26.1-devel snapshot with freedreno/kgsl)
REPO=$(cd "$(dirname "${BASH_SOURCE:-$0}")/.." && pwd)
: "${WORK:=$REPO/build/work}"
: "${VENUS_KERNEL:=$WORK/android_kernel_xiaomi_sm8350}"
: "${OH_SRC:=$WORK/openharmony}"
: "${SYSROOT:=$WORK/sysroot}"
: "${MESA_SRC:=$WORK/mesa}"
export REPO WORK VENUS_KERNEL OH_SRC SYSROOT MESA_SRC
export PATH="$REPO/build:$PATH"
mkdir -p "$WORK"
