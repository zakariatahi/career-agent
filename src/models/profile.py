from pydantic import BaseModel, Field


class UserProfile(BaseModel):
    target_roles: list[str] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list)

    education: str | None = None
    experience: list[str] = Field(default_factory=list)

    preferred_locations: list[str] = Field(default_factory=list)

    remote: bool = True