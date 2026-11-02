import io
import json
import urllib.error

import pytest
from finsights_lib.client import Client
from finsights_lib.common import FinsightsError


class Resp:
	def __init__(self, payload):
		self._b = json.dumps(payload).encode()

	def read(self):
		return self._b


class Scripted:
	"""Opener: login always ok unless login_status; other calls pop from `script`."""

	def __init__(self, script, login_status=None):
		self.script, self.calls, self.login_status = list(script), [], login_status

	def open(self, req, timeout=None):
		path = req.full_url.split("site.test", 1)[1]
		self.calls.append((req.get_method(), path))
		if "/api/method/login" in path:
			if self.login_status:
				raise _http(self.login_status, {})
			return Resp({"message": "Logged In"})
		item = self.script.pop(0)
		if isinstance(item, Exception):
			raise item
		if isinstance(item, int):
			raise _http(item, {})
		if isinstance(item, tuple):
			raise _http(*item)
		return Resp(item)


def _http(code, body):
	raw = body if isinstance(body, bytes) else json.dumps(body).encode()
	return urllib.error.HTTPError("http://site.test/x", code, "err", {}, io.BytesIO(raw))


def _client(script, **kw):
	op = Scripted(script, kw.pop("login_status", None))
	kw.setdefault("user", "alice")
	kw.setdefault("password", "hunter2pw")
	return Client("http://site.test", opener=op, **kw), op


def _err(script, **kw):
	c, op = _client(script, **kw)
	with pytest.raises(FinsightsError) as e:
		c.get("/api/resource/Insights Workbook")
	return e.value, op


@pytest.mark.parametrize("code", [401, 403])
def test_auth_permission_hint(code):
	# retry also fails -> final error carries the hint
	e, _ = _err([code, code])
	assert e.exit_code == 4 and e.status == code
	assert "authentication or permission problem for alice" in str(e)
	assert "Insights User/Admin role" in str(e)


def test_404_not_found():
	e, _ = _err([404])
	assert e.exit_code == 4 and e.status == 404 and "not found" in str(e)
	assert "GET /api/resource/Insights Workbook" in str(e)


@pytest.mark.parametrize("code", [409, 417, 422, 400])
def test_validation_4xx_prefixed(code):
	e, _ = _err([code])
	assert e.exit_code == 4 and e.status == code and "server rejected the request" in str(e)


def test_5xx_server_error():
	e, _ = _err([500])
	assert e.exit_code == 4 and e.status == 500 and "server error" in str(e)


def test_network_error_cannot_reach():
	e, _ = _err([urllib.error.URLError("refused")])
	assert e.exit_code == 4 and "cannot reach site.test" in str(e)
	e2, _ = _err([TimeoutError("timed out")])
	assert "cannot reach site.test" in str(e2)


def test_server_messages_extracted_with_exc_type():
	msgs = json.dumps([json.dumps({"message": "Operation <b>foo</b> is  invalid"})])
	e, _ = _err([(417, {"exc_type": "ValidationError", "_server_messages": msgs})])
	assert "ValidationError" in str(e) and "Operation foo is invalid" in str(e)
	assert "<b>" not in str(e)


@pytest.mark.parametrize(
	"sm",
	["not json", json.dumps("str"), json.dumps(["not json either"]), json.dumps([None, 5]), "[[", 7],
)
def test_malformed_server_messages_do_not_crash(sm):
	e, _ = _err([(417, {"exc_type": "X", "_server_messages": sm, "exception": "frappe.Boom: bad"})])
	assert e.status == 417 and "X" in str(e)


def test_falls_back_to_exception_then_message_and_truncates():
	e, _ = _err([(500, {"exception": "E" * 1000})])
	assert "E" * 300 in str(e) and "E" * 301 not in str(e)
	e2, _ = _err([(500, {"message": "plain <i>msg</i>"})])
	assert "plain msg" in str(e2)


def test_non_json_body_handled():
	e, _ = _err([(502, b"<html><body>Bad Gateway</body></html>")])
	assert e.status == 502 and "server error" in str(e) and "<html>" not in str(e)


def test_secrets_scrubbed_everywhere():
	leak = json.dumps([json.dumps({"message": "bad pw hunter2pw secret-xyz key-abc"})])
	c, _ = _client([(417, {"exception": "hunter2pw", "_server_messages": leak})],
		api_key="key-abc", api_secret="secret-xyz")
	with pytest.raises(FinsightsError) as e:
		c.post("/api/resource/X", {"password": "hunter2pw"})
	for s in ("hunter2pw", "secret-xyz", "key-abc"):
		assert s not in str(e.value) and s not in repr(e.value) and s not in repr(c)


def test_relogin_retry_succeeds_once():
	c, op = _client([{"data": 1}, 401, {"data": 2}])
	assert c.get("/a") == {"data": 1}
	assert c.get("/b") == {"data": 2}
	logins = [x for x in op.calls if "login" in x[1]]
	assert len(logins) == 2  # initial + one re-login
	assert [x[1] for x in op.calls if "login" not in x[1]] == ["/a", "/b", "/b"]


def test_retry_does_not_loop():
	c, op = _client([{"data": 1}, 403, 403, {"data": 9}])
	c.get("/a")
	with pytest.raises(FinsightsError) as e:
		c.get("/b")
	assert e.value.status == 403
	assert [x[1] for x in op.calls if "login" not in x[1]] == ["/a", "/b", "/b"]
	assert len([x for x in op.calls if "login" in x[1]]) == 2


def test_no_relogin_with_api_key():
	c, op = _client([401], user=None, password=None, api_key="k1k", api_secret="s1s")
	with pytest.raises(FinsightsError):
		c.get("/a")
	assert [x for x in op.calls if "login" in x[1]] == []
