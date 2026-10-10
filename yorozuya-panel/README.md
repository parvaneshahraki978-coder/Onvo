<div align="center">
  <h1>🍓 YorozuyaPanel — Python Edition <span>万事屋</span></h1>
  <p><b>کل پنل (بک‌اند + فرانت‌اند) در یک فایل — <code>app.py</code> — با stdlib خالص پایتون</b><br>
  صفر pip package · صفر دیتابیس · مناسب ضعیف‌ترین سرورها و هاست‌های رایگان</p>
  <p>
    <img alt="Python" src="https://img.shields.io/badge/Python-3.8%2B-3776AB?style=flat-square&logo=python&logoColor=white">
    <img alt="deps" src="https://img.shields.io/badge/dependencies-0-2ec4b6?style=flat-square">
    <img alt="single file" src="https://img.shields.io/badge/single--file-%E2%9C%94-f4d35e?style=flat-square">
    <img alt="Gintama" src="https://img.shields.io/badge/Theme-Gintama🍓-e63946?style=flat-square">
  </p>
</div>

> ⚠️ این پوشه نسخه‌ی **Python v1.2.0** (تک‌فایل، stdlib خالص) است. نسخه‌ی قدیمی v1.0.0 (FastAPI) با commit `e4a16b3` در تاریخچه‌ی git موجود است.

## ✨ قابلیت‌ها (مشترک با همه‌ی نسخه‌ها)

- 👥 **کاربران**: uuid، کلید اشتراک، محدودیت/مصرف ترافیک، تاریخ انقضا، فعال/غیرفعال، یادداشت
- 🔗 **سابسکریپشن**: لینک‌های vless / vmess / trojan / ss + ساب base64 + صفحه‌ی عمومی اشتراک (`/p/<key>`)
- 🛰 **نودها**: TCP health-check، latency، IP خروجی و کشور
- ⚖️ **لودبالانسر واقعی**: round-robin / least-latency / random بین نودهای سالم + URL ساب لودبالانس (`/api/lb/sub/<key>` با هدر `X-Yorozuya-LB-Node`)
- 🖼 **QR کد** client-side (بدون هیچ وابستگی سمت سرور)
- 🔐 **setup اولین اجرا** (`/setup`) — بدون env var؛ `ADMIN_PASSWORD` هم به‌عنوان override کار می‌کند
- ⏰ **keep-alive cron** (self-ping) — هاست رایگان هیچ‌وقت نمی‌خوابد
- 💾 **persistence** با JSON (`state.json`) — نوشتن atomic + debounced + backup/restore
- 🍡 تم گینتاما (万事屋)

## 🚀 اجرا

```bash
python3 app.py
# → آدرس «Web Panel path» را باز کن (/panel) و در اولین اجرا رمز ادمین را بساز
```

## 🌍 Deploy روی هاست رایگان

| هاست | روش |
|---|---|
| **Render** | New → Web Service → این repo → Root Directory: `yorozuya-panel` → Start Command: `python app.py` — یا با `render.yaml` همین پوشه (Blueprint) |
| **Railway** | New Project → Deploy from GitHub → Root: `yorozuya-panel` → Start: `python app.py` |
| **Replit** | فقط `app.py` را آپلود کن → Run (خودش `python3 app.py` را تشخیص می‌دهد) |
| **Wispbyte** | سرور Python بساز → فقط `app.py` را آپلود کن → Start |
| **Koyeb** | New App → Python → Start command: `python app.py` |
| **Oracle Cloud (Always Free)** | VM رایگان بساز → `python3 app.py` اجرا کن (پورت را در فایروال/NSG باز کن) |
| **هر VPS** | `python3 app.py` — نیازی به `pip install` نیست! |

## ⚙️ متغیرهای محیطی (همه اختیاری)

| متغیر | پیش‌فرض | توضیح |
|---|---|---|
| `PORT` / `SERVER_PORT` | `8080` | پورت گوش‌دادن (از خود سرور/هاست خوانده می‌شود) |
| `PUBLIC_URL` / `RENDER_EXTERNAL_URL` / `APP_URL` / `DOMAIN` | — | آدرس عمومی برای لینک‌های ساب (در Render خودکار از `RENDER_EXTERNAL_URL` خوانده می‌شود) |
| `ADMIN_PASSWORD` | — | override رمز ادمین (در غیر این‌صورت از `/setup`) |
| `KEEPALIVE_MINUTES` | `10` | self-ping هر N دقیقه (`0` = خاموش) |
| `NODE_CHECK_MINUTES` | `5` | چک خودکار سلامت نودها هر N دقیقه |
| `DATA_DIR` | `./yorozuya-data` | محل نگهداری `state.json` |

## 📁 فایل‌ها

- `app.py` — کل پنل (بک‌اند + فرانت‌اند) در یک فایل
- `render.yaml` — deploy یک‌کلیکی روی Render
- `Dockerfile` — اجرای کانتینری
- `README.md` — همین صفحه

## 📡 API

همان API نسخه‌ی Node (`yorozuya-node/`): `/api/setup`، `/api/login`، `/api/users`، `/api/nodes`، `/api/lb`، `/api/sub/<key>`، `/api/lb/sub/<key>`، `/api/settings/backup` و بقیه.

## 📝 نکات

- QR کدها **سمت کلاینت** ساخته می‌شوند — endpoint قدیمی `/qr` در این نسخه `501` می‌دهد.
- sessionها در حافظه‌ی process هستند (بعد از restart همه logout می‌شوند — مثل نسخه‌ی Node).
- نیاز به **Python 3.8+** — هیچ `pip install` لازم نیست.
- نسخه‌های دیگر پنل: `yorozuya-node/` (Node.js) و `yorozuya-php/` (PHP).

---
🍓 ساخته‌شده با شیر توت‌فرنگی و تنبلی گینتاما — ما همه‌کار می‌کنیم!
