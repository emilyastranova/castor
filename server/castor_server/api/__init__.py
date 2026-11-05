from fastapi import APIRouter

from . import (
    activity,
    agent_tokens,
    agents,
    attachments,
    auth,
    clients,
    commands,
    jobs,
    notifications,
    parsers,
    playbooks,
    projects,
    tasks,
    users,
    variables,
)

api_router = APIRouter(prefix="/api")
for module in (auth, clients, projects, tasks, jobs, agents, attachments, activity, commands, parsers, playbooks, variables, notifications, agent_tokens, users):
    api_router.include_router(module.router)
