import hashlib
import json
from datetime import datetime
from pathlib import Path

from solution.models import Result
from solution.source_validation import detect_functions


def test_documented_example_matches_current_schema_and_sources():
    root = Path(__file__).parents[1]
    report = json.loads((root / "examples/results.json").read_text())
    assert report["schema_version"] == 2
    assert datetime.fromisoformat(report["updated_at"]).tzinfo is not None
    for row in report["results"]:
        result = Result.model_validate(row)
        source = (root / "data" / result.path).read_bytes()
        assert result.source_sha256 == hashlib.sha256(source).hexdigest()
        assert result.analysis.functions == detect_functions(source.decode())
        assert result.functions_validation == "passed"
