from datetime import timezone
from _zoneinfo import ZoneInfo

import jdatetime

TEHRAN_TZ= ZoneInfo("Asia/Tehran")

PERSION_DIGITS_TABLE = str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")

def to_persion_digits(value):
    return str(value).translate(PERSION_DIGITS_TABLE)

def format_jalali_datetime(dt):
    if not dt:
        return "-"
    
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)

    local_dt= dt.astimezone(TEHRAN_TZ)
    jalali_dt = jdatetime.datetime.fromgregorian(datetime=local_dt)

    text=(
        f"{jalali_dt.year:04d}/"
        f"{jalali_dt.month:02d}/"
        f"{jalali_dt.day:02d}"
        "، ساعت  "
        f"{jalali_dt.hour:02d}:"
        f"{jalali_dt.minute:02d}"
    )
    return to_persion_digits(text)