"""Ship a compiled workbook inside an app: template hook layout (3.15.1) or standard layout (forward)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from . import desk as deskmod
from .catalog import spec_tables
from .common import FinsightsError
from .compile import dumps

HOOK_LINE = 'insights_workbooks = "workbook_templates"'
HOOK_RE = re.compile(r"^\s*insights_workbooks\s*=", re.M)
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
		forward: bool = False) -> ShipResult:
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
		tdir = pkg / "workbook_templates" / slug
		_put(res, tdir / "manifest.json", dumps(_manifest(spec)), write, pkg)
		_put(res, tdir / "workbook.json", dumps(compiled_template), write, pkg)
		hooks = pkg / "hooks.py"
		text = hooks.read_text(encoding="utf-8")
		if not HOOK_RE.search(text):
			res.changed = True
			res.files.append(str(hooks))
			res.lines.append(f"~ update {hooks.relative_to(pkg.parent)}: append {HOOK_LINE}")
			if write:
				hooks.write_text(text + ("" if text.endswith("\n") or not text else "\n") + HOOK_LINE + "\n", encoding="utf-8")
		res.warnings.append(DEPRECATED)
	if not res.changed:
		res.lines.append("no changes")
	return res
