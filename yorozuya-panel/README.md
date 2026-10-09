<div align="center">
  <img src="static/logo.svg" width="140" alt="YorozuyaPanel logo">
  <h1>🍓 YorozuyaPanel <span>万事屋</span></h1>
  <p><b>سبک‌ترین پنل مدیریت کانفیگ با روح گینتاما</b><br>یوزر • سابسکریپشن • نود • آی‌پی خروجی و کشور • آماده Render</p>
  <p>
    <img alt="Python" src="https://img.shields.io/badge/Python-3.12-3776AB?style=flat-square&logo=python&logoColor=white">
    <img alt="FastAPI" src="https://img.shields.io/badge/FastAPI-009688?style=flat-square&logo=fastapi&logoColor=white">
    <img alt="Gintama" src="https://img.shields.io/badge/Theme-Gintama🍓-e63946?style=flat-square">
  </p>
</div>

> 📁 این پروژه داخل پوشه `yorozuya-panel/` ریپوی Onvo زندگی می‌کنه.
> اگه یه روز خواستی جداش کنی، کافیه همین پوشه رو به یه ریپوی مستقل منتقل کنی —
> اون‌وقت فایل `render.yaml` هم به‌صورت خودکار (Blueprint) کار می‌کنه.

---

## ✨ چی داره؟

| قابلیت | توضیح |
|---|---|
| 👥 کاربران | ساخت/ویرایش/حذف، سقف ترافیک، انقضای خودکار، فعال/غیرفعال |
| 🔗 سابسکریپشن | لینک ساب base64 + صفحه اشتراک `/p/<key>` + QR + دکمه ایمپورت اپ‌ها |
| 🛰 نودها | vless / vmess / trojan / ss — با چک سلامت TCP + پینگ |
| 🌍 آی‌پی و کشور | آی‌پی خروجی سرور + کشور و پرچم، resolve و کشور هر نود |
| 🔎 ابزار IP | بزن ببین هر آی‌پی/دامنه مال کجای دنیاست |
| 💾 بکاپ | دانلود/ریستور JSON با یه کلیک |
| 🍓 تم گینتاما | تابلوی چوبی 万事屋، شیر توت‌فرنگی، و جمله‌های بامزه |

**سبک یعنی سبک:** یه فایل `main.py`، بدون دیتابیس (JSON)، فقط ۶ تا dependency.

## 🚀 دیپلوی روی Render (۲ دقیقه)

چون پروژه توی زیرپوشه‌ست، Blueprint خودکار (`render.yaml`) کار نمی‌کنه — دستی بساز:

1. توی [Render Dashboard](https://dashboard.render.com) → **New → Web Service** → ریپوی Onvo رو انتخاب کن.
2. **Root Directory** رو بذار: `yorozuya-panel`
3. Runtime می‌شه **Docker** (خودکار از `Dockerfile` همین پوشه).
4. Health Check Path: `/healthz` — پلن: Free.
5. Environment → این دو تا رو اضافه کن:
   - `ADMIN_PASSWORD` = یه رمز قوی (بعداً از همین‌جا پیداش می‌کنی)
   - `SECRET_KEY` = یه رشته رندوم بلند
6. **Create** رو بزن → بعد از بالا اومدن برو به `/login` و وارد شو.

> ⚠️ پلن رایگان Render دیسک نداره و با هر ردیپلوی دیتا می‌پره! برای دیتای ماندگار: پلن `starter` + دیسک ۱ گیگ (mount روی `/data`) و `DATA_DIR=/data` — یا مرتب بکاپ بگیر. 💾

## 🐳 اجرای لوکال

```bash
cd yorozuya-panel
pip install -r requirements.txt
ADMIN_PASSWORD="یه-رمز-قوی" python main.py
# → http://localhost:8080/login
```

با داکر (از ریشه ریپو):

```bash
docker build -t yorozuya ./yorozuya-panel
docker run -p 8080:10000 -e ADMIN_PASSWORD="یه-رمز-قوی" yorozuya
```

## 🔧 متغیرهای محیطی

| متغیر | پیش‌فرض | توضیح |
|---|---|---|
| `ADMIN_PASSWORD` | `yorozuya` | ⚠️ حتماً عوضش کن! |
| `SECRET_KEY` | رندوم | کلید سشن (اختیاری) |
| `PORT` | `8080` | Render خودش `10000` تزریق می‌کنه |
| `DATA_DIR` | `./data` | محل ذخیره state؛ با دیسک Render = `/data` |

## 🔌 API (خلاصه)

```
POST /api/login                      ورود (ست کردن کوکی سشن)
GET  /api/server-info                آی‌پی خروجی + کشور + آمار
POST /api/server-info/refresh        رفرش آی‌پی خروجی
GET/POST        /api/users           لیست / ساخت یوزر
PATCH/DELETE    /api/users/{id}      ویرایش / حذف
POST /api/users/{id}/reset-traffic   ریست ترافیک
POST /api/users/{id}/rotate-sub      لینک ساب جدید
GET  /api/users/{id}/subscription    لینک‌ها + ساب + QR
GET/POST        /api/nodes           لیست / ساخت نود
PATCH/DELETE    /api/nodes/{id}      ویرایش / حذف
POST /api/nodes/{id}/check           چک سلامت یه نود
POST /api/nodes/check-all            چک همه نودها
POST /api/tools/lookup               {query} → آی‌پی + کشور
GET  /api/settings/backup            دانلود بکاپ JSON
POST /api/settings/restore           ریستور از فایل

# عمومی (بدون لاگین):
GET  /p/{sub_key}                    صفحه اشتراک یوزر 🌐
GET  /api/sub/{sub_key}              ساب base64 (?format=text هم داره)
GET  /api/sub/{sub_key}/qr           QR لینک ساب
GET  /api/public/sub/{sub_key}       JSON اشتراک برای صفحه
```

## 🗺 نقشه راه

- [ ] اتصال به Xray API برای ترافیک لحظه‌ای واقعی
- [ ] تلگرام بات فروش (مثل SpiderPanel)
- [ ] اسکنر SNI/IP
- [ ] چند ادمین + نقش

## 🙏 تشکر

- ایده و الهام: [SpiderPanel](https://github.com/amirh00sain/SpiderPanel) از amirh00sain 🕷
- تم: فن‌آرت گینتاما — هیچ وابستگی به ناشر انیمه/مانگا نداره. فقط عشق. 🍓

---

<div align="center">万事屋 • یوروزویا همه‌کار می‌کنه… حتی سابسکریپشن 😎</div>
