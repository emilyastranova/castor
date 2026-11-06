from fastapi import APIRouter

from . import (
    activity,
    agent_tokens,
    agents,
    app_settings,
    assets,
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
    transfer,
    users,
    variables,
)

api_router = APIRouter(prefix="/api")
for module in (auth, clients, projects, tasks, jobs, agents, attachments, activity, commands, parsers, playbooks, variables, notifications, agent_tokens, users, dashboard, tools, notes, search, people, assets, transfer, app_settings):
    api_router.include_router(module.router)
