"""Restricted YAML subset loader (stdlib only).

Supported: 2-space block maps/sequences, flow {a: 1, b: [x, y]} (may span lines),
bare/"dq"/'sq' scalars, int/float/true/false/null, # comments.
Rejected with SpecParseError(line, col): anchors/aliases, tags, multi-doc, block scalars, tabs.
"""

from __future__ import annotations

import re

from .common import SpecParseError

_INT = re.compile(r"^[-+]?\d+$")
_FLOAT = re.compile(r"^[-+]?(\d+\.\d*|\.\d+|\d+(?=[eE]))([eE][-+]?\d+)?$")
_BLOCK = re.compile(r"^[|>][+-]?\d*$")
_ESC = {"n": "\n", "t": "\t", "r": "\r", '"': '"', "\\": "\\", "/": "/", "0": "\0"}


def load(path, force_builtin: bool = False):
	with open(path, encoding="utf-8") as f:
		return loads(f.read(), force_builtin=force_builtin)


def loads(text: str, force_builtin: bool = False):
	if not force_builtin:
		try:
			import yaml  # type: ignore
		except ImportError:
			yaml = None
		if yaml is not None:
			try:
				return yaml.safe_load(text)
			except yaml.YAMLError as e:
				mark = getattr(e, "problem_mark", None)
				raise SpecParseError(str(getattr(e, "problem", e)), (mark.line + 1) if mark else 1, (mark.column + 1) if mark else 1)
	return _Parser(text).parse()


def _scalar(tok: str, line: int, col: int):
	if tok in ("null", "~", "Null", "NULL"):
		return None
	if tok in ("true", "True", "TRUE"):
		return True
	if tok in ("false", "False", "FALSE"):
		return False
	if _INT.match(tok):
		return int(tok)
	if _FLOAT.match(tok):
		return float(tok)
	return tok


