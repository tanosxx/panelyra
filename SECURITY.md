# Security policy

Panelyra is pre-release software for a direct USB connection between devices you
control. The most recent source revision is the current maintenance target;
there is no promised long-term support or response-time commitment.

## Reporting a vulnerability

When the public GitHub repository has private vulnerability reporting enabled,
use **Security → Report a vulnerability** in that repository. This local source
tree does not assume that a reporting endpoint or email address already exists.

If private reporting is unavailable, open a minimal issue asking the maintainer
for a private reporting channel. Do not include exploit details, credentials,
personal desktop images or affected users' data in that public issue. Maintainers
should enable private reporting before announcing a public release.

Please include the affected revision, platform versions, preconditions, impact
and a small reproduction when a private channel is available.

## Trust boundaries

- UTD1 carries video without application-level authentication or encryption.
  USB address and subnet restrictions reduce exposure but do not authenticate a
  peer. Use a direct cable to a trusted computer; this is not an Internet service.
- The Android server listens on a detected USB IPv4 address, or on loopback for
  ADB. Video on TCP 27183 and language control on TCP 27184 use the same address
  and peer restrictions. Neither intentionally listens on Wi-Fi or `0.0.0.0`.
- Language control accepts only a fixed, bounded English/Russian setting and
  persists it locally. The channel has no application-level authentication or
  encryption; an allowed peer can change this preference. Invalid requests do
  not change the preference and cannot supply arbitrary UI text or commands.
- ADB requires Android's normal authorization. Approve only computers you trust.
- The Linux sender checks the USB driver, peer subnet and route before connecting.
- The temporary APK server exposes one selected APK and a landing page. It is
  not a general file server; stop it after installation.
- The video receiver validates sizes and timestamps before feeding MediaCodec.
  Android's decoder and the host's GStreamer plugins remain part of the trusted
  computing base and need normal operating-system security updates.
- GNOME screen capture exposes the additional monitor's contents to the tablet.
  Move sensitive windows away before starting a demonstration or shared session.
- On GNOME lock, the sender replaces capture with a local opaque lock image.
  It does not bypass GNOME's capture inhibition or unlock the PC. Automatic
  reconnection after unlocking reuses the same USB connection; it does not add
  remote input, password handling, or a GNOME Shell extension.
- The Linux bell reads public release metadata and plain-text news over HTTPS
  from the repository configured by the publisher. Repository control and
  GitHub's HTTPS service are part of the trust boundary. Notices cannot install
  an update or execute a command; optional links are restricted to that same
  GitHub repository and open only after a user action. Remote text and local
  cached notices are validated before display. The maintainer must protect
  repository write access and review news changes as public content.

## Signing and builds

Local debug APKs are for testing. Release APKs need a separately protected,
backed-up signing key whose certificate remains stable across updates. Never
commit a private key, key password, local backup or release secret. CI runs tests
and builds without production signing secrets or automatic publication.

See [privacy](docs/privacy.md) for data handling and
[distribution](docs/distribution.md) for release preparation.
