from pydantic import BaseModel


class TickerCreate(BaseModel):
    symbol: str
    sector: str | None = None
    company_name: str | None = None


class TickerOut(BaseModel):
    model_config = {"from_attributes": True}

    symbol: str
    sector: str | None
    company_name: str | None
