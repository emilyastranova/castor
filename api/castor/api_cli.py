#!/usr/bin/env python3
import typer
import click
import json
from typing import List

app = typer.Typer()

# Helper function to load data from a JSON file
def load_data():
    try:
        with open("data.json", "r") as file:
            return json.load(file)
    except FileNotFoundError:
        return {"engagements": {}, "users": {}, "jobs": {}}

# Helper function to save data to a JSON file
def save_data(data):
    with open("data.json", "w") as file:
        json.dump(data, file)

# Engagement commands
@app.command("engagement")
def engagement_command(
    action: str = typer.Argument(..., metavar="ACTION", help="Action to perform (create, list, delete)"),
    name: str = typer.Option(None, "--name", "-n", help="Engagement name"),
):
    data = load_data()
    engagements = data["engagements"]

    if action == "create":
        if name in engagements:
            typer.echo(f"Engagement '{name}' already exists.")
        else:
            engagements[name] = {}
            save_data(data)
            typer.echo(f"Engagement '{name}' created.")
    elif action == "list":
        typer.echo("Engagements:")
        for engagement in engagements:
            typer.echo(f"- {engagement}")
    elif action == "delete":
        if name in engagements:
            del engagements[name]
            save_data(data)
            typer.echo(f"Engagement '{name}' deleted.")
        else:
            typer.echo(f"Engagement '{name}' not found.")

# User commands
@app.command("user")
def user_command(
    action: str = typer.Argument(..., metavar="ACTION", help="Action to perform (create, list, delete)"),
    name: str = typer.Option(None, "--name", "-n", help="User name"),
):
    data = load_data()
    users = data["users"]

    if action == "create":
        if name in users:
            typer.echo(f"User '{name}' already exists.")
        else:
            users[name] = {}
            save_data(data)
            typer.echo(f"User '{name}' created.")
    elif action == "list":
        typer.echo("Users:")
        for user in users:
            typer.echo(f"- {user}")
    elif action == "delete":
        if name in users:
            del users[name]
            save_data(data)
            typer.echo(f"User '{name}' deleted.")
        else:
            typer.echo(f"User '{name}' not found.")

# Job commands
@app.command("job")
def job_command(
    action: str = typer.Argument(..., metavar="ACTION", help="Action to perform (create, list, delete)"),
    name: str = typer.Option(None, "--name", "-n", help="Job name"),
):
    data = load_data()
    jobs = data["jobs"]

    if action == "create":
        if name in jobs:
            typer.echo(f"Job '{name}' already exists.")
        else:
            jobs[name] = {}
            save_data(data)
            typer.echo(f"Job '{name}' created.")
    elif action == "list":
        typer.echo("Jobs:")
        for job in jobs:
            typer.echo(f"- {job}")
    elif action == "delete":
        if name in jobs:
            del jobs[name]
            save_data(data)
            typer.echo(f"Job '{name}' deleted.")
        else:
            typer.echo(f"Job '{name}' not found.")

if __name__ == "__main__":
    app()
