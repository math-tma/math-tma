-- Math TMA — PostgreSQL schema
-- Run once against your Railway Postgres database (or via database.py init_db()).

CREATE TABLE IF NOT EXISTS users (
    id                      BIGINT PRIMARY KEY,          -- Telegram user id
    username                TEXT,
    coins                   BIGINT NOT NULL DEFAULT 0,
    diamonds                BIGINT NOT NULL DEFAULT 0,
    referrer_id             BIGINT REFERENCES users(id),
    referral_ads_watched    INT NOT NULL DEFAULT 0,       -- ads watched BY this user, counted for referrer's reward
    referral_rewarded       BOOLEAN NOT NULL DEFAULT FALSE,
    last_daily_claim        DATE,
    daily_doubled_today     BOOLEAN NOT NULL DEFAULT FALSE,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS game_sessions (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         BIGINT NOT NULL REFERENCES users(id),
    started_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    ends_at         TIMESTAMPTZ NOT NULL,
    extended        BOOLEAN NOT NULL DEFAULT FALSE,   -- +15s continue already used
    multiplier_used BOOLEAN NOT NULL DEFAULT FALSE,
    finished        BOOLEAN NOT NULL DEFAULT FALSE,
    correct_count   INT NOT NULL DEFAULT 0,
    coins_earned    INT NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS problems (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id      UUID NOT NULL REFERENCES game_sessions(id),
    question        TEXT NOT NULL,
    options         JSONB NOT NULL,       -- ["12", "14", "9", "20"]
    correct_index   INT NOT NULL,         -- never sent to the client
    answered        BOOLEAN NOT NULL DEFAULT FALSE,
    is_correct      BOOLEAN
);
CREATE INDEX IF NOT EXISTS idx_problems_session ON problems(session_id);

-- Every ad view (rewarded video) — diamonds/bonuses are ONLY credited when
-- AdsGram's server-to-server postback marks a row 'completed'. Never trust
-- a client-side "ad finished" event for crediting rewards.
CREATE TABLE IF NOT EXISTS ad_views (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         BIGINT NOT NULL REFERENCES users(id),
    purpose         TEXT NOT NULL,        -- 'diamond' | 'continue' | 'multiplier' | 'daily_double' | 'sponsor_recheck'
    session_id      UUID REFERENCES game_sessions(id),
    status          TEXT NOT NULL DEFAULT 'pending',  -- pending | completed | expired
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at    TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_ad_views_user ON ad_views(user_id);

CREATE TABLE IF NOT EXISTS sponsor_tasks (
    id                  SERIAL PRIMARY KEY,
    channel_username    TEXT NOT NULL,    -- e.g. '@my_channel'
    channel_id          BIGINT NOT NULL,  -- numeric id the bot uses for getChatMember
    reward              INT NOT NULL DEFAULT 40,
    active              BOOLEAN NOT NULL DEFAULT TRUE
);

CREATE TABLE IF NOT EXISTS sponsor_task_completions (
    task_id         INT NOT NULL REFERENCES sponsor_tasks(id),
    user_id         BIGINT NOT NULL REFERENCES users(id),
    completed_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (task_id, user_id)
);

CREATE TABLE IF NOT EXISTS payout_requests (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         BIGINT NOT NULL REFERENCES users(id),
    stars           INT NOT NULL,
    diamonds_spent  INT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'pending',  -- pending | paid | rejected
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    paid_at         TIMESTAMPTZ
);

-- Cosmetic shop: backgrounds bought with Coins (never Diamonds/Stars).
CREATE TABLE IF NOT EXISTS backgrounds (
    id          INT PRIMARY KEY,
    name        TEXT NOT NULL,
    css_value   TEXT NOT NULL,   -- CSS `background` value (gradient) applied to the app
    price       INT NOT NULL,
    animated    BOOLEAN NOT NULL DEFAULT FALSE
);

CREATE TABLE IF NOT EXISTS user_backgrounds (
    user_id         BIGINT NOT NULL REFERENCES users(id),
    background_id   INT NOT NULL REFERENCES backgrounds(id),
    purchased_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, background_id)
);

ALTER TABLE users ADD COLUMN IF NOT EXISTS selected_background_id INT REFERENCES backgrounds(id);

INSERT INTO backgrounds (id, name, css_value, price, animated) VALUES
 (1, 'Klassik',        'linear-gradient(180deg,#ffffff,#ffffff)',                          0,    FALSE),
 (2, 'Ocean Breeze',    'linear-gradient(135deg,#4facfe,#00f2fe)',                          150,  FALSE),
 (3, 'Sunset Vibes',    'linear-gradient(135deg,#ff9a56,#ff6a88)',                          300,  FALSE),
 (4, 'Zumrad O''rmon',  'linear-gradient(135deg,#11998e,#38ef7d)',                          500,  FALSE),
 (5, 'Binafsha Tuman',  'linear-gradient(135deg,#654ea3,#eaafc8)',                          800,  FALSE),
 (6, 'Oltin Soat',      'linear-gradient(135deg,#f7971e,#ffd200)',                          1300, FALSE),
 (7, 'Shimoliy Chiroq', 'linear-gradient(135deg,#00c6ff,#0072ff,#7f00ff)',                  2000, TRUE),
 (8, 'Galaktika Orzu',  'linear-gradient(135deg,#0f0c29,#302b63,#24243e)',                  3200, TRUE),
 (9, 'Olmos Nafisligi', 'linear-gradient(135deg,#e0eafc,#cfdef3,#ffffff,#e0eafc)',          5000, TRUE)
ON CONFLICT (id) DO NOTHING;

-- Single-row-per-key config table. Cashout toggle + pool are read/written
-- inside a transaction with SELECT ... FOR UPDATE to avoid race conditions
-- when many users cash out at the same moment.
CREATE TABLE IF NOT EXISTS settings (
    key     TEXT PRIMARY KEY,
    value   TEXT NOT NULL
);
INSERT INTO settings (key, value) VALUES ('is_cashout_open', 'false') ON CONFLICT DO NOTHING;
INSERT INTO settings (key, value) VALUES ('stars_pool_remaining', '450') ON CONFLICT DO NOTHING;
INSERT INTO settings (key, value) VALUES ('max_stars_pool', '450') ON CONFLICT DO NOTHING;
