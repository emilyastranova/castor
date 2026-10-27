from fastapi import APIRouter

from . import (
    activity,
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
    variables,
)

api_router = APIRouter(prefix="/api")
for module in (auth, clients, projects, tasks, jobs, agents, attachments, activity, commands, parsers, playbooks, variables, notifications):
    api_router.include_router(module.router)
