# Installation

Install the module with pip:

```bash
pip install reemote
```

The server for the optional ReST API can be started locally with:

```bash
reemote --port=8001 
```

Parameter are passed to [uvicorn](https://uvicorn.dev/#command-line-options) except:

* `--inventory`: The inventory file (optional).
* `--logging`: The logging file (optional).