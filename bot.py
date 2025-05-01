import os
import json
import asyncio
import logging
import uuid
import re  # добавлено для поиска веса
import math  # для округления вверх

from aiogram import Bot, Dispatcher, types
from aiogram.client.bot import DefaultBotProperties
from aiogram.types import (
    ReplyKeyboardMarkup,
    KeyboardButton,
    InlineKeyboardMarkup,
    InlineKeyboardButton
)
from aiogram.filters import Command
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
from aiogram.enums import ParseMode
from admin_tools import handle_admin_command, handle_user_message
from config import TOKEN, ADMIN  # PAYMENT_DETAILS не используется

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Определяем базовую директорию скрипта для формирования абсолютных путей
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Создаем бота и диспетчер с указанием default-свойств для бота
bot = Bot(token=TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()

# Загружаем базу данных с категориями и товарами
with open(os.path.join(BASE_DIR, "database.json"), "r", encoding="utf-8") as f:
    db = json.load(f)

# Глобальный словарь для хранения корзин пользователей (для реального проекта лучше использовать БД)
CARTS = {}


# Фоновая задача для периодической очистки кеша каждые 10 часов
async def clear_cache_periodically():
    while True:
        await asyncio.sleep(10 * 3600)  # 10 часов = 36000 секунд
        CARTS.clear()
        logger.info("Кеш очищен.")


# FSM для оформления заказа (разбивка ввода на несколько шагов)
class OrderForm(StatesGroup):
    waiting_for_fio = State()
    waiting_for_address = State()
    waiting_for_phone = State()
    waiting_for_comment = State()
    waiting_for_promo = State()


# FSM для поиска
class SearchForm(StatesGroup):
    waiting_for_query = State()


# FSM для калькулятора чая по граммам
class TeaCalcForm(StatesGroup):
    waiting_for_grams = State()


# ★ Функции для формирования клавиатур ★

def main_menu_reply():
    """
    Формирует главное меню с кнопками: Каталог, Корзина, Поиск, Поддержка
    """
    buttons = [
        [KeyboardButton(text="Каталог"), KeyboardButton(text="Поиск")],
        [KeyboardButton(text="Корзина"), KeyboardButton(text="Поддержка")]
    ]
    return ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)


def catalog_menu_reply():
    """
    Формирует клавиатуру с категориями (из базы данных) и кнопкой "Назад"
    """
    buttons = [[KeyboardButton(text=category)] for category in db["categories"].keys()]
    buttons.append([KeyboardButton(text="Назад")])
    return ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)


def product_list_inline(category: str):
    """
    Формирует inline-клавиатуру со списком товаров выбранной категории
    """
    buttons = []
    products = db["categories"].get(category, {})
    for product_key, product in products.items():
        buttons.append([InlineKeyboardButton(
            text=product["name"],
            callback_data=f"item:{category}:{product_key}"
        )])
    # Добавляем две кнопки в одной строке: "В меню" и "Назад"
    main_menu_button = InlineKeyboardButton(text="В меню", callback_data="back_to_main")
    catalog_button = InlineKeyboardButton(text="Назад", callback_data="back_to_catalog")
    buttons.append([main_menu_button, catalog_button])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def product_detail_inline(category: str, product_key: str):
    """
    Формирует inline-клавиатуру для деталей товара – кнопки "Добавить в корзину" и "Назад"
    """
    buttons = [
        [InlineKeyboardButton(
            text="Добавить в корзину",
            callback_data=f"add:{category}:{product_key}"
        )],
        [InlineKeyboardButton(
            text="Назад",
            callback_data=f"back:{category}"
        )]
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def build_cart_message(user_id: int):
    """
    Формирует описание корзины и клавиатуру с кнопками:
    «Оформить заказ», «Очистить корзину», «Редактировать корзину» и «Калькулятор»
    """
    items = CARTS.get(user_id, [])
    if not items:
        return "Ваша корзина пуста.", None
    text = "<b>Ваш заказ:</b>\n"
    total = 0
    for item in items:
        category = item["category"]
        product_key = item["product_key"]
        quantity = item["quantity"]
        product = db["categories"].get(category, {}).get(product_key, {})
        if not product:
            continue
        name = product.get("name", "Товар")
        price = product.get("price", 0)
        subtotal = price * quantity
        total += subtotal
        text += f"<b>{name}</b> x{quantity} — {subtotal}₽\n"
    text += f"\n<b>Итого:</b> {total}₽"
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Оформить заказ", callback_data="checkout")],
        [InlineKeyboardButton(text="Очистить корзину", callback_data="clear_cart")],
        [InlineKeyboardButton(text="Редактировать корзину", callback_data="edit_cart")],
        [InlineKeyboardButton(text="Калькулятор", callback_data="calc_cart")]
    ])
    return text, keyboard


