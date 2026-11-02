import pytest
from fakesite import FakeSite
from finsights_lib.client import Client
from finsights_lib.common import FinsightsError


def test_missing_url_exit_2():
	with pytest.raises(FinsightsError) as e:
		Client.from_env({})
	assert e.value.exit_code == 2


def test_missing_credentials_exit_2():
	with pytest.raises(FinsightsError) as e:
		Client.from_env({"INSIGHTS_URL": "http://x"})
	assert e.value.exit_code == 2


def test_from_env_ok_and_repr_hides_secrets():
	c = Client.from_env({"INSIGHTS_URL": "http://x/", "INSIGHTS_USER": "u", "INSIGHTS_PASSWORD": "s3cretpw"})
	assert "s3cretpw" not in repr(c) and c.base_url == "http://x"


def test_auth_failure_exit_4_without_leaking_password():
	site = FakeSite(password="right")
	c = Client("http://site.test", user="Administrator", password="wrongpw", opener=site)
	with pytest.raises(FinsightsError) as e:
		c.get("/api/resource/Insights Workbook")
	assert e.value.exit_code == 4
	assert "wrongpw" not in str(e.value)


def test_login_then_crud_roundtrip_parses_json_fields():
	site = FakeSite()
	c = site.client()
	r = c.post("/api/resource/Insights Query v3", {"title": "q", "operations": [{"type": "source"}]})
	nm = r["data"]["name"]
	assert isinstance(site.docs["Insights Query v3"][nm]["operations"], list)
	assert isinstance(c.get("/api/resource/Insights Query v3/" + nm)["data"]["operations"], str)
	assert c.get_doc("Insights Query v3", nm)["operations"] == [{"type": "source"}]


def test_api_key_header_skips_login():
	site = FakeSite()
	c = Client("http://site.test", api_key="k", api_secret="s", opener=site)
	c.list_docs("Insights Workbook")
	assert ("POST", "/api/method/login") not in site.calls


def test_list_docs_filters():
	site = FakeSite()
	c = site.client()
	c.post("/api/resource/Insights Workbook", {"title": "A"})
	c.post("/api/resource/Insights Workbook", {"title": "B"})
	rows = c.list_docs("Insights Workbook", filters={"title": "B"}, fields=["name", "title"])
	assert rows == [{"name": "2", "title": "B"}]


def test_run_query_sends_full_doc():
	site = FakeSite(canned_rows=[{"a": 1}, {"a": 2}])
	c = site.client()
	nm = c.post("/api/resource/Insights Query v3", {"title": "q", "operations": [{"type": "source"}]})["data"]["name"]
	out = c.run_query(nm)
	assert len(out["rows"]) == 2
	body = [b for m, p, b in site.bodies if p.endswith("run_doc_method")][0]
	assert body["method"] == "execute" and body["docs"]["operations"] == [{"type": "source"}]
	assert body["args"]["force"] == 1


def test_run_query_server_error_exit_4():
	site = FakeSite()
	c = site.client()
	nm = c.post("/api/resource/Insights Query v3", {"title": "q", "operations": [{"type": "source"}]})["data"]["name"]
	site.fail_run.add(nm)
	with pytest.raises(FinsightsError) as e:
		c.run_query(nm)
	assert e.value.exit_code == 4
