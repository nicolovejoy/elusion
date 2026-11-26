# Review report: auditable-decisions

- criterion: auditable-decisions
- review model: gpt-6-luna
- review prompt version: auditable-decisions/v1
- publication years: 2025-2026
- judge model: claude-opus-5-5
- judge prompt version: auditable-decisions/judge-v2
- judge effort: low
- date: 2026-11-26
- seed: 20261104

## Population

Every abstract published in 2025-2026 and scored under the review model and prompt
version above.

- population N: 23485 of 23521 abstracts published in 2025-2026, in ingestion order, not a random draw
- flagged (score >= 3): 10577
- discard (score < 3): 12908
- threshold: 3
- flagged sample: 400 drawn with seed 20261104 from the pair's flagged pile; 398 labeled by the judge
- discard sample: 400 drawn with seed 20261104 from the pair's discard pile; 400 labeled by the judge
- refused by the judge: 2 flagged and 0 of the discard sample have no label, because the judge model declined to answer about them; they are in the pile sizes and in no metric below

## Records (PRISMA-style counts)

PRISMA is the reporting standard for systematic reviews; its flow diagram is a
count of records at each stage. Identified: the distinct works each query returned.

- search, stemmed, `"technology-assisted review"`: 31
- search, stemmed, `"high-recall retrieval"`: 19
- search, stemmed, `"citation screening"`: 131
- search, stemmed, `"e-discovery" OR "electronic discovery"`: 100
- search, stemmed, `"LLM-as-a-judge"`: 2228
- search, stemmed, `"algorithmic accountability"`: 1231
- search, stemmed, `"explainable AI" AND audit`: 1383
- search, stemmed, `"automated decision-making"`: 2803
- search, stemmed, `"audit trail"`: 6162
- search, exact, `("AI agent" OR "AI agents" OR "LLM agent" OR "LLM agents" OR "LLM-based agent" OR "LLM-based agents" OR "agentic AI" OR "agentic system" OR "agentic systems" OR "agentic workflow" OR "agentic workflows" OR "autonomous agent" OR "autonomous agents") AND (audit OR audits OR auditing OR auditable OR auditability)`: 4142
- search, exact, `("AI agent" OR "AI agents" OR "LLM agent" OR "LLM agents" OR "LLM-based agent" OR "LLM-based agents" OR "agentic AI" OR "agentic system" OR "agentic systems" OR "agentic workflow" OR "agentic workflows" OR "autonomous agent" OR "autonomous agents") AND (accountability OR accountable)`: 2175
- search, exact, `("AI agent" OR "AI agents" OR "LLM agent" OR "LLM agents" OR "LLM-based agent" OR "LLM-based agents" OR "agentic AI" OR "agentic system" OR "agentic systems" OR "agentic workflow" OR "agentic workflows" OR "autonomous agent" OR "autonomous agents") AND (traceability OR traceable OR provenance)`: 2409
- search, exact, `("AI agent" OR "AI agents" OR "LLM agent" OR "LLM agents" OR "LLM-based agent" OR "LLM-based agents" OR "agentic AI" OR "agentic system" OR "agentic systems" OR "agentic workflow" OR "agentic workflows" OR "autonomous agent" OR "autonomous agents") AND ("human oversight" OR "human-in-the-loop")`: 2116
- search, exact, `("AI agent" OR "AI agents" OR "LLM agent" OR "LLM agents" OR "LLM-based agent" OR "LLM-based agents" OR "agentic AI" OR "agentic system" OR "agentic systems" OR "agentic workflow" OR "agentic workflows" OR "autonomous agent" OR "autonomous agents") AND (logging OR observability OR "audit log" OR "audit logs")`: 1224
- search, exact, `"AI audit" OR "AI audits" OR "AI auditing" OR "algorithm audit" OR "algorithm audits" OR "algorithmic audit" OR "algorithmic audits" OR "algorithmic auditing"`: 1276
- search, exact, `"decision provenance"`: 94
- citation, works citing Cormack and Grossman 2014 (W2029075138): 23
- citation, works citing O'Mara-Eves and others 2015 (W4313371821): 75
- citation, works citing Raji and others 2020 (W3001807593): 75
- citation, works citing Zheng and others 2023 (W7133239970): 75
- citation, works citing Chan and others 2024 (W4399364360): 75

- records identified, all routes: 27847
- duplicates removed: 4326
- distinct works: 23521
- abstracts stored: 23521 (a work whose abstract changed between two harvests is stored once per text)
- screened: 23485
- not screened: 36
- excluded (score < 3): 12908
- included (score >= 3): 10577

## Metrics

Point estimate and Clopper-Pearson 95% interval; ground truth is the judge's label.

- precision: 0.734 [0.687, 0.776] (k/n = 292/398)
- elusion: 0.295 [0.251, 0.342] (k/n = 118/400)
- richness: 0.493 [0.447, 0.538], derived from the two pile intervals
- recall: 0.671 [0.622, 0.717], derived from the two pile intervals

- precision: fraction of flagged abstracts that are truly relevant.
- elusion: relevant rate inside the discard pile, measured by sampling it.
- richness: fraction of the whole population that is relevant.
- recall: fraction of truly relevant abstracts the system flagged.

Richness and recall are not measured on a sample of their own: they combine the
precision and elusion intervals weighted by pile sizes. Both are monotone in
precision and elusion, and the two samples are independent, so each derived
interval covers the true value with probability at least about 90% (0.95 × 0.95),
not 95%.

