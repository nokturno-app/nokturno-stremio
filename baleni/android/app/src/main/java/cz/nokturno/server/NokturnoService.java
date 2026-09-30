package cz.nokturno.server;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
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
        Intent otevrit = new Intent(this, MainActivity.class);
        PendingIntent pi = PendingIntent.getActivity(this, 0, otevrit,
                PendingIntent.FLAG_UPDATE_CURRENT | (Build.VERSION.SDK_INT >= 23 ? PendingIntent.FLAG_IMMUTABLE : 0));
        Notification n = b.setContentTitle("Nokturno běží na pozadí")
                .setContentText("Služba pro Stremio a Nuvio na portu 7140, po restartu se spustí sama")
                .setContentIntent(pi)
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
                    // když doplněk skončí (chyba, vyjímka), za pár sekund se spustí znovu
                    while (true) {
                        try {
                            if (!Python.isStarted()) Python.start(new AndroidPlatform(getApplicationContext()));
                            Python.getInstance().getModule("nokturno_apk.start").callAttr("run", data);
                        } catch (Throwable t) {
                            Log.e("Nokturno", "doplněk skončil", t);
                        }
                        try {
                            Thread.sleep(5000);
                        } catch (InterruptedException e) {
                            return;
                        }
                    }
                }, "nokturno");
                vlakno.start();
            }
        }
        return START_STICKY;
    }

    /** Smazání aplikace z posledních aplikací službu nezastaví; kdyby ji systém přesto ukončil, naplánuje se restart. */
    @Override
    public void onTaskRemoved(Intent rootIntent) {
        Intent i = new Intent(getApplicationContext(), NokturnoService.class);
        PendingIntent pi = PendingIntent.getService(this, 1, i,
                PendingIntent.FLAG_ONE_SHOT | (Build.VERSION.SDK_INT >= 23 ? PendingIntent.FLAG_IMMUTABLE : 0));
        android.app.AlarmManager am = (android.app.AlarmManager) getSystemService(ALARM_SERVICE);
        if (am != null) am.set(android.app.AlarmManager.ELAPSED_REALTIME, android.os.SystemClock.elapsedRealtime() + 2000, pi);
        super.onTaskRemoved(rootIntent);
    }

    @Override
    public IBinder onBind(Intent intent) {
        return null;
    }
}
