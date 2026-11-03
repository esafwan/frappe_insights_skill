"""Argparse wiring for finsights. Commands: lint compile catalog plan apply verify desk ship fmt, plus the
API-backed build bulk export review heal. Exit codes: 0 ok, 1 lint/verify/check failure, 2 usage/parse, 3 live conflict,
4 auth/network, 5 `plan --exit-code` with pending changes. Secrets are env-only and never printed."""

from __future__ import annotations

import argparse
import dataclasses
import importlib
import json
import os
import sys
from pathlib import Path

from . import catalog as catmod
from . import desk as deskmod
from . import ship as shipmod
from . import spec as specmod
from .common import FinsightsError
from .compile import compile_spec, dumps
from .lint import exit_code_for, lint_spec


class _Usage(argparse.ArgumentParser):
	def error(self, message):
		raise FinsightsError(f"{self.prog}: {message}", 2)


def _global_flags(p, default):
	p.add_argument("--spec", metavar="FILE", default=default, help="spec file (.json/.yaml/.yml)")
	p.add_argument("--json", action="store_true", default=default, help="machine output")
	p.add_argument("--write", action="store_true", default=default, help="apply changes (default is dry-run)")


def build_parser() -> argparse.ArgumentParser:
	p = _Usage(prog="finsights", description="Deterministic Frappe Insights v3 workbooks from a spec")
	_global_flags(p, None)
	p.set_defaults(json=False, write=False)
	sub = p.add_subparsers(dest="cmd", required=True, parser_class=_Usage)

	def add(name, help_):
		sp = sub.add_parser(name, help=help_)
		_global_flags(sp, argparse.SUPPRESS)
		return sp

	sp = add("lint", "check a spec against the lint rules")
	sp.add_argument("--strict", action="store_true", help="warnings fail too")
	sp.add_argument("--only", default=None, help="comma list of rule ids")
	sp.add_argument("--catalog", default=None, metavar="FILE|live")

	sp = add("compile", "compile a spec to workbook JSON")
	sp.add_argument("--format", choices=["template", "standard"], default="template")
	sp.add_argument("-o", "--output", default=None, metavar="FILE")
	sp.add_argument("--check", default=None, metavar="FILE", help="exit 1 when FILE differs from the compile")
	sp.add_argument("--catalog", default=None, metavar="FILE")

	sp = add("catalog", "snapshot table columns from the live site")
	sp.add_argument("--tables", default=None, help="comma list of DocTypes (default: those the spec reads)")
	sp.add_argument("--data-source", default=os.environ.get("INSIGHTS_DATA_SOURCE") or "Site DB")
	sp.add_argument("-o", "--output", default=None, metavar="FILE")

	for name, h in (("plan", "diff the spec against the live site"), ("apply", "create/update on the live site"),
			("verify", "read back and run every query")):
		sp = add(name, h)
		sp.add_argument("--catalog", default=None, metavar="FILE")
		if name != "verify":
			sp.add_argument("--adopt", action="store_true")
			sp.add_argument("--rename", action="append", default=[], metavar="ID=OLD TITLE")
		if name == "plan":
			sp.add_argument("--exit-code", action="store_true", help="exit 5 when changes are pending")
		if name == "apply":
			sp.add_argument("--prune", action="store_true")
			sp.add_argument("--materialize-data-query", action="store_true")
		if name == "verify":
			sp.add_argument("--min-rows", type=int, default=1)
			sp.add_argument("--deep", action="store_true",
				help="also run each chart's own data query (source + chart ops), reporting V06/V07")

	sp = add("desk", "print fdesk commands (or the desk Dashboard doc) for the spec's desk section")
	sp.add_argument("--tier", choices=["url", "island"], default="url")
	sp.add_argument("--dashboard", default=None, metavar="NAME", help="live dashboard name")
	sp.add_argument("--app", default="<app path>")

	sp = add("ship", "write the workbook into an app")
	sp.add_argument("--app", default=".", metavar="PATH")
	sp.add_argument("--standard", action="store_true")
	sp.add_argument("--forward", action="store_true", help="acknowledge the layout is not loadable on 3.15.1")
	sp.add_argument("--catalog", default=None, metavar="FILE")
	sp.add_argument("--templates-dir", default=None, metavar="DIR",
		help="write into this directory when the app's hooks.py already declares insights_workbooks = \"DIR\"")

	sp = add("fmt", "convert a spec to JSON")
	sp.add_argument("--to", choices=["json"], required=True)
	sp.add_argument("-o", "--output", default=None, metavar="FILE")

	sp = add("build", "one-shot lint/heal/plan/apply/verify of a spec (dry-run unless --write)")
	_build_flags(sp)
	sp.add_argument("--confirm", default=None, metavar="TOKEN")

	sp = add("bulk", "build many specs from a JSON list or {\"specs\": [...]}")
	sp.add_argument("file", metavar="FILE.json")
	_build_flags(sp)
	sp.add_argument("--confirm", action="append", default=[], metavar="WORKBOOK_ID=TOKEN")
	sp.add_argument("--stop-on-error", action="store_true")
	sp.add_argument("--max-items", type=int, default=20)

	sp = add("export", "export a live workbook (or the index of all) as JSON")
	g = sp.add_mutually_exclusive_group(required=True)
	g.add_argument("--workbook", default=None, metavar="REF")
	g.add_argument("--all", action="store_true")
	sp.add_argument("-o", "--output", default=None, metavar="FILE")

	sp = add("review", "run every chart query of a live workbook and report")
	sp.add_argument("--workbook", required=True, metavar="REF")
	sp.add_argument("--min-rows", type=int, default=1)

	sp = add("heal", "offline: apply safe fixes to a spec, list proposed ones")
	sp.add_argument("-o", "--output", default=None, metavar="FILE")
	sp.add_argument("--max-rounds", type=int, default=3)
	sp.add_argument("--catalog", default=None, metavar="FILE")
	return p


