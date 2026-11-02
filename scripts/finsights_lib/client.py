"""HTTP client for a Frappe site running Insights. The ONLY module that touches the network.

urllib + http.cookiejar only. Secrets come from the environment and are never echoed.
"""

from __future__ import annotations

import http.cookiejar
import json
import re
import time
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


RETRY_DELAYS = (0.4, 0.8, 1.6)


def _transient_http(code, raw) -> bool:
	if code in (502, 503, 504):
		return True
	if code != 500:
		return False
	text = (raw or b"").decode("utf-8", "replace")
	if not text.strip() or "SAVEPOINT" in text:
		return True
	try:
		json.loads(text)
	except ValueError:
		return True
	return False


_TAG_RE = re.compile(r"<[^>]*>")
_MAX_MSG = 300


def _clean(text) -> str:
	text = _TAG_RE.sub(" ", str(text))
	return re.sub(r"\s+", " ", text).strip()[:_MAX_MSG]


def _first_server_message(payload: dict) -> str:
	"""First meaningful text from `_server_messages` (JSON string of JSON strings), defensively."""
	raw = payload.get("_server_messages")
	if raw:
		try:
			items = json.loads(raw) if isinstance(raw, str) else raw
			for it in items if isinstance(items, list) else [items]:
				try:
					obj = json.loads(it) if isinstance(it, str) else it
				except ValueError:
					obj = it
				txt = obj.get("message") if isinstance(obj, dict) else obj
				txt = _clean(txt) if isinstance(txt, (str, int, float)) else ""
				if txt:
					return txt
		except (ValueError, TypeError, AttributeError):
			pass
	for k in ("exception", "message"):
		v = payload.get(k)
		if isinstance(v, str) and _clean(v):
			return _clean(v)
	return ""


class Client:
	def __init__(self, base_url, user=None, password=None, api_key=None, api_secret=None, opener=None, sleep=time.sleep):
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
		self._sleep = sleep
		self.retries_used = 0

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
	def _secrets(self):
		return [x for x in (self._password, self._api_secret, self._api_key) if x and len(str(x)) >= 3]

	def _scrub(self, text: str) -> str:
		for x in self._secrets():
			text = text.replace(str(x), "***")
		return text

	def _http_error(self, method, path, e, raw=None) -> FinsightsError:
		code = e.code
		detail = ""
		try:
			if raw is None:
				raw = e.read()
			payload = json.loads(raw.decode("utf-8", "replace"))
			if isinstance(payload, dict):
				exc_type = payload.get("exc_type")
				msg = _first_server_message(payload)
				detail = " | ".join(x for x in (exc_type and _clean(exc_type), msg) if x)
		except Exception:
			detail = ""
		where = f"{method} {path} -> HTTP {code}"
		if code in (401, 403):
			who = self._user or ("API key" if self._api_key else "the configured user")
			head = (
				f"{where}: authentication or permission problem for {who}: check "
				"INSIGHTS_USER/INSIGHTS_PASSWORD or API key and the Insights User/Admin role"
			)
		elif code == 404:
			head = f"{where}: not found"
		elif code >= 500:
			head = f"{where}: server error"
		else:
			head = f"{where}: server rejected the request"
		msg = self._scrub(head + (f" ({detail})" if detail else ""))
		err = FinsightsError(msg, 4)
		err.status = code
		return err

	def _request(self, method: str, path: str, params=None, body=None, _retry=True) -> dict:
		is_login = path.startswith("/api/method/login")
		if not is_login:
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
		idempotent = (
			is_login
			or method == "GET"
			or (
				method == "POST"
				and path.endswith("run_doc_method")
				and isinstance(body, dict)
				and body.get("method") == "execute"
			)
		)
		attempt = 0
		while True:
			try:
				resp = self._opener.open(req, timeout=60)
				raw = resp.read()
				break
			except urllib.error.HTTPError as e:
				try:
					ebody = e.read()
				except Exception:
					ebody = b""
				err = self._http_error(method, path, e, ebody)
				if idempotent and attempt < len(RETRY_DELAYS) and _transient_http(err.status, ebody):
					self._sleep(RETRY_DELAYS[attempt])
					attempt += 1
					self.retries_used += 1
					continue
				if (
					err.status in (401, 403)
					and _retry
					and not is_login
					and self._logged_in
					and not (self._api_key and self._api_secret)
					and self._user
					and self._password
				):
					self._logged_in = False
					self.login()  # one re-login; raises on failure
					return self._request(method, path, params, body, _retry=False)
				raise err from None
			except (urllib.error.URLError, OSError) as e:
				if idempotent and attempt < len(RETRY_DELAYS):
					self._sleep(RETRY_DELAYS[attempt])
					attempt += 1
					self.retries_used += 1
					continue
				host = urllib.parse.urlparse(self.base_url).netloc or self.base_url
				reason = self._scrub(_clean(getattr(e, "reason", e)))
				raise FinsightsError(f"{method} {path}: cannot reach {host} ({reason})", 4) from None
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
