"""One-call Python API: build (lint -> heal -> compile -> plan -> apply -> verify), build_many, diff.

Every function returns a plain JSON-serialisable dict, never prints and never raises FinsightsError
(errors become the `error` dict). Dry run is the default; destructive operations (prune, adopting an
unmarked same-title workbook) need the confirm token of an identical dry run."""

from __future__ import annotations

import contextlib
import copy
import hashlib
import io
import json
import os
import re
from pathlib import Path

from . import catalog as catmod
from . import client as clientmod
from . import yamlish
from .apply import DEP_KEY, apply_plan, plan_apply
from .common import FinsightsError
from .compile import compile_spec
from .lint import exit_code_for, lint_spec
from .spec import load_spec
from .verify import verify as verify_doc

MAX_APPLY_RETRIES = 2
KINDS = ("queries", "charts", "dashboards")


# ------------------------------------------------------------------ helpers
def _error(e, stage):
	if isinstance(e, FinsightsError):
		code, msg = e.exit_code, str(e)
	else:
		code, msg = 1, f"{type(e).__name__}: {e}"
	return {"exit_code": code, "message": msg, "partial_lines": list(getattr(e, "partial_lines", []) or []),
		"stage": stage}


def _blank(write):
	return {"ok": False, "stage": "load", "write": bool(write),
		"workbook": {"id": "", "title": "", "live_name": None}, "findings": [], "plan": None, "applied": None,
		"verify": None, "dashboards": [], "rounds": 0, "healed": [], "needs_decision": [], "spec": {},
		"confirm": None, "error": None}


def _is_envelope(x):
	return isinstance(x, dict) and isinstance(x.get("dependencies"), dict)


def _load(spec):
	"""dict | path | JSON/YAML text -> dict (deep copy). Raises FinsightsError."""
	if isinstance(spec, dict):
		return copy.deepcopy(spec)
	if isinstance(spec, os.PathLike) or (isinstance(spec, str) and "\n" not in spec and not spec.lstrip().startswith("{")):
		return load_spec(spec)
	if isinstance(spec, str):
		text = spec.strip()
		try:
			data = json.loads(text)
		except ValueError:
			try:
				data = yamlish.loads(spec)
			except Exception as e:
				raise FinsightsError(f"spec text is neither JSON nor YAML: {e}", 2)
		if not isinstance(data, dict):
			raise FinsightsError("spec top level must be a mapping", 2)
		return data
	raise FinsightsError(f"unsupported spec input {type(spec).__name__}", 2)


def _load_catalog(catalog):
	if catalog is None or isinstance(catalog, dict):
		return catalog
	return catmod.load(catalog)


def _wb_ident(obj):
	if _is_envelope(obj):
		d = obj.get("doc") or {}
		return str(d.get("name") or ""), str(d.get("title") or "")
	wb = obj.get("workbook") if isinstance(obj, dict) else None
	wb = wb if isinstance(wb, dict) else {}
	return str(wb.get("id") or ""), str(wb.get("title") or "")


def _fd(findings):
	return [f.to_dict() if hasattr(f, "to_dict") else dict(f) for f in findings]


def _has_error(fds):
	return any(f.get("severity") == "error" for f in fds)


def make_token(wb_id, title, actions, prune=False, adopt=False):
	acts = [{"op": a["op"], "doctype": a["doctype"], "title": a["title"], "live_name": a["live_name"]} for a in actions]
	blob = json.dumps({"workbook": wb_id, "title": title, "actions": acts, "prune": bool(prune), "adopt": bool(adopt)},
		sort_keys=True, separators=(",", ":"), ensure_ascii=False)
	return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def _fix(rule, path, description, alternatives=None):
	return {"rule": rule, "path": path, "safe": False, "description": description, "patch": [],
		"alternatives": alternatives or []}


def _title_ptr(spec):
	return "/doc/title" if _is_envelope(spec) else "/workbook/title"