class _Parser:
	def __init__(self, text: str):
		self.items = []  # (indent, text, lineno)
		for n, raw in enumerate(text.split("\n"), 1):
			raw = raw.rstrip("\r")
			lead = len(raw) - len(raw.lstrip(" "))
			if raw[lead:lead + 1] == "\t" or (raw.strip() and raw[:lead].count("\t")):
				raise SpecParseError("tabs are not allowed", n, raw.index("\t") + 1)
			if not raw.strip():
				continue
			body = self._strip_comment(raw, n)
			if not body.strip():
				continue
			if body.strip() in ("---", "...") or body.startswith("--- "):
				raise SpecParseError("multi-document YAML is not supported", n, 1)
			lead = len(body) - len(body.lstrip(" "))
			self.items.append((lead, body.strip(), n))
		self.i = 0

	@staticmethod
	def _strip_comment(raw: str, n: int) -> str:
		q = None
		for k, ch in enumerate(raw):
			if q:
				if ch == q:
					q = None
				continue
			if ch in "\"'" and (k == 0 or raw[k - 1] in " :,[{-\t"):
				q = ch
			elif ch == "#" and (k == 0 or raw[k - 1] in " \t"):
				return raw[:k].rstrip()
		return raw.rstrip()

	def parse(self):
		if not self.items:
			return None
		indent = self.items[0][0]
		val = self._block(indent)
		if self.i < len(self.items):
			ind, _t, ln = self.items[self.i]
			raise SpecParseError("unexpected indentation", ln, ind + 1)
		return val

	# ---- block level
	def _block(self, indent: int):
		ind, text, ln = self.items[self.i]
		if text == "-" or text.startswith("- "):
			return self._seq(indent)
		return self._map(indent)

	def _seq(self, indent: int):
		out = []
		while self.i < len(self.items):
			ind, text, ln = self.items[self.i]
			if ind != indent or not (text == "-" or text.startswith("- ")):
				if ind > indent:
					raise SpecParseError("unexpected indentation", ln, ind + 1)
				break
			rest = text[1:].lstrip(" ")
			off = ind + (len(text) - len(rest))
			if not rest:
				self.i += 1
				if self.i < len(self.items) and self.items[self.i][0] > indent:
					out.append(self._block(self.items[self.i][0]))
				else:
					out.append(None)
			elif rest == "-" or rest.startswith("- "):
				self.items[self.i] = (off, rest, ln)
				out.append(self._seq(off))
			elif self._split_key(rest, ln, off) is not None:
				self.items[self.i] = (off, rest, ln)
				out.append(self._map(off))
			else:
				out.append(self._inline(rest, ln, off + 1))
		return out

	@staticmethod
	def _quote_end(s: str, start: int):
		q = s[start]
		k = start + 1
		while k < len(s):
			if q == '"' and s[k] == "\\":
				k += 2
				continue
			if s[k] == q:
				if q == "'" and s[k + 1:k + 2] == "'":
					k += 2
					continue
				return k
			k += 1
		return None

	def _split_key(self, s: str, ln: int, off: int):
		"""Return (key, rest, rest_col) when s is `key: rest`, else None."""
		if s[0] in "\"'":
			end = self._quote_end(s, 0)
			if end is None:
				raise SpecParseError("unterminated quoted string", ln, off + 1)
			if s[end + 1:end + 2] != ":" or (len(s) > end + 2 and s[end + 2] != " "):
				return None
			key = self._unquote(s[:end + 1], ln, off)
			tail = s[end + 2:]
			rest = tail.lstrip(" ")
			return key, rest, off + end + 2 + (len(tail) - len(rest))
		if s[0] in "{[":
			return None
		m = re.search(r":(?: |$)", s)
		if not m:
			return None
		key = s[:m.start()].strip()
		tail = s[m.end():]
		rest = tail.lstrip(" ")
		return key, rest, off + m.end() + (len(tail) - len(rest))

	def _map(self, indent: int):
		out = {}
		while self.i < len(self.items):
			ind, text, ln = self.items[self.i]
			if ind != indent:
				if ind > indent:
					raise SpecParseError("unexpected indentation", ln, ind + 1)
				break
			if text == "-" or text.startswith("- "):
				break
			sk = self._split_key(text, ln, ind)
			if sk is None:
				raise SpecParseError("expected `key: value`", ln, ind + 1)
			key, rest, rcol = sk
			if text[0] not in "\"'":
				self._reject_special(key, ln, ind + 1)
			if key in out:
				raise SpecParseError(f"duplicate key {key!r}", ln, ind + 1)
			if not rest:
				self.i += 1
				if self.i < len(self.items):
					nind, ntext, _ = self.items[self.i]
					if nind > indent:
						out[key] = self._block(nind)
						continue
					if nind == indent and (ntext == "-" or ntext.startswith("- ")):
						out[key] = self._seq(indent)
						continue
				out[key] = None
			else:
				out[key] = self._inline(rest, ln, rcol + 1)
		return out

	# ---- values
	def _reject_special(self, s: str, ln: int, col: int):
		if s[:1] in "&*":
			raise SpecParseError("anchors and aliases are not supported", ln, col)
		if s[:1] == "!":
			raise SpecParseError("tags are not supported", ln, col)
		if _BLOCK.match(s):
			raise SpecParseError("block scalars (| >) are not supported", ln, col)

	def _inline(self, s: str, ln: int, col: int):
		"""Parse a value starting on the current item (consumes it, plus flow continuation lines)."""
		if s[0] in "{[":
			buf, pos = [], []
			cur = s
			cl, cc = ln, col
			self.i += 1
			while True:
				for k, ch in enumerate(cur):
					buf.append(ch)
					pos.append((cl, cc + k))
				if self._balanced("".join(buf)):
					break
				if self.i >= len(self.items):
					raise SpecParseError("unterminated flow collection", ln, col)
				nind, ntext, nln = self.items[self.i]
				self.i += 1
				buf.append(" ")
				pos.append((cl, cc + len(cur)))
				cur, cl, cc = ntext, nln, nind + 1
			fp = _Flow("".join(buf), pos)
			val = fp.value()
			fp.ws()
			if fp.p < len(fp.s):
				raise SpecParseError("unexpected text after flow collection", *fp.at())
			return val
		self.i += 1
		self._reject_special(s, ln, col)
		if s[0] in "\"'":
			end = self._quote_end(s, 0)
			if end is None:
				raise SpecParseError("unterminated quoted string", ln, col)
			if s[end + 1:].strip():
				raise SpecParseError("unexpected text after quoted string", ln, col + end + 1)
			return self._unquote(s, ln, col - 1)
		if re.search(r":(?: |$)", s):
			raise SpecParseError("nested mapping on one line is not supported", ln, col)
		return _scalar(s, ln, col)

	@staticmethod
	def _balanced(s: str) -> bool:
		depth, q = 0, None
		k = 0
		while k < len(s):
			ch = s[k]
			if q:
				if q == '"' and ch == "\\":
					k += 2
					continue
				if ch == q:
					q = None
			elif ch in "\"'":
				q = ch
			elif ch in "{[":
				depth += 1
			elif ch in "}]":
				depth -= 1
			k += 1
		return depth <= 0

	@staticmethod
	def _unquote(tok: str, ln: int, col: int) -> str:
		return _unquote(tok, ln, col)


