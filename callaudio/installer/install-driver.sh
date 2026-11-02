#!/bin/bash
# SPDX-License-Identifier: MIT
# Installs NivoxAudio.driver for ALL users of THIS Mac. Run it yourself, on your
# own Mac, from the extracted ZIP folder:  sudo ./install-driver.sh
# What it does: copies the plug-in to /Library/Audio/Plug-Ins/HAL (root:wheel)
# and restarts coreaudiod (all audio stops ~1-2 s). Nothing else.
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
src="$here/NivoxAudio.driver"
dst="/Library/Audio/Plug-Ins/HAL/NivoxAudio.driver"
[[ "$(uname -s)" == "Darwin" && "$(uname -m)" == "arm64" ]] || { echo "Requires Apple Silicon macOS" >&2; exit 1; }
[[ "$(id -u)" == "0" ]] || { echo "Run with sudo" >&2; exit 1; }
[[ -d "$src" ]] || { echo "Missing $src" >&2; exit 1; }
/usr/bin/codesign --verify --strict "$src" || { echo "Signature check failed" >&2; exit 1; }
/usr/bin/xattr -dr com.apple.quarantine "$src" 2>/dev/null || true
rm -rf "$dst"
/usr/bin/ditto "$src" "$dst"
/usr/sbin/chown -R root:wheel "$dst"
/bin/chmod -R go-w "$dst"
/usr/bin/killall coreaudiod 2>/dev/null || true
echo "Installed $dst. Devices: 'Nivox Microphone', 'Nivox Speaker'."
