import os

from dotenv import load_dotenv
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CopyTextButton


load_dotenv()

CHANNEL_URL = os.getenv("CHANNEL_URL")


def main_menu_keyboard():
    keyboard = [
        [
            InlineKeyboardButton(
                text="🔗 دریافت لینک اشتراک",
                callback_data="menu:sub"
            )
        ],
        [
            InlineKeyboardButton(
                text="📊 وضعیت اشتراک",
                callback_data="menu:status"
            ),
            InlineKeyboardButton(
                text="🛡️ حریم خصوصی",
                callback_data="menu:privacy"
            )
        ],
        [
            InlineKeyboardButton(
                text="📘 راهنما",
                callback_data="menu:help"
            )
        ]
    ]

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


def subscription_keyboard(sub_url):
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📋  کپی لینک اشتراک",
                    copy_text= CopyTextButton(
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