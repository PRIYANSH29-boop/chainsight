"""Filing-level XBRL segment extractor for JPMorgan-style SEC filings.

Approved spike scope (3 Oct 2026): parse the Business Segments (Details)
R-file of a 10-K, keep the canonical segment members, exclude one-off
members (Visa, Apple Card) and percentage rows, normalize $-millions to
base units, and tag every row with regime + consolidation role + provenance.

Evidence base: FY2025 10-K accn 0001628280-26-008131 (R223) and FY2023 10-K
accn 0000019617-24-000225 (R217), inspected natively 2026-10-03.

Heuristics documented as limitations (JPM-style, calendar fiscal year):
- annual period-ends are the header dates falling on Dec. 31;
- a metric row is kept only if its value count equals the annual-period count;
- section detection is text-structural ("Operating Segments" + display name
  + "Segment Reporting Information [Line Items]"); unknown extra member lines
  (e.g. "Apple Credit Card Portfolio") mark the section excluded;
- R-file report numbers are NEVER hardcoded: locate via FilingSummary.xml.

Read-only by design: the live-fetch helper never writes files.
"""
import os
import re
import urllib.request

# ---------------------------------------------------------------- canon maps
DISPLAY_TO_MEMBER = {
    "3SEG": {
        "Consumer & Community Banking": "jpm_ConsumerCommunityBankingMember",
        "Commercial & Investment Bank": "jpm_CommercialAndInvestmentBankMember",
        "Asset & Wealth Management": "jpm_AssetandWealthManagementSegmentMember",
    },
    "4SEG": {
        "Consumer & Community Banking": "jpm_ConsumerCommunityBankingMember",
        "Corporate & Investment Bank": "jpm_CorporateAndInvestmentBankMember",
        "Commercial Banking": "jpm_CommercialBankingMember",
        "Asset & Wealth Management": "jpm_AssetandWealthManagementSegmentMember",
    },
}
FOUR_ONLY = {"Corporate & Investment Bank", "Commercial Banking"}
ALL_DISPLAYS = set(DISPLAY_TO_MEMBER["3SEG"]) | set(DISPLAY_TO_MEMBER["4SEG"])

SEGMENT_AXIS = "us-gaap_StatementBusinessSegmentsAxis"
CONSOL_AXIS = "srt_ConsolidationItemsAxis"

METRIC_MAP = {  # label -> (metric, basis)
    "Total net revenue": ("total_net_revenue", "duration"),
    "Income/(loss) before income tax expense/(benefit)": ("pretax_income", "duration"),
    "Net income": ("net_income", "duration"),
    "Total assets": ("total_assets", "instant"),
    "Net interest income": ("net_interest_income", "duration"),
    "Noninterest revenue": ("noninterest_revenue", "duration"),
}

LINE_ITEMS = "Segment Reporting Information [Line Items]"
DATE_RE = re.compile(r"^([A-Z][a-z]{2})\. (\d{1,2}), (\d{4})$")
VAL_RE = re.compile(r"^\$?\s?\(?\$?\s?-?[\d][\d,]*\)?$")
FOOT_RE = re.compile(r"^\[\d+\]$")

MONTHS = {"Jan": 1, "Feb": 2, "Mar": 3, "Apr": 4, "May": 5, "Jun": 6,
          "Jul": 7, "Aug": 8, "Sep": 9, "Oct": 10, "Nov": 11, "Dec": 12}


DEFREF_MARK = "@@DEFREF "


def _strip_html(html_text):
    t = re.sub(r"<script.*?</script>", " ", html_text, flags=re.S | re.I)
    # live R-files carry the axis/member in the header anchor's defref_ attr;
    # surface it as a marker line immediately before the anchor's text
    t = re.sub(r"<a[^>]*defref_([^'\"]+)['\"][^>]*>",
               lambda m: "\n" + DEFREF_MARK + m.group(1) + "@@\n", t)
    t = re.sub(r"<[^>]+>", "\n", t)
    t = t.replace("&#160;", " ").replace("&amp;", "&").replace("&#8211;", "-")
    return [ln.strip() for ln in t.split("\n") if ln.strip()]


