"""One detached native prompt inspection; stdin/stdout are private JSON pipes."""

import contextlib
import json
import sys


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
    with contextlib.redirect_stdout(sys.stderr):
        prompt = inspect(payload["cwd"])
    json.dump({"system_prompt": prompt}, sys.stdout)
