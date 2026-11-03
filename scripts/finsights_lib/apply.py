"""Plan and apply a compiled workbook through the REST doctype API (idempotent upsert)."""

from __future__ import annotations

import copy
import json
import re
import sys
from dataclasses import dataclass, field

from .common import FinsightsError

DOCTYPES = {
	"workbook": "Insights Workbook",
	"query": "Insights Query v3",
	"chart": "Insights Chart v3",
	"dashboard": "Insights Dashboard v3",
}
DEP_KEY = {"query": "queries", "chart": "charts", "dashboard": "dashboards"}
FIELDS = {
	"query": ("title", "folder", "sort_order", "use_live_connection", "is_builder_query",
		"is_native_query", "is_script_query", "operations"),
	"chart": ("title", "folder", "sort_order", "query", "chart_type", "config"),
	"dashboard": ("title", "items"),
}
JSON_FIELDS = ("operations", "config", "items")
KIND_OF = {v: k for k, v in DOCTYPES.items()}
_REF = re.compile(r"`([^`]+)`")


@dataclass
class Action:
	op: str  # create | update | orphan | noop
	doctype: str
	key: str  # spec-local name (workbook: compiled doc name)
	live_name: str | None
	fields: dict = field(default_factory=dict)
	title: str = ""


@dataclass
class Plan:
	actions: list
	name_map: dict
	workbook_live_name: str | None = None
	title_matched: bool = False  # workbook was found by title, not by marker


@dataclass
class Result:
	changed: bool = False
	lines: list = field(default_factory=list)
	names: dict = field(default_factory=dict)


# ------------------------------------------------------------------ helpers
def marker(compiled: dict) -> str:
	return "finsights:" + compiled["doc"]["name"]


def _rewrite_ops(node, nm, wb_live):
	if isinstance(node, list):
		for n in node:
			_rewrite_ops(n, nm, wb_live)
	elif isinstance(node, dict):
		if "query_name" in node:
			node["query_name"] = nm.get(node["query_name"], node["query_name"])
			if "workbook" in node:
				node["workbook"] = wb_live
		for v in node.values():
			_rewrite_ops(v, nm, wb_live)


def rewrite(kind: str, doc: dict, name_map: dict, wb_live) -> dict:
	"""Return a copy of a compiled doc with every spec-local reference swapped for live names."""
	d = copy.deepcopy(doc)
	d.pop("name", None)
	d["workbook"] = wb_live
	if kind == "query":
		_rewrite_ops(d.get("operations"), name_map, wb_live)
	elif kind == "chart":
		d["query"] = name_map.get(d.get("query"), d.get("query"))
	elif kind == "dashboard":
		for it in d.get("items") or []:
			if it.get("type") == "chart" and "chart" in it:
				it["chart"] = name_map.get(it["chart"], it["chart"])
			if isinstance(it.get("links"), dict):
				links = {}
				for ck, ref in it["links"].items():
					ref = _REF.sub(lambda m, nm=name_map: "`%s`" % nm.get(m.group(1), m.group(1)), ref, count=1)
					links[name_map.get(ck, ck)] = ref
				it["links"] = links
	return d


def _strip(v):
	if isinstance(v, dict):
		return {k: _strip(x) for k, x in v.items() if k != "moved"}
	if isinstance(v, list):
		return [_strip(x) for x in v]
	if isinstance(v, bool):
		return int(v)
	return v


def normalise(value):
	if isinstance(value, str):
		s = value.strip()
		if s[:1] in "[{":
			try:
				value = json.loads(s)
			except ValueError:
				pass
	if value == "":
		value = None
	return json.dumps(_strip(value), sort_keys=True, ensure_ascii=False)


def norm_dashboard_items(items, name_map=None):
	"""Single normaliser for dashboard `items`, used by plan diff (apply) and V01 (verify).

	Drops server noise (layout.moved), optionally rewrites chart refs via name_map, and sorts by
	layout.i so only content/x/y/w/h differences remain.
	"""
	if isinstance(items, str):
		try:
			items = json.loads(items)
		except ValueError:
			return items
	out = []
	for it in items or []:
		it = _strip(it)
		if name_map and isinstance(it, dict) and it.get("chart"):
			it["chart"] = name_map.get(it["chart"], it["chart"])
		out.append(it)
	return sorted(out, key=lambda i: str((i.get("layout") or {}).get("i", "")) if isinstance(i, dict) else "")


