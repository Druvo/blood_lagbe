"""The pure-Python .po compiler (no GNU gettext on the build machine)."""
import gettext
import tempfile
from pathlib import Path

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase, override_settings

from core.management.commands.compile_po import (
    POSyntaxError, build_mo, compile_file, parse_entries, parse_po,
)

SAMPLE = r'''# A comment
msgid ""
msgstr ""
"Content-Type: text/plain; charset=UTF-8\n"
"Plural-Forms: nplurals=2; plural=(n > 1);\n"

#: some/file.html
msgid "Hello"
msgstr "Halo"

msgid "Say \"quoted\" and a \\ backslash"
msgstr "Bol \"uddhrit\" ebong \\ "

msgid "Tab\there"
msgstr "Tab\tekhane"

msgid ""
"A long sentence that is split "
"over several lines."
msgstr ""
"Ekti lomba bakyo ja "
"onek line e bhaga."

msgid "Line one\nLine two"
msgstr "Prothom\nDwitiyo"

msgid "%(n)s item"
msgid_plural "%(n)s items"
msgstr[0] "%(n)sTi jinis"
msgstr[1] "%(n)sTi jinis (plural)"

msgctxt "menu"
msgid "Open"
msgstr "Kholo (menu)"

msgctxt "door"
msgid "Open"
msgstr "Kholo (door)"

#, fuzzy
msgid "Guessed"
msgstr "Anumaan"

msgid "Not translated yet"
msgstr ""

#, python-format, fuzzy
msgid "Flag combo %(x)s"
msgstr "Anumaan %(x)s"

msgid "Bangla text"
msgstr "বাংলা লেখা"
'''


class CompilePoTests(SimpleTestCase):
    def compile_sample(self, text=SAMPLE):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        po = Path(tmp.name) / 'django.po'
        po.write_text(text, encoding='utf-8')
        mo = compile_file(po)
        with open(mo, 'rb') as handle:
            return gettext.GNUTranslations(handle)

    def test_plain_message(self):
        catalog = self.compile_sample()
        self.assertEqual(catalog.gettext('Hello'), 'Halo')

    def test_unicode_message(self):
        catalog = self.compile_sample()
        self.assertEqual(catalog.gettext('Bangla text'), 'বাংলা লেখা')

    def test_escaped_quotes_backslash_and_tab(self):
        catalog = self.compile_sample()
        self.assertEqual(
            catalog.gettext('Say "quoted" and a \\ backslash'),
            'Bol "uddhrit" ebong \\ ',
        )
        self.assertEqual(catalog.gettext('Tab\there'), 'Tab\tekhane')

    def test_multiline_strings_are_joined(self):
        catalog = self.compile_sample()
        self.assertEqual(
            catalog.gettext(
                'A long sentence that is split over several lines.'),
            'Ekti lomba bakyo ja onek line e bhaga.',
        )

    def test_newline_escape(self):
        catalog = self.compile_sample()
        self.assertEqual(catalog.gettext('Line one\nLine two'), 'Prothom\nDwitiyo')

    def test_plural_forms(self):
        catalog = self.compile_sample()
        self.assertEqual(
            catalog.ngettext('%(n)s item', '%(n)s items', 1), '%(n)sTi jinis')
        self.assertEqual(
            catalog.ngettext('%(n)s item', '%(n)s items', 5),
            '%(n)sTi jinis (plural)')

    def test_message_context(self):
        catalog = self.compile_sample()
        self.assertEqual(catalog.pgettext('menu', 'Open'), 'Kholo (menu)')
        self.assertEqual(catalog.pgettext('door', 'Open'), 'Kholo (door)')
        # Without a context the message is not found: returned unchanged.
        self.assertEqual(catalog.gettext('Open'), 'Open')

    def test_fuzzy_entries_are_skipped(self):
        catalog = self.compile_sample()
        self.assertEqual(catalog.gettext('Guessed'), 'Guessed')
        self.assertEqual(catalog.gettext('Flag combo %(x)s'), 'Flag combo %(x)s')

    def test_empty_translation_falls_back_to_the_msgid(self):
        catalog = self.compile_sample()
        self.assertEqual(catalog.gettext('Not translated yet'), 'Not translated yet')

    def test_header_is_kept(self):
        catalog = self.compile_sample()
        self.assertEqual(catalog.info()['content-type'], 'text/plain; charset=UTF-8')
        self.assertEqual(catalog.plural(1), 0)
        self.assertEqual(catalog.plural(2), 1)

    def test_fuzzy_flag_is_read_but_only_for_the_next_entry(self):
        entries = {e.msgid: e for e in parse_entries(SAMPLE)}
        self.assertTrue(entries['Guessed'].fuzzy)
        self.assertTrue(entries['Flag combo %(x)s'].fuzzy)
        self.assertFalse(entries['Hello'].fuzzy)
        self.assertFalse(entries['Not translated yet'].fuzzy)
        self.assertFalse(entries['Bangla text'].fuzzy)

    def test_parse_po_keys(self):
        messages = parse_po(SAMPLE)
        self.assertIn('menu\x04Open', messages)
        self.assertIn('%(n)s item\x00%(n)s items', messages)
        self.assertNotIn('Guessed', messages)

    def test_mo_file_is_sorted_and_deterministic(self):
        messages = parse_po(SAMPLE)
        self.assertEqual(build_mo(messages), build_mo(dict(reversed(list(messages.items())))))

    def test_syntax_errors_are_reported(self):
        with self.assertRaises(POSyntaxError):
            parse_po('msgid "x"\nmsgstr y\n')
        with self.assertRaises(POSyntaxError):
            parse_po('msgstr "orphan"\n')
        with self.assertRaises(POSyntaxError):
            parse_po('msgid "no translation line"\n')

    def test_command_compiles_and_checks(self):
        with tempfile.TemporaryDirectory() as tmp:
            lang_dir = Path(tmp) / 'xx' / 'LC_MESSAGES'
            lang_dir.mkdir(parents=True)
            (lang_dir / 'django.po').write_text(SAMPLE, encoding='utf-8')
            with override_settings(LOCALE_PATHS=[tmp]):
                with self.assertRaises(CommandError):
                    call_command('compile_po', '--check')  # no .mo yet
                call_command('compile_po')
                self.assertTrue((lang_dir / 'django.mo').exists())
                call_command('compile_po', '--check')  # now up to date
                (lang_dir / 'django.po').write_text(
                    SAMPLE + '\nmsgid "More"\nmsgstr "Aro"\n', encoding='utf-8')
                with self.assertRaises(CommandError):
                    call_command('compile_po', '--check')  # stale

    def test_committed_bangla_catalogue_is_compiled(self):
        """The .mo shipped in the repo must match the .po (run compile_po)."""
        call_command('compile_po', '--check')

    def test_project_locale_path_is_configured(self):
        self.assertEqual(
            [Path(p) for p in settings.LOCALE_PATHS],
            [Path(settings.BASE_DIR) / 'locale'],
        )
