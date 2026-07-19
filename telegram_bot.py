from html import escape
import os
import asyncio

from dotenv import load_dotenv

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command, CommandStart
from aiogram.types import Message, CallbackQuery, BotCommand

from telegram_keyboards import (
    main_menu_keyboard,
    back_to_menu_keyboard,
    subscription_keyboard,
    operator_selection_keyboard
)

from database.repositories.bot_user_repository import (
    get_or_create_user,
    is_subscription_active
)

from database.repositories.public_config_repository import(
    get_public_configs_for_subscription
)

from utils.date_utils import format_jalali_datetime

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")

PUBLIC_BASE_URL = os.getenv(
    "PUBLIC_BASE_URL",
    "http://127.0.0.1:8000"
).rstrip("/")

FREE_TRIAL_DAYS =int(
    os.getenv("FREE_TRIAL_DAYS", "3")
)

ENABLE_MANUAL_CONFIGS = os.getenv(
    "ENABLE_MANUAL_CONFIGS",
    "false"
).lower() == "true"

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is missing in .env")


dp = Dispatcher()
LRM= "\u200e"
def ltr(text):
    return LRM + text

def build_subscription_url(user, operator="default"):
    return (
        f"{PUBLIC_BASE_URL}/sub/"
        f"{user['subscription_token']}"
        f"?operator={operator}"
    )

def get_operator_label(operator):
    if operator == "mci":
        return "همراه اول"

    if operator == "default":
        return "ایرانسل"

    return operator


def ensure_active_user(user):
    return bool(user and user.get("is_active"))


def manual_configs_disabled_text():
    return (
        "🧩 دریافت کانفیگ دستی در حال حاضر غیرفعال است.\n\n"
        "برای امنیت بیشتر و دریافت آپدیت دائمی، لطفاً از گزینه "
        "«🔗 دریافت لینک اشتراک» استفاده کنید.\n\n"
        "اگر برنامه V2RayN بدون فیلترشکن لینک را Update نمی‌کند، "
        "ابتدا با یک اتصال موقت وارد شوید، سپس لینک اشتراک را Update کنید."
    )

def get_or_create_user_from_message(message: Message):
    from_user = message.from_user

    return get_or_create_user(
        telegram_id=from_user.id,
        chat_id=message.chat.id,
        username=from_user.username,
        first_name=from_user.first_name,
        last_name=from_user.last_name,
        language_code=from_user.language_code,
    )


def get_or_create_user_from_callback(callback: CallbackQuery):
    from_user = callback.from_user

    return get_or_create_user(
        telegram_id=from_user.id,
        chat_id=callback.message.chat.id,
        username=from_user.username,
        first_name=from_user.first_name,
        last_name=from_user.last_name,
        language_code=from_user.language_code,
    )


@dp.message(CommandStart())
async def start_handler(message: Message):
    user = get_or_create_user_from_message(message)

    if user.get("trial_was_activated"):
        text = (
            "🚀 به M Proxy خوش آمدید.\n\n"
            f"اشتراک رایگان {FREE_TRIAL_DAYS} روزه شما فعال شد.\n\n"
            "برای دریافت لینک اشتراک V2RayN، روی دکمه زیر بزنید.\n\n"
            "برای استفاده از این سرویس نیازی به ارسال شماره تلفن نیست."
        )
    elif user["is_active"]:
        subscription_end = format_jalali_datetime(
            user["subscription_end"]
        )
        text = (
            "🚀 خوش برگشتی.\n\n"
            "اشتراک شما تا این تاریخ فعال است:\n"
            f"{subscription_end}\n\n"
            "از منوی زیر ادامه دهید."
        )
    else:
        subscription_end = format_jalali_datetime(
            user["subscription_end"]
        )
        text = (
            "🚀 خوش برگشتی.\n\n"
            "اشتراک شما در این تاریخ به پایان رسیده است:\n"
            f"{subscription_end}\n\n"
            "برای ادامه استفاده، از گزینه «💳 تمدید اشتراک» استفاده کنید."
        )

    await message.answer(
        text,
        reply_markup=main_menu_keyboard()
    )


