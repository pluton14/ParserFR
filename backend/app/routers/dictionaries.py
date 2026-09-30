"""Словари ключевых слов — веб-замена файлу dict.txt."""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, HTTPException, Response, UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import Dictionary
from ..schemas import DictionaryCreate, DictionaryOut, DictionaryUpdate, clean_keywords

router = APIRouter(prefix="/api/dictionaries", tags=["dictionaries"])


def _get_or_404(db: Session, dictionary_id: int) -> Dictionary:
    row = db.get(Dictionary, dictionary_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Словарь не найден")
    return row


@router.get("", response_model=list[DictionaryOut])
def list_dictionaries(db: Session = Depends(get_db)) -> list[Dictionary]:
    return list(
        db.execute(select(Dictionary).order_by(Dictionary.updated_at.desc())).scalars().all()
    )


@router.post("", response_model=DictionaryOut, status_code=201)
def create_dictionary(payload: DictionaryCreate, db: Session = Depends(get_db)) -> Dictionary:
    row = Dictionary(
        name=payload.name,
        description=payload.description,
        keywords=payload.keywords,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


@router.get("/{dictionary_id}", response_model=DictionaryOut)
def get_dictionary(dictionary_id: int, db: Session = Depends(get_db)) -> Dictionary:
    return _get_or_404(db, dictionary_id)


@router.patch("/{dictionary_id}", response_model=DictionaryOut)
def update_dictionary(
    dictionary_id: int, payload: DictionaryUpdate, db: Session = Depends(get_db)
) -> Dictionary:
    row = _get_or_404(db, dictionary_id)
    if payload.name is not None:
        row.name = payload.name
    if payload.description is not None:
        row.description = payload.description
    if payload.keywords is not None:
        row.keywords = clean_keywords(payload.keywords)
    db.commit()
    db.refresh(row)
    return row


@router.delete("/{dictionary_id}", status_code=204, response_class=Response)
def delete_dictionary(dictionary_id: int, db: Session = Depends(get_db)) -> Response:
    row = _get_or_404(db, dictionary_id)
    db.delete(row)
    db.commit()
    return Response(status_code=204)


@router.post("/import", response_model=DictionaryOut, status_code=201)
async def import_dictionary(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
) -> Dictionary:
    """Загрузка словаря из .txt — того самого формата, что читал dict.txt.

    Одна строка — одно слово или словосочетание; пустые строки игнорируются.
    """
    raw = await file.read()
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        text = raw.decode("cp1252", errors="replace")

    keywords = clean_keywords(
        [line.strip() for line in text.splitlines() if line.strip()]
    )
    if not keywords:
        raise HTTPException(status_code=400, detail="Файл не содержит слов для поиска")

    name = (file.filename or "dict.txt").rsplit(".", 1)[0]
    row = Dictionary(name=name, description="Импортировано из файла", keywords=keywords)
    db.add(row)
    db.commit()
    db.refresh(row)
    return row
