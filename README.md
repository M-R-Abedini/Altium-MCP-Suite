# Altium MCP Suite

مجموعهٔ سورس سه MCP برای Altium Designer، به‌همراه هماهنگ‌کنندهٔ اجرای اسکریپت‌ها، بازیابی اتصال و آزمون‌های اصلاحات. این مخزن snapshot قابل‌ساخت از پروژه‌های اصلی است؛ نویسندگان و مجوزهای اصلی در هر زیرپوشه حفظ شده‌اند.

| بخش | کاربرد | مجوز اصلی |
| --- | --- | --- |
| [eda-agent](eda-agent/README.md) | کنترل شماتیک، PCB و کتابخانه در Altium؛ ورودی هماهنگ‌شده: `eda_stdio.py` | Apache-2.0 |
| [coffeenmusic](coffeenmusic/README.md) | ابزارهای پل قدیمی Altium؛ ورودی: `coffeenmusic/server/codex_stdio.py` | MIT |
| [altium-designer-mcp](altium-designer-mcp/README.md) | خواندن و نوشتن فایل‌های کتابخانهٔ Altium با سرور Rust | GPL-3.0-or-later |

منبع و commit هر snapshot در [UPSTREAM.json](UPSTREAM.json) ثبت شده‌اند. کدهای هماهنگ‌کنندهٔ جدید تحت [MIT](LICENSE) هستند؛ این مجوز جایگزین مجوز زیرپروژه‌ها نیست. [یادداشت تغییرات](MODIFICATIONS.md) و [گزارش بررسی](REVIEW.md) محدوده و نتیجهٔ کنترل‌ها را توضیح می‌دهند.

## نصب در Windows

Python 3.12، Git و Altium Designer لازم‌اند. کنترل زنده روی AD26 آزمایش شده است. دستورها را از ریشهٔ همین مخزن اجرا کنید:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe setup.py --altium-exe "C:\Program Files\Altium\AD26\X2.EXE" --library-dir "D:\MyProject\Libraries"
```

مسیرها را برای رایانهٔ خود تنظیم کنید. `--library-dir` اختیاری است و می‌تواند چند بار تکرار شود. خروجی‌های `mcp.local.json` و `codex.local.toml` تنظیمات آمادهٔ کلاینت هستند؛ بخش‌های آن‌ها را در تنظیمات کلاینت خود ادغام و سرورهای MCP را یک بار دوباره راه‌اندازی کنید. نصب‌کننده تنظیمات فعلی کلاینت را بازنویسی نمی‌کند. سرورهای قدیمی همان مجموعه را هم‌زمان با این ورودی‌ها فعال نگه ندارید.

برای ساخت سرور کتابخانه، Rust 1.95.0 و ابزار C++ ویژوال استودیو روی Windows لازم‌اند:

```powershell
Set-Location altium-designer-mcp
cargo build --release --locked
```

فایل ساخته‌شده در `altium-designer-mcp/target/release/altium-designer-mcp.exe` قرار می‌گیرد. سورس کامل Rust و `Cargo.lock` در مخزن هستند؛ محیط Python و باینری‌های نصب محلی در Git نگهداری نمی‌شوند.

## رفتار اتصال

Altium و پروژهٔ موردنظر را باز کنید. برای عملیات زنده، ابتدا `app_context` را بخوانید تا نوع سند فعال و نسخهٔ اسکریپت مشخص شود. در صورت توقف پل، درخواست بعدی آن را پیش از ارسال فرمان بازیابی می‌کند. هماهنگ‌کننده، موتور مشترک اسکریپت را میان دو پل Python نوبتی در اختیارشان می‌گذارد. ابزار کتابخانهٔ Rust مستقل است.

X پنجرهٔ وضعیت فقط آن را مخفی می‌کند؛ Detach توقف صریح است. keepalive پل متوقف‌شده را پشت صحنه راه‌اندازی نمی‌کند. اگر پنجرهٔ modal باز باشد، خطای مشخص گزارش می‌شود؛ پس از تکمیل یا بستن آن درخواست را تکرار کنید. فرمان ویرایشِ timeoutشده خودکار تکرار نمی‌شود.

فایل‌های runtime به‌صورت پیش‌فرض در `%LOCALAPPDATA%/AltiumMCPSuite` ساخته می‌شوند. اسکریپت‌ها در مسیر دارای هش محتوا کپی می‌شوند تا cache اسکریپت Altium نسخهٔ قدیمی را نگه ندارد. مسیر تبادل پل قدیمی نیز در همین فضای کاربری است. `ALTIUM_EXE`، `ALTIUM_MCP_RUNTIME` و `EDA_AGENT_WORKSPACE` برای نصب سفارشی قابل تنظیم‌اند. برای چند نسخهٔ نصب‌شدهٔ Altium، مسیر executable را صریح مشخص کنید.

## آزمون‌ها

چهار ابزار جدید کنترل اتصال، snapshot نت‌لیست، قرارداد هر پایه، مقایسهٔ تغییرات و تطبیق دوسویهٔ شماتیک/پد PCB را بدون وابستگی KiCad انجام می‌دهند. نام کامل نت‌های سلسله‌مراتبی حفظ می‌شود و دادهٔ ناقص نتیجهٔ قبول نمی‌گیرد. [راهنما و نمونهٔ ورودی](CONNECTIVITY_VERIFICATION.md).

```powershell
.\.venv\Scripts\python.exe -m pytest tests -q
.\.venv\Scripts\python.exe -m pytest eda-agent/tests/design/test_connectivity_contracts.py -q
.\.venv\Scripts\python.exe tests/smoke_stdio.py
Set-Location eda-agent
..\.venv\Scripts\python.exe -m pytest tests/test_bridge.py tests/test_recovery.py tests/test_workspace_pointer_isolation.py tests/test_timeout_looks_for_a_dialog.py tests/test_units.py tests/test_websocket_framing.py -q
```

آزمون `tests/smoke_stdio.py --live` اختیاری است و پل Altium باز را برای کنترل بازیابی متوقف و دوباره راه‌اندازی می‌کند؛ پس از پایان عملیات جاری اجرا شود. تست‌های کامل upstream نیز همراه سورس هستند؛ برخی به پنجرهٔ واقعی، دادهٔ fixture یا ابزارهای دیگر نیاز دارند.

پروژهٔ سخت‌افزار مرتبط: [RV1106G3 Header Board Robotics](https://github.com/M-R-Abedini/RV1106G3_Header_Board_Robotics).
