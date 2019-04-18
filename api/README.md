# Castor CLI/Backend

## Quickstart

Install the requirements and run the server

```shell
pip install -r requirements.txt
```

```shell
uvicorn castor_server:castor --reload
```

## Folder Structure

```shell
.
├── api_cli.py
├── api_server.py
├── core
│   ├── api
│   │   ├── comments.py
│   │   └── __init__.py
│   ├── database
│   │   └── mongodb.py
│   ├── globals.py
│   ├── __init__.py
│   └── models
│       ├── comment.py
│       └── user.py
├── plugins
│   └── __init__.py
└── requirements.txt
```
