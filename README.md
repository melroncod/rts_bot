# Random Tea Store Bot

A lightweight and functional Telegram bot for ordering tea and tea accessories online.

## Overview

- Provide users with an intuitive interface to browse the product catalog.
- Allow users to build a cart, place orders step by step, and send order details directly to the admin.
- Automatically clear cart cache every 10 hours.

## Features

- **Product Catalog**
  - Tea categories and products defined in `database.json` (name, weight, price, description, photo).
  - Navigation via custom reply and inline keyboards.
- **Cart Management**
  - Add or remove items.
  - View cart contents and total price.
  - Clear the cart using `/clear`.
- **Order Placement (FSM)**
  - Step-by-step collection of user details: Name → Address → Phone → Comment → Promo code (optional).
  - Input validation and rounding logic.
- **Admin Notifications**
  - Forwards all user messages (excluding admins) to the admin chat.
  - Instant notifications for new orders and inquiries.
- **Auto Cache Clearing**
  - Background task resets all carts every 10 hours.

## Bot Link

Use the bot to order tea here: https://t.me/RandomTeaStore_Bot

## Commands

| Command     | Description                                |
|-------------|--------------------------------------------|
| `/start`    | Welcome message and main menu              |
| `/menu`     | Display product categories                 |
| `/cart`     | Show cart contents                         |
| `/clear`    | Clear the cart                             |
| `/checkout` | Place an order (step-by-step)              |
| `/help`     | Show available commands                    |

### Admin Features

- All user messages are automatically forwarded to admin chat.
- Admins defined by `config.ADMIN`.

## Dependencies

- Python 3.11
- Aiogram
- python-dotenv

Dependencies listed in `requirements.txt`.

## License

Distributed under the GNU License.  
© 2025 melroncod

---

# Random Tea Store Bot

Телеграм-бот для заказа чая и аксессуаров.

## Обзор

- Удобный интерфейс для навигации по каталогу товаров.
- Собирайте корзину, оформляйте заказ пошагово, данные сразу отправляются администратору.
- Автоматическое очищение корзины каждые 10 часов.

## Функционал

- **Каталог товаров**
  - Категории чая и товары из `database.json` (название, вес, цена, описание, фото).
  - Навигация через встроенные клавиатуры.
- **Управление корзиной**
  - Добавление/удаление позиций.
  - Просмотр содержимого и общей стоимости.
  - Очистка корзины командой `/clear`.
- **Оформление заказа (FSM)**
  - Пошаговый сбор данных: ФИО → адрес → телефон → комментарий → промокод (опционально).
  - Валидация и округление.
- **Уведомления администратору**
  - Пересылка всех сообщений пользователей в админ-чат.
  - Мгновенные уведомления о новых заказах и запросах.
- **Авто-очистка корзины**
  - Фоновая задача очищает корзину каждые 10 часов.

## Ссылка

Воспользоваться ботом и заказать чай можно по ссылке: https://t.me/RandomTeaStore_Bot

## Команды

| Команда     | Описание                                   |
|-------------|--------------------------------------------|
| `/start`    | Приветствие и главное меню                 |
| `/menu`     | Показать категории товаров                 |
| `/cart`     | Показать содержимое корзины                |
| `/clear`    | Очистить корзину                           |
| `/checkout` | Оформить заказ (пошагово)                  |
| `/help`     | Справка по командам                        |

### Админ-функции

- Все сообщения пользователей пересылаются в админ-чат.
- Администраторы задаются через `config.ADMIN`.

## Зависимости

- Python 3.11
- Aiogram
- python-dotenv

Зависимости указаны в `requirements.txt`.

## Лицензия

Distributed under the GNU License.  
© 2025 melroncod

