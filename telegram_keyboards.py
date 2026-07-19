import os

from dotenv import load_dotenv
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CopyTextButton


load_dotenv()

CHANNEL_URL = os.getenv("CHANNEL_URL")

ENABLE_MANUAL_CONFIGS = os.getenv(
    "ENABLE_MANUAL_CONFIGS",
    "false"
).lower() == "true"


def main_menu_keyboard():
    keyboard = [
        [
            InlineKeyboardButton(
                text="🔗 دریافت لینک اشتراک",
                callback_data="menu:sub"
            )
        ],
    ]

    if ENABLE_MANUAL_CONFIGS:
        keyboard.append(
            [
                InlineKeyboardButton(
                    text="🧩 دریافت کانفیگ دستی",
                    callback_data="menu:manual"
                )
            ]
        )

    keyboard.extend(
        [
            [
                InlineKeyboardButton(
                    text="📊 وضعیت اشتراک",
                    callback_data="menu:status"
                )
            ],
            [
                InlineKeyboardButton(
                    text="📘 راهنما",
                    callback_data="menu:help"
                )
            ],
            [
                InlineKeyboardButton(
                    text="💳 تمدید اشتراک",
                    callback_data="menu:renew"
                )
            ],
        ]
    )

    if CHANNEL_URL:
        keyboard.append(
            [
                InlineKeyboardButton(
                    text="📣 کانال ما",
                    url=CHANNEL_URL
                )
            ]
        )

    return InlineKeyboardMarkup(
        inline_keyboard=keyboard
    )


def back_to_menu_keyboard():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="⬅️ بازگشت به منو",
                    callback_data="menu:home"
                )
            ]
        ]
    )


def operator_selection_keyboard(mode):
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="همراه اول",
                    callback_data=f"{mode}:operator:mci"
                )
            ],
            [
                InlineKeyboardButton(
                    text="ایرانسل",
                    callback_data=f"{mode}:operator:default"
                )
            ],
            [
                InlineKeyboardButton(
                    text="⬅️ بازگشت به منو",
                    callback_data="menu:home"
                )
            ]
        ]
    )


def subscription_keyboard(sub_url):
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📋 کپی لینک اشتراک",
                    copy_text=CopyTextButton(
                        text=sub_url
                    )
                )
            ],
            [
                InlineKeyboardButton(
                    text="⬅️ بازگشت به منو",
                    callback_data="menu:home"
                )
            ]
        ]
    )