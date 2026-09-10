# Releasing unitybuild

Version **0.1.0** is published under the MIT license:

- [Public repository](https://github.com/c0sogi/unitybuild)
- [PyPI package](https://pypi.org/project/unitybuild/0.1.0/)
- [GitHub release](https://github.com/c0sogi/unitybuild/releases/tag/v0.1.0)
- Version 0.1.0 has a known intermittent macOS cancellation failure; use the corrected 0.1.1 release.

The commands below describe the release procedure. For subsequent releases,
change the version first and use a new output directory. Never rebuild changed
runtime code under an already-published version.

## Validate and package

Run from this checkout:

```sh
uv sync --locked
uv run pytest -q
uv run ruff check src tests
uv run pyright
uv build --no-sources --out-dir dist/0.1.1
uvx --from twine twine check --strict dist/0.1.1/*
uv publish --dry-run dist/0.1.1/unitybuild-0.1.1-py3-none-any.whl dist/0.1.1/unitybuild-0.1.1.tar.gz
```

Use exactly these two distribution files when publishing. The top-level `dist`
directory may contain older local versions, which must not be uploaded together.
The wheel contains the C# template and license. It has no application dependency
or editable source reference. Install into a fresh environment outside the checkout
and verify the CLI, configuration, library import and actual player build.
See [VALIDATION.md](VALIDATION.md) for completed checks and their limits.

## Publication boundary

The following steps publish externally. They were completed for 0.1.0. Recheck package-name availability and review the staged source,
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

Commands used for the first publication (the repository already exists):

```sh
gh repo create c0sogi/unitybuild --public --source . --remote origin --push
uv publish dist/0.1.1/unitybuild-0.1.1-py3-none-any.whl dist/0.1.1/unitybuild-0.1.1.tar.gz
```

The GitHub repository name and PyPI project name are separate. A missing PyPI
project page does not guarantee that the name can be registered. Authentication,
name acceptance and remote CI execution were verified for the first release.
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
The consuming workspace completed this transition for 0.1.0 and passed its
tests and saved-profile preview using the PyPI installation.

Build and publication guidance follows the
[uv package guide](https://docs.astral.sh/uv/guides/package/).

Use synthetic test project names and paths. Do not copy real people, student
identifiers, device identifiers or personal workspace paths into fixtures or
release material. Review tracked source, Git history and CI output before publication.
