#!/usr/bin/env python3
"""Rally WSAPI client. Stdlib-only. Emits JSON on stdout for machine consumption.

Config resolution for the API key (first hit wins):
    1. $RALLY_API_KEY environment variable
    2. .env file in the current working directory (KEY=VALUE lines)
    3. ~/.rally config file (JSON)

Write the key back to ~/.rally with `config set api_key <KEY>` so it persists
across projects. Other config (default_project, orchestration_mode, base_url)
also lives in ~/.rally.

The script is non-interactive: commands that need a key will exit non-zero with
a structured error if one can't be found, so Claude can prompt the user in the
conversation and save the answer via `config set`.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

CONFIG_PATH = Path.home() / ".rally"
DEFAULT_BASE_URL = "https://rally1.rallydev.com/slm/webservice/v2.0/"
DEFAULT_PAGESIZE = 200

TYPE_BY_PREFIX = {
    "US": "hierarchicalrequirement",
    "DE": "defect",
    "TA": "task",
    "DS": "defectsuite",
    "TC": "testcase",
    "TS": "testset",
    "F":  "portfolioitem/feature",
    "I":  "portfolioitem/initiative",
    "E":  "portfolioitem/epic",
}

COMMON_FIELDS = [
    "FormattedID", "Name", "State", "ScheduleState", "Description", "Notes",
    "Owner", "Project", "Iteration", "Release", "Parent", "PortfolioItem",
    "WorkProduct", "Requirement", "TestCase", "Tasks", "Defects", "Children",
    "Priority", "Severity", "Blocked", "BlockedReason", "Ready",
    "PlanEstimate", "TaskEstimateTotal", "TaskRemainingTotal", "Tags",
    "CreationDate", "LastUpdateDate", "ObjectID", "_ref", "_refObjectName",
]


def die(code: str, message: str, **extra: Any) -> None:
    payload = {"error": {"code": code, "message": message, **extra}}
    print(json.dumps(payload, indent=2))
    sys.exit(1)


def load_config() -> dict:
    if not CONFIG_PATH.exists():
        return {}
    try:
        return json.loads(CONFIG_PATH.read_text())
    except json.JSONDecodeError as e:
        die("config_parse_error", f"~/.rally is not valid JSON: {e}")
    return {}


def save_config(cfg: dict) -> None:
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2, sort_keys=True))
    os.chmod(CONFIG_PATH, 0o600)


def load_dotenv_key() -> str | None:
    env_path = Path.cwd() / ".env"
    if not env_path.exists():
        return None
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        if k.strip() == "RALLY_API_KEY":
            return v.strip().strip('"').strip("'")
    return None


def resolve_api_key(cfg: dict) -> str | None:
    return os.environ.get("RALLY_API_KEY") or load_dotenv_key() or cfg.get("api_key")


def base_url(cfg: dict) -> str:
    return cfg.get("base_url", DEFAULT_BASE_URL).rstrip("/") + "/"


def http_get(url: str, api_key: str) -> dict:
    req = urllib.request.Request(url, headers={"zsessionid": api_key, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        die("http_error", f"HTTP {e.code} from Rally", url=url, body=body[:2000])
    except urllib.error.URLError as e:
        die("network_error", f"Network error: {e.reason}", url=url)
    except json.JSONDecodeError as e:
        die("bad_response", f"Rally returned non-JSON: {e}", url=url)
    return {}


def require_key(cfg: dict) -> str:
    key = resolve_api_key(cfg)
    if not key:
        die(
            "missing_api_key",
            "No Rally API key found. Check $RALLY_API_KEY, ./.env, or ~/.rally.",
            how_to_fix="Ask the user for their Rally ALM WSAPI read-only key, then run: "
                       "scripts/rally.py config set api_key <KEY>",
            get_key_url="https://rally1.rallydev.com/login/accounts/index.html#/keys",
        )
    return key


def query_url(cfg: dict, artifact: str, query: str | None = None,
              fetch: str = "true", pagesize: int = DEFAULT_PAGESIZE,
              start: int = 1, order: str | None = None,
              project: str | None = None) -> str:
    params: dict[str, str] = {
        "fetch": fetch,
        "pagesize": str(pagesize),
        "start": str(start),
    }
    if query:
        params["query"] = query
    if order:
        params["order"] = order
    if project:
        params["project"] = project
    return f"{base_url(cfg)}{artifact}?{urllib.parse.urlencode(params)}"


def unwrap_query(response: dict) -> dict:
    qr = response.get("QueryResult")
    if qr and "Errors" in qr and qr["Errors"]:
        die("rally_query_error", "; ".join(qr["Errors"]), warnings=qr.get("Warnings", []))
    return qr or response


def artifact_type_for_formatted_id(fid: str) -> str:
    fid = fid.strip().upper()
    for prefix in sorted(TYPE_BY_PREFIX, key=len, reverse=True):
        if fid.startswith(prefix):
            return TYPE_BY_PREFIX[prefix]
    die("unknown_formatted_id", f"Don't know the artifact type for '{fid}'",
        known_prefixes=sorted(TYPE_BY_PREFIX))
    return ""


def _shrink_relation(v: dict) -> dict:
    out = {"Name": v.get("_refObjectName") or v.get("Name"), "_ref": v.get("_ref")}
    if v.get("FormattedID"):
        out["FormattedID"] = v["FormattedID"]
    return out


def _shrink_collection(v: dict) -> dict:
    return {"Count": v.get("Count", 0), "_ref": v.get("_ref")}


def _shrink_tags(v: dict) -> list[str]:
    names = v.get("_tagsNameArray") or []
    return [t.get("Name") for t in names if t.get("Name")]


def slim(obj: dict, fields: list[str] | None = None) -> dict:
    if obj is None:
        return obj
    fields = fields or COMMON_FIELDS
    out = {k: obj.get(k) for k in fields if k in obj}
    for k in ("Owner", "Project", "Iteration", "Release", "Parent", "PortfolioItem", "WorkProduct",
              "Requirement", "DefaultProject"):
        v = out.get(k)
        if isinstance(v, dict):
            out[k] = _shrink_relation(v)
    for k in ("Tasks", "Defects", "Children", "UserStories", "TestCase"):
        v = out.get(k)
        if isinstance(v, dict):
            out[k] = _shrink_collection(v)
    if isinstance(out.get("Tags"), dict):
        out["Tags"] = _shrink_tags(out["Tags"])
    return out


def cmd_config(args: argparse.Namespace) -> None:
    cfg = load_config()
    if args.action == "get":
        if args.key:
            val = cfg.get(args.key)
            print(json.dumps({args.key: "***" if args.key == "api_key" and val else val}, indent=2))
        else:
            redacted = {**cfg, **({"api_key": "***"} if cfg.get("api_key") else {})}
            print(json.dumps(redacted, indent=2))
    elif args.action == "set":
        if not args.key or args.value is None:
            die("bad_args", "Usage: config set <key> <value>")
        cfg[args.key] = args.value
        save_config(cfg)
        print(json.dumps({"ok": True, "saved": args.key, "path": str(CONFIG_PATH)}, indent=2))
    elif args.action == "unset":
        cfg.pop(args.key, None)
        save_config(cfg)
        print(json.dumps({"ok": True, "removed": args.key}, indent=2))


def get_me(cfg: dict, key: str) -> dict:
    """Return the authenticated user. GET /user (no query) returns {"User": {...}}."""
    data = http_get(f"{base_url(cfg)}user?fetch=UserName,EmailAddress,DisplayName,ObjectID,DefaultProject", key)
    user = data.get("User")
    if not user:
        die("auth_failed", "Rally returned no user; the API key may be invalid or lack permission.")
    return user


def cmd_whoami(args: argparse.Namespace) -> None:
    cfg = load_config()
    key = require_key(cfg)
    user = get_me(cfg, key)
    print(json.dumps({"ok": True, "user": slim(user,
          ["UserName", "EmailAddress", "DisplayName", "ObjectID", "DefaultProject", "_ref", "_refObjectName"])}, indent=2))


def cmd_projects(args: argparse.Namespace) -> None:
    cfg = load_config()
    key = require_key(cfg)
    url = query_url(cfg, "project",
                    fetch="Name,ObjectID,State,Description,Parent",
                    pagesize=args.pagesize, start=args.start,
                    order="Name")
    data = http_get(url, key)
    qr = unwrap_query(data)
    items = [slim(r, ["Name", "ObjectID", "State", "Parent", "_ref"]) for r in qr.get("Results", [])]
    print(json.dumps({"total": qr.get("TotalResultCount"), "projects": items}, indent=2))


def resolve_project_ref(cfg: dict, key: str, project_arg: str | None) -> str | None:
    if not project_arg:
        return cfg.get("default_project_ref")
    if project_arg.startswith("http"):
        return project_arg
    if project_arg.isdigit():
        return f"{base_url(cfg)}project/{project_arg}"
    url = query_url(cfg, "project", query=f'(Name = "{project_arg}")',
                    fetch="Name,ObjectID", pagesize=2)
    qr = unwrap_query(http_get(url, key))
    results = qr.get("Results", [])
    if not results:
        die("project_not_found", f"No project named '{project_arg}'")
    if len(results) > 1:
        die("project_ambiguous", f"Multiple projects named '{project_arg}'",
            matches=[slim(r, ["Name", "ObjectID", "_ref"]) for r in results])
    return results[0]["_ref"]


def cmd_get(args: argparse.Namespace) -> None:
    cfg = load_config()
    key = require_key(cfg)
    fid = args.formatted_id.strip().upper()
    artifact = artifact_type_for_formatted_id(fid)
    fetch = "true" if args.full else ",".join(COMMON_FIELDS)
    url = query_url(cfg, artifact, query=f'(FormattedID = "{fid}")', fetch=fetch, pagesize=2)
    qr = unwrap_query(http_get(url, key))
    results = qr.get("Results", [])
    if not results:
        die("not_found", f"No {artifact} with FormattedID '{fid}'")
    print(json.dumps(slim(results[0]) if not args.full else results[0], indent=2))


def fetch_collection(cfg: dict, key: str, collection_ref: str, pagesize: int = DEFAULT_PAGESIZE) -> list:
    if "?" in collection_ref:
        url = f"{collection_ref}&fetch=true&pagesize={pagesize}"
    else:
        url = f"{collection_ref}?fetch=true&pagesize={pagesize}"
    qr = unwrap_query(http_get(url, key))
    return qr.get("Results", [])


def cmd_children(args: argparse.Namespace) -> None:
    """Fetch immediate children for a given artifact.

    - US: Tasks, Defects, Children (sub-stories)
    - DE: Tasks
    - DS: Defects
    - F/I/E (PortfolioItem): Children (lower-level PIs), UserStories
    """
    cfg = load_config()
    key = require_key(cfg)
    fid = args.formatted_id.strip().upper()
    artifact = artifact_type_for_formatted_id(fid)
    url = query_url(cfg, artifact, query=f'(FormattedID = "{fid}")', fetch="true", pagesize=2)
    qr = unwrap_query(http_get(url, key))
    results = qr.get("Results", [])
    if not results:
        die("not_found", f"No {artifact} with FormattedID '{fid}'")
    parent = results[0]

    buckets: dict[str, list] = {}
    for rel in ("Tasks", "Defects", "Children", "UserStories"):
        ref = parent.get(rel)
        if isinstance(ref, dict) and ref.get("Count", 0) > 0 and ref.get("_ref"):
            buckets[rel] = [slim(r) for r in fetch_collection(cfg, key, ref["_ref"])]
    print(json.dumps({
        "parent": slim(parent),
        "children": buckets,
    }, indent=2))


def cmd_tree(args: argparse.Namespace) -> None:
    """Recursive children to a bounded depth. Keep default shallow to avoid blowups."""
    cfg = load_config()
    key = require_key(cfg)

    def expand(fid: str, depth: int) -> dict:
        artifact = artifact_type_for_formatted_id(fid)
        url = query_url(cfg, artifact, query=f'(FormattedID = "{fid}")', fetch="true", pagesize=2)
        qr = unwrap_query(http_get(url, key))
        results = qr.get("Results", [])
        if not results:
            return {"FormattedID": fid, "error": "not_found"}
        node = slim(results[0])
        if depth <= 0:
            return node
        children: dict[str, list] = {}
        for rel in ("Tasks", "Defects", "Children", "UserStories"):
            ref = results[0].get(rel)
            if isinstance(ref, dict) and ref.get("Count", 0) > 0 and ref.get("_ref"):
                kids = fetch_collection(cfg, key, ref["_ref"])
                children[rel] = [
                    expand(k["FormattedID"], depth - 1) if k.get("FormattedID") else slim(k)
                    for k in kids
                ]
        if children:
            node["_children"] = children
        return node

    print(json.dumps(expand(args.formatted_id.strip().upper(), args.depth), indent=2))


def cmd_list(args: argparse.Namespace) -> None:
    cfg = load_config()
    key = require_key(cfg)
    type_map = {"US": "hierarchicalrequirement", "DE": "defect", "TA": "task",
                "DS": "defectsuite", "TC": "testcase",
                "F": "portfolioitem/feature"}
    artifact = type_map.get(args.type.upper()) if args.type else "hierarchicalrequirement"
    if not artifact:
        die("bad_type", f"Unknown --type '{args.type}'", known=list(type_map))

    clauses: list[str] = []
    if args.state:
        field = "State" if artifact in {"defect", "defectsuite"} else "ScheduleState"
        clauses.append(f'({field} = "{args.state}")')
    if args.owner:
        if args.owner.lower() == "me":
            uid = get_me(cfg, key).get("ObjectID")
            if not uid:
                die("whoami_failed", "Could not resolve current user ObjectID.")
            clauses.append(f"(Owner.ObjectID = {uid})")
        else:
            clauses.append(f'(Owner.UserName = "{args.owner}")')
    if args.name_contains:
        clauses.append(f'(Name contains "{args.name_contains}")')
    if args.iteration:
        clauses.append(f'(Iteration.Name = "{args.iteration}")')

    query = None
    if clauses:
        q = clauses[0]
        for c in clauses[1:]:
            q = f"({q} AND {c})"
        query = q

    project_ref = resolve_project_ref(cfg, key, args.project)
    url = query_url(cfg, artifact, query=query, fetch="true",
                    pagesize=args.pagesize, start=args.start,
                    order=args.order or "FormattedID", project=project_ref)
    qr = unwrap_query(http_get(url, key))
    items = [slim(r) for r in qr.get("Results", [])]
    print(json.dumps({
        "total": qr.get("TotalResultCount"),
        "start": qr.get("StartIndex"),
        "pagesize": qr.get("PageSize"),
        "items": items,
    }, indent=2))


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="rally", description="Rally WSAPI read-only client.")
    sub = p.add_subparsers(dest="command", required=True)

    pc = sub.add_parser("config", help="Manage ~/.rally config")
    pc.add_argument("action", choices=["get", "set", "unset"])
    pc.add_argument("key", nargs="?")
    pc.add_argument("value", nargs="?")
    pc.set_defaults(func=cmd_config)

    pw = sub.add_parser("whoami", help="Validate the API key and show current user")
    pw.set_defaults(func=cmd_whoami)

    pp = sub.add_parser("projects", help="List projects visible to the user")
    pp.add_argument("--pagesize", type=int, default=DEFAULT_PAGESIZE)
    pp.add_argument("--start", type=int, default=1)
    pp.set_defaults(func=cmd_projects)

    pg = sub.add_parser("get", help="Fetch one artifact by FormattedID (US1234, DE99, TA5, F42…)")
    pg.add_argument("formatted_id")
    pg.add_argument("--full", action="store_true", help="Return all fields, not just common ones")
    pg.set_defaults(func=cmd_get)

    pch = sub.add_parser("children", help="Fetch immediate children (tasks/defects/sub-stories)")
    pch.add_argument("formatted_id")
    pch.set_defaults(func=cmd_children)

    pt = sub.add_parser("tree", help="Recursive children to a bounded depth")
    pt.add_argument("formatted_id")
    pt.add_argument("--depth", type=int, default=2)
    pt.set_defaults(func=cmd_tree)

    pl = sub.add_parser("list", help="Query artifacts with filters")
    pl.add_argument("--type", default="US", help="US|DE|TA|DS|TC|F (default US)")
    pl.add_argument("--project", help="Project name, OID, or ref (default: default_project_ref)")
    pl.add_argument("--state", help="e.g. Defined, In-Progress, Completed, Accepted, Open, Closed")
    pl.add_argument("--owner", help="'me' or a UserName (e.g. alice@example.com)")
    pl.add_argument("--iteration", help="Exact iteration Name")
    pl.add_argument("--name-contains", help="Substring match on Name")
    pl.add_argument("--order", default=None, help="e.g. 'LastUpdateDate DESC'")
    pl.add_argument("--pagesize", type=int, default=50)
    pl.add_argument("--start", type=int, default=1)
    pl.set_defaults(func=cmd_list)

    return p


def main() -> None:
    args = build_parser().parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