def _contains(want, got) -> bool:
	"""True when `got` holds everything in `want`; extra dict keys in `got` are ignored. Insights 3.50-dev adds
	defaults on save (chart config `id`, `number_column_options`, ...), which must not read as drift."""
	if isinstance(want, dict):
		return isinstance(got, dict) and all(k in got and _contains(v, got[k]) for k, v in want.items())
	if isinstance(want, list):
		return isinstance(got, list) and len(want) == len(got) and all(_contains(a, b) for a, b in zip(want, got))
	return want == got or (isinstance(want, bool) or isinstance(got, bool)) and int(bool(want)) == int(bool(got))


def to_develop_config(cfg):
	"""Best-effort image of a 3.15-shaped chart config after Insights 3.50-dev's `normalize_chart_config`
	(server-side on every save). Only keys observed live are mapped (2026-11-03, Insights 3.50.0-dev):
	  Number `comparison: true`            -> number_column_options[*].comparison = {source: previous}
	  `date_column.granularity`            -> window.grain (and removed from date_column)
	Anything else the server adds is covered by the subset compare in `_contains`."""
	import copy
	cfg = copy.deepcopy(cfg)
	if not isinstance(cfg, dict):
		return cfg
	if cfg.get("comparison") in (True, 1):
		cfg.pop("comparison")
		n = len(cfg.get("number_columns") or [])
		cfg["number_column_options"] = [{"comparison": {"source": "previous"}} for _ in range(max(n, 1))]
	dc = cfg.get("date_column")
	if isinstance(dc, dict) and dc.get("granularity"):
		cfg["window"] = {**(cfg.get("window") or {}), "grain": dc.pop("granularity")}
	return cfg


def _loaded(v):
	return json.loads(json.dumps(_strip(json.loads(normalise(v)))))


def diff_fields(kind: str, desired: dict, live: dict) -> dict:
	out = {}
	for f in FIELDS[kind] + ("workbook",):
		if f not in desired:
			continue
		if kind == "dashboard" and f == "items":
			a, b = norm_dashboard_items(desired[f]), norm_dashboard_items(live.get(f))
			if json.dumps(a, sort_keys=True) != json.dumps(b, sort_keys=True):
				out[f] = desired[f]
		elif normalise(desired[f]) != normalise(live.get(f)):
			want = _loaded(desired[f])
			if kind == "chart" and f == "config":
				want = to_develop_config(want)
			if f in JSON_FIELDS and _contains(want, _loaded(live.get(f))):
				continue  # live only has server-added defaults on top of the spec
			out[f] = desired[f]
	return out


def _topo(queries: dict) -> list:
	order, seen, stack = [], set(), set()

	def refs(op_list):
		found = []
		_collect(op_list, found)
		return found

	def _collect(node, found):
		if isinstance(node, list):
			for n in node:
				_collect(n, found)
		elif isinstance(node, dict):
			if "query_name" in node:
				found.append(node["query_name"])
			for v in node.values():
				_collect(v, found)

	def visit(k):
		if k in seen:
			return
		if k in stack:
			raise FinsightsError(f"query reference cycle at {k}", 2)
		stack.add(k)
		for r in refs(queries[k].get("operations")):
			if r in queries:
				visit(r)
		stack.discard(k)
		seen.add(k)
		order.append(k)

	for k in sorted(queries, key=lambda x: (queries[x].get("sort_order") or 0, x)):
		visit(k)
	return order


