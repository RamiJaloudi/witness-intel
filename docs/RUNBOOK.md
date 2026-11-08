# Local Windows runbook

## Prerequisites

Use Python 3.12 with the pinned dependency lock, PostgreSQL with the matching pgvector extension, and Tesseract if you need image OCR. Install Ollama for the default local generation and embeddings. Run the app on 127.0.0.1.

## Setup

From this repository in PowerShell:

```powershell
py -3 -m venv .venv
.venv/Scripts/python.exe -m pip install -r requirements.lock.txt
Copy-Item .env.example .env
New-Item -ItemType Directory -Force C:/WitnessAI/input
```

Create the database using psql as a local PostgreSQL administrator. Choose your own password; do not reuse the example literally:

```sql
CREATE ROLE witness LOGIN PASSWORD 'CHOOSE_A_LOCAL_PASSWORD';
CREATE DATABASE witness_ai OWNER witness;
```

Connect to the new `witness_ai` database as a PostgreSQL administrator and run `CREATE EXTENSION IF NOT EXISTS vector;` before initializing the app schema.

Edit `.env` locally: set DATABASE_URL with the chosen password (URL-encode special characters) and optionally GEMINI_API_KEY from Google AI Studio. You can also save the Gemini key through Settings after starting the application. Folder-path imports accept any existing folder accessible to the local backend; no IMPORT_ROOT configuration is required. Generate PRIVACY_KEY with:

```powershell
.venv/Scripts/python.exe -c "import secrets; print(secrets.token_hex(32))"
```

Copy the result into `.env`; do not share it. Set TESSERACT_CMD to the full tesseract.exe path if it is not on PATH. Keep DATA_ROOT outside shared/cloud-synced locations where possible. Set restrictive NTFS permissions for originals, `.env` and database backups.

The lock file captures the validated Python 3.12/Windows dependency set. `requirements.txt` expresses supported dependency ranges for deliberate upgrades; rerun checks after changing versions. The UI uses port 8765 to avoid a preexisting service observed on port 8080 during validation. Host/origin checks currently allow localhost/127.0.0.1 on 8765; update both the guard and launcher together if changing ports.

```powershell
.venv/Scripts/python.exe -m app.main --init-db
./scripts/start.ps1
```

Open http://127.0.0.1:8765. `/api/health` checks database connectivity. Run only one server worker for this pilot.

## Ollama fallback

With Ollama running locally, use `ollama pull qwen3.5:9b` for the example configuration and `ollama pull nomic-embed-text` for semantic search. You can choose a different installed generation model in Settings. The app uses the Ollama service on 127.0.0.1:11434 by default.

## Validation before use

Use a synthetic corpus with known witnesses, dates, contradictory accounts, sensitive identifiers and unsupported files. Confirm no chunk can reach AI before approval, masks persist, failed files block analysis, unknown citations are rejected, approved claims alone reach question generation, and provider failures do not trigger automatic fallback. Check OCR against source images manually. Unit tests are necessary but do not replace real PostgreSQL/API/OCR integration tests.

## Troubleshooting

- Database unavailable: start PostgreSQL service; check DATABASE_URL and local authentication. Initialize schema before opening the UI.
- Gemini failure: use Settings to save an available model and Test Gemini connection using synthetic input. `gemini-2.5-flash` was rejected for this account; the default is now `gemini-3.8-flash`. A catalog entry alone does not guarantee generation availability. Provider authentication/model/quota/outage errors now have application-authored messages; raw response bodies are not exposed. HTTP 429 and transient 5xx errors get bounded retries. Select Ollama explicitly to use the local fallback. This machine's `qwen2.5:latest` was verified with an isolated synthetic end-to-end check; an unavailable `gemma3:12b` tag must not be assumed installed.
- OCR failure: verify Tesseract executable/language packs; preprocess poor-quality scans.
- Unsupported file/attachments: export supported preservation copies and create a fresh matter. Analysis refuses corpus completion while failed files remain.
- Missing witness: check extraction and OCR, segmentation, aliases and actual source coverage. This pilot cannot guarantee exhaustive discovery.
- Interrupted run: completed verified passages remain checkpointed. A failed/partial batch can resume using the same provider/model and unchanged source revisions. A process crash may leave `running` rows; inspect and reconcile their status before retrying rather than claiming they completed. The application blocks concurrent runs for one workspace. Do not treat a completed batch or an interrupted corpus as full coverage. Reconciliation tooling is future work.
- Privacy correction after analysis: preserve audit/history; create a new matter version and reimport rather than mutating cited evidence.

## Backup and restoration

