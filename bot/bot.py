import os
import sys
import asyncio
import logging
import uuid
import math


sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from aiogram import Bot, Dispatcher, types
from aiogram.client.bot import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext

from app.database import SessionLocal
from app.models import Tea
from app.crud import get_all_categories, get_teas_by_category, get_tea  # предполагаем, что эти функции есть в crud
from config import TOKEN, ADMIN


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


bot = Bot(token=TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()


CARTS = {}

# Фоновая задача: каждые 10h очистка CARTS
async def clear_cache_periodically():
    while True:
        await asyncio.sleep(10 * 3600)
        CARTS.clear()
        logger.info("Кеш (CARTS) очищен.")

#FSM-Состояния
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


#Формирование клавиатур
def main_menu_reply() -> types.ReplyKeyboardMarkup:
    buttons = [
        [types.KeyboardButton(text="Каталог"), types.KeyboardButton(text="Поиск")],
        [types.KeyboardButton(text="Корзина"), types.KeyboardButton(text="Поддержка")]
    ]
    return types.ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)

def catalog_menu_reply() -> types.ReplyKeyboardMarkup:
    """
    Формирование клавиатуры из категорий в том порядке,
    который указан в custom_order. Последняя строка — "Назад".
    """
    db = SessionLocal()
    try:
        categories = get_all_categories(db)  # возвращает List[str]
    except Exception as e:
        logger.exception("Ошибка получения категорий: %s", e)
        categories = []
    finally:
        db.close()

    # Задаём свой порядок (те категории, которые хотим видеть первыми)
    custom_order = [
        "Шу пуэры",
        "Шен пуэры",
        "Улуны",
        "Габа улуны",
        "Зелёные",
        "Красные",
        "Белые",
        "Жёлтые"
        "Посуда",
        "Чайные духи",
    ]

    # Формируем итоговый список ordered:
    # сначала — те, что есть в custom_order, а потом «лишние» (если они всё же появились в БД, но не вошли в custom_order)
    ordered = []
    for cat in custom_order:
        if cat in categories:
            ordered.append(cat)
    for cat in categories:
        if cat not in ordered:
            ordered.append(cat)

    buttons = [[types.KeyboardButton(text=cat)] for cat in ordered]
    buttons.append([types.KeyboardButton(text="Назад")])
    return types.ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)


