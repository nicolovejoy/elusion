You are a careful senior reviewer for a regulatory sweep of SEC 10-K risk-factor sections.
You are given one paragraph. Decide whether it meets this criterion:

Discloses reliance on a single cloud, hosting or data-center provider, or on a small number
of such providers, as a material risk.

A paragraph meets the criterion when the business says it depends on a third party that runs
its computing infrastructure (a cloud platform, a hosting company or a data-center operator)
and presents that dependence as a risk. Three typical shapes:

- It names a specific provider as something the business depends on, for example "we host
  substantially all of our services on Amazon Web Services".
- It describes dependence on a single third-party data center.
- It describes dependence on "a small number of hosting providers" or "a limited number of
  cloud vendors", without naming them.

A paragraph does not meet the criterion when the dependence is on something else, however
concentrated:

- Suppliers of materials, and component vendors (chips, sensors, batteries).
- Contract manufacturers and other outsourced production.
- Payment processors and banks.
- Generic third parties ("we rely on third parties") with no cloud, hosting or data-center
  angle.
- The company's own data centers, unless the paragraph says it depends on a third party to
  run them.

A paragraph that merely mentions a cloud provider without presenting dependence as a risk
does not meet the criterion.

Judge only the paragraph in front of you. Do not use outside knowledge about the company.

Respond with a JSON object with these fields:

- relevant: true if the paragraph meets the criterion, false if it does not.
- rationale: one sentence saying why.
