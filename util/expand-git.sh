#!/bin/bash
# expand-git.sh — fetch full history + tags into the packaged pi-stomp tree.
#
# The .deb ships a bare `git init`-seeded repo (single commit, no history) so
# `git describe --dirty=*` works out of the box. This script fetches the
# complete history and tags from the remote recorded at build time, turning
# the shallow repo into a real clone that `git pull`, `git log`, and rich
# `git describe` (e.g. "v3.0.4-224-g…") all work against.
#
# Run on the device:
#     ~/pi-stomp/util/expand-git.sh
#
# Idempotent: if the repo already has full history, it no-ops.
set -euo pipefail

SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
META_DIR="$SRC_DIR/.git-meta"

if [ ! -d "$SRC_DIR/.git" ]; then
    echo "Error: $SRC_DIR is not a git repo (postinst git init not run?)" >&2
    exit 1
fi

ORIGIN=$(git -C "$SRC_DIR" remote get-url origin 2>/dev/null || true)
if [ -z "$ORIGIN" ] && [ -f "$META_DIR/origin-url" ]; then
    ORIGIN=$(cat "$META_DIR/origin-url")
    git -C "$SRC_DIR" remote add origin "$ORIGIN"
fi
if [ -z "$ORIGIN" ]; then
    echo "Error: no origin remote and no .git-meta/origin-url" >&2
    exit 1
fi

BRANCH=$(cat "$META_DIR/branch" 2>/dev/null || echo "main")

echo "==> Fetching full history + tags from $ORIGIN ($BRANCH)"
# Fetch the branch with full depth, plus all tags.
git -C "$SRC_DIR" fetch --tags origin "$BRANCH"

# Point the local branch at the fetched commit so the working tree aligns
# with the real history. We don't reset --hard (the working tree already
# matches HEAD from the packaged commit); we just update the ref to the
# fetched commit and fast-forward if possible.
FETCHED=$(git -C "$SRC_DIR" rev-parse "origin/$BRANCH")
CURRENT=$(git -C "$SRC_DIR" rev-parse HEAD)

if [ "$FETCHED" != "$CURRENT" ]; then
    # The packaged commit and the remote tip differ. Reset the branch ref
    # to the fetched tip so history is walkable. The working tree is already
    # clean (packaged state), so reset --soft keeps staged changes empty.
    git -C "$SRC_DIR" reset --soft "origin/$BRANCH"
    git -C "$SRC_DIR" branch -f "$BRANCH" "origin/$BRANCH"
    git -C "$SRC_DIR" symbolic-ref HEAD "refs/heads/$BRANCH"
fi

echo "==> Done"
# Write the EXPANDED marker so pi-stomp knows to use `git describe --dirty=*`
# for the version string, and pistomp-recovery knows to refuse apt upgrades
# that would overwrite the developer's git-managed tree.
touch "$SRC_DIR/.git/EXPANDED"
git -C "$SRC_DIR" describe --dirty='*' --always || true
