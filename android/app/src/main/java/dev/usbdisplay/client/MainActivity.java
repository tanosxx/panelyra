package dev.usbdisplay.client;

import android.app.Activity;
import android.content.ActivityNotFoundException;
import android.content.Context;
import android.content.Intent;
import android.content.res.Configuration;
import android.graphics.Color;
import android.graphics.Typeface;
import android.graphics.drawable.GradientDrawable;
import android.graphics.drawable.StateListDrawable;
import android.provider.Settings;
import android.os.Bundle;
import android.util.Log;
import android.view.Gravity;
import android.view.Surface;
import android.view.SurfaceHolder;
import android.view.SurfaceView;
import android.view.View;
import android.view.WindowManager;
import android.widget.Button;
import android.widget.FrameLayout;
import android.widget.ImageView;
import android.widget.LinearLayout;
import android.widget.TextView;
import android.widget.ScrollView;
import android.widget.Toast;

import java.io.BufferedInputStream;
import java.io.Closeable;
import java.io.IOException;
import java.net.InetSocketAddress;
import java.net.ServerSocket;
import java.net.Socket;
import java.net.SocketTimeoutException;
import java.util.Locale;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicBoolean;

/** Foreground-only display over USB tethering, with Android loopback as an ADB fallback. */
public final class MainActivity extends Activity implements SurfaceHolder.Callback {
    private static final String TAG = "Panelyra";
    private static final int INK = Color.rgb(245, 247, 252);
    private static final int MUTED = Color.rgb(170, 183, 204);
    private static final int MINT = Color.rgb(105, 228, 203);
    private static final int INDIGO = Color.rgb(116, 107, 255);
    private static final String PREFERENCES = "panelyra";
    private static final String PC_LANGUAGE = "pc_language";

    private Context localizedContext;
    private String selectedLanguage;
    private FrameLayout root;
    private SurfaceView video;
    private TextView status;
    private View statusPanel;
    private TextView statusTitle;
    private TextView connectionLabel;
    private TextView connectionAddress;
    private LinearLayout connectionActions;
    private boolean resumed;
    private boolean surfaceReady;
    private Session session;
    private int videoWidth;
    private int videoHeight;
    private int currentTitle = R.string.search_title;
    private int currentMessage = R.string.search_message;
    private UsbNetwork.Target currentTarget;
    private Throwable currentError;
    private boolean currentCanRefresh;
    private boolean currentDiscoveryDiagnostic;

