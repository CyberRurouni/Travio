
/* =========================================================
   EXTENSIONS
   ========================================================= */
CREATE EXTENSION IF NOT EXISTS "pgcrypto";
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS supabase_vault;
create extension if not exists pg_cron;


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
    agent_email TEXT,         -- email used by agent to send emails
    vault_secret_id UUID,      -- reference to Vault secret (app password)
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

    agency_id UUID NOT NULL REFERENCES agencies(id) ON DELETE CASCADE,

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
   Note: No agency_id here! Prospects are global entities.
   ========================================================= */
CREATE TABLE prospects (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT,
    psyche_eval TEXT,
    psyche_eval_confidence NUMERIC(3,2),
    created_at TIMESTAMPTZ DEFAULT now()
);



/* =========================================================
    AGENCY-PROSPECT LINK
    ---------------------------------------------------------
    Links prospects to the agencies they've contacted.
    This enables many-to-many relationship.
   ========================================================= */
CREATE TABLE agency_prospects (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),  -- Optional, could use composite PK
    agency_id UUID NOT NULL REFERENCES agencies(id) ON DELETE CASCADE,
    prospect_id UUID NOT NULL REFERENCES prospects(id) ON DELETE CASCADE,
    first_contacted_at TIMESTAMPTZ DEFAULT now(),  -- Useful metadata
    last_contacted_at TIMESTAMPTZ DEFAULT now(),   -- Denormalized for performance     
    UNIQUE(agency_id, prospect_id)                   -- Prevent duplicates
);



/* =========================================================
   CONTACT METHODS
   ---------------------------------------------------------
   All channels/identifiers used by a prospect.
   Still per-prospect (global to the person).
   ========================================================= */
CREATE TABLE contact_methods (
    prospect_id UUID NOT NULL REFERENCES prospects(id) ON DELETE CASCADE,
    channel_type TEXT NOT NULL,
    identifier_hash TEXT NOT NULL,
    created_at TIMESTAMPTZ DEFAULT now(),
    PRIMARY KEY (channel_type, identifier_hash)  -- Global uniqueness across all agencies!
);



/* =========================================================
   PROSPECT PRESENCE
   ---------------------------------------------------------
   Now linked through agency_prospects to know which agency
   context the presence is for.
   ========================================================= */
CREATE TABLE prospect_presence (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),  -- Need ID since composite PK changed
    agency_prospect_id UUID NOT NULL REFERENCES agency_prospects(id) ON DELETE CASCADE,
    channel_type TEXT NOT NULL,
    raw_identifier TEXT,
    last_contacted_at TIMESTAMPTZ,
    UNIQUE(agency_prospect_id, channel_type)
);



/* =========================================================
   SESSIONS
   ---------------------------------------------------------
   Now linked through agency_prospects to maintain agency context.
   ========================================================= */
CREATE TABLE sessions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    agency_prospect_id UUID NOT NULL REFERENCES agency_prospects(id) ON DELETE CASCADE,
    intent TEXT,                   -- 'browsing', 'inquiring', 'ready_to_book', etc
    first_impression JSONB DEFAULT NULL,
    change_in_intent BOOLEAN DEFAULT false,
    intent_history JSONB DEFAULT '[]',
    intent_confidence NUMERIC(3,2),
    constraints TEXT,             -- e.g., "budget<2000, destination=Miami"
    decision_pending BOOLEAN DEFAULT false,
    initiated_at TIMESTAMPTZ DEFAULT now(),
    last_activity_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    ended_at TIMESTAMPTZ,
    is_ended BOOLEAN DEFAULT false
);



/* =========================================================
   PROSPECT INTERESTS
   ---------------------------------------------------------
   Now linked through agency_prospects.
   ========================================================= */
