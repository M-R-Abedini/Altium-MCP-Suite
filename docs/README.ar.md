<div dir="ltr">

# Altium MCP Suite

[English](../README.md) · [فارسی](README.fa.md) · [简体中文](README.zh-CN.md) · [العربية](README.ar.md)

</div>

<div dir="rtl">

خوادم <span dir="ltr"><code>MCP</code></span> تتيح للذكاء الاصطناعي قراءة وتحرير مخططاتك ولوحاتك ومكتباتك في <span dir="ltr"><code>Altium</code></span>. يبقى <span dir="ltr"><code>Altium</code></span> مفتوحًا، ويعمل الذكاء الاصطناعي مباشرة على المشروع المفتوح.

**المتطلبات:** <span dir="ltr"><code>Windows</code></span> و <span dir="ltr"><code>Python 3.12</code></span>. تم الاختبار المباشر على <span dir="ltr"><code>Altium Designer 26</code></span>.

## ماذا يستطيع أن يفعل

- قراءة وتحرير المخطط المفتوح: إضافة المكونات، رسم الأسلاك، وضع لافتات الشبكات ومنافذ الطاقة، تحرير المعاملات، استخراج <span dir="ltr"><code>BOM</code></span> وقائمة الشبكات.
- العمل على اللوحة: تحريك المكونات، رسم المسارات والوصلات، فحص مسافات التوضيع، التجميع في لوحة واحدة.
- فحص التصميم: لافتات شبكات يتيمة، منافذ عائمة، أرجل غير موصولة في الدوائر المتكاملة، مكثفات فصل مفقودة، تعارض التسميات، مكونات خارج الشبكة — أو فحص <span dir="ltr"><code>lint</code></span> واحد من 31 نقطة على المخطط واللوحة.
- أربعة أدوات للقراءة فقط لفحص التوصيل: لقطة لربط الأرجل بالشبكات (ببصمة <span dir="ltr"><code>SHA-256</code></span>)، وتعريف عقد لكل رجل (الشبكة المتوقعة، أو عدم توصيل مقصود مع ذكر السبب)، ومقارنة قبل/بعد، والتحقق باتجاهين من تطابق وسادات المخطط واللوحة.
- حساب عرض المسار والمعاوقة وميزانية الأطوال. هذه حاسبات، ليست إثبات سلامة إشارة.
- قراءة وكتابة ملفات المكتبات <span dir="ltr"><code>.SchLib</code></span> و <span dir="ltr"><code>.PcbLib</code></span>.
- جسران ومحرك سكربت واحد: قفل مشترك يمنع جسري بايثون من التنازع على محرك سكربت <span dir="ltr"><code>Altium</code></span>، وكل طلب يحمل معرّفًا حتى لا يُقبَل رد متأخر على طلب قديم.

## ما زال قيد التطوير

- المساعدة في التوضيع: التوضيع الدفعي وفحص المسافات يعملان؛ أدوات التوضيع الأدق ما زالت قيد الإنجاز.
- استقرار الجسر: كل تعطل معروف في <span dir="ltr"><code>DelphiScript</code></span> يُصلَح أو يُحتوى، لكن أعطالًا جديدة ما زالت تظهر — والعمل مستمر.
- الاختبار تم حتى الآن على <span dir="ltr"><code>Altium Designer 26</code></span> فقط؛ النسخ الأخرى لم تُختبَر بعد.

## القيود الحالية

- قد يترك فشل التعديل الجماعي تغييرات جزئية؛ تجميع العمليات في خطوة تراجع واحدة لا يضمن استعادتها بالكامل. احفظ نسخة احتياطية قبل التعديلات الكبيرة.