Precision by score, from the same flagged sample; a description, not a separate
estimate of the population.

- score 3: 0.546 [0.474, 0.618] (k/n = 106/194)
- score 4: 0.912 [0.864, 0.947] (k/n = 186/204)

## Harvest recall

The fraction of on-topic works that the search terms returned at all. Elusion
cannot see it: a work no term returned is not in the discard pile. It is measured
on the citation seeds, works found by following citations to an anchor paper and
not by any search word. A seed that the review flagged and the judge labeled
relevant is an on-topic work known without the terms; harvest recall is the
fraction of those that a search term also returned.

- harvest recall: 0.256 [0.181, 0.344] (k/n = 31/121)

- citation seeds drawn: 322
- of those, flagged by the review: 149
- of those, labeled relevant by the judge: 121

Its limit: a seed the review discarded is never judged, so a relevant seed the
screen missed is absent from the denominator. And the seeds are works that cite
five chosen papers, not a random sample of the on-topic literature.

## Judge agreement with the owner

Fraction of the owner's labels the judge matched. The blind figure is on his
labels as first given; the other is after he re-read the judge's disagreements
and changed some, so it is not a blind measurement.

- agreement, blind labels: 0.667 [0.472, 0.827] (k/n = 20/30)
- agreement, after the owner revised 3 labels: 0.767 [0.577, 0.901] (k/n = 23/30)

## Rationale quality

The grader (claude-opus-5-5) read each abstract of the flagged sample with the reviewer's
rationale and quote and said whether the rationale is a faithful reading. The
quote check is mechanical: does the quote occur in the abstract.

- grader model: claude-opus-5-5
- rationale sound: 0.769 [0.724, 0.809] (k/n = 306/398)
- quote verbatim: 0.970 [0.948, 0.984] (k/n = 386/398)

Rationale soundness against the judge's label, over the flagged sample:

- sound and relevant: 260
- sound and not relevant: 46
- unsound and relevant: 32
- unsound and not relevant: 60

## Cost

