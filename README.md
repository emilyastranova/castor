# Castor

Task management tool + job orchestrator

## About

Castor is a Kanban-style collaborative task management tool that allows users to keep track of both human and computer tasks. For example, creating a card with the `nmap 192.168.0.0/16` command and moving it from "To-do" to "In-Progress" will send the command to an available agent, execute it, and record the output inside the card. Additionally, a card might just be a human task, such as "Schedule meeting with security operations team". These tasks can contain output logs, screenshots, user comments, and custom fields.

## Technical Details

Castor is made up of 6 components:

| Component | Description |
| --------- | ----------- |
| Web frontend | Monitor tasks, manage agents, etc. |
| Nginx proxy | Route web traffic |
| FastAPI Python backend | Receive commands from web frontend and communicate with the orchestrator |
| Python worker/orchestrator | Allocate tasks, manage data storage and scale agents as needed |
| MongoDB | Store jobs, outputs, screenshots, credentials, users, etc. |
| Runners/Agents | Run jobs |

### Architecture Diagram

![Architecture Diagram](/docs/assets/architecture.png)

## Installation

### Prerequisites

- Docker/Docker Compose
- Git

### Steps

1. Clone the repository

    ```bash
    git clone git@github.com: emilyastranova/castor.git
    ```

2. Change directory

    ```bash
    cd castor
    ```

3. Create a `.env` file

    ```bash
    cp .env.example .env
    ```

4. Start the services

    ```bash
    docker compose up -d
    ```

## Roadmap

This project is a work in progress. You can see the roadmap in [ROADMAP.md](./ROADMAP.md)

## License

Castor uses the MIT license, see [LICENSE.md](./LICENSE.md)
