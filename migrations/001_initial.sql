-- Initial Witness AI Workbench schema; alternative to --init-db, not an upgrade migration.


CREATE TABLE matters (
	name VARCHAR NOT NULL,
	privacy_mode VARCHAR NOT NULL,
	sensitive_terms JSON NOT NULL,
	instructions TEXT NOT NULL,
	id VARCHAR(36) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
	created_by VARCHAR NOT NULL,
	revision INTEGER NOT NULL,
	PRIMARY KEY (id)
)

;


CREATE TABLE audit_events (
	matter_id VARCHAR(36) NOT NULL,
	action VARCHAR NOT NULL,
	details JSON NOT NULL,
	id VARCHAR(36) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
	created_by VARCHAR NOT NULL,
	revision INTEGER NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(matter_id) REFERENCES matters (id)
)

;

CREATE INDEX ix_audit_events_matter_id ON audit_events (matter_id);


CREATE TABLE documents (
	matter_id VARCHAR(36) NOT NULL,
	name VARCHAR NOT NULL,
	sha256 VARCHAR NOT NULL,
	size INTEGER NOT NULL,
	status VARCHAR NOT NULL,
	error VARCHAR NOT NULL,
	id VARCHAR(36) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
	created_by VARCHAR NOT NULL,
	revision INTEGER NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(matter_id) REFERENCES matters (id)
)

;

CREATE INDEX ix_documents_matter_id ON documents (matter_id);


CREATE TABLE runs (
	matter_id VARCHAR(36) NOT NULL,
	provider VARCHAR NOT NULL,
	model VARCHAR NOT NULL,
	purpose VARCHAR NOT NULL,
	status VARCHAR NOT NULL,
	manifest JSON NOT NULL,
	prompt_version VARCHAR NOT NULL,
	error VARCHAR NOT NULL,
	id VARCHAR(36) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
	created_by VARCHAR NOT NULL,
	revision INTEGER NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(matter_id) REFERENCES matters (id)
)

;

CREATE INDEX ix_runs_matter_id ON runs (matter_id);


CREATE TABLE chunks (
	document_id VARCHAR(36) NOT NULL,
	locator VARCHAR NOT NULL,
	text TEXT NOT NULL,
	detections JSON NOT NULL,
	approved BOOLEAN NOT NULL,
	id VARCHAR(36) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
	created_by VARCHAR NOT NULL,
	revision INTEGER NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(document_id) REFERENCES documents (id)
)

;

CREATE INDEX ix_chunks_document_id ON chunks (document_id);


CREATE TABLE outputs (
	matter_id VARCHAR(36) NOT NULL,
	run_id VARCHAR(36) NOT NULL,
	kind VARCHAR NOT NULL,
	payload JSON NOT NULL,
	review_status VARCHAR NOT NULL,
	id VARCHAR(36) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
	created_by VARCHAR NOT NULL,
	revision INTEGER NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(matter_id) REFERENCES matters (id),
	FOREIGN KEY(run_id) REFERENCES runs (id)
)

;

CREATE INDEX ix_outputs_matter_id ON outputs (matter_id);
