from fastapi import FastAPI
import json

from reemote.app import app

# Save the OpenAPI schema to a file
if __name__ == "__main__":
    openapi_schema = app.openapi()  # Get the OpenAPI schema as a Python dictionary
    with open("openapi.json", "w") as f:
        json.dump(openapi_schema, f, indent=4)  # Write the schema to a file