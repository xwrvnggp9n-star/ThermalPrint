# App Store Connect listing — ThermalPrint (iOS)

Copy-paste source for the App Store Connect submission of `app.sklar.thermalprint`.
Character limits noted per field; counts below are within limit.

---

## App information

- **Name** (max 30): `ThermalPrint`
- **Subtitle** (max 30): `Photos to your thermal printer`
- **Primary category:** Utilities
- **Secondary category:** Photo & Video
- **Bundle ID:** `app.sklar.thermalprint`
- **SKU:** `thermalprint-ios` (any unique string; not shown to users)
- **Primary language:** English (U.S.)
- **Copyright:** `© 2026 Sandor Sklar`

## URLs

- **Support URL:**
  https://thermalprint.sklar.app
- **Marketing URL:**
  https://thermalprint.sklar.app
- **Privacy Policy URL:** REQUIRED — see "Open items" below (needs a hosted page).

---

## Promotional text (max 170)

> Print your photos on an inexpensive MXW01 Bluetooth thermal printer, straight from your iPhone or iPad. What you see in the preview is exactly what prints.

## Description (max 4000)

> ThermalPrint turns your photos into crisp thermal prints on the MXW01 family of inexpensive Bluetooth "cat printer" thermal printers — right from your iPhone or iPad.
>
> WHAT YOU SEE IS WHAT PRINTS
> The preview is the exact 384-pixel, 1-bit bitmap the printer receives. No surprises between the screen and the paper.
>
> DIALED-IN DITHERING
> Floyd–Steinberg dithering with darkness, brightness, and contrast controls tuned for readable photos on thermal paper.
>
> SIMPLE BY DESIGN
> - Pick a photo, preview the real output, and print.
> - The printer is found automatically over Bluetooth and remembered for next time.
> - No account, no sign-in, no data collection.
>
> WORKS WITH
> The MXW01 family of Bluetooth thermal printers. A companion macOS app is available at thermalprint.sklar.app.
>
> ThermalPrint collects no data and includes no tracking. It talks only to your printer, over Bluetooth.

## Keywords (max 100, comma-separated, no spaces after commas for max density)

```
thermal printer,MXW01,cat printer,photo print,bluetooth,dither,receipt printer,mini printer,384
```
(93 chars)

## What's New (version notes, max 4000) — for v1.0 first release

> First release of ThermalPrint for iPhone and iPad. Print photos to your MXW01 Bluetooth thermal printer with a true-to-paper preview and adjustable dithering.

---

## App Privacy (nutrition label)

- **Data collection:** **Data Not Collected.** The app collects no data, has no analytics, no third-party SDKs, and no tracking. Local settings are stored in UserDefaults only (already declared in `PrivacyInfo.xcprivacy`, reason `CA92.1`).
- **Tracking:** No.

## Export compliance

- `ITSAppUsesNonExemptEncryption = NO` is already set in the Info.plist → answer **"No"** to the encryption question; no compliance docs needed.

## Age rating

- All content questions: **None** → rating **4+**.

## Pricing

- **$4.99 USD** (paid). Set in App Store Connect → *Pricing and Availability* (pick the $4.99
  price point directly; no code/build change — the IPA is identical for free vs paid).
- **PREREQUISITE:** to sell a paid app you must accept the **Paid Applications Agreement** and
  complete **tax + banking** info in App Store Connect (*Business → Agreements, Tax, and Banking*).
  The app cannot be submitted as paid until this agreement is active.
- **Commission:** 30%, or **15%** if enrolled in the **App Store Small Business Program**
  (revenue < $1M/year) — worth enrolling (~$0.75 vs ~$1.50 per sale at $4.99).
- Note: the macOS app is free; charging only for iOS is fine (separate SKUs).

---

## App Review Information (IMPORTANT — hardware dependency)

Reviewers will NOT have an MXW01 printer. Apps that need special hardware get rejected when the reviewer can't exercise them, so spell this out in the "Notes" field:

> ThermalPrint prints to an MXW01 Bluetooth thermal printer (external hardware not included with the app). A physical printer is NOT required to review the app: launch it, choose any photo, and the full UI — including the exact 1-bit print preview and the darkness/brightness/contrast/dither controls — works without a printer connected. The "Print" action is the only step that requires the paired hardware. Bluetooth permission is used solely to discover and send to the printer; no data is collected or transmitted anywhere else.

- Consider attaching a short **demo video** of a real print, since review can't reproduce it. (I can help script/record one.)
- **Demo account:** not applicable (no login).

---

## Screenshots (required — you must provide)

App Store Connect requires screenshots for at least:
- **6.9" iPhone** (e.g. iPhone 16 Pro Max / 17 Pro) — required.
- **6.5"/6.7" iPhone** — often auto-scaled from 6.9", but check.
- **13" iPad** — required if iPad is a supported device (it is; `TARGETED_DEVICE_FAMILY = 1,2`).

Capture on-device or in the simulator (Cmd-S in Simulator saves to Desktop). Good shots: the photo loaded with the live preview; the dithering controls; a finished print if you can photograph it.

---

## Open items (things I can't do headlessly)

1. **Privacy Policy URL** — App Store Connect requires one even when no data is collected. Simplest fix: add a `/privacy` page to the landing site (thermalprint.sklar.app) stating "ThermalPrint collects no data." I can draft the page HTML if you want.
2. **Screenshots** — must be captured by you (device or simulator).
3. **Create the app record** in App Store Connect for `app.sklar.thermalprint`, then the build can be uploaded.
