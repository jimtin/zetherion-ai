# Phase 6: Discord Management Skill

**Status**: Planning (Post-5G)
**Dependencies**: Phase 5D (Skills Framework), Phase 5F (Heartbeat Scheduler)
**Created**: 2026-02-06

---

## Overview

A comprehensive Discord server management skill that allows natural language control of Discord servers through the SecureClaw DM interface. Uses a separate bot token for isolation.

---

## Architecture

### Separate Bot Design

The Discord Management skill uses its own bot token, separate from SecureClaw:

```
┌─────────────────┐                    ┌─────────────────────┐
│   SecureClaw    │                    │  Discord Management │
│   (your DM)     │                    │       Bot           │
│                 │                    │                     │
│  DISCORD_TOKEN  │                    │  MGMT_BOT_TOKEN     │
└────────┬────────┘                    └──────────┬──────────┘
         │                                        │
         │  REST API (internal)                   │  Discord API
         │  /skill/discord_management/handle      │  (admin actions)
         ▼                                        ▼
┌─────────────────────────────────────────────────────────────┐
│                    Skills Service (5D)                       │
│                                                             │
│  ┌─────────────────────────────────────────────────────┐   │
│  │              DiscordManagementSkill                  │   │
│  │                                                      │   │
│  │  - Receives commands via handle()                    │   │
│  │  - Executes via Management Bot client                │   │
│  │  - Emits events for cross-posting skills             │   │
│  │  - Stores audit logs in Qdrant                       │   │
│  └─────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────┘
```

**Why separate bot?**
- SecureClaw stays lightweight (no admin perms needed)
- Management bot can be invited to multiple servers you don't own
- Clear permission boundary
- Can be disabled without affecting core SecureClaw

---

## Configuration

### Environment Variables

```bash
# ======================================
# DISCORD MANAGEMENT SKILL (Phase 6)
# ======================================

# Separate bot token for management actions
# Create a new bot in Discord Developer Portal with admin permissions
DISCORD_MGMT_BOT_TOKEN=

# Bot application ID (for slash commands if needed)
DISCORD_MGMT_BOT_ID=

# Servers this bot manages (comma-separated guild IDs)
# The bot must be invited to these servers with appropriate permissions
MANAGED_GUILD_IDS=123456789,987654321

# Default server for commands when not specified
DEFAULT_MANAGED_GUILD_ID=123456789

# ======================================
# AUTO-MODERATION DEFAULTS
# ======================================

# Spam detection: messages per 5 seconds to trigger
AUTOMOD_SPAM_THRESHOLD=5

# Raid detection: joins per 10 seconds to trigger
AUTOMOD_RAID_THRESHOLD=10

# Whitelisted domains for link filtering (comma-separated)
AUTOMOD_LINK_WHITELIST=github.com,docs.google.com,discord.com

# New account restriction: days old to bypass restrictions
AUTOMOD_NEW_ACCOUNT_DAYS=7

# ======================================
# MODERATION DEFAULTS
# ======================================

# Default timeout duration in minutes
DEFAULT_TIMEOUT_MINUTES=10

# Warning escalation thresholds
WARN_TO_TIMEOUT_COUNT=3
WARN_TO_BAN_COUNT=5

# Audit log retention days
AUDIT_LOG_RETENTION_DAYS=90
```

---

## Human Tasks Covered

### Server Setup & Configuration
- [ ] Create and configure server settings (name, icon, banner, description)
- [ ] Set verification level and content filter
- [ ] Configure server boost perks
- [ ] Set up server discovery settings
- [ ] Create and manage server templates
- [ ] Configure vanity URL (if boosted)
- [ ] Set default notification settings
- [ ] Configure AFK channel and timeout

### Channel Management
- [ ] Create text/voice/forum/stage channels
- [ ] Create and organize categories
- [ ] Set channel topics and descriptions
- [ ] Configure channel permissions per role
- [ ] Set slowmode on channels
- [ ] Archive/delete unused channels
- [ ] Pin important messages
- [ ] Set up channel-specific rules
- [ ] Create threads and manage thread settings
- [ ] Configure auto-archive duration for threads

