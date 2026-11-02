from .models import Analysis

# Deliberately narrow normalization: never guess version or '-or-later' semantics.
ALIASES = {"mit license": "MIT", "mit": "MIT", "apache-2.0": "Apache-2.0"}
CATEGORIES = {
    "MIT": "permissive",
    "Apache-2.0": "permissive",
    "BSD-2-Clause": "permissive",
    "BSD-3-Clause": "permissive",
    "ISC": "permissive",
    "Zlib": "permissive",
    **{
        f"{family}-{version}-{suffix}": "copyleft"
        for family, versions in [
            ("GPL", ["2.0", "3.0"]),
            ("LGPL", ["2.1", "3.0"]),
            ("AGPL", ["3.0"]),
        ]
        for version in versions
        for suffix in ["only", "or-later"]
    },
    "MPL-2.0": "copyleft",
    "EPL-2.0": "copyleft",
}


def normalize_license(analysis: Analysis) -> Analysis:
    name = analysis.license_name
    if name:
        name = ALIASES.get(name.strip().lower(), name.strip())
    name = name or None
    expected = CATEGORIES.get(name or "")
    if expected and analysis.license_category not in {expected, "unknown"}:
        raise ValueError("License category disagrees with the known SPDX identifier")
    return analysis.model_copy(update={"license_name": name})
