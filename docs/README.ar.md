<div dir="ltr">

# Altium MCP Suite

[English](../README.md) · [فارسی](README.fa.md) · [简体中文](README.zh-CN.md) · [العربية](README.ar.md)

</div>

<div dir="rtl">

اتصال <span dir="ltr"><code>MCP</code></span> واحد باسم <span dir="ltr"><code>mcp altium suite</code></span> يتيح للذكاء الاصطناعي قراءة وتحرير مخططاتك ولوحاتك ومكتباتك في <span dir="ltr"><code>Altium</code></span>. يبقى البرنامج مفتوحًا وتستهدف العمليات المشروع المحدد. تعمل محركات التصميم والعمليات القديمة والمكتبات داخل الاتصال نفسه مع التنسيق المشترك.

**المتطلبات:** <span dir="ltr"><code>Windows</code></span> و <span dir="ltr"><code>Python 3.12</code></span>. تم الاختبار المباشر على <span dir="ltr"><code>Altium Designer 26</code></span>.

## ماذا يستطيع أن يفعل

- قراءة وتحرير المخطط المفتوح: إضافة المكونات، رسم الأسلاك، وضع لافتات الشبكات ومنافذ الطاقة، تحرير المعاملات، استخراج <span dir="ltr"><code>BOM</code></span> وقائمة الشبكات.
- العمل على اللوحة: تحريك المكونات، رسم المسارات والوصلات، فحص مسافات التوضيع، التجميع في لوحة واحدة.
- فحص التصميم: لافتات شبكات يتيمة، منافذ عائمة، أرجل غير موصولة في الدوائر المتكاملة، مكثفات فصل مفقودة، تعارض التسميات، مكونات خارج الشبكة — أو 33 فحصًا في استدعاء واحد، مع <span dir="ltr"><code>DRC</code></span> اختياري.
- أربعة أدوات للقراءة فقط لفحص التوصيل: لقطة لربط الأرجل بالشبكات (ببصمة <span dir="ltr"><code>SHA-256</code></span>)، وتعريف عقد لكل رجل (الشبكة المتوقعة، أو عدم توصيل مقصود مع ذكر السبب)، ومقارنة قبل/بعد، والتحقق باتجاهين من تطابق وسادات المخطط واللوحة.
- حساب عرض المسار والمعاوقة وميزانية الأطوال. هذه حاسبات، ليست إثبات سلامة إشارة.
- قراءة وكتابة ملفات المكتبات <span dir="ltr"><code>.SchLib</code></span> و <span dir="ltr"><code>.PcbLib</code></span>.
- البحث في فهرس <span dir="ltr"><code>ChilaBoard</code></span> العام وتنزيل المكتبات مع الإصدار والمصدر والبصمات والترخيص؛ وتجهيز ملفات التنزيل الرسمية من <span dir="ltr"><code>SnapMagic</code></span> و <span dir="ltr"><code>SamacSys</code></span> للفحص المحلي. الربط المباشر بواجهتيهما وتحويل <span dir="ltr"><code>EPW</code></span> غير منفذين. [دليل المكتبات السحابية](review/CLOUD_LIBRARIES_2026-10-27.md).
- مهارة <span dir="ltr"><code>$altium-circuit-design</code></span> لمتطلبات الدائرة ومراجعة البيانات والرموز والبصمات والتخطيط المنظم والتحقق من التوصيل. نجاح التنزيل لا يثبت صحة ترقيم الأرجل أو الحزمة.
- إدارة بدائل التجميع بمعرّف فريد، وتحديث حالة تركيب المكونات دفعيًا، وتصدير مصفوفة المكونات؛ مع نسخ احتياطي للمشروع والتحقق عبر واجهة <span dir="ltr"><code>Altium</code></span>. [سير عمل البدائل](review/STORAGE_VARIANTS_2026-10-08.md).
- جسران ومحرك سكربت واحد: قفل مشترك يمنع جسري بايثون من التنازع على محرك سكربت <span dir="ltr"><code>Altium</code></span>، وكل طلب يحمل معرّفًا حتى لا يُقبَل رد متأخر على طلب قديم.
- عملاء متزامنون: يرتّب طابور مشترك أوامر Altium، وتُقرأ المكونات والشبكات والوسادات في مرور واحد. يمكن للعملاء تحليل اللقطة بالتوازي؛ اللقطة سجل سابق وليست شرطًا صالحًا للتعديل الحي. [الاستخدام والتحقق](review/CONCURRENT_CLIENTS_2026-10-14.md).
- أسماء مشاريع السكربت مختلفة للجسرين. عند بدء جسر <span dir="ltr"><code>EDA</code></span>، تُغلق المشاريع القديمة المولّدة بعد التحقق، مع الاحتفاظ بالمصادر المعدّلة. [التحقق من دورة حياة السكربتات](review/SCRIPT_PROJECT_LIFECYCLE_2026-10-10.md).

