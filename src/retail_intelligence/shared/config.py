from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url


class BranchSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=None, extra="ignore")
    branch_id: int = Field(ge=1, le=3)
    central_url: str | None = None
    demo_sync_enabled: bool = False
    database_url: str = Field(min_length=1)

    @field_validator("database_url")
    @classmethod
    def validate_database(cls, value: str) -> str:
        url = make_url(value)
        if url.drivername != "sqlite" or not url.database or url.database == ":memory:":
            raise ValueError("Branch requires a file-based SQLite URL")
        return value


class CentralSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=None, extra="ignore")
    database_url: str = Field(min_length=1)

    @field_validator("database_url")
    @classmethod
    def validate_database(cls, value: str) -> str:
        if make_url(value).drivername != "postgresql+psycopg":
            raise ValueError("Central requires a postgresql+psycopg URL")
        return value