def build_cart_edit_message(user_id: int):
    """
    Формирует сообщение для редактирования корзины с кнопками для изменения количества или удаления товара.
    """
    items = CARTS.get(user_id, [])
    if not items:
        return "Ваша корзина пуста.", None
    text = "<b>Редактирование корзины:</b>\n"
    keyboard = InlineKeyboardMarkup(inline_keyboard=[])
    for item in items:
        category = item["category"]
        product_key = item["product_key"]
        quantity = item["quantity"]
        product = db["categories"].get(category, {}).get(product_key, {})
        if not product:
            continue
        name = product.get("name", "Товар")
        price = product.get("price", 0)
        subtotal = price * quantity
        text += f"<b>{name}</b> x{quantity} — {subtotal}₽\n"
        row = [
            InlineKeyboardButton(text="➖", callback_data=f"cart:minus:{category}:{product_key}"),
            InlineKeyboardButton(text="➕", callback_data=f"cart:plus:{category}:{product_key}"),
            InlineKeyboardButton(text="❌", callback_data=f"cart:delete:{category}:{product_key}")
        ]
        keyboard.inline_keyboard.append(row)
    return text, keyboard


def support_inline():
    """
    Формирует inline-клавиатуру для поддержки с кнопкой перехода в чат с администратором
    """
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Связаться с поддержкой", url="https://t.me/melron27")]
    ])
    return keyboard


# ★ Обработчики сообщений ★

@dp.message(Command("start"))
async def start(message: types.Message):
    """
    При команде /start выводим главное меню
    """
    await message.answer("Добро пожаловать! Выберите нужное действие:",
                         reply_markup=main_menu_reply())


@dp.message(Command("cancel"))
async def cancel(message: types.Message, state: FSMContext):
    """
    Отмена любой операции
    """
    current_state = await state.get_state()
    if current_state:
        await state.clear()
        await message.answer("Операция отменена.", reply_markup=main_menu_reply())
    else:
        await message.answer("Нет активных операций.", reply_markup=main_menu_reply())


@dp.message(lambda message: message.text == "Каталог")
async def catalog_menu(message: types.Message):
    """
    Вывод списка категорий
    """
    await message.answer("Выберите категорию:", reply_markup=catalog_menu_reply())


@dp.message(lambda message: message.text in db["categories"].keys())
async def select_category(message: types.Message):
    """
    Вывод списка товаров выбранной категории
    """
    category = message.text
    await message.answer(f"<b>Категория:</b> {category}\nВыберите товар:",
                         reply_markup=types.ReplyKeyboardRemove())
    await message.answer("Список товаров:", reply_markup=product_list_inline(category))


@dp.message(lambda message: message.text == "Корзина")
async def show_cart(message: types.Message):
    """
    Вывод корзины пользователя
    """
    cart_text, cart_keyboard = build_cart_message(message.from_user.id)
    await message.answer(cart_text,
                         reply_markup=cart_keyboard if cart_keyboard else types.ReplyKeyboardRemove())


@dp.message(lambda message: message.text == "Поддержка")
async def support(message: types.Message):
    """
    Вывод кнопки для связи с администратором
    """
    await message.answer(
        "Если у вас возникли вопросы, нажмите кнопку ниже для связи с поддержкой:",
        reply_markup=support_inline()
    )


@dp.message(lambda message: message.text == "Поиск")
async def search_start(message: types.Message, state: FSMContext):
    """
    Запуск поиска товаров
    """
    await message.answer("Введите ключевое слово или код товара для поиска (для отмены введите /cancel):")
    await state.set_state(SearchForm.waiting_for_query)


