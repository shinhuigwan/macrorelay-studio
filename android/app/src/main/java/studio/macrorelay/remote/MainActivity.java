package studio.macrorelay.remote;

import android.app.Activity;
import android.app.AlertDialog;
import android.graphics.Color;
import android.os.Bundle;
import android.text.InputType;
import android.view.Gravity;
import android.view.Menu;
import android.view.MenuItem;
import android.view.ViewGroup;
import android.webkit.WebChromeClient;
import android.webkit.WebResourceError;
import android.webkit.WebResourceRequest;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.EditText;
import android.widget.FrameLayout;
import android.widget.LinearLayout;
import android.widget.ProgressBar;
import android.widget.TextView;

import java.net.URI;

public final class MainActivity extends Activity {
    private static final String PREFS = "macrorelay_remote";
    private static final String SERVER_URL = "server_url";
    private WebView webView;
    private ProgressBar progress;
    private String currentUrl = "";

    @Override
    protected void onCreate(Bundle state) {
        super.onCreate(state);
        setTitle("MacroRelay Remote");
        buildView();
        currentUrl = getPreferences(MODE_PRIVATE).getString(SERVER_URL, "");
        if (currentUrl.isEmpty() || isPrivateNetworkUrl(currentUrl)) {
            String bundled = normalizeUrl(BuildConfig.DEFAULT_RELAY_URL);
            if (bundled != null) {
                currentUrl = bundled;
                getPreferences(MODE_PRIVATE).edit().putString(SERVER_URL, currentUrl).apply();
            }
        }
        if (currentUrl.isEmpty()) {
            webView.post(() -> showServerDialog(false));
        } else {
            loadServer();
        }
    }

    @SuppressWarnings("SetJavaScriptEnabled")
    private void buildView() {
        FrameLayout root = new FrameLayout(this);
        webView = new WebView(this);
        webView.setBackgroundColor(Color.rgb(12, 15, 23));
        webView.getSettings().setJavaScriptEnabled(true);
        webView.getSettings().setDomStorageEnabled(true);
        webView.getSettings().setDatabaseEnabled(true);
        webView.getSettings().setLoadWithOverviewMode(true);
        webView.getSettings().setUseWideViewPort(true);
        webView.setWebChromeClient(new WebChromeClient());
        webView.setWebViewClient(new WebViewClient() {
            @Override
            public void onPageFinished(WebView view, String url) {
                progress.setVisibility(ProgressBar.GONE);
            }

            @Override
            public void onReceivedError(WebView view, WebResourceRequest request, WebResourceError error) {
                if (request.isForMainFrame()) {
                    progress.setVisibility(ProgressBar.GONE);
                    showConnectionError();
                }
            }
        });
        root.addView(webView, new FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT));
        progress = new ProgressBar(this);
        FrameLayout.LayoutParams progressParams = new FrameLayout.LayoutParams(64, 64);
        progressParams.gravity = Gravity.CENTER;
        root.addView(progress, progressParams);
        setContentView(root);
    }

    private void loadServer() {
        progress.setVisibility(ProgressBar.VISIBLE);
        webView.loadUrl(currentUrl);
    }

    private void showConnectionError() {
        String escaped = currentUrl.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;");
        webView.loadDataWithBaseURL(null,
                "<html><meta name='viewport' content='width=device-width'><body style='background:#0c0f17;color:#eef2ff;font-family:sans-serif;padding:28px'>"
                        + "<h2>PC에 연결할 수 없습니다</h2><p>Studio가 실행 중이고 모바일 원격이 켜져 있는지 확인하세요.</p>"
                        + "<p style='color:#9aa6c3'>" + escaped + "</p><p>우측 상단 설정에서 주소를 다시 입력할 수 있습니다.</p></body></html>",
                "text/html", "UTF-8", null);
    }

    private void showServerDialog(boolean cancelable) {
        LinearLayout panel = new LinearLayout(this);
        panel.setOrientation(LinearLayout.VERTICAL);
        int padding = (int) (20 * getResources().getDisplayMetrics().density);
        panel.setPadding(padding, 0, padding, 0);
        TextView guide = new TextView(this);
        guide.setText("기본 인터넷 연결이 실패한 경우에만 Studio 설정 > 모바일 원격의 연결 주소를 입력하세요.");
        guide.setTextSize(15);
        panel.addView(guide);
        EditText input = new EditText(this);
        input.setSingleLine(true);
        input.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_URI);
        input.setHint("https://relay.example.workers.dev");
        input.setText(currentUrl);
        panel.addView(input, new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT));
        AlertDialog dialog = new AlertDialog.Builder(this)
                .setTitle("PC 연결 주소")
                .setView(panel)
                .setCancelable(cancelable)
                .setNegativeButton(cancelable ? "취소" : "종료", (d, which) -> {
                    if (!cancelable) finish();
                })
                .setPositiveButton("연결", null)
                .create();
        dialog.setOnShowListener(unused -> dialog.getButton(AlertDialog.BUTTON_POSITIVE).setOnClickListener(v -> {
            String normalized = normalizeUrl(input.getText().toString());
            if (normalized == null) {
                input.setError("http:// 또는 https://로 시작하는 올바른 주소를 입력하세요.");
                return;
            }
            currentUrl = normalized;
            getPreferences(MODE_PRIVATE).edit().putString(SERVER_URL, currentUrl).apply();
            dialog.dismiss();
            loadServer();
        }));
        dialog.show();
    }

    private String normalizeUrl(String value) {
        String candidate = value == null ? "" : value.trim();
        if (!candidate.matches("^[a-zA-Z][a-zA-Z0-9+.-]*://.*")) {
            candidate = "http://" + candidate;
        }
        try {
            URI parsed = URI.create(candidate);
            String scheme = parsed.getScheme();
            if (!("http".equalsIgnoreCase(scheme) || "https".equalsIgnoreCase(scheme)) || parsed.getHost() == null) {
                return null;
            }
            return candidate.endsWith("/") ? candidate : candidate + "/";
        } catch (IllegalArgumentException error) {
            return null;
        }
    }

    private boolean isPrivateNetworkUrl(String value) {
        try {
            String host = URI.create(value).getHost();
            if (host == null) return true;
            host = host.toLowerCase();
            if (host.equals("localhost") || host.equals("127.0.0.1") || host.startsWith("10.") || host.startsWith("192.168.")) {
                return true;
            }
            if (host.startsWith("172.")) {
                String[] parts = host.split("\\.");
                if (parts.length > 1) {
                    int second = Integer.parseInt(parts[1]);
                    return second >= 16 && second <= 31;
                }
            }
            return false;
        } catch (Exception ignored) {
            return true;
        }
    }

    @Override
    public boolean onCreateOptionsMenu(Menu menu) {
        menu.add("새로고침").setShowAsAction(MenuItem.SHOW_AS_ACTION_NEVER);
        menu.add("PC 주소 설정").setShowAsAction(MenuItem.SHOW_AS_ACTION_ALWAYS);
        return true;
    }

    @Override
    public boolean onOptionsItemSelected(MenuItem item) {
        if ("PC 주소 설정".contentEquals(item.getTitle())) {
            showServerDialog(true);
            return true;
        }
        if ("새로고침".contentEquals(item.getTitle())) {
            loadServer();
            return true;
        }
        return super.onOptionsItemSelected(item);
    }

    @Override
    public void onBackPressed() {
        if (webView.canGoBack()) webView.goBack(); else super.onBackPressed();
    }

    @Override
    protected void onDestroy() {
        if (webView != null) webView.destroy();
        super.onDestroy();
    }
}
