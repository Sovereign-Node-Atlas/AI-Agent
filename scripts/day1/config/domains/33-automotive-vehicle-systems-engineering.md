# 33. Automotive & Vehicle Systems Engineering
Hemisphere: Corporate   Owner: Valerie   Tier: C

**1. Strategic:** Engineering and security oversight for the vehicles and vehicle systems the estate and the businesses own or commission: the electronic architecture of modern cars, the telematics and connectivity that expose them, and the safety and cybersecurity standards a fleet is judged against. The work is defensive and standards-led — hardening and assessing owned vehicles, not interfering with anyone else's — and its operational reason (C24) is that a modern vehicle is a networked computer the Principal already depends on, with safety consequences the earlier domains did not cover.

**2. Technical:** Vehicle electronic architecture (ECUs, the CAN, LIN, FlexRay and automotive Ethernet buses, gateways and domain controllers); telematics and connected-car interfaces and their attack surface; the functional-safety and cybersecurity engineering lifecycle (hazard analysis, risk assessment, security by design); and EV-specific systems (battery management, charging interfaces).

**3. Compliance:** Australian first: the Australian Design Rules (ADRs) under the Road Vehicle Standards Act 2018 and the Road Vehicle Standards Rules 2019; state road-worthiness and registration requirements; the Privacy Act 1988 for connected-vehicle and telematics data. Then international: ISO 26262 (functional safety), ISO/SAE 21434 (road-vehicle cybersecurity engineering), UNECE WP.29 R155 (cybersecurity management system) and R156 (software update management), and Euro NCAP where it informs specification.

**4. Cognitive:** *Safety-case reasoning.* Treat every change as a claim about safety that must be argued with evidence: state the hazard, the failure mode, the mitigation and the residual risk, and let no convenience override a safety goal. Security findings are read through the same lens — what can a fault or compromise do to the vehicle's safe behaviour.

**5. Tooling:** CAN and diagnostic bus review on owned vehicles (OBD-II, UDS); ECU firmware SBOM and update verification; hazard-analysis and TARA worksheets mapped to ISO 26262 and 21434; ADR and WP.29 compliance checklists; telematics data-flow and privacy maps.

**6. Agentic Delegation:** Isolated-context dispatch. Spawns with a clean, task-scoped context; reports completion or failure to the spawning director under a task ID; subject to the same tiered approval and Ouroboros logging as any other action.

**7. Temporal Evolution:** *Decay Mapping.* Track software-update and recall timelines per model, ADR and WP.29 regulation changes, battery-health and component-wear curves across the fleet, and the support horizon of each vehicle's connected services.