def product_list_inline(category: str) -> types.InlineKeyboardMarkup:
    """
    Формирование inline-клавиатуры со списком товаров выбранной категории из БД.
    """
    db = SessionLocal()
    try:
        teas = get_teas_by_category(db, category)  # возвращает List[Tea]
    except Exception as e:
        logger.exception("Ошибка получения чаёв по категории: %s", e)
        teas = []
    finally:
        db.close()

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
    Для конкретного товара: кнопка "Добавить в корзину" и "Назад".
    """
    buttons = [
        [types.InlineKeyboardButton(text="Добавить в корзину", callback_data=f"add:{tea_id}")],
        [types.InlineKeyboardButton(text="Назад", callback_data="back_to_details")]
    ]
    return types.InlineKeyboardMarkup(inline_keyboard=buttons)

def build_cart_message(user_id: int):
    """
    Строим текст корзины и выдаем inline-клавиатуру:
    Оформить заказ, Очистить корзину, Редактировать корзину, Калькулятор.
    """
    items = CARTS.get(user_id, [])
    if not items:
        return "Ваша корзина пуста.", None

    text = "<b>Ваш заказ:</b>\n"
    total = 0.0
    db = SessionLocal()
    try:
        for item in items:
            tea_obj = get_tea(db, item["tea_id"])
            if not tea_obj:
                continue
            price = float(tea_obj.price)
            subtotal = price * item["quantity"]
            total += subtotal
            text += f"<b>{tea_obj.name}</b> x{item['quantity']} — {subtotal:.0f}₽\n"
    except Exception as e:
        logger.exception("Ошибка при формировании корзины: %s", e)
    finally:
        db.close()

    text += f"\n<b>Итого:</b> {total:.0f}₽"
    keyboard = types.InlineKeyboardMarkup(inline_keyboard=[
        [types.InlineKeyboardButton(text="Оформить заказ", callback_data="checkout")],
        [types.InlineKeyboardButton(text="Очистить корзину", callback_data="clear_cart")],
        [types.InlineKeyboardButton(text="Редактировать корзину", callback_data="edit_cart")],
        [types.InlineKeyboardButton(text="Калькулятор", callback_data="calc_cart")]
    ])
    return text, keyboard

def build_cart_edit_message(user_id: int):
    """
    Формируем текст и inline-клавиатуру для редактирования корзины:
    Кнопки «-», «+», «❌» для каждого товара.
    """
    items = CARTS.get(user_id, [])
    if not items:
        return "Ваша корзина пуста.", None

    text = "<b>Редактирование корзины:</b>\n"
    keyboard = types.InlineKeyboardMarkup(inline_keyboard=[])
    db = SessionLocal()
    try:
        for item in items:
            tea_obj = get_tea(db, item["tea_id"])
            if not tea_obj:
                continue
            qty = item["quantity"]
            price = float(tea_obj.price)
            subtotal = price * qty
            text += f"<b>{tea_obj.name}</b> x{qty} — {subtotal:.0f}₽\n"
            row = [
                types.InlineKeyboardButton(text="➖", callback_data=f"cart:minus:{tea_obj.id}"),
                types.InlineKeyboardButton(text="➕", callback_data=f"cart:plus:{tea_obj.id}"),
                types.InlineKeyboardButton(text="❌", callback_data=f"cart:delete:{tea_obj.id}")
            ]
            keyboard.inline_keyboard.append(row)
    except Exception as e:
        logger.exception("Ошибка при формировании сообщения редактирования корзины: %s", e)
    finally:
        db.close()

    return text, keyboard

def support_inline() -> types.InlineKeyboardMarkup:
    """
    Кнопка для связи с поддержкой/админом.
    """
    # Замените на свой @username админа
    admin_username = ADMIN if ADMIN.startswith("@") else f"@{ADMIN}"
    keyboard = types.InlineKeyboardMarkup(inline_keyboard=[
        [types.InlineKeyboardButton(text="Связаться с поддержкой", url=f"https://t.me/{admin_username.lstrip('@')}")]
    ])
    return keyboard

#Обработчики message

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

# Проверка, является ли текст сообщением-именем категории
def is_category_message(text: str) -> bool:
    db = SessionLocal()
    try:
        categories = get_all_categories(db)
        return text in categories
    except Exception:
        return False
    finally:
        db.close()

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
    query_text = message.text.strip().lower()
    db = SessionLocal()
    try:
        results = []
        if query_text.isdigit():
            tea_obj = get_tea(db, int(query_text))
            if tea_obj and tea_obj.is_active:
                results.append(tea_obj)
        else:
            # Поиск по имени и описанию
            results = (
                db.query(Tea)
                .filter(
                    Tea.is_active == True,
                    (Tea.name.ilike(f"%{query_text}%")) |
                    (Tea.description.ilike(f"%{query_text}%"))
                )
                .all()
            )
    except Exception as e:
        logger.exception("Ошибка при поиске товаров: %s", e)
        results = []
    finally:
        db.close()

    if not results:
        await message.answer("Товар не найден.", reply_markup=main_menu_reply())
    else:
        text = "<b>Результаты поиска:</b>\n\n"
        keyboard = types.InlineKeyboardMarkup(inline_keyboard=[])
        for tea_obj in results:
            text += f"• <b>{tea_obj.name}</b>\n"
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

#Обработчики callback-запросов
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

    db = SessionLocal()
    try:
        tea_obj = get_tea(db, tea_id)
    except Exception as e:
        logger.exception("Ошибка получения товара: %s", e)
        tea_obj = None
    finally:
        db.close()

    if not tea_obj or not tea_obj.is_active:
        await bot.send_message(query.from_user.id, "Товар не найден или недоступен.")
        return

    # Формируем подпись (caption)
    caption = (
        f"<b>🍵 {tea_obj.name}</b>\n"
        f"<b>💰 Цена:</b> {float(tea_obj.price):.0f}₽"
    )
    if tea_obj.weight:
        price_per_gram = float(tea_obj.price) / float(tea_obj.weight)
        caption += f"\n<b>💶 Цена за грамм:</b> {price_per_gram:.2f}₽/г"
    if tea_obj.description:
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
    """
    Возврат к списку товаров категории.
    Для упрощения просто возвращаемся к выбору категории.
    """
    await query.answer()
    try:
        await query.message.delete()
    except Exception:
        pass
    await bot.send_message(query.from_user.id, "Выберите категорию:", reply_markup=catalog_menu_reply())

@dp.callback_query(lambda c: c.data and c.data.startswith("add:"))
async def add_to_cart_callback(query: types.CallbackQuery):
    """
    Добавляем товар в корзину по его tea_id: callback_data = "add:<tea_id>"
    """
    await query.answer()
    try:
        _, tea_id_str = query.data.split(":")
        tea_id = int(tea_id_str)
    except Exception:
        await query.answer("Неверный товар.")
        return

    user_id = query.from_user.id
    if user_id not in CARTS:
        CARTS[user_id] = []

    # Проверяем, есть ли уже этот товар в корзине
    for item in CARTS[user_id]:
        if item["tea_id"] == tea_id:
            item["quantity"] += 1
            break
    else:
        CARTS[user_id].append({"tea_id": tea_id, "quantity": 1})

    await query.answer("Товар добавлен в корзину.")

@dp.callback_query(lambda c: c.data == "clear_cart")
async def clear_cart_callback(query: types.CallbackQuery):
    CARTS[query.from_user.id] = []
    await query.answer("Корзина очищена.")
    await query.message.edit_text("Ваша корзина пуста.", reply_markup=types.ReplyKeyboardRemove())

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

    db = SessionLocal()
    try:
        calc_items = []
        for item in items:
            tea_obj = get_tea(db, item["tea_id"])
            if tea_obj and tea_obj.weight:
                calc_items.append(tea_obj)
    except Exception as e:
        logger.exception("Ошибка при формировании списка для калькулятора: %s", e)
        calc_items = []
    finally:
        db.close()

    if not calc_items:
        await query.answer("Нет товаров с указанием веса для расчёта.", show_alert=True)
        return

    # Сохраним в FSMContext всю информацию: список tea_obj, индекс, суммы и результаты
    await state.update_data(calc_items=calc_items, calc_index=0, calc_total=0, calc_results=[])
    first_tea = calc_items[0]
    price_per_gram = float(first_tea.price) / float(first_tea.weight)
    await query.message.edit_text(
        f"Введите количество грамм для <b>{first_tea.name}</b>\n"
        f"(Цена за грамм: {price_per_gram:.2f}₽):",
        parse_mode=ParseMode.HTML
    )
    await state.set_state(TeaCalcForm.waiting_for_grams)

@dp.message(TeaCalcForm.waiting_for_grams)
async def process_grams(message: types.Message, state: FSMContext):
    user_input = message.text.strip()
    try:
        grams = float(user_input)
        if grams <= 0:
            raise ValueError
    except ValueError:
        await message.answer("Пожалуйста, введите положительное число грамм.")
        return

    data = await state.get_data()
    calc_items = data.get("calc_items", [])
    calc_index = data.get("calc_index", 0)
    calc_total = data.get("calc_total", 0)
    calc_results = data.get("calc_results", [])

    current_tea = calc_items[calc_index]
    price_per_gram = float(current_tea.price) / float(current_tea.weight)
    subtotal = math.ceil(grams * price_per_gram)
    calc_total += subtotal
    calc_results.append({
        "name": current_tea.name,
        "grams": grams,
        "subtotal": subtotal
    })

    calc_index += 1
    if calc_index < len(calc_items):
        next_tea = calc_items[calc_index]
        next_price_per_gram = float(next_tea.price) / float(next_tea.weight)
        await state.update_data(calc_index=calc_index, calc_total=calc_total, calc_results=calc_results)
        await message.answer(
            f"Введите количество грамм для <b>{next_tea.name}</b>\n"
            f"(Цена за грамм: {next_price_per_gram:.2f}₽):",
            parse_mode=ParseMode.HTML
        )
    else:
        # Выводим финальный результат
        result_text = "<b>Расчёт стоимости по граммам:</b>\n\n"
        for item in calc_results:
            result_text += f"<b>{item['name']}</b>: {item['grams']} г — {item['subtotal']}₽\n"
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
    fio = message.text.strip()
    if not fio:
        await message.answer("Пожалуйста, введите корректное ФИО:")
        return
    await state.update_data(fio=fio)
    await message.answer("Введите адрес доставки (для отмены /cancel):")
    await state.set_state(OrderForm.waiting_for_address)

@dp.message(OrderForm.waiting_for_address)
async def process_address(message: types.Message, state: FSMContext):
    address = message.text.strip()
    if not address:
        await message.answer("Пожалуйста, введите корректный адрес:")
        return
    await state.update_data(address=address)
    await message.answer("Введите номер телефона (для отмены /cancel):")
    await state.set_state(OrderForm.waiting_for_phone)

@dp.message(OrderForm.waiting_for_phone)
async def process_phone(message: types.Message, state: FSMContext):
    phone = message.text.strip()
    if not phone:
        await message.answer("Пожалуйста, введите корректный номер телефона:")
        return
    await state.update_data(phone=phone)
    await message.answer("Оставьте комментарий к заказу, если есть (для отмены /cancel):")
    await state.set_state(OrderForm.waiting_for_comment)

@dp.message(OrderForm.waiting_for_comment)
async def process_comment(message: types.Message, state: FSMContext):
    comment = message.text.strip()
    if not comment:
        await message.answer("Здесь можете уточнить детали заказа:")
        return
    await state.update_data(comment=comment)
    await message.answer("Введите промокод, если есть (для отмены /cancel):")
    await state.set_state(OrderForm.waiting_for_promo)

@dp.message(OrderForm.waiting_for_promo)
async def process_promo(message: types.Message, state: FSMContext):
    promo = message.text.strip()
    if not promo:
        promo = "—"

    user_data = await state.get_data()
    fio = user_data.get("fio", "Не указано")
    address = user_data.get("address", "Не указано")
    phone = user_data.get("phone", "Не указан")
    comment = user_data.get("comment", "Не указан")
    user_id = message.from_user.id
    items = CARTS.get(user_id, [])
    if not items:
        await message.answer("Ваша корзина пуста.")
        await state.clear()
        return

    # Формируем текст заказа
    order_number = uuid.uuid4().hex[:8].upper()
    order_text = f"<b>Номер заказа:</b> {order_number}\n\n"
    order_text += "<b>Состав заказа:</b>\n"
    total = 0.0
    db = SessionLocal()
    try:
        for item in items:
            tea_obj = get_tea(db, item["tea_id"])
            if not tea_obj:
                continue
            price = float(tea_obj.price)
            subtotal = price * item["quantity"]
            total += subtotal
            order_text += f"<b>{tea_obj.name}</b> x{item['quantity']} — {subtotal:.0f}₽\n"
    except Exception as e:
        logger.exception("Ошибка при формировании текста заказа: %s", e)
    finally:
        db.close()

    order_text += f"\n<b>Итого:</b> {total:.0f}₽\n\n"
    order_text += "<b>Контактная информация:</b>\n"
    order_text += f"ФИО: {fio}\n"
    order_text += f"Адрес: {address}\n"
    order_text += f"Телефон: {phone}\n\n"
    order_text += f"Комментарий: {comment}\n\n"
    order_text += f"Промокод: {promo}\n\n\n"
    order_text += f"<i>Отправил: {message.from_user.full_name} (@{message.from_user.username}), ID: {message.from_user.id}</i>"

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

# Ловим все остальные текстовые сообщения (не команды и не категории)
@dp.message(lambda message: message.text and not message.text.startswith("/"))
async def catch_all_messages(message: types.Message):
    """
    Если сообщение не попало ни под один из основных хендлеров,
    возвращаем приглашение в главное меню.
    """
    await message.answer("Не понял вашу команду. Нажмите /start, чтобы вернуться в главное меню.")

#Запуск бота
async def main():
    try:
        await bot.delete_webhook(drop_pending_updates=True)
    except Exception as e:
        logger.exception("Ошибка удаления webhook: %s", e)
    asyncio.create_task(clear_cache_periodically())
    await dp.start_polling(bot, skip_updates=True)

if __name__ == "__main__":
    asyncio.run(main())
