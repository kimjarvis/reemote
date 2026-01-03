# Reemote

An API for controlling remote systems.

## Installation

Install the module with pip:

```bash
pip install reemote
```

## ReST API

The server for the optional ReST API can be started locally with:

```bash
reemote --port=8006 
```

Parameter, such as the port number, are passed to [uvicorn](https://uvicorn.dev/#command-line-options) except:

* `--inventory`: The inventory file path (optional).
* `--logging`: The logging file path (optional).

## Documentation

[Home page](https://reemote.org/)

[API Documentation](redoc-static.html)
