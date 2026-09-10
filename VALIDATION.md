# unitybuild validation

Last updated: 2026-09-10. Package version: 0.1.0 (unpublished first release).
This document records package-level checks and their limits. Application-specific
migration history, deployment logs, device identifiers, and machine-local evidence
paths are outside its scope.

The first public version is 0.1.0. Earlier 0.1.11 through 0.1.14 labels below
identify unpublished development snapshots, not previous public releases. Runtime
files in the 0.1.0 wheel match the validated 0.1.14 candidate byte for byte; only
version metadata and documentation changed.

## Latest source checks

| Check | Result |
| --- | --- |
| Windows Python tests | 117 passed, 2 subtests passed; 2 POSIX-only tests skipped |
| Linux Python tests | 121 passed, 2 subtests passed |
| Ruff, including import sorting | Passed on Windows |
| Pyright | Passed on Windows |
| macOS execution | Not performed |

Linux tests ran under WSL2 with musl and CPython 3.12.13. Filesystem lock tests used
native Linux storage rather than a Windows-mounted directory. Test results do not
establish that Unity player builds work on every host or target platform.

The release fixes build-monitor output on strict CP949 and ASCII streams.
Real subprocess tests cover plain and rich output with CP949, ASCII and UTF-8.
Unrepresentable terminal characters are escaped; captured output and UTF-8 log
files preserve the original text. The caller's stream encoding is unchanged and
its error policy is restored when monitoring ends, including exceptional exits.

Version 0.1.13 removes compatibility with the former package name. Tests verify
that obsolete configuration, environment variables, registry and log directories
are ignored. Only the `unitybuild` console command is installed. Configuration uses
`unitybuild.toml`, the default registry uses `~/.unitybuild`, and build records use
`Logs/unitybuild`. The bundled helper uses `UnityBuild.Builder.Build` and
`UNITYBUILD_*` parameters. Existing obsolete files are not deleted or migrated.

The 0.1.11 refactor consolidates platform metadata and separates build support from
default output paths. Tests register a synthetic implementation once and verify
that configuration, CLI, saved-profile decoding and module discovery all consume
it. A known unsupported target remains rejected even when given a default output.
Release/Debug now has one shared enum. Version/installation lookup and quiet/live
process execution were consolidated; obsolete forwarding modules were removed
from the consuming adapter. No new platform build support is claimed.

Version 0.1.11 distributions passed strict metadata checks. Inspection confirmed
that removed modules are absent from the wheels. A fresh Windows environment
installed the standalone wheel and passed import, CLI, helper and configuration
checks outside the checkout. Installing the consumer adapter afterward passed
dependency checks and a native-profile build preview.

The module consolidation reduces the library from 26 to 19 Python source files.
Menu controls, rendering and terminal interaction share one module; standard and
saved profiles share another. Builder provisioning moved into builds, environment
preparation into execution, and command registration into the CLI module. The
consumer adapter also consolidated four modules. Installed-wheel checks passed for standalone and consumer CLI paths,
including configuration and native-profile previews. Both distributions passed
strict metadata checks. This restructuring did not execute a Unity player build.

## Actual Unity builds

| Editor | Host | Build path | Observed result |
| --- | --- | --- | --- |
| 6000.3.14f1 | Windows | 0.1.14 installed wheel, bundled C# builder, Windows player | Independent project built successfully with strict CP949 console output and no application adapter installed |
| 6000.3.14f1 | Windows | 0.1.13 installed wheel, bundled C# builder, Windows player | Fresh independent project built successfully through `UnityBuild.Builder.Build`; only the current helper path was created |
| 6000.3.14f1 | Windows | Saved profile, Windows player | Built from an independent project with no C# files; no helper added |
| 6000.3.14f1 | Windows | Saved profiles, Android Release and Development | Both APKs built; manifest debugging flags matched the configurations |
| 2022.3.17f1 | Windows | Bundled C# builder, Windows player | Initial build and unchanged rebuild succeeded |

The native path uses `-activeBuildProfile` and `-build`; it does not invoke the
configured C# build method. Native profile execution currently requires Unity 6.3
or newer. Application-owned build callbacks remain under Unity's control.

The Unity 2022 project initially had no enabled scenes and contained an unused
editor-only namespace import in a runtime script. Enabling its scene and removing
that import allowed player compilation. The library does not repair arbitrary
project source errors automatically. Desktop player interaction was not tested.

Unity reporting a build failure with exit code zero was observed and rejected.
Regression tests also reject failures that leave a partial output. An unchanged,
nonempty output is accepted only with an explicit Unity success report. Desktop
output checks include the adjacent player data directory.

