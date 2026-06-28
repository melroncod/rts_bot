import os
import sys
import time
import html
import asyncio
import logging
import uuid
import math
from contextlib import contextmanager

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from aiogram import Bot, Dispatcher, types
from aiogram.client.bot import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext

from app.database import SessionLocal
from app.models import Tea
from app.crud import get_all_categories, get_teas_by_category, get_tea, search_teas
from config import TOKEN, ADMIN, ADMIN_USER

from admin_tools import handle_admin_command, handle_user_message

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Прокси
PROXY_URL = os.getenv("PROXY_URL")

# Сессия с прокси
session = AiohttpSession(proxy=PROXY_URL) if PROXY_URL else AiohttpSession()

# Бот
bot = Bot(
    token=TOKEN,
    session=session,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    request_timeout=60
)

dp = Dispatcher()

# Корзины пользователей в памяти: {user_id: [{"tea_id": int, "quantity": int}, ...]}
# ВНИМАНИЕ: хранилище не персистентно — теряется при рестарте. Для продакшена
# рекомендуется вынести корзины/заказы в БД (см. README, раздел «Дальнейшее развитие»).
CARTS = {}

CART_CLEAR_INTERVAL = 10 * 3600  # как часто полностью очищать кеш корзин, сек
CATEGORIES_TTL = 60              # сколько секунд кешировать список категорий, сек
MAX_FIELD_LEN = 500             # максимальная длина текстовых полей заказа

# Порядок категорий в меню каталога (категории не из списка добавляются в конец)
CATEGORY_ORDER = [
    "Шу пуэры",
    "Шен пуэры",
    "Улуны",
    "Габа улуны",
    "Зелёные",
    "Красные",
    "Белые",
    "Жёлтые",
    "Хэй Ча",
    "Посуда",
    "Чайные духи",
]

# Кеш категорий, чтобы не дёргать БД на каждое текстовое сообщение
_categories_cache = {"value": [], "ts": 0.0}