## ما زال قيد التطوير

- المساعدة في التوضيع: التوضيع الدفعي وفحص المسافات يعملان؛ أدوات التوضيع الأدق ما زالت قيد الإنجاز.
- استقرار الجسر: كل تعطل معروف في <span dir="ltr"><code>DelphiScript</code></span> يُصلَح أو يُحتوى، لكن أعطالًا جديدة ما زالت تظهر — والعمل مستمر.
- الاختبار تم حتى الآن على <span dir="ltr"><code>Altium Designer 26</code></span> فقط؛ النسخ الأخرى لم تُختبَر بعد.

## القيود الحالية

- قد يترك فشل التعديل الجماعي تغييرات جزئية؛ تجميع العمليات في خطوة تراجع واحدة لا يضمن استعادتها بالكامل. احفظ نسخة احتياطية قبل التعديلات الكبيرة.

- تجريبي. بعض العمليات قد تُعطّل محرك <span dir="ltr"><code>DelphiScript</code></span> في <span dir="ltr"><code>Altium</code></span> وتوقف حلقة العمل — خذ نسخة احتياطية من تصميمك قبل أن تدع الذكاء الاصطناعي يحرّره.
- قد ينتظر الحفظ وأوامر الواجهة أثناء تنفيذ أمر. تتحرر ملكية المحرك بعد نحو ثانيتين من الخمول؛ رسائل ping الخلفية لا تمدد المهلة، ويعيد استدعاء الأداة التالي تشغيل الحلقة تلقائيًا.
- تُرفض الأحرف فوق <span dir="ltr"><code>U+00FF</code></span> في معاملات JSON لجسر EDA قبل الإرسال لمنع استبدالها بعلامة استفهام؛ نقل Unicode الكامل لم يُنفّذ بعد.
- يحافظ جسر <span dir="ltr"><code>EDA</code></span> على مسارات <span dir="ltr"><code>UNC</code></span>؛ الوصول إلى المجلد المشترك يعتمد على صلاحيات ويندوز.
- يتطلب <span dir="ltr"><code>proj_sync_pcb</code></span> الخيار <span dir="ltr"><code>allow_modal=True</code></span>؛ تحديث اللوحة من المخطط ما زال يفتح حوارًا تفاعليًا.
- أدوات فحص التوصيل تتحقق من تخصيص الأرجل فقط، لا من النحاس. ولا ترى بيانات لم تُدخَل إليها.
- الحوار المفتوح يحجب أوامر السكربت؛ تشخيص الحوارات عبر <span dir="ltr"><code>Win32</code></span> يبقى متاحًا. تُرفض الأوامر الجديدة أثناء معالجة أمر سابق. عند انتهاء المهلة يُسحب الطلب غير المقروء؛ التحرير الجاري لا يمكن إلغاؤه، لذا افحص التصميم قبل إعادة المحاولة.

## التثبيت

