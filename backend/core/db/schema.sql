
/* =========================================================
   EXTENSIONS
   ========================================================= */
CREATE EXTENSION IF NOT EXISTS "pgcrypto";
CREATE EXTENSION IF NOT EXISTS vector;



/* =========================================================
   AGENCIES
   ---------------------------------------------------------
   Stores agencies using the system.
   Minimal info to avoid duplication.
   ========================================================= */
CREATE TABLE agencies (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT NOT NULL,
    issued_email TEXT,         -- email used for identification
    created_at TIMESTAMPTZ DEFAULT now()
);



/* =========================================================
   PLANS
   ---------------------------------------------------------
   Subscription plans (logical only).
   ========================================================= */
CREATE TABLE plans (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT NOT NULL
);



/* =========================================================
   ACTIVE PACKAGES
   ---------------------------------------------------------
   Join table: which plans an agency currently has active.
   Prevents duplicating package definitions.
   ========================================================= */
CREATE TABLE active_packages (
    agency_id UUID NOT NULL REFERENCES agencies(id) ON DELETE CASCADE,
    plan_id UUID NOT NULL REFERENCES plans(id) ON DELETE CASCADE,
    PRIMARY KEY (agency_id, plan_id)
);



/* =========================================================
   AGENCY PACKAGES
   ---------------------------------------------------------
   Travel packages offered by agencies.
   ========================================================= */
CREATE TABLE agency_packages (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    agency_id UUID NOT NULL REFERENCES agencies(id),

    -- public-facing info
    name TEXT NOT NULL,               -- "Basic", "Pro", "Custom Miami Tour"
    description TEXT,

    -- pricing (soft assumptions)
    price_amount NUMERIC(10,2),        -- nullable (for "Contact us")
    price_currency CHAR(3) DEFAULT 'USD',
    pricing_model TEXT,                -- 'fixed', 'per_person', 'per_day', 'custom'

    -- scope / applicability
    category TEXT,                     -- 'travel', 'umrah', 'corporate', etc
    destination TEXT,                  -- 'Miami', 'Paris', nullable
    duration_days INT,                 -- nullable

    -- recommendation signals
    ideal_for TEXT[],                  -- ['family', 'honeymoon']
    includes TEXT[],                   -- ['hotel', 'flight', 'visa']

    -- operational
    is_active BOOLEAN DEFAULT true,
    is_custom BOOLEAN DEFAULT false,

    -- embeddings for semantic search
    embedding vector(1536),

    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now()
);



/* =========================================================
   PROSPECTS
   ---------------------------------------------------------
   Every person who has ever contacted an agency.
   Used for memory, personalization, and continuity.
   ========================================================= */
CREATE TABLE prospects (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT,
    agency_id UUID NOT NULL REFERENCES agencies(id) ON DELETE CASCADE,
    psyche_eval TEXT,                   -- qualitative assessment
    psyche_eval_confidence NUMERIC(3,2), -- confidence score (0–1)
    created_at TIMESTAMPTZ DEFAULT now()
);



/* =========================================================
   CONTACT METHODS
   ---------------------------------------------------------
   All channels/identifiers used by a prospect.
   Stores HASHED identifiers for privacy.
   ========================================================= */
CREATE TABLE contact_methods (
    prospect_id UUID NOT NULL REFERENCES prospects(id) ON DELETE CASCADE,
    channel_type TEXT NOT NULL,         -- whatsapp, email, instagram, etc
    identifier_hash TEXT NOT NULL,       -- hashed phone/email/handle
    created_at TIMESTAMPTZ DEFAULT now(),
    PRIMARY KEY (channel_type, identifier_hash)
);



/* =========================================================
   PROSPECT PRESENCE
   ---------------------------------------------------------
   Tracks activity / last contact per channel.
   Used for follow-up detection.
   ========================================================= */
CREATE TABLE prospect_presence (
    prospect_id UUID NOT NULL REFERENCES prospects(id) ON DELETE CASCADE,
    channel_type TEXT NOT NULL,
    raw_identifier TEXT,                -- unhashed (optional, operational)
    last_contacted_at TIMESTAMPTZ,
    PRIMARY KEY (prospect_id, channel_type)
);



