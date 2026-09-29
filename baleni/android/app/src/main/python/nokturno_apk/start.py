"""Vstup z Javy (NokturnoService): doplněk v tomto procesu, přes zavaděč."""
import logging
import os


def run(data):
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    tady = os.path.dirname(os.path.abspath(__file__))
    with open(os.path.join(tady, "version.txt"), encoding="utf-8") as f:
        os.environ["NOKTURNO_VESTAVENA_VERZE"] = f.read().strip()
    # vestavěný balík `nokturno` leží vedle tohohle balíku
    os.environ["NOKTURNO_VESTAVENY"] = os.path.dirname(tady)
    from nokturno_apk import zavadec
    os.makedirs(data, exist_ok=True)
    return zavadec.spust_v_procesu(data)
