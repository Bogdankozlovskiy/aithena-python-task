import json
from pathlib import Path

import pytest

from evals.run import summarize
from solution.models import Analysis
from solution.source_validation import extract_functions

DATASET = json.loads((Path(__file__).resolve().parents[1] / "evals/analysis.json").read_text())


@pytest.mark.parametrize("case", DATASET["cases"], ids=lambda c: c["id"])
def test_gold_schema_and_parser_agree(case):
    gold = Analysis.model_validate(case["expected"])
    actual = extract_functions(case["source"], gold.language)
    assert actual is None or actual == gold.functions


def test_dataset_split_and_ids():
    cases = DATASET["cases"]
    assert len({c["id"] for c in cases}) == len(cases)
    assert sum(c["split"] == "holdout" for c in cases) == 10
    assert sum(c["split"] == "dev" for c in cases) == 20


def test_eval_summary_does_not_confuse_repeatability_and_correctness():
    rows = [{"case": "a", "actual": {"wrong": True}, "passed": False, "calls": []}] * 2
    report = summarize(rows, "analysis")
    assert report["passed"] == 0
    assert report["exactly_repeatable_cases"] == 1


def test_eval_failure_is_not_reported_as_repeatable():
    rows = [{"case": "a", "passed": False, "calls": []}] * 2
    assert summarize(rows, "analysis")["exactly_repeatable_cases"] == 0


@pytest.mark.parametrize("version", ["v1", "current"])
def test_eval_cli_replays_frozen_prompt_without_network(monkeypatch, tmp_path, version):
    from evals import run
    from solution import llm

    for key in ["SYSTEM", "ANALYZE", "TRANSLATE", "PROMPT_VERSION"]:
        monkeypatch.setattr(llm, key, getattr(llm, key))
    monkeypatch.setenv("LLM_MODEL", "test-model")
    monkeypatch.setattr(run, "load_dotenv", lambda *a, **k: None)
    output = tmp_path / "report.json"
    monkeypatch.setattr(
        "sys.argv",
        [
            "evals",
            "--prompt-version",
            version,
            "--settings",
            "baseline",
            "--repeats",
            "1",
            "--workers",
            "1",
            "--output",
            str(output),
        ],
    )

    def fake_evaluate(case, repeat, model, kind, settings):
        assert llm.PROMPT_VERSION == ("v3" if version == "current" else "v1")
        assert settings == "baseline"
        return {
            "case": case["id"],
            "repeat": repeat,
            "passed": True,
            "actual": case["expected"],
            "calls": [],
        }

    monkeypatch.setattr(run, "evaluate", fake_evaluate)
    assert run.main() == 0
    report = json.loads(output.read_text())
    assert report["summary"]["total"] == 20
    assert report["prompt_version"] == ("v3" if version == "current" else "v1")
    expected_prompts = (
        run.load_current_prompt()
        if version == "current"
        else json.loads((run.ROOT / "prompts-v1.json").read_text())
    )
    assert report["prompts"] == {
        "system": expected_prompts["system"],
        "task": expected_prompts["analyze"],
    }


def test_eval_cli_rejects_unbounded_repeats(monkeypatch, tmp_path):
    from evals import run

    monkeypatch.setattr(
        "sys.argv", ["evals", "--repeats", "100000", "--output", str(tmp_path / "x")]
    )
    with pytest.raises(SystemExit) as exc:
        run.main()
    assert exc.value.code == 2


def test_current_prompt_is_shared_and_packaged():
    from solution import llm
    from solution.prompts import CURRENT_VERSION, load_current_prompt

    prompts = load_current_prompt()
    assert llm.PROMPT_VERSION == CURRENT_VERSION
    assert (llm.SYSTEM, llm.ANALYZE, llm.TRANSLATE) == tuple(
        prompts[key] for key in ("system", "analyze", "translate")
    )
    assert not (Path(__file__).parents[1] / f"evals/prompts-{CURRENT_VERSION}.json").exists()


@pytest.mark.parametrize(
    "case",
    json.loads((Path(__file__).parents[1] / "evals/security.json").read_text())["cases"],
    ids=lambda c: c["id"],
)
def test_security_gold_matches_independent_parser(case):
    expected = Analysis.model_validate(case["expected"])
    assert extract_functions(case["source"], expected.language) == expected.functions