def _plan_alternatives(e, spec, title, client):
	"""Turn a plan-time FinsightsError into needs_decision entries."""
	msg = str(e)
	status = getattr(e, "status", None)
	ptr = _title_ptr(spec)
	rename = {"kind": "rename_workbook", "description": f"build a new workbook instead: rename it to {title + ' (2)'!r}",
		"patch": [{"op": "replace", "path": ptr, "value": title + " (2)"}]}
	if "finsights did not create" in msg:
		adopt = {"kind": "adopt", "description": "take over the existing workbook (sets the finsights marker); --prune "
			"would then delete its other queries, charts and dashboards",
			"how": "build(spec, adopt=True) as a dry run to get the confirm token, then "
				"build(spec, adopt=True, write=True, confirm=<token>)"}
		return [_fix("APPLY", ptr, "an unmarked workbook with this title exists", [adopt, rename])]
	if "managed by template/marker" in msg:
		return [_fix("APPLY", ptr, "the workbook with this title belongs to another template/marker", [rename])]
	if "ambiguous" in msg:
		names = []
		if client is not None:
			with contextlib.suppress(Exception):
				names = [r.get("name") for r in client.list_docs("Insights Workbook", filters={"title": title}, fields=["name"])]
		hint = f"ambiguous match: {msg}"
		if names:
			hint += f" (live workbooks titled {title!r}: {', '.join(map(str, names))})"
		return [_fix("APPLY", ptr, hint + "; remove or rename the duplicates on the site", [rename])]
	if status in (401, 403):
		return [_fix("APPLY", "", "authentication or permission problem: check the credentials and the Insights User/Admin role")]
	if status is not None and 400 <= status < 500:
		return [_fix("APPLY", "", f"the server rejected the request: {msg}; fix the spec value the server names and retry")]
	return []


def _verify_fixes(fds):
	out = []
	for f in fds:
		if f.get("severity") != "error":
			continue
		alts = []
		if f.get("rule") == "V05":
			alts = [{"kind": "relink", "description": "relink the filter to a column the query returns (select it in the query)"},
				{"kind": "drop_link", "description": "remove this filter link from applies_to (decide explicitly; never auto-dropped)"}]
		out.append(_fix(f.get("rule", "V"), f.get("path", ""), f.get("message", "") + (f" ({f['fix_hint']})" if f.get("fix_hint") else ""), alts))
	return out


def _lint_compile(spec, cat):
	findings = lint_spec(spec, cat)
	fds = _fd(findings)
	doc = None
	if not exit_code_for(findings):
		try:
			doc, extra = compile_spec(spec, cat, "template")
			fds += _fd(extra)
		except FinsightsError:
			raise
		except Exception as e:
			raise FinsightsError(f"compile failed: {type(e).__name__}: {e}", 1)
	return fds, doc, findings


def _dashboards(doc, name_map):
	out = []
	for key, d in ((doc.get("dependencies") or {}).get("dashboards") or {}).items():
		live = (name_map or {}).get(key)
		out.append({"title": d.get("title", key), "live_name": live, "path": f"/insights/dashboards/{live}" if live else None})
	return out


# ------------------------------------------------------------------ build
def build(spec, *, catalog=None, client=None, write=False, adopt=False, prune=False, renames=None, verify=True,
		heal="safe", max_rounds=3, confirm=None, min_rows=1):
	res = _blank(write)
	stage = "load"
	try:
		return _build(res, spec, catalog, client, write, adopt, prune, renames, verify, heal, max_rounds, confirm, min_rows)
	except Exception as e:  # never raise
		res["ok"] = False
		res["error"] = _error(e, res["stage"] or stage)
		return res


