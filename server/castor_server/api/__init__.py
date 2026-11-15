from fastapi import APIRouter

from . import (
    activity,
    agent_tokens,
    agents,
    ai,
    alerts,
    app_settings,
    archive,
    assets,
    attachments,
    auth,
    changes,
    clients,
    commands,
    dashboard,
    exports,
    files,
    groups,
    jobs,
    mcp,
    mcp_connections,
    moves,
    notes,
    notifications,
    parsers,
    people,
    playbooks,
    plugin_directory,
    plugin_panels,
    plugins,
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
for module in (auth, clients, projects, tasks, jobs, changes, archive, agents, attachments, files, activity, commands, parsers, playbooks, variables, notifications, agent_tokens, users, dashboard, exports, summaries, themes, plugin_directory, plugin_panels, plugins, tools, notes, posts, moves, search, people, assets, transfer, app_settings, alerts, ai, tokens, mcp, mcp_connections, ssh_hosts, tags, sso, groups):
    api_router.include_router(module.router)
# AI drafts for job outputs live under /jobs and /parser-proposals (next to parsers.router).
api_router.include_router(parsers.proposals)
api_router.include_router(ssh_hosts.config_router)
api_router.include_router(tags.project_router)
api_router.include_router(sso.admin_router)
api_router.include_router(groups.project_router)
