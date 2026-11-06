"""Compile every ``locale/*/LC_MESSAGES/django.po`` to ``django.mo``.

    python manage.py compile_po            # all languages under locale/
    python manage.py compile_po --check    # fail if a .mo is missing or stale

This machine (and many small servers) has no GNU gettext, so ``compilemessages``
cannot run. The compiler below is a port of CPython's Tools/i18n/msgfmt.py with
support for message contexts (msgctxt), plural forms and multi-line strings.
The .po file is the source of truth; the compiled .mo is committed as well.
"""
import ast
import struct
from array import array
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

CTXT_SEP = '\x04'  # separates msgctxt from msgid inside the catalogue key
PLURAL_SEP = '\x00'
MAGIC = 0x950412DE


class POSyntaxError(ValueError):
    pass


def _unquote(raw, lineno):
    """Turn a quoted .po string literal into a Python str."""
    raw = raw.strip()
    if len(raw) < 2 or raw[0] != '"' or raw[-1] != '"':
        raise POSyntaxError('line %d: expected a quoted string' % lineno)
    try:
        # Reuse Python's escape handling (\n, \t, \", \\ ...). Only a plain
        # quoted literal is evaluated, never arbitrary code.
        value = ast.literal_eval(raw)
    except (SyntaxError, ValueError) as exc:
        raise POSyntaxError('line %d: bad string literal (%s)' % (lineno, exc))
    if not isinstance(value, str):
        raise POSyntaxError('line %d: expected a text string' % lineno)
    return value


class Entry:
    """One catalogue entry of a .po file."""

    def __init__(self, fuzzy=False):
        self.msgctxt = None
        self.msgid = None
        self.msgid_plural = None
        self.msgstr = {}  # index -> text
        self.fuzzy = fuzzy
        self.lineno = 0

    @property
    def key(self):
        key = self.msgid
        if self.msgid_plural is not None:
            key += PLURAL_SEP + self.msgid_plural
        if self.msgctxt is not None:
            key = self.msgctxt + CTXT_SEP + key
        return key

    @property
    def value(self):
        return PLURAL_SEP.join(self.msgstr[i] for i in sorted(self.msgstr))

    @property
    def translated(self):
        return any(text for text in self.msgstr.values())


def parse_entries(text):
    """Parse .po text into a list of Entry objects (header included)."""
    entries = []
    current = None
    field = None  # (name, index) the next continuation line belongs to
    pending_fuzzy = False

    def start_entry(lineno):
        nonlocal current, pending_fuzzy
        current = Entry(fuzzy=pending_fuzzy)
        current.lineno = lineno
        pending_fuzzy = False
        entries.append(current)

    for lineno, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        if line.startswith('#'):
            # A comment ends the previous entry; '#, fuzzy' flags the next.
            if current is not None and current.msgstr:
                current = None
            if line.startswith('#,') and 'fuzzy' in line[2:].replace(',', ' ').split():
                pending_fuzzy = True
            continue

        if line.startswith('msgctxt'):
            if current is None or current.msgstr:
                start_entry(lineno)
            current.msgctxt = _unquote(line[len('msgctxt'):], lineno)
            field = ('msgctxt', None)
        elif line.startswith('msgid_plural'):
            if current is None or current.msgid is None:
                raise POSyntaxError('line %d: msgid_plural before msgid' % lineno)
            current.msgid_plural = _unquote(line[len('msgid_plural'):], lineno)
            field = ('msgid_plural', None)
        elif line.startswith('msgid'):
            if current is None or current.msgstr or current.msgid is not None:
                start_entry(lineno)
            current.msgid = _unquote(line[len('msgid'):], lineno)
            field = ('msgid', None)
        elif line.startswith('msgstr'):
            if current is None or current.msgid is None:
                raise POSyntaxError('line %d: msgstr before msgid' % lineno)
            rest = line[len('msgstr'):].lstrip()
            index = 0
            if rest.startswith('['):
                close = rest.index(']')
                index = int(rest[1:close])
                rest = rest[close + 1:]
            current.msgstr[index] = _unquote(rest, lineno)
            field = ('msgstr', index)
        elif line.startswith('"'):
            piece = _unquote(line, lineno)
            if current is None or field is None:
                raise POSyntaxError('line %d: stray string' % lineno)
            name, index = field
            if name == 'msgstr':
                current.msgstr[index] += piece
            else:
                setattr(current, name, getattr(current, name) + piece)
        else:
            raise POSyntaxError('line %d: cannot parse %r' % (lineno, line))

    for entry in entries:
        if entry.msgid is None or not entry.msgstr:
            raise POSyntaxError(
                'line %d: entry without msgid or msgstr' % entry.lineno)
    return entries


def parse_po(text):
    """Return {key: value} ready to be written as a .mo.

    Fuzzy entries and entries without a translation are skipped; the header
    (empty msgid) is always kept.
    """
    messages = {}
    for entry in parse_entries(text):
        is_header = entry.msgid == '' and entry.msgctxt is None
        if entry.fuzzy and not is_header:
            continue
        if entry.translated or is_header:
            messages[entry.key] = entry.value
    return messages


def build_mo(messages):
    """Return the bytes of a GNU .mo file for ``messages`` ({key: value})."""
    keys = sorted(messages)  # the .mo format needs sorted msgids
    offsets = []
    ids = strs = b''
    for key in keys:
        kb = key.encode('utf-8')
        vb = messages[key].encode('utf-8')
        offsets.append((len(ids), len(kb), len(strs), len(vb)))
        ids += kb + b'\0'
        strs += vb + b'\0'
    keystart = 7 * 4 + 16 * len(keys)
    valuestart = keystart + len(ids)
    koffsets = []
    voffsets = []
    for o1, l1, o2, l2 in offsets:
        koffsets += [l1, o1 + keystart]
        voffsets += [l2, o2 + valuestart]
    table = array('i', koffsets + voffsets)
    header = struct.pack(
        'Iiiiiii', MAGIC, 0, len(keys), 7 * 4, 7 * 4 + len(keys) * 8, 0, 0,
    )
    return header + table.tobytes() + ids + strs


def compile_file(po_path):
    """Compile one .po file next to itself. Returns the .mo path."""
    po_path = Path(po_path)
    messages = parse_po(po_path.read_text(encoding='utf-8'))
    mo_path = po_path.with_suffix('.mo')
    mo_path.write_bytes(build_mo(messages))
    return mo_path


def po_files(root):
    return sorted(Path(root).glob('*/LC_MESSAGES/django.po'))


class Command(BaseCommand):
    help = 'Compile locale/*/LC_MESSAGES/django.po to django.mo (no gettext needed).'

    def add_arguments(self, parser):
        parser.add_argument(
            '--check', action='store_true',
            help='Do not write; exit with an error if a .mo is missing or out of date.',
        )

    def handle(self, *args, **options):
        found = []
        for root in settings.LOCALE_PATHS:
            found.extend(po_files(root))
        if not found:
            raise CommandError('No django.po files found under LOCALE_PATHS.')
        stale = []
        for po in found:
            try:
                if options['check']:
                    expected = build_mo(parse_po(po.read_text(encoding='utf-8')))
                    mo = po.with_suffix('.mo')
                    if not mo.exists() or mo.read_bytes() != expected:
                        stale.append(str(mo))
                    continue
                mo = compile_file(po)
            except POSyntaxError as exc:
                raise CommandError('%s: %s' % (po, exc))
            self.stdout.write('Compiled %s' % mo)
        if stale:
            raise CommandError('Out of date (run compile_po): ' + ', '.join(stale))
        if options['check']:
            self.stdout.write('All .mo files are up to date.')
