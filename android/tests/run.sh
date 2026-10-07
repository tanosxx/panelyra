#!/usr/bin/env bash
# Standalone video/language parser and USB-network regressions; JDK 17+ is enough.
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
if [[ -n "${JAVA_HOME:-}" ]]; then
    JAVAC="$JAVA_HOME/bin/javac"
    JAVA="$JAVA_HOME/bin/java"
else
    JAVAC=javac
    JAVA=java
fi
CLASSES="$(mktemp -d "${TMPDIR:-/tmp}/panelyra-java-tests.XXXXXXXX")"
trap 'rm -rf -- "$CLASSES"' EXIT
"$JAVAC" --release 8 -encoding UTF-8 -d "$CLASSES" \
    "$ROOT/app/src/main/java/dev/usbdisplay/client/VideoProtocol.java" \
    "$ROOT/app/src/main/java/dev/usbdisplay/client/UsbNetwork.java" \
    "$ROOT/app/src/main/java/dev/usbdisplay/client/LanguageProtocol.java" \
    "$ROOT/app/src/main/java/dev/usbdisplay/client/LanguageServer.java" \
    "$ROOT/tests/dev/usbdisplay/client/VideoProtocolTest.java" \
    "$ROOT/tests/dev/usbdisplay/client/UsbNetworkTest.java" \
    "$ROOT/tests/dev/usbdisplay/client/LanguageProtocolTest.java" \
    "$ROOT/tests/dev/usbdisplay/client/LanguageServerTest.java"
"$JAVA" -cp "$CLASSES" dev.usbdisplay.client.VideoProtocolTest "$@"
"$JAVA" -cp "$CLASSES" dev.usbdisplay.client.UsbNetworkTest
"$JAVA" -cp "$CLASSES" dev.usbdisplay.client.LanguageProtocolTest
if [[ "${PANELYRA_TEST_NETWORK:-0}" == 1 ]]; then
    "$JAVA" -cp "$CLASSES" dev.usbdisplay.client.LanguageServerTest
fi
