# Darkest Dungeon Unity — local Android build wrapper

This repository is a small, local build wrapper for [Reinisch/Darkest-Dungeon-Unity](https://github.com/Reinisch/Darkest-Dungeon-Unity). The upstream Unity project contains the campaign and gameplay code, but omits its ignored game assets. This repository does not mirror that source or publish Darkest Dungeon assets.

The maintainer linked the missing files in [issue #17](https://github.com/Reinisch/Darkest-Dungeon-Unity/issues/17): `Ignored files.zip` and `Heroes.zip`. Download both from the [shared Drive folder](https://drive.google.com/drive/folders/0B4fCt9AnmePGNDJGcmFZckpTanM?resourcekey=0-i1228-uq1SJcdtxXZOg-eg) and keep them local. The build helper copies only the ignored asset directories into the local Unity checkout.

## Requirements

- Python 3.9 or newer
- Git
- Unity Editor **2017.3.0f3** with Android Build Support installed
- The two ZIP archives from the issue-linked Drive folder, unless the local Unity checkout already has all ignored assets

The upstream project targets Unity 2017.3. The current GitHub release is an old PvP-only APK, so this wrapper builds from the project source instead of reusing that APK.

## Build a local APK

From this repository root, run:

```powershell
python tools/build_android.py `
  --asset-input "C:\Users\you\Downloads\Ignored files.zip" `
  --asset-input "C:\Users\you\Downloads\Heroes.zip" `
  --unity "C:\Program Files\Unity\Hub\Editor\2017.3.0f3\Editor\Unity.exe"
```

The first run clones the upstream project into the ignored `local-projects/` folder. The helper imports files only under the asset paths excluded by the upstream `.gitignore`, validates the expected asset folders, and invokes Unity in batch mode. The APK and build log go to `artifacts/`.

To import and validate the files without building, add `--prepare-only`. To use an existing checkout, pass `--project "path\to\Darkest-Dungeon-Unity"`.

The build uses the scenes enabled in the Unity project's Build Settings and stops if Campaign Selection, Estate Management, or Dungeon is missing or disabled. It does not alter the campaign data or add game assets to this public repository. Unity may require its local license to be activated before batch builds can run.

## Android status

The upstream README lists estate management, heroes, combat, quest generation, town events, inventory, plot quest maps, and narration as implemented. It also lists improved Android/iOS UI as unfinished. A successful APK build therefore confirms packaging, not full touch usability; campaign navigation and dungeon interaction still need device testing and Android-specific UI work.
