"""IOC 3-letter codes → ISO 3166-1 alpha-2 → flag emoji.

The flag emoji standard uses pairs of regional-indicator letters built
from ISO alpha-2 codes (KE → 🇰🇪). IOC and ISO disagree on a couple of
dozen codes (GER vs DE, NED vs NL, TPE vs TW, …), so we keep an explicit
mapping for everything that appears in our parquet plus a reasonable
margin for historical codes.

The mapping is also used by the templates: ``flag_emoji_map()`` returns
a JSON-serializable dict the JS in ``event.html`` reads to render flags
in the table without baking emoji into every row of every per-event JSON.
"""

from __future__ import annotations

# IOC code → ISO 3166-1 alpha-2. None for non-country entries that have
# no flag (mixed nationality, refugee teams, historical empires).
IOC_TO_ISO2: dict[str, str | None] = {
    # Africa
    "ALG": "DZ",
    "ANG": "AO",
    "BDI": "BI",
    "BEN": "BJ",
    "BOT": "BW",
    "BUR": "BF",
    "CAF": "CF",
    "CHA": "TD",
    "CIV": "CI",
    "CMR": "CM",
    "COD": "CD",
    "CGO": "CG",
    "COM": "KM",
    "CPV": "CV",
    "DJI": "DJ",
    "EGY": "EG",
    "ERI": "ER",
    "ETH": "ET",
    "GAB": "GA",
    "GAM": "GM",
    "GBS": "GW",
    "GEQ": "GQ",
    "GHA": "GH",
    "GUI": "GN",
    "KEN": "KE",
    "LBR": "LR",
    "LES": "LS",
    "LBA": "LY",
    "MAD": "MG",
    "MAR": "MA",
    "MAW": "MW",
    "MLI": "ML",
    "MOZ": "MZ",
    "MRI": "MU",
    "MTN": "MR",
    "NAM": "NA",
    "NGR": "NG",
    "NIG": "NE",
    "RSA": "ZA",
    "RWA": "RW",
    "SEY": "SC",
    "SEN": "SN",
    "SLE": "SL",
    "SOM": "SO",
    "SSD": "SS",
    "STP": "ST",
    "SUD": "SD",
    "SWZ": "SZ",
    "TAN": "TZ",
    "TOG": "TG",
    "TUN": "TN",
    "UGA": "UG",
    "ZAM": "ZM",
    "ZIM": "ZW",
    # Americas
    "AHO": "AN",
    "ANT": "AG",
    "ARG": "AR",
    "ARU": "AW",
    "BAH": "BS",
    "BAR": "BB",
    "BER": "BM",
    "BIZ": "BZ",
    "BOL": "BO",
    "BRA": "BR",
    "CAN": "CA",
    "CAY": "KY",
    "CHI": "CL",
    "COL": "CO",
    "CRC": "CR",
    "CUB": "CU",
    "DMA": "DM",
    "DOM": "DO",
    "ECU": "EC",
    "ESA": "SV",
    "GRN": "GD",
    "GUA": "GT",
    "GUY": "GY",
    "HAI": "HT",
    "HON": "HN",
    "ISV": "VI",
    "IVB": "VG",
    "JAM": "JM",
    "LCA": "LC",
    "MEX": "MX",
    "NCA": "NI",
    "PAN": "PA",
    "PAR": "PY",
    "PER": "PE",
    "PUR": "PR",
    "SKN": "KN",
    "SUR": "SR",
    "TRI": "TT",
    "URU": "UY",
    "USA": "US",
    "VEN": "VE",
    "VIN": "VC",
    # Asia
    "AFG": "AF",
    "BAN": "BD",
    "BHU": "BT",
    "BRN": "BN",
    "BRU": "BN",
    "CAM": "KH",
    "CHN": "CN",
    "HKG": "HK",
    "INA": "ID",
    "IND": "IN",
    "IRI": "IR",
    "IRQ": "IQ",
    "JOR": "JO",
    "JPN": "JP",
    "KAZ": "KZ",
    "KGZ": "KG",
    "KOR": "KR",
    "KSA": "SA",
    "KUW": "KW",
    "LAO": "LA",
    "LBN": "LB",
    "MAS": "MY",
    "MDV": "MV",
    "MGL": "MN",
    "MYA": "MM",
    "NEP": "NP",
    "OMA": "OM",
    "PAK": "PK",
    "PHI": "PH",
    "PLE": "PS",
    "PRK": "KP",
    "QAT": "QA",
    "SGP": "SG",
    "SRI": "LK",
    "SYR": "SY",
    "THA": "TH",
    "TJK": "TJ",
    "TKM": "TM",
    "TLS": "TL",
    "TPE": "TW",
    "UAE": "AE",
    "UZB": "UZ",
    "VIE": "VN",
    "YEM": "YE",
    # Europe
    "ALB": "AL",
    "AND": "AD",
    "ARM": "AM",
    "AUT": "AT",
    "AZE": "AZ",
    "BEL": "BE",
    "BIH": "BA",
    "BLR": "BY",
    "BUL": "BG",
    "CRO": "HR",
    "CYP": "CY",
    "CZE": "CZ",
    "DEN": "DK",
    "ESP": "ES",
    "EST": "EE",
    "FIN": "FI",
    "FRA": "FR",
    "GBR": "GB",
    "GEO": "GE",
    "GER": "DE",
    "GRE": "GR",
    "HUN": "HU",
    "IRL": "IE",
    "ISL": "IS",
    "ISR": "IL",
    "ITA": "IT",
    "KOS": "XK",
    "LAT": "LV",
    "LIE": "LI",
    "LTU": "LT",
    "LUX": "LU",
    "MDA": "MD",
    "MKD": "MK",
    "MLT": "MT",
    "MNE": "ME",
    "MON": "MC",
    "NED": "NL",
    "NOR": "NO",
    "POL": "PL",
    "POR": "PT",
    "ROU": "RO",
    "RUS": "RU",
    "SLO": "SI",
    "SMR": "SM",
    "SRB": "RS",
    "SUI": "CH",
    "SVK": "SK",
    "SWE": "SE",
    "TUR": "TR",
    "UKR": "UA",
    # Oceania
    "ASA": "AS",
    "AUS": "AU",
    "COK": "CK",
    "FIJ": "FJ",
    "FSM": "FM",
    "GUM": "GU",
    "KIR": "KI",
    "MHL": "MH",
    "NRU": "NR",
    "NZL": "NZ",
    "PLW": "PW",
    "PNG": "PG",
    "SAM": "WS",
    "SOL": "SB",
    "TGA": "TO",
    "TUV": "TV",
    "VAN": "VU",
    # Historical / former teams — fall through to the closest modern
    # successor state's flag. Not perfect (the DDR's flag wasn't the
    # modern German one, etc.), but giving a GDR athlete a German flag
    # next to their name is more useful than just the bare IOC code.
    "GDR": "DE",  # East Germany → Germany
    "FRG": "DE",  # West Germany → Germany
    "URS": "RU",  # Soviet Union → Russia (largest legal successor)
    "CIS": "RU",  # Commonwealth of Independent States → Russia
    "EUN": "RU",  # Unified Team 1992 → Russia (largest contributor)
    "TCH": "CZ",  # Czechoslovakia → Czech Republic
    "BOH": "CZ",  # Bohemia → Czech Republic
    "YUG": "RS",  # Yugoslavia → Serbia (largest contributor)
    "SCG": "RS",  # Serbia & Montenegro → Serbia
    "ANZ": "AU",  # Australasia 1908/1912 → Australia
    # Mixed / refugee
    "ROT": None,
    "EOR": None,
    "MIX": None,
    "ANA": None,
    "AIN": None,
}


def ioc_to_emoji(ioc: str | None) -> str:
    """Return the flag emoji for an IOC code, or empty string."""
    if not ioc:
        return ""
    iso = IOC_TO_ISO2.get(ioc.upper())
    if iso is None or len(iso) != 2:
        return ""
    base = 0x1F1E6 - ord("A")
    return chr(base + ord(iso[0])) + chr(base + ord(iso[1]))


def flag_emoji_map() -> dict[str, str]:
    """JSON-serializable IOC → emoji map for the frontend.

    Entries with no flag (historical / mixed teams) are omitted so the JS
    ``map[code] || code`` pattern falls through to the bare 3-letter code.
    """
    return {ioc: ioc_to_emoji(ioc) for ioc, iso in IOC_TO_ISO2.items() if iso is not None}
