"""One detached native prompt inspection; stdin/stdout are private JSON pipes."""

import contextlib
import json
import sys
import traceback
from pathlib import Path


def inspect(cwd):
    from agent.runtime_cwd import reset_session_cwd, set_session_cwd
    from agent.system_prompt import build_system_prompt
    from hermes_cli.prompt_size import _build_inspection_agent

    scope = set_session_cwd(cwd)
    agent = None
    try:
        agent = _build_inspection_agent("desktop")
        return build_system_prompt(agent)
    finally:
        if agent is not None:
            agent.close()
        reset_session_cwd(scope)


if __name__ == "__main__":
    payload = json.load(sys.stdin)
    # Native tools/extensions may print diagnostics. They are never part of the
    # protocol and the parent never returns them in an HTTP error.
    try:
        with contextlib.redirect_stdout(sys.stderr):
            prompt = inspect(payload["cwd"])
        result = {"system_prompt": prompt}
    except Exception as error:
        frames = traceback.extract_tb(error.__traceback__)
        result = {
            "error": {
                "kind": type(error).__name__,
                "location": frames[-1].name if frames else "inspect",
                "module": Path(frames[-1].filename).name if frames else "context_worker.py",
            }
        }
    json.dump(result, sys.stdout)