@dp.callback_query(F.data == "menu:home")
async def menu_home_callback(callback: CallbackQuery):
    await callback.answer()

    await callback.message.edit_text(
        "به منوی اصلی خوش آمدید.\n\n"
        "یکی از گزینه‌های زیر را انتخاب کنید.",
        reply_markup=main_menu_keyboard()
    )


@dp.message(Command("sub"))
async def subscription_handler(message: Message):
    user = get_or_create_user_from_message(message)

    if not ensure_active_user(user):
        await message.answer(
            "اشتراک شما فعال نیست.\n",
            reply_markup=main_menu_keyboard()
        )
        return

    await message.answer(
        "برای دریافت لینک اشتراک، اپراتور موردنظر را انتخاب کنید:",
        reply_markup=operator_selection_keyboard("sub")
    )


@dp.callback_query(F.data == "menu:sub")
async def subscription_callback(callback: CallbackQuery):
    await callback.answer()

    user = get_or_create_user_from_callback(callback)

    if not ensure_active_user(user):
        await callback.message.edit_text(
            "اشتراک شما فعال نیست.\n",
            reply_markup=back_to_menu_keyboard()
        )
        return

    await callback.message.edit_text(
        "برای دریافت لینک اشتراک، اپراتور موردنظر را انتخاب کنید:",
        reply_markup=operator_selection_keyboard("sub")
    )

@dp.callback_query(F.data.startswith("sub:operator:"))
async def subscription_operator_callback(callback: CallbackQuery):
    await callback.answer()

    operator = callback.data.split(":")[-1]

    user = get_or_create_user_from_callback(callback)

    if not ensure_active_user(user):
        await callback.message.edit_text(
            "اشتراک شما فعال نیست.\n",
            reply_markup=back_to_menu_keyboard()
        )
        return

    sub_url = build_subscription_url(
        user,
        operator=operator
    )
    safe_sub_url = escape(sub_url)
    operator_label = get_operator_label(operator)

    await callback.message.edit_text(
        f"🔗 لینک اشتراک {operator_label} آماده است.\n\n"
        f"<code>{safe_sub_url}</code>\n\n"
        "برای کپی کردن لینک، روی دکمه «📋 کپی لینک اشتراک» بزنید.\n"
        "سپس آن را در بخش Subscription برنامه V2RayN اضافه کنید.",
        reply_markup=subscription_keyboard(sub_url),
        parse_mode="HTML"
    )


@dp.message(Command("status"))
async def status_handler(message: Message):
    user = get_or_create_user_from_message(message)

    if user["is_active"]:
        subscription_end = format_jalali_datetime(user['subscription_end'])
        text = (
            "وضعیت اشتراک:\n\n"
            "اشتراک شما فعال است.\n\n"
            f"پایان اشتراک:\n"
            f"{subscription_end}"
        )
    else:
        text = (
            "وضعیت اشتراک:\n\n"
            "اشتراک شما تمام شده است.\n\n"
        )

    await message.answer(
        text,
        reply_markup=main_menu_keyboard()
    )


@dp.callback_query(F.data == "menu:status")
async def status_callback(callback: CallbackQuery):
    await callback.answer()

    user = get_or_create_user_from_callback(callback)

    if user["is_active"]:
        subscription_end = format_jalali_datetime(user['subscription_end'])
        text = (
            "وضعیت اشتراک:\n\n"
            "اشتراک شما فعال است.\n\n"
            f"پایان اشتراک:\n"
            f"{subscription_end}"
        )
    else:
        text = (
            "وضعیت اشتراک:\n\n"
            "اشتراک شما تمام شده است.\n\n"
        )

    await callback.message.edit_text(
        text,
        reply_markup=back_to_menu_keyboard()
    )