/* =========================================================
   PROSPECT INTERESTS
   ---------------------------------------------------------
   Captures what packages a prospect is interested in.
   Also tracks outcomes (e.g., booked).
   ========================================================= */
CREATE TABLE prospect_interests (
    prospect_id UUID NOT NULL REFERENCES prospects(id) ON DELETE CASCADE,
    agency_id UUID NOT NULL REFERENCES agencies(id) ON DELETE CASCADE,
    agency_package UUID REFERENCES agency_packages(id),
    package_type TEXT,                  -- e.g. 'custom'
    session_id UUID,                    -- linked later to sessions
    created_at TIMESTAMPTZ DEFAULT now(),
    outcome TEXT,                       -- booked, declined, etc
    PRIMARY KEY (prospect_id, agency_id, created_at)
);



/* =========================================================
   SESSIONS
   ---------------------------------------------------------
   A logical conversation thread around a single intent.
   Intent can evolve; session ends when outcome is reached.
   ========================================================= */
CREATE TABLE sessions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    prospect_id UUID NOT NULL REFERENCES prospects(id) ON DELETE CASCADE,
    intent TEXT,                        -- exploring, trip_to_x, etc
    first_impression JSONB DEFAULT NULL,
    change_in_intent BOOLEAN DEFAULT false,
    intent_history JSONB DEFAULT '[]',
    intent_confidence NUMERIC(3,2),
    essence TEXT,                       -- distilled goal
    constraints TEXT,                   -- budget, time, visa, etc
    decision_pending BOOLEAN DEFAULT false,
    initiated_at TIMESTAMPTZ DEFAULT now(),
    ended_at TIMESTAMPTZ,
    is_ended BOOLEAN DEFAULT false
);



/* =========================================================
   CHATS
   ---------------------------------------------------------
   Message-level storage.
   Preserves order and sender for exact replay/training.
   ========================================================= */
CREATE TABLE chats (
    session_id UUID NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    msg_sequence INT NOT NULL,
    sender TEXT NOT NULL,               -- prospect | assistant
    msg TEXT,
    PRIMARY KEY (session_id, msg_sequence)
);


/* =========================================================
    RECOMMENDATION EVENTS
   ---------------------------------------------------------
    Logs every time a recommendation is made.
   ========================================================= */
CREATE TABLE recommendation_events (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id UUID NOT NULL
        REFERENCES sessions(id)
        ON DELETE CASCADE,
    prospect_request TEXT NOT NULL,
    request_embedding VECTOR(1536),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);



/* =========================================================
    RECOMMENDATION ITEMS
   ---------------------------------------------------------
    Stores each recommended package for an event.
   ========================================================= */
CREATE TABLE recommendation_items (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    recommendation_event_id UUID NOT NULL
        REFERENCES recommendation_events(id)
        ON DELETE CASCADE,
    session_id UUID NOT NULL
        REFERENCES sessions(id)
        ON DELETE CASCADE,
    package_id UUID NOT NULL,
    package_obj_snapshot JSONB NOT NULL,
    memory_embedding VECTOR(1536), -- Combination of request + package for semantic search
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);



/* =========================================================
   PROSPECT SNAPSHOT (VIEW)
   ---------------------------------------------------------
   Read-optimized collation for fast querying.
   ========================================================= */
CREATE VIEW prospect_snapshot AS
SELECT
    p.id AS prospect_id,
    p.agency_id,
    p.name,
    p.psyche_eval,
    s.id AS session_id,
    pi.agency_package,
    pi.package_type,
    pp.last_contacted_at,
    s.decision_pending,
    pi.outcome,
    p.created_at
FROM prospects p
LEFT JOIN sessions s
    ON s.prospect_id = p.id
LEFT JOIN prospect_interests pi
    ON pi.prospect_id = p.id
LEFT JOIN prospect_presence pp
    ON pp.prospect_id = p.id;


/* =========================================================
   INDEXES
   ---------------------------------------------------------
   Performance optimizations (ordered by table definition)
   ========================================================= */