Stop ingestion/analysis. Back up PostgreSQL with `pg_dump -Fc -f <protected-path>/witness_ai.dump witness_ai`, using local PostgreSQL authentication without putting passwords on the command line. Back up DATA_ROOT and `.env` separately into encrypted storage. Originals and SQL must be restored together; preserve PRIVACY_KEY. Restore to a clean database with `pg_restore`, verify `/api/health`, document counts, source hashes and sample citations before resuming. Test restores periodically; this initial build has no automated restore validation.

## Retention and deletion

The frontend supports recoverable deletion and restoration through Trash, plus separately confirmed permanent purge of one, selected, or all trashed workspaces. Purge removes the selected managed copies and database records, including workspace audit history. It does not erase original input folders, separate exports, backups, database WAL or disk remnants. No automated retention/legal-hold enforcement or forensic secure-erasure workflow is implemented. Establish matter-specific retention and hold rules before client data. Do not use ad hoc row deletion that leaves orphan originals.

Purge operates only on canonical UUID folders directly under DATA_ROOT. Reparse points/symlinks are rejected, and every selected workspace must still be in Trash with no running analysis. SQL deletes are scoped to selected workspace IDs and ordered by foreign-key dependencies. Managed folders are renamed into `.purge-pending/<operation UUID>/` before SQL commit. Database failure rolls back SQL and restores staged folders. After commit, staged copies are removed; failures remain visible as pending cleanup. `.purge-receipts/` retains minimal UUID/count/time receipts, not source content or names.

Use Resolve pending operation in Trash with its typed confirmation. If all workspace records remain, staged folders are restored. If all records were purged, remaining staged copies are deleted. Mixed/inconsistent records or unsafe paths require operator review and are not automatically deleted. Do not manually move/delete these folders while an operation is active. Restoration is blocked until pending operations are resolved. Back up database/storage consistently, and include exports and backups in any separate retention/deletion plan.

PostgreSQL workflow tests now use a uniquely named schema per test and drop only their own tables/schema during cleanup. They do not create workspaces in the app's active schema. Synthetic workspaces from older test runs can be selected in Manage workspaces and moved to Trash after confirmation.

## GitHub preparation and secrets

The repository intentionally does not contain a populated `.env`. `.env.example` is safe configuration documentation. `.gitignore` excludes data, uploads, env files, private keys, dumps and archives, including the reference ZIP. No reference `.env` was read or copied.

Before the first publication, initialize Git locally, stage only reviewed source/documentation, and inspect `git status` plus `git diff --cached`. Run:

```powershell
.venv/Scripts/python.exe scripts/check_secrets.py
```

This scans the staged index without printing matched secrets. Also use an established secret scanner such as Gitleaks before publication and enable GitHub secret scanning/push protection where available. The bundled scanner is deliberately limited; private repositories do not make committed secrets safe. Never publish original documents, SQL dumps, exports, `.env`, cloud service credentials or the reference ZIP. If a secret was ever committed, revoke/rotate it and clean history before pushing; `.gitignore` cannot erase history. A GitHub remote has not been created or pushed by this build.

## Known production prerequisites

Authentication/SSO, role and matter authorization, immutable external audit storage, database migrations, durable background jobs, upload/file-size limits, parser isolation and malware checks, stronger local PII/PHI detection, reviewed compliance policies, semantic claim verification and a full native-format test suite remain required for firm deployment.

For local model troubleshooting, check `ollama list` and `ollama ps`. The requested Qwen tag is `qwen3.5:9b`; install with `ollama pull qwen3.5:9b`, then select it in Settings. A slow first call may include model loading. Ollama remains the default, and failures never silently send a run to another provider. Model changes apply to new runs; an in-flight run retains its recorded model.

## Upgrade: local vectors and specialist drafts
Install updated `requirements.txt` (or the refreshed lock file). Install pgvector for the PostgreSQL server version, enable `CREATE EXTENSION vector` using a database administrator, then apply `migrations/002_local_embeddings.sql` before starting the upgraded app. Alternatively, after enabling the extension, `python -m app.main --init-db` creates only missing tables. Back up the database first. Vector dimensions are fixed at 768 for the selected Nomic model; changing embedding models requires a planned reindex/schema change, not mixing models.

