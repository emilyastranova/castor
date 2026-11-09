from fastapi import APIRouter

from . import (
    activity,
    agent_tokens,
    agents,
    ai,
    alerts,
    app_settings,
    assets,
    attachments,
    auth,
    clients,
    commands,
    dashboard,
    jobs,
    mcp,
    notes,
    notifications,
    parsers,
    people,
    playbooks,
    projects,
    search,
    ssh_hosts,
    tags,
    tasks,
    tokens,
    tools,
    transfer,
    users,
    variables,
)

api_router = APIRouter(prefix="/api")
for module in (auth, clients, projects, tasks, jobs, agents, attachments, activity, commands, parsers, playbooks, variables, notifications, agent_tokens, users, dashboard, tools, notes, search, people, assets, transfer, app_settings, alerts, ai, tokens, mcp, ssh_hosts, tags):
    api_router.include_router(module.router)
# AI drafts for job outputs live under /jobs and /parser-proposals (next to parsers.router).
api_router.include_router(parsers.proposals)
api_router.include_router(ssh_hosts.config_router)
api_router.include_router(tags.project_router)
