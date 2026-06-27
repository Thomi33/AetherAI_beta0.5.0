-- 0001_init.sql — Esquema inicial del subsistema de memoria de Aether.
-- Versión: 1
--
-- Migración VERSIONADA. La aplica EXCLUSIVAMENTE el runner (migrations.py),
-- nunca el agente de forma automática. No editar esta migración una vez
-- aplicada en producción: para cambios, crear 0002_*.sql.

CREATE TABLE IF NOT EXISTS conversations (
    id        TEXT PRIMARY KEY,
    timestamp INTEGER NOT NULL,
    content   TEXT    NOT NULL,
    source    TEXT
);

CREATE TABLE IF NOT EXISTS memories (
    id         TEXT    PRIMARY KEY,
    type       TEXT    NOT NULL CHECK (type IN ('fact', 'preference', 'event')),
    content    TEXT    NOT NULL,
    importance INTEGER NOT NULL DEFAULT 0 CHECK (importance BETWEEN 0 AND 10),
    created_at INTEGER NOT NULL,
    tags       TEXT
);

CREATE TABLE IF NOT EXISTS commands (
    id        TEXT PRIMARY KEY,
    command   TEXT NOT NULL,
    result    TEXT,
    timestamp INTEGER NOT NULL
);

-- Índices de lectura frecuentes (no alteran el esquema lógico de columnas).
CREATE INDEX IF NOT EXISTS idx_conversations_ts ON conversations (timestamp);
CREATE INDEX IF NOT EXISTS idx_memories_type     ON memories (type);
CREATE INDEX IF NOT EXISTS idx_memories_importance ON memories (importance);
CREATE INDEX IF NOT EXISTS idx_commands_ts       ON commands (timestamp);
