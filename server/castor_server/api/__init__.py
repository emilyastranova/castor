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
    exports,
    groups,
    jobs,
    mcp,
    moves,
    notes,
    notifications,
    parsers,
    people,
    playbooks,
    posts,
    projects,
    search,
    ssh_hosts,
    sso,
    summaries,
    tags,
    tasks,
    themes,
    tokens,
    tools,
    transfer,
    users,
    variables,
)

api_router = APIRouter(prefix="/api")
for module in (auth, clients, projects, tasks, jobs, agents, attachments, activity, commands, parsers, playbooks, variables, notifications, agent_tokens, users, dashboard, exports, summaries, themes, tools, notes, posts, moves, search, people, assets, transfer, app_settings, alerts, ai, tokens, mcp, ssh_hosts, tags, sso, groups):
    api_router.include_router(module.router)
# AI drafts for job outputs live under /jobs and /parser-proposals (next to parsers.router).
api_router.include_router(parsers.proposals)
api_router.include_router(ssh_hosts.config_router)
api_router.include_router(tags.project_router)
api_router.include_router(sso.admin_router)
api_router.include_router(groups.project_router)
