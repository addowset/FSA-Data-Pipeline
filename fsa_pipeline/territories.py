"""Resolve a supplier's stated patch to a set of FSA local authorities.

A supplier answers "how far do you deliver?" in their own words -- "Yorkshire",
"Cambridgeshire", "the North West", "Bristol". This turns that into the set of
authority names their report should cover.

Authorities, not postcode areas. A postcode area is a Royal Mail sorting
convenience and crosses county lines freely: CB reaches into Essex, PE deep
into Lincolnshire, S across Derbyshire and Doncaster. A Cambridge roaster sent
"Cambridgeshire" venues in Saffron Walden and Old Leake knows at a glance that
the territory filter is wrong, and that is the first impression the report gets
to make. Authority boundaries are the unit the data is actually collected in
and they match what a supplier means by their patch.

REGION comes from the authorities table's own region_name column, so the twelve
regions never drift from the database. PLACE is the hand-built part: cities,
counties and the sub-regions suppliers actually name. It is deliberately a
lookup rather than anything clever -- a supplier typing "Cheshire" wants the
two Cheshire unitaries, not a fuzzy match that might reach Cheadle.
"""
from __future__ import annotations

import re
import sqlite3


class UnknownTerritory(ValueError):
    """Raised when a place name has no entry in the gazetteer."""


# Region synonyms -> the region_name values used in the authorities table.
REGION_SYNONYMS = {
    "london": "London",
    "scotland": "Scotland",
    "northern ireland": "Northern Ireland",
    "ni": "Northern Ireland",
    "wales": "Wales",
    "north west": "North West",
    "north west england": "North West",
    "north east": "North East",
    "yorkshire": "Yorkshire and Humberside",
    "yorkshire and the humber": "Yorkshire and Humberside",
    "yorkshire and humberside": "Yorkshire and Humberside",
    "east midlands": "East Midlands",
    "west midlands": "West Midlands",
    "south west": "South West",
    "sw": "South West",
    "south east": "South East",
    "se": "South East",
    "east counties": "East Counties",
    "east of england": "East Counties",
    "east anglia": "East Counties",
}

# Cities and towns. The value is the set of authorities a supplier naming that
# place would expect to see, which is not always one: "Aberdeen" to a wholesaler
# means the city and the shire around it.
CITIES = {
    "aberdeen": {"Aberdeen City", "Aberdeenshire"},
    "bath": {"Bath and North East Somerset"},
    "basingstoke": {"Basingstoke and Deane"},
    "belfast": {"Belfast City"},
    "birmingham": {"Birmingham"},
    "bolton": {"Bolton"},
    "bournemouth": {"Bournemouth, Christchurch and Poole"},
    "bradford": {"Bradford"},
    "brighton": {"Brighton and Hove"},
    "bristol": {"Bristol"},
    "cambridge": {"Cambridge City"},
    "cardiff": {"Cardiff"},
    "chelmsford": {"Chelmsford"},
    "cirencester": {"Cotswold"},
    "colchester": {"Colchester"},
    "cramlington": {"Northumberland"},
    "diss": {"South Norfolk"},
    "dundee": {"Dundee City"},
    "eastbourne": {"Eastbourne"},
    "edinburgh": {"Edinburgh (City of)"},
    "exeter": {"Exeter City"},
    "falkirk": {"Falkirk"},
    "glasgow": {"Glasgow City"},
    "gloucester": {"Gloucester City"},
    "halifax": {"Calderdale"},
    "harrogate": {"North Yorkshire"},
    "horsham": {"Horsham"},
    "hull": {"Hull City"},
    "leeds": {"Leeds"},
    "leicester": {"Leicester City"},
    "liverpool": {"Liverpool"},
    "luton": {"Luton"},
    "maidstone": {"Maidstone"},
    "manchester": {"Manchester"},
    "milton keynes": {"Milton Keynes"},
    "newcastle": {"Newcastle Upon Tyne"},
    "northampton": {"North Northamptonshire", "West Northamptonshire"},
    "norwich": {"Norwich City"},
    "nottingham": {"Nottingham City"},
    "oxford": {"Oxford City"},
    "plymouth": {"Plymouth City"},
    "sheffield": {"Sheffield"},
    "southampton": {"Southampton"},
    "stafford": {"Stafford"},
    "stoke on trent": {"Stoke-On-Trent"},
    "sunderland": {"Sunderland"},
    "swansea": {"Swansea"},
    "swindon": {"Swindon"},
    "tamworth": {"Tamworth"},
    "taunton": {"Somerset"},
    "wakefield": {"Wakefield"},
    "worcester": {"Worcester City"},
    "wrexham": {"Wrexham"},
    "york": {"York"},
}

