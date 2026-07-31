-- KB management: members for visibility='shared' KBs.
CREATE TABLE IF NOT EXISTS kb_members (
    kb_id     TEXT NOT NULL REFERENCES knowledge_bases(id) ON DELETE CASCADE,
    user_id   TEXT NOT NULL REFERENCES kb_users(id) ON DELETE CASCADE,
    role      TEXT NOT NULL DEFAULT 'viewer' CHECK (role IN ('viewer','editor')),
    added_at  TEXT NOT NULL,
    PRIMARY KEY (kb_id, user_id)
);
CREATE INDEX IF NOT EXISTS idx_kb_members_user ON kb_members(user_id);
