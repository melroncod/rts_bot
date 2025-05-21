# app/routers/teas.py

from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app import crud, schemas

router = APIRouter(prefix="/api/teas", tags=["teas"])


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@router.post("/", response_model=schemas.TeaRead, status_code=201)
def create_tea(tea: schemas.TeaCreate, db: Session = Depends(get_db)):
    db_item = crud.get_tea_by_name(db, tea.name)
    if db_item:
        raise HTTPException(status_code=400, detail="Tea with this name already exists")
    return crud.create_tea(db, tea)


@router.get("/", response_model=List[schemas.TeaRead])
def read_teas(
        skip: int = Query(0, ge=0),
        limit: int = Query(100, ge=1),
        category: Optional[str] = Query(None),
        db: Session = Depends(get_db),
):
    teas = crud.get_teas(db, skip=skip, limit=limit, category=category)
    return teas


@router.get("/{tea_id}", response_model=schemas.TeaRead)
def read_tea(tea_id: int, db: Session = Depends(get_db)):
    db_item = crud.get_tea(db, tea_id)
    if not db_item:
        raise HTTPException(status_code=404, detail="Tea not found")
    return db_item


@router.patch("/{tea_id}", response_model=schemas.TeaRead)
def update_tea(tea_id: int, tea_upd: schemas.TeaUpdate, db: Session = Depends(get_db)):
    updated = crud.update_tea(db, tea_id, tea_upd)
    if not updated:
        raise HTTPException(status_code=404, detail="Tea not found")
    return updated


@router.delete("/{tea_id}", status_code=204)
def delete_tea(tea_id: int, db: Session = Depends(get_db)):
    success = crud.delete_tea(db, tea_id)
    if not success:
        raise HTTPException(status_code=404, detail="Tea not found")
    return