def _build(res, spec, catalog, client, write, adopt, prune, renames, verify, heal, max_rounds, confirm, min_rows):
	if heal not in ("safe", "propose", "off"):
		raise FinsightsError(f"heal must be safe|propose|off, got {heal!r}", 2)
	spec = _load(spec)
	envelope = _is_envelope(spec)
	res["spec"] = spec
	res["workbook"]["id"], res["workbook"]["title"] = _wb_ident(spec)
	cat = _load_catalog(catalog)
	holder = {"client": client, "tried": client is not None}

	def get_client():
		if not holder["tried"]:
			holder["tried"] = True
			try:
				holder["client"] = clientmod.Client.from_env()
			except FinsightsError:
				holder["client"] = None
		return holder["client"]

	# auto catalog snapshot (missing catalog self-heal)
	if not envelope and cat is None:
		tables = catmod.spec_tables(spec) if isinstance(spec, dict) else []
		c = get_client() if tables else None
		if c is not None:
			with contextlib.suppress(Exception):
				cat = catmod.snapshot(c, tables)

	# ---- lint / heal loop (offline)
	res["stage"] = "lint"
	if envelope:
		doc = copy.deepcopy(spec)
	else:
		fds, doc, _ = _lint_compile(spec, cat)
		if _has_error(fds) and heal != "off":
			try:
				from . import heal as healmod
			except ImportError:
				healmod = None
			if healmod is not None:
				if heal == "propose":
					res["needs_decision"] = list(healmod.suggest(lint_spec(spec, cat), spec, cat))
				else:
					h = healmod.heal_spec(spec, cat, max_rounds=max_rounds, allow="safe")
					spec = h["spec"]
					res["spec"] = spec
					res["rounds"] = int(h.get("rounds") or 0)
					res["healed"] = list(h.get("applied") or [])
					res["needs_decision"] = list(h.get("proposed") or [])
					res["workbook"]["id"], res["workbook"]["title"] = _wb_ident(spec)
					fds, doc, _ = _lint_compile(spec, cat)
		res["findings"] = fds
		if _has_error(fds):
			res["ok"] = False
			return res
	res["stage"] = "compile"
	res["workbook"]["id"], res["workbook"]["title"] = _wb_ident(doc)
	wb_id, title = res["workbook"]["id"], res["workbook"]["title"]
	res["dashboards"] = _dashboards(doc, {})

	c = get_client()
	if c is None:
		res["ok"] = True  # offline: lint/compile only
		return res

	# ---- plan
	res["stage"] = "plan"
	try:
		plan = plan_apply(doc, c, adopt=adopt, renames=renames)
	except FinsightsError as e:
		res["error"] = _error(e, "plan")
		res["needs_decision"] += _plan_alternatives(e, spec, title, c)
		return res
	actions = [{"op": a.op, "doctype": a.doctype, "title": a.title, "live_name": a.live_name} for a in plan.actions]
	res["plan"] = {"creates": sum(a["op"] == "create" for a in actions), "updates": sum(a["op"] == "update" for a in actions),
		"orphans": sum(a["op"] == "orphan" for a in actions), "actions": actions}
	res["workbook"]["live_name"] = plan.workbook_live_name
	res["dashboards"] = _dashboards(doc, plan.name_map)
	token = make_token(wb_id, title, actions, prune, adopt)
	res["confirm"] = token
	needs_confirm = bool(prune or plan.title_matched)

	if not write:
		try:
			dry = apply_plan(plan, doc, c, write=False, prune=prune)
		except FinsightsError as e:
			res["confirm"] = None
			res["error"] = _error(e, "plan")
			return res
		res["plan"]["lines"] = list(dry.lines)
		res["ok"] = True
		return res

	if needs_confirm and confirm != token:
		what = "prune" if prune else "adopting an unmarked workbook"
		res["error"] = {"exit_code": 3, "partial_lines": [], "stage": "plan",
			"message": ("confirm token required: run a dry run first" if not confirm else "confirm token does not match the current plan; run a dry run again")
				+ f" ({what} is destructive; pass confirm=<token from the dry run>)"}
		return res

	# ---- apply (idempotent; retry after a partial failure)
	res["stage"] = "apply"
	partial, out, attempts = [], None, 0
	while True:
		attempts += 1
		try:
			with contextlib.redirect_stderr(io.StringIO()):
				out = apply_plan(plan, doc, c, write=True, prune=prune)
			break
		except FinsightsError as e:
			partial += list(getattr(e, "partial_lines", []) or [])
			status = getattr(e, "status", None)
			retryable = e.exit_code == 4 and (status is None or status >= 500) and attempts <= MAX_APPLY_RETRIES
			if not retryable:
				err = _error(e, "apply")
				err["partial_lines"] = partial
				res["error"] = err
				return res
			try:
				plan = plan_apply(doc, c, adopt=adopt, renames=renames)
			except FinsightsError as e2:
				err = _error(e2, "apply")
				err["partial_lines"] = partial
				res["error"] = err
				return res
	lines = out.lines
	if partial and lines == ["no changes"]:
		lines = []
	res["applied"] = {"changed": bool(out.changed or partial), "lines": partial + lines, "names": out.names}
	res["workbook"]["live_name"] = plan.name_map.get(doc["doc"]["name"]) or plan.workbook_live_name or \
		(out.names.get("workbook") or {}).get(title)
	name_map = {}
	for a in plan.actions:
		if a.live_name:
			name_map[a.key] = a.live_name
	for kind in ("dashboard",):
		for t, n in (out.names.get(kind) or {}).items():
			for key, d in (doc["dependencies"].get("dashboards") or {}).items():
				if d.get("title") == t:
					name_map[key] = n
	res["dashboards"] = _dashboards(doc, name_map)

	# ---- verify
	if verify:
		res["stage"] = "verify"
		try:
			vf = _fd(verify_doc(doc, c, min_rows=min_rows))
		except FinsightsError as e:
			res["error"] = _error(e, "verify")
			return res
		bad = _has_error(vf)
		res["verify"] = {"ok": not bad, "findings": vf}
		if bad:
			res["needs_decision"] += _verify_fixes(vf)
			return res
	res["stage"] = "done"
	res["ok"] = True
	return res


