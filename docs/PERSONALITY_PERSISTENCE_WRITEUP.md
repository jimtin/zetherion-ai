# Full Personality Persistence + Contact Profile Aggregation

## Overview

Previously, the Gemini personality extraction pipeline produced a rich `PersonalitySignal` for every email, but `_persist_signals()` only stored a fraction of it — contact name/email/company, `preferences_revealed`, and `schedule_signals`. Everything else was discarded after extraction:

- **WritingStyle** — formality, greeting/signoff patterns, emoji use, vocabulary level, sentence length
- **CommunicationProfile** — primary/secondary traits, emotional tone, assertiveness, responsiveness
- **RelationshipDynamics** — familiarity, power dynamic, trust level, rapport indicators
- **commitments_made**, **expectations_set**
- **author_role** distinction (owner vs contact)

This implementation adds full signal persistence, an aggregation layer that builds evolving per-person profiles from multiple observations, and wires the aggregated profiles into the LLM decision context.

---

## Architecture Flow

```
Email arrives
  -> _extract_personality() -> PersonalitySignal
       -> _persist_signals() (fire-and-forget)
            |-- [existing] upsert PersonalContact
            |-- [existing] increment interaction count
            |-- [existing] store preferences + schedule as learnings
            |-- [NEW] INSERT raw signal -> personality_signal_log
            |-- [NEW] GET existing -> aggregate -> UPSERT -> personality_profiles
            |-- [NEW] store commitments + expectations as learnings
            `-- [NEW] if author_role=owner & obs>=3: enrich personal_profile.communication_style
```

```
DecisionContextBuilder.build()
  |-- [existing] user_profile, contacts, policies, learnings
  |-- [NEW] owner personality profile (writing style, communication traits)
  `-- [NEW] contact personality profiles for mentioned emails
```

---

## Files Changed

### 1. `src/zetherion_ai/personal/models.py` (modified)

Added four new Pydantic models at the end of the file, after the existing `PersonalLearning` class:

**`AggregatedWritingStyle`** — Tracks formality, sentence length, and vocabulary level as distribution dicts (e.g. `{"formal": 7, "casual": 2}`) with a `_mode` field for the most frequent value. Boolean writing traits (uses_greeting, uses_signoff, uses_emoji, uses_bullet_points) are tracked as running rates (0.0–1.0). Greeting and signoff styles are collected as deduped string lists.

**`AggregatedCommunication`** — Distribution counting for primary_trait, secondary_trait, and emotional_tone. Assertiveness tracked via EMA (exponential moving average). Responsiveness signals collected as a deduped string list.

**`AggregatedRelationship`** — EMA for familiarity and trust_level. Distribution counting for power_dynamic. Rapport indicators collected as a deduped string list.

**`PersonalityProfile`** — The main aggregated model combining all three sub-models plus commitments, expectations, preferences, schedule_signals (all as string lists), a confidence score, and timestamps. Includes `to_db_row()` and `from_db_row()` for PostgreSQL serialization. The `from_db_row()` method handles JSONB parsing for the nested sub-models.

Also added `MAX_LIST_ITEMS = 20` constant for capping string lists.

### 2. `src/zetherion_ai/personal/aggregation.py` (new)

Pure aggregation module with zero I/O. Single public function:

```python
def aggregate_signal_into_profile(
    existing: PersonalityProfile,
    signal: PersonalitySignal,
) -> PersonalityProfile:
```

Returns a **new** `PersonalityProfile` — input is never mutated.

**Aggregation strategies by data type:**

| Data type | Strategy | Example |
|---|---|---|
| Enum fields (formality, traits, tone, etc.) | Distribution counting + mode | `{"formal": 7, "casual": 2}` -> mode = "formal" |
| Float fields (assertiveness, familiarity, trust) | EMA: `alpha = min(0.3, 2/(n+1))` | Converges toward true value with more observations |
| Boolean fields (uses_greeting, uses_emoji, etc.) | Running rate: `(old*n + new) / (n+1)` | 7/10 messages with greeting -> rate = 0.7 |
| String lists (styles, indicators, etc.) | Union + dedup (case-insensitive), cap at 20 | No duplicates, bounded growth |
| Confidence | `min(0.95, 1 - 1/(1 + 0.3*n))` | 1 obs -> 0.23, 5 -> 0.60, 10 -> 0.75, 20 -> 0.86 |

