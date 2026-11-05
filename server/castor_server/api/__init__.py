from fastapi import APIRouter

from . import (
    activity,
    agent_tokens,
    agents,
    attachments,
    auth,
    clients,
    commands,
    dashboard,
    jobs,
    notes,
    notifications,
    parsers,
    people,
    playbooks,
    projects,
    search,
    tasks,
    tools,
    users,
    variables,
)

api_router = APIRouter(prefix="/api")
for module in (auth, clients, projects, tasks, jobs, agents, attachments, activity, commands, parsers, playbooks, variables, notifications, agent_tokens, users, dashboard, tools, notes, search, people):
    api_router.include_router(module.router)
