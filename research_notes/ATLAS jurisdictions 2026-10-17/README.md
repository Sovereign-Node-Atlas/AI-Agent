# ATLAS jurisdictions, 2026-10-17

Research record for task J1. On 2026-10-17 the Principal said their main locations are VIC, NSW and QLD, and that
ATLAS should cover Australian Standards, regulations and Acts across the full spectrum of their work. This folder
holds the verified jurisdiction table the domain profiles will use. Research only: no profile, card or baseline
file was changed.

| File | Contents |
|---|---|
| `jurisdictions.md` | The table, grouped by subject (43 subject rows, 202 entries, all VERIFIED). Then free regulator documents, Australian Standards (number, edition and title only), which rows apply to which profile, and the gaps. |
| `evidence.tsv` | One line per table entry: row, jurisdiction, exact title or body, official URL, and how it was checked. |

## Method

1. Read all 36 profiles in `scripts/day1/config/domains/` and their cards in `cards/`. Listed every state-level
   law, regulator, tribunal or scheme they name or plainly need.
2. Checked every title live on 2026-10-17 on the official site:
   - VIC: legislation.vic.gov.au (site search index, then the in-force page heading).
   - QLD: legislation.qld.gov.au (site database, then the in-force page heading).
   - Commonwealth: the Federal Register of Legislation's public data service (exact name, status in force).
   - NSW: legislation.nsw.gov.au in-force feed (see the limit below).
   - Regulators and tribunals: their own sites. Australian Standards: the Standards Australia store listing.
3. Left out anything that could not be opened. "No direct equivalent" notes rest on official title searches.

Claims carry VERIFIED, INFERRED or UNVERIFIED. Only titles, years, jurisdictions and in-force status are VERIFIED;
the role of each Act is INFERRED unless the note says otherwise.

## Main limit

legislation.nsw.gov.au blocked automated access with a bot challenge on every path except its feeds. Only NSW
titles republished this week could be verified, so most NSW Act cells are empty (36 of 43 rows). The fix is a person
checking those titles in an ordinary browser, or the live ATLAS reading the NSW feed weekly. Several regulator
sites (ATO, ACCC, Fair Work Ombudsman, Safe Work Australia and others) also refused automated access. See
`jurisdictions.md` Section 6.

## Not decided

Two proposals still await the Principal: spreading the 16 Australian-law test tasks across the Commonwealth, VIC,
NSW and QLD, and adding about 10 Australian tax and finance tasks. This folder assumes neither.