# ------------------------------------------------------------------ build_many
def build_many(specs, *, stop_on_error=False, max_items=20, **kw):
	summary = {"total": len(specs) if isinstance(specs, (list, tuple)) else 0, "ok": 0, "failed": 0, "skipped": 0}

	def refuse(msg):
		return {"ok": False, "items": [], "summary": summary,
			"error": {"exit_code": 2, "message": msg, "partial_lines": [], "stage": "load"}}

	if not isinstance(specs, (list, tuple)):
		return refuse("specs must be a list")
	if len(specs) > max_items:
		return refuse(f"{len(specs)} specs exceed max_items={max_items}; nothing was run")
	loaded, ids, titles = [], {}, {}
	for i, s in enumerate(specs):
		try:
			d = _load(s)
		except Exception:
			loaded.append(s)
			continue
		loaded.append(d)
		wid, title = _wb_ident(d)
		for seen, val, what in ((ids, wid, "id"), (titles, title, "title")):
			if val and val in seen:
				return refuse(f"items {seen[val]} and {i} share workbook {what} {val!r}; nothing was run")
			if val:
				seen[val] = i
	confirms = kw.pop("confirm", None)
	items = []
	stopped = False
	for d in loaded:
		if stopped:
			summary["skipped"] += 1
			items.append({**_blank(kw.get("write", False)), "stage": "skipped", "skipped": True})
			continue
		kw2 = dict(kw)
		if isinstance(confirms, dict):
			wid, title = _wb_ident(d) if isinstance(d, dict) else ("", "")
			kw2["confirm"] = confirms.get(wid, confirms.get(title))
		else:
			kw2["confirm"] = confirms
		r = build(d, **kw2)
		items.append(r)
		if r["ok"]:
			summary["ok"] += 1
		else:
			summary["failed"] += 1
			stopped = stop_on_error
	return {"ok": summary["failed"] == 0 and summary["skipped"] == 0, "items": items, "summary": summary}


# ------------------------------------------------------------------ diff
def _jsonish(v):
	if isinstance(v, str) and v.strip()[:1] in ("[", "{"):
		with contextlib.suppress(ValueError):
			return json.loads(v)
	return v


def _normalise_env(env):
	"""{kind: {title: normalised doc}} with names/ids replaced by kind:title."""
	deps = env.get("dependencies") or {}
	names = {}
	for kind in KINDS:
		for key, d in (deps.get(kind) or {}).items():
			names[key] = f"{kind}:{d.get('title', key)}"
			if d.get("name"):
				names[d["name"]] = names[key]
	pat = re.compile(r"`([^`]+)`")

	def norm(v):
		if isinstance(v, dict):
			return {names.get(k, k): norm(x) for k, x in v.items() if k not in ("name", "workbook", "moved")}
		if isinstance(v, list):
			return [norm(x) for x in v]
		if isinstance(v, str):
			if v in names:
				return names[v]
			return pat.sub(lambda m: "`%s`" % names.get(m.group(1), m.group(1)), v)
		return v

	out = {}
	for kind in KINDS:
		out[kind] = {}
		for key, d in (deps.get(kind) or {}).items():
			out[kind][d.get("title", key)] = norm({k: _jsonish(x) for k, x in d.items()})
	return out


def diff(a, b):
	try:
		ea, eb = _load(a), _load(b)
		for e in (ea, eb):
			if not _is_envelope(e):
				raise FinsightsError("diff expects compiled envelopes (dict with `dependencies`)", 2)
	except Exception as e:
		return {"added": {}, "removed": {}, "changed": {}, "same": False, "error": _error(e, "load")}
	na, nb = _normalise_env(ea), _normalise_env(eb)
	added, removed, changed = {}, {}, {}
	for kind in KINDS:
		ta, tb = set(na[kind]), set(nb[kind])
		if tb - ta:
			added[kind] = sorted(tb - ta)
		if ta - tb:
			removed[kind] = sorted(ta - tb)
		for t in sorted(ta & tb):
			da, db = na[kind][t], nb[kind][t]
			fields = sorted(f for f in set(da) | set(db) if json.dumps(da.get(f), sort_keys=True) != json.dumps(db.get(f), sort_keys=True))
			if fields:
				changed.setdefault(kind, []).append({"title": t, "fields": fields})
	return {"added": added, "removed": removed, "changed": changed, "same": not (added or removed or changed)}