# ------------------------------------------------------------------ plan
def _find_workbook(compiled, client, adopt):
	mk = marker(compiled)
	title = compiled["doc"]["title"]
	if not client.has_field(DOCTYPES["workbook"], "from_template"):
		# Insights 3.50-dev (Frappe develop): no ownership marker field. A workbook is matched by title only,
		# flagged _by_title so --prune stays refused (nothing proves finsights owns it).
		hits = client.list_docs(DOCTYPES["workbook"], filters={"title": title}, fields=["name", "title"])
		if len(hits) > 1:
			raise FinsightsError(f"ambiguous: {len(hits)} workbooks titled {title!r}", 3)
		return dict(hits[0], _by_title=True) if hits else None
	flds = ["name", "title", "from_template"]
	hits = client.list_docs(DOCTYPES["workbook"], filters={"from_template": mk}, fields=flds)
	if len(hits) > 1:
		raise FinsightsError(f"ambiguous: {len(hits)} workbooks carry marker {mk}", 3)
	if hits:
		return hits[0]
	hits = client.list_docs(DOCTYPES["workbook"], filters={"title": title}, fields=flds)
	if len(hits) > 1:
		raise FinsightsError(f"ambiguous: {len(hits)} workbooks titled {title!r}", 3)
	if hits:
		ft = hits[0].get("from_template")
		if ft and ft != mk and not adopt:
			raise FinsightsError(
				f"workbook {hits[0]['name']} is managed by template/marker {ft!r}; pass adopt to take it over", 3
			)
		if not ft and not adopt:
			raise FinsightsError(
				f"a workbook titled {title!r} exists ({hits[0]['name']}) that finsights did not create. "
				"`--adopt` takes it over (sets the finsights marker); `--prune` will then treat its other "
				"queries, charts and dashboards as orphans and delete them. Review `plan` before pruning.", 3
			)
		return dict(hits[0], _by_title=True)
	return None


def _is_empty_helper(row: dict) -> bool:
	ops = row.get("operations")
	if isinstance(ops, str):
		ops = ops.strip()
		ops = json.loads(ops) if ops else None
	return not row.get("title") and not ops


def plan_apply(compiled: dict, client, adopt: bool = False, renames: dict | None = None) -> Plan:
	renames = renames or {}
	title = compiled["doc"]["title"]
	wb_key = compiled["doc"]["name"]
	wb = _find_workbook(compiled, client, adopt)
	actions, name_map = [], {}
	if wb is None:
		wbf = {"title": title}
		if client.has_field(DOCTYPES["workbook"], "from_template"):
			wbf["from_template"] = marker(compiled)
		actions.append(Action("create", DOCTYPES["workbook"], wb_key, None, wbf, title))
		wb_live = None
	else:
		wb_live = wb["name"]
		f = {}
		if wb.get("title") != title:
			f["title"] = title
		if client.has_field(DOCTYPES["workbook"], "from_template") and wb.get("from_template") != marker(compiled):
			f["from_template"] = marker(compiled)
		actions.append(Action("update" if f else "noop", DOCTYPES["workbook"], wb_key, wb_live, f, title))
	name_map[wb_key] = wb_live
	deps = compiled.get("dependencies", {})
	live_by_kind, matched = {}, {}
	helpers = set()  # chart data_query helper queries: server-managed, never user content
	for kind in ("chart", "query", "dashboard"):
		rows = []
		if wb_live:
			fields = ["name", "title"] + {"query": ["operations"], "chart": ["data_query"]}.get(kind, [])
			if kind == "chart" and not client.has_field(DOCTYPES["chart"], "data_query"):
				fields = ["name", "title"]
			rows = client.list_docs(DOCTYPES[kind], filters={"workbook": wb_live}, fields=fields)
		if kind == "chart":
			helpers = {r.get("data_query") for r in rows if r.get("data_query")}
		if kind == "query":
			rows = [r for r in rows if r["name"] not in helpers and not _is_empty_helper(r)]
		live_by_kind[kind] = rows
		matched[kind] = set()
		kdocs = deps.get(DEP_KEY[kind]) or {}
		for key, doc in kdocs.items():
			want = [doc["title"]]
			if key in renames or doc.get("name") in renames:
				old = renames.get(key, renames.get(doc.get("name")))
				clash = any(k2 != key and d2["title"] == old for k2, d2 in kdocs.items())
				if clash and any(r["title"] == old for r in rows):
					raise FinsightsError(
						f"rename {old!r} is ambiguous: a live {kind} with that title is also the match of "
						"another spec item. Rename or remove one of them first", 3)
				want.append(old)
			hit = None
			for t in want:
				c = [r for r in rows if r["title"] == t and r["name"] not in matched[kind]]
				if len(c) > 1:
					raise FinsightsError(f"ambiguous: {len(c)} {kind}s titled {t!r}", 3)
				if c:
					hit = c[0]
					break
			if hit:
				name_map[key] = hit["name"]
				matched[kind].add(hit["name"])
	# build actions kind by kind; diff needs the full name_map (placeholders stay local)
	for kind in ("query", "chart", "dashboard"):
		docs = deps.get(DEP_KEY[kind]) or {}
		keys = _topo(docs) if kind == "query" else sorted(docs, key=lambda x: (docs[x].get("sort_order") or 0, x))
		for key in keys:
			doc = docs[key]
			want = rewrite(kind, doc, name_map, wb_live)
			live_name = name_map.get(key)
			if live_name is None:
				actions.append(Action("create", DOCTYPES[kind], key, None,
					{f: want[f] for f in FIELDS[kind] + ("workbook",) if f in want}, doc["title"]))
				continue
			live = client.get_doc(DOCTYPES[kind], live_name)
			changed = diff_fields(kind, want, live)
			changed.pop("workbook", None)
			actions.append(Action("update" if changed else "noop", DOCTYPES[kind], key, live_name,
				changed, doc["title"]))
	for kind in ("dashboard", "chart", "query"):
		for r in live_by_kind[kind]:
			if r["name"] not in matched[kind]:
				actions.append(Action("orphan", DOCTYPES[kind], "", r["name"], {}, r["title"]))
	return Plan(actions, name_map, wb_live, bool(wb and wb.get("_by_title")))


