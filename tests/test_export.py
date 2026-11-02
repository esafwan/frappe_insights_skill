import copy
import json
from pathlib import Path

from fakesite import FakeSite
from finsights_lib.apply import apply_plan, plan_apply
from finsights_lib.compile import dumps
from finsights_lib.export import export_all, export_workbook, review

GOLDEN = json.loads((Path(__file__).parent / "golden" / "ury_overview.json").read_text())
ROWS = [{"posting_date": "2026-01-01", "branch": "b", "x": 1}]


def built(**kw):
	kw.setdefault("canned_rows", ROWS)
	site = FakeSite(**kw)
	comp = copy.deepcopy(GOLDEN)
	c = site.client()
	apply_plan(plan_apply(comp, c), comp, c, write=True)
	return site, comp, c


def test_export_by_name_title_marker():
	site, comp, c = built()
	a = export_workbook(c, "1")
	assert a["ok"] and a["workbook"]["title"] == "URY Sales"
	for ref in ("URY Sales", "finsights:ury-sales", "ury-sales"):
		b = export_workbook(c, ref)
		assert b["ok"] and b["workbook"]["name"] == "1", ref
	assert a["workbook"]["marker"] == "finsights:ury-sales"


def test_ambiguous_title_lists_candidates():
	site, comp, c = built()
	site.docs["Insights Workbook"]["99"] = {"name": "99", "title": "URY Sales", "from_template": None, "modified": "t"}
	r = export_workbook(c, "URY Sales")
	assert not r["ok"] and r["error"]["exit_code"] == 3
	assert "1" in r["error"]["message"] and "99" in r["error"]["message"]


def test_unknown_ref():
	site, comp, c = built()
	r = export_workbook(c, "nope")
	assert not r["ok"] and r["error"]["exit_code"] == 2
	assert not review(c, "nope")["ok"]


def test_counts_and_no_server_fields():
	site, comp, c = built()
	r = export_workbook(c, "1")
	deps = r["compiled"]["dependencies"]
	g = GOLDEN["dependencies"]
	assert len(deps["queries"]) == len(g["queries"])
	assert len(deps["charts"]) == len(g["charts"])
	assert len(deps["dashboards"]) == len(g["dashboards"])
	assert r["summary"] == {"queries": len(g["queries"]), "charts": len(g["charts"]),
		"dashboards": len(g["dashboards"]), "filters": 2}
	for kind in ("queries", "charts", "dashboards"):
		for d in deps[kind].values():
			assert not ({"data_query", "linked_charts", "owner", "modified", "idx", "docstatus", "creation"} & set(d))
	assert r["lossy"] == []


def test_envelope_shape_and_parsed_json():
	site, comp, c = built()
	e = export_workbook(c, "1")["compiled"]
	assert set(e) == {"version", "type", "name", "doc", "dependencies"}
	assert e["version"] == "1.0" and e["type"] == "Workbook"
	assert set(e["dependencies"]) == {"folders", "queries", "charts", "dashboards"}
	q = next(iter(e["dependencies"]["queries"].values()))
	assert isinstance(q["operations"], list)
	assert isinstance(next(iter(e["dependencies"]["dashboards"].values()))["items"], list)
	assert isinstance(next(iter(e["dependencies"]["charts"].values()))["config"], dict)


def test_live_names_and_names_map():
	site, comp, c = built()
	r = export_workbook(c, "1")
	ch = r["names"]["chart"]["Daily Sales"]
	assert ch in r["compiled"]["dependencies"]["charts"]
	assert r["compiled"]["dependencies"]["charts"][ch]["query"] in r["compiled"]["dependencies"]["queries"]


def test_byte_stable():
	site, comp, c = built()
	assert dumps(export_workbook(c, "1")["compiled"]) == dumps(export_workbook(c, "URY Sales")["compiled"])


def test_helper_queries_excluded():
	site, comp, c = built()
	site.docs["Insights Query v3"]["hh"] = {"name": "hh", "workbook": "1", "title": "", "operations": []}
	r = export_workbook(c, "1")
	assert "hh" not in r["compiled"]["dependencies"]["queries"]
	site.docs["Insights Query v3"]["hd"] = {"name": "hd", "workbook": "1", "title": "x", "operations": [{"a": 1}]}
	ch = next(iter(site.docs["Insights Chart v3"].values()))
	ch["data_query"] = "hd"
	r = export_workbook(c, "1")
	assert "hd" not in r["compiled"]["dependencies"]["queries"]
	assert export_all(c)["workbooks"][0]["queries"] == len(GOLDEN["dependencies"]["queries"])