def _build_flags(sp):
	sp.add_argument("--heal", choices=["safe", "propose", "off"], default="safe")
	sp.add_argument("--max-rounds", type=int, default=3)
	sp.add_argument("--adopt", action="store_true")
	sp.add_argument("--prune", action="store_true")
	sp.add_argument("--catalog", default=None, metavar="FILE")
	sp.add_argument("--no-verify", action="store_true")
	sp.add_argument("--min-rows", type=int, default=1)


# ---------------------------------------------------------------- helpers

def _out(s: str):
	sys.stdout.write(s if s.endswith("\n") else s + "\n")


def _err(s: str):
	sys.stderr.write(s if s.endswith("\n") else s + "\n")


def _load(args):
	if not args.spec:
		raise FinsightsError("--spec FILE is required", 2)
	return specmod.load_spec(args.spec)


def _client():
	mod = importlib.import_module("finsights_lib.client")
	return mod.Client.from_env()


def _catalog(args, spec, allow_live=False):
	"""None when no catalog is available."""
	c = getattr(args, "catalog", None)
	if c == "live":
		if not allow_live:
			raise FinsightsError("--catalog live is only valid for lint", 2)
		return catmod.snapshot(_client(), catmod.spec_tables(spec))
	if c:
		return catmod.load(c)
	rel = spec.get("catalog") if isinstance(spec.get("catalog"), str) else None
	base = Path(args.spec).resolve().parent if args.spec else Path.cwd()
	p = (base / rel) if rel else Path.cwd() / "finsights_catalog.json"
	if rel and not p.is_file():
		raise FinsightsError(f"catalog file {p} (from spec) not found", 2)
	return catmod.load(p) if p.is_file() else None


def _show_findings(findings, as_json):
	if as_json:
		return
	for f in findings:
		_err(f"{f.severity} {f.rule} {f.path}: {f.message}" + (f" (hint: {f.fix_hint})" if f.fix_hint else ""))


