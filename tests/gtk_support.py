"""One registered GTK application for every GUI test in this process."""

import os
import unittest

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gio, Gtk


_application = None


def get_test_application():
    global _application
    if _application is None:
        if not Gtk.init_check(None)[0]:
            raise unittest.SkipTest("No GTK display is available")
        # Multiple independently registered Gtk.Application hosts can conflict
        # over the process-wide desktop integration. Keep this host alive while
        # each test owns and destroys only its individual windows.
        application = Gtk.Application(
            application_id="io.github.tanosx.Panelyra.Tests.p" + str(os.getpid()),
            flags=Gio.ApplicationFlags.NON_UNIQUE)
        application.register(None)
        _application = application
    return _application
