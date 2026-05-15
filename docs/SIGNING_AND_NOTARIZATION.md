# Axion — Code Signing & Notarization

> **Status: requirements documentation only.** Axion is **not signed or
> notarized** in this build. No certificates, Apple/Microsoft accounts,
> signing secrets, or notarization credentials exist in this repository,
> and Phase 26 added none. This document records *exactly what a future
> implementer would need* and the steps they would run, so the signing
> work can be scoped and budgeted without guesswork.

For the higher-level distribution plan see
[`INSTALLER_ROADMAP.md`](INSTALLER_ROADMAP.md).

---

## Why signing matters

A downloaded, unsigned app triggers an OS security prompt:

- **macOS Gatekeeper** blocks first launch of an un-notarized app
  (*"cannot be opened because Apple cannot check it…"*).
- **Windows SmartScreen** warns on an unsigned executable
  (*"Windows protected your PC"*).

Signing + notarization removes those prompts and gives the customer a
verifiable chain of trust: the binary came from a known developer and
has not been tampered with since.

Until that work lands, the honest customer guidance is unchanged:
**use the terminal launcher** (`scripts/run_local.sh` /
`scripts/run_local.ps1`), which is not subject to Gatekeeper, or
right-click → **Open** the macOS `.app` once.

---

## macOS — Developer ID signing + notarization

### Prerequisites (none present in this repo)

| Requirement | Notes |
|---|---|
| Apple Developer Program membership | Paid annual account. |
| **Developer ID Application** certificate | Used to sign apps distributed *outside* the App Store. Installed in the build machine's keychain. |
| App Store Connect **API key** | `.p8` key + key ID + issuer ID — used by the notary service (`notarytool`). |

### Steps a future implementer would run

1. **Sign** the app with the hardened runtime:
   ```
   codesign --force --deep --options runtime \
       --sign "Developer ID Application: <NAME> (<TEAMID>)" \
       Axion.app
   ```
2. **Notarize** — submit to Apple's notary service and wait for the
   result:
   ```
   xcrun notarytool submit Axion.zip \
       --key AuthKey_<KEYID>.p8 --key-id <KEYID> --issuer <ISSUER> \
       --wait
   ```
3. **Staple** the notarization ticket so the app launches offline
   without a Gatekeeper round-trip:
   ```
   xcrun stapler staple Axion.app
   ```
4. Package the signed, stapled `.app` into a `.dmg` and (optionally)
   notarize the `.dmg` too.

### Notes

- The **hardened runtime** (`--options runtime`) is mandatory for
  notarization. It can require entitlement adjustments if the app does
  anything the runtime restricts; Axion is a plain local web server, so
  the default entitlements are expected to suffice.
- The current `Axion.app` carries only an **ad-hoc** signature
  (`Contents/_CodeSignature/`). Ad-hoc is *not* a Developer ID
  signature and does **not** satisfy Gatekeeper for a downloaded app.

---

## Windows — Authenticode code signing

### Prerequisites (none present in this repo)

| Requirement | Notes |
|---|---|
| Code-signing certificate | **OV** (Organization Validated) or **EV** (Extended Validation). EV clears Windows SmartScreen reputation faster. Modern certs are typically issued on a hardware token / HSM. |
| `signtool.exe` | From the Windows SDK. |
| A timestamp authority URL | RFC-3161 (e.g. the CA's TSA endpoint). |

### Steps a future implementer would run

1. Build a real `Axion.exe` (PyInstaller) — see
   [`INSTALLER_ROADMAP.md`](INSTALLER_ROADMAP.md).
2. **Sign with a timestamp:**
   ```
   signtool sign /fd SHA256 /tr http://timestamp.<ca>.com /td SHA256 \
       /a Axion.exe
   ```
3. Package into an **MSI** (WiX) or **MSIX**, and sign the installer
   package the same way.

---

## Timestamping — why `/tr` matters

A code-signing certificate expires (typically 1–3 years). Without a
**trusted timestamp**, every signature made with that certificate
becomes invalid the moment the certificate expires — including on
copies customers already installed.

An RFC-3161 timestamp records *when* the signing happened. The OS then
trusts the signature as long as the certificate was valid **at signing
time**, even years after the certificate itself expires. Both the
macOS notary flow and the Windows `signtool /tr` flag above provide
this. **Always timestamp.**

---

## CI secret requirements (for a future signed-release workflow)

If signing is added to `.github/workflows/release-local-app.yml`, it
would run as **post-build** steps and read the following GitHub Actions
**secrets**. These do **not** exist today and must be created by whoever
owns the certificates. Every signing step must be **conditional on the
secret being present** so credential-less runs (forks, this repo today)
still pass.

| Secret (suggested name) | Purpose |
|---|---|
| `MACOS_CERT_P12_BASE64` | Developer ID Application cert + key, base64-encoded. |
| `MACOS_CERT_PASSWORD` | Password for the `.p12`. |
| `MACOS_NOTARY_KEY_P8` | App Store Connect API `.p8` key (notarization). |
| `MACOS_NOTARY_KEY_ID` | API key ID. |
| `MACOS_NOTARY_ISSUER` | API key issuer ID. |
| `WINDOWS_CERT_PFX_BASE64` | Authenticode cert + key, base64-encoded (or HSM/token config). |
| `WINDOWS_CERT_PASSWORD` | Password for the `.pfx`. |

**Security rules for whoever implements this:**

- Never echo a secret to the CI log. Decode certs to a temp file,
  import to a temporary keychain/store, and delete on job completion.
- Keep signing steps `if:`-gated on secret presence so the workflow
  stays green without credentials.
- Do not commit any certificate, `.p12`/`.pfx`/`.p8`, or password to the
  repository — the release-zip builder already excludes `.env` and
  secret-shaped files; certificates must never be added to the tree.

---

## Current honest status

| Item | Status |
|---|---|
| macOS Developer ID signing | ❌ not done — no certificate |
| macOS notarization | ❌ not done — no Apple account/API key |
| macOS `.app` ad-hoc signature | ✅ present, but **not** Gatekeeper-valid for downloads |
| Windows Authenticode signing | ❌ not done — no certificate |
| Timestamping | ❌ n/a until signing exists |
| CI signing secrets | ❌ none created |

Nothing here is a blocker for a local technical customer. It is the
prerequisite list for a polished, prompt-free **non-technical**
distribution — to be picked up when certificates and accounts are
available.