# Counties and sub-regions, including the abbreviations suppliers write.
COUNTIES = {
    "bedfordshire": {"Bedford", "Central Bedfordshire", "Luton"},
    "berkshire": {"Reading", "Bracknell Forest", "Slough", "West Berkshire",
                  "Windsor and Maidenhead", "Wokingham"},
    "borders": {"Scottish Borders"},
    "buckinghamshire": {"Buckinghamshire", "Milton Keynes"},
    "cambridgeshire": {"Cambridge City", "South Cambridgeshire", "East Cambridgeshire",
                       "Huntingdonshire", "Fenland", "Peterborough City"},
    "central scotland": {"Falkirk", "Stirling", "Clackmannanshire", "Glasgow City",
                         "Edinburgh (City of)", "North Lanarkshire", "South Lanarkshire",
                         "West Lothian"},
    "cheshire": {"Cheshire East", "Cheshire West and Chester"},
    "county down": {"Newry, Mourne and Down", "Ards and North Down",
                    "Lisburn and Castlereagh City"},
    "cornwall": {"Cornwall", "Isles of Scilly"},
    "cumbria": {"Cumberland", "Westmorland and Furness"},
    "derbyshire": {"Amber Valley", "Bolsover", "Chesterfield", "Derby City",
                   "Derbyshire Dales", "Erewash", "High Peak", "North East Derbyshire",
                   "South Derbyshire"},
    "devon": {"East Devon", "Exeter City", "Mid Devon", "North Devon", "Plymouth City",
              "South Hams", "Teignbridge", "Torbay", "Torridge", "West Devon"},
    "dorset": {"Dorset", "Bournemouth, Christchurch and Poole"},
    "dumfriesshire": {"Dumfries and Galloway"},
    "durham": {"Durham", "Darlington"},
    "essex": {"Basildon", "Braintree", "Brentwood", "Castle Point", "Chelmsford",
              "Colchester", "Epping Forest", "Harlow", "Maldon", "Rochford",
              "Southend-On-Sea", "Tendring", "Thurrock", "Uttlesford"},
    "fife": {"Fife"},
    "gloucestershire": {"Cheltenham", "Cotswold", "Forest of Dean", "Gloucester City",
                        "Stroud", "Tewkesbury"},
    "greater manchester": {"Manchester", "Bolton", "Bury", "Oldham", "Rochdale", "Salford",
                           "Stockport", "Tameside", "Trafford", "Wigan"},
    "hampshire": {"Basingstoke and Deane", "East Hampshire", "Eastleigh", "Fareham",
                  "Gosport", "Hart", "Havant", "New Forest", "Rushmoor", "Southampton",
                  "Test Valley", "Winchester City", "Portsmouth"},
    "herefordshire": {"Herefordshire"},
    "hertfordshire": {"Broxbourne", "Dacorum", "East Hertfordshire", "Hertsmere",
                      "North Hertfordshire", "St Albans City", "Stevenage", "Three Rivers",
                      "Watford", "Welwyn Hatfield"},
    "isle of wight": {"Isle of Wight"},
    "kent": {"Ashford", "Canterbury City", "Dartford", "Dover", "Folkestone and Hythe",
             "Gravesham", "Maidstone", "Medway", "Sevenoaks", "Swale", "Thanet",
             "Tonbridge and Malling", "Tunbridge Wells"},
    "lancashire": {"Blackburn", "Blackpool", "Burnley", "Chorley", "Fylde", "Hyndburn",
                   "Lancaster City", "Pendle", "Preston", "Ribble Valley", "Rossendale",
                   "South Ribble", "West Lancashire", "Wyre"},
    "leicestershire": {"Leicester City", "Blaby", "Charnwood", "Harborough",
                       "Hinckley and Bosworth", "Melton", "North West Leicestershire",
                       "Oadby and Wigston"},
    "lincolnshire": {"Lincoln City", "Boston", "East Lindsey", "North Kesteven",
                     "South Holland", "South Kesteven", "West Lindsey",
                     "North Lincolnshire", "North East Lincolnshire"},
    "merseyside": {"Liverpool", "Knowsley", "Sefton", "St Helens", "Wirral"},
    "norfolk": {"Norwich City", "Breckland", "Broadland", "Great Yarmouth",
                "King's Lynn and West Norfolk", "North Norfolk", "South Norfolk"},
    "north wales": {"Anglesey", "Conwy", "Denbighshire", "Flintshire", "Gwynedd", "Wrexham"},
    "north yorkshire": {"North Yorkshire", "York"},
    "northumberland": {"Northumberland"},
    "northumbria": {"Northumberland", "Newcastle Upon Tyne", "Gateshead", "North Tyneside",
                    "South Tyneside", "Sunderland", "Durham"},
    "nottinghamshire": {"Nottingham City", "Ashfield", "Bassetlaw", "Broxtowe", "Gedling",
                        "Mansfield", "Newark and Sherwood", "Rushcliffe"},
    "oxfordshire": {"Oxford City", "Cherwell", "South Oxfordshire", "Vale of White Horse",
                    "West Oxfordshire"},
    "shropshire": {"Shropshire", "Telford and Wrekin Council"},
    "somerset": {"Somerset", "Bath and North East Somerset", "North Somerset"},
    "south wales": {"Cardiff", "Newport", "Swansea", "Bridgend", "Caerphilly",
                    "Merthyr Tydfil", "Monmouthshire", "Neath Port Talbot",
                    "Rhondda Cynon Taf", "Torfaen", "Vale of Glamorgan", "Blaenau Gwent"},
    "south yorkshire": {"Sheffield", "Barnsley", "Doncaster", "Rotherham"},
    "staffordshire": {"Stafford", "Cannock Chase", "East Staffordshire", "Lichfield",
                      "Newcastle-Under-Lyme", "South Staffordshire",
                      "Staffordshire Moorlands", "Stoke-On-Trent", "Tamworth"},
    "suffolk": {"Ipswich", "Babergh", "East Suffolk", "Mid Suffolk", "West Suffolk"},
    "surrey": {"Elmbridge", "Epsom and Ewell", "Guildford", "Mole Valley",
               "Reigate and Banstead", "Runnymede", "Spelthorne", "Surrey Heath",
               "Tandridge", "Waverley", "Woking"},
    "sussex": {"Adur", "Arun", "Brighton and Hove", "Chichester", "Crawley", "Eastbourne",
               "Hastings", "Horsham", "Lewes", "Mid Sussex", "Rother", "Wealden",
               "Worthing"},
    "tyne and wear": {"Newcastle Upon Tyne", "Gateshead", "North Tyneside", "South Tyneside",
                      "Sunderland"},
    "west yorkshire": {"Leeds", "Bradford", "Calderdale", "Kirklees", "Wakefield"},
    "wiltshire": {"Wiltshire", "Swindon"},
    "worcestershire": {"Worcester City", "Bromsgrove", "Malvern Hills", "Redditch",
                       "Wychavon", "Wyre Forest"},
}

