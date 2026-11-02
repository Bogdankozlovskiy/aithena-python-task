"""The packaged current prompt is shared by production and evaluation."""

import json
from importlib.resources import files

CURRENT_VERSION = "v3"


def load_current_prompt() -> dict[str, str]:
    return json.loads(files("solution").joinpath(f"prompts-{CURRENT_VERSION}.json").read_text())