def _parse_value(tok):
    neg = "(" in tok
    num = re.sub(r"[^\d]", "", tok)
    if not num:
        return None
    v = int(num)
    return -v if neg else v


def locate_segment_report(filing_summary_xml):
    """Return HtmlFileName of the Business Segments (Details) report.
    Never hardcodes R-numbers; filters out Narrative/Goodwill/Loans/Tables."""
    reports = re.findall(
        r"<Report[^>]*>(.*?)</Report>", filing_summary_xml, flags=re.S)
    candidates = []
    for block in reports:
        long_m = re.search(r"<LongName>(.*?)</LongName>", block, flags=re.S)
        file_m = re.search(r"<HtmlFileName>(.*?)</HtmlFileName>", block, flags=re.S)
        if not long_m or not file_m:
            continue
        name = long_m.group(1)
        if "Business Segments" not in name or "(Details)" not in name:
            continue
        if any(bad in name for bad in ("Narrative", "Goodwill", "Loans", "Portfolio", "Tables")):
            continue
        candidates.append((name, file_m.group(1).strip()))
    if not candidates:
        return None
    for name, fn in candidates:  # prefer the Summary details report
        if "Summary" in name:
            return fn
    return candidates[0][1]


def _annual_period_ends(lines):
    dates = []
    for ln in lines[:60]:  # header region only
        m = DATE_RE.match(ln)
        if m:
            mon, day, yr = m.groups()
            dates.append((int(yr), MONTHS.get(mon, 0), int(day)))
    annual = [d for d in dates if d[1] == 12 and d[2] == 31]  # JPM-style Dec-31 FYE
    return ["%04d-%02d-%02d" % d for d in annual]


def _section_from_context(context, regime, defref=None):
    """Classify a section from the member lines collected before [Line Items].
    When the header anchor carried a defref_, prefer it: a
    StatementBusinessSegmentsAxis defref yields the member directly; a
    ConsolidationItemsAxis defref confirms the role."""
    member_override = role_hint = None
    if defref:
        if "StatementBusinessSegmentsAxis=" in defref:
            member_override = defref.split("StatementBusinessSegmentsAxis=")[-1]
            role_hint = "operating"
        elif defref.endswith("OperatingSegmentsMember"):
            role_hint = "operating"
        elif defref.endswith("CorporateNonSegmentMember"):
            role_hint = "corporate"
        elif defref.endswith("MaterialReconcilingItemsMember"):
            role_hint = "reconciling"
    ctx = [c for c in context if c]  # keep order
    if ctx == ["Corporate"]:
        return dict(role="corporate", display="Corporate",
                    member="us-gaap_CorporateNonSegmentMember",
                    dimension=CONSOL_AXIS, excluded=False)
    if ctx == ["Reconciling Items"]:
        return dict(role="reconciling", display="Reconciling Items",
                    member="us-gaap_MaterialReconcilingItemsMember",
                    dimension=CONSOL_AXIS, excluded=False)
    displays = [c for c in ctx if c in ALL_DISPLAYS]
    extras = [c for c in ctx if c not in ALL_DISPLAYS]
    if len(displays) == 1 and not extras:
        d = displays[0]
        return dict(role="operating", display=d,
                    member=member_override
                    or DISPLAY_TO_MEMBER[regime].get(d, "UNMAPPED_" + d),
                    dimension=SEGMENT_AXIS, excluded=False)
    if member_override and len(ctx) == 1 and role_hint == "operating":
        # display name unknown to the canon map, but the filing's own defref
        # names the member authoritatively
        return dict(role="operating", display=ctx[0], member=member_override,
                    dimension=SEGMENT_AXIS, excluded=False)
    # one-off member (e.g. Apple Credit Card Portfolio) or unknown -> excluded
    return dict(role="excluded", display=" / ".join(ctx) or "(unknown)",
                member=None, dimension=None, excluded=True)


