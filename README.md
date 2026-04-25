# claude-rally

A Claude skill that pulls work items from [Broadcom Rally](https://www.broadcom.com/products/software/value-stream-management/rally) (Agile Central) via the WSAPI and feeds them to Claude Code so you can triage, fix, and commit against them without leaving your editor.

The skill is read-only. It reaches into Rally using your personal API key, normalizes the response, and lets Claude reason about the ticket — summarizing, drilling into children (tasks, sub-stories, defects in a defect suite), or walking through a batch item-by-item.

## Features

- **Fetch by FormattedID** — `US1234`, `DE99`, `TA42`, `DS7`, `F8`, `I2`, `E1`
- **Navigate the hierarchy** — tasks under a story, defects under a suite, the full tree under a feature
- **Queue view** — list stories/defects filtered by project, owner (`me`), iteration, state
- **Two orchestration modes** — dump context inline (default) or spawn per-task subagents
- **Zero pip installs** — stdlib-only Python script
- **Project-agnostic auth** — API key resolves from env, `.env`, or `~/.rally`; save it once, use it everywhere

## Prerequisites

- Python 3.8+
- A Rally account with access to the projects you care about
- A **Rally ALM WSAPI Read-Only API key** — create one at <https://rally1.rallydev.com/login/accounts/index.html#/keys>

  Pick the "Read Only" scope. This skill never writes to Rally in v1, and the key scope is your guardrail.

## Install

### As a Claude Code skill (recommended)

Clone the repo and drop (or symlink) it into your Claude skills directory:

```bash
git clone https://github.com/judeOtenyo/claude-rally.git ~/.claude/skills/rally
```

Or symlink from anywhere you already keep the repo:

```bash
git clone https://github.com/judeOtenyo/claude-rally.git ~/code/claude-rally
ln -s ~/code/claude-rally ~/.claude/skills/rally
```

Restart Claude Code (or start a new session) and the skill will be picked up automatically — the triggering description covers phrases like "pull US1234", "what's in my Rally queue?", and "fetch the tasks in this story".

### As a Claude.ai skill

1. Clone the repo.
2. Zip the folder: `cd claude-rally && zip -r ../claude-rally.skill . -x '.git/*' '.claude/*'`
3. Upload `claude-rally.skill` via the skill picker in Claude.ai.

Or, if you have the `skill-creator` tooling from Anthropic, use its `package_skill.py` to produce a signed bundle.

## Updating

The skill is just a git working tree, so updates are a `git pull` away. Your config in `~/.rally` (API key, default project, orchestration mode) lives outside the skill directory and survives updates — you won't need to re-auth.

### If you cloned directly into `~/.claude/skills/rally`

```bash
cd ~/.claude/skills/rally
git pull origin main
```

### If you cloned elsewhere and symlinked

```bash
cd ~/code/claude-rally   # wherever you actually cloned it
git pull origin main
```

The symlink picks up the changes automatically — nothing to do on the `~/.claude/skills/rally` side.

### If you uploaded a `.skill` bundle to Claude.ai

Re-clone (or `git pull` your existing clone), re-zip, and re-upload — Claude.ai treats the new upload as a new version:

```bash
cd ~/code/claude-rally
git pull origin main
zip -r ../claude-rally.skill . -x '.git/*' '.claude/*'
```

### Checking what version you're on

There aren't tagged releases yet, but you can always check the commit:

```bash
git -C ~/.claude/skills/rally log -1 --oneline
```

Restart Claude Code (or start a fresh session) after pulling so the updated `SKILL.md` description is reloaded.

## First-run configuration

The first time Claude invokes the skill, it will run `whoami` to check your API key. If no key is found, Claude will ask you for one in the chat and save it to `~/.rally` (chmod 600) via the skill's config command.

The key resolves in this order:

1. `RALLY_API_KEY` environment variable
2. `RALLY_API_KEY` line in a `.env` file in the current working directory
3. `api_key` field in `~/.rally`

If you'd rather set it up manually up-front:

```bash
# one-off, persists to ~/.rally
python3 ~/.claude/skills/rally/scripts/rally.py config set api_key <YOUR_KEY>

# sanity check
python3 ~/.claude/skills/rally/scripts/rally.py whoami
```

You can also set a default project so Claude doesn't have to ask which project you mean:

```bash
python3 ~/.claude/skills/rally/scripts/rally.py projects                                   # list what you can see
python3 ~/.claude/skills/rally/scripts/rally.py config set default_project_name "My Team"
python3 ~/.claude/skills/rally/scripts/rally.py config set default_project_ref <OID_or_ref>
```

## Usage

Once installed, just talk to Claude naturally in any repo:

- *"Pull US7107 for me"* — Claude fetches the story, summarizes it, asks before changing code
- *"What's in my queue in Events Application Team?"* — runs a filtered `list` and shows you the top hits
- *"Walk me through all the tasks in US7107, committing each one separately"* — fetches children, works through them one at a time, commits per task
- *"Fix every defect in DS42"* — drills into the defect suite and iterates
- *"Open F522 and show me the sub-stories"* — expands a portfolio item

The skill's `SKILL.md` tells Claude which command to run for each pattern. You don't have to memorize the CLI — but if you want to, see below.

## CLI reference

All commands emit JSON. Errors emit `{"error": {"code": "...", "message": "..."}}` and exit non-zero.

| Command | What it does |
|---|---|
| `rally.py whoami` | Validates the API key and returns the current Rally user |
| `rally.py config get [key]` | Read `~/.rally` (API key redacted) |
| `rally.py config set <key> <value>` | Persist a config field |
| `rally.py projects` | List projects visible to the key |
| `rally.py get <FID>` | Fetch one artifact. `--full` for all fields |
| `rally.py children <FID>` | Immediate children (Tasks, Defects, Children, UserStories) |
| `rally.py tree <FID> [--depth N]` | Recursive children, default depth 2 |
| `rally.py list --type US --owner me --state In-Progress` | Filtered query. See `--help` for all flags |

FormattedID prefix mapping: `US` user story, `DE` defect, `TA` task, `DS` defect suite, `TC` test case, `F`/`I`/`E` portfolio items (feature/initiative/epic). Some Rally tenants rename the portfolio levels — if an `F####` lookup 404s, ask your admin what your levels are called and tell Claude.

## Configuration reference (`~/.rally`)

```json
{
  "api_key": "_yourkey...",
  "default_project_ref": "https://rally1.rallydev.com/slm/webservice/v2.0/project/123456789",
  "default_project_name": "My Team",
  "orchestration_mode": "inline",
  "base_url": "https://rally1.rallydev.com/slm/webservice/v2.0/"
}
```

- `orchestration_mode`: `"inline"` (default) works the ticket in the current conversation; `"subagent"` spawns one Agent per child task.
- `base_url`: override for on-prem Rally installations.

## Roadmap

v1 is intentionally read-only. Natural next steps:

- Writes: state transitions, comments, attaching PR links (needs Full Access API key)
- More `list` filters: tags, blockers, priority, iteration ranges
- Local caching of fetched items for offline reference
- Workspace-scoped queries for tenants with multiple workspaces

Open an issue if you want something bumped up the list.

## Contributing

PRs welcome. The skill itself is three files you can hack on directly:

- `SKILL.md` — the triggering description and workflow playbook (what Claude sees)
- `scripts/rally.py` — the WSAPI client
- `references/wsapi.md` — detail docs Claude loads on demand

If you're adding a new command, mirror the error-code pattern in `die()` and keep the output shape predictable — Claude consumes the JSON directly.

## License

Apache License 2.0. See [LICENSE](LICENSE).

## Disclaimer

Not affiliated with Broadcom or Anthropic. "Rally" and "Agile Central" are trademarks of Broadcom. Use at your own risk; read the script before you paste your API key into anything.
