"""Report wrapped strings that have no Bangla translation, and stale entries.

    python scripts/check_translations.py          # exit 1 if anything is missing
    python scripts/check_translations.py --stale  # also fail on stale entries
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'blood_lagbe.settings')

from core.i18n_check import compare, format_report  # noqa: E402


def main(argv):
    result = compare()
    report = format_report(result)
    if report:
        print(report)
    bad = result['missing'] or result['empty'] or result['fuzzy']
    if '--stale' in argv:
        bad = bad or result['stale']
    print('\n%d missing, %d empty, %d fuzzy, %d stale' % (
        len(result['missing']), len(result['empty']),
        len(result['fuzzy']), len(result['stale']),
    ))
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
