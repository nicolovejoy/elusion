# Auditable decisions

> Studies how the decisions of an automated system (a classifier, a language model or an
> agent) can be checked or defended afterwards: measuring what it got right and what it
> missed, recording what it did and why, or having a person review or override it.

The documents relating to our auditable decisions are essentially paper abstracts. An
abstract counts when the paper presents or evaluates a way of checking, recording or
overseeing what an automated system decided. Four typical shapes:

- It validates automated document review or citation screening: recall, elusion, sampling
  the discarded documents, or a rule for when to stop reviewing.
- It gives a model's or an agent's decisions an audit trail: logging, provenance, lineage or
  traces of what the system did and why, kept so the decisions can be examined later. This
  includes identifying, monitoring and logging the actions of AI agents.
- It compares an LLM used as a judge or grader with human judges, or measures how far such a
  judge can be trusted.
- It sets out or tests a procedure for human oversight of automated decisions: a person
  reviewing, approving or overriding what the system decided.

An abstract does not count when checking the system's decisions is not what the paper
studies:

- It applies a model, or builds an agent, and reports only how accurate it is.
- Its audit trail or audit log belongs to a system that makes no automated decision: a
  financial ledger, a clinical audit, a blockchain record of goods or payments.
- It presents a method for explaining a model, with no audit or accountability use.
- It is legal, ethical or policy commentary with no method and no measurement: it argues
  that automated decisions should be accountable without studying how to make them so.
