# AmtHero24 Legal Operator Completion Sheet

Purpose: define the factual evidence required to replace fail-closed placeholders before publication. This public-repository template must never contain the owner's private identity documents, home address, telephone number, residence status, tax identifiers, contracts, or unapproved legal/operator facts.

## Public-repository privacy boundary

- Full legal/operator name: `[REQUIRED — keep factual value outside the public repository until exact publication approval]`
- Trading/project name: `AmtHero24`
- Legal form/status: `[REQUIRED]`
- Serviceable postal address / ladungsfähige Anschrift: `[REQUIRED — private until exact publication approval]`
- Country of the serviceable address: `[REQUIRED]`
- Email: `info@amthero24.de` (must be operational before publication)
- Support email: `support@amthero24.de` (must be operational before publication)
- Telephone number: `[REVIEW WHETHER LEGALLY REQUIRED AND INTENTIONALLY APPROVED]`
- Commercial register / register court / register number, only if applicable: `[APPLICABILITY REVIEW]`
- VAT identification number or Wirtschafts-Identifikationsnummer, only if issued/applicable: `[APPLICABILITY REVIEW]`
- Supervisory/licensing authority, only if the actual activity requires one: `[APPLICABILITY REVIEW]`
- Consumer dispute-resolution statement under VSBG: `[REQUIRED FACTUAL DECISION]`

Collect the actual values in a private owner-controlled record. Copy only the legally required, reviewed public subset into deployment variables at the controlled publication step. Never store identity documents, residence permits, lease agreements, tax letters, Gewerbeanmeldung certificates, Meta verification documents, or unredacted legal correspondence in GitHub.

## DDG / provider-information review

Before `LEGAL_PRODUCTION_READY=true`, verify against the actual operator status:

- provider name and serviceable address;
- rapid electronic contact and direct communication method;
- legal form and authorized representative, if a juristic person exists;
- register and number, if registered;
- VAT/Wirtschafts-ID only if actually issued;
- competent authority only if the activity is subject to authorization;
- no obsolete TMG or EU ODR-platform wording;
- every required item is easily recognizable, directly reachable, and permanently available.

## VSBG review

Do not invent willingness, obligation, employee count, or a dispute-resolution body.

- determine whether the general information duty applies to the actual business situation;
- record whether the operator is willing or legally obliged to participate;
- name a competent consumer arbitration body only when a factual participation obligation or commitment exists;
- keep the website and AGB wording consistent;
- obtain a fresh review if staffing or the commercial model changes.

## Website / TDDDG technology review

- confirm the deployed website still uses no analytics, marketing, social-media trackers, remote fonts, or third-party video embeds;
- inventory any storage/access on user devices before publication;
- if a non-essential technology is added later, implement valid prior consent before activation;
- do not display a consent banner merely as decoration when no non-essential technology exists;
- ensure cookie/privacy wording matches the exact deployed technology inventory.

## GDPR Article 13/14 completion facts

The final privacy notice must state, for each real processing purpose:

- controller identity/contact details and data-protection-officer contact only if applicable;
- categories of personal data and source where data are not obtained directly;
- purposes and exact legal bases;
- legitimate interests where Article 6(1)(f) is used;
- recipients/categories of recipients;
- third-country transfers, adequacy decision or safeguards and how to obtain them;
- retention periods or objective criteria;
- whether providing data is required and consequences of not providing it;
- rights of access, rectification, erasure, restriction, portability, objection and consent withdrawal as applicable;
- right to complain to the competent supervisory authority;
- meaningful information about relevant automated decision-making/profiling, or a truthful statement that no such legally relevant decision is made;
- separate, clear explanations for Website, WhatsApp/Meta, model provider, documents/audio, Hero Memory, Beta admission, reminders, security logs, support email, backups and retention.

## Processing/data-flow facts that must be verified

Do not set any corresponding production-ready flag until documentary evidence exists.

- Meta / WhatsApp Cloud API contractual roles, terms and transfer basis confirmed
- Railway processing terms, subprocessor information and actual deployment region confirmed
- Groq processing terms and actual retention/ZDR configuration confirmed
- Cloudflare processing role for DNS/proxy/security confirmed
- email provider, mailbox locations, processing terms and retention confirmed
- complete subprocessor list matches the actual production architecture
- Article 28 processor contracts/AVVs and subprocessor authorizations confirmed where applicable
- international-transfer safeguards documented where applicable
- retention descriptions match runtime and backup reality
- raw document/audio handling statement matches production implementation
- Hero Memory consent, export and deletion descriptions match production implementation
- Beta participation consent remains separate from Hero Memory consent
- incident/support procedures do not move real-user content into public GitHub issues or CI

## AI transparency and service-boundary review

- users are informed clearly that Sam is an AI assistant when they interact with it;
- Sam does not claim to be human, a public authority, lawyer, tax adviser, doctor, or guaranteed decision-maker;
- important deadlines, amounts and generated text must be checked against originals;
- any synthetic public media/content receives the legally required disclosure/marking where applicable;
- no high-risk or regulated-service claim is introduced without a separate factual/legal assessment;
- maintain an internal AI-literacy and operator-training record appropriate to the actual use.

## Accessibility / BFSG applicability review

Do not claim statutory BFSG compliance or exemption before the actual enterprise size, service model, consumer-contract path and publication date are verified.

- retain the tested keyboard, focus, semantic, contrast, reduced-motion and local-media controls regardless of legal minimum;
- determine whether the service falls within the relevant electronic-commerce/service scope;
- determine whether the operator factually qualifies as a microenterprise providing services;
- if BFSG duties apply, complete the required accessible service information and authority details;
- record qualified review rather than inferring exemption from the current project size alone.

## Publication gates

`LEGAL_PRODUCTION_READY=true` is allowed only when:

1. Gewerbeanmeldung/operator status is complete and factually evidenced;
2. every mandatory public operator field is complete and intentionally approved;
3. final German legal texts have been reviewed against the actual services, commercial model and data flows;
4. no placeholder or unverified legal claim remains in public legal routes;
5. all processor/subprocessor/transfer facts are verified;
6. `info@` and `support@` receive and send authenticated mail successfully;
7. canonical domain, TLS, redirects and website-service ownership are verified;
8. VSBG, TDDDG, GDPR, AI-transparency and BFSG applicability wording has been reviewed against the actual facts;
9. the current production Smoke and Deployment Certification are healthy on the exact backend SHA;
10. the owner explicitly approves the exact final public legal text/version.

## Legal drafting note

The website drafts are operational templates, not a substitute for qualified German legal and tax review. Final wording must reflect the actual operator status, commercial model, processors, transfers, technologies and service scope at the moment of publication. Historical CI, old legal assumptions or values from a private handoff are never publication evidence.
