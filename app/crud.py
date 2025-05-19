from typing import List, Optional
from sqlalchemy.orm import Session
from sqlalchemy import distinct, or_
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
