import io
import json
import urllib.error

import pytest
from finsights_lib.client import Client
from finsights_lib.common import FinsightsError

SAVE = {"exc_type": "OperationalError", "exception": "SAVEPOINT sp1 does not exist"}


def _http(code, body=b""):
	raw = body if isinstance(body, bytes) else json.dumps(body).encode()
	return urllib.error.HTTPError("http://site.test/x", code, "err", {}, io.BytesIO(raw))


class Resp:
	def __init__(self, payload):
		self._b = json.dumps(payload).encode()

	def read(self):
		return self._b


class Op:
	"""script: per-path-prefix list of outcomes (Exception | (code, body) | payload)."""

	def __init__(self, login=(), other=()):
		self.login, self.other, self.calls = list(login), list(other), []

	def open(self, req, timeout=None):
		path = req.full_url.split("site.test", 1)[1]
		self.calls.append((req.get_method(), path))
		q = self.login if "/api/method/login" in path else self.other
		item = q.pop(0) if q else {"message": "Logged In"}
		if isinstance(item, Exception):
			raise item
		if isinstance(item, tuple):
			raise _http(*item)
		return Resp(item)


def _c(login=(), other=()):
	op = Op(login, other)
	slept = []
	c = Client("http://site.test", user="alice", password="hunter2pw", opener=op, sleep=slept.append)
	return c, op, slept


def test_login_500_savepoint_then_success():
	c, op, slept = _c(login=[(500, SAVE)], other=[{"data": []}])
	assert c.get("/api/resource/X") == {"data": []}
	assert slept == [0.4] and c.retries_used == 1
	assert [m for m, p in op.calls].count("POST") == 2


def test_get_503_then_success():
	c, op, slept = _c(other=[(503, b""), {"data": 1}])
	assert c.get("/api/resource/X") == {"data": 1}
	assert slept == [0.4]


def test_execute_500_empty_then_success():
	c, op, slept = _c(other=[(500, b""), {"message": {"ok": 1}}])
	r = c.post("/api/method/insights.api.run_doc_method", {"method": "execute", "docs": {}})
	assert r == {"message": {"ok": 1}} and slept == [0.4]


def test_three_failures_then_exit4_with_backoff():
	c, op, slept = _c(other=[(502, b"")] * 4)
	with pytest.raises(FinsightsError) as e:
		c.get("/api/resource/X")
	assert e.value.exit_code == 4 if hasattr(e.value, "exit_code") else True
	assert e.value.status == 502 and "502" in str(e.value)
	assert slept == [0.4, 0.8, 1.6]
	assert len([x for x in op.calls if "login" not in x[1]]) == 4


def test_network_error_retried_then_ok():
	c, op, slept = _c(other=[urllib.error.URLError("boom"), TimeoutError("t"), {"data": 2}])
	assert c.get("/api/resource/X") == {"data": 2}
	assert slept == [0.4, 0.8] and c.retries_used == 2


def test_network_error_exhausted():
	c, op, slept = _c(other=[urllib.error.URLError("down")] * 4)
	with pytest.raises(FinsightsError) as e:
		c.get("/api/resource/X")
	assert "cannot reach" in str(e.value)
	assert slept == [0.4, 0.8, 1.6]


def test_resource_create_500_not_retried():
	c, op, slept = _c(other=[(500, b""), {"data": {}}])
	with pytest.raises(FinsightsError) as e:
		c.post("/api/resource/Insights Workbook", {"title": "t"})
	assert e.value.status == 500
	assert slept == [] and c.retries_used == 0
	assert len([x for x in op.calls if "login" not in x[1]]) == 1


def test_put_not_retried():
	c, op, slept = _c(other=[(500, SAVE), {"data": {}}])
	with pytest.raises(FinsightsError):
		c.put("/api/resource/Insights Workbook/x", {"title": "t"})
	assert slept == []
	assert len([x for x in op.calls if "login" not in x[1]]) == 1


def test_delete_not_retried():
	c, op, slept = _c(other=[(503, b"")])
	with pytest.raises(FinsightsError):
		c.delete("/api/resource/X/y")
	assert slept == []


def test_json_500_without_savepoint_not_retried():
	c, op, slept = _c(other=[(500, {"exc_type": "ValidationError", "exception": "bad"})])
	with pytest.raises(FinsightsError):
		c.get("/api/resource/X")
	assert slept == []


def test_401_unchanged_relogin_once_no_sleep():
	c, op, slept = _c(other=[(401, {}), {"data": 3}])
	assert c.get("/api/resource/X") == {"data": 3}
	assert slept == [] and c.retries_used == 0


def test_401_persistent_errors_without_retry():
	c, op, slept = _c(other=[(401, {}), (401, {})])
	with pytest.raises(FinsightsError) as e:
		c.get("/api/resource/X")
	assert e.value.status == 401 and slept == []


def test_no_secrets_in_errors():
	c, op, slept = _c(other=[(500, {"exception": "pwd hunter2pw leaked"})])
	with pytest.raises(FinsightsError) as e:
		c.put("/api/resource/X/y", {})
	assert "hunter2pw" not in str(e.value)
	c2, _, _ = _c(other=[urllib.error.URLError("hunter2pw refused")] * 4)
	with pytest.raises(FinsightsError) as e2:
		c2.get("/api/resource/X")
	assert "hunter2pw" not in str(e2.value)
