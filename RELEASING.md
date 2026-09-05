# Releasing tiergraph

*Maintainer release checklist. This file ships in the source distribution but
is not part of the user-facing documentation set.*

Publishing is automated via `.github/workflows/publish.yml` using **PyPI Trusted
Publishing (OIDC)** — no API tokens. You cut a GitHub Release; the workflow
builds and uploads. This doc is the operator checklist.

---

## One-time setup (completed)

These prerequisites were completed for the first release. Recheck them only if
the repository, workflow, or publishing environment changes.

### 1. GitHub repository

- Confirm the repo at `github.com/lenzo-ka/tiergraph` (the `[project.urls]` in
  `pyproject.toml` point there).
- Push `main`.

### 2. PyPI trusted publisher

`tiergraph` exists on PyPI and uses a normal trusted publisher configured with:

- PyPI project → **Publishing** → trusted publisher:
  - PyPI Project Name: `tiergraph`
  - Owner: `lenzo-ka`   Repository: `tiergraph`
  - Workflow name: `publish.yml`
  - Environment name: `pypi`

The pending-publisher setup used for the first release is complete; it became
this normal publisher after that upload.

### 3. GitHub Environment

Repo → **Settings → Environments** → create the `pypi` environment (the publish
job references it; the OIDC identity is scoped to it).

---

## Cutting a release

1. **Set the version** — single source of truth is `src/tiergraph/__init__.py`:
   ```python
   __version__ = "X.Y.Z"
   ```
   (`pyproject.toml` reads it via `[tool.hatch.version]`; do not edit a version
   there.)

2. **Head the changelog.** `CHANGELOG.md` ships inside the source distribution,
   and it is the artifact that carries this release's breaks to whoever installs
   it. Left as development leaves it, the published tarball's newest *released*
   section is the previous release and everything this one changed sits under
   `## [Unreleased]` — a reader of the shipped file concludes nothing changed.
   Three edits:

   - Re-head `## [Unreleased]` as `## [X.Y.Z] - YYYY-MM-DD`, dated the day the
     tag is cut.
   - Open a fresh, empty `## [Unreleased]` above it.
   - At the foot of the file, retarget the `[Unreleased]` link ref at the new
     tag and add a link ref for the release:

     ```
     [Unreleased]: https://github.com/lenzo-ka/tiergraph/compare/vX.Y.Z...HEAD
     [X.Y.Z]: https://github.com/lenzo-ka/tiergraph/compare/vW.V.U...vX.Y.Z
     ```

   The placement is load-bearing twice over. It must precede the **tag**,
   because the tag is what the sdist gets built from and PyPI is immutable — an
   unheaded changelog cannot be corrected in place afterwards, only in the next
   release. And it must precede **step 3**, so the gate runs over the changelog
   that ships rather than over a draft of it — the `changelog-claims` step
   reads entries under `[Unreleased]` *and* under the section named by
   `tiergraph.__version__`, so a section re-headed to `X.Y.Z` stays inside its
   reach and the re-heading is not a way around it. (One narrowing: the
   `FORMAT_VERSION` claim shape is checked under `[Unreleased]` only. It is
   checked on every pull request of the release line, where such an entry is
   written, rather than for the first time here.) The step also settles step 4:
   the version in step 1 is often already committed by the pull request that
   opened the release line, and this edit is what leaves something for that
   commit.