Install the pgvector build matching your PostgreSQL server version. See the [pgvector project](https://github.com/pgvector/pgvector) for installation instructions.

Ensure `ollama list` includes `nomic-embed-text:latest`; if missing, run `ollama pull nomic-embed-text`. Indexing defaults to automatic server-side maintenance; Settings offers Manual mode for explicit batches. The index reports pending counts and resumes incrementally. Newly discovered identities can invalidate earlier masked vectors, requiring another indexing pass. Hybrid search falls back visibly to keyword results when embeddings are absent, stale or unavailable. Masking is not a guarantee of legal de-identification.

Full work-product generation can take multiple model calls per witness and can return fewer than the requested questions. Local generation uses a bounded 16,384-token context and 8,192-token output cap; Qwen3.5 thinking is disabled for these structured extraction/drafting requests. Ollama is the default; choose a cloud provider explicitly. No automatic provider failover occurs. Final legal use requires attorney review of the saved drafts and underlying sources.

On an authorized restart, reconcile only confirmed interrupted run IDs, preserving their outputs and writing a `run_interrupted` audit event. Do not indiscriminately mark all running jobs failed while another process may still be working. Stage checkpoints survive failures, but reruns currently start a new graph; they do not resume an old stage automatically.


## Automatic index maintenance
`INDEXING_MODE=automatic` is the default when unset; `manual` disables automatic work. Settings saves this value in the ignored `.env` file and signals the worker immediately, with no restart required. Run a single application server process for this local pilot. A lifespan-owned worker scans active workspaces in bounded 50-passage batches; completed work is checkpointed in vector rows, not an in-memory queue. Closing the browser does not stop it. Restarting the server restarts scanning from current SQL fingerprints.

The worker pauses for analysis in the same workspace or any local Ollama analysis, skips Trash, and uses a 60-second retry delay for an embedding failure. Completed workspaces are rescanned periodically (roughly 30–45 seconds when idle); progress may queue behind other workspaces. Manual batches and automatic work share an in-process lock. PostgreSQL advisory locks prevent simultaneous writers for the same workspace across processes. Post-HTTP writes recheck source scope, Trash/deletion, exclusion and masking fingerprints under the matter row lock, so in-flight embeddings do not resurrect purged workspaces or save obsolete source vectors. Automatic vector creations identify `local-indexer` as creator; batch audit events record `automatic=true`.

Shutdown cancels the worker; saved vectors remain. This is resumable local maintenance, not an external durable scheduler: leave the server and Ollama running. Inspect Settings progress for errors; check the local embedding model/service and database rather than changing the generation provider. The progress endpoint is `GET /api/matters/{id}/search/index/status`; mode is `POST /api/settings/indexing` with mode `automatic` or `manual`. These endpoints return counts/state, not source text or credentials.


## Contextual question editing
New outlines run the context-research/editor stage automatically. Existing pending drafts can use `POST /api/matters/{id}/outlines/{outline_id}/contextualize` with the selected provider. The UI action is **Improve document context**. The revision creates a separate `question_context_revision` run, `document_context` citation outputs and a `question_context_revision` audit event with the before-wording snapshot. Original outline ownership/run identity is retained; the payload records the revision run ID. No additional migration is required.

A source reference may be missing, ambiguous or in a different workspace; the editor must not substitute another message's headers. Missing metadata is left unknown. Context windows use capped excerpts; investigators may need to review the full cited passage or source manually. Metadata is source content, not proof of authenticity, delivery or receipt. Approved/rejected questions are protected. Concurrent outline revision changes abort the operation. Failures retain the original outline, although gathered context citation outputs may remain linked to the failed run for audit. Drafts that cannot be safely reworded remain marked for review, and the result reports retained counts.


## Appearance settings
`UI_PALETTE=classic` is the default. Settings saves catalog IDs to the ignored `.env`, applies them immediately and restores them on reload. No migration or model restart is needed for palette changes. Unknown/removed IDs fall back to Classic; arbitrary CSS or color input is rejected. The curated catalog and source attribution are in `app/palettes.py`, presentation in `app/static/palettes.css` and `palettes.js`. No third-party requests occur during runtime theme selection. Source swatches remain exact; interface roles may be adjusted to keep white button/header text readable. Review/danger colors are intentionally independent of the palette.


## Bulk evidence and question review
The bulk preview/apply endpoints are `/api/matters/{id}/review/bulk-preview` and `/review/bulk`. A preview snapshot hashes selected output revisions, statuses and content/question hashes. Apply requires acknowledgement and the matching snapshot, then validates source scope/support and writes all changes in one SQL transaction. Targets are locked before applying. Stale selections and foreign-matter IDs are rejected; approved/rejected questions are protected by pending-only scopes. Each `bulk_work_product_review` audit event records before statuses/revisions, item identifiers/hashes, counts and the requested decision. Bulk review does not edit text or privacy flags and does not process narrative profile approval.

The open review panel and selections are held in per-workspace/per-witness browser-memory state; they survive application refresh/re-render after decisions, not a full browser reload. This is presentation state, not a database review decision. A full reload starts with a closed panel. No migration is required.


## Witness overview and sparse-evidence presentation
`GET /api/matters/{id}/witnesses/overview` builds matter-scoped, exact-source-checked summaries and reports current extraction checkpoint coverage. Explicit named-role statements can form a cited digest; other first-person excerpts stay in the supporting-findings inspector. Repeated statements are deduplicated for evidence-depth assessment, and the limited flag is a conservative heuristic, not a completeness/confidence guarantee. Dated chronology uses explicit event date fields; undated findings are not assigned email dates. Excluded/invalid source quotes do not enter the overview. No migration or AI request is required for this display.

Existing narrative text is not overwritten by the overview. The UI suppresses stale, misattributed, raw-first-person or overlong sparse narratives and shows the current short digest. Regeneration uses the stricter narrative instructions and source checks. Similar names are never merged by these presentation changes. Extraction coverage counts distinct current chunk revisions with saved processed extraction checkpoints; embedding coverage is a separate measure.


## Consolidated witness UI
Navigation now contains Import, Privacy, Witnesses, Audit and Settings. Chronology, relationship findings and cited question outlines are rendered under each witness. One persistent question editor is mounted into the selected witness card; moving it preserves handlers and field state. Unsaved form drafts are isolated by workspace/witness in browser memory. Existing output records, API routes, citations, reviews and exports are unchanged; no migration or server restart is required for this frontend change. Existing legacy internal page requests for Questions/Chronology/Relationships resolve to Witnesses.


## Gemini processing policy
The operator requested Gemini processing without privacy-approval requirements. Keep `PRIVACY_REVIEW_REQUIRED=false` (the default); this now controls extraction and all Gemini synthesis/question/context approval gates consistently. There is no separate mandatory name/context approval or workspace acknowledgement when off. No schema migration is needed. Automatic regex/custom-term masking remains enabled, and source flags continue to distinguish reviewed from unreviewed passages. Instruction detections are sanitized rather than blocking generation while the gate is off. If an operator explicitly re-enables manual review, approval requirements apply again. Generation-start audits record the effective policy. Ollama remains the default; no automatic Ollama failover is introduced.

## Persistent full corpus analysis

`ANALYSIS_MODE=full` is the default; Settings can persist `manual` without restarting. Full jobs use existing Run and Audit records and need no schema migration. Run a single local application server. Running full jobs recover after restart, retaining verified passage checkpoints and recovering their interrupted child workflows under a PostgreSQL advisory lock. Paused jobs remain paused. Provider errors retain progress; transient failures retry up to three times with 60/120/240-second delays, then require Resume. Standalone interrupted manual jobs still require inspection. Full extraction precedes per-witness synthesis when automatic drafting is selected. Settings scope changes apply to future jobs and do not cancel existing ones.

## Double-click Windows startup

Double-click `Start-WitnessAI.bat` at the repository root. The launcher opens visible Python consoles for PostgreSQL logs and the application, starts Ollama in a visible console if needed, opens pgAdmin with its existing profile, and opens the application in your default browser. Already running services are reused; active analysis is not interrupted. If the existing app was launched by the preview, its available preview logs are displayed. A server started by this launcher prints output directly in its application console. Keep that server console open while working; closing it can interrupt requests. PostgreSQL is not automatically stopped on exit.

Copy `scripts/launcher.example.json` to the ignored `data/launcher.json` and configure the existing PostgreSQL and pgAdmin paths. A nonempty `database_url` overrides the `.env` value for launcher processes. `postgres_data` must point to an existing cluster. Run `Start-WitnessAI.bat --check` for a read-only readiness check.

pgAdmin opens with its existing profile. Register the host, port, maintenance database and username from your local PostgreSQL setup.

## Stop Windows services

Double-click `Stop-WitnessAI.bat` to stop the Workbench application, close the configured pgAdmin instance, cleanly stop the configured PostgreSQL cluster, stop local Ollama (including a reused Ollama instance), and close Workbench launcher consoles. Browser windows are left open. Unrelated Python programs and PostgreSQL clusters are not selected. pgAdmin editor windows close too; save SQL edits before stopping.

The script verifies executable paths/process ancestry and creation times, sends Ctrl+Break to the verified application's console, and waits up to 60 seconds. If the application is still active, shutdown stops with PostgreSQL left running. `Stop-WitnessAI.bat -Force` permits forced application termination after that wait; it never forcibly kills PostgreSQL. PostgreSQL uses `pg_ctl stop -m fast -w`, which rolls back active database transactions and performs a clean shutdown. Saved documents and outputs are retained. Running non-full workflows are audited as interrupted after the server exits, releasing their UI locks; full-corpus jobs retain their restart recovery state. In-flight generation can be interrupted, so finish important work before stopping.

`Stop-WitnessAI.bat -Check` lists the verified targets and exits before any signal, database update, GUI close or service stop. Successful shutdown closes its own batch window; failures keep the message visible. The batch uses normal PowerShell execution policy and does not bypass endpoint or authentication protections.


Local Ollama structured-output recovery allows up to three generation attempts on malformed, empty or output-limited responses. Cut-off responses are rejected even when their JSON parses. Retries keep the same provider/model and request a concise complete response; failed responses are never coerced into evidence or marked processed. If all attempts fail, the run retains its checkpoints for Resume.