@dp.message(SearchForm.waiting_for_query)
async def process_search(message: types.Message, state: FSMContext):
    """
    Обработка запроса поиска и вывод результатов
    """
    query_text = message.text.strip().lower()
    results = []
    for category, products in db["categories"].items():
        for product_key, product in products.items():
            if (query_text in product.get("name", "").lower() or
                query_text in product.get("desc", "").lower() or
                query_text == product_key):
                results.append((category, product_key, product))
    if not results:
        await message.answer("Товар не найден.", reply_markup=main_menu_reply())
    else:
        text = "<b>Результаты поиска:</b>\n"
        keyboard = InlineKeyboardMarkup(inline_keyboard=[])
        for category, product_key, product in results:
            text += f"• <b>{product['name']}</b>\n"
            keyboard.inline_keyboard.append([
                InlineKeyboardButton(
                    text=product["name"],
                    callback_data=f"item:{category}:{product_key}"
                )
            ])
        keyboard.inline_keyboard.append([
            InlineKeyboardButton(text="В меню", callback_data="back_to_main")
        ])
        await message.answer(text, reply_markup=keyboard)
    await state.clear()


@dp.message(lambda message: message.text == "Назад")
async def go_back(message: types.Message):
    """
    Возврат в главное меню
    """
    await message.answer("Главное меню:", reply_markup=main_menu_reply())


# ★ Обработчики callback‑запросов ★

@dp.callback_query(lambda c: c.data == "back_to_main")
async def back_to_main_callback(query: types.CallbackQuery):
    """
    Возвращает в главное меню
    """
    await query.answer()
    try:
        await query.message.delete()
    except Exception as e:
        logger.exception("Ошибка удаления сообщения: %s", e)
    await bot.send_message(query.from_user.id, "Главное меню:", reply_markup=main_menu_reply())


@dp.callback_query(lambda c: c.data == "back_to_catalog")
async def back_to_catalog_callback(query: types.CallbackQuery):
    """
    Возвращает к выбору категорий
    """
    await query.answer()
    try:
        await query.message.delete()
    except Exception as e:
        logger.exception("Ошибка удаления сообщения: %s", e)
    await bot.send_message(query.from_user.id, "Выберите категорию:", reply_markup=catalog_menu_reply())


@dp.callback_query(lambda c: c.data and c.data.startswith("item:"))
async def product_item_callback(query: types.CallbackQuery):
    """
    Вывод подробностей товара
    """
    data = query.data.split(":")
    if len(data) != 3:
        await query.answer("Ошибка данных.")
        return
    _, category, product_key = data
    product = db["categories"].get(category, {}).get(product_key)
    if not product:
        await query.answer("Товар не найден.")
        return

    caption = (
        f"<b>🍵 {product['name']}</b>\n"
        f"<b>💰 Цена:</b> {product['price']}₽"
    )
    weight_match = re.search(r'(\d+)\s*г', product['name'], re.IGNORECASE)
    if weight_match:
        weight = float(weight_match.group(1))
        price_per_gram = product["price"] / weight
        caption += f"\n<b>💶Цена за грамм:</b> {price_per_gram:.2f}₽/г"
    caption += f"\n<i>{product['desc']}</i>"

    photo_source = product.get("photo", "")
    if photo_source.startswith("http"):
        try:
            await bot.send_photo(query.from_user.id,
                                 photo=photo_source,
                                 caption=caption,
                                 reply_markup=product_detail_inline(category, product_key))
        except Exception as e:
            logger.exception("Ошибка при отправке фото по URL: %s", e)
            await bot.send_message(query.from_user.id,
                                   "Ошибка при отправке фото по URL.\n" + caption,
                                   reply_markup=product_detail_inline(category, product_key))
    else:
        photo_path = os.path.join(BASE_DIR, photo_source)
        if not os.path.exists(photo_path):
            await bot.send_message(query.from_user.id,
                                   "Фото не найдено.\n" + caption,
                                   reply_markup=product_detail_inline(category, product_key))
        else:
            try:
                with open(photo_path, "rb") as photo:
                    await bot.send_photo(query.from_user.id,
                                         photo=photo,
                                         caption=caption,
                                         reply_markup=product_detail_inline(category, product_key))
            except Exception as e:
                logger.exception("Ошибка при открытии фото: %s", e)
                await bot.send_message(query.from_user.id,
                                       "Ошибка при открытии фото.\n" + caption,
                                       reply_markup=product_detail_inline(category, product_key))
    await query.answer()