def parse_r_file(html_text, accession, filed_date, source_reference,
                 company="JPMORGAN CHASE & CO", cik=19617):
    """Parse a Business Segments (Details) R-file into provenance-rich rows."""
    lines = _strip_html(html_text)
    period_ends = _annual_period_ends(lines)
    n = len(period_ends)
    if n == 0:
        raise ValueError("no Dec-31 annual period ends found in header")

    # regime from the display names present anywhere in the file
    # (names may appear standalone or inside pipe-joined header cells)
    def _parts(ln):
        return [p.strip() for p in ln.split(" | ")] if " | " in ln else [ln]
    regime = ("4SEG" if any(p in FOUR_ONLY for ln in lines for p in _parts(ln))
              else "3SEG")

    rows, warnings = [], []
    context = None            # collecting member lines after a trigger
    ctx_defref = None         # defref captured at the section header
    current = None            # active section dict
    first_lineitems_seen = False
    pending_defref = None

    i = 0
    while i < len(lines):
        ln = lines[i]
        if ln.startswith(DEFREF_MARK) and ln.endswith("@@"):
            pending_defref = ln[len(DEFREF_MARK):-2]
            i += 1
            continue
        this_defref, pending_defref = pending_defref, None
        if " | " in ln:
            # live R-file form: one header cell, pipe-joined, e.g.
            # "Operating Segments | Consumer & Community Banking"
            parts = [p.strip() for p in ln.split(" | ")]
            if parts[0] == "Operating Segments":
                context, ctx_defref = parts[1:], this_defref
            else:  # e.g. "VISA | Class C Common Shares" -> excluded section
                context, ctx_defref = parts, this_defref
            i += 1
            continue
        if ln == "Operating Segments":
            context, ctx_defref = [], this_defref
        elif ln == LINE_ITEMS:
            if context is not None:
                current = _section_from_context(context, regime, ctx_defref)
                context = None
                ctx_defref = None
            elif not first_lineitems_seen:
                current = dict(role="consolidated", display="Consolidated",
                               member=None, dimension=None, excluded=False)
            else:  # contextless later section (e.g. VISA one-off) -> excluded
                current = dict(role="excluded", display="(contextless)",
                               member=None, dimension=None, excluded=True)
            first_lineitems_seen = True
        elif context is not None:
            context.append(ln)
        elif ln in ("Corporate", "Reconciling Items"):
            context, ctx_defref = [ln], this_defref
        elif current and not current["excluded"] and ln in METRIC_MAP:
            metric, basis = METRIC_MAP[ln]
            vals, pct_row, j = [], False, i + 1
            while j < len(lines):
                tok = lines[j]
                if FOOT_RE.match(tok) or tok == "$":
                    j += 1
                    continue
                if tok.endswith("%"):
                    pct_row = True
                    j += 1
                    continue
                if VAL_RE.match(tok):
                    vals.append(_parse_value(tok))
                    j += 1
                    continue
                break
            if pct_row:
                warnings.append("pct row skipped: %s @%s" % (ln, current["display"]))
            elif len(vals) == n:
                for k, v in enumerate(vals):
                    end = period_ends[k]
                    start = end[:4] + "-01-01" if basis == "duration" else None
                    rows.append(dict(
                        cik=cik, company=company,
                        segment=current["display"], member=current["member"],
                        dimension=current["dimension"],
                        consolidation_role=current["role"],
                        segment_regime=regime,
                        metric=metric, label=ln,
                        value_millions=v, scale="millions", unit="USD",
                        value_base=v * 1_000_000,
                        period_start=start, period_end=end,
                        filed_date=filed_date, accession=accession,
                        source_reference=source_reference,
                        notes="",
                    ))
            elif vals:
                warnings.append("value-count mismatch (%d vs %d periods): %s @%s"
                                % (len(vals), n, ln, current["display"]))
            i = j - 1
        i += 1
    return rows, warnings


# ------------------------------------------------- read-only live fetch helper
def fetch_text(url, user_agent=None):
    """Native-run-only, read-only GET. Never writes files. SEC requires a
    real-contact User-Agent (set SEC_USER_AGENT in .env / environment)."""
    ua = user_agent or os.environ.get("SEC_USER_AGENT", "").strip('"') \
        or "chainsight research (contact unset)"
    req = urllib.request.Request(url, headers={"User-Agent": ua})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read().decode("utf-8", errors="replace")


def filing_summary_url(cik, accession):
    return "https://www.sec.gov/Archives/edgar/data/%d/%s/FilingSummary.xml" % (
        int(cik), accession.replace("-", ""))
