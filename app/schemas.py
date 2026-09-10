from pydantic import BaseModel, Field

class SuccessMessage(BaseModel):
    success_message : str = Field(..., description="The success message")
    