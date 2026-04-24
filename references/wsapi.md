# Rally WSAPI reference

Only read this when you hit something the main SKILL.md doesn't cover — weird query syntax, on-prem quirks, unusual artifact types, or the script returns a `rally_query_error` you don't understand.

## Base URL

- SaaS: `https://rally1.rallydev.com/slm/webservice/v2.0/`
- On-prem: customer-specific, usually `https://<host>/slm/webservice/v2.0/`. Override via `scripts/rally.py config set base_url <URL>`.

The API version is `v2.0`. Earlier versions (`1.x`) are retired and will 404.

## Authentication

Single header:

```
zsessionid: <api-key-verbatim>
```

Rally-issued API keys are opaque strings (often starting with an underscore, e.g. `_abc123…`). The underscore is **part of the key**, not a prefix to add. Don't modify the key — paste it verbatim into the header.

API keys carry the permissions of the user who created them, scoped to either "Read Only" or "Full Access" at creation time. v1 of this skill assumes read-only.

Invalid keys return HTTP 401 with a body like:
```json
{"OperationResult": {"Errors": ["Not authorized to perform action: Invalid key"]}}
```

## Artifact types in URL paths

| FormattedID prefix | Type | URL segment |
|---|---|---|
| `US` | User Story | `hierarchicalrequirement` |
| `DE` | Defect | `defect` |
| `TA` | Task | `task` |
| `DS` | Defect Suite | `defectsuite` |
| `TC` | Test Case | `testcase` |
| `TS` | Test Set | `testset` |
| `F` | Portfolio Item: Feature | `portfolioitem/feature` |
| `I` | Portfolio Item: Initiative | `portfolioitem/initiative` |
| `E` | Portfolio Item: Epic | `portfolioitem/epic` |

**Portfolio Items are customer-configurable.** Some orgs rename levels (Theme/Initiative/Feature, or Epic/Capability/Feature). If `portfolioitem/feature` 404s, ask the user what their levels are and try `portfolioitem/<level-name>` in lowercase.

Generic cross-type endpoint: `/artifact` queries across all work-item types but supports fewer filters.

## Query parameter syntax

Rally Query Language (RQL) basics:

- Every term must be wrapped in parens: `(FormattedID = "US1234")`
- Compound with AND/OR, also parenthesized: `((State = "Open") AND (Priority = "High"))`
- Strings in double quotes: `(Name contains "login")`
- Numbers bare: `(Owner.ObjectID = 12345)`
- Dates ISO-8601 with quotes: `(LastUpdateDate > "2025-01-01")`
- Booleans: `(Blocked = true)` (lowercase, no quotes)
- Null checks: `(Owner = null)` / `(Iteration != null)`

Operators: `=`, `!=`, `>`, `<`, `>=`, `<=`, `contains`, `!contains`.

**Dotted-field traversal** is how you filter by related objects:
```
(Owner.UserName = "alice@example.com")
(Iteration.Name = "Sprint 42")
(Project.Name = "Foo Platform")
(Parent.FormattedID = "US100")
```

## Useful query parameters

| Param | What it does | Notes |
|---|---|---|
| `query` | RQL filter (URL-encoded) | Omit to list everything the key can see — usually too broad |
| `fetch` | `true` for all fields, or comma-list like `Name,FormattedID,Owner` | Defaults to a minimal set; always pass explicitly |
| `pagesize` | 1–2000, default 20 | Use 200 as a reasonable max per page |
| `start` | 1-indexed offset for pagination | `start=1` is the first page |
| `order` | e.g. `FormattedID` or `LastUpdateDate DESC` | Multi-key comma-separated |
| `project` | Project OID or full ref to scope the query | If omitted, the query hits all projects visible to the key |
| `projectScopeUp` | `true`/`false` | Include parent projects; default true |
| `projectScopeDown` | `true`/`false` | Include child projects; default true |
| `workspace` | Workspace OID or ref | Needed if the key can see multiple workspaces |

