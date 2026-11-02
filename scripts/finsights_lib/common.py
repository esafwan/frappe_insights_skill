"""Shared types: Finding (lint result) and the exception hierarchy."""

from __future__ import annotations

from dataclasses import dataclass, asdict


class FinsightsError(Exception):
	"""Base error; CLI exits with `exit_code` (2 usage/parse, 3 conflict, 4 network)."""

	def __init__(self, msg: str = "", exit_code: int = 2):
		super().__init__(msg)
		self.exit_code = exit_code


class SpecParseError(FinsightsError):
	"""Spec could not be parsed; carries the 1-based line and column."""

	def __init__(self, msg: str, line: int = 0, col: int = 0):
		super().__init__(f"{line}:{col}: {msg}" if line else msg, 2)
		self.msg = msg
		self.line = line
		self.col = col


@dataclass
class Finding:
	rule: str
	severity: str  # error | warn | info
	path: str  # JSON pointer into the spec
	message: str
	fix_hint: str = ""

	def to_dict(self) -> dict:
		return asdict(self)
