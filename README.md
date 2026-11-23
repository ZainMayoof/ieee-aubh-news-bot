# IEEE AUBH News Bot

Python 3.12 bot that collects public official IEEE vTools opportunities, applies conservative eligibility/date filters, ranks and deduplicates them, and publishes at most one Adaptive Card containing 3-5 opportunities to the existing Microsoft Teams Workflow for **IEEE Board 2026-27**.

## Safety defaults

- `DRY_RUN=true` is the default and never posts or changes published state.
- Sending requires both `--send` and `SEND_TO_TEAMS=true` with `DRY_RUN=false`.
- A webhook success response updates state only after a Teams POST is considered successful.
- If the source is unavailable, fewer than three qualifying events are found, or state cannot be safely read, the bot exits without filling or posting.
- This project never posts to the main **IEEE AUBH Student Branch** chat. The existing Workflow owns the destination; create no second Workflow or duplicate trigger.

## Local setup on Windows PowerShell

Run these commands from the project directory:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
pytest -q
Copy-Item .env.example .env
python main.py --dry-run --state-file state/posted_events.json
```

PowerShell environment variables are set only for the local process. For a real send test:

```powershell
$env:DRY_RUN = "false"
$env:SEND_TO_TEAMS = "true"
$env:TEAMS_WEBHOOK_URL = "https://your-existing-workflow-url..."
python main.py --send --state-file state/posted_events.json
Remove-Item Env:TEAMS_WEBHOOK_URL
Remove-Item Env:SEND_TO_TEAMS
$env:DRY_RUN = "true"
```

Do not commit `.env` or paste the webhook URL into source, logs, screenshots, issues, or pull requests. `.env` is ignored by Git.

## GitHub Actions setup

1. Open **Settings > Secrets and variables > Actions**.
2. Create Actions secret `TEAMS_WEBHOOK_URL` using the URL of the existing Workflow that posts to **IEEE Board 2026-27**.
3. The workflow uses only `contents: write` in addition to the default read permission. The write permission is required for the dedicated `digest-state` branch.
4. Push the repository.
5. Use **Actions > IEEE AUBH Opportunities Digest > Run workflow**. The default dispatch values are dry-run, and the job never posts or modifies state.

The cron schedule is intentionally not enabled until the card payload has been verified in **IEEE Board 2026-27**. Once verified, add `cron: "0 7 * * 0,3"` back under `on:`. That runs Sunday and Wednesday at `07:00 UTC`, which is `10:00 AM Asia/Bahrain` year-round (Bahrain UTC+03). GitHub scheduled runs may be delayed.

## Persisted state and idempotency

The bot uses `state/posted_events.json` for state in the repository. GitHub Actions restores/writes a dedicated `digest-state` branch using `github-actions[bot]`:

- A remote `digest-state` branch is fetched and checked out into `.digest-state` as a detached worktree.
- The script reads and writes `.digest-state/state/posted_events.json`; the main branch is never switched or overwritten.
- After a successful send, the workflow commits only the state file and rebases/pushes to `digest-state`.
- Concurrency is pinned to `ieee-aubh-digest` with `cancel-in-progress: false`, preventing overlapping scheduled and manual runs.
- The workflow fails closed if state is unreadable or a branch cannot be updated safely.

This strategy preserves a durable list of posted IDs across ephemeral runners. It does not preserve state by assuming the runner filesystem survives. The state branch contains IDs only, not cards or secrets.

### Security implications

`contents: write` lets GitHub Actions create/update the dedicated state branch. It does not grant access to Actions secrets beyond what is included in the workflow environment. The repository should remain protected:

- Keep branch protections for `main`; do not allow Actions to push it.
- Restrict repository/Actions secrets to required branches and environments.
- Rotate the webhook bearer URL if it may have been exposed.
- Never include webhook parameters or the full URL in logs.

## Testing Teams delivery

Before enabling the schedule, first run a local or `workflow_dispatch` dry run. To test live delivery safely:

1. Confirm the existing Workflow trigger and destination are **IEEE Board 2026-27**.
2. Temporarily or directly run a manual workflow with:
   - `dry_run=false`
   - `send_to_teams=true`
   - Three or more eligible events must be found.
3. Watch the workflow log for `source_count`, `accepted_count`, rejection counts, `selected_count`, and `published_ids`.
4. Open **IEEE Board 2026-27** and verify exactly one card appears.
5. Open the Power Automate run history for the Workflow and verify its trigger and card-post action both succeeded. An HTTP 2xx from the webhook alone is not delivery proof.
6. If the card payload or action expression is incompatible, adapt the Workflow template or payload; do not disable the post action while claiming success.

Use a dedicated test opportunity only if the existing Workflow and destination are confirmed. Never manually alter the script to target a different chat.

## Enabling the schedule

The scheduled job uses `DRY_RUN=false` and `SEND_TO_TEAMS=true`, but posting still depends on `TEAMS_WEBHOOK_URL` being configured. GitHub scheduled runs can appear late. When real end-to-end live testing is successful:

1. In the repository Actions tab, enable **IEEE AUBH Opportunities Digest** if GitHub disabled it.
2. Confirm `.github/workflows/digest.yml` remains on the default branch.
3. Wait for or manually dispatch one live run to inspect the board chat and Workflow run history.
4. Leave no old webhook URL in secrets, `.env`, logs, or documentation.

## Rotating an exposed webhook

If a webhook URL was exposed in chat, logs, a commit, an issue, or a screenshot:

1. Open the existing Power Automate workflow.
2. Disable or delete the old incoming webhook/trigger, or reset/rotate its URL if the Power Automate UI provides that action.
3. Add or update the trigger URL in the `TEAMS_WEBHOOK_URL` GitHub Actions secret.
4. Run a manual dry run, then a manual live run only after confirming the board destination.
5. Verify successful card delivery and Workflow run history.
6. Only after successful live verification, disable the old Power Automate scheduled digest or trigger that previously sent the digest.

Do not disable the old delivery mechanism before the replacement is proven end-to-end.

## Source and filtering behavior

The source is the public IEEE vTools RSS endpoint:

`https://events.vtools.ieee.org/RST/events/api/public/v8/events/list.rss?span=now~now%2B30.days&published=true&limit=100`

It is the only source enabled in this project. The 100-item limit is not guaranteed and the feed may cover a limited window, so source count is logged every run. A future official source can implement `SourceInterface` in `main.py` without scraping unsupported sites.

Published RSS timestamps are treated only as publication metadata. Event dates are extracted from structured start fields first, then only clearly recognizable dates in title/description. Uncertain or past dates are excluded. HTML is normalized with BeautifulSoup, scripts are stripped, entities are decoded, and repeated boilerplate is removed.

Internal committee meetings, closed/invite-only events, postponed/cancelled events, restricted participation, irrelevant social meetings, expired events, and uncertain dates are rejected conservatively.

## CLI

```text
python main.py --dry-run
python main.py --send
python main.py --dry-run --state-file state/posted_events.json
```

`--send` is never sufficient alone. It also requires `SEND_TO_TEAMS=true`, `DRY_RUN=false`, and a valid `TEAMS_WEBHOOK_URL`.
