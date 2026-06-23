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
    subscription_keyboard
)

from database.repositories.bot_user_repository import (
    get_or_create_user,
    is_subscription_active
)

from utils.date_utils import format_jalali_datetime

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")

PUBLIC_BASE_URL = os.getenv(
    "PUBLIC_BASE_URL",
    "http://127.0.0.1:8000"
).rstrip("/")

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is missing in .env")


dp = Dispatcher()
LRM= "\u200e"
def ltr(text):
    return LRM + text

def build_subscription_url(user):
    return (
        f"{PUBLIC_BASE_URL}/sub/"
        f"{user['subscription_token']}"
    )


@dp.message(CommandStart())
async def start_handler(message: Message):
    user = get_or_create_user(
        telegram_id=message.from_user.id,
        username=message.from_user.username,
        first_name=message.from_user.first_name
    )

    if user["created"]:
        text = (
            "🚀 به MN Proxy خوش آمدید.\n\n"
            "اشتراک رایگان ۱۰ روزه شما فعال شد.\n\n"
            "برای دریافت لینک اشتراک V2RayN، روی دکمه زیر بزنید.\n\n"
            "برای استفاده از این سرویس نیازی به ارسال شماره تلفن نیست."
        )
    else:
        subscription_end = format_jalali_datetime(user['subscription_end'])
        text = (
            "🚀 خوش برگشتی.\n\n"
            f"اشتراک شما تا این تاریخ فعال است:\n"
            f"{subscription_end}\n\n"
            "از منوی زیر ادامه دهید."
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
    user = get_or_create_user(
        telegram_id=message.from_user.id,
        username=message.from_user.username,
        first_name=message.from_user.first_name
    )

    if not user["is_active"]:
        await message.answer(
            "اشتراک شما فعال نیست.\n"
            "فعلاً بخش پرداخت فعال نشده.",
            reply_markup=main_menu_keyboard()
        )
        return

    sub_url = build_subscription_url(user)
    safe_sub_url = escape(sub_url)

    await message.answer(
        "🔗 لینک اشتراک اختصاصی شما آماده است.\n\n"
        f"<code>{safe_sub_url}</code>\n\n"
        "برای کپی کردن لینک، روی دکمه «📋 کپی لینک اشتراک» بزنید.\n"
        "سپس آن را در بخش Subscription برنامه V2RayN اضافه کنید.",
        reply_markup=subscription_keyboard(sub_url),
        parse_mode="HTML"
    )


@dp.callback_query(F.data == "menu:sub")
async def subscription_callback(callback: CallbackQuery):
    await callback.answer()

    user = get_or_create_user(
        telegram_id=callback.from_user.id,
        username=callback.from_user.username,
        first_name=callback.from_user.first_name
    )

    if not user["is_active"]:
        await callback.message.edit_text(
            "اشتراک شما فعال نیست.\n"
            "فعلاً بخش پرداخت فعال نشده.",
            reply_markup=back_to_menu_keyboard()
        )
        return

    sub_url = build_subscription_url(user)
    safe_sub_url = escape(sub_url)

    await callback.message.edit_text(
        "🔗 لینک اشتراک اختصاصی شما آماده است.\n\n"
        f"<code>{safe_sub_url}</code>\n\n"
        "برای کپی کردن لینک، روی دکمه «📋 کپی لینک اشتراک» بزنید.\n"
        "سپس آن را در بخش Subscription برنامه V2RayN اضافه کنید.",
        reply_markup=subscription_keyboard(sub_url),
        parse_mode="HTML"
    )


@dp.message(Command("status"))
async def status_handler(message: Message):
    user = get_or_create_user(
        telegram_id=message.from_user.id,
        username=message.from_user.username,
        first_name=message.from_user.first_name
    )

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
            "فعلاً بخش پرداخت فعال نشده."
        )

    await message.answer(
        text,
        reply_markup=main_menu_keyboard()
    )


@dp.callback_query(F.data == "menu:status")
async def status_callback(callback: CallbackQuery):
    await callback.answer()

    user = get_or_create_user(
        telegram_id=callback.from_user.id,
        username=callback.from_user.username,
        first_name=callback.from_user.first_name
    )

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
            "فعلاً بخش پرداخت فعال نشده."
        )

    await callback.message.edit_text(
        text,
        reply_markup=back_to_menu_keyboard()
    )


