"""Apply the shipped child policy to direct-client QA fixtures.

Production bootstrap runs must inherit their real parent environment untouched;
their Rust bootstrap applies presentation.json itself. Direct loopback/control
fixtures bypass that bootstrap, so they read the exact same published policy.
"""

from pathlib import Path
from bootstrap_contract import validate_environment_defaults
from stage_runtime import read_json


def direct_client_environment(client, environment):
    presentation = read_json(Path(client).parent / "presentation.json")
    defaults = validate_environment_defaults(presentation.get("environmentDefaults", {}))
    result = dict(environment)
    inherited = {key.casefold() for key in result}
    for key, value in defaults.items():
        if key.casefold() not in inherited:
            result[key] = value
    return result
