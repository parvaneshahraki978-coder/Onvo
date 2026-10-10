<div align="center">
  <h1>🍓 YorozuyaPanel — PHP Edition <span>万事屋</span></h1>
  <p><b>کل پنل (بک‌اند + فرانت‌اند) در یک فایل — <code>index.php</code></b><br>
  بدون composer · بدون افزونه‌ی اضافی · بدون دیتابیس · مناسب هاست‌های رایگان PHP</p>
  <p>
    <img alt="PHP" src="https://img.shields.io/badge/PHP-7.4%2B-777BB4?style=flat-square&logo=php&logoColor=white">
    <img alt="deps" src="https://img.shields.io/badge/dependencies-0-2ec4b6?style=flat-square">
    <img alt="single file" src="https://img.shields.io/badge/single--file-%E2%9C%94-f4d35e?style=flat-square">
    <img alt="Gintama" src="https://img.shields.io/badge/Theme-Gintama🍓-e63946?style=flat-square">
  </p>
</div>

## ✨ قابلیت‌ها (مشترک با همه‌ی نسخه‌ها)

- 👥 **کاربران**: uuid، کلید اشتراک، محدودیت/مصرف ترافیک، انقضا، فعال/غیرفعال، یادداشت
- 🔗 **سابسکریپشن**: vless / vmess / trojan / ss + ساب base64 + صفحه‌ی عمومی اشتراک
- 🛰 **نودها**: TCP health-check، latency، IP خروجی و کشور
- ⚖️ **لودبالانسر واقعی**: round-robin / least-latency / random + URL لودبالانس (`X-Yorozuya-LB-Node`)
- 🖼 **QR کد** client-side — بدون GD!
- 🔐 **setup اولین اجرا** (`?p=setup`) — بدون env var؛ `ADMIN_PASSWORD` هم override است
- ⏰ **keep-alive** self-ping — هاسترایگان هیچ‌وقت نمی‌خوابد
- 💾 **state** در `yorozuya-data/state.json` + backup/restore
- 🍡 تم گینتاما (万事屋)

## 🚀 اجرا

**روی هاست:** فقط `index.php` را در `public_html` / `htdocs` آپلود کن — تمام!

**لوکال:**
```bash
php -S 0.0.0.0:8080 index.php
# → http://localhost:8080/?p=setup   (اولین اجرا: رمز ادمین را بساز)
```

## 🔀 آدرس‌ها (?p= routing)

همه‌ی لینک‌ها از الگوی `?p=...` استفاده می‌کنند تا روی **هر** هاست (Apache، nginx، php -S) بدون .htaccess یا تنظیمات اضافه کار کنند:

| صفحه | آدرس |
|---|---|
| پنل | `https://host/?p=panel` |
| setup | `https://host/?p=setup` |
| صفحه اشتراک | `https://host/?p=s&key=<کلید>` |
| ساب (base64) | `https://host/?p=api/sub/<کلید>` |
| ساب لودبالانسر | `https://host/?p=api/lb/sub/<کلید>` |

> اگر پنل را در زیردایرکتوری می‌گذاری (`/panel/index.php`) یا نام فایل را عوض می‌کنی (`panel.php`)، لینک‌ها خودکار درست ساخته می‌شوند.

## 🌍 Deploy روی هاست رایگان PHP

| هاست | روش |
|---|---|
| **000webhost** | ثبت‌نام رایگان → File Manager → `htdocs` → Upload `index.php` → تمام |
| **InfinityFree** | ثبت‌نام رایگان → htdocs → Upload `index.php` → تمام |
| **AwardSpace** | ثبت‌نام رایگان → File Manager → upload `index.php` → تمام |
| **FreeHostia** | میزبانی رایگان → upload `index.php` |
| **هر هاست PHP** | فقط `index.php` را آپلود کن |

## ⚙️ متغیرهای محیطی (همه اختیاری)

| متغیر | پیش‌فرض | توضیح |
|---|---|---|
| `PUBLIC_URL` / `RENDER_EXTERNAL_URL` / `APP_URL` / `DOMAIN` | — | آدرس عمومی برای لینک‌های ساب |
| `ADMIN_PASSWORD` | — | override رمز ادمین |
| `KEEPALIVE_MINUTES` | `10` | self-ping هر N دقیقه (`0` = خاموش) |
| `NODE_CHECK_MINUTES` | `5` | چک خودکار نودها (on-demand) |
| `DATA_DIR` | `./yorozuya-data` | محل state |
| `PORT` / `SERVER_PORT` | `8080` | فقط برای `php -S` |

## 📝 نکات

- **QR client-side** — نیازی به GD نیست.
- **state و sessionها** در `yorozuya-data/` — session بعد از restart هم معتبر می‌ماند.
- PHP **7.4+** — فقط از json/hash/random_bytes و streamهای استاندارد استفاده می‌کند؛ نیازی به cURL/GD/MySQL نیست.
- **keep-alive** با self-ping — برای هاست‌هایی که بعد از مدتی sleep می‌کنند.
- نسخه‌های دیگر پنل: `yorozuya-node/` (Node.js) و `yorozuya-panel/` (Python).

---
🍓 ساخته‌شده با شیر توت‌فرنگی و تنبلی گینتاما — ما همه‌کار می‌کنیم!
