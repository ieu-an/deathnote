# Recruitment / onboarding Discord bot

Production-oriented [discord.py](https://github.com/Rapptz/discord.py) **2.x** bot with:

- Welcome DMs and optional **Unverified** role on join  
- **Verification** panel → modal (5 fields) → staff embed with **Accept / Deny / Interview** buttons  
- **Interview** area: either a **thread** under the staff embed (default) or a **private text channel** under `/setinterviewcategory`, then **Claim** and **Conclude interview** (**Accept** / **Reject**) for the assigned recruiter only  
- **SQLite** persistence: `guild_settings`, `applications`, `pending_applications` (legacy), optional `division_routes`  
- **Eastern (America/New_York)** ping window for optional staff role mentions  
- **Background task**: interview stage **timeout** (24h) → ban + status update + optional log  

## Requirements

- Python **3.10+** (uses `zoneinfo`; on Windows, `tzdata` is listed in `requirements.txt`)  

### Discord application intents (required)

The bot requests the **Server Members** privileged intent (`on_member_join`, welcome DM, unverified role). Discord will refuse the connection until this is turned on for your application:

1. Open [Discord Developer Portal](https://discord.com/developers/applications) → select your application.  
2. Go to **Bot** in the left sidebar.  
3. Under **Privileged Gateway Intents**, turn **ON** **Server Members Intent**.  
4. Save if prompted, then restart the bot.  

You do **not** need **Message Content Intent** for this project (commands are slash-only). The PyNaCl / voice warnings are safe to ignore if you are not using voice.

## Install

```bash
cd "path/to/Recruitement Bot"
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

## Run

```powershell
$env:DISCORD_BOT_TOKEN = "<paste-your-bot-token-here>"
python bot.py
```

| Variable | Purpose |
|----------|--------|
| `DISCORD_BOT_TOKEN` | **Required.** Bot token. |
| `DISCORD_GUILD_ID` | Optional: sync slash commands to **one guild** only (faster for testing). |

## Bot permissions (recommended)

Invite with **applications.commands** and at least:

| Permission | Why |
|------------|-----|
| Manage Roles | Accepted / interview / unverified roles |
| Ban Members | Deny + interview timeout |
| Manage Threads | Create interview threads (when not using interview category) |
| Manage Channels | Create private interview text channels under `/setinterviewcategory` |
| Send Messages / Embed Links / Attach Files / Read History | Posts in staff / log / threads |
| Mention Everyone (optional) | If you use ping role mentions |

## First-time server setup

1. Configure `/setstaffchannel` (applications are posted there).  
2. Optional: configure welcome message channel via `/setwelcomemessagechannel` and text via `/welcomemessageset`.  
3. Configure `/setacceptedrole`, `/setinterviewrole`, `/setunverifiedrole` as needed.  
4. Set `/setstaffrole` so non-admin staff can use application buttons and **Claim** (otherwise only **Administrator** / **Manage Server** / **botmod**).  
5. Recommended: `/setinterviewcategory` so each interview opens a **private channel** in that category (applicant + staff/botmod). Omit to keep **threads** under the staff embed.  
6. Optional: `/setlogchannel` for audit embeds (accept, deny, interview, claim, timeout).  
7. Optional: ping window via `/pingtimeset`, `/pingtimeactive`, `/setpingrole`.  
8. Run `/setup_verification` where applicants should start.  

**Staff visibility:** Restrict staff channels in Discord; the bot only posts there.

## Slash commands

| Command | Description |
|---------|-------------|
| `/welcomemessageactive` | Welcome messages on/off. |
| `/welcomemessageset` | Welcome text (`{user}`, `{server}`, `{verification_channel}`). |
| `/setwelcomemessagechannel` | Channel where welcome message is posted. |
| `/pingtimeactive` | Timed staff pings on/off. |
| `/pingtimeset` | Eastern `HH:MM`–`HH:MM`. |
| `/setpingrole` | Role to ping in window. |
| `/setstaffchannel` | Default staff review channel. |
| `/setup_verification` | Post verification embed + button. |
| `/setunverifiedrole` | Role on server join. |
| `/setbotmodrole` | Extra role for **config** slash commands. |
| `/setacceptedrole` | Role on **Accept**. |
| `/setinterviewrole` | Role on **Interview**. |
| `/setinterviewcategory` | Category for interview **text channels** (omit = thread under embed). |
| `/setstaffrole` | Role for **staff buttons** + claim. |
| `/setlogchannel` | Set audit log channel. |
| `/purgeuserdata` | Delete a user’s application/pending rows in the DB (also on member leave). |

**Config commands:** Administrator, Manage Server, or **botmod** (`/setbotmodrole`).  
**Staff buttons / Claim:** Administrator, Manage Server, **staff** (`/setstaffrole`), or **botmod**.

## Database schema

SQLite file: `data/bot.db` (default).

### `guild_settings`

Core config plus: `welcome_channel_id`, `accepted_role_id`, `interview_role_id`, `interview_category_id`, `staff_role_id`, `log_channel_id` (all nullable).

### User data removal

- **`/purgeuserdata`** removes `applications` and `pending_applications` rows for that user in the server, and clears `recruiter_id` on other rows when it pointed at that user.  
- On **member leave**, the bot runs the same purge automatically (non-bot members).

### `applications`

| Column | Purpose |
|--------|--------|
| `status` | `submitted`, `interview`, `accepted`, `denied` |
| `division` | Unused by the bot UI (left empty); legacy column |
| `recruiter_id` | Set when someone claims in the thread |
| `submitted_at`, `interview_started_at` | Unix UTC seconds |
| `staff_message_id`, `staff_channel_id` | Staff card message |
| `thread_id` | Interview thread |
| `answers_json` | Modal answers (JSON) |

### `division_routes`

Optional table (not used by the current verification flow). You may still query or edit it manually if you extend the bot.

### `pending_applications`

Legacy table; rows are no longer created by verification. Purged after 1 hour if any exist.

## Project layout

- `bot.py` — entrypoint, cog loading, persistent views, slash sync  
- `cogs/onboarding.py` — member join  
- `cogs/interview.py` — interview **Claim** + **Conclude interview** (accept/reject) views  
- `cogs/verification.py` — modal, **StaffDecisionView**  
- `cogs/admin.py` — configuration commands  
- `tasks/timeout_tasks.py` — 10-minute interview timeout loop  
- `utils/database.py` — SQLite  
- `utils/checks.py` — admin/botmod + staff checks  
- `utils/time_utils.py` — Eastern ping window  
- `utils/recruitment_embeds.py` — standard application embed + logs  
- `utils/ping_helpers.py` — optional ping suffix  

## Notes

- **Persistent views** (`Start Verification`, staff buttons, **Claim**) are re-registered on every startup; do not change `custom_id` values without a migration plan.  
- If the **staff message** is deleted, buttons stop working for that application.  

## License

Use and modify for your clan server as needed.
