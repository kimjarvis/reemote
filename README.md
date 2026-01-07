# Reemote

An API for controlling remote systems.

For a detailed description refer to the [Reemote home page](https://reemote.org/).



## Installation

Install with uv:

```bash
uv venv reemote
source reemote/bin/activate
uv pip install reemote
```

## Starting the ReST API 

The server for the optional ReST API can be started locally with:

```bash
uv run reemote --port=8006 
```

Parameter, such as the port number, are passed to [uvicorn](https://uvicorn.dev/#command-line-options) except:

* `--inventory`: The inventory file path (optional).
* `--logging`: The logging file path (optional).

## Starting the documentation server

```bash
mkdocs serve --dev-addr localhost:8002
```

uv install

```bash
uv run mkdocs serve --dev-addr localhost:8002
```

API documentation can be found at http://localhost:8001/redoc

The Reemote Swagger UI can be found a thttp://localhost:8001/docs