    @Override
    public void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        setLanguageContext(getSharedPreferences(PREFERENCES, MODE_PRIVATE).getString(PC_LANGUAGE, null));
        getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
        root = new FrameLayout(this);
        root.setBackgroundColor(Color.BLACK);
        video = new SurfaceView(this);
        video.getHolder().addCallback(this);
        root.addView(video, new FrameLayout.LayoutParams(
                FrameLayout.LayoutParams.MATCH_PARENT, FrameLayout.LayoutParams.MATCH_PARENT,
                Gravity.CENTER));
        statusPanel = createWelcomePanel();
        root.addView(statusPanel, new FrameLayout.LayoutParams(
                FrameLayout.LayoutParams.MATCH_PARENT, FrameLayout.LayoutParams.MATCH_PARENT));
        root.addOnLayoutChangeListener((v, l, t, r, b, ol, ot, or, ob) -> {
            if (r - l != or - ol || b - t != ob - ot) {
                fitVideo();
            }
        });
        setContentView(root);
        hideSystemBars();
    }

    /** Plain platform views keep the waiting screen light on older tablets. */
    private View createWelcomePanel() {
        ScrollView scroll = new ScrollView(this);
        scroll.setFillViewport(true);
        scroll.setClipToPadding(false);
        scroll.setBackground(new GradientDrawable(GradientDrawable.Orientation.TL_BR,
                new int[]{Color.rgb(17, 25, 39), Color.rgb(24, 31, 52)}));
        LinearLayout page = new LinearLayout(this);
        page.setGravity(Gravity.CENTER);
        page.setPadding(dp(32), dp(24), dp(32), dp(24));
        boolean wide = getResources().getConfiguration().screenWidthDp >= 760;
        page.setOrientation(wide ? LinearLayout.HORIZONTAL : LinearLayout.VERTICAL);
        scroll.addView(page, new ScrollView.LayoutParams(
                ScrollView.LayoutParams.MATCH_PARENT, ScrollView.LayoutParams.WRAP_CONTENT));

        LinearLayout introduction = new LinearLayout(this);
        introduction.setOrientation(LinearLayout.VERTICAL);
        LinearLayout brand = new LinearLayout(this);
        brand.setGravity(Gravity.CENTER_VERTICAL);
        ImageView mark = new ImageView(this);
        mark.setImageResource(R.mipmap.ic_launcher);
        mark.setImportantForAccessibility(View.IMPORTANT_FOR_ACCESSIBILITY_NO);
        brand.addView(mark, new LinearLayout.LayoutParams(dp(68), dp(68)));
        TextView name = text(R.string.app_name, 34, INK);
        name.setTypeface(Typeface.create("sans-serif-medium", Typeface.NORMAL));
        LinearLayout.LayoutParams nameParams = wrap();
        nameParams.leftMargin = dp(14);
        brand.addView(name, nameParams);
        introduction.addView(brand);
        TextView subtitle = text(R.string.app_subtitle, 19, MUTED);
        subtitle.setLineSpacing(dp(3), 1);
        LinearLayout.LayoutParams subtitleParams = wrap();
        subtitleParams.topMargin = dp(16);
        subtitleParams.bottomMargin = dp(24);
        introduction.addView(subtitle, subtitleParams);
        addStep(introduction, "1", R.string.step_cable);
        addStep(introduction, "2", R.string.step_tether);
        addStep(introduction, "3", R.string.step_start);
        LinearLayout.LayoutParams introParams = wide
                ? new LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1)
                : new LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT,
                        LinearLayout.LayoutParams.WRAP_CONTENT);
        if (wide) introParams.rightMargin = dp(32);
        else introParams.bottomMargin = dp(24);
        page.addView(introduction, introParams);

        LinearLayout card = new LinearLayout(this);
        card.setOrientation(LinearLayout.VERTICAL);
        card.setPadding(dp(28), dp(28), dp(28), dp(28));
        card.setBackground(rounded(Color.rgb(30, 40, 59), 24, Color.rgb(51, 66, 87)));
        connectionLabel = text(R.string.connection_label, 13, MINT);
        connectionLabel.setTypeface(Typeface.create("sans-serif-medium", Typeface.NORMAL));
        card.addView(connectionLabel);
        statusTitle = text(R.string.search_title, 27, INK);
        statusTitle.setTypeface(Typeface.create("sans-serif-medium", Typeface.NORMAL));
        LinearLayout.LayoutParams titleParams = wrap();
        titleParams.topMargin = dp(15);
        titleParams.bottomMargin = dp(12);
        card.addView(statusTitle, titleParams);
        status = text(R.string.search_message, 16, MUTED);
        status.setLineSpacing(dp(4), 1);
        card.addView(status);
        connectionAddress = text(R.string.empty, 17, INK);
        connectionAddress.setTypeface(Typeface.MONOSPACE);
        connectionAddress.setTextIsSelectable(true);
        connectionAddress.setPadding(dp(14), dp(12), dp(14), dp(12));
        connectionAddress.setBackground(rounded(Color.rgb(17, 25, 39), 10, 0));
        connectionAddress.setVisibility(View.GONE);
        LinearLayout.LayoutParams addressParams = new LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT, LinearLayout.LayoutParams.WRAP_CONTENT);
        addressParams.topMargin = dp(20);
        card.addView(connectionAddress, addressParams);
        connectionActions = new LinearLayout(this);
        connectionActions.setOrientation(LinearLayout.VERTICAL);
        Button settings = action(R.string.open_settings, true);
        settings.setOnClickListener(v -> openConnectionSettings());
        connectionActions.addView(settings, new LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT, dp(50)));
        Button refresh = action(R.string.refresh, false);
        refresh.setOnClickListener(v -> {
            stopSession();
            startIfReady();
        });
        LinearLayout.LayoutParams refreshParams = new LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT, dp(48));
        refreshParams.topMargin = dp(10);
        connectionActions.addView(refresh, refreshParams);
        connectionActions.setVisibility(View.GONE);
        LinearLayout.LayoutParams actionsParams = new LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT, LinearLayout.LayoutParams.WRAP_CONTENT);
        actionsParams.topMargin = dp(22);
        card.addView(connectionActions, actionsParams);
        LinearLayout.LayoutParams cardParams = wide
                ? new LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1)
                : new LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT,
                        LinearLayout.LayoutParams.WRAP_CONTENT);
        page.addView(card, cardParams);
        return scroll;
    }

    private void addStep(LinearLayout parent, String number, int label) {
        LinearLayout row = new LinearLayout(this);
        row.setGravity(Gravity.CENTER_VERTICAL);
        TextView badge = text(R.string.empty, 14, MINT);
        badge.setText(number);
        badge.setGravity(Gravity.CENTER);
        badge.setBackground(rounded(Color.rgb(34, 56, 65), 16, 0));
        row.addView(badge, new LinearLayout.LayoutParams(dp(30), dp(30)));
        TextView description = text(label, 15, INK);
        LinearLayout.LayoutParams descParams = new LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT, LinearLayout.LayoutParams.WRAP_CONTENT);
        descParams.leftMargin = dp(12);
        row.addView(description, descParams);
        LinearLayout.LayoutParams rowParams = new LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT, LinearLayout.LayoutParams.WRAP_CONTENT);
        rowParams.bottomMargin = dp(12);
        parent.addView(row, rowParams);
    }

    private TextView text(int resource, int size, int color) {
        TextView view = new TextView(this);
        view.setText(localized(resource));
        view.setTextSize(size);
        view.setTextColor(color);
        return view;
    }

    private Button action(int label, boolean primary) {
        Button button = new Button(this);
        button.setText(localized(label));
        button.setTextSize(15);
        button.setAllCaps(false);
        button.setTextColor(INK);
        button.setPadding(dp(16), 0, dp(16), 0);
        StateListDrawable background = new StateListDrawable();
        background.addState(new int[]{android.R.attr.state_pressed},
                rounded(primary ? Color.rgb(96, 87, 225) : Color.rgb(50, 65, 89), 12, 0));
        background.addState(new int[]{android.R.attr.state_focused},
                rounded(primary ? INDIGO : Color.rgb(42, 55, 78), 12, MINT));
        background.addState(new int[]{},
                rounded(primary ? INDIGO : Color.rgb(42, 55, 78), 12, 0));
        button.setBackground(background);
        return button;
    }

    private GradientDrawable rounded(int color, int radius, int border) {
        GradientDrawable shape = new GradientDrawable();
        shape.setColor(color);
        shape.setCornerRadius(dp(radius));
        if (border != 0) shape.setStroke(dp(1), border);
        return shape;
    }

    private LinearLayout.LayoutParams wrap() {
        return new LinearLayout.LayoutParams(LinearLayout.LayoutParams.WRAP_CONTENT,
                LinearLayout.LayoutParams.WRAP_CONTENT);
    }

    private int dp(int value) {
        return Math.round(value * getResources().getDisplayMetrics().density);
    }

    private void openConnectionSettings() {
        try {
            startActivity(new Intent(Settings.ACTION_WIRELESS_SETTINGS));
        } catch (ActivityNotFoundException unavailable) {
            try {
                startActivity(new Intent(Settings.ACTION_SETTINGS));
            } catch (ActivityNotFoundException missing) {
                Toast.makeText(this, localized(R.string.settings_unavailable), Toast.LENGTH_LONG).show();
            }
        }
    }

    private String localized(int resource, Object... arguments) {
        return localizedContext.getString(resource, arguments);
    }

    private void setLanguageContext(String pcLanguage) {
        Configuration configuration = new Configuration(getResources().getConfiguration());
        selectedLanguage = LanguageProtocol.selectLanguage(pcLanguage, configuration.locale.getLanguage());
        configuration.setLocale(new Locale(selectedLanguage));
        localizedContext = createConfigurationContext(configuration);
    }

    /** Update only the overlay: the SurfaceView, decoder and active stream remain in place. */
    private void applyLanguage(String language) {
        getSharedPreferences(PREFERENCES, MODE_PRIVATE).edit().putString(PC_LANGUAGE, language).apply();
        if (language.equals(selectedLanguage)) return;
        setLanguageContext(language);
        int visibility = statusPanel.getVisibility();
        root.removeView(statusPanel);
        statusPanel = createWelcomePanel();
        root.addView(statusPanel, new FrameLayout.LayoutParams(
                FrameLayout.LayoutParams.MATCH_PARENT, FrameLayout.LayoutParams.MATCH_PARENT));
        renderStatus();
        statusPanel.setVisibility(visibility);
    }

    private void updateStatus(int title, int message, UsbNetwork.Target target, boolean canRefresh,
                              Throwable error, boolean discoveryDiagnostic) {
        currentTitle = title;
        currentMessage = message;
        currentTarget = target;
        currentCanRefresh = canRefresh;
        currentError = error;
        currentDiscoveryDiagnostic = discoveryDiagnostic;
        renderStatus();
        statusPanel.setVisibility(View.VISIBLE);
    }

    private void renderStatus() {
        statusTitle.setText(localized(currentTitle));
        String message = currentError == null ? localized(currentMessage)
                : localized(currentMessage, readableError(currentError));
        if (currentDiscoveryDiagnostic && currentTarget != null && currentTarget.discoveryError != null) {
            message += "\n\n" + localized(R.string.diagnostic, currentTarget.discoveryError);
        }
        status.setText(message);
        connectionLabel.setText(localized(currentTarget == null ? R.string.connection_label
                : currentTarget.usb ? R.string.connection_usb : R.string.connection_adb));
        connectionAddress.setVisibility(currentTarget == null ? View.GONE : View.VISIBLE);
        if (currentTarget != null) connectionAddress.setText(currentTarget.endpoint());
        connectionActions.setVisibility(currentCanRefresh ? View.VISIBLE : View.GONE);
    }

    @Override
    protected void onResume() {
        super.onResume();
        resumed = true;
        hideSystemBars();
        startIfReady();
    }

    @Override
    protected void onPause() {
        resumed = false;
        stopSession();
        super.onPause();
    }

    @Override
    public void onWindowFocusChanged(boolean hasFocus) {
        super.onWindowFocusChanged(hasFocus);
        if (hasFocus) {
            hideSystemBars();
        }
    }

    private void hideSystemBars() {
        getWindow().getDecorView().setSystemUiVisibility(
                View.SYSTEM_UI_FLAG_IMMERSIVE_STICKY | View.SYSTEM_UI_FLAG_FULLSCREEN
                        | View.SYSTEM_UI_FLAG_HIDE_NAVIGATION | View.SYSTEM_UI_FLAG_LAYOUT_STABLE
                        | View.SYSTEM_UI_FLAG_LAYOUT_FULLSCREEN | View.SYSTEM_UI_FLAG_LAYOUT_HIDE_NAVIGATION);
    }

    @Override
    public void surfaceCreated(SurfaceHolder holder) {
        surfaceReady = true;
        startIfReady();
    }

    @Override
    public void surfaceChanged(SurfaceHolder holder, int format, int width, int height) {
        fitVideo();
    }

    @Override
    public void surfaceDestroyed(SurfaceHolder holder) {
        surfaceReady = false;
        stopSession();
    }

    private void startIfReady() {
        if (resumed && surfaceReady && session == null) {
            updateStatus(R.string.search_title, R.string.search_message, null, false, null, false);
            session = new Session(video.getHolder().getSurface());
            new Thread(session, "usb-display-connection").start();
        }
    }

    private void stopSession() {
        if (session != null) {
            Session previous = session;
            session = null;
            previous.stop();
        }
    }

    private void fitVideo() {
        if (videoWidth <= 0 || videoHeight <= 0 || root.getWidth() <= 0 || root.getHeight() <= 0) {
            return;
        }
        double scale = Math.min((double) root.getWidth() / videoWidth,
                (double) root.getHeight() / videoHeight);
        int width = Math.max(1, (int) (videoWidth * scale));
        int height = Math.max(1, (int) (videoHeight * scale));
        FrameLayout.LayoutParams params = (FrameLayout.LayoutParams) video.getLayoutParams();
        if (params.width != width || params.height != height) {
            params.width = width;
            params.height = height;
            params.gravity = Gravity.CENTER;
            video.setLayoutParams(params);
        }
    }

    private final class Session implements Runnable {
        private final Object socketsLock = new Object();
        private final Surface surface;
        private volatile boolean stopped;
        private ServerSocket server;
        private Socket connection;
        private LanguageServer languageServer;
        private UsbNetwork.Target bindTarget;

        Session(Surface surface) {
            this.surface = surface;
        }

        @Override
        public void run() {
            try {
                while (!stopped) {
                    bindTarget = UsbNetwork.discover();
                    listen(bindTarget);
                }
            } catch (IOException | RuntimeException e) {
                if (!stopped) {
                    Log.w(TAG, "Listener failed", e);
                    showStatus(R.string.error_title,
                            R.string.listener_error, bindTarget, true, e, false);
                }
            } finally {
                synchronized (socketsLock) {
                    closeQuietly(connection);
                    closeQuietly(server);
                    closeQuietly(languageServer);
                    connection = null;
                    server = null;
                    languageServer = null;
                }
            }
        }

        private void listen(UsbNetwork.Target target) throws IOException {
            ServerSocket listener = new ServerSocket();
            synchronized (socketsLock) {
                if (stopped) {
                    closeQuietly(listener);
                    return;
                }
                server = listener;
            }
            try {
                listener.setReuseAddress(true);
                listener.setSoTimeout(1500);
                listener.bind(new InetSocketAddress(target.address, VideoProtocol.PORT), 1);
                startLanguageServer(target);
                showStatus(target.usb ? R.string.usb_ready_title : R.string.adb_wait_title,
                        target.usb ? R.string.usb_ready_message : R.string.adb_wait_message,
                        target, true, null, true);
                while (!stopped) {
                    Socket accepted;
                    try {
                        accepted = listener.accept();
                    } catch (SocketTimeoutException waiting) {
                        // Re-enumerate only while idle, preserving smooth active playback.
                        if (!target.sameBinding(UsbNetwork.discover())) {
                            return;
                        }
                        continue;
                    }
                    if (!target.allowsPeer(accepted.getInetAddress())) {
                        Log.w(TAG, "Rejected peer outside the selected USB subnet");
                        closeQuietly(accepted);
                        continue;
                    }
                    synchronized (socketsLock) {
                        if (stopped) {
                            closeQuietly(accepted);
                            return;
                        }
                        connection = accepted;
                    }
                    serveConnection(accepted, target);
                    closeConnection();
                    if (!stopped && !target.sameBinding(UsbNetwork.discover())) {
                        return;
                    }
                }
            } finally {
                synchronized (socketsLock) {
                    closeQuietly(listener);
                    if (server == listener) {
                        server = null;
                    }
                    closeQuietly(languageServer);
                    languageServer = null;
                }
            }
        }

        private void startLanguageServer(UsbNetwork.Target target) {
            synchronized (socketsLock) {
                if (stopped) return;
                try {
                    languageServer = new LanguageServer(target, language -> {
                        CountDownLatch completed = new CountDownLatch(1);
                        AtomicBoolean applied = new AtomicBoolean(false);
                        runOnUiThread(() -> {
                            try {
                                if (session == this && !stopped) {
                                    applyLanguage(language);
                                    applied.set(true);
                                }
                            } finally {
                                completed.countDown();
                            }
                        });
                        try {
                            return completed.await(2, TimeUnit.SECONDS) && applied.get();
                        } catch (InterruptedException interrupted) {
                            Thread.currentThread().interrupt();
                            return false;
                        }
                    });
                    new Thread(languageServer, "panelyra-language").start();
                } catch (IOException | RuntimeException failure) {
                    Log.w(TAG, "Optional language listener unavailable", failure);
                }
            }
        }

        private void serveConnection(Socket accepted, UsbNetwork.Target target) {
            DecoderPump decoder = null;
            try {
                accepted.setTcpNoDelay(true);
                accepted.setReceiveBufferSize(256 * 1024);
                // A stalled/partial sender cannot leave an old frame on screen indefinitely.
                accepted.setSoTimeout(10_000);
                showStatus(R.string.connected_title, R.string.connected_message, target, false, null, false);
                VideoProtocol.Reader reader = new VideoProtocol.Reader(
                        new BufferedInputStream(accepted.getInputStream(), 64 * 1024));
                VideoProtocol.StreamInfo stream = reader.readHeader();
                VideoProtocol.Frame first = reader.readFrame();
                VideoProtocol.CodecData data = VideoProtocol.parseInitialFrame(first);
                runOnUiThread(() -> {
                    if (session == this && !stopped) {
                        videoWidth = stream.width;
                        videoHeight = stream.height;
                        fitVideo();
                    }
                });
                decoder = new DecoderPump(stream, data, surface, new DecoderPump.Listener() {
                    @Override
                    public boolean isStopped() {
                        return stopped;
                    }

                    @Override
                    public void onFirstFrame() {
                        runOnUiThread(() -> {
                            if (session == Session.this && !stopped && !accepted.isClosed()) {
                                statusPanel.setVisibility(View.GONE);
                            }
                        });
                    }

                    @Override
                    public void onDecoderFailure() {
                        closeQuietly(accepted); // Unblock the network reader.
                    }
                });
                decoder.queue(first);
                while (!stopped) {
                    decoder.queue(reader.readFrame());
                }
            } catch (IOException | RuntimeException e) {
                if (!stopped) {
                    Throwable reason = decoder != null && decoder.failure() != null ? decoder.failure() : e;
                    Log.w(TAG, "Connection ended", reason);
                    showStatus(R.string.stopped_title,
                            R.string.stopped_message, target, true, reason, false);
                }
            } finally {
                closeQuietly(accepted);
                if (decoder != null) {
                    decoder.close();
                }
            }
        }

        private void showStatus(int title, int message, UsbNetwork.Target target, boolean canRefresh,
                                Throwable error, boolean discoveryDiagnostic) {
            runOnUiThread(() -> {
                if (session == this && !stopped) {
                    updateStatus(title, message, target, canRefresh, error, discoveryDiagnostic);
                }
            });
        }

        void stop() {
            stopped = true;
            synchronized (socketsLock) {
                closeQuietly(connection);
                closeQuietly(server);
                closeQuietly(languageServer);
                connection = null;
                server = null;
                languageServer = null;
            }
        }

        private void closeConnection() {
            synchronized (socketsLock) {
                closeQuietly(connection);
                connection = null;
            }
        }
    }

    private String readableError(Throwable e) {
        if (e instanceof java.io.EOFException) {
            return localized(R.string.error_eof);
        }
        if (e instanceof java.net.SocketTimeoutException) {
            return localized(R.string.error_timeout);
        }
        return localized(R.string.diagnostic, e.getMessage() != null
                ? e.getMessage() : e.getClass().getSimpleName());
    }

    private static void closeQuietly(Closeable closeable) {
        if (closeable != null) {
            try {
                closeable.close();
            } catch (IOException ignored) {
                // Idempotent shutdown from either the UI thread or connection worker.
            }
        }
    }
}