- review run: $5.8448 (the cost of this population's scores, summed)
- review jobs that scored this population: 2 (2 local)
- judge run: $4.6998 (whole criterion, every review pair: judge-runs ledger)
- grade run: $2.3740 (this population: grade-runs ledger)
- total for this population (review + grade): $8.2189

A billed call that left no score (a refusal, a truncated answer) is in its review
job's own total and is not in this sum.

## Wall-clock

- review run, summed over 2 review_jobs rows (each from its start to its finish): 2:00:05

## Works kept

The review kept 10577 abstracts, the flagged pile. Listed here are the
292 abstracts of the flagged sample the judge labeled relevant, by OpenAlex work id and title.

- W7203692484: Offline Ambient-Controlled Latent Diffusion: Architecture, Telemetry, and On-Device Evaluation
- W7203458573: Accountability Without a Trusted Center: A Federated Governance Testbed for Multi-Principal AI Agents
- W7164503939: RFC-ATF-11: Agent Trust Fabric — Intake and Predicate Formation Layer and Governance Decision Convergence Layer. Pre-Execution Governance Contract Formation and Multi-Source Decision Aggregation with Typed Composite Boundary Verdicts
- W4416270300: ORCHID: Orchestrated Retrieval-Augmented Classification with Human-in-the-Loop Intelligent Decision-Making for High-Risk Property
- W7204664253: A Reference Architecture for Governing Autonomous, Non-Human Identities in the Regulated Enterprise
- W7212425454: GLAL v0.9 — Governance Layer for Agentic AI
- W7166506284: Can LLMs Judge Better Than They Generate? Evaluating Task Asymmetry, Mechanistic Interpretability and Transferability for In-Context QA
- W7224021345: Research artefact: Criteria-Governed LLM Decision Workflows — operator, ablation package, synthetic inputs and analysis scripts
- W7222850369: HACK Wave 2 — The PQC Gambit: FOXACID, MUSCULAR, Hive, EDGEHILL, SP 800-207 Zero Trust, NIST AI RMF, CNSA 2.0, and FIPS 140-3 — Preregistered Adversarial Batteries Against a Deterministic AI Governance Stack (48/48 PASS, Receipt-First)
- W7163749987: Wisdom Is Not Capability: A Self-Validating Measurement Standard for Grounding-Limited AI Agents
- W7204227414: Benchmarking LLM Judges for Voice-Agent Evaluation: Reliability, Calibration, and Human Oversight
- W7225017352: Can an LLM Agent's "Done" Be Trusted? Scoring a Desktop Agent's Success Reports Against World-State Ground Truth
- W7171789780: AI-Enabled Non-Conformance Prediction and Corrective Action Management in Saudi Mega Infrastructure Projects
- W7160609323: ClawGuard: Out-of-Band Detection of LLM Agent Workflow Hijacking via EM Side Channel
- W7213281051: The Model Proposes, the Code Disposes: A Pre-Registered Ablation of a Verifier-and-Acceptance Stage in an LLM-Orchestrated Offensive-Security Agent
- W7106808122: Decentralized and Explainable Credit Scoring: A Trust-Centric Fintech Approach
- W7135234160: An Explainable Cognitive Bidding Agent for Electricity Markets: A Framework for Zero-Shot Generalization using Large Language Models
- W7206199743: Post-hoc Alignment of LLM-judges to Human Judgment Distribution
- W7217132929: LLM-based Log Analysis - Evaluating LLM-Based Log Analysis in Industrial CI Environ ments: Technical Metrics, Practitioner Perspectives and Per ceived Workflow Impact
- W7155615000: Empowering Multimodal Learning Analytics using Agentic AI: A Comprehensive Platform for Simulation-based Clinical Training with Intelligent Assessment
- W7196982302: The Epistemic-Attentional Scissors: A Conceptual Framework for Epistemic Abdication and Stratification in AI-Mediated Communication
- W7171490130: AI评审中的范式污染机制研究
- W7140090118: Response to NCCoE Concept Paper: Intent Conveyance as the Missing Layer in AI Agent Identity and Authorization
- W7162847151: Artificial Intelligence in Child Welfare Decision-Making: Ethical Frameworks and Practice Implications
- W7216140969: HYBRID BI MODEL: KOLABORASI MACHINE LEARNING DAN VISUAL ANALYTICS UNTUK PENINGKATAN KETEPATAN PREDIKSI BISNIS
- W7155068324: Semantic Consensus: Process-Aware Conflict Detection and Resolution for Enterprise Multi-Agent LLM Systems
- W7128736886: JudgeBoard: Benchmarking and Enhancing Small Language Models for Reasoning Evaluation
- W7213519748: Bounding the Machine: A Cross-Case Study of Governed Autonomy in Five Enterprise Agentic Systems
- W7171431789: Answerable Judgment: A Formal Architecture for Decisions That Remain Open to Reality
- W7202400877: Agent Safety Should Be a Runtime Contract
- W7224805554: Derivation-relative structural admissibility for pre-execution control of LLM-agent workflows
- W7118790823: Chas122/marain-protocol: v0.1.0 — Protocol Specification
- W7202257380: Conformalized time-series foundation models for commodity prices: replication material
- W7225391237: The Pluralism Gate: When Should AI Agents Decide Under Irreducible Value Conflict?
- W7228147136: When Does Asynchronous Review Catch an AI Agent in Time? A Simulation Study of Monitoring under Staffing Gaps, Auditor Error, Bursts and Load
- W7118097266: VECTR: Towards a Reliability Framework for Agentic AI in Drug Development
- W7171737493: Cognitive Agents for Bridge Inspection Prioritization: An Empirical Test of Predictability and Transparency Using the National Bridge Inventory (NBI)
- W7212402364: AI Governance Scan Engines for Enterprise Data Ecosystems: A RAG‑Driven Architecture for Continuous Compliance and Risk Detection
- W7142765533: AI Audit Control Matrix 1.0 (v0.1): A Bounded AI Auditability Architecture for Low-Observability Environments
- W7170075042: Concord: An Agreement-Aware Multi-Adjudication Pipeline for LLM Evaluation
- W7115885076: Automating Scientific Evaluation: A Multi-Agent Framework for Transparent and Trustworthy Peer Review
- W7160039611: Explainable AI in Autonomous Driving: A Comparative Study of Post-Hoc Explanations for YOLOv8
- W7225388199: Trust Responsive Runtime Authorization for Agentic AI with Empirical Characterization of Policy Behavior and Parameter Sensitivity
- W7166833586: Governing Human–AI Collaboration: From Formal Theory to Working Systems
- W7165849592: RLMLite: Reasoning Observability for Multi-Agent AI Systems
- W7214957445: TOPOGUARD-EVOCHAIN: Policy-Aware Federated Learning with Topological Representations and Blockchain-Based Model Lineage
- W7136148630: Fairness Audit Playbook: A Four-Phase Operational Framework for Detecting and Diagnosing Algorithmic Bias
- W7169511782: Digital Pantheon: Simulating and Auditing Coalition Formation with LLM Agents
- W7164669479: An Agent You Cannot Hold to Account Is an Agent You Do Not Control
- W7123405734: PROTEA: Securing Robot Task Planning and Execution
- W7165489119: Constitutional Enforcement as Auditability Infrastructure: Deterministic Rejection Trails for Self-Evolving Software Agents
- W7111744129: Findings of the WMT25 Multilingual Instruction Shared Task: Persistent Hurdles in Reasoning, Generation, and Evaluation
- W7168250390: Towards Autonomous and Auditable Medical Imaging Model Development
- W7223475289: Before an Event Is Named: Distributed Observation of AI-Agent Anomalies with X-Finder and AAWACS
- W7202257452: A Hybrid Graph Neural Network and Explainable AI Framework for Financial Fraud Detection
- W7223705110: CxBOM: A Context Bill of Materials and Signed Tool-Definition Pinning for LLM Agents
- W7171826874: Substantive Equality in the Age of Generative and Agentic AI: Rethinking Creditworthiness
- W7225659774: Secure AI Agent Infrastructure
- W7223078747: When AI Becomes a Business Actor: Closing the Accountability Gap in Agentic Enterprises
- W7152003749: Beyond LLM-as-a-Judge: Deterministic Metrics for Multilingual Generative Text Evaluation
- W7141343600: A Machine Learning Framework for Root Cause Analysis in ISO-Compliant Manufacturing Using RFID Data
- W7159579358: Modeling Clinical Concern Trajectories in Language Model Agents
- W7133632941: AI-to-AI Diplomacy: Why LLM-Only Negotiation Fails Under Zero Trust and How SLE-Governed Systems Enable Proof-Carrying Compacts
- W7203723237: Human in the Loop Agentic Recruitment: Balancing Automation, Recruiter Productivity, and Decision Quality
- W7213341500: IntentLedger: Coordinating Competing ProposedEffects in Agentic Institutions
- W7155641894: Ternary Logic as Constitutional Substrate: A Triadic Computational Governance Architecture for High-Stakes Automated Decision-Making
- W7204626877: A Perturbational-Complexity Surrogate for a Dead Meta-Cognition Channel: Preregistered Honest Negative, Falsifier Amendment, Self-Caught Type I Error, Instrument Defect, and Cutover by a Different Route
- W7224559496: MetaBench: Benchmarking Two-Stage Reranking and Top-N Screening for Meta-Analyses
- W7214723085: From Assistant to Orchestrator: An Experience Report on Human–AI–Agent Collaboration in Reproducible Lakehouse Experimentation
- W7213631281: Agent-friendly Provenance Capture with con-duct
- W7166320079: From Symbolic Personas to an Auditable Cognitive Runtime: Patent-to-Code Traceability and Lessons in Metacontrol from the Nemosine Nous System
- W7203904815: Reconceptualizing Observability for Agentic AI Systems: A Trace-Centric Architecture for Interpreting Non-Deterministic Workflow Behavior
- W7128817551: Information systems auditing of psychiatric diagnoses using context-enriched QA-based language models
- W7171856140: Computation Is Not Authority to Act: A Pre-Execution Authorization Layer for AI Agents, Cloud, Telecom, Payments, and Critical Infrastructure
- W7162403089: Cumulating Evolutionary Volatility: Automated Judgment, Dialectical Development, and the Semantic Economy of Unaccountable Composition
- W7162877229: Autonomous Professional Services Delivery with Quality Assurance
- W7133199154: RAMP: A Protocol for Human Oversight of Autonomous Agents
- W7204450211: AIVORIX OS™: Foundations of a Governance-Centric AI Operating System — Volume I
- W7215075673: What the Score Measured: Failure Attribution in an Agent Memory Evaluation
- W7172108167: A Corrigible Artificial Self Separating Identity Continuity from Instance Survival in Persistent AI Agents
- W7172108718: A Governance Framework for AI-Assisted CRM Workflows: Agentforce Deployment, Risk Management, and Organizational Readiness in Enterprise Salesforce Environments
- W7147115802: "Mycelium for Physical Agents — Trust Infrastructure for Autonomous Vehicles, Humanoid Robots, and the Machines That Must Earn Their Place"
- W7137967206: POLARIS: Typed Planning and Governed Execution for Agentic AI in Back-Office Automation
- W7164702531: Calibrated Multi-Agent Debate for Explainable AI: Traceable Decision-Making Across Domains
- W7213923006: Paper-less FrameWorld
- W7152374157: Who Leads When AI Acts? Authority and Accountability in Agentic Organizations
- W7167718160: Governance-Aware Agentic AI for Enterprise Engineering Systems: A Design-Science Reference Architecture and Quantitative Risk-Control Model
- W7151961372: Управление рисками искусственного интеллекта при обеспечении государственных контрактов банком
- W7167624816: Evidence Authority After Retrieval: A Control-Surface Framework for RAG and Agentic Systems
- W7223097196: Below the Waterline: A Survey of Agentic AI Security, Shadow AI Governance, and LLM Supply-Chain Risk in the Enterprise
- W7114930985: Counterfactual Reasoning and Uncertainty Quantification for AI-Assisted Decision Making
- W7161947063: Ethics-Aware AI Agents for Adaptive Education: A Multi-Agent Theoretical Framework
- W7224117111: Replication Package - The Impact of AI Autonomy on Developer Verification Strategies
- W7161171844: From Feature Variability to Agency Variability: A Software Product-Line Engineering Framework for Governed Agentic AI Systems
- W7166700222: A Multi-Dataset Benchmark for Evaluating LLM Agents in Microservice Failure Diagnosis
- W7204662362: Board Oversight of Artificial Intelligence: Redefining Corporate Governance for Algorithmic Decision-Making
- W4415488058: Integrasi Kepemimpinan Transformasional dan Transformasi Digital dalam Penguatan Doktrin Pertahanan Indonesia Berbasis Kejuangan Integritas dan Pancasila
- W7211896969: Adam Prism: A Sovereignty-First Architecture for Verified, Deterministic LLM Agents
- W7133317350: Fairness in AI: Evaluating and Mitigating Bias in Decision-Making Models
- W7142614737: MIFT-CVS: Cryptographic Provenance and Identity-Field Attribution in Stochastic Control
- W7216975217: RFID-based cadet access management system with face recognition and cctv integration
- W7131374287: Frontier Model Language (FML) + Guardian Protocol (GP-LACF)
- W7171618027: The Autonomous User Relationships Agent (AURA) Council Protocol: Persistent Multi-Agent Governance Through Shared-Pool, Role- Monogamous Intelligence
- W7221688869: Prometheus Platform
- W7172409845: Explainable AI for Multi-Modal Insider Threat Detection: A CNN-LSTM-Autoencoder Framework with SHAP-LIME Comparative Analysis
- W7224997473: Kept or conceded: stance fidelity of language models under repeated user pushback
- W7226005973: AgentGuard: A Framework-Agnostic Runtime Governance Architecture for Autonomous AI Agents
- W7212289344: Gateway-Centered Predictive and Preventive Security Framework for Agentic AI Systems
- W7155548464: Agentic Data Engineering: LLM-Augmented Pipeline Generation, Self-Healing ETL, and Autonomous Repair
- W7160254567: AI Alignment via Incentives and Correction
- W7204553006: Do User-Authored Permission Policies Improve Protection Against AI Agent Overreach?
- W7161009153: Pedagogue-code: an interpretable code generation framework based on dual attention mechanism
- W7212382110: GLAL - Governance Layer for Agentic AI
- W7224888516: Canonics: Versioning the Frame, Not the Facts — Vigency in AI Memory After Judgment Expires
- W7204628592: Blockchain-anchored multi-discriminator GAN architecture for real-time zero-day attack detection in social IoT edge networks with explainable audit trails
- W7169651949: Whose Memory Is this? Measuring and Fencing Self-Attribution of Injected Memory in LLM Agents
- W7119739968: AgentDevel: Reframing Self-Evolving LLM Agents as Release Engineering
- W7171662232: F(AI)2R: Who Did What, and Who Checked? Verifiable AI Provenance as an Executable Skill
- W7214655119: Discussions on Participatory Harm Auditing of AI within a Cultural Heritage Context - Workshop at DARIAH 2026
- W7222323475: Can AI-enabled smart cities advance the constitutional right to life? A rights-based index, governance-opacity audit, and robustness analysis for sustainable urban India
- W7124145696: Trustworthy Agentic AI: Balancing Autonomy with Human Oversight
- W7112775984: Automating Insight: AI-Driven KSA Extraction for Career Clarity
- W7213990342: Tools for Aggregating, Filtering, Sorting, and Auditing Social Media
- W4417262448: Agentic Program Repair From Test Failures at Scale: A Neuro-Symbolic Approach With Static Analysis and Test Execution Feedback
- W7166527720: PROMPTING AS METHOD: DISCLOSURE THRESHOLDS FOR AI-ASSISTED LITERATURE SYNTHESIS
- W7163590206: Algorithmic Due Process: A Comparative Study of Indian and Eu Ai Governance Models
- W7160984332: Tollivar: Using AI to support proactive ethical alignment in organisational decision-making
- W7221135396: Sovereign Acceptance Runtime: Verifiable Human Ratification and Evidentiary Audit Trails for Autonomous AI Agents under the EU AI Act
- W7143541512: AI-Assisted Workflow Orchestration in Regulated Healthcare Contact Centers: Architecture, Governance, and Human-in-the-Loop Design Patterns
- W7169731679: Process-Aware LLM-Agent Scaffolds for Metric-Based Microservice Root-Cause Analysis with Evidence-Trace Scoring
- W7225808791: Artificial Intelligence in Climate Policy Governance: Evidence Boundaries, Sustainability Risks and Institutional Safeguards
- W7159608663: AQUILES: A Production Architecture for Hybrid Agents with Deterministic Decisions in Regulated Domains — The HADD Paradigm: Closing the Determinism–Expressiveness Gap in LLM-Based Agent Systems
- W7148877340: TSQueryBench: LLM-as-a-Judge for Time Series Explanations
- W4415276150: Confidence as a Reward: Transforming LLMs into Reward Models
- W7204196941: SΔϕ-75 — Where Does an AI End? Fork/Merge Identity, Operational Body, and Final Cost-Return Coordinates (v1.0, AI-Native Package)
- W7133362489: CRSA-1 EU Edition: Compositional Runtime Safety Attestation Protocol — Compositional Safety Compliance Profile for EU AI Act Regulation 2024/1689
- W7163540414: Cascading Hallucination in Agentic RAG: The CHARM Framework for Detection and Mitigation
- W4416017238: LLM-as-a-Judge in Entity Retrieval: Assessing Explicit and Implicit Relevance
- W7168378568: Explainable Action Chains in Agentic AI: Enhancing Transparency and Trust
- W7154607292: Drift Detection: When Values Shift | Geometry of Trust | Mathematics - Lesson 3
- W7214995105: The Ammonix ECG Agent: A Local Specialized AI Agent to Transparently Read and Discuss 12-Lead Electrocardiograms
- W7162121615: Trust-DT: A Framework for Trustworthy Healthcare Digital Twins through Synergistic Explainability and Uncertainty Quantification
- W4416036640: Analyzing Uncertainty of LLM-as-a-Judge: Interval Evaluations with Conformal Prediction
- W7207643982: BeVerA-KG: Behaviour-Driven Design-Time Verification of Neuro-Symbolic Agents via Knowledge Graphs for Trustworthy Humanitarian Edge Systems
- W7163898106: payments-agent-audit: Governance Patterns for Autonomous AI Agents in Regulated Payments
- W7140290274: Geometric CONSTITUTIONAL AI / Uptergrove scale: The Agentic Solid Swarm Architecture for Deterministic, ESG-Optimized LLM Governance Uptergrove solution
- W7161276174: Replication repository for LLM Judge
- W4416873483: A Method for Enhancing Invocation Capabilities in Tool Calling with LLM-as-a-Judge System
- W7168494639: SolidState: A Deterministic Finite State Machine Runtime for Safe and Concurrent Multi-Agent Orchestration
- W7165500616: Detecting fake news using machine learning methods
- W7224909762: Workflow-Governed Automated Validation of AI Agent Task Assignments in Microsoft Dynamics 365 ERP
- W7225631668: ACTRA™ — Autonomous Control, Trust & Revenue Assurance Infrastructure: Proprietary Commercial Software Intellectual Property and Enterprise, OEM & Sector Licensing Platform (Version 1.1.0)
- W7214889342: Governing the Edge: Automating Commercial Property and Casualty Insurance Underwriting via a Hybrid Local-Cloud Multi-Agent Framework
- W7223666191: PROMETHEUS: An Enterprise AgenticOps Reference Architecture Framework
- W7226010000: Food Critic: An Agentic Visualization System for Auditable Restaurant-Safety Reasoning
- W7218916003: The Agentic Brain: Dual-Process Metacognition, Evidence-Gated Authority, and Runtime Governance
- W7134884983: WhyLab v3: Cross-Model Evaluation of a Causal Safety Audit for Self-Improving LLM Agents
- W7214013217: CircOS AI Risk Assessment and Responsible AI Guidance
- W4416826785: Scaling qualitative insight: An agentic workflow for analysing student voices
- W7118001790: HYPER-OPSEC Sovereign Cortex (HOSC) A Constitution-First, Legally Enforced Sovereign Reference Architecture for Autonomic Defence of Classified Sovereign Operational Infrastructures
- W7221828208: Governing AI and Blockchain in Finance: A Layered Trust Framework
- W7223334133: The Delayed Countdown Method for Validating Review-Oriented AI Workflows in eDiscovery
- W7154494865: Lume-Causal: Deterministic Causal Governance for Autonomous Systems
- W4414444992: SAFi: A Self-Alignment Framework for Verifiable Runtime Governance of Large Language Models
- W7169529007: Digital Monitoring and the Reconfiguration of Control and Expertise in Manufacturing
- W7223190248: Do Language Models Write Away Their Own Oversight? A Preregistered Test of Constitutional Self-Revision
- W7226000492: Linguistic Inequity in Large Language Models: Assessing Bias, Multilingual Performance, and Dialect Erasure in ChatGPT
- W7148607826: From Black Boxes to Intelligent Agents: A New Paradigm for Artificial Intelligence in Nuclear Medicine
- W7204191359: ClawProBench: Trace-Aware Evaluation of AI Agents with Runtime Coverage and Frozen Workplace-Style Holdouts
- W7171871638: TRACE: Transparent Recording of AI-assisted Collaboration Experiments
- W7162409395: Behind EvoMap: Characterizing a Self-Evolving Agent-to-Agent Collaboration Network
- W7221446570: AI-Powered Invoice Processing
- W7204532752: Making Clinical Language Models Auditable: Concept-Guided Fine-Tuning for Robust Prediction
- W7225822593: Trajectory Memory Compression for Long-Horizon Dialogue Agents with Provenance Preservation
- W7167479239: Graceful Degradation and Hybrid Gating in a Multi-Agent LLM System: An Applied Case Study
- W7137831254: Exploring the Effects of Alignment on Numerical Bias in Large Language Models
- W4415044784: LLM Agents as Programmable Subjects: Assays and Benchmarks for Agentic Behavior and Alignment
- W7166532342: Finite Operational Structure Theory: A Mathematical Theory of Finite Operational Cuts
- W7171644841: Towards adaptive categories: Dimensional governance for agentic AI
- W7213616416: docprep: a reference implementation of the blind-consolidation protocol
- W7119525918: Architecting Agentic Communities using Design Patterns
- W7223666462: humancollab: A Verifiable Trust Layer Between Humans, Businesses and AI Agents
- W7171990495: Blockchain-Based Multimodal Misinformation Detection System
- W7162675778: TRACES: Proactive Safety Auditing for Multi-Turn LLM Agents via Trajectory-State Modeling
- W7160934772: Two Ways to De-Bias an LLM-as-a-Judge: A Continuous-Score Comparison of Hierarchical Bayesian Calibration and Neural-ODE Score Transport
- W7204108889: Agentic AI Requirements Toolkit
- W7225737068: Neuro-Symbolic Artificial Intelligence for Secure, Explainable, and Intelligent Banking and FinTech Systems: Fraud Detection, Credit Risk, Financial Forecasting, and Transaction Security
- W4410850209: VISTA: Verifiable Infrastructure for Secure & Transparent Agents
- W7160526150: Ephemeral Agent Credentialing: A Security Architecture Pattern for Autonomous AI Agents (v1.4)
- W7115596344: FIONA: A Phase-Gated Architecture for Structural Coherence in Long-Horizon Reasoning.
- W7221611284: An Agentic AI Framework for ESG Disclosure Monitoring and Governance Evidence
- W7223030057: Autonomous adaptive spectral analysis with an LLM agent for self-driving laboratories
- W7203918292: Stopping and Routing LLM Judge Panels
- W7154887581: Bounded Decision Regulation in an OTIP Continuity Sandbox: A Frozen Internal Evidence Layer
- W4416971782: Agent Action Guard: Safe AI Agents Through Action Classifier
- W7224403421: COMPARATIVE ANALYSIS OF THE APPLICABILITY OF LANGUAGE MODELS FOR GENERATING STRUCTURED MESSAGES IN RUSSIAN
- W7167786140: TENIR-Gov: Governance Middleware for AI-Enabled Operational Systems
- W7204784906: BeaconXAI: Budgeted Counter-Evidence Shortlisting for Portable AI Audits (Формирование ограниченного по бюджету списка контрсвидетельств для переносимого аудита ИИ)
- W7224386190: Pad-Lattice: A Visual Protocol for AI Agent Control on Physical and Virtual Surfaces
- W7128638099: VERITARCH: A Governance Architecture for Multi-Agent AI Systems Based on Decentralized Exchange Protocol Principles
- W7171667950: Algorithmic Governance in Financial Institutions
- W7204047455: Tamper-Evident Audit Trails for AI Agent Tool Invocations: Design Pitfalls, Cross-Language Conformance, and a Reference Implementation
- W7153752161: NonbAInary: How Does AI Depict Gender?
- W7161144712: SAL‑C: A Runtime Constitutional Governance Layer for Autonomous Systems
- W7163822213: Surface Contamination in AI-Assisted Audits
- W7225434052: DualTrust: Asymmetric Bayesian Reputation and Cryptographic Gating for Autonomous AI Agents
- W7105898814: Designing a Virtuous Automated Decision system using multiple agents with Personas and human-in-the-loop
- W7161737298: Izdeg by Trizloom: The Independent Truth Layer for Autonomous AI Agents
- W7140088920: A conceptual framework for Zero-Error AI agents using Digital AI Passport approach
- W7215080133: A Governance and Security Framework for Enterprise Adoption of Generative AI Digital Communications
- W7214538152: Claims vs. Evidence: A Measured Audit of an AI-Assisted Rust Kernel and an Oracle-Gated Workflow for LLM Proof Completion
- W7155533616: Omission Constraints Decay While Commission Constraints Persist in Long-Context LLM Agents
- W7225913376: The Future Accountant
- W7160404680: From Sessions to Trajectories: ∆-Coherence, Relational Memory, and the Emergence of Computational Identity in AI Systems
- W7155156080: Process Swarm: A Deterministic Architecture for Multi-Artifact Integrity in Agentic Systems
- W7204500145: The Empire, Long Divided, Must Unite: Architectural Convergence in Three LLM Agent Harnesses
- W7134278925: ESAA-Security: An Event-Sourced, Verifiable Architecture for Agent-Assisted Security Audits of AI-Generated Code
- W7148428389: Perceive, Plan, Act, Self-Correct: An Architectural Framework for Goal-Directed Agentic AI Systems
- W4415398699: Automatic Validation of Unstructured LLM-Generated Outputs: An Approach for Q&A Applications in Process Automation
- W4416036248: CheckEval: A reliable LLM-as-a-Judge framework for evaluating text generation using checklists
- W7164990121: File System as a Substrate for Memory and Cognition in AI Agents
- W7203547803: Accountable Reality Transition (ART): A Falsifiable Framework for Autonomous Action Across Trust Boundaries
- W7166510706: Fractal Custody Objects: route-comparable chain-of-custody for deterministic computational biology and AI-agent provenance
- W7169609822: BrainPilot: Automating Brain Discovery with Agentic Research
- W7160993135: An Autonomic 5G Edge Security Framework via Augmented MAPE-K Loops and Deep learning
- W7217677966: Policy-Over-Model Guardrails — An Agentic Mlops Control Plane For Safe Autonomy In Production Engineering And Infra
- W7166829865: CLExEval: A Human-in-the-Loop Framework for Qualitative Evaluation of LLM Clinical Reasoning
- W7222950903: Observability for Autonomous Agents: Tracing, Failure Taxonomies, and Reliability SLOs for Multi-Step Agent Workflows
- W7171308103: Advanced Ensemble Way Automated Resume Screening Using Dual-Level Features and Blockchain
- W7220669358: EVE-COVARA: Certified Recursive Self-Improvement via Square-Root Reserve Transport, Memory-Preserving Adaptation, and Auditable Self-Modification
- W7160892908: Nautilus Compass: Black-box Persona Drift Detection for Production LLM Agents
- W7170032223: Agents in the Wild: Where Research Meets Deployment
- W7211992786: When Engagement Masquerades as Learning: Auditing Extraction in Educational AI
- W7163587277: DELTΔX NextGenV12 Thinking — Non-Autonomous AI Governance Corpus, Controlled Agentic Runtime Framework, and Adversarial First-Shot Audit Evidence Pack.
- W7133790580: Trustworthy AI Posture (TAIP): A Framework for Continuous AI Assurance of Agentic Systems at Horizontal and Vertical scale
- W7225773237: An auditable harness for biomolecular dynamics simulations by AI agents
- W7165752568: AutoSpec: Safety Rule Evolution for LLM Agents via Inductive Logic Programming
- W7226022526: Agentic Datasets: Giving AI Agents More Autonomy Without Losing Control
- W7162109800: Cyber-Resilient Recovery Market Clearing via Trust-Bounded Agentic AI and Auditable Settlement
- W7162342290: Closing the Compliance Gap: AI in Pharma Deviation Capture
- W7213228345: ANASSA: An Agentic AI Orchestration Framework for Spatial Intelligence
- W7222679343: The Locality Gap: Replication package (code, run-level results, tables and figures)
- W7220745000: Formal Runtime Verification for Tool-Using LLM Agents: An Offline Same-Benchmark Study on AgentDojo and STAC
- W7167628002: Memory-Orchestrated Semantic System (MOSS): An Auditable Agentic Memory Architecture
- W7155356345: Balanced Accuracy: The Right Metric for Evaluating LLM Judges - Explained through Youden’s J statistic
- W7165664330: BabelJudge: Measuring LLM-as-a-Judge Reliability Across Languages
- W7222442095: AI Agents in Trade Execution, Market Surveillance and Tokenised Infrastructure Debt
- W4412867986: AI-Driven Risk Assessment in FinTech: Enhancing Accuracy and Regulatory Compliance
- W7212210206: ADVANCING SECURITY TECHNICAL IMPLEMENTATION GUIDE COMPLIANCE AUTOMATION: A PROOF-OF-CONCEPT IMPLEMENTATION FOR CONTINUOUS MONITORING
- W7164886469: ILS-Bench: Investor Language-to-Suitability Benchmark
- W7162799747: Representation Signatures and Risk-Feedback Alignment in LLM Trading Agents
- W7168397525: OAcquire: A Multi-Source Corpus Acquisition Layer for Agentic Systematic Reviews
- W7217604316: The Methodological Construction of Artificial Intelligence-Based Ijtihad in the Determination of Islamic Law: Perspectives of Uṣūl Al-Fiqh and Maqāṣid Al-Syarī‘Ah in the Digital Era
- W7204091095: Transformer-Based Continuous Authentication for Secure E-Learning: A Conceptual Framework Integrating Keystroke Dynamics and Blockchain
- W4415158033: MLRC-Bench: Can Language Agents Solve Machine Learning Research Challenges?
- W7203978893: DITA — Dynamic Interaction Trace Audit: Public Technical Disclosure and Audit Evidence
- W7203621245: Engineering Reliable Coding Agents: Evaluating and Operating the System Around the Model
- W7156283420: A CLOUD-EDGE AGENT ARCHITECTURE FOR LOW-LATENCY,GRAPH-CONSTRAINED RETRIEVAL AND SCHEMA-FIRST ORCHESTRATION
- W7216477558: Designing the Governance of a Container Inspection Automation System at the Port
- W7142964750: Cross-Model Peer Review Dataset: AI Blind Reviews of The Force Multiplier Index
- W7130510307: Algorithmic Economies: Mathematical Intelligence, Decision Systems, and Global Innovation Dynamics
- W7155538830: Responsible artificial intelligence integration framework for psychiatric guidelines
- W7219867259: Deceptive Technical Debt in Autonomous Maintenance: Cognitive Asymmetries and Heterogeneous Auditing Pipelines
- W7131415881: Reversibility as a Design Standard,Why civilization-scale systems must preserve the power to roll back
- W7135026404: Agentic Control Center for Data Product Optimization
- W7165364094: AICOS: Mathematical Foundations of Decision Safety Infrastructure for Reliable Artificial Intelligence Systems
- W4415233743: Responsible Agentic Reasoning and AI Agents: A Critical Survey
- W7106807189: Analyzing Uncertainty of LLM-as-a-Judge: Interval Evaluations with Conformal Prediction
- W7223778199: SINT Protocol: Runtime Authorization and Evidence Logging for LLM-Driven Physical AI Systems
- W7218260845: A Scalable LLM-Based Framework for Intelligent and Sustainable Construction Operations
- W7160957539: Julgamento Automatizado de Agentes Conversacionais: Usando LLM-as-a-Judge para avaliar um software baseado em Inteligência Artificial para o Ensino de Computação
- W7138931634: Auditing AI in Finance: Risk, Fairness, Robustness and Compliance: A Systematic Review
- W7166517648: Auditable Private LLM Serving on Kubernetes: A Vendor-Neutral Architecture and Evidence Model for Regulated Environments
- W7171717345: AI Spaces: From Silos to Sovereign, Cross-Organizational Intelligence
- W7116636916: Reasoning Alignment For Agentic Ai:Argumentation, Belief Revision, And Dialogue
- W7153204829: Post-IAM Governance: Why Agent Registries Are Necessary but Insufficient for Agentic AI Accountability
- W4412945603: Identifying Reliable Evaluation Metrics for Scientific Text Revision
- W7225703306: Agents Propose, Code Verifies, Humans Decide: Verified Agentic Ingestion of Heterogeneous Scientific Tables into Relational Databases
- W7214778777: The five-layer trust stack for agent-to-agent coordination : a compositional architecture for verifiable, auditable, and deception-resistant autonomous agent interactions
- W7163747417: Audit trail for: A measurable trustworthiness audit for AI-driven scientific discovery
- W7154703099: Open Agent Trust Stack (OATS): A System Specification for Zero-Trust AI Agent Execution
- W7222981390: The Accountability Axis for Agent Actions: A Verifiable Form (v0.1.0)
- W7166432469: Execution Integrity Core: VAES / ARO v0.8 Belief World-Model Execution Kernel
- W7217597516: Ethics of AI-Driven Surveillance with Immutable Data Logs
- W7166084006: ECZ Sentinel OODA: A Governed Self-Improving Security Architecture (Defensive Publication & Prior-Art Declaration)
- W7163058264: MICROSERVICES ARCHITECTURE IN CREDIT AND LENDING PLATFORMS: SCALABILITY, FAULT TOLERANCE, AND REGULATORY COMPLIANCE IN DIGITAL BANKING
- W7213251902: Generative Interpretability via Scalable Neuro-Symbolic Models
- W7169478215: Understanding Digital Misinformation Across Platforms and Modalities
- W7225130083: Building Production LLM Agents: An Evaluation-Driven Playbook
- W7216931715: Embedding Legal Norms into AI Workflows: A Framework for Algorithmic Compliance in Finance
- W7223043578: TME LLM OS: A Closed-Loop Systems Engineering Architecture for Epistemic Correction in AI Agent Systems
- W7149473433: AI Tutors vs. Human Error: Who Do You Trust?

## Vocabulary

| this report | systematic review | e-discovery |
| --- | --- | --- |
| first-pass review | screening | first-pass review |
| relevant | eligible | responsive |
| flagged | included | responsive |
| discard pile | excluded | null set |
| elusion | (no common word) | elusion |
| harvest | search | collection |
