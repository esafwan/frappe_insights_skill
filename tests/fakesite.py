"""In-memory fake of the Insights REST surface used by finsights (injectable urllib opener)."""

from __future__ import annotations

import io
import json
import urllib.error
import urllib.parse

from finsights_lib.client import Client

JSON_FIELDS = ("operations", "config", "items")
DTS = {"Insights Workbook", "Insights Query v3", "Insights Chart v3", "Insights Dashboard v3"}


class _Resp:
	def __init__(self, payload):
		self._b = json.dumps(payload).encode()

	def read(self):
		return self._b


class FakeSite:
	"""Acts as `opener`: .open(request, timeout=..) -> response with .read()."""

	def __init__(self, user="Administrator", password="pw", canned_rows=None):
		self.docs = {dt: {} for dt in DTS}
		self.user, self.password = user, password
		self.canned_rows = canned_rows if canned_rows is not None else [{"x": 1}]
		self.query_rows = {}  # live query name -> rows override
		self.calls = []  # (method, path)
		self.bodies = []  # (method, path, body)
		self.fail_run = set()
		self._wb = 0
		self._seq = 0
		self.logged_in = False

	# ---- helpers
	def client(self, **kw):
		return Client("http://site.test", user=self.user, password=self.password, opener=self, **kw)

	def writes(self):
		return [c for c in self.calls if c[0] in ("POST", "PUT", "DELETE") and "login" not in c[1]
			and "run_doc_method" not in c[1]]

	def _out(self, doc):
		d = dict(doc)
		for f in JSON_FIELDS:
			if f in d and not isinstance(d[f], str) and d[f] is not None:
				d[f] = json.dumps(d[f], indent=1)
		return d

	def _err(self, url, code):
		raise urllib.error.HTTPError(url, code, "err", {}, io.BytesIO(b"{}"))

	def _finish(self, dt, doc):
		if dt == "Insights Chart v3" and "data_query" not in doc:
			doc["data_query"] = ""
		if dt == "Insights Dashboard v3":
			items = doc.get("items")
			if isinstance(items, str):
				items = json.loads(items)
			doc["linked_charts"] = [{"chart": i["chart"]} for i in (items or []) if i.get("type") == "chart"]

	def _normalise_in(self, body):
		b = dict(body)
		for f in JSON_FIELDS:
			if isinstance(b.get(f), str):
				b[f] = json.loads(b[f])
		for f in ("data_query", "linked_charts", "name"):
			b.pop(f, None)
		return b

	# ---- opener
	def open(self, req, timeout=None):
		url = req.full_url
		parsed = urllib.parse.urlparse(url)
		path = urllib.parse.unquote(parsed.path)
		q = {k: v[0] for k, v in urllib.parse.parse_qs(parsed.query).items()}
		method = req.get_method()
		body = json.loads(req.data.decode()) if req.data else None
		self.calls.append((method, path))
		self.bodies.append((method, path, body))
		if path == "/api/method/login":
			if body.get("usr") == self.user and body.get("pwd") == self.password:
				self.logged_in = True
				return _Resp({"message": "Logged In"})
			self._err(url, 401)
		if not self.logged_in and "Authorization" not in dict(req.header_items()):
			self._err(url, 403)
		if path == "/api/method/insights.api.run_doc_method":
			doc = body.get("docs") or {}
			if body.get("method") != "execute" or not doc.get("operations"):
				self._err(url, 500)  # TypeError: operations None
			if doc.get("name") in self.fail_run:
				self._err(url, 500)
			rows = self.query_rows.get(doc.get("name"), self.canned_rows)
			cols = [{"name": k} for k in (rows[0] if rows else {})]
			return _Resp({"message": {"rows": rows, "columns": cols}})
		parts = path.split("/")  # '', api, resource, <dt>, [name]
		if len(parts) < 4 or parts[2] != "resource" or parts[3] not in DTS:
			self._err(url, 404)
		dt = parts[3]
		name = parts[4] if len(parts) > 4 else None
		store = self.docs[dt]
		if method == "GET" and name is None:
			filters = json.loads(q["filters"]) if "filters" in q else {}
			rows = [d for d in store.values() if all(d.get(k) == v for k, v in filters.items())]
			fields = json.loads(q["fields"]) if "fields" in q else ["name"]
			return _Resp({"data": [{f: d.get(f) for f in fields} for d in rows]})
		if method == "GET":
			if name not in store:
				self._err(url, 404)
			return _Resp({"data": self._out(store[name])})
		if method == "POST":
			b = self._normalise_in(body)
			if dt == "Insights Workbook":
				self._wb += 1
				nm = str(self._wb)
			else:
				self._seq += 1
				nm = "h%06d" % self._seq
			doc = {"name": nm, "owner": "Administrator", "modified": "t0", "from_template": None, **b}
			self._finish(dt, doc)
			store[nm] = doc
			return _Resp({"data": self._out(doc)})
		if method == "PUT":
			if name not in store:
				self._err(url, 404)
			store[name].update(self._normalise_in(body))
			store[name]["modified"] = "t1"
			self._finish(dt, store[name])
			return _Resp({"data": self._out(store[name])})
		if method == "DELETE":
			store.pop(name, None)
			return _Resp({"message": "ok"})
		self._err(url, 405)
