"""HTTP client for a Frappe site running Insights. The ONLY module that touches the network.

urllib + http.cookiejar only. Secrets come from the environment and are never echoed.
"""

from __future__ import annotations

import http.cookiejar
import json
import urllib.error
import urllib.parse
import urllib.request

from .common import FinsightsError

JSON_FIELDS = ("operations", "config", "items")


def _parse_json_fields(doc: dict) -> dict:
	for f in JSON_FIELDS:
		v = doc.get(f)
		if isinstance(v, str) and v.strip():
			try:
				doc[f] = json.loads(v)
			except ValueError:
				pass
	return doc


class Client:
	def __init__(self, base_url, user=None, password=None, api_key=None, api_secret=None, opener=None):
		if not base_url:
			raise FinsightsError("INSIGHTS_URL is required", 2)
		self.base_url = base_url.rstrip("/")
		self._user = user
		self._password = password
		self._api_key = api_key
		self._api_secret = api_secret
		if opener is None:
			jar = http.cookiejar.CookieJar()
			opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
		self._opener = opener
		self._logged_in = False

	def __repr__(self) -> str:
		return f"Client({self.base_url!r})"

	@classmethod
	def from_env(cls, env=None):
		import os

		env = os.environ if env is None else env
		url = env.get("INSIGHTS_URL")
		if not url:
			raise FinsightsError("INSIGHTS_URL is not set (export INSIGHTS_URL=https://site)", 2)
		key, secret = env.get("INSIGHTS_API_KEY"), env.get("INSIGHTS_API_SECRET")
		user, pwd = env.get("INSIGHTS_USER"), env.get("INSIGHTS_PASSWORD")
		if not ((key and secret) or (user and pwd)):
			raise FinsightsError(
				"set INSIGHTS_API_KEY+INSIGHTS_API_SECRET or INSIGHTS_USER+INSIGHTS_PASSWORD", 2
			)
		return cls(url, user=user, password=pwd, api_key=key, api_secret=secret)

	# -------------------------------------------------------------- transport
	def _request(self, method: str, path: str, params=None, body=None) -> dict:
		if not path.startswith("/api/method/login"):
			self._ensure_login()
		url = self.base_url + urllib.parse.quote(path, safe="/:")
		if params:
			url += "?" + urllib.parse.urlencode(
				{k: (json.dumps(v) if isinstance(v, (list, dict)) else v) for k, v in params.items()}
			)
		headers = {"Accept": "application/json"}
		data = None
		if body is not None:
			data = json.dumps(body).encode("utf-8")
			headers["Content-Type"] = "application/json"
		if self._api_key and self._api_secret:
			headers["Authorization"] = f"token {self._api_key}:{self._api_secret}"
		req = urllib.request.Request(url, data=data, headers=headers, method=method)
		try:
			resp = self._opener.open(req, timeout=60)
			raw = resp.read()
		except urllib.error.HTTPError as e:
			err = FinsightsError(f"{method} {path} -> HTTP {e.code}", 4)
			err.status = e.code
			raise err from None
		except (urllib.error.URLError, OSError) as e:
			raise FinsightsError(f"{method} {path} failed: {getattr(e, 'reason', e)}", 4) from None
		if not raw:
			return {}
		try:
			return json.loads(raw.decode("utf-8"))
		except ValueError:
			raise FinsightsError(f"{method} {path}: response is not JSON", 4) from None

	def _ensure_login(self):
		if not self._logged_in:
			self.login()

	def login(self):
		if self._api_key and self._api_secret:
			self._logged_in = True
			return
		if not (self._user and self._password):
			raise FinsightsError("no credentials configured", 2)
		self._logged_in = True  # avoid recursion; reset on failure
		try:
			self._request("POST", "/api/method/login", body={"usr": self._user, "pwd": self._password})
		except FinsightsError as e:
			self._logged_in = False
			raise FinsightsError(f"authentication failed ({e})", 4) from None

	# -------------------------------------------------------------- verbs
	def get(self, path, params=None) -> dict:
		return self._request("GET", path, params=params)

	def post(self, path, body) -> dict:
		return self._request("POST", path, body=body)

	def put(self, path, body) -> dict:
		return self._request("PUT", path, body=body)

	def delete(self, path) -> dict:
		return self._request("DELETE", path)

	# -------------------------------------------------------------- docs
	def get_doc(self, doctype, name) -> dict:
		r = self.get(f"/api/resource/{doctype}/{name}")
		return _parse_json_fields(dict(r.get("data") or {}))

	def list_docs(self, doctype, filters=None, fields=None, limit=0) -> list:
		params = {"limit_page_length": limit or 0}
		if filters:
			params["filters"] = filters
		if fields:
			params["fields"] = fields
		r = self.get(f"/api/resource/{doctype}", params)
		return list(r.get("data") or [])

	def run_query(self, query_name, page_size=10) -> dict:
		doc = self.get_doc("Insights Query v3", query_name)
		r = self.post(
			"/api/method/insights.api.run_doc_method",
			{"method": "execute", "docs": doc, "args": {"force": 1, "page": 1, "page_size": page_size}},
		)
		return r.get("message") or {}
