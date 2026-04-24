---
name: rally
description: Fetch Broadcom Rally (Agile Central) work items — user stories, defects, tasks, defect suites, features — via the WSAPI using the user's personal API key, then feed them into a Claude Code session to work on. Use this skill whenever the user mentions Rally, a Rally FormattedID (e.g. US12345, DE678, TA42, F99, DS7), Rally projects, iterations, or phrases like "pull this ticket", "fetch my stories", "fix this defect", "work through the tasks in this user story", or any similar request to turn Rally work items into code changes. Also use it when the user wants to set up or configure Rally access, even if no specific ticket is mentioned yet.
---

# Rally skill

Turn Rally work items into actionable context for Claude Code. The skill reads items through the Rally WSAPI (read-only for now), normalizes them into JSON, and then either drops that context into the current conversation (default) or orchestrates per-task subagent runs so Claude Code can work through a batch of items one by one.

## When to use this skill

Invoke this skill when the user:
- Mentions a Rally FormattedID: `US####`, `DE####`, `TA####`, `DS####`, `TC####`, `F####`, `I####`, `E####`
- Asks to "pull", "fetch", "load", "grab", "look at" a ticket, story, defect, task, or feature and the context sounds like Rally (not Jira/Linear/GitHub Issues)
- Wants to work through a batch ("all tasks in US1234", "every defect in DS42", "the sub-stories of this feature")
- Asks to configure Rally auth, set a default project, or switch orchestration modes
- References Rally concepts: iteration, release, portfolio item, defect suite, Agile Central

If the user is ambiguous about the tracker, ask once — don't assume Rally.

## Configuration

Config lives in `~/.rally` (JSON, chmod 600). The API key resolves in order:
1. `$RALLY_API_KEY` environment variable
2. `RALLY_API_KEY` in a `.env` file in the current working directory
3. `api_key` field in `~/.rally`

Other `~/.rally` keys:
- `default_project_ref` — full WSAPI URL or numeric OID of the default project for `list`
- `default_project_name` — human-readable label (for display only)
- `orchestration_mode` — `"inline"` (default) or `"subagent"`
- `base_url` — override for on-prem Rally (default `https://rally1.rallydev.com/slm/webservice/v2.0/`)

### First-run auth flow

1. Run `scripts/rally.py whoami`. If it exits 0, auth is good — move on.
2. If it returns `{"error": {"code": "missing_api_key", ...}}`, ask the user:
   > I need a Rally **ALM WSAPI read-only** API key. You can create one at https://rally1.rallydev.com/login/accounts/index.html#/keys (pick the "Read Only" scope — this skill doesn't write anything yet). Paste the key here and I'll save it to `~/.rally`.
3. When they paste it, run `scripts/rally.py config set api_key <KEY>`, then re-run `whoami` to confirm.
4. If `whoami` returns `auth_failed` or an `http_error` with 401, the key is invalid — tell the user, ask for a new one.

Only prompt the user for a key when the script actually fails. Don't pre-flight on every invocation.

### Setting a default project

After auth works, if there's no `default_project_ref`, offer to set one:
1. Run `scripts/rally.py projects` to list projects.
2. Show the user the names (redact the ObjectIDs unless they ask) and ask which one they want as the default.
3. Save with `config set default_project_ref <OID-or-ref>` and `config set default_project_name "<Name>"`.

Skip this if the user's request already names a specific project.

## Commands

All commands emit JSON on stdout. Errors emit `{"error": {"code": "...", "message": "...", ...}}` and exit non-zero.

| Command | What it does |
|---|---|
| `scripts/rally.py whoami` | Validates the API key and returns the current Rally user |
| `scripts/rally.py config get [key]` | Reads `~/.rally` (api_key redacted) |
| `scripts/rally.py config set <key> <value>` | Persists a config field |
| `scripts/rally.py projects` | Lists projects the user has access to |
| `scripts/rally.py get <FID>` | Fetches one artifact (US/DE/TA/DS/TC/F/I/E); `--full` for all fields |
| `scripts/rally.py children <FID>` | Immediate children — Tasks, Defects, Children, UserStories |
| `scripts/rally.py tree <FID> [--depth N]` | Recursive children, default depth 2 |
| `scripts/rally.py list --type US --project "Foo" --owner me --state In-Progress` | Query with filters |

