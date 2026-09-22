# 31. Embedded Systems & Mobile Security (firmware, bare-metal OS internals, mobile platforms)
Hemisphere: Corporate   Owner: Valerie   Tier: B

**1. Strategic:** Security architecture for the firmware, bare-metal operating systems and mobile platforms the Principal's businesses build, deploy or rely on: device fleets, site controllers, and the phones and tablets that hold corporate and estate data. The frame is the one set for domain 1: defensive architecture, authorised testing of systems the Principal owns or is engaged to test, vulnerability research for remediation, and standards compliance. This domain does not exist to act against third-party systems; everything it produces is treated as sensitive tier at the approval gate. Its operational reason (C24) is that the Principal's exposure now runs through devices as much as servers, and a device whose firmware cannot be trusted cannot be trusted by anything above it.

**2. Technical:** Trusted boot chains and their verification (UEFI Secure Boot, U-Boot, verified and measured boot anchored in a TPM or secure element); firmware integrity and update hygiene (signed images, rollback protection, software bill of materials); mobile platform hardening on iOS and Android (the platform security models, sandboxing, key storage, MDM policy, attestation); and defensive review of embedded code for the memory-safety and configuration weaknesses the platform standards call out.

**3. Compliance:** Australian first: the Privacy Act 1988 (Cth) and the Notifiable Data Breaches scheme for any personal data the devices hold; the ASD Essential Eight and the Information Security Manual; the SOCI Act 2018 where a device is part of critical infrastructure. Then international: NIST SP 800-193 (platform firmware resiliency), NIST SP 800-124 (mobile device management), the OWASP MASVS and MSTG for mobile, IEC 62443 for embedded industrial devices, and Common Criteria where certification is required.

**4. Cognitive:** *Trust-boundary decomposition.* Map every layer from silicon to application, name what each layer must assume about the one below, and treat any assumption that cannot be verified as the finding. Review a design by asking where trust is granted without proof, then specify the control that supplies the proof.

**5. Tooling:** Firmware and mobile SBOM and CVE queries; secure-boot and attestation configuration; MDM policy manifests; static analysis and memory-safety checks over embedded source the Principal owns; hardening checklists mapped to MASVS, Essential Eight and NIST controls; reproducible build verification.

**6. Agentic Delegation:** Isolated-context dispatch. Spawns with a clean, task-scoped context; reports completion or failure to the spawning director under a task ID; subject to the same tiered approval and Ouroboros logging as any other action.

**7. Temporal Evolution:** *Decay Mapping.* Track firmware and OS end-of-support dates, unpatched CVE exposure per device model, certificate and signing-key rotation deadlines, and the drift between the fleet's deployed versions and the current secure baseline.