def _compiled(args, fmt="template"):
	"""Lint (errors stop with exit 1) then compile. Returns (spec, doc, rc)."""
	spec = _load(args)
	cat = _catalog(args, spec)
	findings = lint_spec(spec, cat)
	if exit_code_for(findings):
		_show_findings(findings, False)
		return spec, None, 1
	doc, extra = compile_spec(spec, cat, fmt)
	for f in extra:
		_err(f"{f.severity} {f.rule} {f.path}: {f.message}")
	return spec, doc, 0


def _asdict(o):
	return dataclasses.asdict(o) if dataclasses.is_dataclass(o) else dict(vars(o))


def _renames(items):
	out = {}
	for it in items or []:
		if "=" not in it:
			raise FinsightsError(f"--rename expects id=Old Title, got {it!r}", 2)
		k, v = it.split("=", 1)
		out[k.strip()] = v
	return out


# ---------------------------------------------------------------- commands

def cmd_lint(args) -> int:
	spec = _load(args)
	cat = _catalog(args, spec, allow_live=True)
	only = [s for s in args.only.split(",") if s.strip()] if args.only else None
	findings = lint_spec(spec, cat, only)
	rc = exit_code_for(findings, args.strict)
	if args.json:
		_out(json.dumps({"findings": [f.to_dict() for f in findings], "ok": rc == 0}, indent=1, sort_keys=True))
	else:
		for f in findings:
			_out(f"{f.severity} {f.rule} {f.path}: {f.message}" + (f" (hint: {f.fix_hint})" if f.fix_hint else ""))
		n_e = sum(f.severity == "error" for f in findings)
		n_w = sum(f.severity == "warn" for f in findings)
		_out("ok" if not findings else f"{n_e} error(s), {n_w} warning(s), {len(findings) - n_e - n_w} info")
	return rc


def cmd_compile(args) -> int:
	spec, doc, rc = _compiled(args, args.format)
	if rc:
		return rc
	text = dumps(doc)
	if args.check:
		p = Path(args.check)
		cur = p.read_text(encoding="utf-8") if p.is_file() else None
		if cur != text:
			_err(f"compile --check: {args.check} differs from the compiled output" if cur is not None
				else f"compile --check: {args.check} does not exist")
			return 1
		_out("ok" if not args.json else json.dumps({"ok": True}))
		return 0
	if args.output:
		Path(args.output).write_text(text, encoding="utf-8")
		_out(f"wrote {args.output}")
	else:
		sys.stdout.write(text)
	return 0


def cmd_catalog(args) -> int:
	tables = [t.strip() for t in args.tables.split(",") if t.strip()] if args.tables else None
	if tables is None:
		tables = catmod.spec_tables(_load(args))
	if not tables:
		raise FinsightsError("no tables: pass --tables or --spec", 2)
	text = catmod.dump(catmod.snapshot(_client(), tables, args.data_source))
	if args.output:
		Path(args.output).write_text(text, encoding="utf-8")
		_out(f"wrote {args.output}")
	else:
		sys.stdout.write(text)
	return 0


def _plan(args):
	spec, doc, rc = _compiled(args)
	if rc:
		return None, None, None, rc
	apply_mod = importlib.import_module("finsights_lib.apply")
	client = _client()
	plan = apply_mod.plan_apply(doc, client, adopt=getattr(args, "adopt", False), renames=_renames(getattr(args, "rename", [])))
	return doc, plan, (apply_mod, client), 0


def _plan_lines(plan):
	lines = []
	for a in plan.actions:
		if a.op == "noop":
			continue
		sym = {"create": "+ create", "update": "~ update", "orphan": "- orphan"}.get(a.op, a.op)
		extra = f" fields: {', '.join(sorted(a.fields))}" if a.op == "update" and a.fields else ""
		lines.append(f"{sym} {a.doctype} \"{a.title}\"{extra}")
	return lines or ["no changes"]


def cmd_plan(args) -> int:
	doc, plan, _, rc = _plan(args)
	if rc:
		return rc
	lines = _plan_lines(plan)
	pending = lines != ["no changes"]
	if args.json:
		_out(json.dumps({"actions": [_asdict(a) for a in plan.actions], "name_map": plan.name_map, "pending": pending},
			indent=1, sort_keys=True, default=str))
	else:
		for ln in lines:
			_out(ln)
	return 5 if (args.exit_code and pending) else 0


