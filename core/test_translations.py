"""Guard rails for the hand-maintained Bangla catalogue.

xgettext is not available here, so locale/bn/LC_MESSAGES/django.po is edited by
hand. These tests scan the templates and Python code for wrapped strings
(core/i18n_check.py) and fail when one has no Bangla translation, when a
translation is empty or still marked fuzzy, or when the compiled .mo is stale.
"""
import gettext
import re
from pathlib import Path

from django.test import SimpleTestCase

from core import i18n_check
from core.management.commands.compile_po import PLURAL_SEP


class ScannerTests(SimpleTestCase):
    """The scanner itself must find what xgettext would find."""

    def found(self, template):
        return list(i18n_check.template_msgids(template))

    def test_trans_tag_with_both_quote_styles(self):
        self.assertEqual(
            self.found('{% trans "One" %} {% trans \'Two "x"\' %}'),
            [('One', None), ('Two "x"', None)],
        )

    def test_trans_with_as_variable(self):
        self.assertEqual(self.found('{% trans "Hi" as t %}{{ t }}'), [('Hi', None)])

    def test_blocktrans_variables_become_named_placeholders(self):
        self.assertEqual(
            self.found('{% blocktrans with a=b %}Hello {{ a }}!{% endblocktrans %}'),
            [('Hello %(a)s!', None)],
        )

    def test_blocktrans_plural(self):
        self.assertEqual(
            self.found(
                '{% blocktrans count counter=n %}{{ counter }} bag'
                '{% plural %}{{ counter }} bags{% endblocktrans %}'),
            [('%(counter)s bag', '%(counter)s bags')],
        )

    def test_percent_is_doubled_like_django_does(self):
        self.assertEqual(
            self.found('{% blocktrans %}10% done{% endblocktrans %}'),
            [('10%% done', None)],
        )

    def test_blocktrans_trimmed(self):
        self.assertEqual(
            self.found('{% blocktrans trimmed %}\n  One\n  two\n{% endblocktrans %}'),
            [('One two', None)],
        )

    def test_python_calls(self):
        source = (
            "from django.utils.translation import gettext_lazy as _\n"
            "a = _('Lazy one')\n"
            "b = gettext('Plain')\n"
            "c = ngettext('%(n)s a', '%(n)s as', n)\n"
            "d = other('Ignored')\n"
            "e = _(variable)\n"
        )
        self.assertEqual(
            list(i18n_check.python_msgids(source)),
            [('Lazy one', None), ('Plain', None), ('%(n)s a', '%(n)s as')],
        )


class BanglaCatalogueTests(SimpleTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.report = i18n_check.compare()

    def message(self, label):
        lines = []
        for key, where in self.report[label]:
            lines.append('%r in %s' % (
                key.replace(PLURAL_SEP, ' || '), ', '.join(sorted(set(where))[:2])))
        return '\n'.join(lines)

    def test_every_wrapped_string_has_a_bangla_translation(self):
        self.assertEqual(
            self.report['missing'], [],
            'Add these msgids to locale/bn/LC_MESSAGES/django.po:\n'
            + self.message('missing'))

    def test_no_translation_is_empty(self):
        self.assertEqual(
            self.report['empty'], [],
            'Empty msgstr in django.po:\n' + self.message('empty'))

    def test_no_translation_is_fuzzy(self):
        self.assertEqual(
            self.report['fuzzy'], [],
            'Fuzzy entries are ignored when compiling:\n' + self.message('fuzzy'))

    def test_no_stale_entries(self):
        self.assertEqual(
            self.report['stale'], [],
            'These django.po entries are no longer used anywhere: '
            + ', '.join(repr(k) for k in self.report['stale']))

    def test_placeholders_survive_translation(self):
        """A translation must keep exactly the %(name)s placeholders of its
        msgid, otherwise rendering raises or silently drops a value."""
        pattern = re.compile(r'%\((\w+)\)s')
        problems = []
        for entry in i18n_check.load_po():
            if not entry.msgid:
                continue
            wanted = sorted(pattern.findall(entry.msgid))
            for index, text in entry.msgstr.items():
                if text and sorted(pattern.findall(text)) != wanted:
                    problems.append((entry.msgid, index))
            # A plural form must keep the placeholders of the plural msgid.
            if entry.msgid_plural is not None:
                wanted_plural = sorted(pattern.findall(entry.msgid_plural))
                for index, text in entry.msgstr.items():
                    if text and sorted(pattern.findall(text)) != wanted_plural:
                        problems.append((entry.msgid, index))
        self.assertEqual(problems, [])

    def test_format_braces_survive_translation(self):
        brace = re.compile(r'\{(\w+)\}')
        problems = [
            e.msgid for e in i18n_check.load_po()
            if e.msgid and any(
                sorted(brace.findall(text)) != sorted(brace.findall(e.msgid))
                for text in e.msgstr.values() if text)
        ]
        self.assertEqual(problems, [])

    def test_header_declares_bangla_and_plural_rule(self):
        header = next(e for e in i18n_check.load_po() if e.msgid == '')
        text = header.msgstr[0]
        self.assertIn('charset=UTF-8', text)
        self.assertIn('Language: bn', text)
        self.assertIn('nplurals=2; plural=(n > 1);', text)

    def test_review_notice_is_in_the_catalogue(self):
        source = i18n_check.PO_PATH.read_text(encoding='utf-8')
        self.assertIn('native', source.split('msgid ""')[0].lower())

    def test_compiled_catalogue_matches_the_source(self):
        mo = Path(i18n_check.PO_PATH).with_suffix('.mo')
        self.assertTrue(mo.exists(), 'Run: python manage.py compile_po')
        with open(mo, 'rb') as handle:
            catalog = gettext.GNUTranslations(handle)
        self.assertEqual(catalog.gettext('Home'), 'হোম')

    def test_no_bangla_digits_in_translations(self):
        """Digits stay ASCII everywhere, so numbers read the same in both
        languages (dates, counts and phone-like text)."""
        bangla_digits = set('০১২৩৪৫৬৭৮৯')
        bad = [e.msgid for e in i18n_check.load_po()
               if any(set(t) & bangla_digits for t in e.msgstr.values())]
        self.assertEqual(bad, [])

    def test_product_name_is_never_inside_a_msgid(self):
        from django.conf import settings
        bad = [e.msgid for e in i18n_check.load_po()
               if e.msgid and (settings.SITE_NAME in e.msgid
                               or settings.SITE_NAME in ''.join(e.msgstr.values()))]
        self.assertEqual(bad, [])
