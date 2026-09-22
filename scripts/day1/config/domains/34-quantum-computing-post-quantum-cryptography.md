# 34. Quantum Computing & Post-Quantum Cryptography
Hemisphere: Corporate   Owner: Valerie   Tier: C

**1. Strategic:** Preparing the Principal's cryptography and long-lived secrets for the quantum era: the post-quantum migration of the estate's and businesses' key exchange, signatures and stored ciphertext, and a working understanding of quantum computing itself so the timeline is estimated, not guessed. The frame is the one set for domain 1: defensive architecture, authorised testing of systems the Principal owns or is engaged to test, vulnerability research for remediation, and standards compliance. This domain does not exist to act against third-party systems; everything it produces is treated as sensitive tier at the approval gate. Its operational reason (C24) is harvest-now-decrypt-later: data the Principal encrypts today with classical algorithms can be captured now and broken later, so the migration is a present obligation.

**2. Technical:** The standardised post-quantum algorithms and their trade-offs (ML-KEM / Kyber for key encapsulation, ML-DSA / Dilithium and SLH-DSA / SPHINCS+ for signatures, under FIPS 203, 204 and 205); crypto-agility and hybrid classical-plus-PQC deployment so a scheme can be swapped without re-architecting; inventory of where cryptography actually lives in the stack; and enough quantum-computing literacy (qubits, error correction, the resource estimates behind Shor's and Grover's algorithms) to judge threat timelines rather than accept vendor claims.

**3. Compliance:** Australian first: ASD and ISM cryptographic guidance and its post-quantum transition advice; the Privacy Act 1988 for the confidentiality of personal data over its full retention life; the SOCI Act 2018 where critical infrastructure is involved. Then international: NIST FIPS 203/204/205 and SP 800-208, the NSA CNSA 2.0 suite and timelines, BSI and ETSI quantum-safe guidance, and Common Criteria where certification applies.

**4. Cognitive:** *Time-to-break versus time-to-protect.* Weigh every secret by how long it must stay confidential against how long until it is at risk, and migrate the longest-lived and highest-value data first. Prefer crypto-agility over any single algorithm bet, and treat "quantum is years away" as a reason to start, not to wait.

**5. Tooling:** Cryptographic inventory and discovery across the estate; PQC and hybrid configuration for TLS, SSH, VPN and code signing; test vectors and interoperability checks for ML-KEM and ML-DSA; migration roadmaps ranked by data lifetime; crypto-agility checklists mapped to CNSA 2.0 and ASD timelines.

**6. Agentic Delegation:** Isolated-context dispatch. Spawns with a clean, task-scoped context; reports completion or failure to the spawning director under a task ID; subject to the same tiered approval and Ouroboros logging as any other action.

**7. Temporal Evolution:** *Decay Mapping.* Track the published cryptographically-relevant-quantum-computing estimates as they move, algorithm standardisation and deprecation dates, the migration status of each system against its data-lifetime deadline, and key and certificate rotation onto post-quantum schemes.
