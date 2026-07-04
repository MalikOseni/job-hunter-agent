PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    external_key TEXT NOT NULL UNIQUE,
    source TEXT NOT NULL,
    company TEXT NOT NULL,
    title TEXT NOT NULL,
    location TEXT NOT NULL,
    url TEXT NOT NULL,
    latest_score INTEGER NOT NULL DEFAULT 0,
    latest_tags TEXT NOT NULL DEFAULT '',
    latest_skills TEXT NOT NULL DEFAULT '',
    latest_posted TEXT,
    latest_age_days INTEGER,
    status TEXT NOT NULL,
    status_color TEXT NOT NULL,
    status_reason TEXT NOT NULL,
    eligibility_passed INTEGER NOT NULL DEFAULT 0,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    is_active INTEGER NOT NULL DEFAULT 1
);

CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);
CREATE INDEX IF NOT EXISTS idx_jobs_last_seen ON jobs(last_seen_at);

CREATE TABLE IF NOT EXISTS job_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    run_id TEXT NOT NULL,
    captured_at TEXT NOT NULL,
    score INTEGER NOT NULL,
    tags TEXT NOT NULL,
    skills TEXT NOT NULL,
    posted TEXT,
    age_days INTEGER,
    status TEXT NOT NULL,
    status_color TEXT NOT NULL,
    status_reason TEXT NOT NULL,
    is_shortlisted INTEGER NOT NULL DEFAULT 0,
    raw_payload_json TEXT NOT NULL,
    UNIQUE(job_id, run_id)
);

CREATE INDEX IF NOT EXISTS idx_job_snapshots_run ON job_snapshots(run_id);
CREATE INDEX IF NOT EXISTS idx_job_snapshots_job ON job_snapshots(job_id);

CREATE TABLE IF NOT EXISTS applications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    external_application_id TEXT,
    source TEXT NOT NULL DEFAULT 'queue',
    current_stage TEXT NOT NULL DEFAULT 'pending_review',
    status_color TEXT NOT NULL DEFAULT 'yellow',
    submitted_at TEXT,
    updated_at TEXT NOT NULL,
    notice_weeks INTEGER,
    salary_expectation REAL,
    salary_currency TEXT,
    relocation_assistance_required INTEGER NOT NULL DEFAULT 1,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    UNIQUE(job_id, source)
);

CREATE INDEX IF NOT EXISTS idx_applications_stage ON applications(current_stage);

CREATE TABLE IF NOT EXISTS application_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    application_id INTEGER NOT NULL REFERENCES applications(id) ON DELETE CASCADE,
    event_type TEXT NOT NULL,
    event_at TEXT NOT NULL,
    notes TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_application_events_application ON application_events(application_id);
CREATE INDEX IF NOT EXISTS idx_application_events_event_at ON application_events(event_at);

CREATE TABLE IF NOT EXISTS artifacts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id INTEGER REFERENCES jobs(id) ON DELETE CASCADE,
    application_id INTEGER REFERENCES applications(id) ON DELETE CASCADE,
    artifact_type TEXT NOT NULL,
    artifact_uri TEXT NOT NULL,
    created_at TEXT NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_artifacts_job ON artifacts(job_id);
CREATE INDEX IF NOT EXISTS idx_artifacts_application ON artifacts(application_id);

CREATE TABLE IF NOT EXISTS salary_benchmarks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id INTEGER REFERENCES jobs(id) ON DELETE SET NULL,
    role_family TEXT,
    market_region TEXT,
    currency TEXT NOT NULL,
    low_amount REAL,
    median_amount REAL,
    high_amount REAL,
    source TEXT NOT NULL,
    collected_at TEXT NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_salary_benchmarks_job ON salary_benchmarks(job_id);
CREATE INDEX IF NOT EXISTS idx_salary_benchmarks_source ON salary_benchmarks(source);

CREATE TABLE IF NOT EXISTS run_logs (
    run_id TEXT PRIMARY KEY,
    started_at TEXT NOT NULL,
    completed_at TEXT,
    run_status TEXT NOT NULL DEFAULT 'running',
    jobs_seen INTEGER NOT NULL DEFAULT 0,
    jobs_policy_eligible INTEGER NOT NULL DEFAULT 0,
    jobs_shortlisted INTEGER NOT NULL DEFAULT 0,
    jobs_upserted INTEGER NOT NULL DEFAULT 0,
    notes TEXT
);

CREATE INDEX IF NOT EXISTS idx_run_logs_started_at ON run_logs(started_at);
