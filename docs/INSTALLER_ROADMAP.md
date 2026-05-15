# Axion — Installer Roadmap

> **Status: roadmap only.** Nothing in the "Roadmap" sections below is
> built. Axion ships today exactly as described in **What ships today**.
> This document records the intended path to a premium, non-technical
> installer experience so the work has a stable shape to grow into. It
> introduces **no** certificates, accounts, secrets, or signing steps —
> see [`SIGNING_AND_NOTARIZATION.md`](SIGNING_AND_NOTARIZATION.md) for the
> credential requirements that a future implementer would need.

---

## What ships today

Axion is a **local, single-machine** application (release channel
`local`). It is delivered as a zip — `axion-macos.zip` /
`axion-windows.zip` — built by `scripts/build_release_zip.py`, each with
a `RELEASE_MANIFEST.json` build receipt. There are three supported ways
to launch it:

| Platform | Path | Notes |
|---|---|---|
| macOS / Linux | `./scripts/run_local.sh` | **Supported customer path.** Terminal launcher; creates the venv, runs migrations, starts the server, opens the dashboard. Avoids macOS Gatekeeper entirely. |
| macOS (Finder) | double-click `Axion.app` | Convenience option. **Unsigned** — see *Current limitations* below. |
| Windows | double-click `Axion.bat`, or `run_local.ps1` | Sets up the venv on first run, then starts the server. |

The launcher flow itself is stable and customer-tested (CI validates it
on macOS and Windows). The gap is purely **distribution polish**: there
is no signed, double-click *native installer* yet.

## Current limitations

1. **The macOS `.app` is not code-signed or notarized.** On first launch
   macOS Gatekeeper shows *"cannot be opened because Apple cannot check
   it for malicious software."* The customer must right-click → **Open**
   once to clear it. The terminal launcher (`run_local.sh`) avoids this.
2. **There is no native installer.** The product is a zip the customer
   unzips themselves. There is no `.dmg`, `.pkg`, `.msi`, or `.msix`.
3. **Windows ships as a `.bat`**, not a signed executable. A bundled
   `dist/Axion.exe` (PyInstaller) is *referenced* by `Axion.bat` as a
   preferred launch path but is **not built or shipped** in the release
   zip today.
4. **There is no auto-update mechanism.** Updating means downloading a
   new zip.
5. *Minor / cosmetic:* the icon assets in `assets/` are still named
   `kleitos.*` (legacy product name). They are functional — `Axion.bat`
   and the macOS bundle reference the correct files — but a future
   cleanup could rename them to `axion.*` for consistency.

None of these block a **technical** local customer. They are the
remaining gaps for a **non-technical / enterprise** handoff.

---

## Roadmap — native installers

> Everything below is unbuilt. Each item names what it needs; the
> signing mechanics live in
> [`SIGNING_AND_NOTARIZATION.md`](SIGNING_AND_NOTARIZATION.md).

### macOS — signed `.app` + `.dmg`

1. **Code-sign** `Axion.app` with an Apple *Developer ID Application*
   certificate, using the hardened runtime.
2. **Notarize** the app with Apple's notary service and **staple** the
   ticket so it launches offline without a Gatekeeper prompt.
3. Package the signed, notarized `.app` into a **`.dmg`** with a
   drag-to-Applications layout.
4. Optionally also notarize the `.dmg` itself.

Requires: an Apple Developer account, a Developer ID certificate, and an
App Store Connect API key for the notary service. None are present in
this repository.

### Windows — signed launcher + `.msi` / `.msix`

1. Build a real `Axion.exe` (PyInstaller) so Windows users get a true
   double-click executable rather than a `.bat`.
2. **Code-sign** the executable with an Authenticode (OV or EV)
   certificate, with an RFC-3161 **timestamp**.
3. Package into an **MSI** (WiX/Advanced Installer) or **MSIX** with a
   Start-menu entry and a clean uninstaller.
4. Sign the installer package itself.

Requires: a Windows code-signing certificate (OV or EV) and a signing
toolchain (`signtool`). None are present in this repository.

### CI integration

Once certificates exist, the signing/notarization steps would be added
to `.github/workflows/release-local-app.yml` as **post-build** steps,
gated on the presence of the relevant GitHub Actions secrets so forks
and credential-less runs still pass. The exact secret names and the
build-step shape are listed in
[`SIGNING_AND_NOTARIZATION.md`](SIGNING_AND_NOTARIZATION.md).

---

## Auto-update — future consideration only

Auto-update is **not designed and not implemented**. If pursued, the
honest constraints are:

- It must stay **local-first** — no silent phone-home, no telemetry. An
  update check would be an explicit, opt-in "Check for updates" action.
- It must preserve the customer's `~/axion-data/` and `~/.axion.env`
  untouched, and run the same pre-migration backup the launcher already
  performs.
- A signed update channel depends on the signing work above landing
  first — an unsigned auto-updater would be a security regression.

Until then, updating is: download the new zip, unzip, relaunch. The
launcher migrates the existing database (with a pre-migration backup)
automatically.

---

## Summary

| Capability | Today | Roadmap |
|---|---|---|
| Run locally (terminal) | ✅ shipped | — |
| Run via `Axion.app` (Finder) | ✅ shipped, **unsigned** | sign + notarize |
| Run via `Axion.bat` (Windows) | ✅ shipped | build + sign `Axion.exe` |
| Native installer (`.dmg` / `.msi`) | ❌ not built | roadmap |
| Code signing / notarization | ❌ not done | roadmap (needs certs) |
| Auto-update | ❌ not designed | future consideration only |

See also: [`INSTALL.md`](../INSTALL.md),
[`docs/FINAL_CUSTOMER_HANDOFF.md`](FINAL_CUSTOMER_HANDOFF.md),
[`KNOWN_LIMITATIONS.md`](../KNOWN_LIMITATIONS.md).
