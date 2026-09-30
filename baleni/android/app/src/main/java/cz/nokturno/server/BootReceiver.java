package cz.nokturno.server;

import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;

/** Po zapnutí zařízení a po aktualizaci aplikace doplněk spustí sám, jako Luna. */
public class BootReceiver extends BroadcastReceiver {
    @Override
    public void onReceive(Context ctx, Intent intent) {
        String a = intent.getAction();
        if (Intent.ACTION_BOOT_COMPLETED.equals(a) || Intent.ACTION_MY_PACKAGE_REPLACED.equals(a)
                || "android.intent.action.QUICKBOOT_POWERON".equals(a)) {
            try {
                NokturnoService.spust(ctx);
            } catch (RuntimeException e) {   // Android 12+ smí službu v popředí spustit jen výjimečně
                android.util.Log.e("Nokturno", "start po zapnutí selhal", e);
            }
        }
    }
}
