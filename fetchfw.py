#!/usr/bin/env python3
"""fetchfw: pick a HarmonyOS firmware for the Huawei Pura 70, download it and port it with hmos2venus.

  python3 fetchfw.py                       # list every package found, choose, download, port
  python3 fetchfw.py --offline             # only the catalog shipped with this copy
  python3 fetchfw.py --url <package URL>   # add a package you found (folder, filelist.xml or .zip URL)
  python3 fetchfw.py --list                # only list
  python3 fetchfw.py -- --sweet-rom lineage-sweet.zip --patches defaults   # arguments for hmos2venus.py

Only full packages of the Pura 70 (device model ADY-*) are accepted: each candidate is checked online
(filelist.xml and the package headers, read with range requests) before it is offered.
"""
import argparse
import concurrent.futures
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from lib import fetchfw  # noqa: E402


def parse_args():
    argv = sys.argv[1:]
    rest = []
    if "--" in argv:
        i = argv.index("--")
        argv, rest = argv[:i], argv[i + 1:]
    ap = argparse.ArgumentParser(description="Find and download Huawei Pura 70 HarmonyOS firmware, then port it")
    ap.add_argument("--url", action="append", default=[], help="package URL to check (repeatable)")
    ap.add_argument("--offline", action="store_true",
                    help="do not look online (shared catalog, Huawei update service): only the local catalogs")
    ap.add_argument("--list", action="store_true", help="list the verified packages and exit")
    ap.add_argument("--choose", type=int, help="pick package N without asking")
    ap.add_argument("--dest", default="firmware", help="download folder (default: firmware)")
    ap.add_argument("--download-only", action="store_true", help="do not start hmos2venus.py afterwards")
    ap.add_argument("--no-verify", action="store_true",
                    help="trust the catalog entries without checking them online (faster)")
    a = ap.parse_args(argv)
    a.rest = rest
    return a


def gb(n):
    return "%.2f GB" % (n / 1e9) if n else "?"


def main():
    a = parse_args()
    print("fetchfw - HarmonyOS firmware for the Huawei Pura 70 -> hmos2venus")
    cands = fetchfw.load_catalogs()
    cands += [{"url": u, "source": "command line"} for u in a.url]
    if not a.offline:
        print("looking for Pura 70 firmware online ...")
        cands += fetchfw.load_remote_catalogs()
        cands += fetchfw.scan_link_pages()
        on = fetchfw.query_online()
        if not on:
            print("  Huawei's update service gave no package (it normally answers only signed phone requests)")
        cands += on

    seen, todo, valid = set(), [], []
    for c in cands:
        try:
            zipurl = fetchfw.normalize(c["url"])[1]
        except fetchfw.FwError as e:
            print("skip: %s" % e)
            continue
        if zipurl not in seen:
            seen.add(zipurl)
            todo.append((zipurl, c))

    def check(item):
        zipurl, c = item
        if a.no_verify and c.get("source") != "command line" and c.get("model"):
            info = {"zip": zipurl, "size": c.get("size", 0), "sha256": c.get("sha256", ""),
                    "model": c.get("model", ""), "version": c.get("version", "")}
            return (info if fetchfw.is_pura70(info) and info["size"] else None), None
        try:
            return fetchfw.check_candidate(c["url"], log=lambda *_: None), None
        except fetchfw.FwError as e:
            return None, str(e)

    print("checking %d packages ..." % len(todo))
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex:
        results = list(ex.map(check, todo))
    for (zipurl, c), (info, err) in zip(todo, results):
        if info is None:
            if err and c.get("source") in ("catalog", "user catalog", "command line", "online catalog"):
                print("  skipped %s: %s" % (zipurl, err))
            continue
        print("  ok: %s, %s, %s (%s)" % (info.get("model"), info.get("version"), gb(info["size"]), c.get("source")))
        if c.get("source") in ("command line", "online", "public list"):
            fetchfw.remember(info)
        info["source"] = c.get("source", "")
        info["os"] = c.get("os", "")
        valid.append(info)

    if not valid:
        print("\nNo Pura 70 package available. Find the URL of a full package (update_full_base.zip of an "
              "ADY-* device) and pass it with --url.")
        return 1

    valid.sort(key=lambda i: i.get("versionCode") or i.get("version") or "", reverse=True)
    print("\nAvailable firmware (Huawei Pura 70):")
    print("  #  %-34s %-10s %-9s %s" % ("version", "model", "size", "source"))
    for n, i in enumerate(valid, 1):
        print("  %-2d %-34s %-10s %-9s %s" % (n, i.get("version", "?"), i.get("model", "?"), gb(i["size"]),
                                            i.get("source", "")))
    if a.list:
        return 0

    choice = a.choose
    while choice is None:
        try:
            s = input("\nFirmware to download [1-%d, q = quit]: " % len(valid)).strip()
        except EOFError:
            return 1
        if s.lower() in ("q", "quit", ""):
            return 0
        if s.isdigit() and 1 <= int(s) <= len(valid):
            choice = int(s)
    if not 1 <= choice <= len(valid):
        print("no package %d" % choice)
        return 2
    info = valid[choice - 1]
    if not info.get("sha256") and not a.no_verify:
        print("note: no sha256 in filelist.xml, the download cannot be verified")
    try:
        path = fetchfw.download(info, a.dest)
    except fetchfw.FwError as e:
        print("error: %s" % e)
        return 1
    print("firmware ready: %s" % path)
    if a.download_only:
        return 0

    rest = list(a.rest)
    if "--sweet-rom" not in rest and sys.stdin.isatty():
        s = input("Redmi Note 10 Pro ROM zip (or vendor.img) for the sweet firmware blobs [Enter = skip]: ").strip()
        if s:
            rest += ["--sweet-rom", s.strip('"')]
    cmd = [sys.executable, os.path.join(os.path.dirname(os.path.abspath(__file__)), "hmos2venus.py"),
           "--ota", path] + rest
    print("running: %s" % " ".join(cmd))
    return subprocess.call(cmd)


if __name__ == "__main__":
    sys.exit(main())
