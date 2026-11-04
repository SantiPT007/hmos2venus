FROM debian:bookworm
RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
    clang-15 lld-15 llvm-15 gcc-aarch64-linux-gnu libc6-dev-arm64-cross binutils-aarch64-linux-gnu binutils-arm-linux-gnueabi \
    make bc bison flex libssl-dev libelf-dev python3 python-is-python3 cpio kmod git ca-certificates curl \
    device-tree-compiler mkbootimg android-sdk-libsparse-utils xz-utils lz4 rsync file && \
    ln -s /usr/bin/clang-15 /usr/local/bin/clang && ln -s /usr/bin/ld.lld-15 /usr/local/bin/ld.lld && \
    for t in llvm-ar llvm-nm llvm-objcopy llvm-objdump llvm-strip llvm-readelf llvm-ar; do ln -sf /usr/bin/$t-15 /usr/local/bin/$t; done && \
    rm -rf /var/lib/apt/lists/*