### إضافة Codex

تجمع الإضافة اتصال <span dir="ltr"><code>MCP</code></span> الموحد ومهارة تصميم الدوائر وسير إعداد البيئة. يوضح [دليل الإضافة](PLUGIN_PACKAGE.md) أوامر البناء الفعلية والتثبيت من marketplace محلي وتشخيص البيئة. بعد التثبيت افتح محادثة جديدة وأكمل الإعداد. لا تشمل الإضافة برنامج <span dir="ltr"><code>Altium</code></span> أو ترخيصه. لا تُبقِ الاتصال اليدوي واتصال الإضافة نفسها مفعّلين معًا.

### الاتصال اليدوي والعملاء الآخرون

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

ينشئ الإعداد مدخلاً واحدًا <span dir="ltr"><code>mcp-altium-suite</code></span> في <span dir="ltr"><code>mcp.local.json</code></span> أو <span dir="ltr"><code>codex.local.toml</code></span>. ادمجه في إعدادات العميل وأعد الاتصال. تبقى إعدادات المحركات الخاصة في <span dir="ltr"><code>suite-backends.local.json</code></span>. افتح مشروعك، واكتشف <span dir="ltr"><code>app_context</code></span> بواسطة <span dir="ltr"><code>suite_catalog</code></span> ثم استدعِ الاسم <span dir="ltr"><code>eda__app_context</code></span> عبر <span dir="ltr"><code>suite_invoke</code></span>.

الوضع الافتراضي <span dir="ltr"><code>--toolset minimal</code></span> يعرض فقط <span dir="ltr"><code>suite_catalog</code></span> و <span dir="ltr"><code>suite_invoke</code></span> و <span dir="ltr"><code>suite_status</code></span>. اكتشف المخطط باستخدام <span dir="ltr"><code>with_schema=true</code></span>؛ تبدأ الأسماء بـ <span dir="ltr"><code>eda__</code></span> أو <span dir="ltr"><code>legacy__</code></span> أو <span dir="ltr"><code>libraries__</code></span> أو <span dir="ltr"><code>cloud__</code></span>. تبقى العمليات متاحة وتُتحقق المدخلات قبل الإرسال. يعرض <span dir="ltr"><code>--toolset full</code></span> جميع الأدوات، ويقبل <span dir="ltr"><code>--separate-servers</code></span> للترحيل مع اتصال موحد. [الاتصال الموحد والترحيل](review/UNIFIED_SUITE_2026-10-27.md).

</div>

<div dir="ltr">

```powershell
.\.venv\Scripts\python.exe setup.py --altium-exe "C:\Program Files\Altium\AD26\X2.EXE" --library-dir "D:\MyProject\Libraries"
```

</div>

<div dir="rtl">

تعمل أدوات المكتبات عبر شيفرة Python مستقلة دون مترجم أو ملف تنفيذي خارجي. كرر `--library-dir` للمجلدات المطلوبة. تراخيص Altium وحسابات مزودي المكتبات غير مشمولة.

## الأسئلة والمساهمة

اطرح أسئلة التثبيت والاستخدام في [قسم الأسئلة والأجوبة](https://github.com/M-R-Abedini/Altium-MCP-Suite/discussions/categories/q-a). استخدم [نماذج Issues](https://github.com/M-R-Abedini/Altium-MCP-Suite/issues/new/choose) للإبلاغ عن الأخطاء واقتراح الميزات. يوضح [دليل المساهمة بالإنجليزية](../CONTRIBUTING.md) إعداد بيئة التطوير والاختبارات والترجمة وتسجيل المؤلفين المشاركين.

## الترخيص

شيفرة التنسيق والمكتبات والتوافق القديم بترخيص MIT؛ خلفية EDA بترخيص Apache-2.0. راجع [نطاق التراخيص](../THIRD_PARTY_NOTICES.md) و[المصادر الأصلية](../UPSTREAM.json).

</div>