@dp.callback_query(lambda c: c.data and c.data.startswith("back:"))
async def back_to_product_list_callback(query: types.CallbackQuery):
    """
    Возвращает список товаров выбранной категории
    """
    data = query.data.split(":")
    if len(data) != 2:
        await query.answer("Ошибка данных.")
        return
    _, category = data
    await query.answer()
    if query.message.photo:
        await query.message.edit_caption(
            caption=f"<b>Категория:</b> {category}\nВыберите товар:",
            reply_markup=product_list_inline(category)
        )
    else:
        await query.message.edit_text(
            f"<b>Категория:</b> {category}\nВыберите товар:",
            reply_markup=product_list_inline(category)
        )


@dp.callback_query(lambda c: c.data and c.data.startswith("add:"))
async def add_to_cart_callback(query: types.CallbackQuery):
    """
    Добавляет товар в корзину
    """
    data = query.data.split(":")
    if len(data) != 3:
        await query.answer("Ошибка данных.")
        return
    _, category, product_key = data
    user_id = query.from_user.id
    if user_id not in CARTS:
        CARTS[user_id] = []
    for item in CARTS[user_id]:
        if item["category"] == category and item["product_key"] == product_key:
            item["quantity"] += 1
            break
    else:
        CARTS[user_id].append({"category": category, "product_key": product_key, "quantity": 1})
    await query.answer("Товар добавлен в корзину.")


@dp.callback_query(lambda c: c.data == "clear_cart")
async def clear_cart_callback(query: types.CallbackQuery):
    """
    Очищает корзину пользователя
    """
    CARTS[query.from_user.id] = []
    await query.answer("Корзина очищена.")
    await query.message.edit_text("Ваша корзина пуста.",
                                  reply_markup=types.ReplyKeyboardRemove())


@dp.callback_query(lambda c: c.data == "edit_cart")
async def edit_cart_callback(query: types.CallbackQuery):
    """
    Переходит в режим редактирования корзины
    """
    await query.answer()
    text, keyboard = build_cart_edit_message(query.from_user.id)
    await query.message.edit_text(text, reply_markup=keyboard)


@dp.callback_query(lambda c: c.data and c.data.startswith("cart:"))
async def cart_edit_callback(query: types.CallbackQuery):
    """
    Обрабатывает изменение количества или удаление товара из корзины
    """
    try:
        _, action, category, product_key = query.data.split(":")
    except Exception as e:
        logger.exception("Неверный формат данных для редактирования корзины: %s", e)
        await query.answer("Ошибка данных.")
        return

    user_id = query.from_user.id
    items = CARTS.get(user_id, [])
    for item in items:
        if item["category"] == category and item["product_key"] == product_key:
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
    await query.answer()


@dp.callback_query(lambda c: c.data == "calc_cart")
async def calc_cart_callback(query: types.CallbackQuery, state: FSMContext):
    """
    Запускает процесс калькулятора: фильтрует товары с указанным весом и переводит в режим ввода грамм.
    """
    user_id = query.from_user.id
    items = CARTS.get(user_id, [])
    if not items:
        await query.answer("Ваша корзина пуста.", show_alert=True)
        return

    calc_items = []
    for item in items:
        category = item["category"]
        product_key = item["product_key"]
        product = db["categories"].get(category, {}).get(product_key)
        if product and "weight" in product:
            calc_items.append((category, product_key))
    if not calc_items:
        await query.answer("Нет товаров с указанным весом для расчёта.", show_alert=True)
        return

    await state.update_data(calc_items=calc_items, calc_index=0, calc_total=0, calc_results=[])
    category, product_key = calc_items[0]
    product = db["categories"].get(category, {}).get(product_key)
    price_per_gram = product["price"] / product["weight"]
    await query.message.edit_text(
        f"Введите количество грамм для <b>{product['name']}</b>\n"
        f"(Цена за грамм: {price_per_gram:.2f}₽):",
        parse_mode=ParseMode.HTML
    )
    await state.set_state(TeaCalcForm.waiting_for_grams)
    await query.answer()


