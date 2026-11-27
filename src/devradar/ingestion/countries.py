import re

_NAMES = {
    "US": "united states|united states of america|usa|u\\.s\\.a\\.?|u\\.s\\.",
    "CA": "canada",
    "GB": "united kingdom|great britain|britain|england|scotland|(?<!new south )wales|northern ireland",
    "IE": "ireland",
    "FR": "france",
    "DE": "germany|deutschland",
    "NL": "netherlands|the netherlands|holland",
    "BE": "belgium",
    "LU": "luxembourg",
    "CH": "switzerland",
    "AT": "austria",
    "ES": "spain",
    "PT": "portugal",
    "IT": "italy",
    "GR": "greece",
    "SE": "sweden",
    "NO": "norway",
    "DK": "denmark",
    "FI": "finland",
    "IS": "iceland",
    "PL": "poland",
    "CZ": "czech republic|czechia",
    "SK": "slovakia",
    "HU": "hungary",
    "RO": "romania",
    "BG": "bulgaria",
    "RS": "serbia",
    "HR": "croatia",
    "SI": "slovenia",
    "EE": "estonia",
    "LV": "latvia",
    "LT": "lithuania",
    "UA": "ukraine",
    "RU": "russia|russian federation",
    "TR": "turkey|türkiye|turkiye",
    "CY": "cyprus",
    "MT": "malta",
    "IL": "israel",
    "AE": "united arab emirates|uae",
    "SA": "saudi arabia",
    "QA": "qatar",
    "EG": "egypt",
    "MA": "morocco|maroc",
    "NG": "nigeria",
    "KE": "kenya",
    "ZA": "south africa",
    "IN": "india",
    "PK": "pakistan",
    "BD": "bangladesh",
    "LK": "sri lanka",
    "CN": "china|prc",
    "HK": "hong kong",
    "TW": "taiwan",
    "JP": "japan",
    "KR": "south korea|korea|republic of korea",
    "SG": "singapore",
    "MY": "malaysia",
    "TH": "thailand",
    "VN": "vietnam|viet nam",
    "PH": "philippines",
    "ID": "indonesia",
    "AU": "australia",
    "NZ": "new zealand",
    "MX": "mexico|méxico",
    "BR": "brazil|brasil",
    "AR": "argentina",
    "CL": "chile",
    "CO": "colombia",
    "PE": "peru",
    "UY": "uruguay",
    "CR": "costa rica",
    "PR": "puerto rico",
    "NP": "nepal",
    "GT": "guatemala",
    "SV": "el salvador",
    "PA": "panama",
    "JM": "jamaica",
    "KY": "cayman islands",
    "BH": "bahrain",
    "KW": "kuwait",
    "LB": "lebanon",
    "JO": "jordan",
    "AZ": "azerbaijan",
}
# Short codes are matched only in capitals, so "us" in prose or "in" as a word is not a country.
_CODES = {
    "US": "US",
    "UK": "GB",
    "GB": "GB",
    "UAE": "AE",
    "USA": "US",
    "CAN": "CA",
    "GBR": "GB",
    "IND": "IN",
    "COL": "CO",
    "MYS": "MY",
    "JOR": "JO",
    "KSA": "SA",
    "MEX": "MX",
    "BRA": "BR",
    "ARG": "AR",
    "DEU": "DE",
    "FRA": "FR",
    "AUS": "AU",
    "SGP": "SG",
    "ISR": "IL",
    "POL": "PL",
}
_EUROPE = [
    "GB",
    "IE",
    "FR",
    "DE",
    "NL",
    "BE",
    "LU",
    "CH",
    "AT",
    "ES",
    "PT",
    "IT",
    "GR",
    "SE",
    "NO",
    "DK",
    "FI",
    "IS",
    "PL",
    "CZ",
    "SK",
    "HU",
    "RO",
    "BG",
    "RS",
    "HR",
    "SI",
    "EE",
    "LV",
    "LT",
    "UA",
    "CY",
    "MT",
]
_REGIONS = {
    "europe|european union|eu": _EUROPE,
    "emea": _EUROPE + ["IL", "AE", "SA", "QA", "EG", "MA", "NG", "KE", "ZA", "TR"],
    "apac|asia[- ]pacific|asia": [
        "IN",
        "PK",
        "BD",
        "LK",
        "CN",
        "HK",
        "TW",
        "JP",
        "KR",
        "SG",
        "MY",
        "TH",
        "VN",
        "PH",
        "ID",
        "AU",
        "NZ",
    ],
    "latam|latin america|south america": [
        "MX",
        "BR",
        "AR",
        "CL",
        "CO",
        "PE",
        "UY",
        "CR",
        "GT",
        "SV",
        "PA",
    ],
    "north america": ["US", "CA", "MX"],
}
_US_STATES = {
    "AL": "alabama",
    "AK": "alaska",
    "AZ": "arizona",
    "AR": "arkansas",
    "CA": "california",
    "CO": "colorado",
    "CT": "connecticut",
    "DE": "delaware",
    "DC": "district of columbia",
    "FL": "florida",
    "GA": "georgia",
    "HI": "hawaii",
    "ID": "idaho",
    "IL": "illinois",
    "IN": "indiana",
    "IA": "iowa",
    "KS": "kansas",
    "KY": "kentucky",
    "LA": "louisiana",
    "ME": "maine",
    "MD": "maryland",
    "MA": "massachusetts",
    "MI": "michigan",
    "MN": "minnesota",
    "MS": "mississippi",
    "MO": "missouri",
    "MT": "montana",
    "NE": "nebraska",
    "NV": "nevada",
    "NH": "new hampshire",
    "NJ": "new jersey",
    "NM": "new mexico",
    "NY": "new york",
    "NC": "north carolina",
    "ND": "north dakota",
    "OH": "ohio",
    "OK": "oklahoma",
    "OR": "oregon",
    "PA": "pennsylvania",
    "RI": "rhode island",
    "SC": "south carolina",
    "SD": "south dakota",
    "TN": "tennessee",
    "TX": "texas",
    "UT": "utah",
    "VT": "vermont",
    "VA": "virginia",
    "WA": "washington",
    "WV": "west virginia",
    "WI": "wisconsin",
    "WY": "wyoming",
}
_PROVINCES = {
    "AB": "alberta",
    "BC": "british columbia",
    "MB": "manitoba",
    "NB": "new brunswick",
    "NL": "newfoundland",
    "NS": "nova scotia",
    "ON": "ontario",
    "PE": "prince edward island",
    "QC": "quebec|québec",
    "SK": "saskatchewan",
}
# Cities that name one country when written alone. Ambiguous ones, such as Cambridge or
# Manchester, are left out so they stay unknown.
_CITIES = {
    "US": (
        "san francisco|sf|bay area|silicon valley|new york city|new york|nyc|manhattan|brooklyn"
        "|seattle|boston|chicago|austin|los angeles|san diego|san jose|mountain view|palo alto"
        "|menlo park|sunnyvale|santa clara|cupertino|redwood city|san mateo|foster city"
        "|south san francisco|oakland|berkeley|emeryville|redmond|bellevue|kirkland|denver"
        "|boulder|atlanta|miami|dallas|houston|philadelphia|pittsburgh|washington d\\.?c\\.?"
        "|arlington|reston|herndon|mclean|baltimore|detroit|ann arbor|minneapolis|columbus"
        "|raleigh|durham|charlotte|nashville|phoenix|salt lake city|lehi|portland|irvine"
        "|el segundo|hawthorne|costa mesa|santa monica|playa vista|culver city|long beach"
        "|pasadena|torrance|huntsville|princeton|jersey city|hoboken|stamford|greenwich"
        "|somerville|waltham|needham|burlington|providence|new haven|ithaca|chapel hill"
        "|madison|st\\.? louis|kansas city|indianapolis|cincinnati|cleveland|tampa|orlando"
        "|las vegas|albuquerque|sacramento|fremont|milpitas|san carlos|los altos|livermore"
        "|scotts valley|oxnard|alameda|ventura|omaha"
    ),
    "CA": "toronto|vancouver|montreal|montréal|waterloo|kitchener|ottawa|calgary|edmonton",
    "GB": "london|edinburgh|glasgow|bristol|oxford|leeds|belfast",
    "IE": "dublin|cork",
    "FR": "paris|lyon|toulouse|nice|grenoble",
    "DE": "berlin|munich|münchen|hamburg|frankfurt|cologne|stuttgart|düsseldorf|dusseldorf",
    "NL": "amsterdam|rotterdam|utrecht|eindhoven|the hague|rijswijk|delft",
    "CH": "zurich|zürich|geneva|lausanne|zug",
    "ES": "madrid|barcelona",
    "PT": "lisbon|porto",
    "IT": "milan|rome",
    "DK": "copenhagen",
    "PL": "warsaw|krakow|kraków|wroclaw|wrocław",
    "CZ": "prague",
    "IL": "tel aviv|jerusalem|haifa|kfar saba|herzliya",
    "AE": "dubai|abu dhabi",
    "IN": (
        "bangalore|bengaluru|hyderabad|mumbai|pune|chennai|gurgaon|gurugram|noida|new delhi"
        "|jaipur|mohali|ahmedabad|kolkata"
    ),
    "CN": "shanghai|beijing|shenzhen|hangzhou",
    "JP": "tokyo|osaka",
    "KR": "seoul",
    "TW": "taipei|hsinchu",
    "AU": "sydney|melbourne|brisbane|perth|canberra",
    "NZ": "auckland|wellington",
    "BR": "são paulo|sao paulo|rio de janeiro|belo horizonte|campinas|florianópolis",
    "AR": "buenos aires",
    "CO": "bogotá|bogota|medellín|medellin",
    "MX": "guadalajara|monterrey",
    "PH": "manila|makati|taguig|pasig|pampanga|cebu",
    "MY": "kuala lumpur|penang",
    "RO": "bucharest|cluj",
    "RS": "belgrade|novi sad",
    "BG": "sofia",
    "BE": "brussels|antwerp|ghent",
    "SE": "stockholm|malmö|malmo|gothenburg",
    "AZ": "baku",
    "SA": "riyadh|jeddah",
    "ZA": "cape town|johannesburg",
    "MA": "casablanca",
    "VN": "ho chi minh|hanoi",
    "RU": "moscow|vladivostok",
}


