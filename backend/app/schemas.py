"""Shapes of the JSON the API receives and sends back.

Pydantic checks incoming data against these classes automatically:
a wrong type or a value outside the allowed ones → FastAPI answers 422
before our code even runs.
"""

from typing import Literal

from pydantic import BaseModel, Field

Answer = Literal[1, 3, 5]  # the 3 emoji buttons of each check-in question


class CheckinIn(BaseModel):
    """What the phone sends after the 5-tap morning check-in."""
    energy: Answer
    sleep: Answer
    motivation: Answer
    soreness: Answer
    minutes: int = Field(ge=15, le=180)  # time available today


class CheckinOut(BaseModel):
    """What the API sends back: the traffic light and the workout format."""
    score: int
    level: Literal["vert", "orange", "rouge"]
    format: Literal["complete", "minimale", "express"]
