package cz.nokturno.server;

import android.app.Activity;
import android.app.DownloadManager;
import android.content.ActivityNotFoundException;
import android.content.ClipData;
import android.content.BroadcastReceiver;
import android.content.ClipboardManager;
import android.content.Context;
import android.content.IntentFilter;
import android.content.Intent;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.os.Environment;
import android.os.PowerManager;
import android.provider.Settings;
import android.webkit.JavascriptInterface;
import android.webkit.WebResourceRequest;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.Toast;

import java.io.File;
import java.net.HttpURLConnection;
import java.net.Inet4Address;
import java.net.InetAddress;
import java.net.NetworkInterface;
import java.net.URL;
import java.util.Collections;

/** Spustí službu a ukáže adresy (assets/index.html). Nastavení je ve formuláři /configure. */
public class MainActivity extends Activity {
    private WebView web;
    private long stahovani = -1;
    private final BroadcastReceiver dokonceno = new BroadcastReceiver() {
        @Override
        public void onReceive(Context c, Intent i) {
            long id = i.getLongExtra(DownloadManager.EXTRA_DOWNLOAD_ID, -2);
            if (id != stahovani) return;
            DownloadManager dm = (DownloadManager) getSystemService(DOWNLOAD_SERVICE);
            Uri apk = dm.getUriForDownloadedFile(id);
            if (apk == null) {
                Toast.makeText(MainActivity.this, "Stažení se nepovedlo", Toast.LENGTH_LONG).show();
                return;
            }
            Intent inst = new Intent(Intent.ACTION_VIEW)
                    .setDataAndType(apk, "application/vnd.android.package-archive")
                    .addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION | Intent.FLAG_ACTIVITY_NEW_TASK);
            try {
                startActivity(inst);
            } catch (Exception e) {
                Toast.makeText(MainActivity.this, "Instalace se nespustila, otevři stažený soubor v oznámení", Toast.LENGTH_LONG).show();
            }
        }
    };

    @Override
    protected void onCreate(Bundle b) {
        super.onCreate(b);
        if (Build.VERSION.SDK_INT >= 33) requestPermissions(new String[]{"android.permission.POST_NOTIFICATIONS"}, 1);
        NokturnoService.spust(this);
        pozadatOBaterii(false);
        IntentFilter f = new IntentFilter(DownloadManager.ACTION_DOWNLOAD_COMPLETE);
        if (Build.VERSION.SDK_INT >= 33) registerReceiver(dokonceno, f, Context.RECEIVER_EXPORTED); else registerReceiver(dokonceno, f);
        web = new WebView(this);
        web.setBackgroundColor(0xFF0D0C1D);
        web.getSettings().setJavaScriptEnabled(true);
        web.addJavascriptInterface(new Most(), "Nokturno");
        // odkaz na soubor .apk načtený přímo ve WebView (záloha, když se nepodaří prohlížeč)
        web.setDownloadListener((u, ua, cd, mime, len) -> stahniAInstaluj(u));
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

    @Override
    protected void onDestroy() {
        try {
            unregisterReceiver(dokonceno);
        } catch (Exception ignored) {
        }
        super.onDestroy();
    }

    /** Stáhne APK a po dokončení spustí instalaci; bez povolení „instalovat neznámé aplikace" ho nejdřív vyžádá. */
    private void stahniAInstaluj(String url) {
        if (Build.VERSION.SDK_INT >= 26 && !getPackageManager().canRequestPackageInstalls()) {
            Toast.makeText(this, "Povol Nokturnu instalovat aplikace a klepni na Stáhnout znovu", Toast.LENGTH_LONG).show();
            try {
                startActivity(new Intent(Settings.ACTION_MANAGE_UNKNOWN_APP_SOURCES, Uri.parse("package:" + getPackageName())));
            } catch (Exception ignored) {
            }
            return;
        }
        try {
            File stary = new File(getExternalFilesDir(Environment.DIRECTORY_DOWNLOADS), "nokturno-aktualizace.apk");
            if (stary.exists()) stary.delete();
            DownloadManager.Request r = new DownloadManager.Request(Uri.parse(url))
                    .setTitle("Nokturno – aktualizace")
                    .setNotificationVisibility(DownloadManager.Request.VISIBILITY_VISIBLE_NOTIFY_COMPLETED)
                    .setDestinationInExternalFilesDir(this, Environment.DIRECTORY_DOWNLOADS, "nokturno-aktualizace.apk");
            stahovani = ((DownloadManager) getSystemService(DOWNLOAD_SERVICE)).enqueue(r);
            Toast.makeText(this, "Stahuji aktualizaci…", Toast.LENGTH_LONG).show();
        } catch (Exception e) {
            Toast.makeText(this, "Stažení se nespustilo, otevírám odkaz v prohlížeči", Toast.LENGTH_LONG).show();
            otevriVen(url);
        }
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
        } catch (Exception e) {   // chybějící prohlížeč i zákaz systému; důvod se ukáže uživateli
            android.util.Log.e("Nokturno", "odkaz se neotevřel: " + url, e);
            Toast.makeText(this, "Odkaz se neotevřel (" + e.getClass().getSimpleName() + ")", Toast.LENGTH_LONG).show();
            return false;
        }
    }

    /** Volá stránka: odkazy, schránka a stav služby. */
    private class Most {
        @JavascriptInterface
        public void otevri(String url) {
            // z vlákna mostu se aktivita spouští na hlavním vlákně; TV box bez prohlížeče: formulář se otevře přímo tady
            runOnUiThread(() -> {
                if (otevriVen(url)) return;
                if (url.endsWith(".apk")) {
                    stahniAInstaluj(url);
                } else {
                    ClipboardManager cm = (ClipboardManager) getSystemService(CLIPBOARD_SERVICE);
                    cm.setPrimaryClip(ClipData.newPlainText("Nokturno", url));
                    Toast.makeText(MainActivity.this, "Odkaz je ve schránce, vlož ho do prohlížeče", Toast.LENGTH_LONG).show();
                    web.loadUrl(url);
                }
            });
        }

        @JavascriptInterface
        public void kopiruj(String text) {
            ClipboardManager cm = (ClipboardManager) getSystemService(CLIPBOARD_SERVICE);
            cm.setPrimaryClip(ClipData.newPlainText("Nokturno", text));
            runOnUiThread(() -> Toast.makeText(MainActivity.this, "Zkopírováno", Toast.LENGTH_SHORT).show());
        }

        @JavascriptInterface
        public void aktualizuj(String url) {
            runOnUiThread(() -> stahniAInstaluj(url));
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
