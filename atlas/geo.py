"""The district tile grid, abbreviations and slugs.

Each of the 64 districts is one equal tile. The grid below is drawn by hand
(north at the top, west on the left) so that neighbours on the real map
stay neighbours here as far as a grid allows: Rangpur in the north-west,
Sylhet in the north-east, Dhaka in the middle, Chattogram and Cox's Bazar
down the south-east, Khulna and Barishal in the south-west.

Every cell is a three-letter code, `.` is empty. The codes are only labels
for the tiles; the full name is always available on focus and hover.
"""
from django.utils.text import slugify

from rewards.geo import DISTRICT_NAMES, DISTRICT_TO_DIVISION, DIVISIONS

CODES = {
    'Barguna': 'BGN', 'Barishal': 'BAR', 'Bhola': 'BHO',
    'Jhalokati': 'JHA', 'Patuakhali': 'PAT', 'Pirojpur': 'PIR',
    'Bandarban': 'BAN', 'Brahmanbaria': 'BRA', 'Chandpur': 'CHD',
    'Chattogram': 'CTG', 'Cumilla': 'CUM', "Cox's Bazar": 'COX',
    'Feni': 'FEN', 'Khagrachhari': 'KHA', 'Lakshmipur': 'LAK',
    'Noakhali': 'NOA', 'Rangamati': 'RAN',
    'Dhaka': 'DHA', 'Faridpur': 'FAR', 'Gazipur': 'GAZ',
    'Gopalganj': 'GOP', 'Kishoreganj': 'KIS', 'Madaripur': 'MAD',
    'Manikganj': 'MAN', 'Munshiganj': 'MUN', 'Narayanganj': 'NRG',
    'Narsingdi': 'NRS', 'Rajbari': 'RAJ', 'Shariatpur': 'SHA',
    'Tangail': 'TAN',
    'Bagerhat': 'BAG', 'Chuadanga': 'CHU', 'Jashore': 'JAS',
    'Jhenaidah': 'JHE', 'Khulna': 'KHU', 'Kushtia': 'KUS', 'Magura': 'MAG',
    'Meherpur': 'MEH', 'Narail': 'NAR', 'Satkhira': 'SAT',
    'Jamalpur': 'JAM', 'Mymensingh': 'MYM', 'Netrokona': 'NET',
    'Sherpur': 'SHE',
    'Bogura': 'BOG', 'Chapainawabganj': 'CHP', 'Joypurhat': 'JOY',
    'Naogaon': 'NAO', 'Natore': 'NAT', 'Pabna': 'PAB', 'Rajshahi': 'RSH',
    'Sirajganj': 'SIR',
    'Dinajpur': 'DIN', 'Gaibandha': 'GAI', 'Kurigram': 'KUR',
    'Lalmonirhat': 'LAL', 'Nilphamari': 'NIL', 'Panchagarh': 'PAN',
    'Rangpur': 'RNG', 'Thakurgaon': 'THA',
    'Habiganj': 'HAB', 'Moulvibazar': 'MOU', 'Sunamganj': 'SUN',
    'Sylhet': 'SYL',
}
CODE_TO_DISTRICT = {code: name for name, code in CODES.items()}

#   col: 0   1   2   3   4   5   6   7   8   9   10
LAYOUT = """
.   PAN .   .   .   .   .   .   .   .   .
THA NIL RNG LAL KUR .   .   .   .   .   .
.   DIN JOY GAI JAM SHE .   SUN SYL .   .
.   CHP NAO BOG SIR MYM NET HAB MOU .   .
.   RSH NAT PAB TAN GAZ KIS BRA .   .   .
.   MEH KUS RAJ MAN DHA NRS CUM KHA .   .
.   CHU JHE MAG FAR MUN NRG CHD FEN RAN .
.   .   JAS NAR GOP MAD SHA LAK NOA CTG BAN
.   .   SAT KHU BAG PIR JHA BAR .   COX .
.   .   .   .   .   BGN PAT BHO .   .   .
"""


def _parse(layout):
    cells = {}
    for row, line in enumerate(layout.strip().splitlines()):
        for col, code in enumerate(line.split()):
            if code != '.':
                cells[CODE_TO_DISTRICT[code]] = (col, row)
    return cells


TILES = _parse(LAYOUT)  # district name -> (col, row)
COLUMNS = max(c for c, _r in TILES.values()) + 1
ROWS = max(r for _c, r in TILES.values()) + 1

SLUGS = {name: slugify(name) for name in DISTRICT_NAMES}
SLUG_TO_DISTRICT = {slug: name for name, slug in SLUGS.items()}

DIVISION_NAMES = list(DIVISIONS)  # stable order, also the colour index
DIVISION_INDEX = {name: i for i, name in enumerate(DIVISION_NAMES)}


def division_of(district):
    return DISTRICT_TO_DIVISION[district]


def district_for_slug(slug):
    """The district name for a URL slug, or None."""
    return SLUG_TO_DISTRICT.get(slug)
