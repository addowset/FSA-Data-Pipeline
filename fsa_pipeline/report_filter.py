"""Turn a raw export into a list a supplier would actually pay for.

export_csv.py ships everything the pipeline classified, which is the right
default for a database export and the wrong one for a customer. On the week to
2026-10-22 the raw count for one territory was 377; the number a coffee roaster
could do anything with was 112. Sending the 377 would put a figure in the email
that the attachment contradicts by a factor of three, which is the one error
the recipient is guaranteed to notice.

Six filters, in rough order of how much they remove:

  classification    - NEW_VENUE or OPERATOR_CHANGE only. UNKNOWN means the
                      classifier could not decide whether anything happened,
                      and "we are not sure this is new" is not a product.
  venue type        - a roaster sells to cafes, restaurants, pubs and hotels. A
                      waste contractor also wants takeaways, shops and mobile
                      caterers. One audience's noise is another's lead, so this
                      is a parameter, not a constant.
  address           - no full address, no visit. address_completeness already
                      computes this; nothing was filtering on it.
  chains            - a Costa opening is not a lead for a wholesale roaster;
                      the buying decision was made in a head office years ago.
  institutions      - school, hospital and prison kitchens are not open trade.
                      NOISE_BUSINESS_TYPES catches the ones typed as such; this
                      catches the ones typed as a cafe sitting inside a school.
  FHRSID re-issues  - a council re-registering the same business under a new ID
                      looks exactly like a takeover until you read the names.

Everything here is export scope. Nothing is dropped from the database and
nothing changes how a record was classified.
"""
from __future__ import annotations

import re

from fsa_pipeline.csv_export import address_completeness, join_address

# --- venue sets per audience ------------------------------------------------

CORE_VENUES = {
    "Restaurant/Cafe/Canteen",
    "Pub/bar/nightclub",
    "Hotel/bed & breakfast/guest house",
    "Other catering premises",
}
AUDIENCES = {
    "roaster": CORE_VENUES,
    "broker": CORE_VENUES | {"Takeaway/sandwich shop"},
    "waste": CORE_VENUES | {
        "Takeaway/sandwich shop",
        "Retailers - other",
        "Retailers - supermarkets/hypermarkets",
        "Mobile caterer",
    },
}
AUDIENCES["all"] = AUDIENCES["waste"]

# --- chains -----------------------------------------------------------------
#
# Two lists, because one matching rule does not fit both halves.
#
# CHAIN_STEMS are long and unambiguous, matched against the trading name with
# every non-alphanumeric character stripped. That is what catches the FHRS
# record "Canteen Flaxby Produce, WM MORRISON PRODUCE LTD" -- a word-boundary
# match on "morrisons" misses it, because the register spells the company
# "MORRISON" and buries it in the address line.
#
# CHAIN_WORDS are short enough to appear inside ordinary words -- "spar" is in
# "sparrow", "nisa" in "Danisa" -- so they are matched on word boundaries only.
# Getting this wrong deletes a real independent from a customer's list, which
# is a worse failure than leaving a chain in it.

CHAIN_STEMS = {
    "greggs", "wagamama", "pizzaexpress", "mcdonald", "costacoffee", "starbucks",
    "papajohn", "burgerking", "nandos", "pretamanger", "caffenero",
    "cafenero", "fiveguys", "sainsbury", "morrison", "waitrose", "marksandspencer",
    "greeneking", "marstons", "wetherspoon", "premierinn", "travelodge",
    "holidayinn", "marriott", "tobycarvery", "harvester", "beefeater",
    "brewersfayre", "bellaitalia", "askitalian", "frankieandbennys", "chiquito",
    "pizzahut", "compassgroup", "aramark", "sodexo", "chartwells", "caterlink",
    "allianceinpartnership", "costcutter", "mccoll",
}
CHAIN_WORDS = {
    "tesco", "asda", "aldi", "lidl", "spar", "londis", "nisa", "budgens",
    "one stop", "subway", "kfc", "itsu", "wasabi", "zizzi", "elior", "hilton",
    "costa", "dominos",
}

INSTITUTION = re.compile(
    r"\b(school|college|academy|primary|secondary|nursery|university|hospital|"
    r"prison|hmp|barracks|care home|nursing home)\b",
    re.I,
)


def _squash(value: str | None) -> str:
    """Lower-case and strip everything that is not a letter or digit."""
    return re.sub(r"[^a-z0-9]", "", (value or "").lower())


# Words that carry no identity and differ freely between two spellings of the
# same business. "Pinot and Picasso" re-registering as "Pinot & Picasso
# Liverpool" is the same shop; without this the ampersand alone defeats the
# containment test and the pair ships to a customer as a change of hands.
_NOISE_WORDS = re.compile(r"\b(and|the|ltd|limited|plc|llp|co|company|uk)\b", re.I)


