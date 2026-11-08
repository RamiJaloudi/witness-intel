-- Enable pgvector with an administrative connection before this additive migration.
CREATE EXTENSION IF NOT EXISTS vector;
CREATE TABLE IF NOT EXISTS corpus_embeddings (
  matter_id varchar(36) NOT NULL REFERENCES matters(id),
  chunk_id varchar(36) NOT NULL UNIQUE REFERENCES chunks(id),
  fingerprint varchar NOT NULL,
  model varchar NOT NULL,
  vector vector(768) NOT NULL,
  id varchar(36) PRIMARY KEY,
  created_at timestamptz NOT NULL,
  updated_at timestamptz NOT NULL,
  created_by varchar NOT NULL,
  revision integer NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_corpus_embeddings_matter_id ON corpus_embeddings(matter_id);
-- Exact cosine search is used for the local pilot. Add measured HNSW indexing for larger deployments.
