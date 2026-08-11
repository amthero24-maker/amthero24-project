# AmtHero24 Legal Operator Completion Sheet

Purpose: collect only factual operator/provider data needed to replace fail-closed placeholders before publication. Do not guess any value.

## Mandatory factual fields

- Full legal/operator name: `Wissam Zidan` (confirm exact legal spelling before publication)
- Trading/project name: `AmtHero24`
- Legal form/status: `[REQUIRED]`
- Serviceable postal address in Germany: `[REQUIRED]`
- Email: `info@amthero24.de` (must be operational before publication)
- Support email: `support@amthero24.de` (must be operational before publication)
- Telephone number: `[REVIEW WHETHER REQUIRED/INTENDED]`
- Commercial register / register court / register number, if applicable: `[APPLICABILITY REVIEW]`
- VAT identification number, if issued/applicable: `[APPLICABILITY REVIEW]`
- Supervisory/licensing authority, only if the activity requires one: `[APPLICABILITY REVIEW]`
- Consumer dispute resolution statement under VSBG: `[REQUIRED DECISION BASED ON ACTUAL BUSINESS STATUS]`

## Processing/data-flow facts that must be verified

Do not set any corresponding production-ready flag until documentary evidence exists.

- Meta / WhatsApp Cloud API contractual and data-transfer basis confirmed
- Railway processing terms, subprocessor information and actual deployment region confirmed
- Groq processing terms and actual retention/ZDR configuration confirmed
- Cloudflare processing role for DNS/proxy/security confirmed
- Any email provider and its processing terms confirmed
- Complete subprocessor list matches actual production architecture
- International-transfer safeguards documented where applicable
- Retention descriptions match runtime reality
- Raw document/audio handling statement matches production implementation
- Hero Memory consent, export and deletion descriptions match production implementation

## Publication gates

`LEGAL_PRODUCTION_READY=true` is allowed only when:

1. every mandatory operator field is complete;
2. final German legal texts have been reviewed against actual services and data flows;
3. no placeholder remains in public legal routes;
4. all processor/subprocessor facts are verified;
5. `info@` and `support@` are functioning;
6. the canonical domain and TLS are verified;
7. the owner explicitly approves the exact final legal text/version.

## Legal drafting note

The website drafts are operational templates, not a substitute for qualified German legal review. The final wording must reflect the actual operator status, commercial model, processors, data transfers and service scope at the moment of publication.