def cmd_apply(args) -> int:
	if args.materialize_data_query:
		_err("note: --materialize-data-query is not implemented; ignored")
	doc, plan, (apply_mod, client), rc = _plan(args)
	if rc:
		return rc
	res = apply_mod.apply_plan(plan, doc, client, write=args.write, prune=args.prune)
	if args.json:
		_out(json.dumps({"changed": res.changed, "lines": res.lines, "names": getattr(res, "names", {}), "write": args.write},
			indent=1, sort_keys=True, default=str))
	else:
		for ln in res.lines:
			_out(ln)
		if res.changed and not args.write:
			_out("dry-run: pass --write to apply")
	return 0


def cmd_verify(args) -> int:
	spec, doc, rc = _compiled(args)
	if rc:
		return rc
	verify_mod = importlib.import_module("finsights_lib.verify")
	client = _client()
	findings = verify_mod.verify(doc, client, min_rows=args.min_rows, **({"deep": True} if getattr(args, "deep", False) else {}))
	bad = [f for f in findings if f.severity == "error"]
	urls = []
	if not bad:
		apply_mod = importlib.import_module("finsights_lib.apply")
		nm = apply_mod.plan_apply(doc, client).name_map
		urls = [f"/insights/dashboards/{nm.get(n, n)}" for n in doc["dependencies"]["dashboards"]]
	if args.json:
		_out(json.dumps({"findings": [f.to_dict() for f in findings], "urls": urls, "ok": not bad}, indent=1, sort_keys=True))
	else:
		for f in findings:
			_out(f"{f.severity} {f.rule} {f.path}: {f.message}")
		for u in urls:
			_out(u)
		if not findings:
			_out("ok")
	return 1 if bad else 0


def cmd_desk(args) -> int:
	spec = _load(args)
	name = args.dashboard
	if args.tier == "url" and not name and os.environ.get("INSIGHTS_URL"):
		try:
			_, doc, rc = _compiled(args)
			if doc:
				apply_mod = importlib.import_module("finsights_lib.apply")
				nm = apply_mod.plan_apply(doc, _client()).name_map
				first = next(iter(doc["dependencies"]["dashboards"]), None)
				name = nm.get(first) if first else None
		except (FinsightsError, ImportError) as e:
			_err(f"note: could not resolve the live dashboard name ({e}); using a placeholder")
	elif args.tier == "url" and not name:
		_err("note: no --dashboard and INSIGHTS_URL unset; using a placeholder name")
	cmds = deskmod.commands(spec, name, app_hint=args.app, tier=args.tier)
	if args.json:
		_out(json.dumps({"tier": args.tier, "commands": cmds}, indent=1))
	else:
		for c in cmds:
			_out(c)
	return 0


def cmd_ship(args) -> int:
	fmt = "standard" if args.standard else "template"
	if args.standard and not args.forward:
		raise FinsightsError("--standard needs --forward: the standard layout is not loadable on Insights 3.15.1", 2)
	spec, doc, rc = _compiled(args, fmt)
	if rc:
		return rc
	res = shipmod.ship(doc, spec, args.app, write=args.write, standard=args.standard, forward=args.forward,
		templates_dir=args.templates_dir)
	for w in res.warnings:
		_err(w)
	if args.json:
		_out(json.dumps({"changed": res.changed, "lines": res.lines, "write": args.write, "warnings": res.warnings},
			indent=1, sort_keys=True))
	else:
		for ln in res.lines:
			_out(ln)
		if res.changed and not args.write:
			_out("dry-run: pass --write to apply")
	return 0


def cmd_fmt(args) -> int:
	spec = _load(args)
	text = json.dumps(spec, indent=1, ensure_ascii=False) + "\n"
	if args.output:
		Path(args.output).write_text(text, encoding="utf-8")
		_out(f"wrote {args.output}")
	else:
		sys.stdout.write(text)
	return 0