def test_export_all_index():
	site, comp, c = built()
	r = export_all(c)
	assert r["ok"] and len(r["workbooks"]) == 1
	w = r["workbooks"][0]
	assert (w["title"], w["marker"], w["charts"], w["dashboards"]) == ("URY Sales", "finsights:ury-sales", 3, 1)
	assert w["modified"] and "compiled" not in w


def test_lossy_reported():
	site, comp, c = built()
	ch = next(iter(site.docs["Insights Chart v3"].values()))
	ch["custom_note"] = "hello"
	r = export_workbook(c, "1")
	assert any("custom_note" in s for s in r["lossy"])
	assert all("custom_note" not in d for d in r["compiled"]["dependencies"]["charts"].values())


def test_review_healthy():
	site, comp, c = built()
	r = review(c, "URY Sales")
	assert r["ok"] and r["findings"] == []
	assert len(r["charts"]) == 3 and all(x["ok"] and x["rows"] == 1 for x in r["charts"])
	d = r["dashboards"][0]
	assert d["links_ok"] and d["charts"] == 3
	assert {(f["name"], f["type"], f["links"]) for f in d["filters"]} == {("Date", "Date", 3), ("Branch", "String", 3)}


def test_review_v02_empty_rows():
	site, comp, c = built(canned_rows=[])
	r = review(c, "1")
	assert not r["ok"] and {f["rule"] for f in r["findings"]} >= {"V02"}
	assert all(x["rows"] == 0 and not x["ok"] for x in r["charts"])
	assert review(c, "1", min_rows=0)["charts"][0]["ok"]


def test_review_v05_broken_link():
	site, comp, c = built()
	dash = next(iter(site.docs["Insights Dashboard v3"].values()))
	flt = next(i for i in dash["items"] if i["type"] == "filter")
	flt["links"]["ghost-chart"] = "`q`.`c`"
	r = review(c, "1")
	assert not r["ok"] and "V05" in {f["rule"] for f in r["findings"]}
	assert r["dashboards"][0]["links_ok"] is False


def test_review_query_error():
	site, comp, c = built()
	site.fail_run.update(site.docs["Insights Query v3"])
	r = review(c, "1")
	assert not r["ok"] and {f["rule"] for f in r["findings"]} == {"V03"}
	assert all(x["error"] and x["rows"] is None for x in r["charts"])


def test_review_runs_each_query_once():
	site, comp, c = built()
	site.calls.clear()
	review(c, "1")
	assert len([x for x in site.calls if x[1].endswith("run_doc_method")]) == 1


def test_review_v04_linked_mismatch():
	site, comp, c = built()
	d = next(iter(site.docs["Insights Dashboard v3"].values()))
	d["linked_charts"] = d["linked_charts"][:1]
	assert "V04" in {f["rule"] for f in review(c, "1")["findings"]}


def test_json_serialisable_and_no_secrets():
	site, comp, c = built()
	site.fail_run.update(site.docs["Insights Query v3"])
	blob = json.dumps([export_workbook(c, "1"), export_all(c), review(c, "1"), export_workbook(c, "zz")])
	assert site.password not in blob
	assert "Authorization" not in blob


def test_never_raises_on_network_error():
	site, comp, c = built()
	c2 = FakeSite().client()
	c2._password = "wrong"
	r = export_all(c2)
	assert r["ok"] is False and "exit_code" in r["error"]
	assert review(c2, "1")["ok"] is False


def test_ref_accepts_spec_id_kebab_and_full_marker():
	# Regression (found live): the marker is kebab-cased, but users pass the spec id as typed.
	from finsights_lib.export import _resolve
	rows = [{"name": "7", "title": "URY Sales", "from_template": "finsights:ury-sales", "modified": "x"}]

	class C:
		def list_docs(self, *a, **k):
			return rows

	for ref in ("ury_sales", "ury-sales", "finsights:ury-sales", "finsights:ury_sales", "7", "URY Sales"):
		assert _resolve(C(), ref)["name"] == "7"