@dp.message(TeaCalcForm.waiting_for_grams)
async def process_grams(message: types.Message, state: FSMContext):
    """
    Обрабатывает ввод грамм для товара, рассчитывает стоимость (округляя вверх) и переходит к следующему товару,
    либо выводит итоговый результат с кнопками "Назад" и "В меню".
    """
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

    category, product_key = calc_items[calc_index]
    product = db["categories"].get(category, {}).get(product_key)
    price_per_gram = product["price"] / product["weight"]
    subtotal = math.ceil(grams * price_per_gram)
    calc_total += subtotal
    calc_results.append({
        "name": product["name"],
        "grams": grams,
        "subtotal": subtotal
    })

    calc_index += 1

    if calc_index < len(calc_items):
        next_category, next_product_key = calc_items[calc_index]
        next_product = db["categories"].get(next_category, {}).get(next_product_key)
        next_price_per_gram = next_product["price"] / next_product["weight"]
        await state.update_data(calc_index=calc_index, calc_total=calc_total, calc_results=calc_results)
        await message.answer(
            f"Введите количество грамм для <b>{next_product['name']}</b>\n"
            f"(Цена за грамм: {next_price_per_gram:.2f}₽):",
            parse_mode=ParseMode.HTML
        )
    else:
        result_text = "<b>Расчёт стоимости по граммам:</b>\n\n"
        for item in calc_results:
            result_text += f"<b>{item['name']}</b>: {item['grams']} г – {item['subtotal']}₽\n"
        result_text += f"\n<b>Итог:</b> {calc_total}₽"
        result_keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Назад", callback_data="back_to_cart")],
            [InlineKeyboardButton(text="В меню", callback_data="back_to_main")]
        ])
        await message.answer(result_text, parse_mode=ParseMode.HTML, reply_markup=result_keyboard)
        await state.clear()


@dp.callback_query(lambda c: c.data == "back_to_cart")
async def back_to_cart_callback(query: types.CallbackQuery):
    """
    Возвращает к просмотру корзины после расчёта калькулятором
    """
    await query.answer()
    cart_text, cart_keyboard = build_cart_message(query.from_user.id)
    await query.message.edit_text(cart_text, reply_markup=cart_keyboard)


@dp.callback_query(lambda c: c.data == "checkout")
async def checkout_callback(query: types.CallbackQuery, state: FSMContext):
    """
    При нажатии "Оформить заказ" переходим к вводу контактных данных.
    """
    if not CARTS.get(query.from_user.id):
        await query.answer("Ваша корзина пуста.", show_alert=True)
        return
    await query.answer()
    await query.message.edit_text("Введите ваше ФИО (для отмены введите /cancel):")
    await state.set_state(OrderForm.waiting_for_fio)


@dp.message(OrderForm.waiting_for_fio)
async def process_fio(message: types.Message, state: FSMContext):
    """
    Получает ФИО и запрашивает адрес доставки.
    """
    fio = message.text.strip()
    if not fio:
        await message.answer("Пожалуйста, введите корректное ФИО:")
        return
    await state.update_data(fio=fio)
    await message.answer("Введите адрес доставки (для отмены введите /cancel):")
    await state.set_state(OrderForm.waiting_for_address)


@dp.message(OrderForm.waiting_for_address)
async def process_address(message: types.Message, state: FSMContext):
    """
    Получает адрес доставки и запрашивает номер телефона.
    """
    address = message.text.strip()
    if not address:
        await message.answer("Пожалуйста, введите корректный адрес:")
        return
    await state.update_data(address=address)
    await message.answer("Введите номер телефона (для отмены введите /cancel):")
    await state.set_state(OrderForm.waiting_for_phone)