Helper functions (all pure, exported for direct testing):
- `_increment_distribution(dist, key)` — returns new dict with key incremented
- `_mode_of(dist, fallback)` — returns key with highest count
- `_ema(old, new, n)` — exponential moving average with adaptive alpha
- `_running_rate(old_rate, new_value, n)` — boolean running rate
- `_merge_string_list(existing, new_items)` — union + case-insensitive dedup + cap
- `_confidence(n)` — confidence growth curve

### 3. `src/zetherion_ai/personal/storage.py` (modified)

**Schema additions** — Two new PostgreSQL tables appended to `PERSONAL_SCHEMA_SQL`:

```sql
-- Raw signal audit log (append-only)
personality_signal_log (
    id, user_id, author_role, author_email, author_name,
    email_external_id, signal_data JSONB, extraction_confidence, created_at
)
-- Index: (user_id, author_email, created_at DESC)

-- Aggregated per-person profiles
personality_profiles (
    id, user_id, subject_email, subject_role,
    observation_count, writing_style JSONB, communication JSONB,
    relationship JSONB, commitments JSONB, expectations JSONB,
    preferences JSONB, schedule_signals JSONB, confidence,
    first_observed, last_observed, updated_at
    UNIQUE (user_id, subject_email, subject_role)
)
-- Index: (user_id, subject_role)
```

**New methods on `PersonalStorage`:**

| Method | Purpose |
|---|---|
| `log_personality_signal(user_id, signal_data, author_role, author_email, ...)` | Append raw signal to audit log, returns log ID |
| `get_personality_profile(user_id, subject_email, subject_role)` | Fetch one aggregated profile or None |
| `upsert_personality_profile(profile)` | Insert or update aggregated profile (ON CONFLICT upsert), returns profile ID |
| `list_personality_profiles(user_id, *, subject_role, min_observations, limit)` | List profiles with optional filters, ordered by observation_count DESC |

Import added: `PersonalityProfile` from models.

### 4. `src/zetherion_ai/routing/email_router.py` (modified)

**`_persist_signals()` signature change:**
- Added `account_ref: str` parameter (passed from the call site at line 381)

**`_persist_signals()` new behavior** (after existing contact + preference/schedule persistence):

1. **Raw signal log** — Calls `log_personality_signal()` with the full `PersonalitySignal.to_dict()`, author metadata, and email external ID
2. **Profile aggregation** — Fetches existing `PersonalityProfile` (or creates empty one), calls `aggregate_signal_into_profile()`, then upserts the result
3. **Commitments as learnings** — Each `commitments_made` entry stored as a `LearningCategory.FACT` with prefix `[commitment:{email}]`
4. **Expectations as learnings** — Each `expectations_set` entry stored as a `LearningCategory.FACT` with prefix `[expectation:{email}]`
5. **Owner self-enrichment** — When `subject_role == "owner"` and `observation_count >= 3`, calls `_enrich_owner_profile()`

**New method `_enrich_owner_profile(user_id, personality_profile)`:**

Blends aggregated owner personality back into `personal_profile.communication_style` using EMA blend (alpha=0.3):
- `formality_mode` -> `CommunicationStyle.formality` via map: very_formal=1.0, formal=0.8, semi_formal=0.5, casual=0.3, very_casual=0.1
- `avg_sentence_length_mode` -> `CommunicationStyle.verbosity` via map: short=0.25, medium=0.5, long=0.75
- `emoji_rate` -> `CommunicationStyle.emoji_usage` (direct blend)

This means the system gradually learns the owner's writing style from their sent emails and updates their profile accordingly, without harsh overwrites.

### 5. `src/zetherion_ai/personal/context.py` (modified)

**`DecisionContext` dataclass** — Two new fields:
- `owner_personality: dict[str, Any]` — aggregated owner profile dict
- `contact_personalities: list[dict[str, Any]]` — aggregated contact profile dicts

**`to_prompt_fragment()`** — Extended to render:
- Owner style line: `"Owner style: formal formality, direct communication, assertiveness: 0.72"`
- Contact personality lines (max 3): `"Contact bob@example.com: familiarity=0.85, dynamic=peer, obs=7"`

**`is_empty`** — Extended to include the two new fields.

**`DecisionContextBuilder.build()`** — Extended with:
- Owner personality query: `list_personality_profiles(user_id, subject_role="owner", limit=1)`
- Contact personality queries: For each `mentioned_email`, calls `get_personality_profile(user_id, email, "contact")`

