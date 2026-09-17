"""
Админ-команды управления каталогом прямо из Telegram (только для ID из ADMIN).

/hide                                — список товаров в продаже с их ID
/hide <ID | часть названия>          — скрыть товар (закончился)
/show                                — список скрытых товаров с их ID
/show <ID | часть названия>          — вернуть товар в продажу
/price <ID | часть названия> <цена>  — изменить цену

Товар не удаляется из БД, а скрывается через is_active=False: описание и фото
сохраняются, и при поступлении товар возвращается одной командой.
"""
import html
import logging
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from aiogram import Dispatcher, F
from aiogram.filters import Command, CommandObject
from aiogram.types import BotCommand, Message

from app.database import SessionLocal
from app.crud import (
    find_teas_by_name,
    get_tea_any,
    list_teas_by_status,
    set_tea_active,
    set_tea_price,
)
from config import ADMIN
from admin_tools import split_message

logger = logging.getLogger(__name__)

MAX_PRICE = Decimal("99999999.99")  # предел колонки price: Numeric(10, 2)
MAX_MATCHES_SHOWN = 10              # сколько совпадений показывать при неоднозначном названии

# Команды для меню Telegram — показываются только админам (см. setup_bot_commands в bot.py)
ADMIN_BOT_COMMANDS = [
    BotCommand(command="hide", description="🛠 Скрыть товар (закончился)"),
    BotCommand(command="show", description="🛠 Вернуть товар в продажу"),
    BotCommand(command="price", description="🛠 Изменить цену товара"),
]

HIDE_USAGE = (
    "Использование:\n"
    "<code>/hide 12</code> — скрыть товар по ID\n"
    "<code>/hide лунцзин</code> — скрыть по части названия"
)
SHOW_USAGE = (
    "Использование:\n"
    "<code>/show 12</code> — вернуть товар по ID\n"
    "<code>/show лунцзин</code> — вернуть по части названия"
)
PRICE_USAGE = (
    "Использование:\n"
    "<code>/price 12 2600</code> — цена по ID\n"
    "<code>/price лунцзин 2600</code> — цена по части названия"
)

# Настраиваются в register_admin_catalog
_on_catalog_changed = lambda: None
_category_order = []


def _fmt_price(price) -> str:
    price = Decimal(price)
    return f"{price:.0f}₽" if price == price.to_integral_value() else f"{price:.2f}₽"


def _tea_line(tea) -> str:
    # <code> делает ID копируемым по нажатию в Telegram
    return f"<code>{tea.id}</code> — {html.escape(tea.name)} · {_fmt_price(tea.price)}"


def _format_grouped(teas) -> str:
    """Список товаров, сгруппированный по категориям в порядке каталога."""
    groups = {}
    for tea in teas:
        groups.setdefault(tea.category, []).append(tea)
    ordered = [c for c in _category_order if c in groups]
    ordered += sorted(c for c in groups if c not in ordered)

    lines = []
    for category in ordered:
        lines.append(f"\n<b>{html.escape(category)}</b>")
        lines.extend(_tea_line(t) for t in groups[category])
    return "\n".join(lines)


def _format_candidates(candidates) -> str:
    shown = candidates[:MAX_MATCHES_SHOWN]
    text = "Найдено несколько товаров, укажите ID:\n\n" + "\n".join(_tea_line(t) for t in shown)
    if len(candidates) > MAX_MATCHES_SHOWN:
        text += "\n…и другие — уточните название."
    return text


def _resolve(db, query: str, is_active):
    """
    Ищет товар по ID (число) или части названия.
    Возвращает (tea, candidates): tea — если найден ровно один, иначе список совпадений.
    """
    if query.isdigit():
        return get_tea_any(db, int(query)), []
    matches = find_teas_by_name(db, query, is_active=is_active, limit=MAX_MATCHES_SHOWN + 1)
    if len(matches) == 1:
        return matches[0], []
    return None, matches


def _parse_price(raw: str):
    """'2600', '349,50', '2600₽', '2600р' → Decimal с 2 знаками; некорректное значение → None."""
    cleaned = raw.strip().rstrip("₽рР.").replace(",", ".")
    try:
        price = Decimal(cleaned)
    except InvalidOperation:
        return None
    if not price.is_finite() or price <= 0 or price > MAX_PRICE:
        return None
    return price.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