### Role Management
- [ ] Create roles with specific permissions
- [ ] Assign color, icon, and display settings
- [ ] Set role hierarchy (who can manage whom)
- [ ] Assign/remove roles from members
- [ ] Create permission overrides per channel
- [ ] Set up role mentionability
- [ ] Configure which roles appear separately in member list

### Member Management
- [ ] View member list and activity
- [ ] Kick members
- [ ] Ban/unban members
- [ ] Timeout members (temporary mute)
- [ ] Change member nicknames
- [ ] Assign/remove roles from members
- [ ] View member join date and activity
- [ ] Prune inactive members
- [ ] Track member notes (warnings, history)

### Moderation Actions
- [ ] Delete individual messages
- [ ] Bulk delete messages (purge)
- [ ] Review and act on reported content
- [ ] Lock channels during incidents
- [ ] Manage quarantine channels
- [ ] Issue verbal warnings
- [ ] Track warning history per user
- [ ] Escalate repeated offenders
- [ ] Handle appeals

### Auto-Moderation
- [ ] Set up word/phrase blacklists
- [ ] Block spam patterns
- [ ] Detect and remove excessive caps/emoji
- [ ] Filter links (whitelist/blacklist domains)
- [ ] Detect invite links and handle them
- [ ] Prevent mention spam (@everyone abuse)
- [ ] Rate limit message frequency
- [ ] Detect raid patterns (mass joins)
- [ ] Block NSFW content in SFW channels
- [ ] Detect scam/phishing attempts

### Welcome & Onboarding
- [ ] Set up welcome channel and messages
- [ ] Configure member screening questions
- [ ] Assign default roles on join
- [ ] Create rules channel and agreement flow
- [ ] Set up introduction channel prompts
- [ ] Guide new members through server structure
- [ ] Create FAQ/help resources

### Announcements & Communication
- [ ] Post announcements to specific channels
- [ ] Schedule announcements for future times
- [ ] Create recurring announcements
- [ ] Mention specific roles for updates
- [ ] Cross-post to announcement channels
- [ ] Create and manage polls
- [ ] Pin important updates

### Event Management
- [ ] Create scheduled events
- [ ] Set event details (time, location, description)
- [ ] Send event reminders
- [ ] Track RSVPs/interested members
- [ ] Follow up after events
- [ ] Manage stage channel events

### Content Curation
- [ ] Pin valuable messages
- [ ] Create and update resource threads
- [ ] Maintain FAQ content
- [ ] Archive old but valuable content
- [ ] Highlight community contributions
- [ ] Manage media/meme channels

### Analytics & Reporting
- [ ] Track member growth over time
- [ ] Monitor active vs inactive members
- [ ] Identify most active channels
- [ ] Track peak activity times
- [ ] Monitor message volume trends
- [ ] Identify top contributors
- [ ] Track moderation action frequency
- [ ] Report on server health metrics

### Integration Management
- [ ] Set up webhooks
- [ ] Configure bot permissions
- [ ] Manage connected apps
- [ ] Set up Twitch/YouTube notifications
- [ ] Configure social media feeds

### Community Building
- [ ] Identify and promote active members
- [ ] Recognize contributions (special roles)
- [ ] Mediate conflicts between members
- [ ] Foster positive culture
- [ ] Respond to feedback and suggestions
- [ ] Run community events/competitions

### Security & Safety
- [ ] Audit permissions regularly
- [ ] Review bot access levels
- [ ] Monitor for compromised accounts
- [ ] Handle doxxing/harassment incidents
- [ ] Manage verification requirements
- [ ] Review audit logs for suspicious activity
- [ ] Respond to Discord Trust & Safety reports

### Backup & Recovery
- [ ] Document server structure
- [ ] Export important data
- [ ] Plan for disaster recovery
- [ ] Maintain role/permission documentation

---

## Implementation Phases

### Phase 6A: Foundation + Core Moderation

**Scope:**
- Management bot setup and connection
- Multi-server support
- Permission verification on startup
- Basic member moderation (kick, ban, timeout)
- Warning system with history
- Message moderation (delete, purge)
- Basic audit logging