# Abbreviations suppliers write on their own websites.
ABBREVIATIONS = {
    "beds": "bedfordshire",
    "berks": "berkshire",
    "bucks": "buckinghamshire",
    "cambs": "cambridgeshire",
    "co down": "county down",
    "glos": "gloucestershire",
    "hants": "hampshire",
    "herts": "hertfordshire",
    "iow": "isle of wight",
    "lancs": "lancashire",
    "leics": "leicestershire",
    "lincs": "lincolnshire",
    "notts": "nottinghamshire",
    "oxon": "oxfordshire",
    "staffs": "staffordshire",
    "tyne & wear": "tyne and wear",
    "wilts": "wiltshire",
    "worcs": "worcestershire",
    "yorks": "yorkshire",
}

NATIONAL = {"uk", "united kingdom", "nationwide", "national", "uk-wide", "whole uk",
            "gb", "britain", "great britain", "all"}


def normalise(name: str) -> str:
    """Lower-case, strip a leading 'the', collapse whitespace and punctuation."""
    s = (name or "").strip().lower()
    s = re.sub(r"^the\s+", "", s)
    s = re.sub(r"[.,]", "", s)
    s = re.sub(r"\s+", " ", s)
    return s


def all_authorities(conn: sqlite3.Connection) -> set[str]:
    return {row[0] for row in conn.execute("SELECT name FROM authorities")}


def regions(conn: sqlite3.Connection) -> dict[str, set[str]]:
    """region_name -> the authorities in it, read from the database."""
    out: dict[str, set[str]] = {}
    for name, region in conn.execute("SELECT name, region_name FROM authorities"):
        if region:
            out.setdefault(region, set()).add(name)
    return out


def resolve(conn: sqlite3.Connection, name: str) -> set[str]:
    """A place name -> the authorities it covers.

    Raises UnknownTerritory rather than guessing. A report quietly built on a
    half-understood territory is worse than one that didn't get sent, because
    nobody finds out until the customer reads it.
    """
    key = normalise(name)
    if not key:
        raise UnknownTerritory("no territory given")
    if key in NATIONAL:
        return all_authorities(conn)
    key = ABBREVIATIONS.get(key, key)
    if key in REGION_SYNONYMS:
        by_region = regions(conn)
        region = REGION_SYNONYMS[key]
        if region not in by_region:
            raise UnknownTerritory(
                "region %r is not present in the authorities table" % region
            )
        return set(by_region[region])
    if key in COUNTIES:
        return set(COUNTIES[key])
    if key in CITIES:
        return set(CITIES[key])
    # An exact authority name is always allowed, so anything in the database
    # can be asked for even if the gazetteer has never heard of it.
    for authority in all_authorities(conn):
        if normalise(authority) == key:
            return {authority}
    raise UnknownTerritory(
        "don't know the territory %r -- add it to fsa_pipeline/territories.py "
        "or name a local authority exactly as the authorities table spells it" % name
    )


def resolve_many(conn: sqlite3.Connection, names: list[str]) -> set[str]:
    """Union of several places: a supplier covering 'Devon and Cornwall'."""
    out: set[str] = set()
    for name in names:
        out |= resolve(conn, name)
    return out


def validate(conn: sqlite3.Connection) -> list[str]:
    """Every authority named in the gazetteer must exist in the database.

    Councils reorganise -- Cumbria became Cumberland and Westmorland and Furness
    in 2023 -- so this is a real failure mode, not a theoretical one. Returns
    the names that no longer resolve.
    """
    known = all_authorities(conn)
    named = set()
    for mapping in (CITIES, COUNTIES):
        for authorities in mapping.values():
            named |= authorities
    return sorted(named - known)
