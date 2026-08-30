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

2. **Verify locally** (all must be clean):
   ```bash
   make check          # lint, strict types, suite, three hash seeds, docs currency
   python -m build
   PYTHONPATH=src python -c "import tiergraph; print(tiergraph.__version__)"
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

- **Tag ↔ version**: the release step compares `${TAG#v}` against
  `tiergraph.__version__`. A mismatch fails the build — bump the version *and*
  tag together.
- **Two packages, one distribution**: `[tool.hatch.build.targets.wheel]` ships
  both `src/tiergraph` and `src/tiergraph_dot`. Confirm both are in the wheel:
  `python -m zipfile -l dist/*.whl | grep -E 'tiergraph(_dot)?/'`.
- **CI must be green first**: `ci.yml` runs on the push; only cut the release
  once it passes.
- **Re-releases**: PyPI is immutable — you cannot overwrite `X.Y.Z`. If a build
  is bad, bump to `X.Y.Z+1` (or a post-release `X.Y.Z.postN`).
- **Consumer dependencies**: published consumers should use a released version
  constraint such as `tiergraph>=X.Y`; a git-URL dependency makes the consumer
  unpublishable to PyPI.
