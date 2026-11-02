import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from finsights_lib import yamlish

ROOT = Path(__file__).resolve().parents[1]
LIB = ROOT / "scripts"
FIXTURES = [ROOT / "tests" / "fixtures" / "ury_overview.yaml", *sorted((ROOT / "examples").glob("*.yaml"))]

SRC = "a: no\nb: Y\nc: 08\nd: 007\ne: 2026-10-21\nf: [on, 0x1f, 1_000, 1e3, 7, 1.5]\ng: \"08\"\n"

_CODE = (
	"import sys, json\n"
	"sys.path.insert(0, %r)\n"
	"from finsights_lib import yamlish\n"
	"d = yamlish.loads(%r)\n"
	"print(json.dumps(d, sort_keys=True))\n"
	"print('yaml' in sys.modules)\n"
)


def _run(env_extra, hide_yaml):
	env = {k: v for k, v in os.environ.items() if k != "FINSIGHTS_YAML"}
	env.update(env_extra)
	code = _CODE % (str(LIB), SRC)
	if hide_yaml:
		code = "import sys\nsys.modules['yaml'] = None\n" + code.replace("print('yaml' in sys.modules)", "print(False)")
	out = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True, check=True).stdout.splitlines()
	return out


def test_same_result_with_and_without_pyyaml():
	hidden = _run({}, True)
	plain = _run({}, False)
	assert hidden[0] == plain[0]


def test_default_never_imports_pyyaml():
	assert _run({}, False)[1] == "False"


def test_expected_values():
	d = json.loads(_run({}, True)[0])
	assert d["a"] == "no" and d["b"] == "Y" and d["c"] == "08" and d["d"] == "007"
	assert d["e"] == "2026-10-21" and d["g"] == "08"
	assert d["f"] == ["on", "0x1f", "1_000", "1e3", 7, 1.5]


def test_env_var_default_off_and_pyyaml_opt_in():
	pytest.importorskip("yaml")
	assert yamlish.loads("a: no\n")["a"] == "no"
	assert yamlish.loads("a: no\n", engine="pyyaml")["a"] is False


@pytest.mark.parametrize("path", FIXTURES, ids=lambda p: p.name)
def test_fixtures_equal_pyyaml_on_safe_subset(path):
	yaml = pytest.importorskip("yaml")

	# Plain yaml.safe_load is YAML 1.1: the bare key `on:` (join condition) becomes the boolean
	# True and yes/no/on/off become booleans. Compare against a loader where only true/false are
	# booleans, which is what the built-in parser implements.
	class Yaml12Loader(yaml.SafeLoader):
		pass

	resolvers = {k: [r for r in v if r[0] != "tag:yaml.org,2002:bool"]
		for k, v in yaml.SafeLoader.yaml_implicit_resolvers.items()}
	Yaml12Loader.yaml_implicit_resolvers = resolvers
	Yaml12Loader.add_implicit_resolver(
		"tag:yaml.org,2002:bool", __import__("re").compile(r"^(?:true|True|false|False)$"), list("tTfF"))
	text = path.read_text(encoding="utf-8")
	assert yamlish.load(path) == yaml.load(text, Loader=Yaml12Loader)  # noqa: S506 (safe subclass)


def test_plain_pyyaml_misreads_on_key_but_builtin_does_not():
	# Regression guard for the join `on:` key: this is why PyYAML must not be the default parser.
	yaml = pytest.importorskip("yaml")
	text = "joins:\n  - on: [parent, name]\n"
	assert list(yaml.safe_load(text)["joins"][0]) == [True]
	assert list(yamlish.loads(text)["joins"][0]) == ["on"]
