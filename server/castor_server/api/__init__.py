from fastapi import APIRouter

from . import (
    activity,
    agents,
    attachments,
    auth,
    clients,
    commands,
    jobs,
    parsers,
    playbooks,
    projects,
    tasks,
)

api_router = APIRouter(prefix="/api")
for module in (auth, clients, projects, tasks, jobs, agents, attachments, activity, commands, parsers, playbooks):
    api_router.include_router(module.router)
