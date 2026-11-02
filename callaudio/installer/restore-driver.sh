#!/bin/bash
# SPDX-License-Identifier: MIT
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
exec /bin/bash "$here/manage-driver.sh" restore "${1:?Usage: sudo ./restore-driver.sh BACKUP/previous.driver}"