def _words(alternatives: str) -> re.Pattern:
    return re.compile(rf"(?<![\w.])(?:{alternatives})(?![\w])", re.IGNORECASE)


_COUNTRY_PATTERNS = {code: _words(names) for code, names in _NAMES.items()}
_CODE_PATTERN = re.compile(rf"(?<![\w.])({'|'.join(_CODES)})(?![\w.])")
_STATE_CODES = re.compile(
    rf"(?:(?:,|\s)\s*|^\s*)({'|'.join(_US_STATES)}|{'|'.join(_PROVINCES)})(?![\w.])"
)
_US_STATE_NAMES = _words("|".join(_US_STATES.values()))
_PROVINCE_NAMES = _words("|".join(_PROVINCES.values()))
_CITY_PATTERNS = {code: _words(names) for code, names in _CITIES.items()}
_REGION_PATTERNS = [(_words(names), set(codes)) for names, codes in _REGIONS.items()]
_PLACES = re.compile(r";|\||•|·|\s/\s|(?<!,)\s(?i:or)\s|\sand\s|\s&\s")
# "New Mexico" and "Georgia, US" would otherwise read as the countries Mexico and Georgia.
_US_PREFIXED = re.compile(r"new mexico|new england|west jordan", re.IGNORECASE)


def country_code(name: str) -> str | None:
    """ISO code for a country an ATS reports as a code or a name."""
    name = name.strip()
    if re.fullmatch(r"[A-Za-z]{2}", name):
        return "GB" if name.upper() == "UK" else name.upper()
    for code, pattern in _COUNTRY_PATTERNS.items():
        if pattern.fullmatch(name):
            return code
    return _CODES.get(name)