---

## Tests Added

### `tests/unit/test_personal_aggregation.py` (new) — 38 tests

| Test class | Tests | What it covers |
|---|---|---|
| `TestFirstSignalInitialization` | 1 | First observation bootstraps distributions from empty |
| `TestDistributionMode` | 2 | Mode tracks most frequent value, changes when new value dominates |
| `TestEMAConvergence` | 4 | Assertiveness, familiarity, trust converge; EMA helper function |
| `TestBooleanRateTracking` | 3 | Greeting rate, emoji rate track correctly; running rate helper |
| `TestStringListDedup` | 5 | Dedup + cap, different styles accumulate, case-insensitive dedup, rapport indicators |
| `TestConfidenceGrowth` | 4 | Growth curve values at 1/5/10/20, never exceeds 0.95, zero case, profile confidence grows |
| `TestPurity` | 1 | Input profile not mutated |
| `TestRolePreservation` | 3 | Owner/contact role preserved, ID preserved |
| `TestSecondaryTraits` | 2 | Secondary trait accumulation, None not accumulated |
| `TestCommitmentsAndExpectations` | 2 | Commitments and expectations accumulate |
| `TestPreferencesAndSchedule` | 2 | Preferences and schedule signals accumulate |
| `TestHelperFunctions` | 4 | mode_of edge cases, running_rate first/second observation |
| `TestTimestamps` | 2 | first_observed stays, last_observed updates |
| `TestResponsivenessSignals` | 2 | Accumulation, empty signal not added |

### `tests/unit/test_routing_email_router.py` (extended) — 5 new tests

| Test | What it covers |
|---|---|
| `test_persist_signals_logs_full_signal` | `log_personality_signal` called with complete signal data |
| `test_persist_signals_aggregates_contact_profile` | get -> aggregate -> upsert cycle for contact |
| `test_persist_signals_aggregates_owner_profile` | author_role=owner path |
| `test_persist_signals_stores_commitments_as_learnings` | commitments + expectations stored as FACT learnings |
| `test_persist_signals_enriches_owner_communication_style` | After 3+ observations, owner communication_style blended |

### `tests/unit/test_personal_context.py` (extended) — 8 new tests

| Test | What it covers |
|---|---|
| `test_build_includes_owner_personality` | Owner profile queried and populated |
| `test_build_includes_contact_personalities` | Contact profiles queried for mentioned emails |
| `test_build_no_personality_profiles_leaves_empty` | Empty when no profiles exist |
| `test_is_empty_false_with_owner_personality` | is_empty respects new field |
| `test_is_empty_false_with_contact_personalities` | is_empty respects new field |
| `test_prompt_fragment_renders_owner_personality` | Owner line rendered correctly |
| `test_prompt_fragment_renders_contact_personalities` | Contact lines rendered correctly |
| `test_prompt_fragment_limits_contact_personalities_to_3` | Max 3 contacts in prompt |

---

## Verification Results

- **5,286 tests passing** (51 new tests added)
- **90.04% coverage** (above 90% threshold)
- **ruff check** — all clean
- **ruff format** — all clean
- **mypy** — all clean

---

## Data Flow Example

When an email from `boss@example.com` is processed:

1. Gemini extracts a `PersonalitySignal` with `author_role=contact`, formality=formal, assertiveness=0.7, etc.
2. `_persist_signals` fires as a background task:
   - Raw signal JSON logged to `personality_signal_log` (audit trail)
   - Existing `PersonalityProfile` fetched (or empty one created)
   - `aggregate_signal_into_profile()` merges the new observation:
     - `formality_distribution["formal"]` incremented from 6 to 7
     - `assertiveness_ema` updated: `0.68 * 0.7 + 0.7 * 0.3 = 0.686`
     - `greeting_styles` list gains "Hello," if not already present
     - `observation_count` goes from 6 to 7
     - `confidence` goes from `0.64` to `0.67`
   - Updated profile upserted to `personality_profiles`
   - Any commitments/expectations stored as learnings

3. Later, when `DecisionContextBuilder.build()` runs:
   - Owner personality profile included in LLM context
   - If `boss@example.com` is in `mentioned_emails`, their contact profile is included
   - LLM sees: `"Contact boss@example.com: familiarity=0.65, dynamic=superior, obs=7"`
