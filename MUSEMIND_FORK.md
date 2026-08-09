# MuseMind RAGFlow Fork

This repository is a fork of [infiniflow/ragflow](https://github.com/infiniflow/ragflow) maintained for MuseMind.

The goal of this fork is **not** to create an independent RAGFlow distribution. MuseMind should stay as close as possible to official RAGFlow releases and carry only a small, explicit set of changes required by our use case.

The current baseline and every MuseMind runtime patch are recorded in
[`MUSEMIND_PATCHES.md`](MUSEMIND_PATCHES.md). Bundle qualification must use an exact fork commit and
must update that ledger with the image/SDK/service digests and the evidence actually exercised.

## Branch model

### Default branch

The repository default branch is intentionally set to `musemind`.

This means that GitHub opens the repository on the MuseMind-supported RAGFlow state and new pull requests will normally target `musemind` by default. It also makes `musemind` the initially checked-out branch for new clones.

`main` is deliberately **not** the default branch: it exists as a clean mirror of upstream development and must not receive MuseMind-specific changes.

### `main`

`main` mirrors `infiniflow/ragflow:main`.

It represents the current upstream development state and should not contain MuseMind-specific commits.

Rules:

- Do not develop MuseMind features directly on `main`.
- Do not merge MuseMind pull requests into `main`.
- Keep it synchronized with `upstream/main` using fast-forward updates whenever possible.
- Do not treat `main` as the version deployed by MuseMind.

### `musemind`

`musemind` is the long-lived branch used as the base for the MuseMind RAGFlow build.

It is based on a **specific stable RAGFlow release tag**, not on the latest commit of `main`.

Conceptually:

```text
RAGFlow stable tag + MuseMind patches = musemind
```

Example:

```text
upstream/main
A---B---C---D---E---F---G
        ^
      vX.Y.Z
        \
         M1---M2---M3   musemind
```

Where `M1`, `M2`, and `M3` are small MuseMind-specific patches.

The exact upstream release currently used by MuseMind should always be recorded in the repository, release notes, or deployment metadata.

## Feature branches

All MuseMind changes should be developed in short-lived branches created from `musemind`.

Recommended naming:

```text
mm/<short-feature-name>
```

Examples:

```text
mm/explicit-document-retrieval
mm/retrieval-filtering
mm/document-selection
```

Typical workflow:

```bash
git checkout musemind
git pull origin musemind
git checkout -b mm/explicit-document-retrieval
```

After implementation and testing, open a pull request:

```text
mm/explicit-document-retrieval -> musemind
```

Do not target `main` with MuseMind-specific changes.

## Remotes

The local repository should normally have two remotes:

```text
origin    -> MuseMinds/ragflow
upstream  -> infiniflow/ragflow
```

Example setup:

```bash
git remote add upstream https://github.com/infiniflow/ragflow.git
git fetch upstream --tags
```

## Synchronizing `main`

To update the fork's upstream mirror:

```bash
git fetch upstream

git checkout main
git merge --ff-only upstream/main
git push origin main
```

If the fast-forward fails, investigate before merging: `main` may contain commits that do not exist upstream.

## Creating `musemind`

Create `musemind` from the stable RAGFlow tag selected for MuseMind.

Example:

```bash
git fetch upstream --tags

git checkout <ragflow-stable-tag>
git checkout -b musemind
git push -u origin musemind
```

Do not create it automatically from the latest `main` commit unless MuseMind intentionally wants to run an unreleased upstream version.

## Upgrading RAGFlow

MuseMind upgrades should normally move from one stable upstream release to another.

Example:

```text
RAGFlow vX.Y.Z + MuseMind patches
                ↓
RAGFlow vX.Y.(Z+1) + MuseMind patches
```

Recommended workflow:

1. Fetch the new upstream release and tags.
2. Create an upgrade branch from `musemind`.
3. Rebase or replay the MuseMind delta onto the new stable release.
4. Resolve conflicts explicitly.
5. Run RAGFlow and MuseMind integration tests.
6. Open a pull request into `musemind`.
7. Record the new upstream base version.

Suggested branch name:

```text
upgrade/ragflow-<version>
```

Example:

```text
upgrade/ragflow-v0.27.0
```

An upstream upgrade should be treated as an explicit dependency upgrade, not as routine synchronization with `main`.

## Patch philosophy

The maintenance cost of this fork is approximately the size and complexity of:

```bash
git diff <upstream-base-tag>...musemind
```

Therefore MuseMind-specific changes should be:

- small;
- isolated;
- well tested;
- documented;
- implemented in as few upstream files as reasonably possible;
- free from unrelated refactoring.

Whenever possible, MuseMind business logic should remain in the MuseMind codebase rather than being moved into RAGFlow.

The RAGFlow fork should contain only changes that genuinely need to happen inside RAGFlow.

## Potential upstream contributions

Some MuseMind patches may later be useful to RAGFlow itself.

The first MuseMind implementation does **not** need to be immediately generalized into an upstream-ready contribution.

The preferred sequence is:

```text
MuseMind requirement
      ↓
small MuseMind patch
      ↓
validated in our use case
      ↓
optional generalized implementation
      ↓
PR to infiniflow/ragflow
```

If a patch is proposed upstream, prepare a dedicated branch from the appropriate upstream base and adapt the implementation, tests, API design, documentation, and naming to RAGFlow's contribution standards.

Do not assume that the MuseMind-specific commit must be identical to the eventual upstream contribution.

## Core rules

1. `musemind` is the repository default branch.
2. `main` tracks upstream development and remains free of MuseMind-specific commits.
3. `musemind` tracks the RAGFlow release actually adopted by MuseMind plus our patches.
4. MuseMind feature branches start from `musemind`.
5. MuseMind feature PRs target `musemind`.
6. Upstream upgrades are deliberate stable-release upgrades.
7. Keep the delta from upstream as small as possible.
8. Prefer configuration or MuseMind-side logic over modifying RAGFlow when both are viable.
9. A patch that could eventually go upstream may still begin as a MuseMind-specific implementation.

## Mental model

```text
infiniflow/ragflow
       |
       +---- main ------------------------------> ongoing upstream development
       |
       +---- stable tag vX.Y.Z
                    |
                    +---- MuseMinds/ragflow:musemind
                              |
                              +---- mm/feature-a
                              +---- mm/feature-b
                              +---- upgrade/ragflow-vNext
```

The objective is always:

> **Official stable RAGFlow + the smallest possible MuseMind delta.**
