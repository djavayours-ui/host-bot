# Avenor Builder — bot yasaydigan bot

Bitta jarayonda: **asosiy (builder) bot** + u orqali yaratilgan **barcha botlar** (har biri o'z polling siklida).

## Ishga tushirish
```bash
pip install -r requirements.txt
export BOT_TOKEN=123:ASOSIY_BOT_TOKENI     # builder bot
export ADMIN_IDS=123456789                 # siz (asosiy admin), vergul bilan bir nechta
python main.py
```
Ixtiyoriy: `DATA_DIR` (standart `data`), `TZ_NAME` (standart `Asia/Tashkent`), `BRAND`.
Host-bot platformasida: zipni yuklang, `ADMIN_IDS` ni «Muhit o'zgaruvchilari»ga qo'shing (`BOT_TOKEN` o'zi beriladi).
Ma'lumotlar `DATA_DIR/builder.db` (SQLite) da — shu fayl zaxiralansa, hammasi saqlanadi.

## Rollar
| Kim | Qayerda | Nima qila oladi |
|---|---|---|
| **Asosiy admin** (`ADMIN_IDS`) | Builder botda `🛡 Admin panel` | Do'kon narxlari, limitlar, valyuta, promokodlar, foydalanuvchilar, barcha botlar, xabar yuborish. **Barcha yaratilgan botlarda yashirin admin** (`/admin`), hech qayerda ko'rinmaydi. |
| **Bot egasi** | Builder botda — oddiy foydalanuvchi; o'z botida `/admin` | Faqat o'z botini sozlaydi. Asosiy admin panelga **kira olmaydi**. |

## Bot yaratish
`➕ Bot yaratish` → token yuboriladi → bot darhol ishga tushadi. Token xabari chatdan o'chiriladi.
Botlarim: to'xtatish/yoqish, token almashtirish (faqat shu botning tokeni), o'chirish.

## Yaratilgan bot ichida (`/admin`)
- **Start xabari** — har qanday turdagi xabar (matn, rasm, video, GIF, fayl, audio, ovozli, dumaloq video, stiker). Shrift/format (qalin, kursiv, spoyler, mono, havola) saqlanadi.
- **Pastki tugmalar** — har biriga xabar + inline tugmalar biriktiriladi; rang, qator (yonma-yon / yangi qator).
- **Inline tugma turlari:** URL · nusxalash (copy) · pop-up · buyruq/xabar ochish · ulashish (forward/share) · inline rejim (chat tanlash) · inline rejim (shu chat) · Web App. Hammasiga **rang**: 🔵 primary, 🟢 success, 🔴 danger (Bot API 9.4; yangi Telegram klientlarida ko'rinadi).
- **Buyruqlar** (`/nom`) — har biriga xabar va inline tugmalar.
- **Majburiy obuna** — kanallar (standart 1 ta), obuna xabari (har qanday turda), kanal tugmasi matni (`%channel%`), URL, rang, «Tekshirish» tugmasi. Bot kanalda administrator bo'lishi shart.
- Xabar yuborish (har qanday turda), statistika, balans berish (`+50`, `-20`, `=100`), ban/unban.

### O'zgaruvchilar (xabar va inline tugma matnida)
`%time% %date% %datetime% %weekday% %name%` (profilga havolali ism) `%nickname% %fullname% %first_name% %last_name% %username% %id% %balance%` (foydalanuvchining shu botdagi balansi) `%users% %botname% %botusername% %channel%`.

## Limitlar va Do'kon
Standart: har xabarga **2** inline tugma (maks. **20**), **6** pastki tugma (maks. 50), **1** majburiy kanal (maks. 10), foydalanuvchiga **3** bot. Hammasi admin panelda o'zgaradi.
Qolganini egasi builder botdagi **🛒 Do'kon**dan sotib oladi (har bir bot uchun alohida). Narxlar admin panelda belgilanadi (0 = sotuvda yo'q).

## Valyuta (Stars)
Ichki valyuta (nomi/belgisi/kursi admin panelda; standart: Tanga 🪙, 1 ⭐ = 1 🪙). Hamyon **Telegram Stars (XTR)** orqali to'ldiriladi (paketlar yoki o'z miqdori) — tashqi provayder kerak emas. To'lovlar `charge_id` bo'yicha bir marta hisoblanadi.

## Promokodlar (admin)
Yaratish: mukofot → **nechta kod** (yoki o'z kodingiz) → har kod **necha marta** (yoki ∞) → **tugash** (kun soni / sana / ∞) → **boshlanish** (darhol / sana). Avtomatik kodlar 8 belgili: `A8BXG67H`. Ro'yxat, yoqish/o'chirish, o'chirish, `.txt` eksport. Har foydalanuvchi har kodni 1 marta ishlatadi.

## Tayyor botlar (shablonlar)
`➕ Bot yaratish` → **🆕 Bo'sh bot** yoki **📁 Kino bot**. Shablon bot yaratilganda tanlanadi va keyin o'zgarmaydi.

### 📁 Kino bot
Foydalanuvchi tomonida: kino **nomi** yoki **kodini** yozadi → bot topib yuboradi (apostrof/imlo xatosiga bardoshli qidiruv, bir nechta natija bo'lsa tugmalar). Ko'p qismli kinolarda qism tanlash tugmalari va ◀️ ▶️, `📤 Ulashish` (havola `?start=k<kod>` kinoni to'g'ridan-to'g'ri ochadi). Tayyor buyruqlar: `/top` (mashhur), `/new` (yangi), `/random` (tasodifiy). Majburiy obuna, xabar yuborish, statistika, ban, referal va boshqa umumiy imkoniyatlar odatdagidek ishlaydi. Fayl **nusxalanadi** (copy), shuning uchun «forward» belgisi, kanal nomi yoki boshqa birovning imzosi chiqmaydi; fayl ostidagi matnni egasi o'zi belgilaydi (`/admin` → 🎬 Kino bazasi → ✏️ Fayl ostidagi matn).

**Baza — Telegram kanal.** `/admin` → `🎬 Kino bazasi` → `📡 Baza kanali`: botni kanalga administrator qiling (xabar yuborish/o'chirish huquqi bilan), kanalni ulang. Har bir kino kanalda shunday saqlanadi:
```
#tez_va_jahldor_5

🎬 Tez va Jahldor 5
🆔 Kod: 12
```
Sarlavhadagi **birinchi hashtag** kino nomi; bir xil hashtagli postlar — bitta kinoning qismlari. Kino qo'shish ikki yo'l bilan:
- botda `➕ Kino qo'shish` (video/fayl yuborasiz → nom so'raladi → bot kanalga hashtag bilan joylaydi, kod beradi; mavjud nom bo'lsa yangi qism bo'ladi);
- kanalga o'zingiz `#kino_nomi` sarlavhali video/fayl tashlasangiz — bot uni o'zi indekslaydi.

**Muhim cheklov:** Telegram Bot API botga kanal tarixini qidirishga ruxsat bermaydi, shuning uchun postlar ro'yxati (xabar ID + hashtag) `builder.db` da indekslanadi (fayllarning o'zi faqat kanalda). Bot qo'shilishidan oldingi postlarni `🔁 Eski postlarni indekslash` orqali kanaldan forward qilib qo'shasiz. `builder.db` zaxirasi (`/backup`) indeksni ham saqlaydi. Kanaldan post o'chirilsa, foydalanuvchi so'raganda indeksdan ham olib tashlanadi.

Admin: `📋 Kinolar` (ro'yxat, qism qo'shish, o'chirish — faqat botdan yoki kanaldan ham), `🔒 Himoya` (foydalanuvchi faylni forward/saqlay olmaydi), ko'rishlar statistikasi.

## Yangi imkoniyatlar
- **Referal tizimi** — har foydalanuvchining shaxsiy havolasi `t.me/bot?start=ref_<id>`; yangi odam kelganda taklif qiluvchiga mukofot (admin panel → «🎁 Referal va bonus») va xabar boradi. O'zini taklif qilish, ikkinchi marta hisoblash, banlangan foydalanuvchi — bloklangan. Statistikada «eng ko'p taklif qilganlar».
- **Kunlik bonus** — `/bonus` (har ~20 soatda 1 marta, miqdorni egasi belgilaydi).
- **Tayyor buyruqlar** — `/balance`, `/bonus`, `/ref` (o'z buyrug'ingiz shu nomda bo'lsa, o'zingiznikiga ustunlik beriladi).
- **Kalit so'zlar (avto-javob)** — foydalanuvchi aniq matn yozsa bot tayyor xabar (inline tugmalar bilan) yuboradi (maks. 50).
- **Qo'shimcha adminlar** — bot egasi 10 tagacha adminni ID orqali qo'shadi (faqat egasi boshqaradi).
- **Foydalanuvchilar CSV eksporti**, **7 kunlik o'sish grafigi**, botni bloklagan/qaytgan foydalanuvchilarni **avtomatik kuzatish** (`my_chat_member`).
- **Xabar yuborishni to'xtatish** tugmasi; **flood himoyasi** (10 soniyada 20 tadan ko'p so'rov e'tiborsiz).
- Yangi o'zgaruvchilar: `%referrals%`, `%ref_link%`.
- Asosiy admin: `/backup` — bazaning izchil zaxira nusxasini yuboradi.
- **Panel bitta xabarda ishlaydi:** yangi bo'lim ochilganda yoki qiymat so'ralganda bot yangi xabar yubormaydi — joriy xabarning o'zi tahrirlanadi, siz yuborgan javob esa chatdan o'chiriladi (xabar yuborish uchun yuborilgan xabar bundan mustasno).
- **Ikki marta javob himoyasi:** bir xil `update_id` ikkinchi marta qayta ishlanmaydi. Agar bot **ikki joyda** (masalan, server va GitHub Actions'da) ishlayotgan bo'lsa, `Conflict` xatosi ketma-ket 3 marta takrorlanganda egasi va adminlarga ogohlantirish yuboriladi.
- **Holat xabari (ETA):** bot ishga tushgach `ADMIN_IDS` dagi adminlarga «✅ Botingiz ishga tushdi — run bo'ldi!» xabari boradi: hodisa (rejali qayta ishga tushirish / qo'lda / kod yangilandi + commit matni va versiya), ishlagan vaqt, **keyingi yangilanishgacha qolgan vaqt (ETA, progress-bar bilan)** va voqealar ro'yxati. Shu xabarning o'zi tahrirlanib turadi (tugashga yaqinlashgan sari tez-tez: 60 → 30 → 10 soniya). Yangilanishda xabar «🔄 qayta ishga tushirish boshlandi» holatiga o'tadi, yangi seans esa yangi xabar yuboradi (eskisi «tugagan» deb belgilanadi). Adminlar asosiy botga kamida bir marta `/start` bosgan bo'lsin. `RUN_SECONDS` berilmasa (oddiy server) ETA ko'rsatilmaydi.
- **`/top`** — balans va takliflar bo'yicha reyting. **`/contact`** — murojaat: foydalanuvchi xabari egasiga/adminlarga boradi, ular shu xabarga *reply* qilsa javob foydalanuvchiga yetadi (panel → «🎁 Referal va bonus» → «Murojaat: yoqish»). **Xush kelibsiz bonusi** — yangi foydalanuvchiga bir martalik balans. **Tugma statistikasi** — statistikada eng ko'p bosilgan inline tugmalar.
- GitHub'da `main`/`master` ga push qilinsa, ishlayotgan bot tartibli to'xtab, yangi kod darhol ishga tushadi (`.md` fayllar bundan mustasno).
- Eski bazalar avtomatik yangilanadi (ustunlar o'zi qo'shiladi).

## GitHub Actions'da ishga tushirish (`.github/workflows/bot.yml`)
1. Repozitoriyni **private** qiling (baza ichida bot tokenlari saqlanadi).
2. Settings → Secrets and variables → Actions → **Secrets**: `BOT_TOKEN`, `ADMIN_IDS` qo'shing (ixtiyoriy Variable: `BRAND`).
3. Actions → «Avenor Builder bot» → **Run workflow**. Keyin har 6 soatda o'zi qayta ishga tushadi.

Qanday ishlaydi: bitta job ~5 soat 45 daqiqa ishlaydi, `concurrency` navbat tufayli keyingisi oldingisi tugagach darhol boshlanadi (ikki nusxa bir vaqtda ishlamaydi). Baza `actions/cache` orqali ishlar orasida saqlanadi.

Cheklovlar (bilib qo'ying):
- Baza faqat job **oxirida** saqlanadi. Runner kutilmaganda o'chsa, oxirgi ishga tushirishdan keyingi o'zgarishlar yo'qolishi mumkin — vaqti-vaqti bilan `/backup` bilan nusxa oling.
- Cache 7 kun ishlatilmasa o'chadi va repozitoriy bo'yicha 10 GB bilan cheklangan; botni 7 kundan ortiq to'xtatib qo'ymang.
- Almashtirish orasida bir necha soniya-daqiqa uzilish bo'ladi. Jiddiy loyiha uchun VPS yaxshiroq.
- GitHub Actions'ni bunday doimiy bot hosting sifatida ishlatish xizmat shartlariga zid kelishi mumkin; hisobingiz uchun xavfini o'zingiz baholang.

## Cheklovlar (bilib qo'ying)
- Albom (bir nechta rasm) bitta xabar sifatida saqlanmaydi — bittadan yuboring. Premium custom-emoji faqat bot egasi Premium bo'lsa ko'rinadi.
- Pastki klaviatura foydalanuvchida `/start` bosilganda yangilanadi; start xabarida inline tugma bo'lsa, klaviatura alohida «⌨️» xabarida yuboriladi (Telegram bitta xabarda ikkalasiga ruxsat bermaydi).
- Inline rejim tugmalari ishlashi uchun @BotFather'da shu bot uchun `/setinline` yoqilgan bo'lsin.
- Asosiy adminlar faqat `ADMIN_IDS` orqali belgilanadi (o'zgartirgach qayta ishga tushiring).
- Yaratilgan botlar bitta jarayonda ishlaydi: yuzlab botgacha qulay; undan ko'pi uchun jarayonlarni ajrating.