def _squash_name(value: str | None) -> str:
    """Squash for comparing two spellings of one trading name."""
    text = (value or "").lower().replace("&", " and ")
    text = _NOISE_WORDS.sub(" ", text)
    return re.sub(r"[^a-z0-9]", "", text)


def is_chain(business_name: str | None, address: str | None = None) -> bool:
    """True if this looks like a branch of a national operator.

    The address is checked as well as the name because the register routinely
    puts the operating company in the address lines -- a factory canteen shows
    up as a plausible independent until you read where it is.
    """
    squashed_name = _squash(business_name)
    squashed_both = squashed_name + _squash(address)
    if any(stem in squashed_both for stem in CHAIN_STEMS):
        return True
    spaced = " %s " % re.sub(r"[^a-z0-9 ]", " ", (business_name or "").lower())
    spaced = re.sub(r"\s+", " ", spaced)
    return any(" %s " % word in spaced for word in CHAIN_WORDS)


def is_institution(business_name: str | None, address: str | None) -> bool:
    """A kitchen inside a school or hospital, typed as a cafe."""
    return bool(INSTITUTION.search(business_name or "")) or bool(
        INSTITUTION.search(address or "")
    )


def is_reissue(classification: str, business_name: str | None,
               previous_business_name: str | None) -> bool:
    """A council re-registering the same business under a fresh FHRSID.

    Containment rather than equality, because the re-registration is rarely
    byte-identical: "The Rutland Hub" replacing "Rutland Hub", "Jacksons"
    replacing "Jacksons Fish Bar". If either normalised name contains the
    other, nobody changed hands.
    """
    if classification != "OPERATOR_CHANGE":
        return False
    new = _squash_name(business_name)
    old = _squash_name(previous_business_name)
    if not new or not old:
        return False
    return new in old or old in new


# --- company-match corroboration -------------------------------------------
#
# Only address-corroborated matches get company columns in a customer file. Of
# 102 company numbers checked by hand on the supplier list, four were the wrong
# company -- a dissolved Surrey entity attached to a Norfolk business, that
# kind of thing. A plausible but wrong company name is worse than a blank cell,
# because the customer cannot tell it is wrong and may act on it.

CORROBORATION = (
    "registered address is an exact match for this establishment's address",
    "qualified via exact registered-address match",
)
NOT_CORROBORATED = "address isn't corroborat"


def company_is_corroborated(reason: str | None) -> bool:
    text = (reason or "").lower()
    if NOT_CORROBORATED in text:
        return False
    return any(phrase in text for phrase in CORROBORATION)


# --- the filter itself ------------------------------------------------------

SELLABLE_CLASSIFICATIONS = ("NEW_VENUE", "OPERATOR_CHANGE")

REASONS = ("outside territory", "unclassified", "venue type", "incomplete address",
           "chain", "institution", "re-issue")


def filter_records(records, *, audience: str = "all",
                   authorities: set[str] | None = None):
    """Return (kept, removed_counts).

    removed_counts is a plain dict keyed by REASONS, so the report can say what
    it took out and the number is auditable rather than asserted.
    """
    if audience not in AUDIENCES:
        raise ValueError(
            "unknown audience %r -- one of %s" % (audience, ", ".join(sorted(AUDIENCES)))
        )
    keep_types = AUDIENCES[audience]
    counts = {reason: 0 for reason in REASONS}
    kept = []
    for record in records:
        if authorities is not None and record["authority_name"] not in authorities:
            counts["outside territory"] += 1
            continue
        if record["classification"] not in SELLABLE_CLASSIFICATIONS:
            counts["unclassified"] += 1
            continue
        if record["business_type"] not in keep_types:
            counts["venue type"] += 1
            continue
        address = join_address(
            record["address_line_1"], record["address_line_2"],
            record["address_line_3"], record["address_line_4"],
        )
        postcode = record["postcode_from_live_api"] or record["post_code"]
        if address_completeness(
            record["address_line_1"], record["address_line_2"],
            record["address_line_3"], record["address_line_4"], postcode,
        ) != "full":
            counts["incomplete address"] += 1
            continue
        if is_chain(record["business_name"], address):
            counts["chain"] += 1
            continue
        if is_institution(record["business_name"], address):
            counts["institution"] += 1
            continue
        if is_reissue(record["classification"], record["business_name"],
                      record["previous_business_name"]):
            counts["re-issue"] += 1
            continue
        kept.append(record)
    return kept, counts
