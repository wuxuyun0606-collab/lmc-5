-- LMC-5 pgvector backend · production-grade schema
-- 配合 extras/pgvector_backend/*.py 使用。
-- 设计目标：每个轴都有可审计的落库结构，没有任何"模型直接改写库"的捷径。

-- 前置依赖 ---------------------------------------------------------------
CREATE EXTENSION IF NOT EXISTS vector;

-- === Curated memories（XYZEM 五轴的主表）-------------------------------
CREATE TABLE IF NOT EXISTS lmc5_curated_memories (
    id              BIGSERIAL PRIMARY KEY,

    -- 内容
    source          TEXT NOT NULL,            -- 来自哪个生产路径（dream / heartbeat / manual / ...）
    category        TEXT NOT NULL,            -- 业务分类（heartbeat / identity / core / fragments / ...）
    title           TEXT,
    content         TEXT NOT NULL,
    keywords        TEXT,
    topic_tag       TEXT,

    -- M 轴 · 代谢 / 排序
    weight          NUMERIC(6,3) DEFAULT 1.0,
    original_weight NUMERIC(6,3),
    hit_count       INTEGER DEFAULT 0,
    last_hit        TIMESTAMPTZ,
    depth           INTEGER,                   -- 1-5 加深度系数
    activation_boost NUMERIC(6,3) DEFAULT 0,   -- 时间涟漪累积

    -- E 轴 · 情绪 / 体验信号
    valence         REAL,                      -- [-1, 1]
    arousal         REAL,                      -- [0, 1]
    tension         REAL,                      -- [0, 1]
    emotion_confidence REAL,                   -- [0, 1]
    response_tendency TEXT,                    -- comfort | engage | withdraw | alert
    growth_delta    TEXT,                      -- growth | stable | setback
    emotion_scorer  TEXT,
    emotion_rubric_version TEXT,
    mood_icon       TEXT,

    -- Z 轴 · 事实演化
    version_status  TEXT NOT NULL DEFAULT 'current',
                                               -- current / review / superseded / historical / archived / candidate_thread
    superseded_by   BIGINT REFERENCES lmc5_curated_memories(id),
    superseded_at   TIMESTAMPTZ,
    valid_at        TIMESTAMPTZ,               -- 现实轴：事实生效时间
    invalid_at      TIMESTAMPTZ,               -- 现实轴：被覆盖后失效时间
    fact_key        TEXT,                      -- 同一事实的多版本共享此 key
    active_fact     BOOLEAN DEFAULT FALSE,
    protected       BOOLEAN DEFAULT FALSE,     -- protected 记忆永不衰减、不被自动覆盖

    -- 元数据
    confidence      REAL,
    trust_level     REAL,
    resolved        BOOLEAN DEFAULT FALSE,
    digested        BOOLEAN DEFAULT FALSE,
    source_file     TEXT DEFAULT '',           -- 追溯：来自哪条 dream 候选 / 哪段 chunk
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS lmc5_curated_status_idx
    ON lmc5_curated_memories (version_status);
CREATE INDEX IF NOT EXISTS lmc5_curated_fact_key_idx
    ON lmc5_curated_memories (fact_key) WHERE fact_key IS NOT NULL;
CREATE INDEX IF NOT EXISTS lmc5_curated_category_idx
    ON lmc5_curated_memories (category);
CREATE INDEX IF NOT EXISTS lmc5_curated_created_idx
    ON lmc5_curated_memories (created_at DESC);


-- === Vectors（owner-keyed embeddings）---------------------------------
CREATE TABLE IF NOT EXISTS lmc5_vectors (
    id              BIGSERIAL PRIMARY KEY,
    owner_type      TEXT NOT NULL,            -- curated / chunk / event / ...
    owner_id        BIGINT NOT NULL,
    text_preview    TEXT,
    embedding       halfvec(3072),            -- 与 LMC5Config.embed_dim 一致；如改 1024 同步改这里
    model_name      TEXT NOT NULL DEFAULT 'gemini-embedding-2',
    dimension       INTEGER NOT NULL DEFAULT 3072,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (owner_type, owner_id, model_name, dimension)
);

CREATE INDEX IF NOT EXISTS lmc5_vectors_ivfflat
    ON lmc5_vectors USING ivfflat (embedding halfvec_cosine_ops)
    WITH (lists = 100);


-- === Memory relations（Y 轴）------------------------------------------
CREATE TABLE IF NOT EXISTS lmc5_memory_relations (
    id              BIGSERIAL PRIMARY KEY,
    source_id       BIGINT NOT NULL REFERENCES lmc5_curated_memories(id),
    target_id       BIGINT NOT NULL REFERENCES lmc5_curated_memories(id),
    relation_type   TEXT NOT NULL,
                                              -- safe: same_event / same_topic / temporal_sequence /
                                              --       emotional_link / derived_from / in_thread
                                              -- review: contradiction / cause_effect / supports
    strength        REAL DEFAULT 0.5,         -- [0, 1]
    reason          TEXT,
    valid_from      TIMESTAMPTZ DEFAULT NOW(),
    valid_until     TIMESTAMPTZ,              -- NULL = 活边；NOT NULL = 已注销
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (source_id, target_id, relation_type)
);

CREATE INDEX IF NOT EXISTS lmc5_relations_source_idx
    ON lmc5_memory_relations (source_id) WHERE valid_until IS NULL;
CREATE INDEX IF NOT EXISTS lmc5_relations_target_idx
    ON lmc5_memory_relations (target_id) WHERE valid_until IS NULL;
CREATE INDEX IF NOT EXISTS lmc5_relations_type_idx
    ON lmc5_memory_relations (relation_type);


-- === Z 轴审计表（contradiction → supersede 必经路径）-------------------
-- 设计铁律：housekeeper LLM 永远不直接 supersede；判决落 z_audit，
-- 人工 --approve 后才执行 UPDATE curated.version_status='superseded'。
CREATE TABLE IF NOT EXISTS lmc5_z_audit (
    id              BIGSERIAL PRIMARY KEY,
    pair_key        TEXT NOT NULL,            -- "small_id:large_id" 去重键
    content_hash    TEXT NOT NULL,            -- MD5(content_a || NUL || content_b)
    verdict         TEXT NOT NULL,            -- supersede / both_valid / parse_fail
    stale_id        BIGINT,                   -- supersede 时：哪条该被覆盖
    current_id      BIGINT,                   -- supersede 时：覆盖方
    reason          TEXT,
    evidence        TEXT,                     -- 必须从原文引用，引用不出来 → both_valid
    status          TEXT NOT NULL DEFAULT 'pending',
                                              -- pending / done / rejected / skip
    judged_at       TIMESTAMPTZ DEFAULT NOW(),
    reviewed_at     TIMESTAMPTZ,
    UNIQUE (pair_key, content_hash)           -- 同一对内容判过就不再判
);

CREATE INDEX IF NOT EXISTS lmc5_z_audit_pending_idx
    ON lmc5_z_audit (status) WHERE status = 'pending';


-- === Hippocampus 写入溯源（防同一候选反复入库）-------------------------
-- night_dream.py 写候选时把 candidate_key 落 curated.source_file，
-- 此表只是说明性 view 占位；如需独立追踪可建实表。
COMMENT ON COLUMN lmc5_curated_memories.source_file IS
    'For dream-promoted memories: stores the candidate_key '
    '(dream-v1:chunks=42,43:type=event:title=...) so repeated hippocampus '
    'runs do not double-write the same observation.';


-- === Raw events journal（三层检索的兜底层 + SessionEnd 钩子的归档目标）-----
-- 设计铁律：raw vs curated 严格分离。原始对话/工具调用进 raw_events，
-- 经过 hippocampus 闸门才能晋升 curated_memories。三层检索的兜底层查这张表。
CREATE TABLE IF NOT EXISTS lmc5_raw_events (
    id              BIGSERIAL PRIMARY KEY,
    session_id      TEXT,
    role            TEXT,                     -- user / assistant / tool / system
    channel         TEXT,                     -- claude_code / telegram / cli / ...
    content         TEXT NOT NULL,
    metadata        JSONB DEFAULT '{}'::jsonb,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    content_tsv     tsvector GENERATED ALWAYS AS (to_tsvector('simple', coalesce(content, ''))) STORED,
    UNIQUE (session_id, role, content)
);

CREATE INDEX IF NOT EXISTS lmc5_raw_events_tsv_idx
    ON lmc5_raw_events USING GIN (content_tsv);
CREATE INDEX IF NOT EXISTS lmc5_raw_events_created_idx
    ON lmc5_raw_events (created_at DESC);
CREATE INDEX IF NOT EXISTS lmc5_raw_events_session_idx
    ON lmc5_raw_events (session_id, created_at);

COMMENT ON TABLE lmc5_raw_events IS
    'Raw event journal (append-only). Hippocampus reads chunks from this table '
    'to propose curated_memories candidates. Recall pipeline queries this table '
    'as the third-stage FTS fallback when vector + curated FTS both miss.';


-- === Cold storage（冷归档）--------------------------------------------
CREATE TABLE IF NOT EXISTS lmc5_cold_storage (
    id              BIGSERIAL PRIMARY KEY,
    original_id     BIGINT NOT NULL,
    source          TEXT,
    category        TEXT,
    title           TEXT,
    content         TEXT,
    weight          NUMERIC(6,3),
    reason          TEXT,                     -- decay_low_weight / manual_archive / ...
    archived_at     TIMESTAMPTZ DEFAULT NOW()
);


-- === Narrative timeline（X 轴提炼层）---------------------------------
CREATE TABLE IF NOT EXISTS lmc5_narrative_index (
    id              BIGSERIAL PRIMARY KEY,
    period          TEXT NOT NULL,            -- weekly / monthly
    start_date      DATE NOT NULL,
    end_date        DATE NOT NULL,
    title           TEXT NOT NULL,
    summary         TEXT NOT NULL,
    seed_memory_ids BIGINT[] DEFAULT '{}',
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (period, start_date)
);


-- === E 轴失败日志（categorized failures）-----------------------------
-- e_axis_scorer.py 默认写文件；如需查询统计，把日志路径换成此表。
CREATE TABLE IF NOT EXISTS lmc5_e_axis_failures (
    id              BIGSERIAL PRIMARY KEY,
    record_id       BIGINT,                   -- 关联的 curated_memories.id（可为空）
    category        TEXT NOT NULL,            -- http_timeout / parse_fail / schema_fail / range_fail / ...
    detail          TEXT,
    occurred_at     TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS lmc5_e_fail_category_idx
    ON lmc5_e_axis_failures (category, occurred_at DESC);


-- === Stopwords（X 轴 chunk 词频用）-----------------------------------
CREATE TABLE IF NOT EXISTS lmc5_dynamic_stopwords (
    id              BIGSERIAL PRIMARY KEY,
    word            TEXT NOT NULL UNIQUE,
    freq            INTEGER NOT NULL,
    learned_at      TIMESTAMPTZ DEFAULT NOW()
);


-- === 推荐迁移顺序 ----------------------------------------------------
-- 1. CREATE EXTENSION vector;
-- 2. 建 lmc5_curated_memories（主表）
-- 3. 建 lmc5_vectors（依赖 curated）
-- 4. 建 lmc5_memory_relations（外键依赖 curated）
-- 5. 建 lmc5_z_audit / lmc5_cold_storage / lmc5_narrative_index
-- 6. 建 lmc5_e_axis_failures / lmc5_dynamic_stopwords
-- 7. 跑数据回填（curated 内容 + embeddings_v3 内容）
--
-- 改 embedding 维度时（3072 ↔ 1024）：lmc5_vectors.embedding 类型同步改 halfvec(N)，
-- 索引重建 lists 参数按 corpus 大小调（1k 用 lists=20，100k 用 lists=200）。
