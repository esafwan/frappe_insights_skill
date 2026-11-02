import copy

from test_verify import applied as _applied, rules
from finsights_lib.verify import verify


def applied(**kw):
	kw.setdefault("canned_rows", [{"posting_date": "2026-01-01", "branch": "b", "x": 1}])
	return _applied(**kw)


def dash(site):
	return next(iter(site.docs["Insights Dashboard v3"].values()))


def date_filter(site):
	return next(i for i in dash(site)["items"] if i.get("type") == "filter" and i.get("links"))


def v05(fs):
	return [f for f in fs if f.rule == "V05"]


def test_clean_passes():
	site, comp, c = applied()
	assert v05(verify(comp, c)) == []


def test_unknown_chart():
	site, comp, c = applied()
	f = date_filter(site)
	f["links"]["ghost-chart"] = next(iter(f["links"].values()))
	r = v05(verify(comp, c))
	assert len(r) == 1 and r[0].severity == "error" and "ghost-chart" in r[0].path


def test_malformed_link():
	site, comp, c = applied()
	f = date_filter(site)
	k = next(iter(f["links"]))
	f["links"][k] = "pos_invoices.posting_date"
	r = v05(verify(comp, c))
	assert len(r) == 1 and "malformed" in r[0].message


def test_query_mismatch():
	site, comp, c = applied()
	f = date_filter(site)
	k = next(iter(f["links"]))
	f["links"][k] = "`other-query`.`posting_date`"
	r = v05(verify(comp, c))
	assert len(r) == 1 and "!=" in r[0].message


def test_column_missing():
	site, comp, c = applied()
	f = date_filter(site)
	k = next(iter(f["links"]))
	q = f["links"][k].split(".")[0]
	f["links"][k] = q + ".`nope`"
	r = v05(verify(comp, c))
	assert len(r) == 1 and "nope" in r[0].message and r[0].severity == "error"


def test_filter_without_links_warns():
	site, comp, c = applied()
	date_filter(site)["links"] = {}
	r = v05(verify(comp, c))
	assert len(r) == 1 and r[0].severity == "warn"


def test_query_error_not_crash_and_single_v03():
	site, comp, c = applied()
	site.fail_run.update(site.docs["Insights Query v3"])
	fs = verify(comp, c)
	assert v05(fs) == []
	assert "V03" in rules(fs)


def test_empty_links_none():
	site, comp, c = applied()
	date_filter(site)["links"] = None
	assert [f.severity for f in v05(verify(comp, c))] == ["warn"]