Artifact type prefixes: `US` user story, `DE` defect, `TA` task, `DS` defect suite, `TC` test case, `F`/`I`/`E` portfolio items (feature / initiative / epic). The script picks the right endpoint from the prefix.

## Workflows

The user's intent usually matches one of these patterns. Pick the one that fits, then execute.

### 1. Single item — "fix DE1234"

```bash
scripts/rally.py get DE1234
```
Summarize the item for the user (one-line header + description preview + owner/state/iteration), then ask "ready to dig in?" before making any code changes. The Description field is often HTML — strip tags when you quote it back to the user.

### 2. Item + its children — "work through all the tasks in US500"

```bash
scripts/rally.py children US500
```
Present a numbered checklist of the children. Then, depending on `orchestration_mode`:

- **inline** (default): tackle each child in the current session, one at a time. After each, ask whether to commit before moving on. The user wanted "commit each task individually" by default, so make separate commits per task unless they say otherwise.
- **subagent**: spawn one Agent per child via the Agent tool with `subagent_type: general-purpose`, passing the task context and the repo root. Do this only if the user asked for it or `orchestration_mode` is set to `"subagent"` — launching agents is higher-cost than inline work.

### 3. Full hierarchy — "do everything under F42"

```bash
scripts/rally.py tree F42 --depth 3
```
This returns a nested structure. Summarize the tree first, get the user to confirm the scope, then iterate through leaves. Features can span dozens of stories — always show the count and let the user narrow down before starting.

### 4. Queue view — "what's assigned to me in the Foo project?"

```bash
scripts/rally.py list --type US --project "Foo" --owner me --state In-Progress
```
List the FormattedIDs + Names and ask which one to pull into detail. Don't auto-select.

### 5. Defect suite drill-down — "fix every defect in DS7"

```bash
scripts/rally.py children DS7
```
The `children.Defects` bucket has the full list. Confirm scope, then iterate.

## Hand-off to Claude Code

Once an item is loaded, the useful context to extract and reason from is:

- `FormattedID` + `Name` — use in commit messages and PR titles
- `Description` / `Notes` — acceptance criteria, repro steps (HTML; strip or render as markdown)
- `State` / `ScheduleState` — don't start work on Accepted/Closed items without confirming
- `Owner` — sanity check it's the current user (or that the user explicitly wants to work someone else's queue)
- `Iteration` / `Release` — useful for commit/PR context
- `Blocked` / `BlockedReason` — if true, surface this before starting

For commit messages, a reasonable default format is:

```
<type>(<scope>): <Name>

Rally: <FormattedID>
```

…but match the repo's existing commit style (check `git log` first).

## Error handling

The script's structured errors (`{"error": {"code": "..."}}`) tell you what happened:

- `missing_api_key` — run the first-run auth flow
- `auth_failed` / `http_error` with 401 — invalidated key, prompt for a new one
- `http_error` with 403 — the key is valid but lacks access to the project/artifact; tell the user
- `not_found` — the FormattedID doesn't exist or isn't visible (could be a typo or a project access issue)
- `project_ambiguous` — multiple projects share the name; show the user the matches and ask
- `rally_query_error` — the WSAPI rejected the query; usually a syntax issue on our end, report it

## Growing the skill

v1 is read-only on purpose. Natural next steps when the user asks:
- Writes: status transitions, adding comments, attaching a PR link (needs the Full Access key, not the read-only one — prompt accordingly)
- Bulk filters (tag, blocker, priority) — extend `list` flags
- Caching the last-fetched item as markdown in `./.rally/cache/` for offline reference
- On-prem Rally support — `base_url` is already configurable

See `references/wsapi.md` for Rally Query Language details, field reference, and edge cases you'll hit once you're past the happy path.