@dp.message(Command("configs"))
async def configs_handler(message: Message):
    active = is_subscription_active(message.from_user.id)

    if not active:
        await message.answer(
            "اشتراک شما فعال نیست.\n"
            "فعلاً بخش پرداخت فعال نشده.",
            reply_markup=main_menu_keyboard()
        )
        return

    await message.answer(
        "کانفیگ‌ها دیگر مستقیم در چت ارسال نمی‌شوند.\n\n"
        "برای دریافت لینک اشتراک V2RayN از دستور زیر استفاده کنید:\n"
        "/sub\n\n"
        "با لینک اشتراک، V2RayN با هر Update پنج کانفیگ برتر فعلی را دریافت می‌کند.",
        reply_markup=main_menu_keyboard()
    )


@dp.message(Command("privacy"))
async def privacy_handler(message: Message):
    await message.answer(
        "حریم خصوصی:\n\n"
        "- برای استفاده از این سرویس نیازی به شماره تلفن نیست.\n"
        "- Bot شماره تلفن، لوکیشن یا contact درخواست نمی‌کند.\n"
        "- Bot فقط شناسه تلگرام و وضعیت اشتراک را برای مدیریت دسترسی نگه می‌دارد.\n"
        "- لینک اشتراک اختصاصی شما فقط برای دریافت کانفیگ‌های به‌روز استفاده می‌شود.\n\n"
        "نکته: لطفاً لینک اشتراک خود را برای دیگران ارسال نکنید.",
        reply_markup=main_menu_keyboard()
    )


@dp.callback_query(F.data == "menu:privacy")
async def privacy_callback(callback: CallbackQuery):
    await callback.answer()

    await callback.message.edit_text(
        "حریم خصوصی:\n\n"
        "- برای استفاده از این سرویس نیازی به شماره تلفن نیست.\n"
        "- Bot شماره تلفن، لوکیشن یا contact درخواست نمی‌کند.\n"
        "- Bot فقط شناسه تلگرام و وضعیت اشتراک را برای مدیریت دسترسی نگه می‌دارد.\n"
        "- لینک اشتراک اختصاصی شما فقط برای دریافت کانفیگ‌های به‌روز استفاده می‌شود.\n\n"
        "نکته: لطفاً لینک اشتراک خود را برای دیگران ارسال نکنید.",
        reply_markup=back_to_menu_keyboard()
    )


@dp.message(Command("help"))
async def help_handler(message: Message):
    await message.answer(
        "📘 راهنما:\n\n"
        "۱. روی «دریافت لینک اشتراک» بزنید.\n"
        "۲. لینک را در V2RayN اضافه کنید.\n"
        "۳. هر بار Update بزنید، ۵ کانفیگ برتر فعلی دریافت می‌شود.\n\n"
        "دستورهای متنی:\n\n"
        "/start - نمایش منوی اصلی\n"
        "/sub - دریافت لینک اشتراک\n"
        "/status - وضعیت اشتراک\n"
        "/privacy - توضیح حریم خصوصی\n"
        "/configs - راهنمای دریافت کانفیگ از طریق Subscription\n"
        "/help - راهنما\n\n"
        "کانفیگ‌ها مستقیم در چت ارسال نمی‌شوند.",
        reply_markup=main_menu_keyboard()
    )

@dp.callback_query(F.data == "menu:help")
async def help_callback(callback: CallbackQuery):
    await callback.answer()

    await callback.message.edit_text(
        "📘 راهنما:\n\n"
        "۱. روی «دریافت لینک اشتراک» بزنید.\n"
        "۲. لینک را در V2RayN اضافه کنید.\n"
        "۳. هر بار Update بزنید، ۵ کانفیگ برتر فعلی دریافت می‌شود.\n\n"
        "دستورهای متنی:\n\n"
        "/start - نمایش منوی اصلی\n"
        "/sub - دریافت لینک اشتراک\n"
        "/status - وضعیت اشتراک\n"
        "/privacy - توضیح حریم خصوصی\n"
        "/configs - راهنمای دریافت کانفیگ از طریق Subscription\n"
        "/help - راهنما\n\n"
        "کانفیگ‌ها مستقیم در چت ارسال نمی‌شوند.",
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
        BotCommand(
            command="privacy",
            description="حریم خصوصی"
        ),
        BotCommand(
            command="help",
            description="راهنما"
        ),
    ]

    await bot.set_my_commands(commands)


async def main():
    bot = Bot(token=BOT_TOKEN)

    await setup_bot_commands(bot)

    print("BOT STARTED")

    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())