def countries_in(location: str) -> set[str]:
    """Countries a location string names; empty when it does not clearly say."""
    found: set[str] = set()
    for place in _PLACES.split(location):
        found |= _place_countries(place)
    return found


def _place_countries(place: str) -> set[str]:
    cities, country_codes = _cities(place)
    found = _named(place)
    if not found:
        # "Remote - Europe" rules out the US without naming one country.
        for pattern, codes in _REGION_PATTERNS:
            if pattern.search(place):
                found |= codes
    for code in _STATE_CODES.findall(place):
        if code not in country_codes:
            found.add("US" if code in _US_STATES else "CA")
    if _US_STATE_NAMES.search(place):
        found.add("US")
    if _PROVINCE_NAMES.search(place):
        found.add("CA")
    return found | cities


def _named(text: str) -> set[str]:
    found = {
        code
        for code, pattern in _COUNTRY_PATTERNS.items()
        if pattern.search(_US_PREFIXED.sub("", text))
    }
    return found | {_CODES[code] for code in _CODE_PATTERN.findall(text)}


def _cities(place: str) -> tuple[set[str], set[str]]:
    """Countries of cities, skipping a city whose next part says where it is."""
    parts = [part.strip() for part in place.split(",")]
    found, country_codes = set(), set()
    for part, following in zip(parts, parts[1:] + [""], strict=True):
        countries = {c for c, pattern in _CITY_PATTERNS.items() if pattern.search(part)}
        if not countries:
            continue
        # "Bangalore, IN" and "Berlin, DE" end in a country code, not a US state.
        if following in countries:
            found.add(following)
            country_codes.add(following)
        elif not _qualifies(following):
            found |= countries
    return found, country_codes


def _qualifies(part: str) -> bool:
    """Whether a part such as "Ontario" or "TX" says where the city before it is."""
    # In "London, Paris, New York", New York is the next city in a list.
    if any(pattern.fullmatch(part) for pattern in _CITY_PATTERNS.values()):
        return False
    return bool(
        _named(part)
        or _STATE_CODES.fullmatch(f", {part}")
        or _US_STATE_NAMES.fullmatch(part)
        or _PROVINCE_NAMES.fullmatch(part)
    )


def posting_countries(locations: tuple[str, ...], hints: dict[str, str]) -> list[str]:
    """Countries of a posting; an ATS-reported country beats reading the location text."""
    found: set[str] = set()
    for location in locations:
        found |= {hints[location]} if location in hints else countries_in(location)
    return sorted(found)
