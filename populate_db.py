import json
import os
from app.database import engine, SessionLocal, Base
from app.models import Tea

def main():
    # 1) Создаём таблицы, если их нет
    Base.metadata.create_all(bind=engine)

    db = SessionLocal()
    with open("migrations/database.json", encoding="utf-8") as f:
        data = json.load(f)["categories"]

    for category, items in data.items():
        for key, obj in items.items():
            name = obj["name"]
            # Пропускаем, если запись уже есть
            if db.query(Tea).filter(Tea.name == name).first():
                continue

            tea = Tea(
                name=name,
                category=category,
                price=obj.get("price", 0),
                weight=obj.get("weight"),
                description=obj.get("desc"),
                photo_url=obj.get("photo"),
                is_active=True
            )
            db.add(tea)
    db.commit()
    db.close()

if __name__ == "__main__":
    main()
