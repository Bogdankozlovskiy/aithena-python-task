"""Enforce the interpreter contract, including direct source execution."""

import runpy
import sys
from pathlib import Path

import pytest


@pytest.mark.parametrize("version", [(3, 12), (3, 14)])
def test_unsupported_python_is_rejected(monkeypatch, version):
    monkeypatch.setattr(sys, "version_info", version)
    with pytest.raises(RuntimeError, match="Python 3.13 is required"):
        runpy.run_path(str(Path(__file__).parents[1] / "solution" / "__init__.py"))


def test_test_runner_uses_python_313():
    assert sys.version_info[:2] == (3, 13)
