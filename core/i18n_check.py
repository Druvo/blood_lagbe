"""Find every translatable string in the project and compare it with the .po.

GNU xgettext is not available here, so the Bangla catalogue
(locale/bn/LC_MESSAGES/django.po) is maintained by hand. This module scans the
source for the same markers xgettext would find and reports

* missing:  a wrapped msgid with no Bangla translation (absent, empty or fuzzy)
* stale:    a .po entry that nothing in the source uses any more

Markers understood:
  templates   {% trans "..." %}, {% blocktrans %}...{% endblocktrans %}
              (with `with`, `count`, `trimmed`, `{% plural %}`)
  python      gettext / gettext_lazy / gettext_noop / ngettext / ngettext_lazy
              and the `_()` alias, called with a plain string literal

Run it from the command line with `python scripts/check_translations.py`.
"""
import ast
import os
import re
from pathlib import Path

from core.management.commands.compile_po import PLURAL_SEP, parse_entries

BASE_DIR = Path(__file__).resolve().parent.parent
PO_PATH = BASE_DIR / 'locale' / 'bn' / 'LC_MESSAGES' / 'django.po'

SKIP_DIRS = {
    '.git', '.claude', '__pycache__', 'node_modules', 'staticfiles', 'media',
    'locale', 'migrations', 'venv', 'env', '.venv', 'scripts', 'admin',
}
# Names whose first string argument is a msgid.
SINGULAR_FUNCS = {
    'gettext', 'gettext_lazy', 'gettext_noop', 'gettext_noop_lazy', '_',
    'ugettext', 'ugettext_lazy',
}
PLURAL_FUNCS = {'ngettext', 'ngettext_lazy'}

_STRING = r'''"(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*\''''
TRANS_RE = re.compile(
    r'\{%\s*(?:trans|translate)\s+(' + _STRING + r')([^%]*)%\}'
)
BLOCK_RE = re.compile(
    r'\{%\s*(?:blocktrans|blocktranslate)\b([^%]*)%\}(.*?)'
    r'\{%\s*end(?:blocktrans|blocktranslate)\s*%\}',
    re.DOTALL,
)
PLURAL_TAG_RE = re.compile(r'\{%\s*plural\s*%\}')
VAR_RE = re.compile(r'\{\{\s*(\w+)\s*\}\}')


def _unquote(literal):
    return ast.literal_eval(literal)


def _block_msgid(text, trimmed):
    if trimmed:
        text = re.sub(r'\s*\n\s*', ' ', text.strip())
    return VAR_RE.sub(lambda m: '%(' + m.group(1) + ')s', text.replace('%', '%%'))


def template_msgids(text):
    """Yield (msgid, msgid_plural_or_None) found in a template's source."""
    for match in TRANS_RE.finditer(text):
        yield _unquote(match.group(1)), None
    for match in BLOCK_RE.finditer(text):
        options, body = match.groups()
        trimmed = re.search(r'\btrimmed\b', options) is not None
        parts = PLURAL_TAG_RE.split(body, maxsplit=1)
        singular = _block_msgid(parts[0], trimmed)
        plural = _block_msgid(parts[1], trimmed) if len(parts) > 1 else None
        yield singular, plural


def python_msgids(source):
    """Yield (msgid, msgid_plural_or_None) for gettext-style calls."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not node.args:
            continue
        func = node.func
        name = func.id if isinstance(func, ast.Name) else (
            func.attr if isinstance(func, ast.Attribute) else None)
        first = node.args[0]
        if not (isinstance(first, ast.Constant) and isinstance(first.value, str)):
            continue
        if name in SINGULAR_FUNCS:
            yield first.value, None
        elif name in PLURAL_FUNCS and len(node.args) > 1:
            second = node.args[1]
            if isinstance(second, ast.Constant) and isinstance(second.value, str):
                yield first.value, second.value


def _source_files(root):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for name in filenames:
            path = Path(dirpath) / name
            if name.endswith(('.html', '.htm', '.txt')):
                yield path
            elif name.endswith('.py') and not (
                name.startswith('test') or name in {'manage.py', 'conftest.py'}
            ) and 'tests' not in path.relative_to(root).parts:
                yield path


def collect_source_msgids(root=BASE_DIR):
    """Return {key: [relative locations]} where key is the catalogue key
    (msgid, or msgid + NUL + msgid_plural for plural messages)."""
    found = {}
    for path in _source_files(root):
        try:
            text = path.read_text(encoding='utf-8')
        except UnicodeDecodeError:
            continue
        if path.suffix == '.py':
            items = python_msgids(text)
        else:
            items = template_msgids(text)
        for msgid, plural in items:
            if not msgid.strip():
                continue
            key = msgid if plural is None else msgid + PLURAL_SEP + plural
            found.setdefault(key, []).append(str(path.relative_to(root)))
    return found


def load_po(po_path=PO_PATH):
    return parse_entries(Path(po_path).read_text(encoding='utf-8'))


def compare(root=BASE_DIR, po_path=PO_PATH):
    """Return a dict with the lists 'missing', 'empty', 'fuzzy', 'stale'."""
    source = collect_source_msgids(root)
    entries = [e for e in load_po(po_path) if e.msgid != '']
    by_key = {}
    for entry in entries:
        by_key[entry.key] = entry
    # A plural entry in the .po may be matched by msgid alone (blocktrans
    # without a plural) and the other way round; index by singular too.
    by_singular = {e.msgid: e for e in entries}

    missing, empty, fuzzy = [], [], []
    used = set()
    for key, where in sorted(source.items()):
        entry = by_key.get(key) or by_singular.get(key.split(PLURAL_SEP)[0])
        if entry is None:
            missing.append((key, where))
            continue
        used.add(entry.key)
        if entry.fuzzy:
            fuzzy.append((key, where))
        elif not entry.translated or not all(
            text.strip() for text in entry.msgstr.values()
        ):
            empty.append((key, where))
    stale = [e.key for e in entries if e.key not in used]
    return {'missing': missing, 'empty': empty, 'fuzzy': fuzzy, 'stale': stale}


def format_report(result):
    lines = []
    for label in ('missing', 'empty', 'fuzzy'):
        for key, where in result[label]:
            shown = key.replace(PLURAL_SEP, ' || ')
            lines.append('%s: %r  (%s)' % (label.upper(), shown, ', '.join(sorted(set(where))[:3])))
    for key in result['stale']:
        lines.append('STALE: %r' % key.replace(PLURAL_SEP, ' || '))
    return '\n'.join(lines)
