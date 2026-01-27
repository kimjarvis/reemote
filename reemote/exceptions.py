from pydantic import BaseModel, Field


class ReturnCodeNotZeroError(Exception):
    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


# Define the schema for the HTTP 500 error response
class BadRequestErrorResponse(BaseModel):
    detail: str = Field(
        description="The host cannot process the request due to a client error."
    )


# Define the schema for the HTTP 503 error response
class ServiceUnavailableErrorResponse(BaseModel):
    detail: str = Field(
    description="The asyncssh error message returned when SSH "
                "failed to establish SSH connection to the required service."
                "Refer to https://asyncssh.readthedocs.io/en/latest/api.html#exceptions"

    )

# Define the schema for the HTTP 500 error response
class InternalServerErrorResponse(BaseModel):
    detail: str = Field(
    description="The asyncssh error message returned when SSH "
        "session was established but process creation failed."
        "Refer to https://asyncssh.readthedocs.io/en/latest/api.html#exceptions"
    )