async def _answer_long(message: Message, text: str):
    for part in split_message(text):
        await message.answer(part)


async def _set_visibility(message: Message, args, make_active: bool):
    """Общая логика /hide и /show."""
    query = (args or "").strip()
    status_word = "скрытых товаров" if make_active else "товаров в продаже"
    changed = False

    try:
        with SessionLocal() as db:
            if not query:
                teas = list_teas_by_status(db, is_active=not make_active)
                if not teas:
                    text = "Скрытых товаров нет." if make_active else "В продаже нет товаров."
                else:
                    title = "🙈 Скрытые товары" if make_active else "🛍 Товары в продаже"
                    usage = SHOW_USAGE if make_active else HIDE_USAGE
                    text = f"<b>{title}:</b>\n{_format_grouped(teas)}\n\n{usage}"
            else:
                tea, candidates = _resolve(db, query, is_active=not make_active)
                if tea is None and candidates:
                    text = _format_candidates(candidates)
                elif tea is None:
                    text = f"Не найдено среди {status_word}: «{html.escape(query)}»."
                elif tea.is_active == make_active:
                    state = "уже в продаже" if make_active else "уже скрыт"
                    text = f"ℹ️ {_tea_line(tea)}\nТовар {state}."
                else:
                    tea = set_tea_active(db, tea.id, make_active)
                    changed = True
                    if make_active:
                        text = f"✅ Возвращён в продажу:\n{_tea_line(tea)}"
                    else:
                        text = (
                            f"🙈 Скрыт из каталога:\n{_tea_line(tea)}\n\n"
                            f"Вернуть: <code>/show {tea.id}</code>"
                        )
    except Exception as e:
        logger.exception("Ошибка админ-команды видимости товара: %s", e)
        await message.answer("⚠️ Ошибка базы данных, изменения не применены.")
        return

    if changed:
        _on_catalog_changed()
    await _answer_long(message, text)


async def cmd_hide(message: Message, command: CommandObject):
    await _set_visibility(message, command.args, make_active=False)


async def cmd_show(message: Message, command: CommandObject):
    await _set_visibility(message, command.args, make_active=True)


async def cmd_price(message: Message, command: CommandObject):
    parts = (command.args or "").strip().rsplit(maxsplit=1)
    if len(parts) != 2:
        await message.answer(PRICE_USAGE)
        return

    query, raw_price = parts
    price = _parse_price(raw_price)
    if price is None:
        await message.answer(f"Некорректная цена: «{html.escape(raw_price)}».\n\n{PRICE_USAGE}")
        return

    try:
        with SessionLocal() as db:
            tea, candidates = _resolve(db, query, is_active=None)
            if tea is None and candidates:
                text = _format_candidates(candidates)
            elif tea is None:
                text = f"Товар не найден: «{html.escape(query)}»."
            elif Decimal(tea.price) == price:
                text = f"ℹ️ {_tea_line(tea)}\nЦена уже такая."
            else:
                old_price = _fmt_price(tea.price)
                tea = set_tea_price(db, tea.id, price)
                text = (
                    f"💰 Цена изменена: {old_price} → <b>{_fmt_price(tea.price)}</b>\n"
                    f"<code>{tea.id}</code> — {html.escape(tea.name)}"
                )
                if not tea.is_active:
                    text += f"\n\n⚠️ Товар сейчас скрыт. Вернуть: <code>/show {tea.id}</code>"
    except Exception as e:
        logger.exception("Ошибка админ-команды /price: %s", e)
        await message.answer("⚠️ Ошибка базы данных, изменения не применены.")
        return

    await _answer_long(message, text)


def register_admin_catalog(dp: Dispatcher, on_catalog_changed=None, category_order=()):
    """
    Регистрирует админ-команды. Вызывать ДО обработчиков FSM-состояний, иначе
    команда, отправленная посреди оформления заказа/поиска, уйдёт в FSM-обработчик.
    Не-админам команды не отвечают (как будто их нет).
    """
    global _on_catalog_changed, _category_order
    if on_catalog_changed:
        _on_catalog_changed = on_catalog_changed
    _category_order = list(category_order)

    is_admin = F.from_user.id.in_(ADMIN)
    dp.message.register(cmd_hide, Command("hide"), is_admin)
    dp.message.register(cmd_show, Command("show"), is_admin)
    dp.message.register(cmd_price, Command("price"), is_admin)
