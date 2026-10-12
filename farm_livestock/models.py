"""SQLAlchemy ORM models for the Farm Livestock Management System."""
from __future__ import annotations

from datetime import date, datetime
from typing import Optional

from sqlalchemy import Date, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Animal(Base):
    __tablename__ = "animals"

    id: Mapped[int] = mapped_column(primary_key=True)
    tag_id: Mapped[str] = mapped_column(String(50), unique=True, index=True)
    animal_name: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    species: Mapped[str] = mapped_column(String(20), index=True)
    breed: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    sex: Mapped[str] = mapped_column(String(10))
    production_stage: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)
    dam_id: Mapped[Optional[int]] = mapped_column(ForeignKey("animals.id", ondelete="SET NULL"), nullable=True)
    sire_id: Mapped[Optional[int]] = mapped_column(ForeignKey("animals.id", ondelete="SET NULL"), nullable=True)
    birth_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)  # unknown is allowed
    enrolled_date: Mapped[date] = mapped_column(Date, default=date.today)
    status: Mapped[str] = mapped_column(String(15), default="Active", index=True)
    planned_breeding_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    last_heat_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Animal {self.tag_id} {self.species} {self.status}>"


class BreedingEvent(Base):
    __tablename__ = "breeding_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    animal_id: Mapped[int] = mapped_column(ForeignKey("animals.id", ondelete="CASCADE"), index=True)
    service_date: Mapped[date] = mapped_column(Date)
    method: Mapped[str] = mapped_column(String(10), default="Natural")
    sire_id: Mapped[Optional[int]] = mapped_column(ForeignKey("animals.id", ondelete="SET NULL"), nullable=True)
    sire_label: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)  # bull/boar name if external
    straw_id: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    status: Mapped[str] = mapped_column(String(15), default="Served", index=True)
    actual_birth_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    litter_size: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)


class HusbandryTask(Base):
    __tablename__ = "husbandry_tasks"
    __table_args__ = (UniqueConstraint("animal_id", "source_key", name="uq_task_source"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    animal_id: Mapped[int] = mapped_column(ForeignKey("animals.id", ondelete="CASCADE"), index=True)
    breeding_id: Mapped[Optional[int]] = mapped_column(ForeignKey("breeding_events.id", ondelete="CASCADE"), nullable=True)
    template_key: Mapped[str] = mapped_column(String(50), index=True)
    source_key: Mapped[str] = mapped_column(String(120))  # idempotency key for the generator
    task_type: Mapped[str] = mapped_column(String(150))
    category: Mapped[str] = mapped_column(String(20), default="Health")
    due_date: Mapped[date] = mapped_column(Date, index=True)
    status: Mapped[str] = mapped_column(String(12), default="Pending", index=True)  # Pending|Done|Cancelled
    completed_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    auto_generated: Mapped[bool] = mapped_column(default=True)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)


class ProductionLog(Base):
    __tablename__ = "production_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    animal_id: Mapped[int] = mapped_column(ForeignKey("animals.id", ondelete="CASCADE"), index=True)
    log_date: Mapped[date] = mapped_column(Date, index=True)
    metric: Mapped[str] = mapped_column(String(30), index=True)
    value: Mapped[float] = mapped_column(Float)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)


class CostRecord(Base):
    """Feed / medical / other costs. animal_id set => individual; else species group or farm-wide ('All')."""

    __tablename__ = "cost_records"

    id: Mapped[int] = mapped_column(primary_key=True)
    animal_id: Mapped[Optional[int]] = mapped_column(ForeignKey("animals.id", ondelete="CASCADE"), nullable=True, index=True)
    species: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)  # species name or "All"
    record_date: Mapped[date] = mapped_column(Date, index=True)
    category: Mapped[str] = mapped_column(String(20))
    description: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    quantity_kg: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    amount: Mapped[float] = mapped_column(Float)