## Target discovery

The build and settings menus read the selected Editor installation's `modules.json`
and `PlaybackEngines` folders. Queries verify the Editor version without opening
a project or adding a discovery script. This reads installation metadata; it does
not call Unity's `BuildPipeline.IsBuildTargetSupported` API.

Installed-wheel queries on Windows returned 11 catalog targets for 2022.3.17f1
and 12 for 6000.3.14f1. Catalog entries include unavailable or unsupported targets;
these counts are not counts of working build targets. Platform translations and
the supported build implementations remain explicit in the package.

Tests cover changed, missing and corrupt metadata, unknown modules, missing Editors,
backend/server distinctions, menu refresh, and Windows/Linux/macOS installation
layouts. Detecting a module does not verify its SDK, selected backend or compilation.
Unconfigured projects do not default to Android: a saved profile, configuration,
or explicit user selection must supply the target.

## Settings, scenes and history

The basic settings menu exposes target, output and project name. C# method and
environment variable names are under **Script builder (advanced)**. Native saved
profiles do not use those settings or receive injected script-builder variables.
Script builds pass output, profile and platform through child-process variables.

Settings edits stay in memory until **Save changes**. Tests cover preview, discard,
advanced-menu edits, validation, comments, BOM/CRLF, no-op saves, configuration
fallbacks, external-edit detection and output paths that overlap project sources.
Saving settings does not start Unity or install the helper.

The bundled builder checks for enabled scenes before starting Unity. The scene
editor updates the standard scene-list block with explicit save and ordering,
while preserving unrelated settings. Saved profiles and custom methods retain
authority over their own scene selection.

History displays decoded project names, dates, status, duration, target and paths.
New records use readable UTF-8 JSON; old escaped Unicode is decoded on display.
Failure summaries extract available compiler errors or build exceptions.

## Cancellation and process environment

Windows probes exclusive access to the project lock file. Linux and macOS probe
both nonblocking `flock` and POSIX record locks. A leftover unlocked file does not
block retry and is not forcibly removed. Live editors and inaccessible locks still
block concurrent background builds.

Real Windows and Linux subprocess tests cover held locks, Unicode paths, repeated
cancellation and retry. Linux tests also cover children that ignore SIGTERM after
their parent exits, while preserving unrelated processes. Actual Unity cancellation
and retry were verified with Unity 2022.3.17f1 on Windows only.

For MSIX-hosted Windows launches, child `TEMP` and `TMP` are corrected when inherited
paths point into redirected LocalAppData. An independent Java probe and native
Android builds verified this correction without Java option overrides. Ordinary
terminal launches, custom non-redirected paths and the parent environment are
preserved. This host-environment correction is separate from C# build parameters.

## Distribution checks and remaining limits

Version 0.1.0 wheel and source distributions passed strict Twine metadata checks.
A fresh Windows environment installed the wheel outside the checkout and built an
independent Windows player before any consumer adapter was installed. The current
CLI executable was present and the obsolete executable was absent. The build log
confirmed the new C# entry point, and the UTF-8 history record reported success
under the current log directory. Installing the updated consumer adapter afterward
passed dependency checks and a native saved-profile build preview. These checks
are not publication confirmation.

A second fresh Windows environment resolved the consumer's ordinary versioned
dependency through `--find-links` to the candidate wheel directory. It passed
dependency checks and a native saved-profile preview without either source checkout
installed. A fresh Linux environment also installed the wheel, passed dependency
checks and ran the CLI. Artifact inspection verified metadata, the MIT license,
the bundled C# template and absence of application-specific or obsolete package names
in runtime files. The `uv publish --dry-run` check passed for exactly the two current
distribution files; this does not authenticate or upload to PyPI.

- No Unity Editor build was run on Linux or macOS. macOS behavior has layout fixtures
  but no actual execution verification.
- A three-OS CI workflow exists locally; no GitHub CI run was verified.
- Passing these checks does not certify every Unity release from 2022 onward,
  every platform, project, SDK or scripting backend.
- The previous CP949 build-monitor failure is fixed in the release. A strict CP949
  installed-wheel Unity player build succeeded; regression tests additionally
  exercised replacement characters and emoji with original UTF-8 log preservation.

## Repeatable package checks

From the package checkout:

```sh
uv sync --locked
uv run pytest -q
uv run ruff check src tests
uv run pyright
uv build --no-sources
```

Actual player validation additionally requires the matching Unity Editor, target
modules and a valid project. Installing a wheel or passing Python tests alone does
not establish player-build or device-runtime success.
