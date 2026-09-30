import unittest

from nokturno import soukroma


class ZProxyAdmin(unittest.TestCase):
    def test_pravidla(self):
        cf = {"Cf-Connecting-IP": "1.2.3.4"}
        self.assertFalse(soukroma.z_proxy({}))
        self.assertTrue(soukroma.z_proxy(cf))
        self.assertTrue(soukroma.z_proxy({**cf, "Host": "kc9jri.nokturno.stream", "Cf-Access-Authenticated-User-Email": "a@b.c"}))
        self.assertTrue(soukroma.z_proxy({**cf, "Host": "admin.nokturno.stream"}))
        self.assertFalse(soukroma.z_proxy({**cf, "Host": "admin.nokturno.stream", "Cf-Access-Authenticated-User-Email": "a@b.c"}))
