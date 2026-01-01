# Reemote

An API for controlling remote systems.

# Installation

Install the module with pip:

```bash
pip install reemote
```

The optional ReST API can be started with:

```bash
reemote --port=8001 
```

All parameter are passed to (uvicorn)[https://uvicorn.dev/#command-line-options] except:

* `--inventory`: An inventory file (optional).
* `--logging`: The logging file (optional).