/* ---------- Agencies ---------- */
CREATE INDEX idx_agencies_email_name
ON agencies (issued_email, name);

CREATE INDEX idx_agencies_created_at
ON agencies (created_at);


/* ---------- Plans ---------- */
/* (no indexes defined) */


/* ---------- Active Packages ---------- */
/* (composite PK already covers lookups) */


/* ---------- Agency Packages ---------- */
CREATE INDEX idx_packages_agency_ideal_for_active
ON agency_packages
USING GIN (ideal_for)
WHERE is_active = true;

CREATE INDEX idx_packages_agency_category_destination_active
ON agency_packages (agency_id, category, destination)
WHERE is_active = true;

CREATE INDEX idx_packages_agency_price_active
ON agency_packages (agency_id, price_amount)
WHERE is_active = true
  AND price_amount IS NOT NULL;

CREATE INDEX idx_packages_agency_created_active
ON agency_packages (agency_id, created_at DESC)
WHERE is_active = true;

/* ---------- Prospects ---------- */
CREATE INDEX idx_prospects_agency_id_name
ON prospects (agency_id, name);

CREATE INDEX idx_prospects_created_at
ON prospects (created_at);


/* ---------- Contact Methods ---------- */
CREATE UNIQUE INDEX idx_contact_methods_channel_identifier_hash
ON contact_methods (channel_type, identifier_hash);

CREATE INDEX idx_contact_methods_prospect_id
ON contact_methods (prospect_id);


/* ---------- Prospect Presence ---------- */
CREATE INDEX idx_presence_channel_identifier
ON prospect_presence (channel_type, raw_identifier);

CREATE INDEX idx_presence_prospect_id
ON prospect_presence (prospect_id);

CREATE INDEX idx_presence_last_contacted_at
ON prospect_presence (last_contacted_at);


/* ---------- Prospect Interests ---------- */
CREATE INDEX idx_interests_agency_prospect
ON prospect_interests (agency_id, prospect_id);

CREATE INDEX idx_interests_created_at
ON prospect_interests (created_at);

CREATE INDEX idx_interests_session_id
ON prospect_interests (session_id);


/* ---------- Sessions ---------- */
CREATE INDEX idx_sessions_active_prospect
ON sessions (prospect_id)
WHERE ended_at IS NULL;

CREATE INDEX idx_sessions_prospect_intent
ON sessions (prospect_id, intent);

CREATE INDEX idx_sessions_initiated_at_desc
ON sessions (initiated_at DESC);


/* ---------- Chats ---------- */
CREATE INDEX idx_chats_session_id
ON chats (session_id);

/* ---------- Recommendation Events ---------- */
create index idx_re_events_session_created
on recommendation_events (session_id, created_at desc);

/* ---------- Recommendation Items ---------- */
create index idx_re_items_session_created
on recommendation_items (session_id, created_at desc);


/* =========================================================
   FUNCTION & PROCEDURES & Trigers
   ---------------------------------------------------------
   Atomicity, Ease and Productivity
   ========================================================= */


/* ----------- Atomic Registration Of Prospects ----------- */
CREATE OR REPLACE FUNCTION register_prospect(
    p_agency_id UUID,
    p_name TEXT,
    p_channel_type TEXT,
    p_identifier_hash TEXT
)
RETURNS UUID
LANGUAGE plpgsql
AS $$
DECLARE
    v_prospect_id UUID;
BEGIN
    -- Register Prospect
    INSERT INTO prospects (agency_id, name)
    VALUES (p_agency_id, p_name)
    RETURNING id INTO v_prospect_id;

    -- Register Contact Method (enforces uniqueness)
    INSERT INTO contact_methods (
        prospect_id,
        channel_type,
        identifier_hash
    )
    VALUES (
        v_prospect_id,
        p_channel_type,
        p_identifier_hash
    );

    RETURN v_prospect_id;

EXCEPTION
    WHEN unique_violation THEN
        -- Contact already exists → fetch owning prospect
        SELECT prospect_id
        INTO v_prospect_id
        FROM contact_methods
        WHERE channel_type = p_channel_type
          AND identifier_hash = p_identifier_hash;

        RETURN v_prospect_id;
