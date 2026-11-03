# Telegram media bot

Python 3.11+ kerak. Alohida video/post linkini yuboring. `yt-dlp` videolarni,
`gallery-dl` rasm va galereyalarni yuklaydi; birinchisi ishlamasa ikkinchisi sinab ko'riladi.
Sayt login, CAPTCHA yoki hududiy cheklov qo'ysa, barcha linklar ishlashi kafolatlanmaydi.

## Windows: ishga tushirish

```powershell
.\venv\Scripts\python.exe -m pip install -r requirements.txt
.\venv\Scripts\python.exe main.py
```

Virtual muhit bo'lmasa, oldin `python -m venv venv` bajaring.
`.env.example` namunasidagi sozlamalarni `.env` fayliga yozing. Mavjud `.env`ni
almashtirmang: `BOT_TOKEN` qiymatini saqlang. Bir token bilan bir vaqtda bitta
polling jarayonini ishlating. Terminal yopilsa bot ham to'xtaydi.

## Ishlash tartibi

- 3 ta ish parallel; jami 20 ta faol/navbatdagi so'rov. Har foydalanuvchiga bitta ish.
- Video 720p gacha (yt-dlp), fayl 49 MiB gacha, postdan 10 tagacha media.
  Gallery-dl asl faylni oladi, uni qayta kodlamaydi.
- Foto/video albom bo'lib jo'natiladi. Telegram qabul qilmagan format fayl sifatida yuboriladi.
- Aynan bir xil URL qayta yuborilsa, Telegram `file_id` orqali qayta yuklamasdan jo'natiladi.
  Kesh xotirada 6 soat, ko'pi bilan 512 link; bot qayta ishga tushsa tozalanadi.
- Yuklashga 180 soniya. Vaqt tugaganda yuklovchi va uning ffmpeg jarayonlari to'xtatiladi.
- Vaqtinchalik fayllar so'rov oxirida tozalanadi. Limitdan ortiq media olinmaydi.

Chegaralar `.env.example`da. `MAX_FILE_SIZE_MB` 49 dan katta bo'lsa avtomatik 49 ga
tushiriladi. `MAX_QUEUE_SIZE` navbat va ishlayotgan so'rovlarning umumiy sonidir.

Login kerak bo'lsa, o'zingizning Netscape formatidagi cookies faylingizni
`COOKIES_FILE=cookies.txt` orqali ko'rsatishingiz mumkin. Cookie/tokenni ulashmang.
YouTube ayrim linklar uchun yt-dlp qo'llaydigan JavaScript runtime (masalan Deno)
talab qilishi mumkin: https://github.com/yt-dlp/yt-dlp/wiki/EJS

## Tekshiruv

```powershell
.\venv\Scripts\python.exe -m unittest discover -s tests -v
```

Testlar tashqi saytlarga va Telegramga xabar yubormaydi. Haqiqiy tekshiruv uchun
botga qisqa YouTube/TikTok video hamda Instagram rasm/albom linkini yuboring,
keyin xuddi shu linkni takror yuborib keshni tekshiring.

Rasmiy manbalar: https://github.com/yt-dlp/yt-dlp,
https://github.com/mikf/gallery-dl/blob/master/docs/options.md,
https://docs.aiogram.dev/en/latest/api/methods/send_video.html
