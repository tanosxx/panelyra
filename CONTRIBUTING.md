# Contributing to Panelyra

Bug reports, tested device results, translations and focused patches are welcome.
English and Russian are both welcome in issues and pull requests.

## Report a problem

Use the bug report form in the repository where this project is published.
Include the Panelyra version, Linux distribution, GNOME version, session type,
tablet model, Android version, USB transport and video mode. Describe what you
expected and what happened, with the smallest reproducible steps.

Include relevant output from `panelyra doctor` or `panelyra start --stats` after
removing device serial numbers, IP addresses, usernames and unrelated content.
Do not post screen recordings containing personal data or signing keys.
For security problems, read [SECURITY.md](SECURITY.md) first.

## Make a change

1. Read the [architecture](docs/architecture.md) and [development guide](docs/development.md).
2. Make one focused change in a branch. Preserve working USB tethering and the
   existing Android application ID unless a migration is explicitly designed.
3. Add a regression test for changes to framing, USB interface selection,
   connection ownership, decoder lifecycle or cleanup. Explain hardware-only
   changes and the devices used to verify them.
4. Run the relevant tests. For capture changes, verify normal shutdown and cable
   disconnection in a real GNOME session; CI has no interactive Mutter session.
5. Update both English and Russian user documentation when behavior changes.
6. Describe the problem, resulting behavior, tests and remaining limitations in
   the pull request. Say when physical-device testing was not possible.

Keep unrelated formatting and generated files out of a patch. Never commit
`.tools/`, `out/`, `backups/`, Android SDK downloads, APK signing keys or desktop
captures. Build outputs belong in release artifacts after review.

Use Python's standard library where practical. The Android client deliberately
uses Java, MediaCodec and native views to remain small; new dependencies should
solve a demonstrated problem. Preserve bounded video queues and never discard
arbitrary encoded reference frames to improve latency.

By submitting a contribution, you agree that it may be distributed under the
project's [MIT License](LICENSE). Please credit any third-party material and
include its required license notices.
