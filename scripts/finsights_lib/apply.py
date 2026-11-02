"""Plan and apply a compiled workbook through the REST doctype API (idempotent upsert)."""

from __future__ import annotations

import copy
import json
import re
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


def diff_fields(kind: str, desired: dict, live: dict) -> dict:
	out = {}
	for f in FIELDS[kind] + ("workbook",):
		if f in desired and normalise(desired[f]) != normalise(live.get(f)):
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
		return hits[0]
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
		actions.append(Action("create", DOCTYPES["workbook"], wb_key, None,
			{"title": title, "from_template": marker(compiled)}, title))
		wb_live = None
	else:
		wb_live = wb["name"]
		f = {}
		if wb.get("title") != title:
			f["title"] = title
		if wb.get("from_template") != marker(compiled):
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
			rows = client.list_docs(DOCTYPES[kind], filters={"workbook": wb_live}, fields=fields)
		if kind == "chart":
			helpers = {r.get("data_query") for r in rows if r.get("data_query")}
		if kind == "query":
			rows = [r for r in rows if r["name"] not in helpers and not _is_empty_helper(r)]
		live_by_kind[kind] = rows
		matched[kind] = set()
		for key, doc in (deps.get(DEP_KEY[kind]) or {}).items():
			want = [doc["title"]]
			if key in renames or doc.get("name") in renames:
				want.append(renames.get(key, renames.get(doc.get("name"))))
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
	return Plan(actions, name_map, wb_live)


# ------------------------------------------------------------------ apply
def _doc_for(compiled, a, kind):
	return compiled["dependencies"][DEP_KEY[kind]][a.key]


def apply_plan(plan: Plan, compiled: dict, client, write: bool = False, prune: bool = False) -> Result:
	res = Result()
	name_map = dict(plan.name_map)
	wb_live = plan.workbook_live_name
	mk = marker(compiled)
	pending = False

	def say(prefix, a, extra=""):
		res.lines.append(f"{prefix} {KIND_OF[a.doctype]} {a.title!r}{extra}")

	for a in plan.actions:
		kind = KIND_OF[a.doctype]
		if a.op == "noop":
			continue
		if a.op == "orphan":
			if prune:
				pending = True
				if write:
					client.delete(f"/api/resource/{a.doctype}/{a.live_name}")
					say("deleted", a, f" ({a.live_name})")
				else:
					say("- would delete", a, f" ({a.live_name})")
			else:
				say("orphan", a, f" ({a.live_name}; not deleted, use prune)")
			continue
		pending = True
		if kind == "workbook":
			if a.op == "create":
				if not write:
					say("+ would create", a)
					continue
				r = client.post(f"/api/resource/{a.doctype}", {"title": a.title})
				wb_live = r["data"]["name"]
				name_map[a.key] = wb_live
				client.put(f"/api/resource/{a.doctype}/{wb_live}", {"from_template": mk})
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