CREATE TABLE prospect_interests (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    agency_prospect_id UUID NOT NULL REFERENCES agency_prospects(id) ON DELETE CASCADE,
    agency_package UUID REFERENCES agency_packages(id),
    package_type TEXT,
    session_id UUID REFERENCES sessions(id),
    created_at TIMESTAMPTZ DEFAULT now(),
    outcome TEXT
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
    ap.agency_id,
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
JOIN agency_prospects ap ON ap.prospect_id = p.id
LEFT JOIN sessions s ON s.agency_prospect_id = ap.id
LEFT JOIN prospect_interests pi ON pi.agency_prospect_id = ap.id
LEFT JOIN prospect_presence pp ON pp.agency_prospect_id = ap.id;



/* =========================================================
   INDEXES
   ---------------------------------------------------------
   Performance optimizations (ordered by table definition)
   ========================================================= */


/* ---------- Agencies ---------- */
CREATE INDEX idx_agencies_email_name
ON agencies (issued_email, name);

CREATE INDEX idx_agencies_agent_email
ON agencies (agent_email);

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


/* ---------- Agency Prospects ---------- */
CREATE UNIQUE INDEX idx_agency_prospects_agency_prospect
ON agency_prospects (agency_id, prospect_id);


/* ---------- Contact Methods ---------- */
CREATE UNIQUE INDEX idx_contact_methods_channel_identifier_hash
ON contact_methods (channel_type, identifier_hash);

CREATE INDEX idx_contact_methods_prospect_id
ON contact_methods (prospect_id);


/* ---------- Prospect Presence ---------- */
CREATE INDEX idx_presence_channel_identifier
ON prospect_presence (channel_type, raw_identifier);

CREATE INDEX idx_presence_prospect_id
ON prospect_presence (agency_prospect_id);

CREATE INDEX idx_presence_last_contacted_at
ON prospect_presence (last_contacted_at);

CREATE UNIQUE INDEX prospect_presence_unique_channel
ON prospect_presence (agency_prospect_id, channel_type);


/* ---------- Sessions ---------- */
CREATE UNIQUE INDEX idx_sessions_active_prospect
ON sessions (agency_prospect_id)
WHERE ended_at IS NULL;

CREATE INDEX idx_sessions_prospect_intent
ON sessions (agency_prospect_id, intent);

CREATE INDEX idx_sessions_initiated_at_desc
ON sessions (initiated_at DESC);


/* ---------- Prospect Interests ---------- */
-- Note: The original had a reference to agency_id which doesn't exist in prospect_interests
-- I've removed that invalid index
CREATE INDEX idx_interests_created_at
ON prospect_interests (created_at);

CREATE INDEX idx_interests_session_id
ON prospect_interests (session_id);

CREATE INDEX idx_interests_agency_prospect
ON prospect_interests (agency_prospect_id);


/* ---------- Chats ---------- */
CREATE INDEX idx_chats_session_id
ON chats (session_id);


/* ---------- Recommendation Events ---------- */
CREATE INDEX idx_re_events_session_created
ON recommendation_events (session_id, created_at DESC);


/* ---------- Recommendation Items ---------- */
CREATE INDEX idx_re_items_session_created
ON recommendation_items (session_id, created_at DESC);

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
RETURNS UUID  -- Returns agency_prospect_id
LANGUAGE plpgsql
AS $$
DECLARE
    v_prospect_id UUID;
    v_agency_prospect_id UUID;
BEGIN
    -- First, find or create the global prospect
    SELECT prospect_id INTO v_prospect_id
    FROM contact_methods
    WHERE channel_type = p_channel_type
      AND identifier_hash = p_identifier_hash;

    IF v_prospect_id IS NULL THEN
        -- Create new global prospect
        INSERT INTO prospects (name)
        VALUES (p_name)
        RETURNING id INTO v_prospect_id;

        -- Add contact method
        INSERT INTO contact_methods (prospect_id, channel_type, identifier_hash)
        VALUES (v_prospect_id, p_channel_type, p_identifier_hash);
    END IF;

    -- Link prospect to agency (if not already linked)
    INSERT INTO agency_prospects (agency_id, prospect_id, first_contacted_at, last_contacted_at)
    VALUES (p_agency_id, v_prospect_id, now(), now())
    ON CONFLICT (agency_id, prospect_id) 
    DO UPDATE SET last_contacted_at = now()
    RETURNING id INTO v_agency_prospect_id;

    RETURN v_agency_prospect_id;
END;
$$;

/* ----------- Handling Prospect Presence ----------- */
CREATE OR REPLACE FUNCTION touch_prospect_presence(
    p_agency_prospect_id UUID,
    p_channel_type TEXT,
    p_raw_identifier TEXT
)
RETURNS VOID
LANGUAGE plpgsql
AS $$
BEGIN
    INSERT INTO prospect_presence (
        agency_prospect_id,
        channel_type,
        raw_identifier,
        last_contacted_at
    )
    VALUES (
        p_agency_prospect_id,
        p_channel_type,
        p_raw_identifier,
        now()
    )
    ON CONFLICT (agency_prospect_id, channel_type)
    DO UPDATE SET last_contacted_at = now();
END;
$$;

/* ----------- Prune Stale Presence ----------- */
CREATE OR REPLACE FUNCTION prune_stale_presence()
RETURNS VOID
LANGUAGE plpgsql
AS $$
BEGIN
    DELETE FROM prospect_presence
    WHERE last_contacted_at < now() - interval '7 days';
END;
$$;

/* ----------- Atomic Initiation Of Sessions ----------- */
CREATE OR REPLACE FUNCTION initiate_session(
    p_agency_prospect_id UUID,
    p_intent TEXT DEFAULT NULL,
    p_intent_confidence NUMERIC(3,2) DEFAULT NULL,
    p_first_impression JSONB DEFAULT NULL,
    p_constraints TEXT DEFAULT NULL
)
RETURNS UUID
LANGUAGE plpgsql
AS $$
DECLARE
    v_session_id UUID;
BEGIN
    -- Try insert
    INSERT INTO sessions (
        agency_prospect_id,
        intent,
        intent_confidence,
        first_impression,
        constraints,
        change_in_intent,
        decision_pending,
        is_ended
    )
    VALUES (
        p_agency_prospect_id,
        p_intent,
        p_intent_confidence,
        p_first_impression,
        p_constraints,
        false,
        false,
        false
    )
    ON CONFLICT DO NOTHING
    RETURNING id INTO v_session_id;

    -- If insert didn't happen, fetch existing active session
    IF v_session_id IS NULL THEN
        SELECT id INTO v_session_id
        FROM sessions
        WHERE agency_prospect_id = p_agency_prospect_id
          AND is_ended = false;
    END IF;

    RETURN v_session_id;
END;
$$;

/* ----------- Update Session Activity ----------- */
CREATE OR REPLACE FUNCTION touch_session(
    p_session_id UUID
)
RETURNS VOID
LANGUAGE plpgsql
AS $$
BEGIN
    UPDATE sessions
    SET last_activity_at = now()
    WHERE id = p_session_id
      AND ended_at IS NULL;
END;
$$;

/* ----------- Atomic End Session ----------- */
CREATE OR REPLACE FUNCTION end_session(
    p_session_id UUID
)
RETURNS BOOLEAN
LANGUAGE plpgsql
AS $$
BEGIN
    UPDATE sessions
    SET ended_at = now(),
        is_ended = true
    WHERE id = p_session_id
      AND ended_at IS NULL;

    RETURN FOUND;
END;
$$;

/* ----------- End Stale Sessions ----------- */
CREATE OR REPLACE FUNCTION end_stale_sessions()
RETURNS VOID
LANGUAGE plpgsql
AS $$
BEGIN
    UPDATE sessions
    SET ended_at = now(),
        is_ended = true          -- keep in sync with your end_session() func
    WHERE ended_at IS NULL
      AND last_activity_at < now() - interval '7 days';
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


/* ----------- Create Vault Secret ----------- */
CREATE OR REPLACE FUNCTION public.create_vault_secret(
    p_secret TEXT,      -- the sensitive value to store (e.g., app password)
    p_name TEXT,        -- logical grouping or name (e.g., "agency_x")
    p_description TEXT, -- optional description for reference
    p_key_id UUID       -- the UUID of the pgsodium key to use (can be NULL)
)
RETURNS UUID
LANGUAGE sql
SECURITY DEFINER
AS $$
    -- We use COALESCE to ensure the Vault extension receives 
    -- valid types even if parameters are passed as NULL.
    SELECT vault.create_secret(
        p_secret,                         -- your secret
        COALESCE(p_name, '')::text,       -- maps to 'name' in vault
        COALESCE(p_description, '')::text, -- maps to 'description' in vault
        p_key_id                          -- must be UUID type (from pgsodium)
    );
$$;

/* ----------- Get Vault Secret ----------- */
CREATE OR REPLACE FUNCTION public.get_vault_secret(
    p_secret_id UUID -- the UUID of the secret you want to retrieve
)
RETURNS TABLE(decrypted_secret TEXT) 
LANGUAGE sql
SECURITY DEFINER -- Required to bypass RLS on the vault schema
AS $$
    -- decrypted_secrets is a VIEW, so we SELECT from it 
    -- and filter by the ID column.
    SELECT decrypted_secret
    FROM vault.decrypted_secrets
    WHERE id = p_secret_id;
$$;


-- ========================================================================
--  CRON JOBS
-- ========================================================================

-- Schedule prune_stale_presence to run daily at 2am
select cron.schedule(
  'prune-stale-presence',
  '0 2 * * *',
  $$ select prune_stale_presence(); $$
);

-- Schedule end_stale_sessions to run daily at 3am
select cron.schedule(
  'end-stale-sessions',
  '0 3 * * *',
  $$ select end_stale_sessions(); $$
);


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


