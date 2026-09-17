# app/crud.py

from typing import List, Optional
from sqlalchemy.orm import Session
from sqlalchemy import distinct, or_, func
from .models import Tea
from .schemas import TeaCreate, TeaUpdate


def get_tea(db: Session, tea_id: int) -> Optional[Tea]:
    """
    Возвращает один активный чай по его ID, или None, если не найден.
    """
    return db.query(Tea).filter(Tea.id == tea_id, Tea.is_active == True).first()


def get_tea_by_name(db: Session, name: str) -> Optional[Tea]:
    """
    Возвращает активный чай по точному совпадению имени или None.
    """
    return db.query(Tea).filter(Tea.name == name, Tea.is_active == True).first()


def get_teas(
    db: Session,
    skip: int = 0,
    limit: int = 100,
    category: Optional[str] = None
) -> List[Tea]:
    """
    Возвращает список активных чаёв.
    Если category задана, фильтрует по ней.
    """
    query = db.query(Tea).filter(Tea.is_active == True)
    if category:
        query = query.filter(Tea.category == category)
    return query.offset(skip).limit(limit).all()


def create_tea(db: Session, tea: TeaCreate) -> Tea:
    """
    Создаёт новый чай по данным из TeaCreate.
    """
    new_tea = Tea(
        name=tea.name,
        category=tea.category,
        origin=tea.origin,
        description=tea.description,
        price=tea.price,
        weight=tea.weight,
        photo_url=tea.photo_url,
        is_active=tea.is_active,
    )
    db.add(new_tea)
    db.commit()
    db.refresh(new_tea)
    return new_tea


def update_tea(db: Session, tea_id: int, tea: TeaUpdate) -> Optional[Tea]:
    """
    Обновляет данные существующего чая (из TeaUpdate). Возвращает обновлённый объект или None, если не найден.
    """
    db_item = get_tea(db, tea_id)
    if not db_item:
        return None

    if tea.name is not None:
        db_item.name = tea.name
    if tea.category is not None:
        db_item.category = tea.category
    if tea.origin is not None:
        db_item.origin = tea.origin
    if tea.description is not None:
        db_item.description = tea.description
    if tea.price is not None:
        db_item.price = tea.price
    if tea.weight is not None:
        db_item.weight = tea.weight
    if tea.photo_url is not None:
        db_item.photo_url = tea.photo_url
    if tea.is_active is not None:
        db_item.is_active = tea.is_active

    db.commit()
    db.refresh(db_item)
    return db_item


def delete_tea(db: Session, tea_id: int) -> bool:
    """
    «Мягкое» удаление: просто отмечаем is_active=False.
    Возвращает True, если объект нашёлся и был деактивирован, иначе False.
    """
    db_item = get_tea(db, tea_id)
    if not db_item:
        return False
    db_item.is_active = False
    db.commit()
    return True


# ========== Новые функции для бота ==========

def get_all_categories(db: Session) -> List[str]:
    """
    Возвращает список уникальных категорий (строки) из таблицы teas, где is_active=True.
    """
    # db.query(Tea.category).filter(Tea.is_active==True).distinct().all() вернёт список кортежей [(категория1,), (категория2,), ...]
    rows = db.query(distinct(Tea.category)).filter(Tea.is_active == True).all()
    # Распакуем кортежи в простой список строк:
    return [row[0] for row in rows]


def get_teas_by_category(db: Session, category: str) -> List[Tea]:
    """
    Возвращает все активные чаи, у которых поле category совпадает с переданной строкой.
    """
    return db.query(Tea).filter(Tea.category == category, Tea.is_active == True).all()


def get_random_tea(
    db: Session,
    exclude_id: Optional[int] = None,
    exclude_categories: Optional[List[str]] = None,
) -> Optional[Tea]:
    """
    Возвращает случайный активный чай (через SQL random(), без загрузки всего каталога).
    exclude_id — id, который нужно исключить (чтобы «ещё раз» не выдавал тот же чай);
    если после его исключения ничего не осталось — исключение id игнорируется.
    exclude_categories — категории, которые не участвуют в ролле (посуда, фигурки и т.п.).
    """
    base = db.query(Tea).filter(Tea.is_active == True)
    if exclude_categories:
        base = base.filter(Tea.category.notin_(exclude_categories))
    if exclude_id is not None:
        tea = base.filter(Tea.id != exclude_id).order_by(func.random()).first()
        if tea:
            return tea
    return base.order_by(func.random()).first()


# ========== Админ-функции (работают и со скрытыми товарами) ==========

def get_tea_any(db: Session, tea_id: int) -> Optional[Tea]:
    """Товар по ID независимо от is_active (в отличие от get_tea)."""
    return db.query(Tea).filter(Tea.id == tea_id).first()


def find_teas_by_name(
    db: Session,
    text: str,
    is_active: Optional[bool] = None,
    limit: int = 20,
) -> List[Tea]:
    """Товары, в названии которых есть text; is_active=None — без фильтра по статусу."""
    query = db.query(Tea).filter(Tea.name.ilike(f"%{text}%"))
    if is_active is not None:
        query = query.filter(Tea.is_active == is_active)
    return query.order_by(Tea.category, Tea.name).limit(limit).all()


def list_teas_by_status(db: Session, is_active: bool) -> List[Tea]:
    """Все товары с заданным статусом, отсортированные по категории и названию."""
    return (
        db.query(Tea)
        .filter(Tea.is_active == is_active)
        .order_by(Tea.category, Tea.name)
        .all()
    )


def set_tea_active(db: Session, tea_id: int, is_active: bool) -> Optional[Tea]:
    """Скрыть/вернуть товар. Возвращает обновлённый объект или None, если не найден."""
    tea = get_tea_any(db, tea_id)
    if not tea:
        return None
    tea.is_active = is_active
    db.commit()
    db.refresh(tea)
    return tea


def set_tea_price(db: Session, tea_id: int, price) -> Optional[Tea]:
    """Изменить цену товара (в т.ч. скрытого). Возвращает обновлённый объект или None."""
    tea = get_tea_any(db, tea_id)
    if not tea:
        return None
    tea.price = price
    db.commit()
    db.refresh(tea)
    return tea


def search_teas(db: Session, query_text: str) -> List[Tea]:
    """
    Ищет чаи по части названия или описания (иконка ilike, регистронезависимый поиск).
    """
    return (
        db.query(Tea)
        .filter(
            Tea.is_active == True,
            or_(
                Tea.name.ilike(f"%{query_text}%"),
                Tea.description.ilike(f"%{query_text}%")
            )
        )
        .all()
    )