@dp.message(Command("configs"))
async def configs_handler(message: Message):
    user = get_or_create_user_from_message(message)

    if not ensure_active_user(user):
        await message.answer(
            "اشتراک شما فعال نیست.\n",
            reply_markup=main_menu_keyboard()
        )
        return
    
    if not ENABLE_MANUAL_CONFIGS:
        await message.answer(
            manual_configs_disabled_text(),
            reply_markup=main_menu_keyboard()
        )
        return

    await message.answer(
        "برای دریافت کانفیگ دستی، اپراتور موردنظر را انتخاب کنید:\n\n"
        "این گزینه برای زمانی است که برنامه نتواند لینک اشتراک را بدون فیلترشکن Update کند.",
        reply_markup=operator_selection_keyboard("manual")
    )

@dp.callback_query(F.data == "menu:manual")
async def manual_configs_menu_callback(callback: CallbackQuery):
    await callback.answer()

    user = get_or_create_user_from_callback(callback)

    if not ensure_active_user(user):
        await callback.message.edit_text(
            "اشتراک شما فعال نیست.\n",
            reply_markup=back_to_menu_keyboard()
        )
        return
    
    if not ENABLE_MANUAL_CONFIGS:
        await callback.message.edit_text(
            manual_configs_disabled_text(),
            reply_markup=back_to_menu_keyboard()
        )
        return

    await callback.message.edit_text(
        "برای دریافت کانفیگ دستی، اپراتور موردنظر را انتخاب کنید:\n\n"
        "این گزینه برای زمانی است که برنامه نتواند لینک اشتراک را بدون فیلترشکن Update کند.",
        reply_markup=operator_selection_keyboard("manual")
    )


@dp.callback_query(F.data.startswith("manual:operator:"))
async def manual_configs_operator_callback(callback: CallbackQuery):
    await callback.answer()

    operator = callback.data.split(":")[-1]

    user = get_or_create_user_from_callback(callback)

    if not ensure_active_user(user):
        await callback.message.edit_text(
            "اشتراک شما فعال نیست.\n",
            reply_markup=back_to_menu_keyboard()
        )
        return
    
    if not ENABLE_MANUAL_CONFIGS:
        await callback.message.edit_text(
            manual_configs_disabled_text(),
            reply_markup=back_to_menu_keyboard()
        )
        return

    operator_label = get_operator_label(operator)

    try:
        configs = get_public_configs_for_subscription(
            operator=operator,
            limit=10,
        )
    except ValueError:
        await callback.message.edit_text(
            "اپراتور انتخاب‌شده معتبر نیست.",
            reply_markup=back_to_menu_keyboard()
        )
        return

    if not configs:
        await callback.message.edit_text(
            "در حال حاضر کانفیگ سالمی برای این اپراتور موجود نیست.",
            reply_markup=back_to_menu_keyboard()
        )
        return

    await callback.message.edit_text(
        f"🧩 کانفیگ‌های دستی {operator_label}\n\n"
        "هر کانفیگ را جداگانه کپی و در V2RayN import کنید.\n"
        "بعد از اتصال، بهتر است لینک اشتراک همان اپراتور را هم Update کنید.",
        reply_markup=back_to_menu_keyboard()
    )

    total = len(configs)

    for index, config_text in enumerate(configs, start=1):
        safe_config = escape(config_text)

        await callback.message.answer(
            f"کانفیگ {index} از {total}:\n\n"
            f"<code>{safe_config}</code>",
            parse_mode="HTML"
        )

