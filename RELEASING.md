# Releasing unitybuild

Candidate: **0.1.0**. Nothing has been pushed or uploaded.
The intended public repository is `c0sogi/unitybuild`. The local repository and
package metadata are prepared for that destination; the remote is not created yet.
MIT is the proposed license for review before publication.

## Validate and package

Run from this checkout:

```sh
uv sync --locked
uv run pytest -q
uv run ruff check src tests
uv run pyright
uv build --no-sources --out-dir dist/0.1.0
uvx --from twine twine check --strict dist/0.1.0/*
uv publish --dry-run dist/0.1.0/unitybuild-0.1.0-py3-none-any.whl dist/0.1.0/unitybuild-0.1.0.tar.gz
```

Use exactly these two distribution files when publishing. The top-level `dist`
directory may contain older local versions, which must not be uploaded together.
The wheel contains the C# template and license. It has no application dependency
or editable source reference. Install into a fresh environment outside the checkout
and verify the CLI, configuration, library import and actual player build.
See [VALIDATION.md](VALIDATION.md) for completed checks and their limits.

## Publication boundary

The following steps publish externally and are deliberately not executed during
preparation. Recheck package-name availability and review the staged source,
README, license and distribution hashes before proceeding.

1. Commit the reviewed files on `main`, create the public GitHub repository, then
   push and wait for the three-OS test workflow to finish.
2. Publish the two reviewed distributions to PyPI using an authenticated local
   `uv publish` invocation. Configure the PyPI API token privately through
   `UV_PUBLISH_TOKEN` or a supported credential provider; do not store it here.
3. Verify the PyPI version and hashes, then install `unitybuild==0.1.0` in a fresh
   environment using PyPI alone. Repeat the CLI and consumer integration checks.
4. Tag the published source as `v0.1.0` and create its GitHub release with the
   reviewed distributions attached.

Prepared commands for the publication steps (not a script to run during checks):

```sh
gh repo create c0sogi/unitybuild --public --source . --remote origin --push
uv publish dist/0.1.0/unitybuild-0.1.0-py3-none-any.whl dist/0.1.0/unitybuild-0.1.0.tar.gz
```

The GitHub repository name and PyPI project name are separate. A missing PyPI
project page does not guarantee that the name can be registered. Authentication,
name acceptance and remote CI execution can only be confirmed at publication.
The local workflow runs checks on pushes and pull requests; it does not upload.

## Consumer transition

Consumers should declare `unitybuild>=0.1.0,<0.2` as a normal dependency.
Before publication, a fresh environment can resolve that requirement using
`uv pip install --find-links PATH_TO_CANDIDATE_DIRECTORY CONSUMER_WHEEL`.
This verifies distribution integration without relying on the source checkout.

After PyPI installation is verified, remove the consumer workspace's
`unitybuild = { path = "../unitybuild", editable = true }` source override and run
`uv lock --upgrade-package unitybuild` followed by `uv sync --locked`.
Confirm that the lock entry uses the package registry and contains no editable
source for unitybuild, then rerun the consumer tests and a saved-profile preview.
Until the distribution is available on PyPI, retain the existing source override
in the active workspace so ordinary synchronization remains usable.

Build and publication guidance follows the
[uv package guide](https://docs.astral.sh/uv/guides/package/).