def _unquote(tok: str, ln: int, col: int) -> str:
	q, body = tok[0], tok[1:-1]
	if q == "'":
		return body.replace("''", "'")
	out, k = [], 0
	while k < len(body):
		ch = body[k]
		if ch == "\\":
			k += 1
			e = body[k] if k < len(body) else ""
			if e == "u":
				out.append(chr(int(body[k + 1:k + 5], 16)))
				k += 4
			elif e in _ESC:
				out.append(_ESC[e])
			else:
				raise SpecParseError(f"unsupported escape \\{e}", ln, col + k + 1)
		else:
			out.append(ch)
		k += 1
	return "".join(out)


class _Flow:
	def __init__(self, s: str, pos):
		self.s, self.pos, self.p = s, pos, 0

	def at(self):
		return self.pos[min(self.p, len(self.pos) - 1)]

	def err(self, msg):
		raise SpecParseError(msg, *self.at())

	def ws(self):
		while self.p < len(self.s) and self.s[self.p] == " ":
			self.p += 1

	def value(self):
		self.ws()
		if self.p >= len(self.s):
			self.err("unexpected end of flow collection")
		ch = self.s[self.p]
		if ch == "{":
			return self.mapping()
		if ch == "[":
			return self.seq()
		if ch in "&*":
			self.err("anchors and aliases are not supported")
		if ch == "!":
			self.err("tags are not supported")
		if ch in "\"'":
			return self.quoted()
		return self.plain(",]}")

	def quoted(self):
		end = _Parser._quote_end(self.s, self.p)
		if end is None:
			self.err("unterminated quoted string")
		ln, col = self.at()
		val = _unquote(self.s[self.p:end + 1], ln, col)
		self.p = end + 1
		return val

	def plain(self, stops, key=False):
		start = self.p
		while self.p < len(self.s):
			ch = self.s[self.p]
			if ch in stops or (key and ch == ":" and (self.s[self.p + 1:self.p + 2] in (" ", ""))):
				break
			self.p += 1
		tok = self.s[start:self.p].strip()
		if not tok:
			self.err("empty value")
		ln, col = self.pos[start]
		return _scalar(tok, ln, col)

	def seq(self):
		self.p += 1
		out = []
		while True:
			self.ws()
			if self.p >= len(self.s):
				self.err("unterminated flow sequence")
			if self.s[self.p] == "]":
				self.p += 1
				return out
			out.append(self.value())
			self.ws()
			if self.p < len(self.s) and self.s[self.p] == ",":
				self.p += 1
			elif self.p < len(self.s) and self.s[self.p] != "]":
				self.err("expected , or ]")

	def mapping(self):
		self.p += 1
		out = {}
		while True:
			self.ws()
			if self.p >= len(self.s):
				self.err("unterminated flow mapping")
			if self.s[self.p] == "}":
				self.p += 1
				return out
			if self.s[self.p] in "\"'":
				key = self.quoted()
			else:
				key = self.plain(",}]", key=True)
			self.ws()
			if self.p >= len(self.s) or self.s[self.p] != ":":
				self.err("expected ':' after key")
			self.p += 1
			self.ws()
			if key in out:
				self.err(f"duplicate key {key!r}")
			if self.p < len(self.s) and self.s[self.p] in ",}":
				out[key] = None
			else:
				out[key] = self.value()
			self.ws()
			if self.p < len(self.s) and self.s[self.p] == ",":
				self.p += 1
			elif self.p < len(self.s) and self.s[self.p] != "}":
				self.err("expected , or }")
