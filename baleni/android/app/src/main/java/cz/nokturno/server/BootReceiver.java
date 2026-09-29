package cz.nokturno.server;

import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;

/** Po zapnutí zařízení doplněk spustí sám, jako Luna. */
public class BootReceiver extends BroadcastReceiver {
    @Override
    public void onReceive(Context ctx, Intent intent) {
        if (Intent.ACTION_BOOT_COMPLETED.equals(intent.getAction())) NokturnoService.spust(ctx);
    }
}