@contextmanager
def db_session():
    """Контекст-менеджер сессии БД: гарантирует close() и убирает дублирование boilerplate."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_categories(force: bool = False) -> list:
    """Список активных категорий с кешированием на CATEGORIES_TTL секунд."""
    now = time.monotonic()
    if not force and _categories_cache["value"] and (now - _categories_cache["ts"] < CATEGORIES_TTL):
        return _categories_cache["value"]
    try:
        with db_session() as db:
            categories = get_all_categories(db)
        _categories_cache["value"] = categories
        _categories_cache["ts"] = now
    except Exception as e:
        logger.exception("Ошибка получения категорий: %s", e)
        categories = _categories_cache["value"]  # отдаём последнее известное значение
    return categories


def fetch_teas_map(tea_ids):
    """Один запрос вместо N: возвращает {tea_id: Tea} для переданных id (порядок не гарантирован)."""
    ids = list({tid for tid in tea_ids})
    if not ids:
        return {}
    try:
        with db_session() as db:
            teas = db.query(Tea).filter(Tea.id.in_(ids)).all()
        return {t.id: t for t in teas}
    except Exception as e:
        logger.exception("Ошибка пакетной выборки товаров: %s", e)
        return {}


def cart_lines(user_id: int):
    """
    Возвращает (lines, total) для корзины пользователя одним пакетным запросом к БД.
    lines: список (tea, quantity, subtotal). Битые/удалённые позиции пропускаются.
    """
    items = CARTS.get(user_id, [])
    if not items:
        return [], 0.0
    teas = fetch_teas_map(item["tea_id"] for item in items)
    lines = []
    total = 0.0
    for item in items:
        tea = teas.get(item["tea_id"])
        if not tea:
            continue
        subtotal = float(tea.price) * item["quantity"]
        total += subtotal
        lines.append((tea, item["quantity"], subtotal))
    return lines, total


# Фоновая задача: периодическая очистка CARTS
async def clear_cache_periodically():
    while True:
        await asyncio.sleep(CART_CLEAR_INTERVAL)
        CARTS.clear()
        logger.info("Кеш (CARTS) очищен.")


# FSM-Состояния
class OrderForm(StatesGroup):
    waiting_for_fio = State()
    waiting_for_address = State()
    waiting_for_phone = State()
    waiting_for_comment = State()
    waiting_for_promo = State()


class SearchForm(StatesGroup):
    waiting_for_query = State()


class TeaCalcForm(StatesGroup):
    waiting_for_grams = State()


# Формирование клавиатур
def main_menu_reply() -> types.ReplyKeyboardMarkup:
    buttons = [
        [types.KeyboardButton(text="Каталог"), types.KeyboardButton(text="Поиск")],
        [types.KeyboardButton(text="Корзина"), types.KeyboardButton(text="Поддержка")]
    ]
    return types.ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)


def catalog_menu_reply() -> types.ReplyKeyboardMarkup:
    """
    Формирование клавиатуры из категорий в порядке CATEGORY_ORDER.
    Категории, которых нет в списке, добавляются в конец. Последняя строка — "Назад".
    """
    categories = get_categories()

    # Сначала — категории из CATEGORY_ORDER, затем все прочие из БД
    ordered = [cat for cat in CATEGORY_ORDER if cat in categories]
    ordered += [cat for cat in categories if cat not in ordered]

    buttons = [[types.KeyboardButton(text=cat)] for cat in ordered]
    buttons.append([types.KeyboardButton(text="Назад")])
    return types.ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)


def product_list_inline(category: str) -> types.InlineKeyboardMarkup:
    """
    Формирование inline-клавиатуры со списком товаров выбранной категории из БД.
    """
    try:
        with db_session() as db:
            teas = get_teas_by_category(db, category)  # возвращает List[Tea]
    except Exception as e:
        logger.exception("Ошибка получения чаёв по категории: %s", e)
        teas = []

    buttons = []
    for tea in teas:
        buttons.append([types.InlineKeyboardButton(
            text=tea.name,
            callback_data=f"item:{tea.id}"
        )])

    main_btn = types.InlineKeyboardButton(text="В меню", callback_data="back_to_main")
    back_btn = types.InlineKeyboardButton(text="Назад", callback_data="back_to_catalog")
    buttons.append([main_btn, back_btn])
    return types.InlineKeyboardMarkup(inline_keyboard=buttons)


def product_detail_inline(tea_id: int) -> types.InlineKeyboardMarkup:
    """
    Для конкретного товара: "Добавить в корзину", быстрый переход в корзину,
    "Назад" (скрыть карточку, вернуться к списку) и "В меню".
    """
    buttons = [
        [types.InlineKeyboardButton(text="🛒 Добавить в корзину", callback_data=f"add:{tea_id}")],
        [types.InlineKeyboardButton(text="🧺 Перейти в корзину", callback_data="open_cart")],
        [
            types.InlineKeyboardButton(text="Назад", callback_data="back_to_details"),
            types.InlineKeyboardButton(text="В меню", callback_data="back_to_main"),
        ],
    ]
    return types.InlineKeyboardMarkup(inline_keyboard=buttons)


def build_cart_message(user_id: int):
    """
    Строим текст корзины и выдаем inline-клавиатуру:
    Оформить заказ, Очистить корзину, Редактировать корзину, Калькулятор.
    """
    lines, total = cart_lines(user_id)
    if not lines:
        return "Ваша корзина пуста.", None

    text = "<b>Ваш заказ:</b>\n"
    for tea_obj, qty, subtotal in lines:
        text += f"<b>{html.escape(tea_obj.name)}</b> x{qty} — {subtotal:.0f}₽\n"

    text += f"\n<b>Итого:</b> {total:.0f}₽"
    keyboard = types.InlineKeyboardMarkup(inline_keyboard=[
        [types.InlineKeyboardButton(text="Оформить заказ", callback_data="checkout")],
        [types.InlineKeyboardButton(text="Очистить корзину", callback_data="clear_cart")],
        [types.InlineKeyboardButton(text="Редактировать корзину", callback_data="edit_cart")],
        [types.InlineKeyboardButton(text="Калькулятор корзины", callback_data="calc_cart")],
        [types.InlineKeyboardButton(text="Назад", callback_data="back_to_main")]
    ])
    return text, keyboard


def build_cart_edit_message(user_id: int):
    """
    Формируем текст и inline-клавиатуру для редактирования корзины:
    Кнопки «-», «+», «❌» для каждого товара,
    а внизу — «Назад» и «В меню».
    """
    lines, _ = cart_lines(user_id)
    if not lines:
        return "Ваша корзина пуста.", None

    text = "<b>Редактирование корзины:</b>\n"
    keyboard = types.InlineKeyboardMarkup(inline_keyboard=[])

    # Строки с товарами и кнопками «➖ / ➕ / ❌»
    for tea_obj, qty, subtotal in lines:
        text += f"<b>{html.escape(tea_obj.name)}</b> x{qty} — {subtotal:.0f}₽\n"
        keyboard.inline_keyboard.append([
            types.InlineKeyboardButton(text="➖", callback_data=f"cart:minus:{tea_obj.id}"),
            types.InlineKeyboardButton(text="➕", callback_data=f"cart:plus:{tea_obj.id}"),
            types.InlineKeyboardButton(text="❌", callback_data=f"cart:delete:{tea_obj.id}"),
        ])

    text += "\n"
    keyboard.inline_keyboard.append([
        types.InlineKeyboardButton(text="Назад", callback_data="back_to_cart")
    ])
    keyboard.inline_keyboard.append([
        types.InlineKeyboardButton(text="В меню", callback_data="back_to_main")
    ])

    return text, keyboard


def support_inline() -> types.InlineKeyboardMarkup:
    """
    Кнопка для связи с поддержкой/админом. ADMIN_USER хранится без ведущего '@'.
    """
    keyboard = types.InlineKeyboardMarkup(inline_keyboard=[
        [types.InlineKeyboardButton(
            text="Связаться с поддержкой",
            url=f"https://t.me/{ADMIN_USER}"
        )]
    ])
    return keyboard


# Обработчики message


@dp.message(Command("start"))
async def start(message: types.Message):
    await message.answer(
        "Добро пожаловать! Выберите нужное действие:",
        reply_markup=main_menu_reply()
    )


@dp.message(Command("cancel"))
async def cancel(message: types.Message, state: FSMContext):
    current_state = await state.get_state()
    if current_state:
        await state.clear()
        await message.answer("Операция отменена.", reply_markup=main_menu_reply())
    else:
        await message.answer("Нет активных операций.", reply_markup=main_menu_reply())


@dp.message(lambda message: message.text == "Каталог")
async def catalog_menu(message: types.Message):
    await message.answer("Выберите категорию:", reply_markup=catalog_menu_reply())


@dp.message(lambda message: message.text == "Корзина")
async def show_cart(message: types.Message):
    cart_text, cart_keyboard = build_cart_message(message.from_user.id)
    await message.answer(
        cart_text,
        reply_markup=cart_keyboard if cart_keyboard else types.ReplyKeyboardRemove()
    )


@dp.message(lambda message: message.text == "Поддержка")
async def support(message: types.Message):
    await message.answer(
        "Если у вас возникли вопросы, нажмите кнопку ниже:",
        reply_markup=support_inline()
    )


@dp.message(lambda message: message.text == "Поиск")
async def search_start(message: types.Message, state: FSMContext):
    await message.answer("Введите ключевое слово или ID товара (для отмены /cancel):")
    await state.set_state(SearchForm.waiting_for_query)


# Проверка, является ли текст сообщением-именем категории (через кеш категорий)
def is_category_message(text: str) -> bool:
    if not text:
        return False
    return text in get_categories()


@dp.message(lambda message: is_category_message(message.text))
async def select_category(message: types.Message):
    """
    Когда пользователь отправил название категории, показываем ему список товаров этой категории.
    """
    category = message.text
    await message.answer(
        f"<b>Категория:</b> {category}\nВыберите товар:",
        reply_markup=types.ReplyKeyboardRemove()
    )
    # Inline-клавиатура со списком товаров:
    await message.answer("Список товаров:", reply_markup=product_list_inline(category))


@dp.message(SearchForm.waiting_for_query)
async def process_search(message: types.Message, state: FSMContext):
    query_text = (message.text or "").strip()
    results = []
    if not query_text:
        await message.answer("Введите непустой запрос.", reply_markup=main_menu_reply())
        await state.clear()
        return
    try:
        with db_session() as db:
            if query_text.isdigit():
                tea_obj = get_tea(db, int(query_text))
                if tea_obj:  # get_tea уже фильтрует по is_active
                    results = [tea_obj]
            else:
                results = search_teas(db, query_text)
    except Exception as e:
        logger.exception("Ошибка при поиске товаров: %s", e)
        results = []

    if not results:
        await message.answer("Товар не найден.", reply_markup=main_menu_reply())
    else:
        text = "<b>Результаты поиска:</b>\n\n"
        keyboard = types.InlineKeyboardMarkup(inline_keyboard=[])
        for tea_obj in results:
            text += f"• <b>{html.escape(tea_obj.name)}</b>\n"
            keyboard.inline_keyboard.append([
                types.InlineKeyboardButton(
                    text=tea_obj.name,
                    callback_data=f"item:{tea_obj.id}"
                )
            ])
        keyboard.inline_keyboard.append([
            types.InlineKeyboardButton(text="В меню", callback_data="back_to_main")
        ])
        await message.answer(text, reply_markup=keyboard)

    await state.clear()


@dp.message(lambda message: message.text == "Назад")
async def go_back(message: types.Message):
    await message.answer("Главное меню:", reply_markup=main_menu_reply())


# Обработчики callback-запросов
@dp.callback_query(lambda c: c.data == "back_to_main")
async def back_to_main_callback(query: types.CallbackQuery):
    await query.answer()
    try:
        await query.message.delete()
    except Exception:
        pass
    await bot.send_message(query.from_user.id, "Главное меню:", reply_markup=main_menu_reply())


@dp.callback_query(lambda c: c.data == "back_to_catalog")
async def back_to_catalog_callback(query: types.CallbackQuery):
    await query.answer()
    try:
        await query.message.delete()
    except Exception:
        pass
    await bot.send_message(query.from_user.id, "Выберите категорию:", reply_markup=catalog_menu_reply())


@dp.callback_query(lambda c: c.data and c.data.startswith("item:"))
async def product_item_callback(query: types.CallbackQuery):
    """
    Показываем карточку товара (название, цена, фото, описание, кнопки).
    callback_data ожидает "item:<tea_id>"
    """
    await query.answer()
    try:
        _, tea_id_str = query.data.split(":")
        tea_id = int(tea_id_str)
    except Exception:
        await query.answer("Неверный товар.")
        return

    try:
        with db_session() as db:
            tea_obj = get_tea(db, tea_id)
    except Exception as e:
        logger.exception("Ошибка получения товара: %s", e)
        tea_obj = None

    if not tea_obj or not tea_obj.is_active:
        await bot.send_message(query.from_user.id, "Товар не найден или недоступен.")
        return

    # Формируем подпись (caption). Название/описание экранируем — они показываются как HTML.
    caption = (
        f"<b>🍵 {html.escape(tea_obj.name)}</b>\n"
        f"<b>💰 Цена:</b> {float(tea_obj.price):.0f}₽"
    )
    if tea_obj.weight:
        price_per_gram = float(tea_obj.price) / float(tea_obj.weight)
        caption += f"\n<b>💶 Цена за грамм:</b> {price_per_gram:.2f}₽/г"
    if tea_obj.description:
        # Описание в БД содержит доверенную HTML-разметку (<b>, <i>) от админа — не экранируем.
        caption += f"\n\n<i>{tea_obj.description}</i>"

    photo_url = getattr(tea_obj, "photo_url", None)

    if photo_url and photo_url.startswith("http"):
        try:
            await bot.send_photo(
                query.from_user.id,
                photo=photo_url,
                caption=caption,
                reply_markup=product_detail_inline(tea_obj.id)
            )
        except Exception as e:
            logger.exception("Ошибка отправки фото по URL: %s", e)
            await bot.send_message(
                query.from_user.id,
                "Ошибка при отправке фото по URL.\n" + caption,
                reply_markup=product_detail_inline(tea_obj.id)
            )
    else:
        if photo_url:
            photo_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), photo_url)
            if os.path.exists(photo_path):
                try:
                    with open(photo_path, "rb") as photo_file:
                        await bot.send_photo(
                            query.from_user.id,
                            photo=photo_file,
                            caption=caption,
                            reply_markup=product_detail_inline(tea_obj.id)
                        )
                except Exception as e:
                    logger.exception("Ошибка при открытии фото: %s", e)
                    await bot.send_message(
                        query.from_user.id,
                        "Ошибка при открытии фото.\n" + caption,
                        reply_markup=product_detail_inline(tea_obj.id)
                    )
            else:
                await bot.send_message(
                    query.from_user.id,
                    "Фото не найдено.\n" + caption,
                    reply_markup=product_detail_inline(tea_obj.id)
                )
        else:
            await bot.send_message(
                query.from_user.id,
                caption,
                reply_markup=product_detail_inline(tea_obj.id)
            )


@dp.callback_query(lambda c: c.data == "back_to_details")
async def back_to_details_callback(query: types.CallbackQuery):
    await query.answer()
    try:
        await query.message.delete()
    except Exception:
        pass


@dp.callback_query(lambda c: c.data and c.data.startswith("add:"))
async def add_to_cart_callback(query: types.CallbackQuery):
    """
    Добавляем товар в корзину по его tea_id: callback_data = "add:<tea_id>"
    """
    try:
        _, tea_id_str = query.data.split(":")
        tea_id = int(tea_id_str)
    except Exception:
        await query.answer("Неверный товар.", show_alert=True)
        return

    user_id = query.from_user.id
    cart = CARTS.setdefault(user_id, [])

    # Проверяем, есть ли уже этот товар в корзине
    for item in cart:
        if item["tea_id"] == tea_id:
            item["quantity"] += 1
            break
    else:
        cart.append({"tea_id": tea_id, "quantity": 1})

    await query.answer("Товар добавлен в корзину.")


@dp.callback_query(lambda c: c.data == "clear_cart")
async def clear_cart_callback(query: types.CallbackQuery):
    # Очищаем корзину в вашем хранилище
    CARTS[query.from_user.id] = []

    # Отвечаем на callback, чтобы у кнопки „часики“ исчезли
    await query.answer("Корзина очищена.")

    # Перезаписываем текст сообщения и полностью убираем inline-клавиатуру
    await query.message.edit_text(
        "Ваша корзина пуста.",
        reply_markup=None
    )


@dp.callback_query(lambda c: c.data == "edit_cart")
async def edit_cart_callback(query: types.CallbackQuery):
    await query.answer()
    text, keyboard = build_cart_edit_message(query.from_user.id)
    await query.message.edit_text(text, reply_markup=keyboard)


@dp.callback_query(lambda c: c.data and c.data.startswith("cart:"))
async def cart_edit_callback(query: types.CallbackQuery):
    """
    Обрабатываем inline-кнопки редактирования корзины:
    callback_data вида "cart:<action>:<tea_id>"
    """
    await query.answer()
    try:
        _, action, tea_id_str = query.data.split(":")
        tea_id = int(tea_id_str)
    except Exception as e:
        logger.exception("Неверный формат данных для редактирования корзины: %s", e)
        await query.answer("Ошибка данных.")
        return

    user_id = query.from_user.id
    items = CARTS.get(user_id, [])

    for item in items:
        if item["tea_id"] == tea_id:
            if action == "minus":
                item["quantity"] -= 1
                if item["quantity"] < 1:
                    items.remove(item)
            elif action == "plus":
                item["quantity"] += 1
            elif action == "delete":
                items.remove(item)
            break

    CARTS[user_id] = items
    text, keyboard = build_cart_edit_message(user_id)
    await query.message.edit_text(text, reply_markup=keyboard)


@dp.callback_query(lambda c: c.data == "calc_cart")
async def calc_cart_callback(query: types.CallbackQuery, state: FSMContext):
    """
    Калькулятор: для каждого товара из корзины, у которого есть поле weight,
    спрашиваем, сколько грамм нужно, и считаем стоимость.
    """
    await query.answer()
    user_id = query.from_user.id
    items = CARTS.get(user_id, [])
    if not items:
        await query.answer("Ваша корзина пуста.", show_alert=True)
        return

    # Берём из корзины только товары с указанным весом, сохраняя порядок корзины
    teas = fetch_teas_map(item["tea_id"] for item in items)
    calc_ids = [item["tea_id"] for item in items
                if teas.get(item["tea_id"]) and teas[item["tea_id"]].weight]

    if not calc_ids:
        await query.answer("Нет товаров с указанием веса для расчёта.", show_alert=True)
        return

    # В FSM храним только id (а не ORM-объекты) — это безопасно для любого storage
    await state.update_data(calc_ids=calc_ids, calc_index=0, calc_total=0, calc_results=[])
    first_tea = teas[calc_ids[0]]
    price_per_gram = float(first_tea.price) / float(first_tea.weight)
    await query.message.edit_text(
        f"Введите количество грамм для <b>{html.escape(first_tea.name)}</b>\n"
        f"(Цена за грамм: {price_per_gram:.2f}₽):",
        parse_mode=ParseMode.HTML
    )
    await state.set_state(TeaCalcForm.waiting_for_grams)


@dp.message(TeaCalcForm.waiting_for_grams)
async def process_grams(message: types.Message, state: FSMContext):
    user_input = (message.text or "").strip().replace(",", ".")
    try:
        grams = float(user_input)
        if grams <= 0:
            raise ValueError
    except ValueError:
        await message.answer("Пожалуйста, введите положительное число грамм.")
        return

    data = await state.get_data()
    calc_ids = data.get("calc_ids", [])
    calc_index = data.get("calc_index", 0)
    calc_total = data.get("calc_total", 0)
    calc_results = data.get("calc_results", [])

    if not calc_ids or calc_index >= len(calc_ids):
        await message.answer("Сессия расчёта устарела. Откройте калькулятор заново.")
        await state.clear()
        return

    teas = fetch_teas_map(calc_ids)
    current_tea = teas.get(calc_ids[calc_index])
    if not current_tea or not current_tea.weight:
        await message.answer("Товар недоступен. Откройте калькулятор заново.")
        await state.clear()
        return

    price_per_gram = float(current_tea.price) / float(current_tea.weight)
    subtotal = math.ceil(grams * price_per_gram)
    calc_total += subtotal
    calc_results.append({
        "name": current_tea.name,
        "grams": grams,
        "subtotal": subtotal
    })

    calc_index += 1
    next_tea = teas.get(calc_ids[calc_index]) if calc_index < len(calc_ids) else None
    if next_tea and next_tea.weight:
        next_price_per_gram = float(next_tea.price) / float(next_tea.weight)
        await state.update_data(calc_index=calc_index, calc_total=calc_total, calc_results=calc_results)
        await message.answer(
            f"Введите количество грамм для <b>{html.escape(next_tea.name)}</b>\n"
            f"(Цена за грамм: {next_price_per_gram:.2f}₽):",
            parse_mode=ParseMode.HTML
        )
    else:
        # Выводим финальный результат
        result_text = "<b>Расчёт стоимости по граммам:</b>\n\n"
        for item in calc_results:
            result_text += f"<b>{html.escape(item['name'])}</b>: {item['grams']} г — {item['subtotal']}₽\n"
        result_text += f"\n<b>Итог:</b> {calc_total}₽"
        result_keyboard = types.InlineKeyboardMarkup(inline_keyboard=[
            [types.InlineKeyboardButton(text="Назад", callback_data="back_to_cart")],
            [types.InlineKeyboardButton(text="В меню", callback_data="back_to_main")]
        ])
        await message.answer(result_text, parse_mode=ParseMode.HTML, reply_markup=result_keyboard)
        await state.clear()


@dp.callback_query(lambda c: c.data == "back_to_cart")
async def back_to_cart_callback(query: types.CallbackQuery):
    await query.answer()
    text, keyboard = build_cart_message(query.from_user.id)
    await query.message.edit_text(text, reply_markup=keyboard)


@dp.callback_query(lambda c: c.data == "open_cart")
async def open_cart_callback(query: types.CallbackQuery):
    """Быстрый переход в корзину с карточки товара (карточка может быть фото — шлём новое сообщение)."""
    await query.answer()
    text, keyboard = build_cart_message(query.from_user.id)
    await bot.send_message(
        query.from_user.id,
        text,
        reply_markup=keyboard if keyboard else None,
    )


@dp.callback_query(lambda c: c.data == "checkout")
async def checkout_callback(query: types.CallbackQuery, state: FSMContext):
    user_id = query.from_user.id
    if not CARTS.get(user_id):
        await query.answer("Ваша корзина пуста.", show_alert=True)
        return

    await query.answer()
    await query.message.edit_text("Введите ваше ФИО (для отмены введите /cancel):")
    await state.set_state(OrderForm.waiting_for_fio)


@dp.message(OrderForm.waiting_for_fio)
async def process_fio(message: types.Message, state: FSMContext):
    fio = (message.text or "").strip()
    if not fio:
        await message.answer("Пожалуйста, введите корректное ФИО текстом:")
        return
    if len(fio) > MAX_FIELD_LEN:
        await message.answer("Слишком длинное значение, сократите, пожалуйста.")
        return
    await state.update_data(fio=fio)
    await message.answer("Введите адрес доставки (для отмены /cancel):")
    await state.set_state(OrderForm.waiting_for_address)


@dp.message(OrderForm.waiting_for_address)
async def process_address(message: types.Message, state: FSMContext):
    address = (message.text or "").strip()
    if not address:
        await message.answer("Пожалуйста, введите корректный адрес текстом:")
        return
    if len(address) > MAX_FIELD_LEN:
        await message.answer("Слишком длинное значение, сократите, пожалуйста.")
        return
    await state.update_data(address=address)
    await message.answer("Введите номер телефона (для отмены /cancel):")
    await state.set_state(OrderForm.waiting_for_phone)


@dp.message(OrderForm.waiting_for_phone)
async def process_phone(message: types.Message, state: FSMContext):
    phone = (message.text or "").strip()
    if not phone:
        await message.answer("Пожалуйста, введите корректный номер телефона текстом:")
        return
    # Минимальная валидация: должно быть хотя бы 5 цифр
    if sum(ch.isdigit() for ch in phone) < 5 or len(phone) > MAX_FIELD_LEN:
        await message.answer("Похоже, номер указан некорректно. Введите телефон ещё раз:")
        return
    await state.update_data(phone=phone)
    await message.answer("Оставьте комментарий к заказу, если есть (для отмены /cancel):")
    await state.set_state(OrderForm.waiting_for_comment)


@dp.message(OrderForm.waiting_for_comment)
async def process_comment(message: types.Message, state: FSMContext):
    comment = (message.text or "").strip()
    if not comment:
        await message.answer("Здесь можете уточнить детали заказа:")
        return
    if len(comment) > MAX_FIELD_LEN:
        await message.answer("Слишком длинный комментарий, сократите, пожалуйста.")
        return
    await state.update_data(comment=comment)
    await message.answer("Введите промокод, если есть (для отмены /cancel):")
    await state.set_state(OrderForm.waiting_for_promo)


@dp.message(OrderForm.waiting_for_promo)
async def process_promo(message: types.Message, state: FSMContext):
    promo = (message.text or "").strip()
    if not promo:
        promo = "—"

    user_data = await state.get_data()
    # Все поля экранируем — они попадают в HTML-сообщение администратору
    fio = html.escape(user_data.get("fio", "Не указано"))
    address = html.escape(user_data.get("address", "Не указано"))
    phone = html.escape(user_data.get("phone", "Не указан"))
    comment = html.escape(user_data.get("comment", "Не указан"))
    promo = html.escape(promo)
    user_id = message.from_user.id

    lines, total = cart_lines(user_id)
    if not lines:
        await message.answer("Ваша корзина пуста.")
        await state.clear()
        return

    # Формируем текст заказа
    order_number = uuid.uuid4().hex[:8].upper()
    order_text = f"<b>Номер заказа:</b> {order_number}\n\n"
    order_text += "<b>Состав заказа:</b>\n"
    for tea_obj, qty, subtotal in lines:
        order_text += f"<b>{html.escape(tea_obj.name)}</b> x{qty} — {subtotal:.0f}₽\n"

    username = message.from_user.username
    username_str = f"@{html.escape(username)}" if username else "без username"
    full_name = html.escape(message.from_user.full_name or "—")

    order_text += f"\n<b>Итого:</b> {total:.0f}₽\n\n"
    order_text += "<b>Контактная информация:</b>\n"
    order_text += f"ФИО: {fio}\n"
    order_text += f"Адрес: {address}\n"
    order_text += f"Телефон: {phone}\n\n"
    order_text += f"Комментарий: {comment}\n\n"
    order_text += f"Промокод: {promo}\n\n\n"
    order_text += f"<i>Отправил: {full_name} ({username_str}), ID: {message.from_user.id}</i>"

    # Отправляем админу(ам)
    try:
        admin_chat = ADMIN
        # Если ADMIN — строка-имя с @, конвертируем в ID через get_chat
        if isinstance(admin_chat, str) and admin_chat.startswith("@"):
            chat_obj = await bot.get_chat(admin_chat)
            admin_chat = chat_obj.id

        # Если ADMIN — список ID, перебираем
        if isinstance(admin_chat, list):
            for admin_id in admin_chat:
                await bot.send_message(admin_id, order_text)
        else:
            await bot.send_message(admin_chat, order_text)

    except Exception as e:
        logger.exception("Ошибка при отправке заказа администратору: %s", e)
        await message.answer("Ошибка при отправке заказа. Проверьте настройки администратора.")
        await state.clear()
        return

    await message.answer(
        f"Ваш заказ принят. Номер заказа: <b>{order_number}</b>\nОжидайте инструкций по оплате.",
        disable_web_page_preview=True
    )
    CARTS[user_id] = []  # очищаем корзину
    await state.clear()
    await message.answer("Главное меню:", reply_markup=main_menu_reply())


# Запуск бота
async def main():
    try:
        await bot.delete_webhook(drop_pending_updates=True)
    except Exception as e:
        logger.exception("Ошибка удаления webhook: %s", e)
    asyncio.create_task(clear_cache_periodically())
    await dp.start_polling(bot)


@dp.message(lambda message: message.text and not message.text.startswith("/"))
async def handle_messages(message: types.Message):
    await handle_admin_command(message, bot)
    await handle_user_message(message, bot)


if __name__ == "__main__":
    asyncio.run(main())