def _parse_text(text, label):
	text = text.lstrip("\ufeff")
	try:
		data = json.loads(text)
	except json.JSONDecodeError:
		try:
			from . import yamlish
			data = yamlish.loads(text)
		except Exception as e:
			raise FinsightsError(f"cannot parse {label} as JSON or YAML: {e}", 2)
	return data


def _load_any(path):
	"""Spec from a file, or stdin when path is '-'."""
	if path == "-":
		data = _parse_text(sys.stdin.read(), "stdin")
		if not isinstance(data, dict):
			raise FinsightsError("spec on stdin must be a mapping", 2)
		return data
	return specmod.load_spec(path)


def _cat_file(args):
	c = getattr(args, "catalog", None)
	if c == "live":
		raise FinsightsError("--catalog live is only valid for lint", 2)
	return catmod.load(c) if c else None


def _json_text(o):
	return json.dumps(o, indent=1, sort_keys=True, default=str, ensure_ascii=False)


def _build_rc(res):
	if res.get("ok"):
		return 0
	err = res.get("error") or {}
	code = err.get("exit_code")
	if code in (2, 3, 4):
		return code
	return 1


def _fix_lines(label, fixes):
	for f in fixes or []:
		yield f"{label}: {f.get('rule', '')} {f.get('path', '')}: {f.get('description', '')}".rstrip()
		for a in f.get("alternatives") or []:
			yield f"    alternative: {a.get('description', '')}"


def _summary(res, indent=""):
	wb = res.get("workbook") or {}
	lines = [f"{indent}{wb.get('title') or wb.get('id') or '-'}: stage={res.get('stage')} ok={res.get('ok')}"
		f" findings={len(res.get('findings') or [])}"]
	plan = res.get("plan")
	if plan:
		lines.append(f"{indent}plan: {plan.get('creates', 0)} create, {plan.get('updates', 0)} update, "
			f"{plan.get('orphans', 0)} orphan")
	for f in res.get("findings") or []:
		lines.append(f"{indent}{f.get('severity')} {f.get('rule')} {f.get('path')}: {f.get('message')}")
	lines += [indent + ln for ln in _fix_lines("healed", res.get("healed"))]
	lines += [indent + ln for ln in _fix_lines("needs_decision", res.get("needs_decision"))]
	for d in res.get("dashboards") or []:
		lines.append(f"{indent}dashboard: {d.get('path') or d.get('live_name') or d.get('title')}")
	err = res.get("error")
	if err:
		lines.append(f"{indent}error ({err.get('exit_code')}): {err.get('message')}")
	if res.get("confirm"):
		lines.append(f"{indent}confirm: {res['confirm']}")
	return lines


def cmd_build(args) -> int:
	if not args.spec:
		raise FinsightsError("--spec FILE|- is required", 2)
	spec = _load_any(args.spec)
	cat = _cat_file(args)
	from . import api
	res = api.build(spec, catalog=cat, write=args.write, adopt=args.adopt, prune=args.prune,
		verify=not args.no_verify, heal=args.heal, max_rounds=args.max_rounds, confirm=args.confirm,
		min_rows=args.min_rows)
	if args.json:
		_out(_json_text(res))
	else:
		for ln in _summary(res):
			_out(ln)
	return _build_rc(res)


def _confirms(items):
	out = {}
	for it in items or []:
		if "=" not in it:
			raise FinsightsError(f"--confirm expects workbook_id=TOKEN, got {it!r}", 2)
		k, v = it.split("=", 1)
		out[k.strip()] = v.strip()
	return out


