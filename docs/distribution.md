# Linux packaging and publication

Panelyra 0.3.0 is prepared for a local Debian package and a GitHub release.
It is **not published in an application store**. The known working target is
Ubuntu 26.04 / GNOME 50 / Wayland. A package that installs on another Linux
system does not establish that screen capture works there.

## Build a local `.deb`

Run from the repository root; building does not require root or a connected
tablet. `dpkg-deb` comes from Ubuntu's `dpkg` package.

```bash
python3 scripts/build-deb.py
```

The outputs are `out/panelyra_0.3.0_all.deb` and its `.sha256` file. The package
contains the desktop application, CLI, icon, AppStream metadata and documentation.
It relies on Ubuntu packages for Python, GTK, GStreamer and PipeWire rather
than copying this machine's dependencies. `Architecture: all` describes the
Python package, not a claim that every host architecture has been tested.

Only allowlisted application files are copied. `.git`, `.tools`, backups,
keystores, recordings and local settings are excluded. The Android APK is a
separate download by default. To include an APK in a **local preview** build:

```bash
python3 scripts/build-deb.py --include-apk out/panelyra-0.3.0-android-debug.apk
```

Use the actual output filename if it differs. This copies the selected APK to
`/usr/share/panelyra/out/panelyra.apk`; it does not sign, inspect or install it.
Do not distribute a developer's private signing key with either package.

## Install, launch, update and remove

Open the `.deb` in Ubuntu App Center, or use the terminal:

```bash
sudo apt install ./out/panelyra_0.3.0_all.deb
panelyra gui
panelyra --help
```

The application appears as **Panelyra** in the application menu. Run it as the
normal desktop user, without `sudo`. To update, install a newer `.deb` the same
way. This local package adds no package repository and never installs updates
automatically. The Linux bell can check GitHub for update notices and developer
news while the app is open, once a real public source is configured. Users can
disable those checks. See [notifications](notifications.md) and [privacy](privacy.md).

```bash
sudo apt remove panelyra
```

Removal deletes packaged files, not user settings. A launcher previously
installed from a source checkout is independent of the system package. Remove
that old launcher manually if it leaves a duplicate menu item.

Ubuntu's documentation distinguishes a local `.deb` opened in App Center from
a package discoverable in the catalog. Searchable Debian apps come from
configured repositories; publishing a file on GitHub does not add it to
Ubuntu's repositories. See [Snap and deb packages](https://ubuntu.com/desktop/docs/en/24.04/explanation/snap-and-deb-packages/).

## Package layout

| Location | Purpose |
| --- | --- |
| `/usr/bin/panelyra` | CLI and `gui` entry point |
| `/usr/bin/panelyra-launcher` | Desktop launcher alias |
| `/usr/share/panelyra/usbdisplay/` | Python application and assets |
| `/usr/share/applications/io.github.tanosx.Panelyra.desktop` | Desktop menu entry |
| `/usr/share/icons/hicolor/` | Scalable and PNG icons |
| `/usr/share/metainfo/io.github.tanosx.Panelyra.metainfo.xml` | AppStream description |
| `/usr/share/doc/panelyra/` | Documentation and license |

The Debian dependencies are in `packaging/debian/control.in`.
NetworkManager is recommended for automatic USB discovery and the temporary
local-only network configuration. ADB is optional. ADB permissions, GNOME
Wayland and a compatible Android hardware decoder must still be available on
the user's machine.

## Validate before distributing

```bash
desktop-file-validate packaging/io.github.tanosx.Panelyra.desktop
appstreamcli validate --no-net --override=url-homepage-missing=info \
  packaging/io.github.tanosx.Panelyra.metainfo.xml
python3 scripts/build-deb.py
dpkg-deb --info out/panelyra_0.3.0_all.deb
dpkg-deb --contents out/panelyra_0.3.0_all.deb
```

The scoped AppStream override is only for the unpublished preview: the actual
project URL is not known yet. It leaves the missing URL visible as an
informational finding. Do not use this override for a public release.

Before a release:

1. Confirm ownership of the GitHub namespace and project name. The current
   `io.github.tanosx.Panelyra` ID is based on the author's pseudonym; it is not
   proof of ownership of a GitHub account or a reserved store name.
