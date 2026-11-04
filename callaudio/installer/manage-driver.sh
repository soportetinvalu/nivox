#!/bin/bash
# SPDX-License-Identifier: MIT
# Transactional HAL management. Never removes quarantine or deletes old bundles.
set -euo pipefail
# Do not resolve root's mutation tools through a caller-controlled PATH.
export PATH=/usr/bin:/bin:/usr/sbin:/sbin
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
mode="${1:-}"
# Stable UIDs cannot identify the loaded binary, and killall is asynchronous.
# Refuse replacement BEFORE creating history/lock or touching any system state.
reject_replacement() {
    if [[ ( "$mode" == install || "$mode" == restore ) && -e "$dst" ]]; then
        echo 'Replacement refused: loaded driver version cannot be verified; installed driver was not changed' >&2
        exit 1
    fi
}
reject_replacement
if [[ -e "$history" || -L "$history" ]]; then
    [[ -d "$history" && ! -L "$history" && "$(/usr/bin/stat -f '%u:%Lp' "$history")" == 0:700 ]] || { echo 'Unsafe backup directory' >&2; exit 1; }
else
    (umask 077; mkdir "$history")
fi
mkdir "$lock" || { echo 'Another transaction or stale lock exists; inspect before retrying' >&2; exit 1; }
txn=''; old=0; published=0; committed=0; changed=0; probe=''
wait_for_hal() {
    local expected="$1" phase="$2" attempt rc deadline=$((SECONDS+40))
    # The signed C probe arms a five-second alarm before making any HAL call.
    # Also cap elapsed polling time, not just the number of failed queries.
    for ((attempt=0; attempt<40 && SECONDS<deadline; attempt++)); do
        if "$probe" "$expected" > "$txn/$phase-hal-state.log" 2>&1; then return 0;
        else rc=$?; fi
        if [[ "$rc" == 124 ]]; then
            echo "HAL probe exceeded its five-second deadline: $txn/$phase-hal-state.log" >&2
            return 1
        fi
        [[ "$SECONDS" -lt "$deadline" ]] || break
        /bin/sleep 1
    done
    echo "HAL state verification timed out (expected $expected): $txn/$phase-hal-state.log" >&2
    return 1
}
restart_and_verify() {
    local expected="$1" phase="$2" rc
    if /usr/bin/killall coreaudiod; then
        wait_for_hal "$expected" "$phase"
    else
        rc=$?
        echo "CoreAudio restart failed (exit $rc); HAL state is unverified" >&2
        return "$rc"
    fi
}
finish() {
    rc=$?
    trap - EXIT
    # Ignore catchable termination signals through recovery AND lock release.
    # Children inherit ignored signals, so repeated Ctrl-C cannot kill ditto.
    trap '' HUP INT TERM
    if [[ "$committed" == 0 && -n "$txn" ]]; then
        [[ "$rc" != 0 ]] || rc=1
        recovery_failed=0
        if [[ "$published" == 1 && -e "$dst" ]]; then
            mv "$dst" "$txn/failed.driver" || recovery_failed=1
        fi
        if [[ "$old" == 1 && -e "$txn/previous.driver" && ! -e "$dst" ]]; then
            # Prefer a copy so the original backup normally remains available.
            if /usr/bin/ditto "$txn/previous.driver" "$txn/recovery.driver" &&
               mv "$txn/recovery.driver" "$dst"; then
                :
            elif [[ ! -e "$dst" ]] && mv "$txn/previous.driver" "$dst"; then
                # Same-filesystem rename can rescue the original on ENOSPC.
                # Never publish the partial recovery copy or delete evidence.
                echo 'Recovery copy failed; original rescued by rename; previous.driver backup consumed' >&2
            else
                recovery_failed=1
            fi
        fi
        if [[ "$changed" == 1 && "$recovery_failed" == 0 ]]; then
            expected=absent
            [[ "$old" == 0 ]] || expected=present
            restart_and_verify "$expected" recovery || recovery_failed=1
        fi
        if [[ "$recovery_failed" == 1 ]]; then
            echo 'RECOVERY UNVERIFIED: inspect retained bundles and HAL before retrying' >&2
        elif [[ "$changed" == 1 ]]; then
            echo 'Previous filesystem and HAL presence state restored; audio health not tested' >&2
        fi
        echo "Transaction failed; preserved evidence: $txn" >&2
    fi
    rmdir "$lock" || rc=1
    exit "$rc"
}
trap finish EXIT
trap 'exit 130' HUP INT TERM
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
# A required, signed, read-only probe travels with this package. Missing/unsafe
# helpers fail before displacing any installed driver. No path override exists.
helper="$(cd "$(dirname "$0")" && pwd -P)/nv-driver-state"
[[ -f "$helper" && -x "$helper" && ! -L "$helper" ]] || { echo 'Missing or unsafe HAL state probe' >&2; exit 1; }
attrs="$(/usr/bin/xattr -lr "$helper")"
[[ "$attrs" != *com.apple.quarantine* ]] || { echo 'Refusing quarantined HAL state probe' >&2; exit 1; }
/usr/bin/codesign --verify --strict "$helper"
probe="$txn/nv-driver-state"
/usr/bin/ditto "$helper" "$probe"
/usr/sbin/chown root:wheel "$probe"
/bin/chmod 700 "$probe"
/usr/bin/codesign --verify --strict "$probe"
if [[ -n "$src" ]]; then
    /usr/bin/ditto "$src" "$txn/candidate.driver"
    /usr/sbin/chown -R root:wheel "$txn/candidate.driver"
    /bin/chmod -R go-w "$txn/candidate.driver"
    /usr/bin/codesign --verify --strict "$txn/candidate.driver"
fi
# Recheck under the lock: another transaction may have finished since preflight.
reject_replacement
if [[ -e "$dst" ]]; then
    old=1
    # Mark intent first: a signal immediately after mv must still recover it.
    changed=1
    mv "$dst" "$txn/previous.driver"
fi
if [[ -n "$src" ]]; then
    published=1
    mv "$txn/candidate.driver" "$dst"
    changed=1
    /usr/bin/codesign --verify --strict "$dst"
fi
expected=absent
[[ -z "$src" ]] || expected=present
restart_and_verify "$expected" transaction
committed=1
printf 'Completed %s; retained backup: %s/previous.driver\n' "$mode" "$txn"
echo "HAL presence verified ($expected); audio health and physical devices not tested"
if [[ "$old" == 1 ]]; then
    printf 'To reverse: sudo bash ./restore-driver.sh %q/previous.driver\n' "$txn"
elif [[ "$published" == 1 ]]; then
    echo 'To reverse initial installation: sudo bash ./uninstall-driver.sh'
fi
