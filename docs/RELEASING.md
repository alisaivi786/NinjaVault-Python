# Releasing ninjavault-cdn

PyPI never accepts the same version twice, so every release needs a new version number.

All NinjaVault SDKs share one three-part version number: `ninjavault-cdn`, `@ninjavault/cdn` (npm) and
`NinjaVault.Cdn` (NuGet) are all `100.42.1`. Use three parts (npm allows no more), and release every SDK with the
same new number when the shared feature set changes.

## Every release

1. Bump `__version__` in `src/ninjavault_cdn/_version.py` (the only place the version lives; hatchling reads it).
2. `make changeset TYPE=Patch` (or `Minor` / `Major`) and fill in the generated `changesets/<version>.md`.
3. `make check` locally: lint, type check, tests, change-set check and build must all pass.
4. Open a pull request against `main`. The **CI** workflow runs the same checks on Python 3.10 to 3.14 (and Windows).
5. Merge. The **Publish** workflow sees that `<version>` is not on PyPI yet, runs the tests again, builds, uploads to
   PyPI with Trusted Publishing (no token), and creates the GitHub release `v<version>` with the change-set as notes
   and the sdist/wheel attached.

A push to `main` whose version is already on PyPI publishes nothing, so docs-only merges are safe.

## One-time setup

### PyPI trusted publisher

The project does not exist on PyPI before the first upload, so register a **pending publisher**:

1. Sign in at <https://pypi.org>, open **Your account -> Publishing**
   (<https://pypi.org/manage/account/publishing/>).
2. Under **Add a new pending publisher**, choose **GitHub** and enter:

   | Field | Value |
   |---|---|
   | PyPI Project Name | `ninjavault-cdn` |
   | Owner | `alisaivi786` |
   | Repository name | `NinjaVault-Python` |
   | Workflow name | `publish.yml` |
   | Environment name | `pypi` |

3. Click **Add**.

A pending publisher does not reserve the name until the first successful upload; publish soon after creating it.
After the first upload it becomes a normal trusted publisher of the project (manage it under the project's
**Settings -> Publishing**).

### GitHub

- **Settings -> Environments -> New environment**: `pypi`. Optionally add required reviewers so each release waits
  for approval, and restrict it to the `main` branch.
- No secrets are needed: the workflow requests a short-lived OIDC token (`id-token: write`).
- **Settings -> Actions -> General -> Workflow permissions** can stay read-only; `publish.yml` asks for
  `contents: write` itself to create the tag and release.

## Manual fallback

```bash
make build
python -m twine upload dist/*     # needs a PyPI API token; prefer the workflow
```
