# One-call API, self-heal, export, review

Python module `finsights_lib` (stdlib only). Every function returns a plain JSON-serialisable dict, never prints, never raises (errors land in `error: {exit_code, message, partial_lines, stage}`). Dry run is the default.
```python
import sys; sys.path.insert(0, "<this skill>/scripts")
from finsights_lib import api, export, heal
```

## 1. Why
One call (`api.build`) runs lint -> heal -> compile -> plan -> apply -> verify and returns one result with `ok`, `stage`, `findings`, `healed`, `needs_decision`, `plan`, `verify`, `dashboards`. An LLM can loop on it: read `needs_decision`, patch the spec, call again. No stdout parsing, no hand-chained CLI steps. The CLI (section 5) is a thin wrapper over the same functions.

## 2. Self-heal contract
1. **Dry run first** (`write=False`, default). Proceed to `write=True` only when the dry run is `ok` and `needs_decision` is empty.
2. **Safe fixes are applied automatically** (only rename/clamp/alias, never add/remove/re-point a query, chart or filter). Exactly these rules:
   - L01 unknown spec key with exactly one near-miss valid key (edit distance <= 2): renamed to it.
   - L03 invalid id: replaced by a slug; references to a renamed query/chart id (chart `query`, dashboard item `chart`) are rewritten too.
   - L04 duplicate title in a kind: the later one becomes `Title (2)`, `(3)`...
   - L09 chart type alias (e.g. `pie`): normalised to the canonical type (`Donut`).
   - L11 layout outside the 20-column grid: x/y/w/h clamped into it.
   - L12 overlapping dashboard items: reflowed top-down; order and sizes kept.
3. **Unsafe fixes are NEVER applied.** They come back in `needs_decision` as `{rule, path, safe: false, description, patch: [], alternatives: [{description, patch}]}`: L05 unknown table or source_query, L06 unknown query column, L07 unknown chart query, L08 unknown chart column (alternatives = closest names, cutoff 0.8), L13 bad `applies_to` (closest column, or remove the entry), L20 missing agg (`count` / `count_distinct`, changes meaning). L10, L02, L16 are reported with no alternatives: edit by hand. Plan-time conflicts (unmarked same-title workbook, other marker, ambiguous title, 401/403, 4xx) and failed verify errors (V05 relink or drop_link) are also returned as `needs_decision`.
4. **Bounded**: at most `max_rounds` (default 3) lint->fix->relint rounds; stops early when a round changes nothing.
5. **Modes** `heal="safe"` (default: apply safe fixes, return the rest), `"propose"` (change nothing, list every fix in `needs_decision`), `"off"` (lint only). Other values give exit_code 2.
The healed spec comes back in `result["spec"]`; save it. Healing runs only on specs, never on compiled envelopes.

## 3. Function reference
Result of `build` (`build_many` wraps a list of these): `ok, stage` (`load|lint|compile|plan|apply|verify|done`), `write, workbook {id,title,live_name}, findings [{rule,severity,path,message,fix_hint}], plan {creates,updates,orphans,actions,lines}|None, applied {changed,lines,names}|None, verify {ok,findings}|None, dashboards [{title,live_name,path}], rounds, healed [fix], needs_decision [fix], spec, confirm (token|None), error`. Without `INSIGHTS_*` env and no `client`, build stops after compile with `ok=True` (offline, `plan` is None).