- تجريبي. بعض العمليات قد تُعطّل محرك <span dir="ltr"><code>DelphiScript</code></span> في <span dir="ltr"><code>Altium</code></span> وتوقف حلقة العمل — خذ نسخة احتياطية من تصميمك قبل أن تدع الذكاء الاصطناعي يحرّره.
- قد ينتظر الحفظ وأوامر الواجهة أثناء تنفيذ أمر. تتحرر ملكية المحرك بعد نحو ثانيتين من الخمول؛ رسائل ping الخلفية لا تمدد المهلة، ويعيد استدعاء الأداة التالي تشغيل الحلقة تلقائيًا.
- تُرفض الأحرف فوق <span dir="ltr"><code>U+00FF</code></span> في معاملات JSON لجسر EDA قبل الإرسال لمنع استبدالها بعلامة استفهام؛ نقل Unicode الكامل لم يُنفّذ بعد.
- مسارات الشبكة يجب أن تكون أقراصًا معيّنة؛ مسارات <span dir="ltr"><code>UNC</code></span> لا تُفتَح.
- يتطلب <span dir="ltr"><code>proj_sync_pcb</code></span> الخيار <span dir="ltr"><code>allow_modal=True</code></span>؛ تحديث اللوحة من المخطط ما زال يفتح حوارًا تفاعليًا.
- أدوات فحص التوصيل تتحقق من تخصيص الأرجل فقط، لا من النحاس. ولا ترى بيانات لم تُدخَل إليها.
- الحوار المفتوح يحجب أوامر السكربت؛ تشخيص الحوارات عبر <span dir="ltr"><code>Win32</code></span> يبقى متاحًا. تُرفض الأوامر الجديدة أثناء معالجة أمر سابق. عند انتهاء المهلة يُسحب الطلب غير المقروء؛ التحرير الجاري لا يمكن إلغاؤه، لذا افحص التصميم قبل إعادة المحاولة.

## التثبيت

راجع [فحوص الاتصال والاستعادة اليدوية](review/TRANSPORT_FINDINGS_2026-10-08.md) لمعالجة ملكية السكربت غير المؤكدة بعد انتهاء المهلة أو تعطل المحرك.

ثبّت <span dir="ltr"><code>Git</code></span> و <span dir="ltr"><code>Python 3.12</code></span> و <span dir="ltr"><code>Altium</code></span> على ويندوز. ضع مسار ملف التنفيذ الخاص بك:

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

ادمج المدخلات المولّدة في <span dir="ltr"><code>mcp.local.json</code></span> أو <span dir="ltr"><code>codex.local.toml</code></span> في إعدادات عميل <span dir="ltr"><code>MCP</code></span> وأعد تشغيل الاتصالات. افتح مشروعك في <span dir="ltr"><code>Altium</code></span> واستدعِ <span dir="ltr"><code>app_context</code></span> للتحقق من حالة الجسر.

لتقليل حجم قائمة الأدوات، أضف <span dir="ltr"><code>--toolset minimal</code></span> عند التثبيت أو تشغيل الخادم الرئيسي. اكتشف الأداة عبر <span dir="ltr"><code>tool_catalog</code></span> مع <span dir="ltr"><code>with_schema=True</code></span> ثم شغّلها عبر <span dir="ltr"><code>tool_invoke</code></span>. تبقى جميع العمليات متاحة؛ تُحمّل تعريفات المعاملات عند الحاجة وتُتحقق وفق قواعد الوضع الكامل. راجع [نتائج القياس](review/PERFORMANCE_2026-10-08.md).

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

خادم المكتبات اختياري (يحتاج نسخة <span dir="ltr"><code>Rust</code></span> المثبتة وأدوات بناء <span dir="ltr"><code>Visual Studio C++</code></span>). للمجلدات المتعددة كرر الخيار <span dir="ltr"><code>--library-dir</code></span>.

## الأسئلة والمساهمة

اطرح أسئلة التثبيت والاستخدام في [قسم الأسئلة والأجوبة](https://github.com/M-R-Abedini/Altium-MCP-Suite/discussions/categories/q-a). استخدم [نماذج Issues](https://github.com/M-R-Abedini/Altium-MCP-Suite/issues/new/choose) للإبلاغ عن الأخطاء واقتراح الميزات. يوضح [دليل المساهمة بالإنجليزية](../CONTRIBUTING.md) إعداد بيئة التطوير والاختبارات والترجمة وتسجيل المؤلفين المشاركين.

## الترخيص

كود المجموعة الجديد بترخيص <span dir="ltr"><code>MIT</code></span>. المشاريع المضمّنة تحتفظ بتراخيصها — انظر <span dir="ltr"><code>UPSTREAM.json</code></span>.

</div>