## Response shape

Single-object or collection GETs are wrapped in a `QueryResult`:

```json
{
  "QueryResult": {
    "Errors": [],
    "Warnings": [],
    "TotalResultCount": 42,
    "StartIndex": 1,
    "PageSize": 200,
    "Results": [
      {
        "_ref": "https://rally1.rallydev.com/slm/webservice/v2.0/hierarchicalrequirement/12345",
        "_refObjectUUID": "…",
        "_refObjectName": "Add login rate limiting",
        "FormattedID": "US1234",
        "Name": "Add login rate limiting",
        "ScheduleState": "In-Progress",
        "Owner": {
          "_ref": "…/user/777",
          "_refObjectName": "Alice Example"
        },
        "Tasks": {
          "_ref": "…/hierarchicalrequirement/12345/Tasks",
          "Count": 3
        }
      }
    ]
  }
}
```

Collection fields (`Tasks`, `Defects`, `Children`) are references with a `Count`, not the objects themselves. GET the `_ref` URL (with `?fetch=true&pagesize=200`) to expand.

Single-artifact GETs by ObjectID (e.g. `/defect/999`) return `{"Defect": {...}}` instead of `QueryResult` — the shape differs. The script always queries by FormattedID to keep it uniform.

## Common fields worth fetching

- Identity: `FormattedID`, `Name`, `ObjectID`, `_ref`
- Status: `State` (defects), `ScheduleState` (stories/tasks), `Ready`, `Blocked`, `BlockedReason`
- Content: `Description`, `Notes` — **HTML**, strip tags before quoting
- Ownership: `Owner`, `SubmittedBy` (defects), `Project`
- Scheduling: `Iteration`, `Release`, `PlanEstimate`, `TaskEstimateTotal`, `TaskRemainingTotal`
- Priority/severity: `Priority`, `Severity` (defects only)
- Hierarchy: `Parent`, `PortfolioItem`, `WorkProduct` (tasks → parent story/defect), `Requirement` (test cases), `Tasks`, `Defects`, `Children`, `UserStories`
- Metadata: `CreationDate`, `LastUpdateDate`, `Tags`

## Pagination

If `TotalResultCount > PageSize`, keep paging by incrementing `start` by `pagesize` until `start > TotalResultCount`. The script's `--start` and `--pagesize` let you do this from the outside, but for anything above a few hundred items, add a loop or push the user toward a narrower query.

## Rate limits & etiquette

Rally doesn't publish hard rate limits but will slow or 503 under heavy load. For bulk work:
- Keep `pagesize` at 200
- Don't hammer `tree` with deep recursion on large portfolio items
- If you get a 503, back off — don't retry tightly

## Known quirks

- `Description`/`Notes` are stored as HTML. They often contain pasted Word/Office markup that's messy. Strip tags and collapse whitespace before showing the user.
- `ScheduleState` transitions — "Defined → In-Progress → Completed → Accepted" — are enforced; you can't skip states via the API.
- Custom fields live under `c_<FieldName>`. Fetch them explicitly: `fetch=c_MyField,Name,FormattedID`.
- Some workspaces require an explicit `workspace` param even though the key only sees one. If you get "Workspace not specified" errors, stash the workspace OID in `~/.rally` and pass it via `--workspace` (not yet supported by the script — would need extending).
- `Owner = null` for unassigned items, not an empty dict.

## Useful links

- API key management: https://rally1.rallydev.com/login/accounts/index.html#/keys
- WSAPI schema browser (once logged in): https://rally1.rallydev.com/slm/doc/webservice/
- Broadcom TechDocs (official): https://techdocs.broadcom.com/us/en/ca-enterprise-software/valueops/rally/rally-help/reference/rally-web-services-api.html
- cURL examples: https://knowledge.broadcom.com/external/article/57528/rally-use-api-key-with-curl.html
