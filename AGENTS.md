# Project instructions

## Version and release tracking

The project owner explicitly requests consistent version tracking for future
work. Apply this policy when changing Panelyra or preparing a release.

- Record user-visible changes in `CHANGELOG.md`. Group work for a release;
  do not increment the application version for every commit or documentation edit.
- Use `MAJOR.MINOR.PATCH`: fixes increment PATCH and new features increment
  MINOR. While the project is `0.x`, incompatible changes require a new MINOR
  and explicit compatibility notes. After `1.0`, incompatible changes require
  a new MAJOR. Choose the next version from actual published releases and tags.
- For a coordinated release, keep `usbdisplay/__init__.py` (`__version__`),
  `android/app/build.gradle` (`versionName`), the default in
  `scripts/build-deb.py`, the newest release in
  `packaging/io.github.tanosx.Panelyra.metainfo.xml`, and the changelog aligned.
  Python packaging and source archive versions derive from `__version__`;
  the fallback Android builder reads Gradle metadata.
- Increase Android `versionCode` for each newly distributed Android build.
  Increment the GNOME extension's integer metadata `version` when distributing
  changed extension code. Neither counter is the application's semantic version.
- Preserve application IDs, extension UUIDs and signing identity during routine
  version bumps. Video `UTD1`, language `PLYL1`, and the extension D-Bus `Version`
  are separate protocol versions; change them only with a compatibility plan.
- Before publishing a release, verify metadata consistency, relevant tests,
  artifact names and checksums. Update current installation examples while
  preserving historical release notes. Use the actual release date.
- A source push is not a packaged release. Keep unreleased changes marked as
  such until release publication. Published releases use matching `vX.Y.Z`
  tags; never silently replace a published tag or artifact with different code.
- GitHub update notices require a published GitHub Release, not just a tag;
  the current notification client only offers stable versions.

Baseline when this policy was added: application `0.3.0` (changelog unreleased),
Android `versionCode` 4, GNOME extension metadata `version` 1. Read the actual
files and release history for subsequent work rather than relying on this baseline.