# ------------------------------------------------------------------ apply
def _doc_for(compiled, a, kind):
	return compiled["dependencies"][DEP_KEY[kind]][a.key]


def apply_plan(plan: Plan, compiled: dict, client, write: bool = False, prune: bool = False) -> Result:
	res = Result()
	name_map = dict(plan.name_map)
	wb_live = plan.workbook_live_name
	mk = marker(compiled)
	if prune and plan.title_matched:
		raise FinsightsError(
			"refusing --prune: the workbook was matched by title and is not yet marked as finsights-managed. "
			"Adopt first without --prune, review `plan`, then prune", 3)

	def say(prefix, a, extra=""):
		res.lines.append(f"{prefix} {KIND_OF[a.doctype]} {a.title!r}{extra}")

	state = {'pending': False}
	try:
		for a in plan.actions:
			kind = KIND_OF[a.doctype]
			if a.op == "noop":
				continue
			if a.op == "orphan":
				if prune:
					state['pending'] = True
					if write:
						client.delete(f"/api/resource/{a.doctype}/{a.live_name}")
						say("deleted", a, f" ({a.live_name})")
					else:
						say("- would delete", a, f" ({a.live_name})")
				else:
					say("orphan", a, f" ({a.live_name}; not deleted, use prune)")
				continue
			state['pending'] = True
			if kind == "workbook":
				if a.op == "create":
					if not write:
						say("+ would create", a)
						continue
					body = {"title": a.title}
					if client.has_field(a.doctype, "from_template"):
						body["from_template"] = mk
					r = client.post(f"/api/resource/{a.doctype}", body)
					wb_live = r["data"]["name"]
					name_map[a.key] = wb_live
					say("created", a, f" ({wb_live})")
				else:
					if not write:
						say("~ would update", a, " " + ",".join(sorted(a.fields)))
						continue
					client.put(f"/api/resource/{a.doctype}/{a.live_name}", dict(a.fields))
					say("updated", a, " " + ",".join(sorted(a.fields)))
				continue
			if not write:
				if a.op == "create":
					say("+ would create", a)
				else:
					say("~ would update", a, " " + ",".join(sorted(a.fields)))
				continue
			want = rewrite(kind, _doc_for(compiled, a, kind), name_map, wb_live)
			if a.op == "create":
				body = {f: want[f] for f in FIELDS[kind] + ("workbook",) if f in want}
				r = client.post(f"/api/resource/{a.doctype}", body)
				name_map[a.key] = r["data"]["name"]
				say("created", a, f" ({name_map[a.key]})")
			else:
				body = {f: want[f] for f in a.fields if f in want}
				client.put(f"/api/resource/{a.doctype}/{a.live_name}", body)
				say("updated", a, " " + ",".join(sorted(body)))
	except Exception as e:
		e.partial_lines = list(res.lines)
		if res.lines:
			print("apply failed part-way; done so far:\n  " + "\n  ".join(res.lines), file=sys.stderr)
		raise
	pending = state['pending']
	res.changed = pending
	if not pending:
		res.lines = ["no changes"] + res.lines
	titles = {"query": {}, "chart": {}, "dashboard": {}}
	for a in plan.actions:
		kind = KIND_OF[a.doctype]
		if kind in titles and a.op != "orphan" and name_map.get(a.key):
			titles[kind][a.title] = name_map[a.key]
	res.names = {"workbook": {compiled["doc"]["title"]: wb_live}, **titles}
	return res
