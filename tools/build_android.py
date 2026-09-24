#!/usr/bin/env python3
"""Import local Darkest Dungeon Unity assets and build a private Android APK."""

from __future__ import annotations

import argparse
import os
import shutil
import stat
import subprocess
import sys
import zipfile
from pathlib import Path, PurePosixPath


UPSTREAM_URL = "https://github.com/Reinisch/Darkest-Dungeon-Unity.git"
EXPECTED_UNITY_VERSION = "2017.3.0f3"
REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PROJECT = REPOSITORY_ROOT / "local-projects" / "Darkest-Dungeon-Unity"
APK_PATH = REPOSITORY_ROOT / "artifacts" / "Darkest-Dungeon-Unity.apk"
LOG_PATH = REPOSITORY_ROOT / "artifacts" / "unity-android-build.log"
REQUIRED_CAMPAIGN_SCENES = (
    "Assets/Scenes/CampaignSelection.unity",
    "Assets/Scenes/EstateManagement.unity",
    "Assets/Scenes/Dungeon.unity",
)

# These are the game-asset directories explicitly excluded by the upstream
# project's .gitignore. Everything else in the archive is left untouched.
ASSET_ROOTS = (
    "Audio",
    "Sprites",
    "StreamingAssets",
    "Textures",
    "Video",
    "Colours",
    "Resources/Screen",
    "Resources/Sprites",
    "Resources/Dungeons",
    "Resources/Data/Heroes/Sprites",
)

EDITOR_SCRIPT = r'''// Generated locally by the Darkest Dungeon Android build wrapper.
using System;
using System.IO;
using System.Linq;
using UnityEditor;
using UnityEngine;

public static class DarkestDungeonLocalAndroidBuild
{
    public static void BuildAndroid()
    {
        var scenes = EditorBuildSettings.scenes
            .Where(scene => scene.enabled)
            .Select(scene => scene.path)
            .ToArray();

        if (scenes.Length == 0)
            throw new Exception("No enabled scenes are listed in Build Settings.");

        var apkPath = Environment.GetEnvironmentVariable("DD_ANDROID_APK_PATH");
        if (String.IsNullOrEmpty(apkPath))
            throw new Exception("DD_ANDROID_APK_PATH is not set by the build wrapper.");

        Directory.CreateDirectory(Path.GetDirectoryName(apkPath));
        var error = BuildPipeline.BuildPlayer(
            scenes, apkPath, BuildTarget.Android, BuildOptions.None);

        if (!String.IsNullOrEmpty(error))
            throw new Exception(error);

        Debug.Log("Android APK created at: " + apkPath);
    }
}
'''


class BuildError(RuntimeError):
    pass


def root_parts(root: str) -> tuple[str, ...]:
    return tuple(part.casefold() for part in PurePosixPath(root).parts)


def safe_parts(raw: str) -> tuple[str, ...]:
    normalized = raw.replace("\\", "/")
    path = PurePosixPath(normalized)
    parts = tuple(part for part in path.parts if part not in ("", ".", "/"))
    if path.is_absolute() or any(part == ".." for part in parts):
        raise BuildError(f"Unsafe path in asset archive: {raw}")
    return parts


def map_asset_path(parts: tuple[str, ...]) -> tuple[str, ...] | None:
    """Return a path under Assets/ when the input is in an ignored asset root."""
    roots = sorted((root_parts(root) for root in ASSET_ROOTS), key=len, reverse=True)

    for index, part in enumerate(parts):
        if part.casefold() == "assets":
            suffix = parts[index + 1 :]
            suffix_lower = tuple(item.casefold() for item in suffix)
            if any(suffix_lower[: len(root)] == root for root in roots):
                return suffix

    lowered = tuple(part.casefold() for part in parts)
    for root in roots:
        for index in range(len(parts) - len(root) + 1):
            if lowered[index : index + len(root)] == root:
                return parts[index:]
    return None


def is_within_asset_roots(relative: tuple[str, ...]) -> bool:
    lowered = tuple(part.casefold() for part in relative)
    return any(
        lowered[: len(root_parts(root))] == root_parts(root)
        for root in ASSET_ROOTS
    )


