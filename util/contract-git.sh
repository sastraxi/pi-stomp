#!/bin/bash
# contract-git.sh — revert the pi-stomp tree to its packaged state.
#
# Inverse of expand-git.sh: removes the EXPANDED marker and resets the
# working tree + HEAD to the single packaged commit, so `dpkg --verify`
# passes and pistomp-recovery can apt-upgrade pi-stomp again.
#
# Run on the device:
#     ~/pi-stomp/util/contract-git.sh
#
# WARNING: discards any uncommitted changes and local commits on the
# current branch. The full git history fetched by expand-git.sh is
# preserved (no `git gc`), so you can re-expand to recover it.
set -euo pipefail

SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [ ! -d "$SRC_DIR/.git" ]; then
    echo "Error: $SRC_DIR is not a git repo" >&2
    exit 1
fi

if [ -f "$SRC_DIR/.git/EXPANDED" ]; then
    rm -f "$SRC_DIR/.git/EXPANDED"
fi

# Find the original packaged commit — it's the root commit that
# postinst created (message starts with "pi-stomp" and contains
# "(packaged)"). We reset to it so the working tree matches the .deb.
PACKAGED_SHA=$(git -C "$SRC_DIR" rev-list --max-parents=0 HEAD 2>/dev/null | while read -r sha; do
    if git -C "$SRC_DIR" log -1 --format='%s' "$sha" | grep -q '(packaged)'; then
        echo "$sha"
        break
    fi
done)

if [ -z "$PACKAGED_SHA" ]; then
    echo "Warning: could not find the packaged root commit; resetting to HEAD." >&2
    PACKAGED_SHA=$(git -C "$SRC_DIR" rev-parse HEAD)
fi

git -C "$SRC_DIR" reset --hard "$PACKAGED_SHA"
echo "==> Reverted to packaged state"
git -C "$SRC_DIR" describe --dirty='*' --always || true
echo "==> apt upgrades for pi-stomp re-enabled"
