# Publishing on GitHub

[Home](../README.md) · [Release and App Center preparation](distribution.md)

The source tree is prepared for publication but does not assume that a public
repository already exists. **TanosX** is the chosen author credit; confirm the
actual GitHub account and ownership before replacing `YOUR_ACCOUNT` below.

## Prepare the repository

1. Review the [MIT license](../LICENSE), project name, README screenshots and
   supported platforms. Keep the pre-release status visible until device and
   package checks are complete.
2. Review the files Git would publish:

   ```bash
   git status --short --ignored
   git ls-files --cached --others --exclude-standard
   git check-ignore .tools/ out/ backups/
   ```

   `.tools/`, `out/` and `backups/` must remain excluded. A backup can contain a
   signing key even when its filename looks harmless. Also exclude local
   configuration, recordings, device identifiers and private SDK files.
3. Run the [development checks](development.md). Open both README languages and
   verify that screenshots, links and installation commands match the candidate.
4. Commit reviewed source and documentation. If preparing a new repository from
   a source archive, initialize Git first. Configure the author identity you
   want visible in Git history before committing.
5. Create an empty **Panelyra** repository under the confirmed account using
   GitHub's interface. Do not separately generate a second license or README.
   Set the repository visibility intentionally; a public push publishes source
   and commit history.
6. Add the real remote and push the branch intended as the default. For a new
   repository whose branch is named `main`:

   ```bash
   git remote add origin https://github.com/YOUR_ACCOUNT/Panelyra.git
   git push -u origin main
   ```

   These are templates. Replace the account and branch with actual values; if
   `origin` already exists, inspect it with `git remote -v` before changing it.

## Configure the project page

- Use the English README as the landing page and retain its Russian link.
- Set the description to “Turn an Android tablet into a second GNOME display over USB”.
- Suggested topics: `gnome`, `wayland`, `android`, `usb`, `second-monitor`,
  `gstreamer`, `python`, `linux`.
- Enable Issues and private vulnerability reporting. The issue forms and
  contribution/security policies are included in `.github/` and the source root.
- Enable Actions if desired and inspect the first CI run. A green workflow
  checks software and packaging; it does not certify physical USB playback.
- Enable Dependabot for action updates. Review updates before merging them.
- Add branch protections appropriate to the maintainers; do not require a
  workflow check until its first successful run establishes the actual check name.

## Enable the notification source

Before building the first public application, set the real repository and news
branch in `usbdisplay/notification_source.py`. The default repository is empty,
so the local preview makes no update/news requests. Commit the root
`announcements.json` to that branch and run:

```bash
/usr/bin/python3 scripts/validate-announcements.py
```

Follow [updates and developer news](notifications.md) for the schema, an example,
Russian instructions for TanosX and the publication workflow. Each later news
message needs only a reviewed feed commit and push. Stable GitHub Releases are
checked separately; publishing a draft or prerelease does not trigger a stable
update notice. Nothing in the validator, package builders or CI publishes news
or releases automatically.

## Create a release candidate

Complete the [distribution release requirements](distribution.md) first: real
maintainer contact, confirmed application namespace, public metadata URLs,
screenshots, a protected Android signing key and physical-device checks.

Prepare release notes from [CHANGELOG.md](../CHANGELOG.md), tag the exact reviewed
source revision and create a **draft or pre-release** while verification remains
incomplete. Attach the built `.deb`, separately signed Android APK and SHA-256
files. Label any intentionally shared debug build as a debug test build.

Build the source archive with the project's explicit file allowlist:

```bash
/usr/bin/python3 scripts/build-source.py --check
/usr/bin/python3 scripts/build-source.py
```

This creates `out/panelyra-0.3.0-source.tar.gz` and its `.sha256` file. `--list`
shows the audited input paths without building an archive. The archive excludes
Git metadata, local tools, build outputs, signing material and backups; it fails
on unexpected risky files inside source directories. It produces the same
archive bytes for the same source contents. Review its inputs and attach this
source archive to the candidate instead of archiving the entire working folder.

Record the source revision and build route. Do not attach `.tools/`, a keystore,
an entire local backup or desktop recordings. Keep private release signing
material outside the repository. Release assets and source must correspond to
the same reviewed revision.

After the public repository exists, update AppStream's homepage, issue/release
links and screenshot URLs to real reachable resources and validate without the
preview exception. GitHub publication alone does not add the app to Ubuntu's
App Center catalog; that process is covered separately in the
[distribution guide](distribution.md).