def copy_stream(source, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("wb") as target:
        shutil.copyfileobj(source, target, length=1024 * 1024)


def copy_asset_input(input_path: Path, project: Path) -> tuple[int, set[str]]:
    assets_dir = project / "Assets"
    copied = 0
    copied_roots: set[str] = set()

    def consume(parts: tuple[str, ...], open_stream) -> None:
        nonlocal copied
        relative = map_asset_path(parts)
        if relative is None or not is_within_asset_roots(relative):
            return
        destination = assets_dir.joinpath(*relative)
        resolved_root = assets_dir.resolve()
        resolved_destination = destination.resolve()
        if resolved_root != resolved_destination and resolved_root not in resolved_destination.parents:
            raise BuildError(f"Asset path escapes the Unity project: {destination}")
        with open_stream() as source:
            copy_stream(source, destination)
        copied += 1
        for asset_root in ASSET_ROOTS:
            root = PurePosixPath(asset_root).parts
            if tuple(part.casefold() for part in relative[: len(root)]) == root_parts(asset_root):
                copied_roots.add(asset_root)
                break

    if input_path.is_dir():
        for file_path in input_path.rglob("*"):
            if file_path.is_symlink() or not file_path.is_file():
                continue
            relative_parts = safe_parts(file_path.relative_to(input_path).as_posix())
            consume(relative_parts, lambda path=file_path: path.open("rb"))
        return copied, copied_roots

    if not input_path.is_file():
        raise BuildError(f"Asset input does not exist: {input_path}")
    if not zipfile.is_zipfile(input_path):
        raise BuildError(f"Asset input is not a ZIP archive or directory: {input_path}")

    with zipfile.ZipFile(input_path) as archive:
        for info in archive.infolist():
            if info.is_dir():
                continue
            parts = safe_parts(info.filename)
            mode = (info.external_attr >> 16) & 0xFFFF
            if stat.S_ISLNK(mode):
                raise BuildError(f"Symlink found in asset archive: {info.filename}")
            consume(parts, lambda item=info: archive.open(item, "r"))

    return copied, copied_roots


def missing_asset_roots(project: Path) -> list[str]:
    missing = []
    for asset_root in ASSET_ROOTS:
        directory = project / "Assets" / Path(asset_root)
        if not directory.is_dir() or not any(
            file_path.is_file() and file_path.suffix.casefold() != ".meta"
            for file_path in directory.rglob("*")
        ):
            missing.append(asset_root)
    return missing


def ensure_project(project: Path) -> None:
    if project.exists() and (project / "ProjectSettings" / "ProjectVersion.txt").is_file():
        return
    if project.exists() and any(project.iterdir()):
        raise BuildError(
            f"Project folder exists but is not a Unity project: {project}. "
            "Pass the correct folder with --project."
        )
    if not shutil.which("git"):
        raise BuildError("Git is required to clone the upstream Unity project.")
    project.parent.mkdir(parents=True, exist_ok=True)
    print(f"Cloning upstream project into {project} ...")
    try:
        subprocess.run(
            ["git", "clone", "--depth", "1", UPSTREAM_URL, str(project)],
            check=True,
        )
    except subprocess.CalledProcessError as error:
        raise BuildError(f"Could not clone the upstream project (exit {error.returncode}).") from error


def check_project_version(project: Path) -> None:
    version_file = project / "ProjectSettings" / "ProjectVersion.txt"
    if not version_file.is_file():
        raise BuildError(f"Missing Unity version file: {version_file}")
    value = ""
    for line in version_file.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.startswith("m_EditorVersion:"):
            value = line.split(":", 1)[1].strip()
            break
    if value != EXPECTED_UNITY_VERSION:
        raise BuildError(
            f"This project targets Unity {value or '(unknown)'}, but this wrapper is "
            f"pinned to {EXPECTED_UNITY_VERSION}. Do not open and upgrade the local project "
            "unless you intend to migrate it."
        )


def check_campaign_scenes(project: Path) -> None:
    settings = project / "ProjectSettings" / "EditorBuildSettings.asset"
    if not settings.is_file():
        raise BuildError(f"Missing Unity Build Settings file: {settings}")

    enabled_scenes: set[str] = set()
    current_enabled = False
    for line in settings.read_text(encoding="utf-8", errors="replace").splitlines():
        item = line.strip()
        if item.startswith("- enabled:"):
            current_enabled = item.split(":", 1)[1].strip() == "1"
        elif item.startswith("enabled:"):
            current_enabled = item.split(":", 1)[1].strip() == "1"
        elif item.startswith("path:") and current_enabled:
            enabled_scenes.add(item.split(":", 1)[1].strip())

    missing = [scene for scene in REQUIRED_CAMPAIGN_SCENES if scene not in enabled_scenes]
    if missing:
        formatted = "\n".join(f"  {scene}" for scene in missing)
        raise BuildError(
            "Campaign build scenes are missing or disabled in Editor Build Settings:\n"
            f"{formatted}"
        )


def find_unity(explicit: str | None) -> Path:
    candidates: list[Path] = []
    if explicit:
        candidates.append(Path(explicit).expanduser())
    env_editor = os.environ.get("UNITY_EDITOR")
    if env_editor:
        candidates.append(Path(env_editor).expanduser())

    for command in ("Unity", "Unity.exe", "unity"):
        found = shutil.which(command)
        if found:
            candidates.append(Path(found))

    home = Path.home()
    candidates.extend(
        [
            home / "Unity/Hub/Editor/2017.3.0f3/Editor/Unity",
            home / "Unity/Hub/Editor/2017.3.0f3/Editor/Unity.exe",
            Path("/Applications/Unity/Hub/Editor/2017.3.0f3/Unity.app/Contents/MacOS/Unity"),
            Path("C:/Program Files/Unity/Hub/Editor/2017.3.0f3/Editor/Unity.exe"),
        ]
    )
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    raise BuildError(
        "Unity 2017.3.0f3 Editor was not found. Pass its executable with --unity "
        "or set UNITY_EDITOR. Install Android Build Support for that editor."
    )


def has_android_module(unity: Path) -> bool:
    for parent in (unity.parent, *unity.parents):
        if (parent / "PlaybackEngines" / "AndroidPlayer").is_dir():
            return True
        if (parent / "Editor" / "Data" / "PlaybackEngines" / "AndroidPlayer").is_dir():
            return True
    return False


def install_editor_entry_point(project: Path) -> None:
    editor_dir = project / "Assets" / "Editor"
    editor_dir.mkdir(parents=True, exist_ok=True)
    script_path = editor_dir / "DarkestDungeonLocalAndroidBuild.cs"
    if script_path.exists() and "Generated locally by the Darkest Dungeon Android build wrapper." not in script_path.read_text(
        encoding="utf-8", errors="replace"
    ):
        raise BuildError(
            f"Refusing to replace an existing editor script: {script_path}. "
            "Move it aside and retry."
        )
    script_path.write_text(EDITOR_SCRIPT, encoding="utf-8")


def print_missing_assets(missing: list[str]) -> None:
    if missing:
        print("Missing asset directories:", file=sys.stderr)
        for asset_root in missing:
            print(f"  Assets/{asset_root}", file=sys.stderr)
        print(
            "Import Ignored files.zip and Heroes.zip from the issue-linked Drive folder, "
            "or supply an extracted asset directory with --asset-input.",
            file=sys.stderr,
        )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Import the local issue-linked asset archives and build an Android APK."
    )
    parser.add_argument(
        "--project",
        type=Path,
        default=DEFAULT_PROJECT,
        help=f"Unity project directory (default: {DEFAULT_PROJECT})",
    )
    parser.add_argument(
        "--asset-input",
        action="append",
        type=Path,
        default=[],
        help="Local ZIP archive or extracted directory; repeat for both issue files.",
    )
    parser.add_argument("--unity", help="Unity Editor executable; defaults to UNITY_EDITOR or common install paths.")
    parser.add_argument(
        "--prepare-only",
        action="store_true",
        help="Clone/import/validate assets without invoking Unity.",
    )
    args = parser.parse_args()

    try:
        project = args.project.expanduser().resolve()
        ensure_project(project)
        check_project_version(project)
        check_campaign_scenes(project)

        total_copied = 0
        for input_path in args.asset_input:
            copied, roots = copy_asset_input(input_path.expanduser().resolve(), project)
            total_copied += copied
            print(f"Imported {copied} files from {input_path} ({len(roots)} asset roots).")

        if total_copied:
            print(f"Copied {total_copied} local asset files into {project / 'Assets'}.")

        missing = missing_asset_roots(project)
        print_missing_assets(missing)
        if missing:
            return 2

        if args.prepare_only:
            print("Project version and ignored asset directories are ready for a local Android build.")
            return 0

        unity = find_unity(args.unity)
        if not has_android_module(unity):
            raise BuildError(
                f"Android Build Support was not found beside {unity}. Add the Android module "
                "to Unity 2017.3.0f3 and retry."
            )

        install_editor_entry_point(project)
        APK_PATH.parent.mkdir(parents=True, exist_ok=True)
        environment = os.environ.copy()
        environment["DD_ANDROID_APK_PATH"] = str(APK_PATH.resolve())
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        command = [
            str(unity),
            "-batchmode",
            "-nographics",
            "-quit",
            "-buildTarget",
            "Android",
            "-projectPath",
            str(project),
            "-executeMethod",
            "DarkestDungeonLocalAndroidBuild.BuildAndroid",
            "-logFile",
            str(LOG_PATH.resolve()),
        ]
        print("Running Unity Android build. This may take a while on the first import...")
        result = subprocess.run(command, cwd=project, env=environment)
        if result.returncode != 0:
            raise BuildError(
                f"Unity exited with code {result.returncode}. Inspect {LOG_PATH} for details."
            )
        if not APK_PATH.is_file() or APK_PATH.stat().st_size == 0:
            raise BuildError(
                f"Unity exited successfully but did not create the expected APK at {APK_PATH}. "
                f"Inspect {LOG_PATH}."
            )

        print(f"APK created: {APK_PATH} ({APK_PATH.stat().st_size:,} bytes)")
        print(f"Unity log: {LOG_PATH}")
        return 0
    except (BuildError, OSError, zipfile.BadZipFile) as error:
        print(f"Build failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