@dp.message(OrderForm.waiting_for_phone)
async def process_phone(message: types.Message, state: FSMContext):
    """
    Получает номер телефона и переходит к вводу комментария.
    """
    phone = message.text.strip()
    if not phone:
        await message.answer("Пожалуйста, введите корректный номер телефона:")
        return
    await state.update_data(phone=phone)
    await message.answer("Оставьте комментарий к заказу, если таковой имеется (для отмены введите /cancel):")
    await state.set_state(OrderForm.waiting_for_comment)


@dp.message(OrderForm.waiting_for_comment)
async def process_comment(message: types.Message, state: FSMContext):
    """
    Получает комментарий к заказу.
    """
    comment = message.text.strip()
    if not comment:
        await message.answer("Здесь можете уточнить детали заказа:")
        return
    await state.update_data(comment=comment)
    await message.answer("Введите промокод, если есть (для отмены введите /cancel):")
    await state.set_state(OrderForm.waiting_for_promo)


@dp.message(OrderForm.waiting_for_promo)
async def process_promo(message: types.Message, state: FSMContext):
    """
    Получает промокод и генерирует номер заказа.
    """
    promo = message.text.strip()
    if not promo:
        await message.answer("Оставьте прочерк, если нет промокода.")
        return

    user_data = await state.get_data()
    fio = user_data.get("fio", "Не указано")
    address = user_data.get("address", "Не указан")
    phone = user_data.get("phone", "Не указан")
    comment = user_data.get("comment", "Не указан")
    user_id = message.from_user.id
    items = CARTS.get(user_id, [])
    if not items:
        await message.answer("Ваша корзина пуста.")
        await state.clear()
        return

    order_number = uuid.uuid4().hex[:8].upper()
    order_text = f"<b>Номер заказа:</b> {order_number}\n\n"
    order_text += "<b>Состав заказа:</b>\n"
    total = 0
    for item in items:
        category = item["category"]
        product_key = item["product_key"]
        quantity = item["quantity"]
        product = db["categories"].get(category, {}).get(product_key, {})
        if not product:
            continue
        name = product.get("name", "Товар")
        price = product.get("price", 0)
        subtotal = price * quantity
        total += subtotal
        order_text += f"<b>{name}</b> x{quantity} — {subtotal}₽\n"
    order_text += f"\n<b>Итого:</b> {total}₽\n\n"
    order_text += "<b>Контактная информация:</b>\n"
    order_text += f"ФИО: {fio}\n"
    order_text += f"Адрес: {address}\n"
    order_text += f"Телефон: {phone}\n\n"
    order_text += f"Комментарий: {comment}\n\n\n"
    order_text += f"Промокод: {promo}\n\n\n\n"
    # Добавляем информацию об отправителе заказа с указанием ID пользователя
    order_text += f"<i>Отправил: {message.from_user.full_name} (@{message.from_user.username}), ID: {message.from_user.id}</i>"

    try:
        admin_chat = ADMIN  # ADMIN может быть списком или строкой/числом
        if isinstance(admin_chat, str) and admin_chat.startswith('@'):
            chat_obj = await bot.get_chat(admin_chat)
            admin_chat = chat_obj.id

        # Если admin_chat - список, отправляем каждому админу
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

    await message.answer(f"Ваш заказ принят. Номер заказа: <b>{order_number}</b>\nОжидайте инструкций по оплате.",
                         disable_web_page_preview=True)
    CARTS[user_id] = []  # Очищаем корзину
    await state.clear()
    await message.answer("Главное меню:", reply_markup=main_menu_reply())




# ★ Запуск бота ★

async def main():
    try:
        await bot.delete_webhook(drop_pending_updates=True)
    except Exception as e:
        logger.exception("Ошибка удаления webhook: %s", e)
    # Запускаем фоновую задачу для очистки кеша каждые 10 часов
    asyncio.create_task(clear_cache_periodically())
    await dp.start_polling(bot, skip_updates=True)

@dp.message(lambda message: message.text and not message.text.startswith("/"))
async def handle_messages(message: types.Message):
    await handle_admin_command(message, bot)
    await handle_user_message(message, bot)


if __name__ == "__main__":
    asyncio.run(main())