**Events Emitted:**
- `member_kicked`
- `member_banned`
- `member_timed_out`
- `warning_issued`
- `messages_deleted`

**Storage:**
- `skill_discord_audit` collection (Qdrant)
- `skill_discord_warnings` collection (Qdrant)

### Phase 6B: Channel & Role Management

**Scope:**
- Channel CRUD operations
- Category management
- Role CRUD operations
- Permission management
- Reaction roles setup
- Channel cloning and templates

**Events Emitted:**
- `channel_created`
- `channel_deleted`
- `channel_updated`
- `role_created`
- `role_assigned`
- `permissions_updated`

**Storage:**
- `skill_discord_config` collection (per-server settings)

### Phase 6C: Auto-Moderation Engine

**Scope:**
- Content filtering (word blacklist, regex)
- Spam detection and prevention
- Link filtering (whitelist/blacklist)
- Raid protection
- New account restrictions
- Scam/phishing detection
- Configurable rule sets per server

**Events Emitted:**
- `automod_triggered`
- `spam_detected`
- `raid_detected`
- `scam_blocked`

**Storage:**
- `skill_discord_automod_rules` collection
- `skill_discord_automod_log` collection

### Phase 6D: Welcome, Announcements & Scheduling

**Scope:**
- Welcome message configuration
- Auto-role on join
- Member screening integration
- Announcement posting
- Scheduled announcements
- Recurring announcements
- Discord event creation
- Event reminders

**Events Emitted:**
- `member_welcomed`
- `announcement_posted`
- `announcement_scheduled`
- `event_created`
- `event_reminder`
- `event_started`
- `event_ended`

**Storage:**
- `skill_discord_welcome_config` collection
- `skill_discord_scheduled` collection

### Phase 6E: Analytics, Audit & Reporting

**Scope:**
- Member growth tracking
- Activity metrics
- Channel analytics
- Moderation statistics
- Daily/weekly reports
- Anomaly detection
- Exportable data

**Events Emitted:**
- `daily_report_generated`
- `weekly_report_generated`
- `anomaly_detected`
- `member_milestone` (100, 500, 1000, etc.)
- `server_milestone`

**Storage:**
- `skill_discord_analytics` collection
- `skill_discord_metrics` (SQLite for time-series)

---

## Event Bus Integration

The Discord Management skill emits events that other skills can subscribe to. This enables cross-posting and multi-platform coordination.

### Event Types

| Event | Payload | Consumers |
|-------|---------|-----------|
| `event_created` | guild_id, event_id, title, description, start_time, end_time, channel_id | Twitter, Slack, Calendar, Email |
| `event_reminder` | guild_id, event_id, title, minutes_until | Twitter, Slack, Email |
| `event_started` | guild_id, event_id, title, channel_id, invite_link | Twitter, Slack |
| `event_ended` | guild_id, event_id, title, attendee_count | Twitter, Analytics |
| `announcement_posted` | guild_id, channel_id, content, mentions, attachments | Twitter, Slack, Email |
| `member_milestone` | guild_id, milestone_type, count, member (optional) | Twitter |
| `server_milestone` | guild_id, milestone_type, value | Twitter |
| `member_welcomed` | guild_id, member_id, member_name | Analytics |
| `automod_triggered` | guild_id, rule_type, member_id, action_taken | Analytics, Alerts |
| `raid_detected` | guild_id, join_count, time_window, action_taken | Alerts, Analytics |

### Cross-Posting Example Flow

```
1. You: "Create an event for Friday 3pm - Community Game Night"
2. Discord Management Skill:
   - Creates Discord scheduled event
   - Emits `event_created` with details
3. Event Bus routes to subscribers:
   - Twitter Skill receives → Tweets "Join us Friday 3pm for Community Game Night!"
   - Calendar Skill receives → Creates Google Calendar event
   - Slack Skill receives → Posts to #announcements channel
4. On Friday at 2:45pm, Heartbeat triggers `event_reminder`
   - Twitter Skill → Tweets "Starting in 15 minutes!"
   - Email Skill → Sends reminder to mailing list
```

### Adding a Cross-Post Skill

To add cross-posting to a new platform after Phase 6D:

1. Create the new skill implementing the Skill interface
2. Declare subscription to relevant events in metadata
3. Implement event handlers for each subscribed event
4. Configure platform credentials
5. Enable the skill

**No changes needed to Discord Management skill.**

---

## Skill Metadata

```
name: discord_management
description: Manage Discord servers via natural language
version: 1.0.0
permissions:
  - READ_PROFILE
  - WRITE_MEMORIES
  - SEND_MESSAGES
  - DISCORD_ADMIN (new permission type for management bot)
collections:
  - skill_discord_audit
  - skill_discord_warnings
  - skill_discord_config
  - skill_discord_automod_rules
  - skill_discord_automod_log
  - skill_discord_welcome_config
  - skill_discord_scheduled
  - skill_discord_analytics
events_emitted:
  - event_created
  - event_reminder
  - event_started
  - event_ended
  - announcement_posted
  - announcement_scheduled
  - member_milestone
  - server_milestone
  - member_welcomed
  - member_kicked
  - member_banned
  - member_timed_out
  - warning_issued
  - messages_deleted
  - channel_created
  - channel_deleted
  - role_created
  - role_assigned
  - automod_triggered
  - raid_detected
  - daily_report_generated
```

---

## Example Interactions

### Moderation
```
You: "Timeout @spammer for 1 hour, spamming links"
Bot: "Done. @spammer timed out for 1 hour in DevServer. Reason: spamming links.
      This is their 2nd warning."

You: "Show warnings for @spammer"
Bot: "Warnings for @spammer in DevServer:
      1. 2026-02-01: Spamming links (timeout 30m)
      2. 2026-02-06: Spamming links (timeout 1h)
      Next violation: 24h timeout"

You: "Purge last 50 messages from @spammer in #general"
Bot: "Deleted 47 messages from @spammer in #general (3 were older than 14 days)."
```

### Channel Management
```
You: "Create a voice channel called Team Meeting under the Meetings category"
Bot: "Created voice channel #Team Meeting in Meetings category."

You: "Set slowmode to 30 seconds in #general"
Bot: "Slowmode set to 30s in #general."

You: "Archive #old-project and restrict to read-only for everyone except admins"
Bot: "Archived #old-project. Now read-only for all except Admin role."
```

### Events & Announcements
```
You: "Create an event for Saturday 2pm - Weekly Gaming Session, in the Gaming voice channel"
Bot: "Created event 'Weekly Gaming Session' for Sat Feb 8 at 2:00 PM in Gaming.
      Cross-posted to: Twitter, #announcements"

You: "Schedule an announcement for Monday 9am: Don't forget to submit your weekly reports!"
Bot: "Scheduled for Mon Feb 10 at 9:00 AM in #announcements.
      Will mention @everyone."

You: "Set up a recurring announcement every Friday at 5pm: Have a great weekend!"
Bot: "Created recurring announcement every Friday at 5:00 PM in #general."
```

### Analytics
```
You: "How's the server doing this week?"
Bot: "DevServer Weekly Summary (Feb 1-7):
      - Members: 1,247 (+23 this week)
      - Messages: 8,432 (+12% vs last week)
      - Most active: #general (2,100 msgs), #dev-help (1,800 msgs)
      - Peak hours: 7-9 PM EST
      - Moderation: 3 timeouts, 0 bans
      - Top contributors: @alice (340 msgs), @bob (280 msgs)"

You: "Who joined in the last 24 hours?"
Bot: "12 new members in DevServer (last 24h):
      - @alice (2h ago) - no roles yet
      - @bob (5h ago) - assigned Member
      ..."
```

---

## Future Considerations

### Potential Extensions
- Voice channel moderation (disconnect, move, mute)
- Stage channel management
- Forum channel moderation
- Thread management at scale
- Server template export/import
- Bot command aliases
- Custom auto-mod rules via natural language
- Integration with Discord's native AutoMod

### Platform Skills That Could Subscribe
- Twitter (cross-post events, milestones)
- Slack (mirror announcements)
- Email (event reminders, reports)
- Calendar (sync events)
- Notion/Confluence (sync documentation)
- Analytics dashboards (metrics export)