@dp.message(Command("help"))
async def help_handler(message: Message):
    text = (
        "📘 راهنما:\n\n"
        "۱. روی «دریافت لینک اشتراک» بزنید.\n"
        "۲. اپراتور موردنظر را انتخاب کنید.\n"
        "۳. لینک را در V2RayN اضافه کنید.\n"
        "۴. لینک اشتراک را هر چند ساعت یک‌بار Update کنید "
        "تا جدیدترین سرورها دریافت شوند.\n\n"
        "اگر V2RayN بدون اتصال اولیه لینک را Update نکرد، "
        "ابتدا با یک اتصال موقت وصل شوید و سپس "
        "Subscription را Update کنید.\n\n"
        "دستورهای متنی:\n\n"
        "/start - نمایش منوی اصلی\n"
        "/sub - دریافت لینک اشتراک\n"
        "/status - وضعیت اشتراک\n"
        "/help - راهنما"
    )

    await message.answer(
        text,
        reply_markup=main_menu_keyboard()
    )

@dp.callback_query(F.data == "menu:help")
async def help_callback(callback: CallbackQuery):
    await callback.answer()

    text = (
        "📘 راهنما:\n\n"
        "۱. روی «دریافت لینک اشتراک» بزنید.\n"
        "۲. اپراتور موردنظر را انتخاب کنید.\n"
        "۳. لینک را در V2RayN اضافه کنید.\n"
        "۴. لینک اشتراک را هر چند ساعت یک‌بار Update کنید "
        "تا جدیدترین سرورها دریافت شوند.\n\n"
        "اگر V2RayN بدون اتصال اولیه لینک را Update نکرد، "
        "ابتدا با یک اتصال موقت وصل شوید و سپس "
        "Subscription را Update کنید.\n\n"
        "دستورهای متنی:\n\n"
        "/start - نمایش منوی اصلی\n"
        "/sub - دریافت لینک اشتراک\n"
        "/status - وضعیت اشتراک\n"
        "/help - راهنما"
    )

    await callback.message.edit_text(
        text,
        reply_markup=back_to_menu_keyboard()
    )

@dp.message(F.contact)
async def contact_handler(message: Message):
    await message.answer(
        "برای استفاده از این سرویس نیازی به ارسال شماره تلفن نیست.\n"
        "شماره تلفن شما ذخیره نمی‌شود.\n\n"
        "برای دریافت لینک اشتراک از منوی زیر استفاده کنید.",
        reply_markup=main_menu_keyboard()
    )

@dp.callback_query(F.data == "menu:renew")
async def renew_subscription_callback(callback: CallbackQuery):
    await callback.answer()

    user = get_or_create_user_from_callback(callback)

    telegram_id = user["telegram_id"]

    if ENABLE_MANUAL_CONFIGS:
        delivery_text = (
            "بعد از تمدید دوباره از منو لینک اشتراک "
            "یا کانفیگ دستی را دریافت کنید."
        )
    else:
        delivery_text = (
            "بعد از تمدید دوباره از منو لینک اشتراک را دریافت کنید."
        )

    text = (
        "💳 تمدید اشتراک\n\n"
        "برای تمدید، شناسه تلگرام زیر را برای پشتیبانی ارسال کنید:\n\n"
        f"<code>{telegram_id}</code>\n\n"
        f"{delivery_text}"
    )

    await callback.message.edit_text(
        text,
        reply_markup=back_to_menu_keyboard(),
        parse_mode="HTML"
    )


async def setup_bot_commands(bot: Bot):
    commands = [
        BotCommand(
            command="start",
            description="شروع و نمایش منوی اصلی"
        ),
        BotCommand(
            command="sub",
            description="دریافت لینک اشتراک V2RayN"
        ),
        BotCommand(
            command="status",
            description="وضعیت اشتراک"
        ),
    ]

    if ENABLE_MANUAL_CONFIGS:
        commands.append(
            BotCommand(
                command="configs",
                description="دریافت کانفیگ دستی"
            )
        )

    commands.append(
        BotCommand(
            command="help",
            description="راهنما"
        )
    )

    await bot.set_my_commands(commands)


async def main():
    bot = Bot(token=BOT_TOKEN)

    await setup_bot_commands(bot)

    print("BOT STARTED")

    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
