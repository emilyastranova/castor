#!/usr/bin/env python
"""Websocket server for controlling agents."""

import os
import sys
import json
import asyncio
from bson.objectid import ObjectId
import websockets
from dotenv import load_dotenv
from loguru import logger
from castor_lib.core.models import Job, Agent
from castor_lib.core.database import CastorDatabase, DatabaseConfig

# Load environment variables
load_dotenv()

agent_connections = {}
command_queue = asyncio.Queue()
DB = None


async def send_message(message: dict):
    """Send a message to all connected clients"""
    for connection in agent_connections.values():
        await connection["websocket"].send(json.dumps(message))


async def get_ready_agent():
    """Get all the next available agent"""
    logger.debug("Waiting for next available agent...")
    # Wait for an agent to be ready
    while True:
        for connection in agent_connections.values():
            if connection["status"] == "ready":
                return connection["websocket"]
        await asyncio.sleep(1)


async def get_database_jobs() -> list[Job]:
    """Get all the jobs from the database"""
    logger.debug("Getting jobs from database...")
    jobs = []
    # While there are no jobs, wait for 1 second
    while len(jobs) == 0:
        # Get all pending jobs
        jobs = DB.get_collection(Job)
        # Get all pending jobs
        jobs = [job for job in jobs if job.status == "pending"]
        await asyncio.sleep(1)
    return jobs

async def job_builder(job: Job):
    """Takes a Job and builds the command from args"""
    command = job.command
    args = job.args
    # Build the command
    if command == "shell":
        return f"{args['command']}"
    else:
        # TODO: Add support for other commands
        return None

async def execute_job(job: Job):
    """Send a job to the next available agent"""
    agent = await get_ready_agent()
    if agent is not None:
        logger.info(f"Sending job to {agent.id}")
        job.command = await job_builder(job)
        if job.command is None:
            logger.error(f"Unknown command: {job.command}")
            return {"status": "error", "message": "Unknown command"}
        # Send job to agent
        await agent.send(json.dumps({"type": "job", "data": dict(job)}, default=str))
        # Update agent status
        agent_connections[agent.id]["status"] = "busy"
        # Make dollar sign query
        DB.update(Job, {"_id": ObjectId(job.id)}, {"status": "running"})
        # TODO: Need to get agent ID from database once agents are implemented
        # in the DB
        # DB.update(Job, {"_id": ObjectId(job.id)}, {"agent_id": agent.id})
        return {"status": "success", "message": f"Job sent to {agent.id}"}
    logger.info("No agents available")
    return {"status": "error", "message": "No agents available"}


async def register(websocket: websockets.WebSocketServerProtocol):
    """Register a new client"""
    connection_info = {
        "id": websocket.id,
        "status": "pending",
        "websocket": websocket
    }
    agent_connections[websocket.id] = connection_info
    logger.info(f"Client registered: {websocket.id}")


async def unregister(websocket: websockets.WebSocketServerProtocol):
    """Unregister a client"""
    del agent_connections[websocket.id]
    logger.info(f"Client unregistered: {websocket.id}")


async def process_message(message: dict):
    """Process the message"""
    try:
        if message["type"] == "output":
            job_id = message["data"]["_id"]
            logger.info(f"Received output for job {job_id}")
            output = message["data"]["output"]
            # If theres a trailing newline, remove it
            if output.endswith("\n"):
                output = output[:-1]
            logger.debug(f"Command output: {output}")
            # Update job status
            output_formatted = {"stdout": output}
            DB.update(Job, {"_id": ObjectId(job_id)}, {"logs": output_formatted, "status": "completed"})
        elif message["type"] == "checkin":
            logger.info("Agent checked in")
            # Get OS, user and hostname
            agent_os = message["data"]["os"]
            user = message["data"]["user"]
            hostname = message["data"]["hostname"]
            logger.debug(f"Agent information: {agent_os}, {user}, {hostname}")
            # Update agent info
            return {"action": "update_info", "data": {"os": agent_os, "user": user, "hostname": hostname}}
        elif message["type"] == "status":
            logger.debug(f"Agent status updated to {message['data']}")
            # Update agent status
            return {"action": "update_status", "data": {"status": message["data"]}}
        else:
            logger.info("Received unknown message type")
            logger.debug(f"Unknown message type: {message['type']}")
    except KeyError:
        logger.info("Received invalid message")
        logger.debug(f"Invalid message: {message}")

    return None