### api.build
`build(spec, *, catalog=None, client=None, write=False, adopt=False, prune=False, renames=None, verify=True, heal="safe", max_rounds=3, confirm=None, min_rows=1)`. `spec` = dict, file path, or JSON/YAML text. `catalog` = dict or path; if missing, a live snapshot is taken when the client exists. `renames` = `{spec_id: "Old live title"}`.
```python
r = api.build("s.yaml", catalog="cat.json")          # dry run
print(r["ok"], r["stage"], r["needs_decision"], r["confirm"])
```
### api.build_many
`build_many(specs, *, stop_on_error=False, max_items=20, **build_kwargs)` -> `{ok, items: [build result], summary {total, ok, failed, skipped}}`; `confirm` may be a dict keyed by workbook id or title. Refuses (exit_code 2, nothing run) above `max_items` or when two specs share a workbook id or title.
```python
r = api.build_many(["a.yaml", "b.yaml"], catalog="cat.json")
print(r["summary"], [i["workbook"]["title"] for i in r["items"] if not i["ok"]])
```
### api.diff
`diff(a, b)` compares two compiled envelopes (dicts or paths/text), names normalised to `kind:title` -> `{added, removed, changed: {kind: [{title, fields}]}, same}`; non-envelopes give `error`.
```python
d = api.diff(export.export_workbook(c, "ury-sales")["compiled"], "workbook.json")
print(d["same"], d["changed"])
```
`c` below is `from finsights_lib.client import Client; c = Client.from_env()` (needs `INSIGHTS_URL` + auth env).
### export.export_workbook
`export_workbook(client, ref)` -> `{ok, workbook {name,title,marker,modified}, compiled, summary {queries,charts,dashboards,filters}, names {kind: {title: live}}, lossy [str], error}`. See section 7.
```python
e = export.export_workbook(c, "URY Sales"); print(e["summary"], e["lossy"])
```
### export.export_all
`export_all(client)` -> `{ok, workbooks: [{name,title,marker,queries,charts,dashboards,modified}]}` (index only).
```python
print([w["title"] for w in export.export_all(c)["workbooks"]])
```
### export.review
`review(client, ref, *, min_rows=1)` runs every chart's query and checks every dashboard -> `{ok, workbook, charts [{title,type,query,rows,ok,error}], dashboards [{title,live_name,path,charts,filters,links_ok}], findings}`. Findings: V03 query failed or chart without query, V02 rows < min_rows, V04 linked_charts != chart items, V05 filter links. Read-only.
```python
rv = export.review(c, "ury-sales"); print(rv["ok"], rv["findings"])
```
### heal.heal_spec
`heal_spec(spec_dict, catalog=None, *, max_rounds=3, allow="safe")` -> `{spec, rounds, applied [fix], proposed [fix], remaining [error findings]}`. Offline. `allow="safe"` applies safe fixes; any other value applies none and proposes all.
```python
h = heal.heal_spec(spec, cat); print(h["rounds"], [f["rule"] for f in h["applied"]], h["remaining"])
```
### heal.suggest
`suggest(findings, spec, catalog=None)` -> list of fix dicts (safe first), nothing applied.
```python
from finsights_lib.lint import lint_spec
fixes = heal.suggest(lint_spec(spec, cat), spec, cat)
```
### heal.apply_patch
`apply_patch(spec, ops)` applies JSON-pointer `replace|remove|add` ops to a deep copy; `ValueError` on a bad op or missing path (it does raise).
```python
spec = heal.apply_patch(spec, r["needs_decision"][0]["alternatives"][0]["patch"])
```
### api.make_token
`make_token(wb_id, title, actions, prune=False, adopt=False)` -> 16-hex sha256 prefix over the plan actions; `build` computes it for you (`result["confirm"]`).

## 4. Confirm-token flow (prune, title adoption)
Destructive writes need the token of an identical dry run: `r = api.build(spec, prune=True)` (dry run, read `r["plan"]`, `r["confirm"]`) then `api.build(spec, prune=True, write=True, confirm=r["confirm"])`. Same for `adopt=True` when an unmarked same-title workbook exists. Refused with `error.exit_code` 3, nothing written: no `confirm`, a token from a different plan/flags (re-run the dry run), and an unmarked same-title workbook without `adopt=True`. The token changes if the plan changes. Plain creates/updates need no token. Adopt, review the plan, only then prune (rule 13).

