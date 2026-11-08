import os
import tempfile
import unittest

from engine.i18n import (
    DEFAULT_LANGUAGE,
    SUPPORTED_LANGUAGES,
    format_bytes_i18n,
    format_speed_i18n,
    get_current_language,
    init_language,
    set_current_language,
    subscribe_language_changed,
    t,
    unsubscribe_language_changed,
)


class BootForgeI18nTests(unittest.TestCase):
    def setUp(self):
        set_current_language("fr", persist=False)

    def tearDown(self):
        set_current_language("fr", persist=False)

    def test_supported_languages(self):
        self.assertIn("fr", SUPPORTED_LANGUAGES)
        self.assertIn("en", SUPPORTED_LANGUAGES)
        self.assertEqual(DEFAULT_LANGUAGE, "fr")

    def test_translation_fr_and_en(self):
        self.assertEqual(t("nav_direct_simple", lang="fr"), "Créer une clé")
        self.assertEqual(t("nav_direct_simple", lang="en"), "Create USB")

        self.assertEqual(t("mode_advanced", lang="fr"), "Avancé")
        self.assertEqual(t("mode_advanced", lang="en"), "Advanced")

        self.assertEqual(t("toolbox_badblocks_ro", lang="fr"), "Mode lecture seule (sécurisé, ne modifie pas les données)")
        self.assertEqual(t("toolbox_badblocks_ro", lang="en"), "Read-only mode (safe, does not modify data)")

    def test_interpolation(self):
        msg_fr = t("toolbox_speed_res", lang="fr", speed="45.0 Mo/s")
        self.assertEqual(msg_fr, "Vitesse de lecture moyenne : 45.0 Mo/s")

        msg_en = t("toolbox_speed_res", lang="en", speed="45.0 MB/s")
        self.assertEqual(msg_en, "Average read speed: 45.0 MB/s")

    def test_fallback_on_unknown_key(self):
        self.assertEqual(t("unknown_key_xyz"), "unknown_key_xyz")

    def test_localized_byte_formatting(self):
        size_5g = 5 * 1024**3
        self.assertEqual(format_bytes_i18n(size_5g, lang="fr"), "5.00 Go")
        self.assertEqual(format_bytes_i18n(size_5g, lang="en"), "5.00 GB")
        self.assertEqual(format_bytes_i18n(0, lang="fr"), "0 o")
        self.assertEqual(format_bytes_i18n(0, lang="en"), "0 B")

    def test_localized_speed_formatting(self):
        speed = 25.5 * 1024**2
        self.assertEqual(format_speed_i18n(speed, lang="fr"), "25.5 Mo/s")
        self.assertEqual(format_speed_i18n(speed, lang="en"), "25.5 MB/s")
        self.assertEqual(format_speed_i18n(None, lang="fr"), "")

    def test_subscribe_language_changed(self):
        events = []

        def on_change(lang: str):
            events.append(lang)

        subscribe_language_changed(on_change)
        try:
            set_current_language("en", persist=False)
            self.assertEqual(get_current_language(), "en")
            self.assertEqual(events, ["en"])

            set_current_language("fr", persist=False)
            self.assertEqual(get_current_language(), "fr")
            self.assertEqual(events, ["en", "fr"])
        finally:
            unsubscribe_language_changed(on_change)


if __name__ == "__main__":
    unittest.main()
