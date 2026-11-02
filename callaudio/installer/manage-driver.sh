#!/bin/bash
# SPDX-License-Identifier: MIT
# Transactional HAL management. Never removes quarantine or deletes old bundles.
set -euo pipefail
[[ "$(uname -s)" == Darwin && "$(uname -m)" == arm64 ]] || { echo 'Requires Apple Silicon macOS' >&2; exit 1; }
[[ "$(id -u)" == 0 ]] || { echo 'Run with sudo' >&2; exit 1; }
hal=/Library/Audio/Plug-Ins/HAL
dst="$hal/NivoxAudio.driver"
history=/Library/Audio/.NivoxAudio-history
lock="$hal/.NivoxAudio.lock"
for p in /Library /Library/Audio /Library/Audio/Plug-Ins "$hal"; do
    [[ -d "$p" && ! -L "$p" ]] || { echo "Unsafe directory: $p" >&2; exit 1; }
done
[[ ! -L "$dst" && ( ! -e "$dst" || -d "$dst" ) ]] || { echo 'Unsafe installed bundle' >&2; exit 1; }
if [[ -e "$history" || -L "$history" ]]; then
    [[ -d "$history" && ! -L "$history" && "$(/usr/bin/stat -f '%u:%Lp' "$history")" == 0:700 ]] || { echo 'Unsafe backup directory' >&2; exit 1; }
else
    (umask 077; mkdir "$history")
fi
mkdir "$lock" || { echo 'Another transaction or stale lock exists; inspect before retrying' >&2; exit 1; }
txn=''; old=0; published=0; committed=0
finish() {
    rc=$?
    trap - EXIT HUP INT TERM
    if [[ "$committed" == 0 && -n "$txn" ]]; then
        if [[ "$published" == 1 && -e "$dst" ]]; then mv "$dst" "$txn/failed.driver" || rc=1; fi
        if [[ "$old" == 1 && -e "$txn/previous.driver" && ! -e "$dst" ]]; then mv "$txn/previous.driver" "$dst" || rc=1; fi
        /usr/bin/killall coreaudiod 2>/dev/null || true
        echo "Transaction failed; preserved evidence: $txn" >&2
    fi
    rmdir "$lock" || rc=1
    exit "$rc"
}
trap finish EXIT
trap 'exit 130' HUP INT TERM
mode="${1:-}"
case "$mode" in
    install) src="${2:?Missing source bundle}" ;;
    restore)
        src="${2:?Missing backup bundle}"
        # Only root-owned transaction backups created here can be restored.
        [[ "$src" == "$history"/transaction.*/previous.driver && ! -L "$(dirname "$src")" ]] || { echo 'Not a managed backup' >&2; exit 1; }
        [[ "$(cd "$(dirname "$src")" && pwd -P)" == "$(dirname "$src")" ]] || { echo 'Noncanonical backup path' >&2; exit 1; }
        ;;
    uninstall) src='' ;;
    *) echo 'Usage: manage-driver.sh install SOURCE | uninstall | restore BACKUP/previous.driver' >&2; exit 1 ;;
esac
if [[ -n "$src" ]]; then
    [[ -d "$src" && ! -L "$src" ]] || { echo 'Missing or unsafe source bundle' >&2; exit 1; }
    attrs="$(/usr/bin/xattr -lr "$src")"
    if [[ "$attrs" == *com.apple.quarantine* ]]; then
        echo 'Refusing quarantined bundle; no security attributes were changed' >&2; exit 1
    fi
    /usr/bin/codesign --verify --strict "$src"
fi
txn="$(/usr/bin/mktemp -d "$history/transaction.XXXXXXXX")"
if [[ -n "$src" ]]; then
    /usr/bin/ditto "$src" "$txn/candidate.driver"
    /usr/sbin/chown -R root:wheel "$txn/candidate.driver"
    /bin/chmod -R go-w "$txn/candidate.driver"
    /usr/bin/codesign --verify --strict "$txn/candidate.driver"
fi
if [[ -e "$dst" ]]; then
    old=1
    mv "$dst" "$txn/previous.driver"
fi
if [[ -n "$src" ]]; then
    published=1
    mv "$txn/candidate.driver" "$dst"
    /usr/bin/codesign --verify --strict "$dst"
fi
/usr/bin/killall coreaudiod 2>/dev/null || true
committed=1
printf 'Completed %s; retained backup: %s/previous.driver\n' "$mode" "$txn"
printf 'To reverse: sudo ./restore-driver.sh %q/previous.driver\n' "$txn"
