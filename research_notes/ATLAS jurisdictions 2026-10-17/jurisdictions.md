# ATLAS jurisdiction table: Commonwealth, VIC, NSW and QLD

Date: 2026-10-17. Task J1 (research only). Baseline: `docs/ATLAS_FRAMEWORK_REVIEW.md` v0.4.0.

This is the verified list of state-level laws, regulators, tribunals and schemes that the 36 domain profiles name
or plainly need, with the VIC, NSW and QLD equivalent and the Commonwealth law where one applies. The Principal
works mainly in VIC, NSW and QLD (statement of 2026-10-17). Domain profiles and cards can cite these rows.

## 1. How to read this

**Count.** 43 subject rows. 202 table entries (196 unique titles or bodies; a few appear
under more than one subject). Every entry in the tables is VERIFIED. Anything that could not be checked live was
left out and is listed in Section 6.

By jurisdiction: Commonwealth 39, VIC 73, NSW 25, QLD 65.

**What VERIFIED means here.** The exact short title, year and jurisdiction were read live on an official site on
2026-10-17, and the instrument was in force. The letter after VERIFIED says how:

| Code | Check | Entries |
|---|---|---|
| L | Opened the in-force page on legislation.vic.gov.au or legislation.qld.gov.au. Its heading matched the title exactly. The title was first found in the site's own database. | 114 |
| A | Read the title record from the Federal Register of Legislation's public data service (api.prod.legislation.gov.au): exact name, collection "Act", status "InForce". The URL given is the register's text page. | 33 |
| F | Listed with its in-force id in the NSW in-force feed on legislation.nsw.gov.au (this week's republications). | 9 |
| P | Read in the authorised Act text (PDF) on legislation.vic.gov.au. | 2 |
| W | Opened the body's own official site (HTTP 200). Its name was in the page title, heading or text. | 44 |

**Notes column.** The note says what the entry is for. Unless marked VERIFIED, a note is INFERRED: the role of an
Act (which part covers what) comes from its title and general knowledge, not from reading the Act. Placing an
entry under a subject is also INFERRED unless its note says otherwise.

**"No direct equivalent".** Where a row says a jurisdiction has no equivalent, the claim rests on a title search of
that jurisdiction's official database for in-force Acts (and, for VIC and QLD, regulations) on 2026-10-17. A title
search cannot rule out a provision buried in an Act with a different title. Such notes say "VERIFIED" only for the
search result itself.

**NSW.** legislation.nsw.gov.au served a bot challenge ("Just a moment...") to every automated request except its
feeds, so NSW Acts could be verified only if they were republished this week. Most NSW Act cells are therefore
empty and say "not verified". No NSW title is given unless it was verified. See Gap N1. I did not try to get round
the challenge.

## 2. The table, grouped by subject

Row groups: A construction and building; B planning, environment and heritage; C property, strata, leasing and
tenancy; D state taxes; E employment and safety; F privacy and surveillance; G family, estate and guardianship;
H consumer law, tribunals and defamation; I transport, vessels and energy; J health, poisons, gene technology and
biosecurity; K public affairs, charities and children.

### A1. Security of payment (construction)

Profiles: 06, 07

| Jur. | Exact title or body | Official URL | Status | Note |
|---|---|---|---|---|
| VIC | Building and Construction Industry Security of Payment Act 2002 | https://www.legislation.vic.gov.au/in-force/acts/building-and-construction-industry-security-payment-act-2002 | VERIFIED (L) |  |
| QLD | Building Industry Fairness (Security of Payment) Act 2017 | https://www.legislation.qld.gov.au/view/html/inforce/current/act-2017-043 | VERIFIED (L) |  |

- Cth: no direct equivalent. VERIFIED: no in-force Commonwealth Act has "Security of Payment" in its title (register title search).
- NSW Act: not verified (see Gap N1). No title is given here on purpose.

### A2. Domestic (home) building contracts

Profiles: 06, 07, 14

| Jur. | Exact title or body | Official URL | Status | Note |
|---|---|---|---|---|
| VIC | Domestic Building Contracts Act 1995 | https://www.legislation.vic.gov.au/in-force/acts/domestic-building-contracts-act-1995 | VERIFIED (L) |  |
| QLD | Queensland Building and Construction Commission Act 1991 | https://www.legislation.qld.gov.au/view/html/inforce/current/act-1991-098 | VERIFIED (L) | INFERRED: QLD domestic building contract rules and builder licensing sit in this Act. |

- Cth: no direct equivalent. VERIFIED: no in-force Commonwealth Act with "Home Building" or "Domestic Building" in its title.
- NSW Act: not verified (see Gap N1). No title is given here on purpose.

### A3. Building control: permits, certification, plumbing

Profiles: 06, 07, 08

| Jur. | Exact title or body | Official URL | Status | Note |
|---|---|---|---|---|
| VIC | Building Act 1993 | https://www.legislation.vic.gov.au/in-force/acts/building-act-1993 | VERIFIED (L) |  |
| VIC | Building Regulations 2018 | https://www.legislation.vic.gov.au/in-force/statutory-rules/building-regulations-2018 | VERIFIED (L) |  |
| QLD | Building Act 1975 | https://www.legislation.qld.gov.au/view/html/inforce/current/act-1975-011 | VERIFIED (L) |  |
| QLD | Building Regulation 2021 | https://www.legislation.qld.gov.au/view/html/inforce/current/sl-2021-0126 | VERIFIED (L) |  |
| QLD | Plumbing and Drainage Act 2018 | https://www.legislation.qld.gov.au/view/html/inforce/current/act-2018-017 | VERIFIED (L) |  |

- Cth: no building-control Act (INFERRED; not searched). The technical code is the NCC (see Section 3), which each state adopts on its own date.
- NSW Act: not verified (see Gap N1). No title is given here on purpose.

### A4. Building and engineering practitioner registration

Profiles: 06, 07, 08, 09

| Jur. | Exact title or body | Official URL | Status | Note |
|---|---|---|---|---|
| VIC | Building Act 1993 | https://www.legislation.vic.gov.au/in-force/acts/building-act-1993 | VERIFIED (L) | INFERRED: building practitioner registration sits in this Act. |
| VIC | Professional Engineers Registration Act 2019 | https://www.legislation.vic.gov.au/in-force/acts/professional-engineers-registration-act-2019 | VERIFIED (L) |  |
| QLD | Queensland Building and Construction Commission Act 1991 | https://www.legislation.qld.gov.au/view/html/inforce/current/act-1991-098 | VERIFIED (L) | INFERRED: contractor licensing. |
| QLD | Professional Engineers Act 2002 | https://www.legislation.qld.gov.au/view/html/inforce/current/act-2002-054 | VERIFIED (L) |  |

- Profile 06 names a NSW design and building practitioners statute. Its title could not be checked live, so it is not repeated here (Gap N1).
- QLD engineers' board site (bpeq.qld.gov.au) blocked automated access (Gap B1).

### A5. Building regulators and building tribunals

Profiles: 06, 07, 08

| Jur. | Exact title or body | Official URL | Status | Note |
|---|---|---|---|---|
| Cth | Australian Building Codes Board | https://www.abcb.gov.au/ | VERIFIED (W) | Writes the NCC. Not a regulator (INFERRED). |
| VIC | Victorian Building Authority | https://content.legislation.vic.gov.au/sites/default/files/2026-09/93-126aa152-authorised.pdf | VERIFIED (P) | VERIFIED: s 193 of the Building Act 1993, authorised version 152 (the current version on 2026-10-17), says "The Victorian Building Authority is established." See Gap V1 on the name in use. |
| VIC | Building Appeals Board | https://content.legislation.vic.gov.au/sites/default/files/2026-09/93-126aa152-authorised.pdf | VERIFIED (P) | VERIFIED: established by s 166 of the Building Act 1993 (same version). |
| NSW | Building Commission NSW | https://www.nsw.gov.au/departments-and-agencies/building-commission | VERIFIED (W) |  |
| QLD | Queensland Building and Construction Commission | https://www.qbcc.qld.gov.au/ | VERIFIED (W) |  |

- Disputes also go to VCAT, NCAT and QCAT (row H2).

### A6. Electrical safety and electrical work

Profiles: 08, 09

| Jur. | Exact title or body | Official URL | Status | Note |
|---|---|---|---|---|
| VIC | Electricity Safety Act 1998 | https://www.legislation.vic.gov.au/in-force/acts/electricity-safety-act-1998 | VERIFIED (L) |  |
| VIC | Electricity Safety (General) Regulations 2019 | https://www.legislation.vic.gov.au/in-force/statutory-rules/electricity-safety-general-regulations-2019 | VERIFIED (L) |  |
| VIC | Energy Safe Victoria | https://www.energysafe.vic.gov.au/ | VERIFIED (W) |  |
| NSW | NSW Government "Electrical work" licensing page | https://www.nsw.gov.au/business-and-economy/licences-and-credentials/building-and-trade-licences-and-registrations/electrical | VERIFIED (W) |  |
| QLD | Electrical Safety Act 2002 | https://www.legislation.qld.gov.au/view/html/inforce/current/act-2002-042 | VERIFIED (L) |  |
| QLD | Electrical Safety Regulation 2026 | https://www.legislation.qld.gov.au/view/html/inforce/current/sl-2026-0113 | VERIFIED (L) | VERIFIED: the 2026 Regulation is the one in force; profiles do not name an older one. |
| QLD | Electrical Safety (Codes of Practice) Notice 2024 | https://www.legislation.qld.gov.au/view/html/inforce/current/sl-2024-0156 | VERIFIED (L) |  |

- Cth: no electrical safety Act (INFERRED; not searched).
- NSW Act: not verified (see Gap N1). No title is given here on purpose.
- QLD Electrical Safety Office pages (worksafe.qld.gov.au) blocked automated access (Gap B1).
- AS/NZS 3000 (wiring rules) is a licensed Standard: see Section 4.

### B1. Planning and development approval

Profiles: 07, 08, 30

| Jur. | Exact title or body | Official URL | Status | Note |
|---|---|---|---|---|
| Cth | Environment Protection and Biodiversity Conservation Act 1999 | https://www.legislation.gov.au/C2004A00485/latest/text | VERIFIED (A) | INFERRED: applies where a project touches matters of national environmental significance. |
| VIC | Planning and Environment Act 1987 | https://www.legislation.vic.gov.au/in-force/acts/planning-and-environment-act-1987 | VERIFIED (L) |  |
| NSW | Land and Environment Court of New South Wales | https://lec.nsw.gov.au/ | VERIFIED (W) |  |
| NSW | NSW Planning Portal (planningportal.nsw.gov.au) | https://www.planningportal.nsw.gov.au/stra | VERIFIED (W) |  |
| QLD | Planning Act 2016 | https://www.legislation.qld.gov.au/view/html/inforce/current/act-2016-025 | VERIFIED (L) |  |
| QLD | Planning and Environment Court Act 2016 | https://www.legislation.qld.gov.au/view/html/inforce/current/act-2016-026 | VERIFIED (L) |  |

- NSW Act: not verified (see Gap N1). No title is given here on purpose.
- Victoria Planning Provisions and ResCode (named in profile 08): not verified; planning.vic.gov.au blocked automated access and the planning-scheme site serves no readable text (Gap B1).

### B2. Environmental impact assessment (EIS / EES)

Profiles: 07

| Jur. | Exact title or body | Official URL | Status | Note |
|---|---|---|---|---|
| Cth | Environment Protection and Biodiversity Conservation Act 1999 | https://www.legislation.gov.au/C2004A00485/latest/text | VERIFIED (A) |  |
| VIC | Environment Effects Act 1978 | https://www.legislation.vic.gov.au/in-force/acts/environment-effects-act-1978 | VERIFIED (L) |  |
| QLD | State Development and Public Works Organisation Act 1971 | https://www.legislation.qld.gov.au/view/html/inforce/current/act-1971-055 | VERIFIED (L) | INFERRED: EIS for coordinated projects. |

- NSW Act: not verified (see Gap N1). No title is given here on purpose.

### B3. Environment protection and EPA licensing

Profiles: 07, 09, 35

| Jur. | Exact title or body | Official URL | Status | Note |
|---|---|---|---|---|
| VIC | Environment Protection Act 2017 | https://www.legislation.vic.gov.au/in-force/acts/environment-protection-act-2017 | VERIFIED (L) |  |
| VIC | Environment Protection Authority Victoria | https://www.epa.vic.gov.au/ | VERIFIED (W) |  |
| NSW | Protection of the Environment Operations (Clean Air) Regulation 2022 | https://legislation.nsw.gov.au/view/html/inforce/current/sl-2022-0811 | VERIFIED (F) |  |
| NSW | NSW Environment Protection Authority | https://www.epa.nsw.gov.au/ | VERIFIED (W) |  |
| QLD | Environmental Protection Act 1994 | https://www.legislation.qld.gov.au/view/html/inforce/current/act-1994-062 | VERIFIED (L) |  |

- NSW: only the Clean Air Regulation could be verified (it was republished this week). The parent NSW Act is not verified (Gap N1).
- QLD environment regulator: not checked.

### B4. Built heritage

Profiles: 07, 08

| Jur. | Exact title or body | Official URL | Status | Note |
|---|---|---|---|---|
| Cth | Environment Protection and Biodiversity Conservation Act 1999 | https://www.legislation.gov.au/C2004A00485/latest/text | VERIFIED (A) | INFERRED: national and Commonwealth heritage lists. |
| VIC | Heritage Act 2017 | https://www.legislation.vic.gov.au/in-force/acts/heritage-act-2017 | VERIFIED (L) |  |
| QLD | Queensland Heritage Act 1992 | https://www.legislation.qld.gov.au/view/html/inforce/current/act-1992-009 | VERIFIED (L) |  |

- NSW Act: not verified (see Gap N1). No title is given here on purpose.
- Heritage Victoria and Heritage NSW sites blocked automated access (Gap B1).

### B5. Aboriginal cultural heritage and native title (development sites)

Profiles: 07, 30

| Jur. | Exact title or body | Official URL | Status | Note |
|---|---|---|---|---|
| Cth | Native Title Act 1993 | https://www.legislation.gov.au/C2004A04665/latest/text | VERIFIED (A) |  |
| Cth | Aboriginal and Torres Strait Islander Heritage Protection Act 1984 | https://www.legislation.gov.au/C2004A02943/latest/text | VERIFIED (A) |  |
| VIC | Aboriginal Heritage Act 2006 | https://www.legislation.vic.gov.au/in-force/acts/aboriginal-heritage-act-2006 | VERIFIED (L) |  |
| NSW | National Parks and Wildlife Act 1974 | https://legislation.nsw.gov.au/view/html/inforce/current/act-1974-080 | VERIFIED (F) | INFERRED: NSW Aboriginal objects and places are protected under this Act. |
| NSW | Native Title (New South Wales) Act 1994 | https://legislation.nsw.gov.au/view/html/inforce/current/act-1994-045 | VERIFIED (F) |  |
| QLD | Aboriginal Cultural Heritage Act 2003 | https://www.legislation.qld.gov.au/view/html/inforce/current/act-2003-079 | VERIFIED (L) |  |
| QLD | Native Title (Queensland) Act 1993 | https://www.legislation.qld.gov.au/view/html/inforce/current/act-1993-085 | VERIFIED (L) |  |

- No profile names this subject. It is plainly needed for development and off-grid land work (INFERRED).

### B6. Water (bores, off-grid supply)

Profiles: 30

| Jur. | Exact title or body | Official URL | Status | Note |
|---|---|---|---|---|
| Cth | Water Act 2007 | https://www.legislation.gov.au/C2007A00137/latest/text | VERIFIED (A) | INFERRED: Murray–Darling Basin scope. |
| VIC | Water Act 1989 | https://www.legislation.vic.gov.au/in-force/acts/water-act-1989 | VERIFIED (L) |  |
| QLD | Water Act 2000 | https://www.legislation.qld.gov.au/view/html/inforce/current/act-2000-034 | VERIFIED (L) |  |

- NSW Act: not verified (see Gap N1). No title is given here on purpose.

### C1. Strata and owners corporations

Profiles: 06, 07, 19

| Jur. | Exact title or body | Official URL | Status | Note |
|---|---|---|---|---|
| VIC | Owners Corporations Act 2006 | https://www.legislation.vic.gov.au/in-force/acts/owners-corporations-act-2006 | VERIFIED (L) |  |
| VIC | Subdivision Act 1988 | https://www.legislation.vic.gov.au/in-force/acts/subdivision-act-1988 | VERIFIED (L) |  |
| QLD | Body Corporate and Community Management Act 1997 | https://www.legislation.qld.gov.au/view/html/inforce/current/act-1997-028 | VERIFIED (L) |  |
| QLD | Office of the Commissioner for Body Corporate and Community Management | https://www.qld.gov.au/housing/body-corporate | VERIFIED (W) |  |

- Cth: no direct equivalent. VERIFIED: no in-force Commonwealth Act with "Strata" or "Owners Corporation" in its title.
- Profile 06 names a NSW strata management statute. Its title could not be checked live (Gap N1).
- QLD: no in-force QLD Act or regulation has "Strata" in its title (VERIFIED, title search); QLD uses body corporate law.

### C2. Retail and commercial leases

Profiles: 06, 07

| Jur. | Exact title or body | Official URL | Status | Note |
|---|---|---|---|---|
| VIC | Retail Leases Act 2003 | https://www.legislation.vic.gov.au/in-force/acts/retail-leases-act-2003 | VERIFIED (L) |  |
| VIC | Victorian Small Business Commission | https://www.vsbc.vic.gov.au/ | VERIFIED (W) |  |
| QLD | Retail Shop Leases Act 1994 | https://www.legislation.qld.gov.au/view/html/inforce/current/act-1994-047 | VERIFIED (L) |  |
| QLD | Queensland Small Business Commissioner | https://www.qsbc.qld.gov.au/ | VERIFIED (W) |  |

- Cth: no direct equivalent. VERIFIED: no in-force Commonwealth Act with "Retail Lease" in its title.
- NSW Act: not verified (see Gap N1). No title is given here on purpose.
- NSW Small Business Commissioner site did not answer (Gap B1).

### C3. Residential tenancies

Profiles: 07, 14, 19

| Jur. | Exact title or body | Official URL | Status | Note |
|---|---|---|---|---|
| VIC | Residential Tenancies Act 1997 | https://www.legislation.vic.gov.au/in-force/acts/residential-tenancies-act-1997 | VERIFIED (L) |  |
| VIC | Consumer Affairs Victoria | https://www.consumer.vic.gov.au/ | VERIFIED (W) |  |
| NSW | NSW Fair Trading | https://www.nsw.gov.au/departments-and-agencies/fair-trading | VERIFIED (W) |  |
| QLD | Residential Tenancies and Rooming Accommodation Act 2008 | https://www.legislation.qld.gov.au/view/html/inforce/current/act-2008-073 | VERIFIED (L) |  |
| QLD | Residential Tenancies Authority | https://www.rta.qld.gov.au/ | VERIFIED (W) |  |

- Cth: no direct equivalent. VERIFIED: no in-force Commonwealth Act with "Residential Tenanc" in its title.
- NSW Act: not verified (see Gap N1). No title is given here on purpose.

### C4. Short-term rental accommodation

Profiles: 19

| Jur. | Exact title or body | Official URL | Status | Note |
|---|---|---|---|---|
| VIC | Short Stay Levy Act 2024 | https://www.legislation.vic.gov.au/in-force/acts/short-stay-levy-act-2024 | VERIFIED (L) |  |
| VIC | State Revenue Office "Short stay levy" page | https://www.sro.vic.gov.au/owning-property/short-stay-levy | VERIFIED (W) |  |
| NSW | NSW Government "Short-term rental accommodation" page | https://www.nsw.gov.au/housing-and-construction/short-term-rental-accommodation | VERIFIED (W) | VERIFIED: the page describes "a mandatory short-term rental accommodation premises register where most hosts must register their properties" and strata by-laws that can restrict some short-term rentals. |
| NSW | NSW Planning Portal "Short Term Rental Accommodation (STRA)" register page | https://www.planningportal.nsw.gov.au/stra | VERIFIED (W) |  |

- QLD: no direct state equivalent. VERIFIED: no in-force QLD Act or regulation has "Short" in its title. Local council rules and body corporate by-laws apply (INFERRED).
- NSW: the Acts behind the NSW scheme are not verified (Gap N1).

### C5. Sale of land, title, property law and estate agents

Profiles: 07, 14, 18

| Jur. | Exact title or body | Official URL | Status | Note |
|---|---|---|---|---|
| VIC | Sale of Land Act 1962 | https://www.legislation.vic.gov.au/in-force/acts/sale-land-act-1962 | VERIFIED (L) |  |
| VIC | Transfer of Land Act 1958 | https://www.legislation.vic.gov.au/in-force/acts/transfer-land-act-1958 | VERIFIED (L) |  |
| VIC | Property Law Act 1958 | https://www.legislation.vic.gov.au/in-force/acts/property-law-act-1958 | VERIFIED (L) |  |
| VIC | Estate Agents Act 1980 | https://www.legislation.vic.gov.au/in-force/acts/estate-agents-act-1980 | VERIFIED (L) |  |
| QLD | Property Law Act 2023 | https://www.legislation.qld.gov.au/view/html/inforce/current/act-2023-027 | VERIFIED (L) | VERIFIED: the only in-force QLD Act with 'Property Law' in its title (title search). |
| QLD | Land Title Act 1994 | https://www.legislation.qld.gov.au/view/html/inforce/current/act-1994-011 | VERIFIED (L) |  |
| QLD | Property Occupations Act 2014 | https://www.legislation.qld.gov.au/view/html/inforce/current/act-2014-022 | VERIFIED (L) |  |

- Cth: no conveyancing Act (INFERRED). Foreign purchasers: see row D2.
- NSW Act: not verified (see Gap N1). No title is given here on purpose.

### D1. Land tax

Profiles: 05, 07, 14, 18

| Jur. | Exact title or body | Official URL | Status | Note |
|---|---|---|---|---|
| VIC | Land Tax Act 2005 | https://www.legislation.vic.gov.au/in-force/acts/land-tax-act-2005 | VERIFIED (L) |  |
| VIC | State Revenue Office (Victoria) | https://www.sro.vic.gov.au/ | VERIFIED (W) |  |
| NSW | Revenue NSW | https://www.revenue.nsw.gov.au/ | VERIFIED (W) |  |
| QLD | Land Tax Act 2010 | https://www.legislation.qld.gov.au/view/html/inforce/current/act-2010-015 | VERIFIED (L) |  |
| QLD | Queensland Revenue Office | https://qro.qld.gov.au/ | VERIFIED (W) |  |

- Cth: no direct equivalent. VERIFIED: no in-force Commonwealth Act with "Land Tax" in its title.
- NSW Act: not verified (see Gap N1). No title is given here on purpose.

### D2. Duties (stamp or transfer duty) and foreign purchasers

Profiles: 05, 07, 14, 18, 23, 25

| Jur. | Exact title or body | Official URL | Status | Note |
|---|---|---|---|---|
| Cth | Foreign Acquisitions and Takeovers Act 1975 | https://www.legislation.gov.au/C2004A01402/latest/text | VERIFIED (A) | Foreign investment approval (FIRB). Not a duty. |
| VIC | Duties Act 2000 | https://www.legislation.vic.gov.au/in-force/acts/duties-act-2000 | VERIFIED (L) |  |
| QLD | Duties Act 2001 | https://www.legislation.qld.gov.au/view/html/inforce/current/act-2001-071 | VERIFIED (L) |  |

- Cth: no stamp duty Act. VERIFIED: the only in-force Commonwealth Act with "Stamp Dut" in its title is an ACT amendment Act from 1986.
- State foreign purchaser surcharges sit in the state duties and land tax Acts (INFERRED).
- NSW Act: not verified (see Gap N1). No title is given here on purpose.

### D3. Payroll tax

Profiles: 05, 16

| Jur. | Exact title or body | Official URL | Status | Note |
|---|---|---|---|---|
| VIC | Payroll Tax Act 2007 | https://www.legislation.vic.gov.au/in-force/acts/payroll-tax-act-2007 | VERIFIED (L) |  |
| QLD | Payroll Tax Act 1971 | https://www.legislation.qld.gov.au/view/html/inforce/current/act-1971-037 | VERIFIED (L) |  |

- Cth: no direct equivalent. VERIFIED: no in-force Commonwealth Act with "Payroll Tax" in its title.
- NSW Act: not verified (see Gap N1). No title is given here on purpose.

### D4. State tax administration (objections, rulings, penalties)

Profiles: 05, 07

| Jur. | Exact title or body | Official URL | Status | Note |
|---|---|---|---|---|
| VIC | Taxation Administration Act 1997 | https://www.legislation.vic.gov.au/in-force/acts/taxation-administration-act-1997 | VERIFIED (L) |  |
| QLD | Taxation Administration Act 2001 | https://www.legislation.qld.gov.au/view/html/inforce/current/act-2001-072 | VERIFIED (L) |  |

- NSW Act: not verified (see Gap N1). No title is given here on purpose.
- ATO site blocked automated access (Gap B1).

### E1. Long service leave

Profiles: 05, 06, 16

| Jur. | Exact title or body | Official URL | Status | Note |
|---|---|---|---|---|
| Cth | Fair Work Act 2009 | https://www.legislation.gov.au/C2009A00028/latest/text | VERIFIED (A) | INFERRED: the National Employment Standards leave long service leave largely to state law. |
| Cth | Long Service Leave (Commonwealth Employees) Act 1976 | https://www.legislation.gov.au/C2004A01621/latest/text | VERIFIED (A) | Commonwealth employees only. |
| VIC | Long Service Leave Act 2018 | https://www.legislation.vic.gov.au/in-force/acts/long-service-leave-act-2018 | VERIFIED (L) |  |
| VIC | Construction Industry Long Service Leave Act 1997 | https://www.legislation.vic.gov.au/in-force/acts/construction-industry-long-service-leave-act-1997 | VERIFIED (L) | Portable scheme for construction. |
| QLD | Industrial Relations Act 2016 | https://www.legislation.qld.gov.au/view/html/inforce/current/act-2016-063 | VERIFIED (L) | INFERRED: QLD long service leave sits in this Act. |
| QLD | Building and Construction Industry (Portable Long Service Leave) Act 1991 | https://www.legislation.qld.gov.au/view/html/inforce/current/act-1991-090 | VERIFIED (L) |  |

- NSW Act: not verified (see Gap N1). No title is given here on purpose.

### E2. Work health and safety (WHS / OHS) and dangerous goods

Profiles: 06, 09, 16, 29, 35

| Jur. | Exact title or body | Official URL | Status | Note |
|---|---|---|---|---|
| Cth | Work Health and Safety Act 2011 | https://www.legislation.gov.au/C2011A00137/latest/text | VERIFIED (A) | INFERRED: Commonwealth workplaces (Comcare). |
| VIC | Occupational Health and Safety Act 2004 | https://www.legislation.vic.gov.au/in-force/acts/occupational-health-and-safety-act-2004 | VERIFIED (L) | VIC has no WHS Act: no in-force VIC Act or rule has 'Work Health and Safety' in its title (VERIFIED, title search). |
| VIC | Occupational Health and Safety Regulations 2017 | https://www.legislation.vic.gov.au/in-force/statutory-rules/occupational-health-and-safety-regulations-2017 | VERIFIED (L) |  |
| VIC | Dangerous Goods Act 1985 | https://www.legislation.vic.gov.au/in-force/acts/dangerous-goods-act-1985 | VERIFIED (L) |  |
| VIC | WorkSafe Victoria | https://www.worksafe.vic.gov.au/ | VERIFIED (W) |  |
| NSW | SafeWork NSW | https://www.safework.nsw.gov.au/ | VERIFIED (W) |  |
| QLD | Work Health and Safety Act 2011 | https://www.legislation.qld.gov.au/view/html/inforce/current/act-2011-018 | VERIFIED (L) |  |
| QLD | Work Health and Safety Regulation 2011 | https://www.legislation.qld.gov.au/view/html/inforce/current/sl-2011-0240 | VERIFIED (L) |  |
| QLD | Work Health and Safety (Codes of Practice) Notice 2022 | https://www.legislation.qld.gov.au/view/html/inforce/current/sl-2022-0096 | VERIFIED (L) |  |

- NSW Act: not verified (see Gap N1). No title is given here on purpose.
- WorkSafe Queensland, Comcare and Safe Work Australia sites blocked automated access (Gap B1).

### E3. Workers compensation

Profiles: 06, 16

| Jur. | Exact title or body | Official URL | Status | Note |
|---|---|---|---|---|
| Cth | Safety, Rehabilitation and Compensation Act 1988 | https://www.legislation.gov.au/C2004A03668/latest/text | VERIFIED (A) |  |
| VIC | Workplace Injury Rehabilitation and Compensation Act 2013 | https://www.legislation.vic.gov.au/in-force/acts/workplace-injury-rehabilitation-and-compensation-act-2013 | VERIFIED (L) |  |
| NSW | Insurance and Care NSW (icare) | https://www.icare.nsw.gov.au/ | VERIFIED (W) |  |
| QLD | Workers’ Compensation and Rehabilitation Act 2003 | https://www.legislation.qld.gov.au/view/html/inforce/current/act-2003-027 | VERIFIED (L) | Note the typographic apostrophe in the official title. |

- NSW Act: not verified (see Gap N1). No title is given here on purpose.
- SIRA (NSW) and WorkCover Queensland pages blocked automated access (Gap B1).

### E4. Anti-discrimination and equal opportunity

Profiles: 06, 08, 28

| Jur. | Exact title or body | Official URL | Status | Note |
|---|---|---|---|---|
| Cth | Disability Discrimination Act 1992 | https://www.legislation.gov.au/C2004A04426/latest/text | VERIFIED (A) |  |
| Cth | Sex Discrimination Act 1984 | https://www.legislation.gov.au/C2004A02868/latest/text | VERIFIED (A) |  |
| Cth | Racial Discrimination Act 1975 | https://www.legislation.gov.au/C2004A00274/latest/text | VERIFIED (A) |  |
| Cth | Age Discrimination Act 2004 | https://www.legislation.gov.au/C2004A01302/latest/text | VERIFIED (A) |  |
| VIC | Equal Opportunity Act 2010 | https://www.legislation.vic.gov.au/in-force/acts/equal-opportunity-act-2010 | VERIFIED (L) |  |
| VIC | Victorian Equal Opportunity and Human Rights Commission | https://www.humanrights.vic.gov.au/ | VERIFIED (W) |  |
| NSW | Anti-Discrimination NSW | https://antidiscrimination.nsw.gov.au/ | VERIFIED (W) |  |
| QLD | Anti-Discrimination Act 1991 | https://www.legislation.qld.gov.au/view/html/inforce/current/act-1991-085 | VERIFIED (L) | VERIFIED: the 1991 Act is the one in force (title search found no other). |

- NSW Act: not verified (see Gap N1). No title is given here on purpose.
- QLD Human Rights Commission and Australian Human Rights Commission sites blocked automated access (Gap B1).

### F1. Privacy and health records

Profiles: 01, 03, 04, 10, 13, 14, 15, 20, 22, 28, 31, 32, 33, 34

| Jur. | Exact title or body | Official URL | Status | Note |
|---|---|---|---|---|
| Cth | Privacy Act 1988 | https://www.legislation.gov.au/C2004A03712/latest/text | VERIFIED (A) |  |
| Cth | My Health Records Act 2012 | https://www.legislation.gov.au/C2012A00063/latest/text | VERIFIED (A) |  |
| Cth | Office of the Australian Information Commissioner | https://www.oaic.gov.au/about-the-OAIC | VERIFIED (W) |  |
| VIC | Privacy and Data Protection Act 2014 | https://www.legislation.vic.gov.au/in-force/acts/privacy-and-data-protection-act-2014 | VERIFIED (L) | INFERRED: Victorian public sector. |
| VIC | Health Records Act 2001 | https://www.legislation.vic.gov.au/in-force/acts/health-records-act-2001 | VERIFIED (L) | INFERRED: reaches private health service providers in VIC too. |
| VIC | Office of the Victorian Information Commissioner | https://ovic.vic.gov.au/ | VERIFIED (W) |  |
| VIC | Health Complaints Commissioner (Victoria) | https://hcc.vic.gov.au/ | VERIFIED (W) |  |
| QLD | Information Privacy Act 2009 | https://www.legislation.qld.gov.au/view/html/inforce/current/act-2009-014 | VERIFIED (L) | INFERRED: QLD public sector. |

- QLD: no separate health records Act. VERIFIED: no in-force QLD Act or regulation has "Health Records" in its title.
- NSW Act: not verified (see Gap N1). No title is given here on purpose.
- NSW Information and Privacy Commission site did not answer; QLD Office of the Information Commissioner blocked (Gap B1).

### F2. Surveillance devices, interception and workplace surveillance

Profiles: 01, 22, 28, 32

| Jur. | Exact title or body | Official URL | Status | Note |
|---|---|---|---|---|
| Cth | Surveillance Devices Act 2004 | https://www.legislation.gov.au/C2004A01387/latest/text | VERIFIED (A) | INFERRED: law-enforcement use. |
| Cth | Telecommunications (Interception and Access) Act 1979 | https://www.legislation.gov.au/C2004A02124/latest/text | VERIFIED (A) |  |
| VIC | Surveillance Devices Act 1999 | https://www.legislation.vic.gov.au/in-force/acts/surveillance-devices-act-1999 | VERIFIED (L) |  |
| QLD | Invasion of Privacy Act 1971 | https://www.legislation.qld.gov.au/view/html/inforce/current/act-1971-050 | VERIFIED (L) | INFERRED: QLD's listening-device rules for private persons sit here. |

- VIC: no workplace surveillance Act. VERIFIED: no in-force VIC Act or rule has "Workplace Surveillance" in its title.
- QLD: no surveillance devices or workplace surveillance Act for private persons. VERIFIED: no in-force QLD Act or regulation has "Surveillance" in its title.
- Profile 28 names a NSW workplace surveillance statute. Its title could not be checked live (Gap N1).

### G1. Wills, succession and probate

Profiles: 14

| Jur. | Exact title or body | Official URL | Status | Note |
|---|---|---|---|---|
| Cth | Family Law Act 1975 | https://www.legislation.gov.au/C2004A00275/latest/text | VERIFIED (A) | Binding financial agreements (named in profile 14). Not a succession law. |
| VIC | Wills Act 1997 | https://www.legislation.vic.gov.au/in-force/acts/wills-act-1997 | VERIFIED (L) |  |
| VIC | Administration and Probate Act 1958 | https://www.legislation.vic.gov.au/in-force/acts/administration-and-probate-act-1958 | VERIFIED (L) |  |
| QLD | Succession Act 1981 | https://www.legislation.qld.gov.au/view/html/inforce/current/act-1981-069 | VERIFIED (L) |  |

- Cth: no succession Act. VERIFIED: the only in-force Commonwealth Act with "Succession" in its title is the Succession to the Crown Act 2015.
- QLD: no in-force QLD Act or regulation has "Wills" in its title (VERIFIED); wills sit in the Succession Act 1981 (INFERRED).
- NSW Act: not verified (see Gap N1). No title is given here on purpose.

### G2. Powers of attorney, guardianship and advance care directives

Profiles: 10, 14, 15

| Jur. | Exact title or body | Official URL | Status | Note |
|---|---|---|---|---|
| VIC | Powers of Attorney Act 2014 | https://www.legislation.vic.gov.au/in-force/acts/powers-attorney-act-2014 | VERIFIED (L) |  |
| VIC | Guardianship and Administration Act 2019 | https://www.legislation.vic.gov.au/in-force/acts/guardianship-and-administration-act-2019 | VERIFIED (L) |  |
| VIC | Medical Treatment Planning and Decisions Act 2016 | https://www.legislation.vic.gov.au/in-force/acts/medical-treatment-planning-and-decisions-act-2016 | VERIFIED (L) | INFERRED: VIC advance care directives. |
| VIC | Office of the Public Advocate | https://www.publicadvocate.vic.gov.au/ | VERIFIED (W) |  |
| NSW | NSW Trustee and Guardian | https://www.nsw.gov.au/departments-and-agencies/trustee-guardian | VERIFIED (W) |  |
| QLD | Powers of Attorney Act 1998 | https://www.legislation.qld.gov.au/view/html/inforce/current/act-1998-022 | VERIFIED (L) |  |
| QLD | Guardianship and Administration Act 2000 | https://www.legislation.qld.gov.au/view/html/inforce/current/act-2000-008 | VERIFIED (L) |  |
| QLD | Public Guardian Act 2014 | https://www.legislation.qld.gov.au/view/html/inforce/current/act-2014-026 | VERIFIED (L) |  |
| QLD | Office of the Public Guardian | https://www.publicguardian.qld.gov.au/ | VERIFIED (W) |  |

- Cth: no direct equivalent. VERIFIED: no in-force Commonwealth Act with "Powers of Attorney" in its title; the only one with "Guardianship" is the Immigration (Guardianship of Children) Act 1946.
- NSW Act: not verified (see Gap N1). No title is given here on purpose.

### G3. Limitation periods and civil liability

Profiles: 06, 17

| Jur. | Exact title or body | Official URL | Status | Note |
|---|---|---|---|---|
| VIC | Limitation of Actions Act 1958 | https://www.legislation.vic.gov.au/in-force/acts/limitation-actions-act-1958 | VERIFIED (L) |  |
| VIC | Wrongs Act 1958 | https://www.legislation.vic.gov.au/in-force/acts/wrongs-act-1958 | VERIFIED (L) | INFERRED: VIC proportionate liability and negligence rules. |
| QLD | Limitation of Actions Act 1974 | https://www.legislation.qld.gov.au/view/html/inforce/current/act-1974-075 | VERIFIED (L) |  |
| QLD | Civil Liability Act 2003 | https://www.legislation.qld.gov.au/view/html/inforce/current/act-2003-016 | VERIFIED (L) |  |

- Cth: no direct equivalent. VERIFIED: no in-force Commonwealth Act with "Limitation of Actions" in its title.
- NSW Act: not verified (see Gap N1). No title is given here on purpose.

### H1. Fair trading and the Australian Consumer Law

Profiles: 02, 03, 11, 12, 17, 19, 27

| Jur. | Exact title or body | Official URL | Status | Note |
|---|---|---|---|---|
| Cth | Competition and Consumer Act 2010 | https://www.legislation.gov.au/C2004A00109/latest/text | VERIFIED (A) | INFERRED: the Australian Consumer Law is Schedule 2. |
| Cth | Australian Securities and Investments Commission Act 2001 | https://www.legislation.gov.au/C2004A00819/latest/text | VERIFIED (A) | INFERRED: consumer protection for financial services. |
| Cth | ASIC | https://www.asic.gov.au/ | VERIFIED (W) |  |
| VIC | Australian Consumer Law and Fair Trading Act 2012 | https://www.legislation.vic.gov.au/in-force/acts/australian-consumer-law-and-fair-trading-act-2012 | VERIFIED (L) |  |
| VIC | Consumer Affairs Victoria | https://www.consumer.vic.gov.au/ | VERIFIED (W) |  |
| NSW | NSW Fair Trading | https://www.nsw.gov.au/departments-and-agencies/fair-trading | VERIFIED (W) |  |
| QLD | Fair Trading Act 1989 | https://www.legislation.qld.gov.au/view/html/inforce/current/act-1989-084 | VERIFIED (L) |  |
| QLD | Office of Fair Trading (Queensland) | https://www.justice.qld.gov.au/about-us/services/fair-trading | VERIFIED (W) |  |

- NSW Act: not verified (see Gap N1). No title is given here on purpose.
- ACCC site blocked automated access (Gap B1).

### H2. Civil and administrative tribunals

Profiles: 06, 07, 14, 19

| Jur. | Exact title or body | Official URL | Status | Note |
|---|---|---|---|---|
| Cth | Administrative Review Tribunal Act 2024 | https://www.legislation.gov.au/C2024A00040/latest/text | VERIFIED (A) |  |
| VIC | Victorian Civil and Administrative Tribunal Act 1998 | https://www.legislation.vic.gov.au/in-force/acts/victorian-civil-and-administrative-tribunal-act-1998 | VERIFIED (L) |  |
| VIC | Victorian Civil and Administrative Tribunal (VCAT) | https://www.vcat.vic.gov.au/ | VERIFIED (W) |  |
| NSW | NSW Civil and Administrative Tribunal (NCAT) | https://ncat.nsw.gov.au/ | VERIFIED (W) |  |
| QLD | Queensland Civil and Administrative Tribunal Act 2009 | https://www.legislation.qld.gov.au/view/html/inforce/current/act-2009-023 | VERIFIED (L) |  |
| QLD | Queensland Civil and Administrative Tribunal (QCAT) | https://www.qcat.qld.gov.au/ | VERIFIED (W) |  |

- NSW Act: not verified (see Gap N1). No title is given here on purpose.
- Administrative Review Tribunal site did not answer (Gap B1).

### H3. Defamation

Profiles: 12, 17, 27

| Jur. | Exact title or body | Official URL | Status | Note |
|---|---|---|---|---|
| VIC | Defamation Act 2005 | https://www.legislation.vic.gov.au/in-force/acts/defamation-act-2005 | VERIFIED (L) |  |
| QLD | Defamation Act 2005 | https://www.legislation.qld.gov.au/view/html/inforce/current/act-2005-055 | VERIFIED (L) |  |

- Cth: no direct equivalent. VERIFIED: no in-force Commonwealth Act with "Defamation" in its title.
- NSW Act: not verified (see Gap N1). No title is given here on purpose.

### I1. Heavy vehicles and chain of responsibility

Profiles: 16

| Jur. | Exact title or body | Official URL | Status | Note |
|---|---|---|---|---|
| Cth | National Heavy Vehicle Regulator | https://www.nhvr.gov.au/about-us | VERIFIED (W) | A national regulator, not a Commonwealth agency (INFERRED). |
| VIC | Heavy Vehicle National Law Application Act 2013 | https://www.legislation.vic.gov.au/in-force/acts/heavy-vehicle-national-law-application-act-2013 | VERIFIED (L) |  |
| QLD | Heavy Vehicle National Law Act 2012 | https://www.legislation.qld.gov.au/view/html/inforce/current/act-2012-021 | VERIFIED (L) | INFERRED: QLD hosts the national law. |

- Cth: no Commonwealth Act (INFERRED; not searched).
- NSW Act: not verified (see Gap N1). No title is given here on purpose.

### I2. Vehicle registration and roadworthiness

Profiles: 18, 33

| Jur. | Exact title or body | Official URL | Status | Note |
|---|---|---|---|---|
| Cth | Road Vehicle Standards Act 2018 | https://www.legislation.gov.au/C2018A00163/latest/text | VERIFIED (A) |  |
| VIC | Road Safety Act 1986 | https://www.legislation.vic.gov.au/in-force/acts/road-safety-act-1986 | VERIFIED (L) |  |
| NSW | Road Transport Act 2013 | https://legislation.nsw.gov.au/view/html/inforce/current/act-2013-018 | VERIFIED (F) |  |
| QLD | Transport Operations (Road Use Management) Act 1995 | https://www.legislation.qld.gov.au/view/html/inforce/current/act-1995-009 | VERIFIED (L) |  |

### I3. Vessels in state waters

Profiles: 18

| Jur. | Exact title or body | Official URL | Status | Note |
|---|---|---|---|---|
| Cth | Navigation Act 2012 | https://www.legislation.gov.au/C2012A00128/latest/text | VERIFIED (A) |  |
| Cth | Marine Safety (Domestic Commercial Vessel) National Law Act 2012 | https://www.legislation.gov.au/C2012A00121/latest/text | VERIFIED (A) |  |
| VIC | Marine Safety Act 2010 | https://www.legislation.vic.gov.au/in-force/acts/marine-safety-act-2010 | VERIFIED (L) |  |
| NSW | Marine Safety Act 1998 | https://legislation.nsw.gov.au/view/html/inforce/current/act-1998-121 | VERIFIED (F) |  |
| QLD | Transport Operations (Marine Safety) Act 1994 | https://www.legislation.qld.gov.au/view/html/inforce/current/act-1994-014 | VERIFIED (L) |  |

- Profile 18 names AMSA for vessels. Recreational vessels in state waters fall under the state Acts (INFERRED).

### I4. Electricity supply, networks and grid connection

Profiles: 09, 30

| Jur. | Exact title or body | Official URL | Status | Note |
|---|---|---|---|---|
| Cth | Renewable Energy (Electricity) Act 2000 | https://www.legislation.gov.au/C2004A00767/latest/text | VERIFIED (A) |  |
| Cth | Australian Energy Market Commission | https://www.aemc.gov.au/ | VERIFIED (W) |  |
| VIC | Electricity Industry Act 2000 | https://www.legislation.vic.gov.au/in-force/acts/electricity-industry-act-2000 | VERIFIED (L) |  |
| VIC | National Electricity (Victoria) Act 2005 | https://www.legislation.vic.gov.au/in-force/acts/national-electricity-victoria-act-2005 | VERIFIED (L) |  |
| NSW | Electricity Supply Act 1995 | https://legislation.nsw.gov.au/view/html/inforce/current/act-1995-094 | VERIFIED (F) |  |
| NSW | Electricity Infrastructure Investment Act 2020 | https://legislation.nsw.gov.au/view/html/inforce/current/act-2020-044 | VERIFIED (F) |  |
| QLD | Electricity Act 1994 | https://www.legislation.qld.gov.au/view/html/inforce/current/act-1994-064 | VERIFIED (L) |  |
| QLD | Electricity—National Scheme (Queensland) Act 1997 | https://www.legislation.qld.gov.au/view/html/inforce/current/act-1997-027 | VERIFIED (L) | Note the em dash in the official title. |

- AEMO and AER sites blocked or did not answer (Gap B1).
- The national electricity law is applied in each state by that state's own Act (INFERRED).

### J1. Medicines and poisons (scheduling, prescribing, compounding)

Profiles: 10, 15, 28, 35

| Jur. | Exact title or body | Official URL | Status | Note |
|---|---|---|---|---|
| Cth | Therapeutic Goods Act 1989 | https://www.legislation.gov.au/C2004A03952/latest/text | VERIFIED (A) |  |
| VIC | Drugs, Poisons and Controlled Substances Act 1981 | https://www.legislation.vic.gov.au/in-force/acts/drugs-poisons-and-controlled-substances-act-1981 | VERIFIED (L) |  |
| QLD | Medicines and Poisons Act 2019 | https://www.legislation.qld.gov.au/view/html/inforce/current/act-2019-026 | VERIFIED (L) |  |

- NSW Act: not verified (see Gap N1). No title is given here on purpose.
- TGA site did not answer (Gap B1).

### J2. Gene technology

Profiles: 15, 36

| Jur. | Exact title or body | Official URL | Status | Note |
|---|---|---|---|---|
| Cth | Gene Technology Act 2000 | https://www.legislation.gov.au/C2004A00762/latest/text | VERIFIED (A) |  |
| VIC | Gene Technology Act 2001 | https://www.legislation.vic.gov.au/in-force/acts/gene-technology-act-2001 | VERIFIED (L) |  |
| QLD | Gene Technology (Queensland) Act 2016 | https://www.legislation.qld.gov.au/view/html/inforce/current/act-2016-054 | VERIFIED (L) | VERIFIED: note "(Queensland)" in the official title. |

- NSW Act: not verified (see Gap N1). No title is given here on purpose.
- OGTR site did not answer (Gap B1).

### J3. Biosecurity

Profiles: 36

| Jur. | Exact title or body | Official URL | Status | Note |
|---|---|---|---|---|
| Cth | Biosecurity Act 2015 | https://www.legislation.gov.au/C2015A00061/latest/text | VERIFIED (A) |  |
| VIC | Plant Biosecurity Act 2010 | https://www.legislation.vic.gov.au/in-force/acts/plant-biosecurity-act-2010 | VERIFIED (L) | VIC has no general biosecurity Act: the only in-force VIC Act with "Biosecurity" in its title is this one (VERIFIED, title search). |
| NSW | Biosecurity Act 2015 | https://legislation.nsw.gov.au/view/html/inforce/current/act-2015-024 | VERIFIED (F) |  |
| QLD | Biosecurity Act 2014 | https://www.legislation.qld.gov.au/view/html/inforce/current/act-2014-007 | VERIFIED (L) |  |

### K1. Lobbying and foreign influence

Profiles: 25, 30

| Jur. | Exact title or body | Official URL | Status | Note |
|---|---|---|---|---|
| Cth | Foreign Influence Transparency Scheme Act 2018 | https://www.legislation.gov.au/C2018A00063/latest/text | VERIFIED (A) |  |
| Cth | Lobbyists Register (Australian Government) | https://lobbyists.ag.gov.au/ | VERIFIED (W) |  |
| NSW | Lobbying of Government Officials Act 2011 | https://legislation.nsw.gov.au/view/html/inforce/current/act-2011-005 | VERIFIED (F) |  |
| QLD | Integrity Act 2009 | https://www.legislation.qld.gov.au/view/html/inforce/current/act-2009-052 | VERIFIED (L) | INFERRED: the QLD lobbyist register sits under this Act. |

- VIC: no lobbying Act. VERIFIED: no in-force VIC Act or rule has "Lobby" in its title. The VIC lobbyist register site did not answer (Gap B1).
- Cth: no lobbying Act. VERIFIED: no in-force Commonwealth Act with "Lobbying" in its title; the federal code is not legislation (INFERRED).
- QLD Integrity Commissioner site blocked automated access (Gap B1).

### K2. Fundraising and charities

Profiles: 24

| Jur. | Exact title or body | Official URL | Status | Note |
|---|---|---|---|---|
| Cth | Australian Charities and Not-for-profits Commission Act 2012 | https://www.legislation.gov.au/C2012A00168/latest/text | VERIFIED (A) |  |
| Cth | Charities Act 2013 | https://www.legislation.gov.au/C2013A00100/latest/text | VERIFIED (A) |  |
| VIC | Fundraising Act 1998 | https://www.legislation.vic.gov.au/in-force/acts/fundraising-act-1998 | VERIFIED (L) |  |
| QLD | Collections Act 1966 | https://www.legislation.qld.gov.au/view/html/inforce/current/act-1966-007 | VERIFIED (L) | INFERRED: QLD charitable collections law. No QLD Act has "Fundrais" in its title (VERIFIED). |

- NSW Act: not verified (see Gap N1). No title is given here on purpose.
- ACNC site did not answer (Gap B1).

### K3. Child safe standards

Profiles: 13

| Jur. | Exact title or body | Official URL | Status | Note |
|---|---|---|---|---|
| VIC | Child Wellbeing and Safety Act 2005 | https://www.legislation.vic.gov.au/in-force/acts/child-wellbeing-and-safety-act-2005 | VERIFIED (L) |  |
| VIC | Social Services Regulator (Victoria) | https://www.vic.gov.au/social-services-regulator | VERIFIED (W) |  |
| NSW | Office of the Children's Guardian | https://ocg.nsw.gov.au/ | VERIFIED (W) |  |
| QLD | Child Safe Organisations Act 2024 | https://www.legislation.qld.gov.au/view/html/inforce/current/act-2024-049 | VERIFIED (L) | VERIFIED: in force; the official record carries a trailing space in the title, shown here without it. |

- Cth: the National Principles for Child Safe Organisations are not an Act (INFERRED).
- NSW Act: not verified (see Gap N1). No title is given here on purpose.
- Queensland Family and Child Commission site did not answer (Gap B1).

## 3. Free regulator documents that apply

Acts, regulations and case law are free to read (Section 2 URLs). So are these regulator documents. Reuse terms
were checked only for the NCC (CC BY 4.0, below).

| Document | Publisher | Official URL | Status | Note |
|---|---|---|---|---|
| National Construction Code: NCC 2025 and NCC 2022 | Australian Building Codes Board | https://ncc.abcb.gov.au/editions-national-construction-code | VERIFIED (W) | The page offers PDF downloads of both editions. NCC 2022 was adopted from 1 May 2023; Amendment 1 from 1 May 2025 (VERIFIED, same page). |
| NCC 2025 state adoption dates | Australian Building Codes Board | https://ncc.abcb.gov.au/ncc-2025/ncc-2025-state-and-territory-adoption-information | VERIFIED (W) | NCC 2025 was published on 1 May 2026. Adoption: VIC 1 May 2026; NSW 1 May 2027; QLD 1 May 2027 (VERIFIED). So NCC 2022 still governs new NSW and QLD work until then (INFERRED). |
| NCC 2025 licence notice | Australian Building Codes Board | https://ncc.abcb.gov.au/editions/ncc-2025/adopted/volume-one/preface/copyright-licence-notice-and-acknowledgment-country | VERIFIED (W) | Licensed under Creative Commons Attribution 4.0, except third-party material, trade marks, images and photographs (VERIFIED). Standards the NCC references are not covered by that licence (INFERRED). |
| Work Health and Safety (Codes of Practice) Notice 2022 (QLD) | QLD Parliamentary Counsel | https://www.legislation.qld.gov.au/view/html/inforce/current/sl-2022-0096 | VERIFIED (L) | INFERRED: the instrument that approves QLD WHS codes of practice. |
| Electrical Safety (Codes of Practice) Notice 2024 (QLD) | QLD Parliamentary Counsel | https://www.legislation.qld.gov.au/view/html/inforce/current/sl-2024-0156 | VERIFIED (L) | INFERRED: approves QLD electrical safety codes of practice. |
| Codes of practice (NSW) | SafeWork NSW | https://www.safework.nsw.gov.au/resource-library/codes-of-practice | VERIFIED (W) | The page says a code "provides detailed information on how you can achieve the standards required under the work health and safety (WHS) laws" and that codes are admissible in court proceedings (VERIFIED). |
| Laws and regulations (VIC), including compliance codes | WorkSafe Victoria | https://www.worksafe.vic.gov.au/laws-and-regulations | VERIFIED (W) | The page refers to compliance codes and consultation on them. The list of codes itself was not opened. |
| Regulatory guides | ASIC | https://www.asic.gov.au/regulatory-resources/find-a-document/regulatory-guides | VERIFIED (W) | |
| Short stay levy guidance (VIC) | State Revenue Office | https://www.sro.vic.gov.au/owning-property/short-stay-levy | VERIFIED (W) | |
| Short-term rental accommodation rules (NSW) | NSW Government | https://www.nsw.gov.au/housing-and-construction/short-term-rental-accommodation | VERIFIED (W) | See row C4. |

Free sources named in the brief that could not be opened live today (Gap B1): Safe Work Australia model codes of
practice, ATO legal database, Fair Work Ombudsman, ACCC, NatHERS, the Victoria Planning Provisions and ResCode,
the Queensland Development Code, and AustLII. They stay usable sources; they are simply not VERIFIED here.

## 4. Australian Standards named or plainly needed (name and number only)

Australian Standards are copyright works sold by Standards Australia through licensed distributors. ATLAS may hold
the full text only of a standard the Principal has licensed, and only where that licence allows storing and
processing it in a private system. For every other standard ATLAS gives the number, edition and title, and what
free sources say about it, and never reproduces clauses. Accepting a licence is the Principal's own act; ATLAS
never accepts terms on the Principal's behalf.

Titles, editions and status below were read live from the Standards Australia store (store.standards.org.au) on
2026-10-17: VERIFIED. No text of any standard was read or stored.

| Number and edition | Title | Store status | Profiles | Store page |
|---|---|---|---|---|
| AS/NZS 1170.0:2002 | Structural design actions, Part 0: General principles | Current | 08 | https://store.standards.org.au/product/as-nzs-1170-0-2002 |
| AS/NZS 1170.1:2002 | Structural design actions, Part 1: Permanent, imposed and other actions | Current | 08 | https://store.standards.org.au/product/as-nzs-1170-1-2002 |
| AS/NZS 1170.2:2021 | Structural design actions, Part 2: Wind actions | Current | 08 | https://store.standards.org.au/product/as-nzs-1170-2-2021 |
| AS/NZS 1170.3:2003 | Structural design actions, Part 3: Snow and ice actions | Current | 08 | https://store.standards.org.au/product/as-nzs-1170-3-2003 |
| AS 1170.4:2024 | Structural design actions, Part 4: Earthquake actions in Australia | Current (the 2007 edition shows "Available Superseded") | 08 | https://store.standards.org.au/product/as-1170-4-2024 |
| AS 1428.1:2021 | Design for access and mobility, Part 1: General requirements for access - New building work | Current | 08 | https://store.standards.org.au/product/as-1428-1-2021 |
| AS 3959:2018 | Construction of buildings in bushfire-prone areas | Pending Revision | 07, 08 | https://store.standards.org.au/product/as-3959-2018 |
| AS/NZS 3000:2018 | Electrical installations (known as the Australian/New Zealand Wiring Rules) | Current. The store also lists Amd 1:2020, Amd 2:2021, Amd 3:2023 and Rul 1:2024. | 09 | https://store.standards.org.au/product/as-nzs-3000-2018 |
| AS 4024.1100:2019 | Safety of machinery, Part 1100: Application guide | Current | 09 | https://store.standards.org.au/product/as-4024-1100-2019 |
| AS/NZS 4024.1201:2014 | Safety of machinery, Part 1201: General principles for design - Risk assessment and risk reduction | Current | 09 | https://store.standards.org.au/product/as-nzs-4024-1201-2014 |

Notes:

- Profile 08 writes "AS 1170" and "AS 1428", and profile 09 writes "AS/NZS 4024". Each is a series. The parts above
  are the ones a design or machinery task most often needs (INFERRED). The 4024 series mixes AS and AS/NZS parts
  (VERIFIED for the two parts shown).
- ISO, IEC and ISO/IEC standards named in the profiles (for example ISO 19650, ISO 9001, ISO/IEC 27001,
  ISO 26262) are also sold copyright works. The same rule should apply to them (INFERRED; not checked here).

## 5. Which rows apply to which profile

| Profile | Rows | Documents and Standards (Sections 3 and 4) | Note |
|---|---|---|---|
| 01 Systems Architect & Cybersecurity | F1, F2 |  |  |
| 02 Growth Executive | H1 |  |  |
| 03 Customer Success & Experience Lead | F1, H1 |  |  |
| 04 Data Scientist & AI Engineer | F1 |  |  |
| 05 CFO & Controller | D1, D2, D3, D4, E1 |  |  |
| 06 General Counsel & CHRO | A1, A2, A3, A4, A5, C1, C2, E1, E2, E3, E4, G3, H2 | NCC |  |
| 07 Development Director, Property & Real Estate | A1, A2, A3, A4, A5, B1, B2, B3, B4, B5, C1, C2, C3, C5, D1, D2, D4, H2 | NCC; AS 3959 |  |
| 08 AEC Computational Designer & Visualizer | A3, A4, A5, A6, B1, B4, E4 | NCC; AS/NZS 1170 series; AS 1428.1; AS 3959; AS/NZS 3000 |  |
| 09 Industrial & Energy Engineer | A4, A6, B3, E2, I4 | NCC; AS/NZS 3000; AS 4024.1100; AS/NZS 4024.1201 |  |
| 10 Chief Medical Officer & Risk Underwriter | F1, G2, J1 |  |  |
| 11 Game Director & Systems Designer | H1 |  |  |
| 12 Creative Director & Narrative Designer | H1, H3 |  |  |
| 13 Dean of Academia & Pedagogy | F1, K3 |  |  |
| 14 Private Family Advisor & Estate Guardian | A2, C3, C5, D1, D2, F1, G1, G2, H2 |  |  |
| 15 Chief Longevity Officer & Performance Physiologist | F1, G2, J1, J2 |  |  |
| 16 COO & Process Architect | D3, E1, E2, E3, I1 | WHS codes of practice |  |
| 17 High-Stakes Negotiator & Strategic Diplomat | G3, H1, H3 |  |  |
| 18 Global Asset Guardian & HNW Concierge | C5, D1, D2, I2, I3 |  |  |
| 19 Travel, Leisure & Global Hospitality Director | C1, C3, C4, H1, H2 |  |  |
| 20 Chief AI Officer & Agentic Systems Architect | F1 |  |  |
| 21 Chief Investment Officer & Quant Strategist | none |  | No state-level row. Its named compliance is Commonwealth-only (ASIC, ATO, corporations law, AUSTRAC) (INFERRED). |
| 22 Director of OSINT & Executive Protection | F1, F2 |  |  |
| 23 Venture Partner & Private Equity Director | D2 |  |  |
| 24 Director of Philanthropy, CSR & Community | K2 |  |  |
| 25 Geopolitical Strategist & Public Affairs Lead | D2, K1 |  |  |
| 26 Cultural Asset & Fine Art Curator | none |  | No state-level row. Its named compliance is Commonwealth-only (movable cultural heritage, resale royalty, AUSTRAC, GST and CGT) (INFERRED). |
| 27 Director of Digital Influence & Synthetic Media | H1, H3 |  |  |
| 28 Chief Behavioral Architect & Neuro-Optimizer | E4, F1, F2, J1 |  |  |
| 29 Frontier Technologies & Spatial Engineer | E2 | WHS codes of practice |  |
| 30 Sovereign Architect & Network State Strategist | B1, B5, B6, I4, K1 |  |  |
| 31 Embedded Systems & Mobile Security | F1 |  |  |
| 32 Communications & Telecom Security | F1, F2 |  |  |
| 33 Automotive & Vehicle Systems Engineering | F1, I2 |  |  |
| 34 Quantum Computing & Post-Quantum Cryptography | F1 |  |  |
| 35 Materials Science & Computational Chemistry | B3, E2, J1 | WHS codes of practice |  |
| 36 Synthetic Biology & Genomic Engineering | J2, J3 |  |  |

## 6. Gaps: what could not be verified

**N1. NSW legislation.** legislation.nsw.gov.au answered every automated request with a Cloudflare bot challenge,
for curl and for the web-fetch tool alike, except its RSS/Atom feeds. A title query on the feed returns only
titles republished in about the last week (VERIFIED: a query for an older title returned nothing). So only 9 NSW
instruments were verified, all from the in-force feed. In 36 of the 43
subject rows the NSW Act cell is empty. Three NSW statutes are named in the profiles themselves (profile 06: a
design and building practitioners Act and a strata schemes management Act; profile 28: a workplace surveillance
Act). Their titles and years as the profiles write them are not confirmed here. To close the gap: a person opens
legislation.nsw.gov.au in an ordinary browser and confirms each NSW title, or the live ATLAS reads the NSW in-force
feed each week and records titles as they are republished.

**B1. Official sites that refused or did not answer.** These bodies or documents were left out of the tables
because their sites blocked automated access (bot challenge or "Access Denied") or did not answer:

- Commonwealth: Administrative Review Tribunal, ATO, ACCC, Fair Work Ombudsman, Safe Work Australia, Comcare,
  OGTR, TGA, ACNC, AEMO, AER, Australian Human Rights Commission, NatHERS.
- VIC: Victorian Building Authority site and bpc.vic.gov.au (see V1), Planning Victoria (planning.vic.gov.au),
  Heritage Victoria, the VIC lobbyist register.
- NSW: SIRA, Information and Privacy Commission, NSW Small Business Commissioner, Heritage NSW.
- QLD: WorkSafe Queensland (and its Electrical Safety Office and WorkCover pages), Office of the Information
  Commissioner, Queensland Human Rights Commission, Integrity Commissioner, Board of Professional Engineers of
  Queensland, Queensland Family and Child Commission, the state planning site, the Queensland Development Code page,
  and the Planning and Environment Court page (404 at the URL tried; the Court's Act is verified in row B1).
- AustLII (free case law and legislation) also refused automated access.

**V1. Victoria's building regulator name.** The current authorised Building Act 1993 (version 152) says in s 193
"The Victorian Building Authority is established." (VERIFIED). The ABCB's NCC 2025 adoption page links Victorian
material under both "Victorian Building Authority" and "Building and Plumbing Commission", and bpc.vic.gov.au
exists but blocked automated access. Which name the regulator now uses is UNVERIFIED.

**X1. Not covered.** Case law; council local laws and planning schemes; Commonwealth-only laws named in the profiles
(for example corporations, spam and anti-money-laundering law), except where one sits in a row's Commonwealth cell.

**X2. Not decided.** Two proposals still await the Principal: spreading the 16 Australian-law test tasks across the
Commonwealth, VIC, NSW and QLD, and adding about 10 Australian tax and finance tasks. Nothing in this table assumes
either.
