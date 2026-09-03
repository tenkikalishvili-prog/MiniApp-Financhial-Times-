"""Напоминания о платежах и долгах (направление C, S11).

Собирает для пользователя текст напоминания на конкретную дату: обязательные платежи
(неоплаченные в этом месяце) и открытые долги — просроченные, на сегодня и ближайшие
(в пределах ``SOON_DAYS`` дней). Если напоминать не о чем — возвращает ``None``.
Текст шлётся ботом раз в день в утренний час (см. bot/handlers/notifications.py).
"""

from __future__ import annotations

from datetime import date

from sqlalchemy.ext.asyncio import AsyncSession

from backend.services import bills as bills_svc
from backend.services import debts as debts_svc

# За сколько дней вперёд предупреждать о приближающемся сроке.
SOON_DAYS = 3


def _fmt(amount) -> str:
    return f"{float(amount):,.0f}".replace(",", " ")


def _bucket(delta: int) -> str | None:
    """Категория срочности по разнице дней (срок − сегодня)."""
    if delta < 0:
        return "overdue"
    if delta == 0:
        return "today"
    if delta <= SOON_DAYS:
        return "soon"
    return None


_MARK = {"overdue": "🔴 Просрочено", "today": "🟡 Сегодня", "soon": "⚪️ Скоро"}
_ORDER = ("overdue", "today", "soon")


def _soon_suffix(delta: int) -> str:
    """«через 1 дн.» / «через 2 дн.» / «через 3 дн.» для ближайших сроков."""
    return f" · через {delta} дн." if delta > 0 else ""


async def build_reminders(
    session: AsyncSession, user_id: int, on_date: date
) -> str | None:
    """Текст напоминания на дату ``on_date`` (локальная дата пользователя) или None."""
    period = f"{on_date.year:04d}-{on_date.month:02d}"

    # ── Платежи ──────────────────────────────────────────────────────────
    bills = await bills_svc.list_bills(session, user_id, active_only=True)
    marks = await bills_svc.marks_for_period(session, user_id, period)
    bill_lines: dict[str, list[str]] = {b: [] for b in _ORDER}
    for b in bills:
        if b.id in marks:
            continue  # уже оплачен в этом месяце
        delta = b.due_day - on_date.day
        bucket = _bucket(delta)
        if bucket is None:
            continue
        suffix = _soon_suffix(delta) if bucket == "soon" else ""
        bill_lines[bucket].append(f"{_MARK[bucket]} — {b.title} {_fmt(b.amount)} ₽ (до {b.due_day}-го){suffix}")

    # ── Долги ────────────────────────────────────────────────────────────
    debts = await debts_svc.list_debts(session, user_id, include_closed=False)
    debt_lines: dict[str, list[str]] = {b: [] for b in _ORDER}
    for d in debts:
        if d.due_date is None:
            continue
        delta = (d.due_date - on_date).days
        bucket = _bucket(delta)
        if bucket is None:
            continue
        remaining = d.amount - d.paid
        who = f"вернуть {d.counterparty}" if d.direction == "owe" else f"ждёте от {d.counterparty}"
        suffix = _soon_suffix(delta) if bucket == "soon" else ""
        debt_lines[bucket].append(f"{_MARK[bucket]} — {who} {_fmt(remaining)} ₽{suffix}")

    # ── Сборка сообщения ─────────────────────────────────────────────────
    def _section(lines: dict[str, list[str]]) -> list[str]:
        out: list[str] = []
        for bucket in _ORDER:
            out.extend(lines[bucket])
        return out

    bills_sec = _section(bill_lines)
    debts_sec = _section(debt_lines)
    if not bills_sec and not debts_sec:
        return None

    parts = ["🔔 <b>Напоминания</b>"]
    if bills_sec:
        parts.append("\n📅 <b>Обязательные платежи:</b>\n" + "\n".join(bills_sec))
    if debts_sec:
        parts.append("\n🤝 <b>Долги:</b>\n" + "\n".join(debts_sec))
    return "\n".join(parts)
