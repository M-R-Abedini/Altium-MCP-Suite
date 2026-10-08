<div dir="ltr">

# Altium MCP Suite

[English](../README.md) · [فارسی](README.fa.md) · [简体中文](README.zh-CN.md)

</div>

<div dir="rtl">

مجموعهٔ سرورهای <span dir="ltr"><code>MCP</code></span> برای خواندن و ویرایش شماتیک، برد و کتابخانه‌های آلتیوم. عملیات روی پروژهٔ باز از موتور <span dir="ltr"><code>DelphiScript</code></span> استفاده می‌کند؛ ویرایش فایل‌های کتابخانه با سرور مستقل <span dir="ltr"><code>Rust</code></span> انجام می‌شود.

**پیش‌نیاز:** ویندوز و <span dir="ltr"><code>Python 3.12</code></span>. اتصال زنده روی <span dir="ltr"><code>Altium Designer 26</code></span> آزمایش شده است.

## معماری و قابلیت‌ها

- [سرور اصلی](../eda-agent/README.md): خواندن و ویرایش اجزای شماتیک؛ استخراج نت‌های کامپایل‌شده؛ کار با قطعات، پدها و مس برد؛ ویرایش نماد و فوت‌پرینت. مجوز: <span dir="ltr"><code>Apache-2.0</code></span>.
- [پل قدیمی آلتیوم](../coffeenmusic/README.md): فرمان‌های تکمیلی با تبادل درخواست و پاسخ در فایل. مجوز: <span dir="ltr"><code>MIT</code></span>.
- [سرور فایل کتابخانه](../altium-designer-mcp/README.md): خواندن و نوشتن فایل‌های <span dir="ltr"><code>.SchLib</code></span> و <span dir="ltr"><code>.PcbLib</code></span> در پوشه‌های مجاز. مجوز: <span dir="ltr"><code>GPL-3.0-or-later</code></span>.

مسیریاب آفلاین با الگوریتم <span dir="ltr"><code>Manhattan A*</code></span>، عملیات ترک و ویا تولید می‌کند. محاسبات عرض ترک، امپدانس و بودجهٔ طول برای تصمیم‌گیری اولیه‌اند؛ تأیید یکپارچگی سیگنال یا حل میدان نیستند. ممیزی فوت‌پرینت، هندسهٔ پدها را با مشخصات سازنده که کاربر وارد کرده مقایسه می‌کند.

در مقایسه با اجرای مستقل [نسخه‌های مبنای این سه سرور](../UPSTREAM.json)، این مجموعه موارد زیر را اضافه می‌کند:

- **هماهنگی اسکریپت‌ها:** قفل مشترک، اجرای دو پل پایتون را سری می‌کند؛ هنگام واگذاری موتور به پل قدیمی، پایش پل اصلی متوقف می‌شود.
- **اعتبار پاسخ و بازیابی:** انتشار اتمی درخواست و شناسهٔ اختصاصی، مانع پذیرش پاسخ قدیمی می‌شود. بازیابی پس از بررسی وضعیت ویرایشگر و پل انجام می‌شود؛ فرمان ویرایشی که مهلتش تمام شده خودکار تکرار نمی‌شود.
- **کنترل تغییرات اتصال:** چهار ابزار فقط‌خواندنی زیر، نگاشت پین و پد به نت را مقایسه می‌کنند. نام کامل نت حفظ می‌شود؛ مثلاً <span dir="ltr"><code>/Camera0/RESET</code></span> با <span dir="ltr"><code>/Camera1/RESET</code></span> یکی نیست.

</div>

<div dir="ltr">

| Tool | کنترل |
| --- | --- |
| `design_connectivity_snapshot` | <span dir="rtl">نگاشت مرتب قطعه/پین/نت و هش SHA-256</span> |
| `design_check_pin_contracts` | <span dir="rtl">نت مورد انتظار هر پین یا اتصال‌نداشتن عمدی با ذکر دلیل</span> |
| `design_diff_connectivity` | <span dir="rtl">پین‌های اضافه/حذف‌شده و تغییر تخصیص نت</span> |
| `design_check_schematic_pcb_parity` | <span dir="rtl">پد مفقود یا اضافی و اختلاف نت پین/پد، در هر دو جهت</span> |

</div>

<div dir="rtl">

این کنترل‌ها به مجموعه اضافه شده‌اند و برای اجرا به کی‌کد وابسته نیستند.

## نصب

