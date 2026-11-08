# Privacy implementation targets

The initial targets requested by the project owner are US/HIPAA and EU/UK GDPR. Manual privacy review is off by default at the project owner's request and can be enabled in Settings. Real names not matched by configured sensitive terms may remain in automatically sanitized model inputs when review is off. Disabling review does not mark passages as human-approved. These targets do not certify this pilot as compliant.

## Implemented controls

Local original storage and extraction; deterministic masking before segmentation and before any future embedding stage; matter-scoped pseudonyms for detected sensitive values; optional human review including retained names, enforceable through Settings; masked instruction screening; explicit provider selection; evidence manifests; audit records; loopback-only deployment instructions; no third-party browser assets or analytics; no API secrets returned to the browser.

## Required organizational decisions before client use

Determine whether the firm acts as a covered entity/business associate, controller or processor for the particular matter. Define lawful basis, any special-category condition, client authorization, vendor/processor terms, data location and transfer safeguards, retention/legal holds, access controls, incident response and subject-rights handling. A reviewer approving names does not itself establish a lawful basis or authorize a cloud vendor.

Where HIPAA applies, HHS describes cloud-service business-associate arrangements and the role of a BAA. Do not assume that a free Gemini Developer API account is approved to process PHI. Verify the exact service and contract; otherwise use an approved local workflow or genuinely de-identified permitted test data. See [HHS cloud guidance](https://www.hhs.gov/hipaa/for-professionals/special-topics/health-information-technology/cloud-computing/index.html).

The current regex patterns do not implement HIPAA Safe Harbor. HHS identifies Expert Determination and Safe Harbor as distinct de-identification methods; removing a handful of patterns is not equivalent. See [HHS de-identification guidance](https://www.hhs.gov/hipaa/for-professionals/special-topics/de-identification/index.html).

Pseudonymised data can remain personal data under EU/UK GDPR; it is not automatically anonymous. Keep the additional identifying information separately protected. See [ICO pseudonymisation guidance](https://ico.org.uk/for-organisations/uk-gdpr-guidance-and-resources/data-sharing/anonymisation/pseudonymisation/) and [EDPB anonymisation/pseudonymisation](https://www.edpb.europa.eu/topics/ai-and-technology/anonymisation-pseudonymisation_en).

Gemini's actual tier terms must be reviewed before each deployment; start with synthetic/public fixtures. See [Gemini pricing and data-use table](https://ai.google.dev/gemini-api/docs/pricing).

## Embeddings decision

Clean and review before embeddings. Embeddings can retain or expose information about their input, and sanitizing the final generated answer does not undo an earlier disclosure. This pilot creates no embeddings. A future implementation must index only privacy-approved text, retain matter/policy/corpus version metadata, and delete/rebuild vectors when a privacy decision changes. Local vectors also remain subject to access and retention controls.

## Remaining implementation gaps

Local NLP recognizers and contextual PHI classification; confidence/false-negative evaluation by language and jurisdiction; patient-record-specific handling; permanent file redaction and visual verification; configurable retention and legal holds; deletion of all derivatives/backups; authenticated actors and roles; immutable audit storage; contractual and regional provider controls; privacy impact assessment documentation. These need validation before handling sensitive client matters.
