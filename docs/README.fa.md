<div dir="ltr">

# Altium MCP Suite

[English](../README.md) · [فارسی](README.fa.md) · [简体中文](README.zh-CN.md)

</div>

<div dir="rtl">

سرورهای <span dir="ltr"><code>MCP</code></span> که به هوش مصنوعی اجازه می‌دهند شماتیک، برد و کتابخانه‌های آلتیوم را بخواند و ویرایش کند. ویرایش زنده از موتور <span dir="ltr"><code>DelphiScript</code></span> آلتیوم روی پروژه‌ای که باز است انجام می‌شود؛ کار با فایل‌های کتابخانه با یک سرور جداگانهٔ <span dir="ltr"><code>Rust</code></span> است.

**پیش‌نیاز:** ویندوز و <span dir="ltr"><code>Python 3.12</code></span>. اتصال زنده روی <span dir="ltr"><code>Altium Designer 26</code></span> آزمایش شده است.

## چه کارهایی می‌کند

- **شماتیک:** اضافه کردن قطعه، سیم‌کشی، گذاشتن لیبل نت و پورت تغذیه روی شماتیک باز؛ ویرایش پارامترها؛ اجرای انوتیشن و سینک <span dir="ltr"><code>ECO</code></span> بدون پنجره‌های مزاحم.
- **برد:** جابه‌جایی قطعات، کشیدن ترک و ویا، پنلایز. یک مسیریاب آفلاین <span dir="ltr"><code>Manhattan A*</code></span> عملیات ترک و ویا تولید می‌کند.
- **ممیزی:** لیبل نتِ بی‌صاحب، پورت شناور، پینِ وصل‌نشدهٔ آی‌سی، خازن دی‌کاپلینگِ جاافتاده، تداخل دیزاینیتور، قطعهٔ خارج از گرید؛ در مجموع یک جاروی <span dir="ltr"><code>lint</code></span> با ۳۱ کنترل روی شماتیک و برد.
- **محاسبه:** عرض ترک، امپدانس و بودجهٔ طول برای تصمیم‌گیری در طراحی. این‌ها ماشین‌حساب‌اند نه اثبات؛ جایگزین حل‌گر میدان نیستند.
- **کتابخانه:** خواندن و نوشتن فایل‌های <span dir="ltr"><code>.SchLib</code></span> و <span dir="ltr"><code>.PcbLib</code></span> داخل پوشه‌های مجاز.

## سرورها

</div>

<div dir="ltr">

| Server | Scope | License |
| --- | --- | --- |
| `eda-agent` | <span dir="rtl">اجزای شماتیک و نت‌های کامپایل‌شده؛ قطعات، پدها و مس برد؛ ویرایش نماد و فوت‌پرینت</span> | `Apache-2.0` |
| `coffeenmusic/altium-mcp` | <span dir="rtl">فرمان‌های تکمیلی آلتیوم با پل درخواست/پاسخ فایل‌محور</span> | `MIT` |
| `altium-designer-mcp` | <span dir="rtl">دسترسی فایل به <code dir="ltr">.SchLib</code> و <code dir="ltr">.PcbLib</code> در پوشه‌های مجاز</span> | `GPL-3.0-or-later` |

</div>

<div dir="rtl">

## این مجموعه چه اضافه می‌کند

کد جدید نسبت به [اسنپ‌شات‌های ثبت‌شدهٔ نسخه‌های اصلی](../UPSTREAM.json) مقایسه شده است:

۱. **یک موتور اسکریپت، دو پل.** یک قفل مشترک دو پل پایتون را سری می‌کند: وقتی پل قدیمی مالک موتور اسکریپت آلتیوم است، پایش پل اصلی متوقف می‌شود و بعد پس داده می‌شود. در نتیجه، هرگز هم‌زمان سراغ موتور اسکریپت نمی‌روند.
۲. **نه نتیجهٔ کهنه، نه ویرایش تکراری.** درخواست‌ها اتمی منتشر می‌شوند و هر کدام شناسه‌ای دارند که پاسخ باید همان را برگرداند؛ پس پاسخ دیررسیدهٔ یک درخواست قدیمی هرگز پذیرفته نمی‌شود. فرمانی که مهلتش تمام شده خودکار تکرار نمی‌شود — ممکن است همان تلاش اول اثر کرده باشد.
۳. **کنترل رگرسیون الکتریکی — چهار ابزار فقط‌خواندنی.** اسنپ‌شات استاندارد نگاشت پین به نت با دایجست <span dir="ltr"><code>SHA-256</code></span>؛ قرارداد هر پین (نت مورد انتظار، یا وصل‌نبودن عمدی با ذکر دلیل)؛ مقایسهٔ قبل و بعد؛ تطابق دوطرفهٔ پدهای شماتیک و برد. نام کامل نت سلسله‌مراتبی حفظ می‌شود: <span dir="ltr"><code>/Camera0/RESET</code></span> و <span dir="ltr"><code>/Camera1/RESET</code></span> دو نت متفاوت‌اند.

</div>

<div dir="ltr">

| Tool | بررسی |
| --- | --- |
| `design_connectivity_snapshot` | <span dir="rtl">نگاشت استاندارد قطعه/پین/نت به‌همراه دایجست</span> |
| `design_check_pin_contracts` | <span dir="rtl">نت مورد انتظار هر پین یا عدم اتصال عمدی با دلیل</span> |
| `design_diff_connectivity` | <span dir="rtl">پین‌های اضافه/حذف‌شده و تغییر تخصیص نت</span> |
| `design_check_schematic_pcb_parity` | <span dir="rtl">پد گم‌شده یا اضافی و مغایرت نت پین/پد، در هر دو جهت</span> |

</div>

<div dir="rtl">

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

اگر می‌خواهید در شروع فقط مجموعهٔ کوچکی از ابزارها معرفی شود، آرگومان‌های <span dir="ltr"><code>"--toolset", "minimal"</code></span> را به ورودی سرور اصلی اضافه کنید. دسترسی به همهٔ ابزارها از طریق <span dir="ltr"><code>tool_catalog</code></span> و <span dir="ltr"><code>tool_invoke</code></span> باقی می‌ماند.

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

نت‌های شماتیک را پس از کامپایل مجدد، بدون فیلتر، همراه با پدهای بردِ همان نسخهٔ پروژه دریافت کنید. مقدار <span dir="ltr"><code>complete=true</code></span> اعلام پوشش کامل از طرف فراخواننده است؛ ابزار نمی‌تواند رکوردهای حذف‌شده از منبع را تشخیص دهد و دادهٔ ناقص هرگز تأیید نمی‌شود. [قالب ورودی و مثال‌ها](../CONNECTIVITY_VERIFICATION.md).

تطابق نت پین و پد، تخصیص پین را بررسی می‌کند نه مس را. دربارهٔ مسیریابی، فاصله‌های مجاز، اختلاف طول زوج تفاضلی، جهت فوت‌پرینت یا نتیجهٔ <span dir="ltr"><code>ERC/DRC</code></span> چیزی نمی‌گوید — پیش از خروجی ساخت، بررسی‌های بومی آلتیوم را اجرا کنید. پس از پایان مهلت فرمان ویرایش، پیش از تکرار، سند را بررسی کنید؛ ممکن است همان تلاش اول اثر کرده باشد. پنجرهٔ محاوره‌ای باز می‌تواند اجرای زنده را متوقف کند.

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
