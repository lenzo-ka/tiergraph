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

2. **Verify locally.** Run these from the development virtualenv that
   [CONTRIBUTING.md](CONTRIBUTING.md#set-up-a-development-environment) builds —
   `make venv` creates `.venv` and installs the `build` frontend along with the
   rest of the development dependencies. Three of these steps are **read**, not
   merely run: they exit 0 whichever answer they found, so an exit status does
   not stand in for looking at the output.

   ```bash
   make check
   .venv/bin/python -m build
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
   - **The wheel listing must show both packages.** The grep exits 0 when
     either package is present, so the exit status decides nothing: read the
     output and confirm entries under `tiergraph/` *and* under `tiergraph_dot/`.
   - **The printed version must equal the `X.Y.Z` set in step 1**, and so must
     the tag cut in step 3 and the `dist/` filenames just built. Nothing
     compares them until the publish workflow does, and by then a mismatch has
     already cost the release.

   Then clear the build artifacts, once the wheel has been read:

   ```bash
   rm -rf dist build
   ```

3. **Commit + tag**:
   ```bash
   git commit -am "Release vX.Y.Z"
   git tag vX.Y.Z          # tag must equal tiergraph.__version__ with a leading v
   git push && git push --tags
   ```

4. **Publish** — create a **GitHub Release** for tag `vX.Y.Z` (Releases → Draft a
   new release). Publishing the release triggers `publish.yml`, which:
   - builds sdist + wheel (both `tiergraph` and `tiergraph_dot` packages),
   - **asserts the tag matches `tiergraph.__version__`** (fails the release
     otherwise),
   - uploads to PyPI via OIDC.

5. **Verify**: `pip install tiergraph==X.Y.Z` then
   `python -c "import tiergraph; print(tiergraph.__version__)"`.

---

## Notes / gotchas

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
  both `src/tiergraph` and `src/tiergraph_dot`. Step 2 lists the wheel while it
  still exists, before `rm -rf dist build` removes it. That listing is read, not
  scored: the pattern matches either package, so *both* is a fact about the
  output rather than about the exit status.
- **CI must be green first**: `ci.yml` runs on the push; only cut the release
  once it passes.
- **Re-releases**: PyPI is immutable — you cannot overwrite `X.Y.Z`. If a build
  is bad, bump to `X.Y.Z+1` (or a post-release `X.Y.Z.postN`).
- **Consumer dependencies**: published consumers should use a released version
  constraint such as `tiergraph>=X.Y`; a git-URL dependency makes the consumer
  unpublishable to PyPI.