3. **Verify locally.** Run these from the development virtualenv that
   [CONTRIBUTING.md](CONTRIBUTING.md#set-up-a-development-environment) builds —
   `make venv` creates `.venv` and installs the `build` frontend along with the
   rest of the development dependencies. Three of these steps are **read**, not
   merely run: they exit 0 whichever answer they found, so an exit status does
   not stand in for looking at the output.

   ```bash
   make check
   .venv/bin/python -m build
   ls -1 dist
   .venv/bin/python -m zipfile -l dist/*.whl | grep -E 'tiergraph(_dot)?/'
   PYTHONPATH=src .venv/bin/python -c "import tiergraph; print(tiergraph.__version__)"
   ```

   - **`make check` must exit 0, and its `format-growth` step must be read.**
     The gate's step list lives in the makefile's `gate` target rather than
     here. Between a version step that opens a new release line and the tag
     that releases it, `format-growth` prints every wire-format break it found
     and still exits 0 — a green gate is compatible with a break, deliberately,
     so that the break is stated instead of refused. Read what it printed and
     confirm every line is a break you meant to take; printing nothing is the
     ordinary case. See
     [Grow the wire format, or say that you did not](CONTRIBUTING.md#grow-the-wire-format-or-say-that-you-did-not).
   - **`ls -1 dist` is the only step that shows the built filenames**, and it
     is here because the version comparison below needs them. `python -m
     zipfile -l dist/*.whl` prints the archive's *members*, never the archive's
     own name, and the glob means the shell resolves the name without ever
     displaying it — so without this line the doc asks for a comparison it
     gives no way to make. Expect exactly `tiergraph-X.Y.Z.tar.gz` and
     `tiergraph-X.Y.Z-py3-none-any.whl`; a stale build left in `dist/` from an
     earlier version shows up here as an extra pair, and it is what the publish
     workflow would upload.
   - **The wheel listing must show both packages.** The grep exits 0 when
     either package is present, so the exit status decides nothing: read the
     output and confirm entries under `tiergraph/` *and* under `tiergraph_dot/`.
   - **The printed version must equal the `X.Y.Z` set in step 1**, and so must
     the tag cut in step 4 and the `dist/` filenames `ls` just listed. Nothing
     compares them until the publish workflow does, and by then a mismatch has
     already cost the release.

   Then clear the build artifacts, once the wheel has been read:

   ```bash
   rm -rf dist build
   ```

4. **Commit + tag.** Look before committing, because there may be nothing to
   commit:

   ```bash
   git status --porcelain
   ```

   - **Something is listed** — the ordinary case, since step 2 always edits
     `CHANGELOG.md`:

     ```bash
     git commit -am "Release vX.Y.Z"
     ```

   - **Nothing is listed.** Do not run `git commit`; it exits 1 on a clean tree
     ("nothing to commit, working tree clean") and this is the worst place in
     the checklist to meet a non-zero exit and improvise. It happens when the
     release line was opened by a pull request that already carried both the
     version and the changelog heading, so steps 1 and 2 were no-ops. There is
     nothing wrong: the commit to tag is `HEAD`. Confirm it is the right one —

     ```bash
     git log -1 --oneline
     PYTHONPATH=src .venv/bin/python -c "import tiergraph; print(tiergraph.__version__)"
     ```

     — and go straight to the tag.

   Then, either way:

   ```bash
   git tag vX.Y.Z          # tag must equal tiergraph.__version__ with a leading v
   git push && git push --tags
   ```

5. **Publish** — create a **GitHub Release** for tag `vX.Y.Z` (Releases → Draft a
   new release). Publishing the release triggers `publish.yml`, which:
   - builds sdist + wheel (both `tiergraph` and `tiergraph_dot` packages),
   - **asserts the tag matches `tiergraph.__version__`** (fails the release
     otherwise),
   - uploads to PyPI via OIDC.

6. **Verify**: `pip install tiergraph==X.Y.Z` then
   `python -c "import tiergraph; print(tiergraph.__version__)"`.

Those six are the whole sequence. In particular there is no `make corpus-capture`
step, although the makefile target's own comment says to run one at a release:
see [`make corpus-capture` is not a release step](#make-corpus-capture-is-not-a-release-step)
below for why not, and for what would change the answer.

---

## Notes / gotchas

### `make corpus-capture` is not a release step

The makefile's `corpus-capture` comment says "Run this at a release", and
nothing in this checklist runs it. That is deliberate, and the reasoning is
here so a releaser who goes looking does not have to reconstruct it.

`corpus/accepted-documents.jsonl` records documents this project's decoder
accepted, and `make gate`'s `format-semantics` step re-runs the current decoder
over every one of them, so a tightening that changes no schema byte still has to
answer for itself. Most rows carry only a capture stamp, but some carry a
**hand-made adjudication** — `legal`, or `never-legal` with a written reason —
and the `never-legal` ones are unreproducible by construction: the decoder was
corrected against exactly those documents, so no later capture can witness one
again. `scripts/capture_corpus.py` therefore **merges** rather than rewrites. It
carries every existing row through byte for byte, appends only documents the
corpus does not already hold, and refuses the whole write — non-zero, naming
each row — if the capture cannot reproduce it.
`make corpus-capture CORPUS_CAPTURE_FLAGS=--retain-unreproduced` explicitly
keeps those rows; the flag's default is false.
That makes running it *safe*. It does not make running it *useful at a release*:

- **A capture at the release commit cannot evidence that it was one.** The stamp
  it writes is `tiergraph.__version__`, which reads `X.Y.Z` at every commit of
  the release line, not just the tagged one. A capture cut minutes before the
  tag and a capture cut weeks earlier produce identical rows. The provenance
  that would justify the step is the thing the mechanism does not record.
- **Nothing expires.** The merge only ever adds, and the gate only ever fails
  over a row the corpus *already holds*. Whatever a capture would contribute
  today it contributes just as well at the next release, with nothing lost in
  between.
- **It costs the release its verification.** The corpus is a tracked file. A
  capture that adds rows changes the tree after step 3 has passed, so step 3 has
  to be run again before the tag — and the rows added arrive unadjudicated, on
  the day of a release, for nobody to have looked at.
- **It does not currently run clean.** The plugin patches `tiergraph.loads` for
  the duration of the run, and the documentation gate reads that patched
  function, so `make corpus-capture` fails `tests/test_docs.py` and exits
  non-zero while still writing its output. That is a defect in the capture, not
  in the release, but it is not something to meet for the first time an hour
  before a tag.

**So: do not run it as part of cutting a release.** Run it deliberately, on its
own branch, as its own reviewed change — the added rows read as an insert-only
diff, which is what makes them reviewable. What would change this answer is a
stamp that records the commit rather than only the version; then a capture at
the tag would assert something a later reader could check, and it would be worth
a step of its own before step 3.

### Adding a denied-name digest

Read the name without displaying it or recording it in shell history, then use
the tracked salt and the gate's normalization to calculate its digest:

```python
from getpass import getpass
from scripts.check_tracked_clean import DENIED_DIGESTS_PATH, denied_digests, digest

denylist = denied_digests(DENIED_DIGESTS_PATH)
print(digest(getpass("Name: "), denylist.salt))
```

Paste only the resulting digest into `denied-name-digests.txt`, preserving
ascending sort order.

- **Tag ↔ version**: the release step compares `${TAG#v}` against
  `tiergraph.__version__`. A mismatch fails the build — bump the version *and*
  tag together.
- **Two packages, one distribution**: `[tool.hatch.build.targets.wheel]` ships
  both `src/tiergraph` and `src/tiergraph_dot`. Step 3 lists the wheel while it
  still exists, before `rm -rf dist build` removes it. That listing is read, not
  scored: the pattern matches either package, so *both* is a fact about the
  output rather than about the exit status.
- **CI must be green first**: `ci.yml` runs on the push; only cut the release
  once it passes.
- **Re-releases**: PyPI is immutable — you cannot overwrite `X.Y.Z`. If a build
  is bad, bump to `X.Y.Z+1` (or a post-release `X.Y.Z.postN`).
- **Consumer dependencies**: published consumers should use a released version
  constraint, and before 1.0 it has to be **capped** — `tiergraph>=X.Y,<X.(Y+1)`,
  so `tiergraph>=0.2,<0.3` against a 0.2 release. A git-URL dependency makes the
  consumer unpublishable to PyPI.

  The cap is not a matter of taste here. The
  [stability policy](README.md#stability) says a pre-1.0 `0.X.0` is in effect a
  major release and carries no compatibility guarantee for the public Python
  API: names may be removed or renamed in one, with no migration path. An
  uncapped `tiergraph>=0.2` therefore admits exactly the releases that are
  documented as allowed to break it, and it does so at the consumer's next
  resolve — no code changed, nothing failed at the time the change was made, and
  the break lands on whoever installs next. The cap is what turns that into a
  deliberate bump: raise it, take the release, and read what the changelog's new
  section says the release removed.

  This applies to a consumer's **runtime** dependency, which is the one that
  binds at install time. A development pin or an editable checkout is the
  maintainer's own business and can track whatever it likes.
