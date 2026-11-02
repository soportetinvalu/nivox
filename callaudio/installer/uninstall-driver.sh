#!/bin/bash
# SPDX-License-Identifier: MIT
# Removes the Nivox HAL plug-in and restarts coreaudiod.  sudo ./uninstall-driver.sh
set -euo pipefail
[[ "$(id -u)" == "0" ]] || { echo "Run with sudo" >&2; exit 1; }
rm -rf "/Library/Audio/Plug-Ins/HAL/NivoxAudio.driver"
/usr/sbin/pkgutil --forget local.christian.nivox.audio-driver.pkg 2>/dev/null || true
/usr/bin/killall coreaudiod 2>/dev/null || true
echo "Removed."
