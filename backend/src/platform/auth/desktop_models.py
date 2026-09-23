"""Public Desktop authentication contracts (compatible with shipped clients)."""

from pydantic import BaseModel, Field, SecretStr


class DesktopStartRequest(BaseModel):
    provider: str | None = None
    callback_url: str = Field(max_length=2048)
    code_challenge: str | None = None
    code_challenge_method: str | None = None


class DesktopStartResponse(BaseModel):
    state: str
    login_url: str


class DesktopBindRequest(BaseModel):
    state: str = Field(min_length=32, max_length=128)
    browser_proof: SecretStr = Field(min_length=43, max_length=128)


class DesktopCompleteRequest(BaseModel):
    state: str = Field(min_length=32, max_length=128)
    access_token: SecretStr = Field(min_length=1, max_length=16384)
    refresh_token: SecretStr = Field(min_length=1, max_length=8192)
    browser_proof: SecretStr | None = None
    expires_in: int | None = None
    user_email: str | None = None


class DesktopCompleteResponse(BaseModel):
    redirect_url: str


class DesktopExchangeRequest(BaseModel):
    code: str = Field(min_length=1, max_length=128)
    state: str = Field(min_length=1, max_length=128)
    code_verifier: SecretStr | None = None
    redirect_uri: str | None = Field(default=None, max_length=2048)
