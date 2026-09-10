# unitybuild

Reusable Unity project discovery, Editor version checks, builds and logs for Windows, Linux and macOS,
with additional Windows-specific MSBuild/UWP tooling.
The package does not require a particular application or repository layout.

## Installation

Requires Python 3.12 or newer and a separately installed Unity Editor with the
matching platform modules. The package does not install Unity or its SDKs.

```sh
uv tool install unitybuild
unitybuild
```

For a Python dependency, use `uv add unitybuild`. To test an unpublished candidate,
install its wheel with `uv tool install ./dist/unitybuild-0.1.1-py3-none-any.whl`.

Windows player builds and Windows/Linux Python tests have been exercised.
Actual Unity builds on Linux and macOS remain unverified. See
[validation results](https://github.com/c0sogi/unitybuild/blob/main/VALIDATION.md)
for the tested Editors, targets and limitations.

## Configuration

The distribution, CLI and Python import are `unitybuild`. Project settings use
`unitybuild.toml`; saved projects use `~/.unitybuild/projects.json`. Select a project
with `--project` or `UNITYBUILD_PROJECT`, and override the registry directory with
`UNITYBUILD_HOME`. Build records are written to `Logs/unitybuild`.

Script builds use `UnityBuild.Builder.Build` and `UNITYBUILD_*` parameters in the
Unity child process. Saved Unity Build Profiles use Unity's native command line.

## Usage

```powershell
unitybuild project scan C:/Projects
unitybuild project add demo 'C:/Projects/My Unity App'
unitybuild --project demo build --platform StandaloneWindows64 --dry-run
unitybuild --project demo build --platform StandaloneWindows64
```

Use `Assets` and `ProjectSettings/ProjectVersion.txt` to identify a Unity project.
Optional configuration lives in `unitybuild.toml` inside the selected project.
On Unity 6.3+, saved Unity Build Profiles are discovered under `Assets`. The menu
shows their actual names and builds with `-activeBuildProfile ... -build ...`.
This path adds no C# helper and needs no `project init` or extra Unity package.
Player settings, scene lists, and project callbacks remain owned by Unity.
Build previews write nothing.

```powershell
unitybuild --project demo profiles
unitybuild --project demo build --profile 'Android Release' --dry-run --details
unitybuild --project demo build --profile 'Assets/Settings/Build Profiles/Android Release.asset'
```

`release` and `debug` are convenience aliases only when they select exactly one
saved non-development/development profile. Use the asset path for duplicate names.
The target and APK/AAB/export format come from the selected asset. The CLI owns
the output path, using the configured output for the matching platform and a
platform default otherwise; Development outputs keep the existing `Debug` subfolder.
The YAML reader supports text-serialized profiles for Android, Windows 64-bit,
Linux 64-bit, macOS, and UWP. Unity itself interprets the profile during execution.

Projects without saved profiles use the script build path. Its default
builder prepares a bundled C# helper on the first real build; existing helper
files are preserved. Explicit custom methods continue to work on this path.
There is no implicit build platform. Without a saved Unity profile or a configured
`build.platform`, the menu asks for the build target and the CLI requires
`build --platform PLATFORM`. Release/Debug controls the build configuration only.
Use `project init PATH --platform StandaloneWindows64` to save a platform or custom
build method. Output defaults follow the selected target (.exe, Linux executable,
.app, .apk, or UWP directory). Existing explicit settings remain authoritative.
Registrations
live in `~/.unitybuild/projects.json` (override with `UNITYBUILD_HOME`).

Open **Build settings** from the project task menu to review and edit TOML settings.
The main menu contains the build target, output path, and project name. Custom C#
methods and their environment variable names are under **Script builder (advanced)**.
**View TOML** shows the draft;
**Save changes** validates it and writes the configuration. Back/Ctrl+C discards
unsaved edits. A missing configuration is created only when saving, without
installing a Unity helper. Existing comments and other settings are preserved;
an external edit detected while the menu is open prevents overwriting it.
Saved Unity profiles continue to own their platform and scenes.

```powershell
unitybuild --project demo project settings
```

**Build scenes** edits Unity's standard `ProjectSettings/EditorBuildSettings.asset`:
add/remove scenes and move them into startup order, then save explicitly. Existing
disabled entries and unrelated Unity settings are preserved. Close this project's
Editor before saving this file. Saved Unity Build Profiles may override this list.
The bundled script builder checks enabled scenes before launching Unity; previews
show the selected scene list or the required setup. Custom methods retain control
of their own scenes.

**Build history** displays local dates, status, duration, target, output and failure
reason instead of raw JSON. Older escaped Unicode records are decoded for display.
New records use readable UTF-8 and include platform/version; available compiler
errors or build exceptions are shown ahead of the generic Unity exit message.

```powershell
unitybuild --project demo project scenes
unitybuild --project demo history
```

The build-target chooser and settings editor read the selected Editor's Unity Hub
`modules.json` catalog and its actual `PlaybackEngines` folders. Installed modules
are shown first; missing modules and targets not implemented by unitybuild
are labelled separately. New/unknown catalog entries remain visible. The adapter
mapping for commands/output formats is still explicit; discovery does not add
new platform build implementations. A detected module is not an SDK/toolchain or
project compilation check, and this is not a live `IsBuildTargetSupported` query.

Use **Refresh installed modules** to rescan. Each query rereads the local files;
the Editor version is verified with `-version`, without opening the project or
adding C# files. Without Hub metadata, only detected folders are listed. Without
the matching Editor, availability is unknown and a target identifier can be entered
explicitly for configuration. Missing known modules block real builds, including
configured targets and saved profiles; previews and configuration remain available.

```powershell
unitybuild --project demo project targets
```

## Python API

```python
from pathlib import Path
from dataclasses import replace
from unitybuild import ProjectCatalog, BuildProfile, build_project, resolve_build_profile

project = ProjectCatalog().load_project(Path("D:/Unity/MyGame"))
# For a project without saved profiles or build.platform, select a target explicitly.
project = replace(project, platform="StandaloneWindows64")
result = build_project(project, BuildProfile.RELEASE, dry_run=True)
print(result.output)
# Real execution returns output, log and record paths; errors raise exceptions.
result = build_project(project, BuildProfile.RELEASE)
# Select a real saved asset by name or project-relative asset path.
result = build_project(project, resolve_build_profile(project, "Android Development"))
```

`build_project` has no prompts and is quiet by default. Pass `emit=print` for plan
messages and `runner=unitybuild.progress.run_logged_process` for live console
output. A custom runner can integrate a job queue or another UI. Editor version
checks and project-lock checks still run before a real build. A zero exit code
alone is insufficient: the output must be nonempty and newly created or updated,
or Unity must explicitly report a successful build that reused existing output.
Desktop player checks include the adjacent scene/script data, not just the launcher.
Cancellation stops a background process tree and records the cancelled status.
An open Editor request only stops monitoring on cancellation; Unity owns its build.

## Extension points

- `ProjectCatalog`: independent config/registry/environment names, plus an optional
  `preset_resolver(path)` callback. Discovery always identifies actual Unity projects.
- `cli.register_commands`: reuse selection, initialization, build and history
  commands with your catalog and `build_handler` callback.
- `ArtifactStore`: source fingerprints, Release artifact validity, Development
  baselines, per-device state and build history. The application supplies its
  configuration fingerprint, target ID and Android package name.
- `editor.run_editor_request`: request/response transport for an existing Unity
  Editor bridge. Supply operation payload, stage descriptions and request/response
  paths. The project must provide a matching C# operation handler.
- `windows.WindowsBuildOptions`: explicit MSBuild/UWP inputs. `run_msbuild` and
  loose deployment helpers are Python APIs; the standalone CLI currently exports
  UWP from Unity but does not expose MSBuild/deployment commands.
- `execution.run_unity_batch`: low-level console runner for existing C# methods.
  `build_project` is the quieter, validated API for ordinary configured builds.

The bundled `Assets/Editor/UnityBuild/Builder.cs` uses enabled Unity build scenes
and `UNITYBUILD_OUTPUT`, `UNITYBUILD_PLATFORM`, `UNITYBUILD_PROFILE`.
Builds prepare a missing helper automatically after checking the project is not busy.
Optional initialization creates configuration and preserves an existing helper. Custom build methods
can be selected with `project init PATH --build-method Company.Editor.Build`.
Exact Unity Editor and platform modules must already be installed.

## Development and distributions

```powershell
uv sync
uv run pytest
uv run ruff check src tests
uv run pyright
uv build --no-sources
uv tool install ./dist/unitybuild-0.1.1-py3-none-any.whl
```

Application validators, MQTT configuration, scene generators and app-specific
build methods belong in the consuming package. Android connection discovery and
authorization remain the responsibility of Droidock. No application checkout,
sibling directory, or editable installation is required by the distributed wheel.

When launched from an MSIX-packaged Windows desktop app, Unity's Java child
processes can fail with `Unable to establish loopback connection` because ordinary
LocalAppData temporary paths are virtualized. Both native and script batch
execution detect that host and use its real `TempState` folder for child `TEMP`
and `TMP`. Custom paths outside the redirected tree and ordinary terminal launches
are preserved. No Java flags, Gradle warmup, global environment changes, or JDK
replacement are required.

Cancelling a Unity process can leave `Temp/UnityLockfile` behind. On Windows,
the next build probes exclusive file access; on Linux and macOS it probes both
`flock` and POSIX record locks without waiting. An unlocked leftover file does not
block a retry. The file is never forcibly deleted, and live project editors or
inaccessible locks still block a second build. POSIX cancellation stops the build's
own process group, escalating to SIGKILL if children ignore SIGTERM even when the
parent has already exited. Unrelated editor sessions are not targeted.

Real lock/cancellation tests passed on Windows, Linux and macOS GitHub Actions.
Actual Unity cancellation/retry was verified on Windows. Unity Editor builds on
Linux and macOS remain unverified.


Custom builders can declare `build.configuration_environment` to receive `release`
or `development` (the Debug profile), alongside the configured output variable.
This keeps application-specific variable names in the project configuration.
The method and variable names are available under **Build settings → Script builder
(advanced)**. These variables pass build values from Python to a C# method in the
Unity child process; they do not change the system environment. Native saved-profile
builds use `-activeBuildProfile` and `-build` and do not inject script-builder variables.

```toml
schema_version = 1
[build]
platform = "Android"
method = "MyGame.Editor.BuildAndroid"
output = "Builds/MyGame.apk"
output_environment = "MY_GAME_OUTPUT"
configuration_environment = "MY_GAME_CONFIGURATION"
```

Console output preserves its current encoding. During build monitoring,
unrepresentable characters are escaped on screen instead of aborting the build;
the full UTF-8 log and captured process output preserve the original text.

See the [release procedure](https://github.com/c0sogi/unitybuild/blob/main/RELEASING.md).

Version 0.1.1 corrects a macOS cancellation race in 0.1.0. A group with only
terminated processes can report a permission error; cancellation now verifies
remaining process state before treating the group as finished. Actual permission
failures for live or inaccessible processes remain errors.
