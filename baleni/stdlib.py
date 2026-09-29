"""Argumenty --hidden-import pro celou standardní knihovnu.

Zavaděč načítá doplněk až za běhu (i staženou novější verzi), takže PyInstaller
z importů nepozná, co z knihovny bude potřeba. Přibalí se tedy celá, bez GUI a testů."""
import importlib
import pkgutil
import sys

VYNECHAT = {"tkinter", "turtle", "turtledemo", "idlelib", "test", "lib2to3", "ensurepip", "venv",
            "pydoc_data", "distutils", "antigravity", "this", "__hello__", "__phello__", "_tkinter"}


def moduly():
    for jmeno in sorted(sys.stdlib_module_names):
        if jmeno in VYNECHAT or jmeno.startswith("_test"):
            continue
        try:
            mod = importlib.import_module(jmeno)
        except Exception:  # noqa: BLE001 – modul jiné platformy
            continue
        yield jmeno
        for info in pkgutil.walk_packages(getattr(mod, "__path__", []) or [], jmeno + "."):
            if any(cast in VYNECHAT or cast in ("tests", "test") for cast in info.name.split(".")):
                continue
            yield info.name


if __name__ == "__main__":
    print(" ".join(f"--hidden-import={m}" for m in moduly()))