گیت، پایتون و آلتیوم را روی ویندوز نصب کنید. مسیر فایل اجرایی را مطابق نصب خودتان تغییر دهید:

</div>

<div dir="ltr">

```powershell
git clone https://github.com/M-R-Abedini/Altium-MCP-Suite.git
Set-Location Altium-MCP-Suite
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe setup.py --altium-exe "C:\Program Files\Altium\AD26\X2.EXE"
```

</div>

<div dir="rtl">

ورودی‌های تولیدشده در <span dir="ltr"><code>mcp.local.json</code></span> یا <span dir="ltr"><code>codex.local.toml</code></span> را به تنظیمات کلاینت اضافه و اتصال‌ها را دوباره راه‌اندازی کنید. پروژه را در آلتیوم باز کنید و با <span dir="ltr"><code>app_context</code></span> سند فعال و وضعیت پل را بررسی کنید.

برای کاهش حجم معرفی ابزارها، آرگومان‌های <span dir="ltr"><code>"--toolset", "minimal"</code></span> را به ورودی سرور اصلی اضافه کنید. دسترسی به همهٔ ابزارها از طریق <span dir="ltr"><code>tool_catalog</code></span> و <span dir="ltr"><code>tool_invoke</code></span> باقی می‌ماند.

سرور اختیاری کتابخانه به نسخهٔ پین‌شدهٔ راست و ابزارهای ساخت <span dir="ltr"><code>Visual Studio C++</code></span> نیاز دارد. پوشهٔ کتابخانه باید موجود باشد؛ برای چند پوشه، گزینهٔ <span dir="ltr"><code>--library-dir</code></span> را تکرار کنید:

</div>

<div dir="ltr">

```powershell
Push-Location altium-designer-mcp
cargo build --release --locked
Pop-Location
.\.venv\Scripts\python.exe setup.py --altium-exe "C:\Program Files\Altium\AD26\X2.EXE" --library-dir "D:\MyProject\Libraries"
```

</div>

<div dir="rtl">

## محدودهٔ اعتبار بررسی‌ها

نت‌های شماتیک را پس از کامپایل مجدد، بدون فیلتر، همراه با پدهای بردِ همان نسخهٔ پروژه دریافت کنید. مقدار <span dir="ltr"><code>complete=true</code></span> اعلام پوشش کامل از طرف فراخواننده است؛ ابزار نمی‌تواند رکوردهای حذف‌شده از منبع را تشخیص دهد. داده‌ای که ناقص اعلام شده، تأیید نمی‌شود. [قالب ورودی و مثال‌ها](../CONNECTIVITY_VERIFICATION.md).

تطابق نت پین و پد، پیوستگی مس، فاصله‌های مجاز، اختلاف طول زوج تفاضلی، جهت فوت‌پرینت یا نتیجهٔ <span dir="ltr"><code>ERC/DRC</code></span> را تأیید نمی‌کند. پیش از خروجی ساخت، بررسی‌های بومی آلتیوم را اجرا و نتایج را بازبینی کنید. پس از پایان مهلت فرمان ویرایش، پیش از تکرار، سند را بررسی کنید؛ ممکن است تغییر اعمال شده باشد. پنجرهٔ محاوره‌ای باز می‌تواند اجرای پل را متوقف کند.

## آزمون و نگهداری

</div>

<div dir="ltr">

```powershell
.\.venv\Scripts\python.exe -m pytest tests eda-agent/tests/design/test_connectivity_contracts.py -q
.\.venv\Scripts\python.exe tests/smoke_stdio.py
```

</div>

<div dir="rtl">

آزمون ارتباط، راه‌اندازی سرورها و چهار ابزار اتصال را با دادهٔ مصنوعی بررسی می‌کند؛ آزمون‌های خودکار همهٔ فرمان‌ها را روی آلتیوم زنده اعتبارسنجی نمی‌کنند. [محدودهٔ آزمون](../REVIEW.md)، [تغییرات مجموعه](../MODIFICATIONS.md) و [گردش‌کار آزمون](../.github/workflows/tests.yml). در گزارش خطا، نسخهٔ آلتیوم، نام ابزار، مراحل بازتولید و خطای برگشتی را بنویسید.

کد هماهنگی و ماژول‌های اتصال جدید با مجوز [MIT](../LICENSE) منتشر شده‌اند. مجوز و نام پدیدآورندگان هر پروژه محفوظ است؛ [منشأ کد](../UPSTREAM.json).

</div>