def cmd_bulk(args) -> int:
	p = Path(args.file)
	try:
		text = p.read_text(encoding="utf-8")
	except OSError as e:
		raise FinsightsError(f"cannot read {args.file}: {e}", 2)
	data = _parse_text(text, args.file)
	if isinstance(data, dict) and "specs" in data:
		data = data["specs"]
	if not isinstance(data, list):
		raise FinsightsError("bulk file must be a JSON list or {\"specs\": [...]}", 2)
	specs = [_load_any(str(Path(x))) if isinstance(x, str) else x for x in data]
	cat = _cat_file(args)
	from . import api
	res = api.build_many(specs, stop_on_error=args.stop_on_error, max_items=args.max_items, catalog=cat,
		write=args.write, adopt=args.adopt, prune=args.prune, verify=not args.no_verify, heal=args.heal,
		max_rounds=args.max_rounds, confirm=_confirms(args.confirm) or None, min_rows=args.min_rows)
	if args.json:
		_out(_json_text(res))
	else:
		sm = res.get("summary") or {}
		_out(f"bulk: ok={res.get('ok')} total={sm.get('total')} ok={sm.get('ok')} failed={sm.get('failed')} "
			f"skipped={sm.get('skipped')}")
		if res.get("error"):
			_out(f"error ({res['error'].get('exit_code')}): {res['error'].get('message')}")
		for it in res.get("items") or []:
			for ln in _summary(it, "  "):
				_out(ln)
	if res.get("error") and not res.get("items"):
		return _build_rc(res)
	failed = (res.get("summary") or {}).get("failed", 0)
	if res.get("ok") and not failed:
		return 0
	codes = [(i.get("error") or {}).get("exit_code") for i in res.get("items") or []]
	return next((c for c in (3, 4) if c in codes), 1)


def _write_or_print(text, output):
	if output:
		Path(output).write_text(text if text.endswith("\n") else text + "\n", encoding="utf-8")
		_out(f"wrote {output}")
	else:
		_out(text)


def cmd_export(args) -> int:
	from . import export
	client = _client()
	res = export.export_all(client) if args.all else export.export_workbook(client, args.workbook)
	_write_or_print(_json_text(res), args.output)
	return 0 if res.get("ok", True) else _build_rc(res)


def cmd_review(args) -> int:
	from . import export
	res = export.review(_client(), args.workbook, min_rows=args.min_rows)
	if args.json:
		_out(_json_text(res))
	else:
		_out(f"review {(res.get('workbook') or {}).get('title', args.workbook)}: ok={res.get('ok')}")
		for c in res.get("charts") or []:
			_out(f"  chart {c.get('title')}: rows={c.get('rows')} {'ok' if c.get('ok') else 'FAIL ' + str(c.get('error'))}")
		for d in res.get("dashboards") or []:
			_out(f"  dashboard {d.get('title')}: {d.get('path')} links_ok={d.get('links_ok')}")
		for f in res.get("findings") or []:
			_out(f"  {f.get('severity')} {f.get('rule')} {f.get('path')}: {f.get('message')}")
	return 0 if res.get("ok") else 1


def cmd_heal(args) -> int:
	if not args.spec:
		raise FinsightsError("--spec FILE|- is required", 2)
	spec = _load_any(args.spec)
	cat = _cat_file(args)
	from . import heal
	res = heal.heal_spec(spec, cat, max_rounds=args.max_rounds)
	out = {k: res.get(k) for k in ("spec", "rounds", "applied", "proposed", "remaining")}
	_write_or_print(_json_text(out), args.output)
	bad = [f for f in (res.get("remaining") or []) if f.get("severity") == "error"]
	return 1 if bad else 0


COMMANDS = {"build": cmd_build, "bulk": cmd_bulk, "export": cmd_export, "review": cmd_review, "heal": cmd_heal,
	"lint": cmd_lint, "compile": cmd_compile, "catalog": cmd_catalog, "plan": cmd_plan, "apply": cmd_apply,
	"verify": cmd_verify, "desk": cmd_desk, "ship": cmd_ship, "fmt": cmd_fmt}


def main(argv=None) -> int:
	argv = list(sys.argv[1:] if argv is None else argv)
	try:
		args = build_parser().parse_args(argv)
		return COMMANDS[args.cmd](args)
	except FinsightsError as e:
		_err(f"error: {e}")
		return e.exit_code
	except SystemExit as e:  # argparse -h / --version
		return e.code if isinstance(e.code, int) else 0
