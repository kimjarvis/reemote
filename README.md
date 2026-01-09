# Reemote

An API for controlling remote systems.

For a detailed description refer to the [Reemote home page](https://reemote.org/).

## Installation

Install the module with pip:

```bash
pip install reemote
```

## Installing the ReST API

The optional ReST API can be installed with pipx.

```bash
python3 -m venv myenv
source myenv/bin/activate
pipx install reemote
```

## Starting the ReST API server

To start the server on the local host:

```bash
reemote --port=8001 
```

Parameter, such as the port number, are passed to [uvicorn](https://uvicorn.dev/#command-line-options) except:

* `--inventory`: The inventory file path (optional).
* `--logging`: The logging file path (optional).

When the server is running, the Swagger UI can be found a http://localhost:8001/docs and API documentation can be found at http://localhost:8001/redoc. 