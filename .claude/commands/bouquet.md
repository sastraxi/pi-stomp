# /bouquet — Rebuild and maintain a git bouquet integration branch

You are helping the user rebuild their integration branch using `git bouquet`
and (optionally) `git town`. Follow the phases below in order. At each
decision point, share your reasoning and wait for the user to confirm before
taking irreversible actions (commits, branch updates, rr-cache edits).

---

## Phase 1 — Understand the current state

**Read the config.**

```sh
cat .bouquet.yaml
```

This tells you:
- `base` — the stable upstream branch all leaves descend from
- `branches` — a map of target integration branches to their leaf globs (order matters for rerere; do not reorder without telling the user)

**Identify the target branch.** If multiple targets exist, ask the user which one they want to work on.

**Check the existing target.**

```sh
git log --oneline <base>..<target>   # rebuild history
git show <target>                     # latest bouquet commit message lists leaves + SHAs
```

**Check the current state of the base and each leaf.**

```sh
git log --oneline -5 <base>
# for each leaf in the target's merge list:
git log --oneline <base>..<leaf>      # commits unique to this leaf
```

**If `git town` is installed**, use it to understand the branch graph. Filter
to only the branches in `.bouquet.yaml` plus `base` and `target`:

```sh
git town branch
```

Each leaf must be a descendant of `base` in git history (enforced by
`git bouquet start`). In a git town setup, every leaf's parent chain must
trace back to `base`. If it doesn't, raise this with the user before
proceeding — that leaf must be rerooted.

**Summarise what you found** — which leaves have new commits since the last
bouquet, which are unchanged, and what the user is about to rebuild.

---

## Phase 2 — Run the rebuild

```sh
git bouquet start [target]
```

Add `--pull` if the user wants to fast-forward `base` and any leaves that
have upstreams before rebuilding. Add `--sync` if they want `git town sync`
to run on each leaf first.

If the build **succeeds with no conflicts**, you are done. Tell the user what
was committed and to which SHA.

If there are **conflicts**, proceed to Phase 3.

---

## Phase 3 — Categorise and resolve each conflict

For each conflict, `git bouquet start` (or `continue`) stops with:

```
CONFLICT
Resolve conflicts in .git/bouquet/worktree, `git add` them,
then run `git bouquet continue`.
```

Inspect the conflict:

```sh
cd .git/bouquet/worktree
git status                 # which files are conflicted
git diff                   # see conflict markers (ours=accumulated, theirs=current leaf)
```

Present the conflict to the user and **ask them to categorise it**:

---

### Category 1 — Bug in an upstream leaf

The conflict exists because a leaf contains a bug (wrong logic, wrong base
assumption) that makes it incompatible with another leaf. The fix belongs
in the leaf itself, not in a merge resolution.

**Action:**
1. `git bouquet abort` to clean up.
2. Identify which leaf introduced the bug. Walk up the git town parent chain
   from the conflicting leaf toward `base`. Find the earliest ancestor that
   contains the problematic code.
3. Check out that branch and commit a fix.
4. If using git town, sync forward: `git town sync -s <fixed-branch>` to
   propagate the fix down to all descendants that depend on it.
5. Rerun `git bouquet start [target]` (the conflict should now be gone or the rerere
   cache will replay correctly).

---

### Category 2 — Wrong prior merge resolution

A previous bouquet run chose the wrong side or wrote incorrect resolution
content. The rerere cache replayed that bad resolution, hiding the fact that
it was wrong.

**If the bad resolution is obvious** (you can see immediately what it should
have been):

1. Find the rr-cache entry:
   ```sh
   # The conflict hash is printed by git rerere; or search by conflicted file:
   grep -rl "<conflicted-filename>" .git/rr-cache/
   ```
2. Edit `.git/rr-cache/<hash>/thisimage` to contain the correct resolution
   (without conflict markers). This is the file rerere replays.
3. Stage the corrected file in the worktree:
   ```sh
   cd .git/bouquet/worktree
   # write the correct content to the file, then:
   git add <file>
   ```
4. `git bouquet continue`.

**If the correct resolution is unclear**, remove the cache entry so the next
build stops at this conflict for human review:

```sh
rm -rf .git/rr-cache/<hash>
git bouquet abort
git bouquet start [target]   # will conflict again and stop for you to resolve manually
```

When you resolve it manually this time, rerere records the new (correct)
resolution and will replay it in all future rebuilds.

---

### Category 3 — Logical incompatibility between features

Two or more leaves implement things in ways that can't be reconciled by
simply picking OURS or THEIRS. The target branch needs new code that doesn't
exist in any single leaf — either to bridge different approaches, fix
regressions, or extend functionality so all features coexist correctly.

**Action — use a patch branch:**

The patch branch is a branch whose git town parent is `target`. Commits there
survive bouquet rebuilds because they are not part of the sequential merge;
instead they sit on top of the target after each rebuild via `git town sync`.

1. **Check if a patch branch already exists.** Ask the user. If one exists
   (e.g. `release/patch`), check it out and add the fix there. If not, ask
   the user what to name it.

2. **Ensure `target` is marked perennial in git town** so `git town sync`
   never tries to delete or rebase it:
   ```sh
   git config git-town.perennial-branches "<target>"
   # or add it to your .git-branches.toml perennials list
   ```
   A perennial branch is never rebased or deleted by git town; sync simply
   skips it and picks up at its children.

3. **Create or check out the patch branch:**
   ```sh
   git town append <patch-branch-name>   # parent = current branch (should be target)
   # or if target isn't checked out:
   git checkout <target>
   git town append <patch-branch-name>
   ```

4. Write the code that makes the two features coexist. Commit it on the
   patch branch.

5. Re-run the bouquet build — the merge conflicts that caused category 3
   still exist, but now you can resolve them in a way that points to the
   patch branch's approach. The rerere cache will capture this resolution
   for future rebuilds.

6. After a successful rebuild, `git town sync` (or `git town sync -s
   <patch-branch>`) will rebase the patch branch on top of the new target,
   keeping the patch always current.

---

## Phase 4 — After a successful build

Confirm the result with the user:

```sh
git show <target>                    # bouquet commit message
git log --oneline <base>..<target>   # rebuild history
git diff <base> <target>             # full diff of what's in the integration branch
```

If a patch branch exists, remind the user to sync it:

```sh
git town sync -s <patch-branch>
```

---

## Key principles to keep in mind

**Why sequential merges beat a true octopus merge:**
Git's octopus strategy aborts on the first conflict that needs manual
resolution, and produces a many-parent commit that can't be cleanly rebuilt.
The bouquet approach does sequential pairwise merges: each conflict is
isolated, independently resolved, and cached by `rerere`. The same conflict
will replay automatically on every future rebuild — this is what makes the
integration branch maintainable over time without constant re-intervention.

**Leaf order is part of the conflict key:**
`rerere` caches resolutions by the exact conflict markers it saw (the "ours"
and "theirs" content). If you reorder leaves, the same logical conflict
produces different markers, invalidating the cache. Never reorder leaves
casually. If you must reorder, warn the user that any affected rerere entries
will need to be re-resolved.

**All leaves must descend from base:**
`git bouquet start` enforces this. A leaf on a sibling branch of `base` (e.g.
on `main` when `base` is a release branch descended from `main`) will be
rejected. Use `git town sync -s <leaf>` to rebase it onto the correct parent.

**The patch branch is the escape hatch for true incompatibilities:**
Don't try to encode category-3 fixes as a rerere resolution — you'd be
hiding real logic in a cache file. Put it in the patch branch where it lives
as a real, reviewable, diffable commit.