END;
$$;


/* ----------- Handling Prospect Presence ----------- */
CREATE OR REPLACE FUNCTION upsert_prospect_presence(
    p_prospect_id UUID,
    p_channel_type TEXT,
    p_raw_identifier TEXT
)
RETURNS VOID
LANGUAGE plpgsql
AS $$
BEGIN
    -- 1️⃣ Update last_contacted_at if exists, else insert new row
    INSERT INTO prospect_presence (
        prospect_id,
        channel_type,
        raw_identifier,
        last_contacted_at
    )
    VALUES (
        p_prospect_id,
        p_channel_type,
        p_raw_identifier,
        now()
    )
    ON CONFLICT (prospect_id, channel_type)
    DO UPDATE
       SET last_contacted_at = now();

    -- 2️⃣ Delete rows older than 7 days (silence threshold)
    DELETE FROM prospect_presence
    WHERE last_contacted_at < now() - interval '7 days';
END;
$$;


/* ----------- Auto Increment Msg Sequence ----------- */
CREATE OR REPLACE FUNCTION auto_incr_msg_seq()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    IF NEW.msg_sequence IS NULL THEN
        SELECT COALESCE(MAX(msg_sequence), 0) + 1
        INTO NEW.msg_sequence
        FROM chats
        WHERE session_id = NEW.session_id;
    END IF;

    RETURN NEW;
END;
$$;

CREATE TRIGGER auto_incr_msg_seq_trigger
BEFORE INSERT ON chats
FOR EACH ROW
EXECUTE FUNCTION auto_incr_msg_seq();

/* ----------- Table Schema ----------- */
CREATE OR REPLACE FUNCTION get_table_schema(
    p_schema_name TEXT,
    p_table_name  TEXT,
    is_sample_requested BOOLEAN DEFAULT false
)
RETURNS JSONB
LANGUAGE plpgsql
AS $$
DECLARE
    v_columns        JSONB;
    v_sample_row     JSONB;
    v_not_null_where TEXT;
BEGIN

    ---- Get column metadata
    SELECT jsonb_agg(
        jsonb_build_object(
            'column_name', column_name,
            'data_type', data_type,
            'is_nullable', is_nullable,
            'position', ordinal_position
        )
        ORDER BY ordinal_position
    )
    INTO v_columns
    FROM information_schema.columns
    WHERE table_schema = p_schema_name
      AND table_name   = p_table_name;

    ---- Build "all columns IS NOT NULL" condition
    IF is_sample_requested THEN
        SELECT string_agg(
            format('%I IS NOT NULL', column_name),
            ' AND '
        )
        INTO v_not_null_where
        FROM information_schema.columns
        WHERE table_schema = p_schema_name
          AND table_name   = p_table_name;
    END IF;

    ---- Fetch ONE fully-populated sample row (optional)
    IF is_sample_requested AND v_not_null_where IS NOT NULL THEN
        EXECUTE format(
            'SELECT to_jsonb(t)
             FROM %I.%I t
             WHERE %s
             LIMIT 1',
            p_schema_name,
            p_table_name,
            v_not_null_where
        )
        INTO v_sample_row;
    END IF;

    ---- Return result
    RETURN jsonb_build_object(
        'schema', p_schema_name,
        'table', p_table_name,
        'columns', COALESCE(v_columns, '[]'::jsonb),
        'sample_row', v_sample_row
    );
END;
$$;

/* ----------- Generic SQL Query Executor ----------- */
CREATE OR REPLACE FUNCTION public.query_sql(sql text)
RETURNS SETOF jsonb AS $$
BEGIN
    RETURN QUERY EXECUTE format(
        'SELECT row_to_json(t)::jsonb FROM (%s) t', 
        sql
    );
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;



-- ========================================================================
--  REALTIME CONFIGURATION
-- ========================================================================

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_publication
        WHERE pubname = 'supabase_realtime'
    ) THEN
        CREATE PUBLICATION supabase_realtime;
    END IF;
END $$;

ALTER PUBLICATION supabase_realtime
ADD TABLE public.prospect_presence;


