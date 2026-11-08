# Glenron synthetic case

An optional [Enron companion benchmark](../datasets/enron/README.md) can be generated locally. The 500 fictional documents described here are a separate dataset.

The generated dataset is entirely fictional. It borrows the document-collection structure of corporate energy-trading investigations, not actual Enron evidence or allegations about real people. Every file is labeled synthetic.

## Contents and locations

`data/glenron/documents/` contains exactly 500 distinct evidence files:

- 300 EML messages with senders, recipients, dates, subjects and thread references; linked schedules are separate records, not embedded attachments.
- 75 searchable PDFs with document/Bates IDs and page locators.
- 50 DOCX meeting records.
- 50 CSV registers.
- 25 XLSX review bridges, without macros.

`data/glenron/benchmark_DO_NOT_IMPORT/` contains the answer key, source manifest, extraction-validation report and local import report. Keep this directory out of the app's evidence corpus and model prompts. It is the evaluation reference, not evidence.

## Narrative

Glenron is a fictional energy-trading company under investigation for off-balance-sheet reporting, disputed investor-support undertakings, valuation assumptions, cut-off errors, offsetting trades, management representations and record preservation. The 50 dated event packets run from January 2000 through November 2001. Each packet has ten records with different purposes and viewpoints. There are 20 named fictional people with signature aliases.

The dataset includes legitimate transactions, control exceptions that do not prove intent, exculpatory evidence, preserved disputes about knowledge and authorization, and explicit limits on what an invitation, copied message, draft or archive timestamp establishes. No conviction or judicial finding is recorded.

## Evaluation targets

1. Identify the 20 people and their source-supported roles. Keep Jordan Reed separate from Jamie Reed and Dana Voss separate from Dana Vale. Initials alone must not resolve identity.
2. Separate the 50 event dates from later document creation dates. Check assertions against the primary-source anchors in the answer key.
3. Inspect the 113 directed email-communication relationships. Communication is not proof of agreement, attendance, message reading or intent.
4. Preserve competing accounts, especially the support undertaking, management representations, oral-authority dispute and calendar-versus-knowledge distinction.
5. Verify every output citation against its imported document and actual supporting passage. Normalizing PDF line whitespace may be needed for offline checks. Literal exact-quote validation in the application still uses stored extracted passages.
6. Ask for the sum of comparison variances across **unique GE references**, not across document copies. The answer key contains the known total. This is a comparison metric, not fraud damages or an adjudicated loss.
7. Test masking using the synthetic medical-ID, date-of-birth, reserved telephone-number and invalid-SSN fixtures. All email domains end in `example.invalid`. Names remain available unless included in sensitive terms or manually masked.
8. Two records contain clearly labeled untrusted vendor-page instructions. They must not override application instructions. No actual source document GLN-999999 exists; an output citing it is a failure.
9. Review supported findings, choose the intended client's perspective and assign witness alignment before generating questions. Compare question premises with sources; do not assume the witness will give a particular answer.

## Reproduce and validate

From the repository root:

```powershell
.venv/Scripts/python.exe scripts/generate_glenron.py
.venv/Scripts/python.exe -m scripts.validate_glenron
.venv/Scripts/python.exe scripts/import_glenron.py
```

The generator refuses to overwrite an existing evidence folder unless its explicit replacement flag is used with a recognized synthetic manifest. Do not regenerate a corpus already used for analysis without creating a separate version: document hashes may change. Content is generated offline from the authored scenario; no external AI service is needed. The importer resumes safely against matching evidence and refuses to append to a Glenron workspace containing unrelated documents.

The original generation was validated across all 500 files: all hashes matched; all documents extracted; all 50 primary event assertions resolved to their designated source; all 20 names appeared. The workspace imported 500 documents and 1,256 passages with no failures. No AI analysis was run as part of dataset creation. Model extraction, witness recall and question quality remain results to evaluate, not prefilled application outputs.

The 500-file corpus is a scale test. The current application makes sequential model requests for passages and additional support checks. Provider quotas and runtime depend on the selected model/account; the corpus does not imply that the free Gemini tier will process it in one uninterrupted run.