2. Add the real HTTPS homepage, issue tracker and release links to MetaInfo.
   Add a real screenshot URL and caption. Never substitute fake URLs just to
   make validation pass.
   Configure the real repository and news branch in
   `usbdisplay/notification_source.py` before the application is built. Publish
   the root `announcements.json` there, validate it with
   `python3 scripts/validate-announcements.py`, and test the bell against the
   public source. Leave the source empty only if this distribution intentionally
   disables news and version checks; document that choice. The full workflow is
   in [updates and developer news](notifications.md).
3. Keep the version in the application, Android build, MetaInfo and package
   builder consistent. Describe the release in the changelog.
4. Choose a real maintainer contact. The default
   `TanosX <packaging@example.invalid>` deliberately marks local preview builds.
5. Run the tests, build in a clean Ubuntu environment, and install/upgrade/remove
   the `.deb` on a test system. Verify menu icon, CLI, USB reconnect, screen
   lock, cursor, keyboard/mouse, settings persistence and cleanup. Test the
   Android APK on the actual tablet; a build is not a device test.
6. Publish the signed Android APK separately, its checksum and the exact source
   version used to build it. Keep the signing key private and backed up. Check
   that the update certificate matches existing installations.
7. Validate all metadata without preview exceptions:

   ```bash
   appstreamcli validate --no-net --strict packaging/io.github.tanosx.Panelyra.metainfo.xml
   python3 scripts/build-deb.py --release \
     --maintainer 'YOUR NAME <YOUR REAL EMAIL>' \
     --homepage 'https://YOUR ACTUAL PROJECT URL'
   ```

The last command intentionally fails until the real public metadata is filled
in. It is a release gate, not a store submission or an ownership check.

## Ubuntu App Center / Snap Store

The practical first release is the tested `.deb` on GitHub. A Snap Store
listing is a separate packaging and review project. Start with a private
development build, audit permissions, test it on the supported desktop, and
then apply for the necessary store permissions before advertising availability.

Panelyra directly uses `org.gnome.Mutter.ScreenCast` (`RecordVirtual` and
`RecordMonitor`), `org.gnome.Mutter.DisplayConfig`, PipeWire, USB network
discovery and optionally ADB. This differs from a portal-only screenshot app.
The current snapd `screencast-legacy` policy names the GNOME Shell screenshot
and screencast APIs, not the Mutter ScreenCast API. Therefore adding that plug
alone does not establish that the current backend works in a strict snap.
See the [snapd policy source](https://github.com/canonical/snapd/blob/master/interfaces/builtin/screencast_legacy.go)
and [PipeWire interface documentation](https://snapcraft.io/docs/reference/interfaces/pipewire-interface/).

There are two routes to investigate:

- A strict snap with the least required interfaces, an appropriate portal or
  host integration backend, and evidence that virtual monitor creation and
  cleanup work inside confinement. Test USB networking and ADB separately.
- A classic snap only if the Snap Store team accepts the technical need.
  Classic confinement provides broad host access and requires manual review;
  specifying `confinement: classic` is not approval. See
  [classic confinement](https://snapcraft.io/docs/explanation/security/classic-confinement/)
  and the [review process](https://snapcraft.io/docs/reference/administration/reviewing-classic-confinement-snaps/).

No untested `snapcraft.yaml` is shipped as a working release recipe. When one
is added, pin a supported base, package the Python/GI/GStreamer runtime, verify
resource paths and codecs in the mounted snap, and reproduce installation on
a clean host. A wrapper that happens to find local development dependencies
is insufficient.

Once packaging is verified, register the chosen name with the publisher's
account, submit a development revision, request any required permissions,
complete the [store listing](store-listing.md), test the beta channel, and only
then promote a release to stable. Keep the unsupported desktop environments
visible in the listing.

## Flatpak / Flathub

Flatpak is a possible later target, not a supported format in this release.
The current backend needs deliberate session D-Bus permissions for Mutter,
access to its PipeWire stream and USB network discovery. ADB and temporary
NetworkManager configuration need separate consideration. A broad host escape
would undermine confinement and is not an acceptable substitute for a tested
integration. Prefer a portal backend when it can provide the required virtual
monitor behavior.

Flatpak filters D-Bus by default and recommends specific `--talk-name`
permissions rather than access to the entire session or system bus. See
[sandbox permissions](https://docs.flatpak.org/en/latest/sandbox-permissions.html).
Flathub also requires an app ID tied to a domain or code-hosting namespace the
publisher controls, accurate licenses and reviewable metadata. See
[submission requirements](https://docs.flathub.org/docs/for-app-authors/requirements)
and [MetaInfo guidelines](https://docs.flathub.org/docs/for-app-authors/metainfo-guidelines).