async def database_poller():
    """Poll the database for new jobs"""
    try:
        while True:
            logger.debug("Polling database for new jobs...")
            jobs = await get_database_jobs()
            logger.debug(f"Received {len(jobs)} new jobs")
            # Add jobs to queue
            for job in jobs:
                # Mark as received
                DB.update(Job, {"_id": ObjectId(job.id)}, {"status": "received"})
                command_queue.put_nowait(job)
            # Sleep for 5 seconds
            await asyncio.sleep(1)
    except websockets.exceptions.ConnectionClosedError:
        pass


async def job_handler():
    """Handle sending jobs to agents"""
    try:
        while True:
            logger.debug("Waiting for jobs...")
            job = await command_queue.get()
            logger.debug(
                f"Job {job.id} received, sending to next available agent")
            # Let database know that job is looking for an agent
            DB.update(Job, {"_id": ObjectId(job.id)}, {"status": "allocating"})
            # Send job to next available agent
            await execute_job(job)
            logger.debug(f"Job {job.id} sent to agent")
            # Sleep for 5 seconds
            await asyncio.sleep(1)
    except websockets.exceptions.ConnectionClosedError:
        pass


async def agent_handler(websocket: websockets.WebSocketServerProtocol):
    """Handle sending and receiving messages concurrently"""
    logger.info(f"Client connected {websocket.id}")

    # Register the client
    await register(websocket)

    logger.debug("Waiting for messages...")
    try:
        async for message in websocket:
            action = await process_message(json.loads(message))
            if action is not None:
                if action["action"] == "update_status":
                    agent_connections[websocket.id]["status"] = action["data"]["status"]
                    logger.info(
                        f"Updated status for {websocket.id} to {action['data']['status']}")
                elif action["action"] == "update_info":
                    agent_connections[websocket.id]["info"] = action["data"]
                    # Add agent to the database
                    agent = Agent(
                        os=action["data"]["os"],
                        username=action["data"]["user"],
                        hostname=action["data"]["hostname"],
                        websocket_id=str(websocket.id),
                        status="ready"
                    )
                    DB.insert(Agent, agent)
                    logger.debug(f"Updated info for {websocket.id}")
    except websockets.exceptions.ConnectionClosedError:
        pass

    # # Unregister the client
    logger.info(f"Client disconnected: {websocket.id}")
    await unregister(websocket)
    logger.debug(f"Number of connected clients: {len(agent_connections)}")


async def main():
    """Main loop"""
    # Start job handler
    asyncio.create_task(job_handler())
    # Start database poller
    asyncio.create_task(database_poller())
    # Get server port from env
    try:
        SERVER_PORT = os.getenv("SERVER_PORT")
        if not  SERVER_PORT:
            raise IndexError
    except IndexError:
        logger.error("Please provide the server IP and port")
        sys.exit(1)
    # Start server
    logger.info(f"Starting server on {SERVER_PORT}")
    async with websockets.serve(agent_handler, "", SERVER_PORT):
        await asyncio.Future()  # run forever


if __name__ == "__main__":
    logger.disable("castor_lib")
    logger.info("Starting server")
    logger.info("Connecting to the MongoDB database...")
    database_config = DatabaseConfig(
        host=os.getenv("CASTOR_SERVER_DB_HOST"),
        port=int(os.getenv("CASTOR_SERVER_DB_PORT")),
        username=os.getenv("CASTOR_SERVER_DB_USERNAME"),
        password=os.getenv("CASTOR_SERVER_DB_PASSWORD"),
    )
    DB = CastorDatabase(database_config)
    logger.info("Connected to the MongoDB database")
    logger.info("Starting main loop")
    asyncio.run(main())
