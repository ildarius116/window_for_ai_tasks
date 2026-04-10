-- Additional databases for MWS GPT stack
-- Main DB (openwebui) is created by POSTGRES_DB env var

CREATE DATABASE litellm;
CREATE DATABASE langfuse;
CREATE DATABASE memory;

\c memory;
CREATE EXTENSION IF NOT EXISTS vector;
