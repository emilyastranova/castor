"""Shell cards: a card that opens a shell on an agent to work in, for when you know what you want
to do but not yet the commands. It's a command card whose command is the shell, run in a terminal
(tty) with no timeout: it ends when it's exited or stopped. Where it runs is a command's usual
choice (the agent's own shell, or an image), and its card's network route applies. A shell sits at
its prompt all the time, so it never alerts for that (interactive.on_prompt)."""

from __future__ import annotations

from . import models as m

# bash where there is one (a login shell, so the person's profile and aliases load), else sh.
SHELL_COMMAND = "if command -v bash >/dev/null 2>&1; then exec bash -l; else exec sh -l; fi"


def apply(task: m.Task, on: bool) -> None:
    """Make the card a shell card, or an ordinary command card again (with nothing to run)."""
    if on:
        task.shell = True
        task.kind = m.TaskKind.command
        task.command = SHELL_COMMAND
        task.command_ref = None
        task.command_args = {}
        task.tty = True
        task.timeout_s = None
        task.service = None
    elif task.shell:
        task.shell = False
        if task.command == SHELL_COMMAND:
            task.command = ""
        task.tty = False
