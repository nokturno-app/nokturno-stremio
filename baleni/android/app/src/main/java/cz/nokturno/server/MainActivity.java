package cz.nokturno.server;

import android.app.Activity;
import android.content.ActivityNotFoundException;
import android.content.ClipData;
import android.content.ClipboardManager;
import android.content.Intent;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.os.PowerManager;
import android.provider.Settings;
import android.webkit.JavascriptInterface;
import android.webkit.WebResourceRequest;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.Toast;

import java.net.HttpURLConnection;
import java.net.Inet4Address;
import java.net.InetAddress;
import java.net.NetworkInterface;
import java.net.URL;
import java.util.Collections;

/** Spustí službu a ukáže adresy (assets/index.html). Nastavení je ve formuláři /configure. */
public class MainActivity extends Activity {
    private WebView web;

    @Override
    protected void onCreate(Bundle b) {
        super.onCreate(b);
        if (Build.VERSION.SDK_INT >= 33) requestPermissions(new String[]{"android.permission.POST_NOTIFICATIONS"}, 1);
        NokturnoService.spust(this);
        pozadatOBaterii(false);
        web = new WebView(this);
        web.setBackgroundColor(0xFF0D0C1D);
        web.getSettings().setJavaScriptEnabled(true);
        web.addJavascriptInterface(new Most(), "Nokturno");
        web.setWebViewClient(new WebViewClient() {
            @Override
            public boolean shouldOverrideUrlLoading(WebView v, WebResourceRequest r) {
                String u = r.getUrl().toString();
                // stremio:// a nuvio:// z formuláře otevřenému přímo v aplikaci patří aplikacím
                if (u.startsWith("http") || u.startsWith("file:")) return false;
                return !otevriVen(u);
            }
        });
        String ip = mistniIp();
        String verze = "";
        try {
            verze = getPackageManager().getPackageInfo(getPackageName(), 0).versionName;
        } catch (Exception ignored) {
        }
        web.loadUrl("file:///android_asset/index.html?ip=" + (ip == null ? "" : ip) + "&v=" + Uri.encode(verze));
        setContentView(web);
    }

    @Override
    public void onBackPressed() {
        if (web != null && web.canGoBack()) web.goBack(); else super.onBackPressed();
    }

    private boolean bezOmezeniBaterie() {
        if (Build.VERSION.SDK_INT < 23) return true;
        PowerManager pm = (PowerManager) getSystemService(POWER_SERVICE);
        return pm == null || pm.isIgnoringBatteryOptimizations(getPackageName());
    }

    /** Bez výjimky z úspor baterie Android službu časem uspí; po vysvětlení v aplikaci (tlačítko) nebo hned při prvním spuštění. */
    private void pozadatOBaterii(boolean vzdy) {
        if (bezOmezeniBaterie()) return;
        android.content.SharedPreferences sp = getSharedPreferences("nokturno", MODE_PRIVATE);
        if (!vzdy && sp.getBoolean("baterie_zeptano", false)) return;
        sp.edit().putBoolean("baterie_zeptano", true).apply();
        try {
            startActivity(new Intent(Settings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS, Uri.parse("package:" + getPackageName())));
        } catch (Exception e) {
            try {
                startActivity(new Intent(Settings.ACTION_IGNORE_BATTERY_OPTIMIZATION_SETTINGS));
            } catch (Exception ignored) {
            }
        }
    }

    private boolean otevriVen(String url) {
        try {
            startActivity(new Intent(Intent.ACTION_VIEW, Uri.parse(url)).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK));
            return true;
        } catch (ActivityNotFoundException e) {
            return false;
        }
    }

    /** Volá stránka: odkazy, schránka a stav služby. */
    private class Most {
        @JavascriptInterface
        public void otevri(String url) {
            // TV box bez prohlížeče: formulář se otevře přímo tady
            if (!otevriVen(url)) runOnUiThread(() -> web.loadUrl(url));
        }

        @JavascriptInterface
        public void kopiruj(String text) {
            ClipboardManager cm = (ClipboardManager) getSystemService(CLIPBOARD_SERVICE);
            cm.setPrimaryClip(ClipData.newPlainText("Nokturno", text));
            runOnUiThread(() -> Toast.makeText(MainActivity.this, "Zkopírováno", Toast.LENGTH_SHORT).show());
        }

        @JavascriptInterface
        public boolean baterieVporadku() {
            return bezOmezeniBaterie();
        }

        @JavascriptInterface
        public void povolBaterii() {
            runOnUiThread(() -> pozadatOBaterii(true));
        }

        @JavascriptInterface
        public boolean bezi() {   // volá se mimo hlavní vlákno, síť tu smí
            try {
                HttpURLConnection c = (HttpURLConnection) new URL("http://127.0.0.1:7140/health").openConnection();
                c.setConnectTimeout(1500);
                c.setReadTimeout(1500);
                return c.getResponseCode() == 200;
            } catch (Exception e) {
                return false;
            }
        }
    }

    private static String mistniIp() {
        try {
            for (NetworkInterface ni : Collections.list(NetworkInterface.getNetworkInterfaces())) {
                if (ni.isLoopback() || !ni.isUp()) continue;
                for (InetAddress a : Collections.list(ni.getInetAddresses())) {
                    if (a instanceof Inet4Address && a.isSiteLocalAddress()) return a.getHostAddress();
                }
            }
        } catch (Exception ignored) {
        }
        return null;
    }
}
