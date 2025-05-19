# app/schemas.py

from typing import Optional
from pydantic import BaseModel, constr, condecimal


class TeaBase(BaseModel):
    name: constr(min_length=1, max_length=200)
    category: constr(min_length=1, max_length=100)
    origin: Optional[constr(max_length=150)] = None
    description: Optional[str] = None
    price: condecimal(max_digits=10, decimal_places=2)
    weight: Optional[condecimal(max_digits=10, decimal_places=2)] = None
    photo_url: Optional[str] = None
    is_active: Optional[bool] = True


class TeaCreate(TeaBase):
    """
    Схема для создания нового чая. Поля: name, category, origin, description,
    price, weight, photo_url. Поле is_active можно не указывать (по умолчанию True).
    """
    pass


class TeaUpdate(BaseModel):
    """
    Схема для обновления чая. Все поля опциональные (можно передать только часть).
    """
    name: Optional[constr(min_length=1, max_length=200)] = None
    category: Optional[constr(min_length=1, max_length=100)] = None
    origin: Optional[constr(max_length=150)] = None
    description: Optional[str] = None
    price: Optional[condecimal(max_digits=10, decimal_places=2)] = None
    weight: Optional[condecimal(max_digits=10, decimal_places=2)] = None
    photo_url: Optional[str] = None
    is_active: Optional[bool] = None


class TeaRead(TeaBase):
    """
    Схема для выдачи клиенту (при GET): к базовым полям добавляем id, created_at, updated_at.
    """
    id: int
    created_at: str
    updated_at: str

    class Config:
        orm_mode = True
