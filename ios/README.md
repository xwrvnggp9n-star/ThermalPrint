# ThermalPrint for iOS

A native Swift / SwiftUI port of the macOS ThermalPrint app. Same job — print
photos to a cheap **MXW01** Bluetooth thermal printer without the adware "Fun
Print" app — same look and defaults, rebuilt for iPhone and iPad with
**CoreBluetooth** and **Core Graphics** instead of Python + bleak + Pillow.

<p align="center">
  <em>Choose a photo → preview the exact 384px dithered output → tune Darkness /
  Brightness / Contrast / Dither → print over Bluetooth LE.</em>
</p>

## Why a rewrite (and what carried over)

iOS has no AppKit and `bleak` doesn't run on iOS, so the macOS app's GUI and BLE
layers can't be reused. What ported is the valuable part — the reverse-engineered
**MXW01 protocol** and the **image pipeline** — and those are verified
byte-for-byte against the Python original (see [Verifying the port](#verifying-the-port)).

| Layer | macOS (Python) | iOS (Swift) |
|-------|----------------|-------------|
| UI | PyObjC / AppKit | SwiftUI |
| Bluetooth | `bleak` → CoreBluetooth | `CoreBluetooth` directly |
| Imaging | Pillow | Core Graphics + a pure-Swift core |
| Protocol | `mxw01.py` | `MXW01Protocol.swift` (clean port) |

## Project layout

```
ios/
  project.yml                 # XcodeGen spec — the source of truth
  ThermalPrint.xcodeproj/     # generated; committed so Xcode works without XcodeGen
  ThermalPrint/
    ThermalPrintApp.swift      # @main app + open-in handling
    ContentView.swift          # the one screen
    AboutView.swift            # About sheet (author, links, support)
    Model/
      AppInfo.swift            # identity/version (port of version.py)
      MXW01Protocol.swift      # BLE UUIDs, command framing, CRC-8, status parsing
      BitmapCore.swift         # pure pipeline: pack, dither, brightness, contrast
      BitmapRenderer.swift     # Core Graphics stages: orient/flatten/rotate/scale
      PrinterManager.swift     # CoreBluetooth connect + print state machine
    Assets.xcassets/           # AppIcon (full-bleed, alpha-free) + AccentColor
```

## Build & run

Requires Xcode 16+ (developed on Xcode 26). The project is generated from
`project.yml` with [XcodeGen](https://github.com/yonwoo9/XcodeGen); the generated
`.xcodeproj` is committed, so you only need XcodeGen if you add/remove files.

```bash
cd ios
xcodegen generate          # only needed after adding/removing source files
open ThermalPrint.xcodeproj
```

Then pick a simulator or your device and hit Run. From the command line:

```bash
cd ios
xcodebuild -project ThermalPrint.xcodeproj -scheme ThermalPrint \
  -sdk iphonesimulator -destination 'platform=iOS Simulator,name=iPhone 17' \
  CODE_SIGNING_ALLOWED=NO build
```

> **Bluetooth needs a real device.** The iOS Simulator has no BLE stack, so
> scanning/printing only works on a physical iPhone/iPad. The UI, preview, and
> dithering all work in the simulator.

## Verifying the port

The byte-level logic (CRC, command framing, print-request bytes, 1-bpp packing,
Floyd–Steinberg dither, brightness, contrast) is checked head-to-head against the
canonical `mxw01.py`:

```bash
bash tools/verify/run.sh      # from the repo root
```

It compiles the real `MXW01Protocol.swift` + `BitmapCore.swift` with a small
driver and diffs the output against Python. All packing/CRC/framing bytes match
exactly, and tone/dither match PIL exactly.

## Shipping to the App Store

The app is written to be submittable. Before you can run on-device or upload:

1. Open `project.yml` and set `DEVELOPMENT_TEAM` to your Apple Developer team ID
   (or set it in Xcode's Signing & Capabilities), then `xcodegen generate`.
2. Bundle id is `app.sklar.thermalprint` — change it to one your team owns.
3. Archive: **Product → Archive**, then distribute via the Organizer.

Already handled: a 1024×1024 alpha-free app icon, the Bluetooth usage string
(`NSBluetoothAlwaysUsageDescription`), the utilities app category, launch screen,
and supported orientations. Photo access uses `PhotosPicker`, which runs
out-of-process and needs no photo-library permission prompt.

Regenerate the app icon from the macOS artwork with:

```bash
.venv/bin/python tools/make_ios_icon.py
```
