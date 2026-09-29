package cz.nokturno.server;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.Service;
import android.content.Context;
import android.content.Intent;
import android.content.pm.ServiceInfo;
import android.os.Build;
import android.os.IBinder;
import android.util.Log;

import com.chaquo.python.Python;
import com.chaquo.python.android.AndroidPlatform;

/** Doplněk běží v popředí, aby ho Android na pozadí nezabil (stejně jako Luna). */
public class NokturnoService extends Service {
    private static final String KANAL = "nokturno";
    private static Thread vlakno;

    public static void spust(Context ctx) {
        Intent i = new Intent(ctx, NokturnoService.class);
        if (Build.VERSION.SDK_INT >= 26) ctx.startForegroundService(i); else ctx.startService(i);
    }

    @Override
    public int onStartCommand(Intent intent, int flags, int startId) {
        NotificationManager nm = (NotificationManager) getSystemService(NOTIFICATION_SERVICE);
        Notification.Builder b;
        if (Build.VERSION.SDK_INT >= 26) {
            nm.createNotificationChannel(new NotificationChannel(KANAL, "Nokturno", NotificationManager.IMPORTANCE_LOW));
            b = new Notification.Builder(this, KANAL);
        } else {
            b = new Notification.Builder(this);
        }
        Notification n = b.setContentTitle("Nokturno běží")
                .setContentText("Doplněk pro Stremio a Nuvio na portu 7140")
                .setSmallIcon(R.mipmap.ic_launcher)
                .setOngoing(true)
                .build();
        if (Build.VERSION.SDK_INT >= 34) {
            startForeground(1, n, ServiceInfo.FOREGROUND_SERVICE_TYPE_SPECIAL_USE);
        } else {
            startForeground(1, n);
        }
        synchronized (NokturnoService.class) {
            if (vlakno == null || !vlakno.isAlive()) {
                final String data = getFilesDir().getAbsolutePath() + "/nokturno";
                vlakno = new Thread(() -> {
                    try {
                        if (!Python.isStarted()) Python.start(new AndroidPlatform(getApplicationContext()));
                        Python.getInstance().getModule("nokturno_apk.start").callAttr("run", data);
                    } catch (Throwable t) {
                        Log.e("Nokturno", "doplněk skončil", t);
                    }
                }, "nokturno");
                vlakno.start();
            }
        }
        return START_STICKY;
    }

    @Override
    public IBinder onBind(Intent intent) {
        return null;
    }
}
