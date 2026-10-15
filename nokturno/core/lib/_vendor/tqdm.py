"""Náhrada tqdm pro pysmb (ukazatel průběhu nepoužíváme)."""


class tqdm:  # noqa: N801 – jméno z pysmb
    def __init__(self, *args, **kwargs):
        pass

    def update(self, n=1):
        pass

    def close(self):
        pass