## 5. CLI equivalents
Spec comes from `--spec FILE` or `--spec -` (stdin, JSON or YAML mapping). Network commands need `INSIGHTS_*`.
| Command | Flags | Notes |
|---|---|---|
| `finsights build` | `--heal safe\|propose\|off`, `--max-rounds N`, `--adopt`, `--prune`, `--catalog FILE`, `--no-verify`, `--min-rows N`, `--confirm TOKEN`, `--write`, `--json` | dry run unless `--write` |
| `finsights bulk FILE.json` | build flags, `--confirm WORKBOOK_ID=TOKEN` (repeat), `--stop-on-error`, `--max-items N` (20) | file is a list or `{"specs": [...]}` of spec mappings or spec file paths |
| `finsights export` | `--workbook REF` or `--all` (exclusive, one required), `-o FILE` | JSON to stdout or file |
| `finsights review` | `--workbook REF`, `--min-rows N`, `--json` | read-only |
| `finsights heal` | `--spec`, `--max-rounds N`, `--catalog FILE`, `-o FILE` | offline; prints `{spec, rounds, applied, proposed, remaining}` |
Exit codes: build/bulk 0 ok; 1 not ok (lint, verify, unhealed errors); 2 usage/parse/heal mode; 3 live conflict or missing/mismatched confirm; 4 auth/network. bulk: 2 for a refused list, else 3/4 if any item had it, else 1. export: 0, else the error code (2 unknown ref, 3 ambiguous, 4 network). review: 0 ok, 1 findings. heal: 0, or 1 when error findings remain.
```bash
cat s.yaml | finsights build --spec - --json > r.json
```

## 6. Worked LLM loop
```python
import sys, json; sys.path.insert(0, "SKILL/scripts")
from finsights_lib import api, export, heal
from finsights_lib.client import Client
c = Client.from_env()
spec = "s.yaml"
r = api.build(spec, catalog="cat.json", client=c)                 # 1 dry run (safe heals applied in memory)
spec = r["spec"]                                                  # healed spec dict; save it
for nd in r["needs_decision"]:                                    # 2 read decisions
    if nd["alternatives"]:                                        #   LLM picks an alternative
        spec = heal.apply_patch(spec, nd["alternatives"][0]["patch"])   # 3 patch the spec
r = api.build(spec, catalog="cat.json", client=c)                 #   re-dry-run until clean
if r["ok"] and not r["needs_decision"]:
    r = api.build(spec, catalog="cat.json", client=c, write=True) # 4 rebuild with write
    print(export.review(c, r["workbook"]["live_name"])["ok"])     # 5 review the live workbook
```
Bulk with two specs (dry run, then write each with its own token only if destructive):
```python
r = api.build_many([spec_a, spec_b], catalog="cat.json", client=c)
if r["ok"]:
    r = api.build_many([spec_a, spec_b], catalog="cat.json", client=c, write=True)
print(r["summary"])
```
Unattended tests for these live in the skill's `tests/` (fake site); the snippets above were not run against a live site (unverified).

## 7. Export semantics
`export_workbook` reads the workbook, its user queries (empty per-chart `data_query` helpers are skipped), charts and dashboards and returns a **faithful compiled envelope** (`{version, type, name, doc, dependencies: {folders, queries, charts, dashboards}}`) keyed by **LIVE names**, with JSON fields parsed. Server-only fields (`data_query`, `linked_charts`, owner, timestamps...) are dropped. `lossy` lists everything dropped or kept as string (non-empty fields not representable in the envelope, invalid JSON). `names` maps kind -> title -> live name. `export_all` is the index only (no docs).
`ref` accepts: live name, spec id (`ury_sales`) or its kebab form (`ury-sales`), the full marker (`finsights:ury-sales`), or an exact title; lookup order is name, marker, title. Unknown -> exit_code 2; ambiguous -> 3 with the candidates listed. A title like `URY Sales` is not normalised to a marker.
Export is NOT a round-trippable spec: it is the compiled envelope, usable with `api.diff` and for inspection, and `api.build` accepts an envelope (no lint/heal) but live names and ids stay as exported.

## 8. Deliberately not done
- No `add_chart`/`add_filter` helpers: edit the spec dict (or apply a fix patch) and rebuild.
- No reverse-compile from live or envelope back to a spec.
- No automatic prune or adopt: both need a dry run and its confirm token, and prune is refused in the run that adopted by title.
- No heal of semantic errors (unknown table/column, missing agg): proposed only.
