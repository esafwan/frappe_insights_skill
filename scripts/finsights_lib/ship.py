"""Ship a compiled workbook inside an app: template hook layout (3.15.1) or standard layout (forward)."""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field
from pathlib import Path

from . import desk as deskmod
from .catalog import spec_tables
from .common import FinsightsError
from .compile import dumps

HOOK_LINE = 'insights_workbooks = "workbook_templates"'
HOOK_RE = re.compile(r"^\s*insights_workbooks\s*=", re.M)
DEFAULT_DIR = "workbook_templates"
DEPRECATED = "WARN deprecated hook (migrate.py:62); removed next major"


@dataclass
class ShipResult:
	changed: bool = False
	lines: list = field(default_factory=list)
	warnings: list = field(default_factory=list)
	files: list = field(default_factory=list)  # absolute paths touched (or to be touched)


def scrub(s: str) -> str:
	return s.replace("-", "_").replace(" ", "_").lower()


def _pkg_dir(app_path) -> Path:
	root = Path(app_path).resolve()
	if (root / "hooks.py").is_file():
		return root
	cand = root / root.name
	if (cand / "hooks.py").is_file():
		return cand
	raise FinsightsError(f"no hooks.py under {root} (expected <app>/<app>/hooks.py)", 2)


def _manifest(spec: dict) -> dict:
	wb = spec["workbook"]
	m = {
		"description": wb.get("description") or "",
		"required_apps": list(wb.get("required_apps") or []),
		"source_doctypes": spec_tables(spec),
		"title": wb["title"],
		"version": int(wb.get("version") or 1),
	}
	if wb.get("module"):
		m["module"] = wb["module"]
	return m


def declared_hook(text: str):
	"""Return (state, value): ('none'|'literal'|'computed', str|None). Uses ast; regex fallback if unparsable."""
	try:
		tree = ast.parse(text)
	except SyntaxError:
		tree = None
	if tree is None:
		ms = list(re.finditer(r"""^[ \t]*insights_workbooks[ \t]*=[ \t]*(?:"([^"\n]*)"|'([^'\n]*)')[ \t]*(?:#.*)?$""", text, re.M))
		if ms:
			m = ms[-1]
			return "literal", m.group(1) if m.group(1) is not None else m.group(2)
		if HOOK_RE.search(text):
			return "computed", None
		return "none", None
	found = None
	for node in tree.body:
		targets, value = [], None
		if isinstance(node, ast.Assign):
			targets, value = node.targets, node.value
		elif isinstance(node, (ast.AnnAssign, ast.AugAssign)):
			targets, value = [node.target], node.value
		if any(isinstance(t, ast.Name) and t.id == "insights_workbooks" for t in targets):
			if isinstance(node, ast.Assign) and isinstance(value, ast.Constant) and isinstance(value.value, str):
				found = ("literal", value.value)
			else:
				found = ("computed", None)
	return found or ("none", None)


def _check_templates_dir(templates_dir: str):
	if (not templates_dir or "/" in templates_dir or "\\" in templates_dir or ".." in templates_dir
			or templates_dir.startswith(".")):
		raise FinsightsError(f"invalid --templates-dir {templates_dir!r}: must be a single directory name", 2)


def _put(res: ShipResult, path: Path, text: str, write: bool, root: Path):
	rel = path.relative_to(root.parent) if root.parent in path.parents else path
	old = path.read_text(encoding="utf-8") if path.is_file() else None
	if old == text:
		return
	res.changed = True
	res.files.append(str(path))
	res.lines.append(f"{'~ update' if old is not None else '+ create'} {rel}")
	if write:
		path.parent.mkdir(parents=True, exist_ok=True)
		path.write_text(text, encoding="utf-8")


def ship(compiled_template: dict, spec: dict, app_path, write: bool = False, standard: bool = False,
		forward: bool = False, templates_dir=None) -> ShipResult:
	if standard and not forward:
		raise FinsightsError("--standard needs --forward: the standard layout is not loadable on Insights 3.15.1", 2)
	pkg = _pkg_dir(app_path)
	res = ShipResult()
	if standard:
		module = (spec["workbook"].get("module") or "")
		if not module:
			raise FinsightsError("workbook.module is required for --standard", 2)
		slug = scrub(compiled_template["name"])
		mdir = pkg / scrub(module)
		_put(res, mdir / "insights_workbook" / slug / f"{slug}.json", dumps(compiled_template), write, pkg)
		dash = deskmod.commands(spec, None, tier="island")[0] + "\n"
		_put(res, mdir / f"{scrub(module)}_dashboard" / slug / f"{slug}.json", dash, write, pkg)
	else:
		slug = compiled_template["name"]
		hooks = pkg / "hooks.py"
		text = hooks.read_text(encoding="utf-8")
		if templates_dir is not None:
			_check_templates_dir(templates_dir)
		state, declared = declared_hook(text)
		if state == "computed":
			raise FinsightsError("hooks.py assigns insights_workbooks to a non-literal value; cannot verify "
				"where templates are loaded from. Make it a plain string literal.", 3)
		if state == "literal" and declared != DEFAULT_DIR:
			if templates_dir != declared:
				raise FinsightsError(f'hooks.py already declares insights_workbooks = "{declared}", not "{DEFAULT_DIR}". '
					f"Change the hook, or re-run with --templates-dir {declared}.", 3)
		elif templates_dir is not None and templates_dir != DEFAULT_DIR:
			raise FinsightsError(f"--templates-dir {templates_dir} does not match the hook "
				f'(declared: "{declared or DEFAULT_DIR}")', 3)
		use_dir = declared if state == "literal" else DEFAULT_DIR
		tdir = pkg / use_dir / slug
		_put(res, tdir / "manifest.json", dumps(_manifest(spec)), write, pkg)
		_put(res, tdir / "workbook.json", dumps(compiled_template), write, pkg)
		if state == "none":
			res.changed = True
			res.files.append(str(hooks))
			res.lines.append(f"~ update {hooks.relative_to(pkg.parent)}: append {HOOK_LINE}")
			if write:
				hooks.write_text(text + ("" if text.endswith("\n") or not text else "\n") + HOOK_LINE + "\n", encoding="utf-8")
		res.warnings.append(DEPRECATED)
	if not res.changed:
		res.lines.append("no changes")
	return res
