from __future__ import annotations

from html import escape
from datetime import datetime, timezone
from typing import Any
import logging

from services.user_preferences import UserPreferenceService


SUPPORTED_LANGUAGES = {
    "en": "English", "ru": "Русский", "uk": "Українська",
    "he": "עברית", "ar": "العربية",
}
RTL_LANGUAGES = frozenset({"he", "ar"})
LANGUAGE_ALIASES = {
    "english": "en", "русский": "ru", "russian": "ru",
    "українська": "uk", "украинский": "uk", "ukrainian": "uk",
    "עברית": "he", "hebrew": "he", "العربية": "ar", "arabic": "ar",
}


EN = {
    "common.unavailable": "Temporarily unavailable",
    "common.example": "Example",
    "common.usage": "Usage",
    "common.updated": "Updated {age}",
    "common.stale": "STALE · {age} old",
    "language.title": "Language",
    "language.choose": "Choose: /language en · ru · uk · he · ar",
    "language.updated": "Language changed to {name}.",
    "language.unsupported": "Unsupported language. Choose en, ru, uk, he, or ar.",
    "start.title": "Liquidity Vision Intelligence",
    "start.welcome": "Welcome, {name}. Evidence-led market intelligence and PAPER decision support.",
    "start.commands": "Start with /analyze BTC 1h, /scanner, /watchlist add BTC SOL, or /help.",
    "start.paper": "PAPER means simulated execution. No analysis or plan guarantees profit.",
    "help.title": "Liquidity Vision Intelligence · Help",
    "help.intro": "Choose a section for focused commands and examples.",
    "help.unknown": "Unknown help section. Example: /help market",
    "help.disclaimer": "Evidence and decision support—not trading authority or a promise of profit.",
    "help.market": "current market analysis, structure, liquidity and public market data",
    "help.trading": "watchlist, journal, replay and PAPER positions",
    "help.copy": "PAPER copy configuration, execution and statistics",
    "help.intelligence": "ranking, quality, contradictions and optional AI advisory",
    "help.research": "edge discovery, cohorts and forward validation",
    "help.system": "public service and data health",
    "help.account": "profile, language, settings, usage and plans",
    "help.premium": "Free, Pro and Elite capabilities",
    "help.settings": "language, alerts and presentation preferences",
    "help.scanner": "ranked opportunities and transparent filters",
    "help.watchlist": "smart watchlist state, ranking and editing",
    "help.alerts": "notification categories and delivery preferences",
    "help.ai": "advisory AI observations, identity, cost and history",
    "help.live": "per-user exchange readiness, risk, reconciliation and explicit LIVE state",
    "plans.title": "Liquidity Vision Intelligence · Plans",
    "plans.current": "Current plan: {plan}",
    "plans.expires": "Expires: {expiry}",
    "plans.disclaimer": "Plans add depth, personalization and research—not profitability or trading authority.",
    "plan.free": "FREE", "plan.pro": "PRO", "plan.elite": "ELITE INTELLIGENCE",
    "my_plan.title": "My Plan · {plan}",
    "my_plan.source": "Source: {source}",
    "my_plan.enabled": "Enabled capabilities: {count}",
    "my_plan.limits": "Usage limits: {limits}",
    "settings.title": "Personal Settings",
    "settings.output": "Output: {value}",
    "settings.verbosity": "Alert verbosity: {value}",
    "settings.language": "Language: {value}",
    "settings.risk": "Risk display: {value}",
    "settings.alerts": "Alerts: {value}",
    "settings.guide": "Examples: /settings mode detailed · /settings timeframe 15m 1h · /alerts quality on",
    "settings.safety": "Preferences cannot override global risk or execution safety.",
    "alerts.title": "Alert Preferences",
    "alerts.updated": "Alert preferences updated.",
    "usage.title": "Usage · {plan}",
    "usage.line": "{name}: {used} / {limit} · remaining {remaining}",
    "usage.reset": "Daily limits reset at 00:00 UTC.",
    "scanner.title": "Liquidity Vision · Scanner V3",
    "scanner.filter": "Filter: {filter} · remaining today: {remaining}",
    "scanner.distribution": "Strategy distribution: {distribution}",
    "scanner.no_results": "No opportunity currently meets this evidence filter.",
    "scanner.disclaimer": "Analytical ranking only. Scores are not probabilities or execution instructions.",
    "scanner.score": "Scanner {scanner} · Quality {quality} · Readiness {readiness} · Fit {fit}",
    "scanner.advantage": "+ {text}", "scanner.contradiction": "− {text}",
    "watchlist.title": "Smart Watchlist V2",
    "watchlist.empty": "Your watchlist is empty.",
    "watchlist.hint": "Use /watchlist add BTC SOL or the Watch button after analysis.",
    "watchlist.checked": "Checked: {value}",
    "watchlist.waiting": "waiting for first cycle",
    "watchlist.error": "Market data temporarily unavailable",
    "watchlist.edit": "Edit: /watchlist add BTC · /watchlist remove BTC · /watchlist rank",
    "system.title": "Liquidity Vision · System Health V3",
    "system.database": "Database: {backend} · {status}",
    "system.provider": "Market provider: {provider} · {status}",
    "system.ai": "AI advisory: {status}",
    "system.details": "Detailed errors and private aggregates are operator-only.",
    "preview.title": "Premium Preview",
    "preview.current": "Current plan: {plan}",
    "preview.no_authority": "Plans never grant trading authority.",
    "copy.title": "PAPER Copy Trading",
    "copy.performance": "PAPER Copy · Performance Separation",
    "copy.rejections": "Copy Rejection Intelligence",
    "positions.title": "PAPER Positions", "orders.title": "PAPER Orders", "fills.title": "PAPER Fills",
    "trade.title": "Trade Replay", "market_story.title": "Market Story",
    "signal_quality.title": "Signal Quality V4", "contradictions.title": "Contradictions",
    "orderbook.title": "Order-book Intelligence", "funding.title": "Funding Intelligence",
    "open_interest.title": "Open Interest Intelligence", "data_health.title": "Decision Data Health",
    "ai.status.title": "AI Advisory Status",
    "error.symbol_invalid": "Invalid or ambiguous symbol. Example: BTC, BTCUSDT, or BTC-USDT.",
    "error.market_unavailable": "Market data temporarily unavailable. Try again shortly.",
    "error.not_found": "No matching decision-time record was found.",
    "menu.help": "Browse intelligence commands", "menu.analyze": "Analyze a market",
    "menu.scanner": "Rank opportunities", "menu.watchlist": "Your tracked markets",
    "menu.trade": "Trade replay and journal", "menu.copy": "PAPER copy overview",
    "menu.positions": "PAPER positions", "menu.signal_rankings": "Ranked signals",
    "menu.research": "Research hub", "menu.profile": "Profile and plan",
    "menu.premium": "Plans and capabilities",
    "menu.start": "Open Liquidity Vision", "menu.journal": "Trade journal",
    "menu.rankings": "Ranked signals", "menu.settings": "Personal settings",
    "menu.alerts": "Alert preferences",
}


REQUIRED_CORE_KEYS = frozenset({
    "language.title", "language.updated", "language.unsupported", "start.title", "start.welcome",
    "start.commands", "start.paper", "help.title", "help.intro", "plans.title",
    "plans.current", "plans.disclaimer", "settings.title", "scanner.title", "watchlist.title",
    "system.title", "error.symbol_invalid", "error.market_unavailable", "usage.title",
})


RU = {
    "language.title": "Язык", "language.updated": "Язык изменён на {name}.",
    "language.unsupported": "Язык не поддерживается. Выберите en, ru, uk, he или ar.",
    "start.title": "Liquidity Vision Intelligence", "start.welcome": "Добро пожаловать, {name}. Аналитика рынка на основе проверяемых данных и PAPER-поддержка решений.",
    "start.commands": "Начните с /analyze BTC 1h, /scanner, /watchlist add BTC SOL или /help.",
    "start.paper": "PAPER означает симуляцию исполнения. Анализ и тариф не гарантируют прибыль.",
    "help.title": "Liquidity Vision Intelligence · Помощь", "help.intro": "Выберите раздел с командами и примерами.",
    "plans.title": "Liquidity Vision Intelligence · Тарифы", "plans.current": "Текущий тариф: {plan}",
    "plans.disclaimer": "Тарифы добавляют глубину и исследования, но не торговые полномочия и не гарантию прибыли.",
    "settings.title": "Персональные настройки", "scanner.title": "Liquidity Vision · Сканер V2",
    "watchlist.title": "Умный список наблюдения V2", "system.title": "Liquidity Vision · Состояние системы V3",
    "error.symbol_invalid": "Неверный или неоднозначный символ. Пример: BTC, BTCUSDT или BTC-USDT.",
    "error.market_unavailable": "Рыночные данные временно недоступны. Повторите позже.",
    "usage.title": "Использование · {plan}",
}
UK = {
    "language.title": "Мова", "language.updated": "Мову змінено на {name}.",
    "language.unsupported": "Мова не підтримується. Оберіть en, ru, uk, he або ar.",
    "start.title": "Liquidity Vision Intelligence", "start.welcome": "Вітаємо, {name}. Ринкова аналітика на основі доказів і PAPER-підтримка рішень.",
    "start.commands": "Почніть з /analyze BTC 1h, /scanner, /watchlist add BTC SOL або /help.",
    "start.paper": "PAPER означає симуляцію виконання. Аналіз і план не гарантують прибуток.",
    "help.title": "Liquidity Vision Intelligence · Допомога", "help.intro": "Оберіть розділ із командами та прикладами.",
    "plans.title": "Liquidity Vision Intelligence · Плани", "plans.current": "Поточний план: {plan}",
    "plans.disclaimer": "Плани додають глибину та дослідження, але не торгові повноваження чи гарантію прибутку.",
    "settings.title": "Персональні налаштування", "scanner.title": "Liquidity Vision · Сканер V2",
    "watchlist.title": "Розумний список спостереження V2", "system.title": "Liquidity Vision · Стан системи V3",
    "error.symbol_invalid": "Некоректний або неоднозначний символ. Приклад: BTC, BTCUSDT або BTC-USDT.",
    "error.market_unavailable": "Ринкові дані тимчасово недоступні. Спробуйте пізніше.",
    "usage.title": "Використання · {plan}",
}
HE = {
    "language.title": "שפה", "language.updated": "השפה שונתה ל־{name}.",
    "language.unsupported": "השפה אינה נתמכת. יש לבחור en, ru, uk, he או ar.",
    "start.title": "Liquidity Vision Intelligence", "start.welcome": "ברוך הבא, {name}. מודיעין שוק מבוסס ראיות ותמיכה בהחלטות PAPER.",
    "start.commands": "אפשר להתחיל עם /analyze BTC 1h, /scanner, /watchlist add BTC SOL או /help.",
    "start.paper": "PAPER הוא ביצוע מדומה. ניתוח או תוכנית אינם מבטיחים רווח.",
    "help.title": "Liquidity Vision Intelligence · עזרה", "help.intro": "בחרו תחום לקבלת פקודות ודוגמאות.",
    "plans.title": "Liquidity Vision Intelligence · תוכניות", "plans.current": "התוכנית הנוכחית: {plan}",
    "plans.disclaimer": "תוכניות מוסיפות עומק ומחקר, לא סמכות מסחר ולא הבטחת רווח.",
    "settings.title": "הגדרות אישיות", "scanner.title": "Liquidity Vision · סורק V2",
    "watchlist.title": "רשימת מעקב חכמה V2", "system.title": "Liquidity Vision · בריאות מערכת V3",
    "error.symbol_invalid": "סמל שגוי או דו־משמעי. דוגמה: BTC, BTCUSDT או BTC-USDT.",
    "error.market_unavailable": "נתוני השוק אינם זמינים זמנית. נסו שוב בקרוב.",
    "usage.title": "שימוש · {plan}",
}
AR = {
    "language.title": "اللغة", "language.updated": "تم تغيير اللغة إلى {name}.",
    "language.unsupported": "اللغة غير مدعومة. اختر en أو ru أو uk أو he أو ar.",
    "start.title": "Liquidity Vision Intelligence", "start.welcome": "مرحباً {name}. معلومات سوق قائمة على الأدلة ودعم قرارات PAPER.",
    "start.commands": "ابدأ باستخدام /analyze BTC 1h أو /scanner أو /watchlist add BTC SOL أو /help.",
    "start.paper": "PAPER يعني تنفيذاً محاكياً. التحليل أو الخطة لا يضمنان الربح.",
    "help.title": "Liquidity Vision Intelligence · المساعدة", "help.intro": "اختر قسماً للأوامر والأمثلة.",
    "plans.title": "Liquidity Vision Intelligence · الخطط", "plans.current": "الخطة الحالية: {plan}",
    "plans.disclaimer": "تضيف الخطط عمقاً وبحثاً، ولا تمنح سلطة تداول أو ضمان ربح.",
    "settings.title": "الإعدادات الشخصية", "scanner.title": "Liquidity Vision · الماسح V2",
    "watchlist.title": "قائمة مراقبة ذكية V2", "system.title": "Liquidity Vision · صحة النظام V3",
    "error.symbol_invalid": "رمز غير صالح أو ملتبس. مثال: BTC أو BTCUSDT أو BTC-USDT.",
    "error.market_unavailable": "بيانات السوق غير متاحة مؤقتاً. حاول بعد قليل.",
    "usage.title": "الاستخدام · {plan}",
}

# Help, menu and high-frequency product surfaces are explicitly translated;
# lower-frequency diagnostic prose may safely fall back to English.
RU.update({
    "help.disclaimer": "Поддержка решений на основе данных — не право торговать и не обещание прибыли.",
    "help.market": "анализ рынка, структура, ликвидность и публичные данные",
    "help.trading": "список наблюдения, журнал, повтор и PAPER-позиции",
    "help.copy": "настройки, исполнение и статистика PAPER-копирования",
    "help.intelligence": "ранжирование, качество, противоречия и AI-советник",
    "help.research": "поиск преимуществ, когорты и форвардная проверка",
    "help.system": "состояние сервиса и свежесть данных", "help.account": "профиль, язык, настройки, лимиты и планы",
    "help.premium": "возможности Free, Pro и Elite", "help.settings": "язык, уведомления и формат",
    "help.scanner": "ранжированные возможности и прозрачные фильтры",
    "help.watchlist": "состояние и настройка умного списка", "help.alerts": "категории и доставка уведомлений",
    "help.ai": "наблюдения AI, идентификатор, стоимость и история",
    "help.live": "готовность, риск, сверка и состояние LIVE для пользователя",
    "menu.help": "Справка по командам", "menu.analyze": "Анализ рынка", "menu.scanner": "Рейтинг возможностей",
    "menu.watchlist": "Отслеживаемые рынки", "menu.trade": "Повтор сделки и журнал",
    "menu.copy": "Обзор PAPER-копирования", "menu.positions": "PAPER-позиции",
    "menu.signal_rankings": "Рейтинг сигналов", "menu.research": "Исследовательский центр",
    "menu.profile": "Профиль и план", "menu.premium": "Планы и возможности",
    "menu.start": "Открыть Liquidity Vision", "menu.journal": "Торговый журнал",
    "menu.rankings": "Рейтинг сигналов", "menu.settings": "Настройки", "menu.alerts": "Уведомления",
    "watchlist.empty": "Ваш список наблюдения пуст.", "watchlist.error": "Рыночные данные временно недоступны",
    "watchlist.checked": "Проверено: {value}", "watchlist.waiting": "ожидается первый цикл",
    "scanner.no_results": "Нет возможностей, соответствующих фильтру.",
    "scanner.disclaimer": "Аналитический рейтинг: оценки не являются вероятностями или инструкциями к исполнению.",
})
UK.update({
    "help.disclaimer": "Підтримка рішень на основі даних — не право торгувати й не обіцянка прибутку.",
    "help.market": "аналіз ринку, структура, ліквідність і публічні дані",
    "help.trading": "список спостереження, журнал, повтор і PAPER-позиції",
    "help.copy": "налаштування, виконання та статистика PAPER-копіювання",
    "help.intelligence": "рейтинг, якість, суперечності та AI-порадник",
    "help.research": "пошук переваг, когорти та форвардна перевірка",
    "help.system": "стан сервісу та свіжість даних", "help.account": "профіль, мова, налаштування, ліміти й плани",
    "help.premium": "можливості Free, Pro та Elite", "help.settings": "мова, сповіщення й формат",
    "help.scanner": "ранжовані можливості та прозорі фільтри",
    "help.watchlist": "стан і редагування списку", "help.alerts": "категорії та доставка сповіщень",
    "help.ai": "AI-спостереження, ідентичність, вартість та історія",
    "help.live": "готовність, ризик, звірка та стан LIVE користувача",
    "menu.help": "Довідка команд", "menu.analyze": "Аналіз ринку", "menu.scanner": "Рейтинг можливостей",
    "menu.watchlist": "Відстежувані ринки", "menu.trade": "Повтор угоди та журнал",
    "menu.copy": "Огляд PAPER-копіювання", "menu.positions": "PAPER-позиції",
    "menu.signal_rankings": "Рейтинг сигналів", "menu.research": "Дослідницький центр",
    "menu.profile": "Профіль і план", "menu.premium": "Плани та можливості",
    "menu.start": "Відкрити Liquidity Vision", "menu.journal": "Журнал угод",
    "menu.rankings": "Рейтинг сигналів", "menu.settings": "Налаштування", "menu.alerts": "Сповіщення",
    "watchlist.empty": "Ваш список спостереження порожній.", "watchlist.error": "Ринкові дані тимчасово недоступні",
    "watchlist.checked": "Перевірено: {value}", "watchlist.waiting": "очікується перший цикл",
    "scanner.no_results": "Немає можливостей, що відповідають фільтру.",
    "scanner.disclaimer": "Аналітичний рейтинг: оцінки не є ймовірностями чи інструкціями до виконання.",
})
HE.update({
    "help.disclaimer": "תמיכה בהחלטות מבוססות ראיות — לא סמכות מסחר ולא הבטחת רווח.",
    "help.market": "ניתוח שוק, מבנה, נזילות ונתוני שוק ציבוריים",
    "help.trading": "רשימת מעקב, יומן, שחזור ועסקאות PAPER", "help.copy": "הגדרות, ביצוע וסטטיסטיקת העתקת PAPER",
    "help.intelligence": "דירוג, איכות, סתירות וייעוץ AI", "help.research": "מחקר יתרון, קבוצות ובדיקות קדימה",
    "help.system": "בריאות השירות ורעננות הנתונים", "help.account": "פרופיל, שפה, הגדרות, שימוש ותוכניות",
    "help.premium": "יכולות Free, Pro ו־Elite", "help.settings": "שפה, התראות ותצוגה",
    "help.scanner": "הזדמנויות מדורגות ומסננים שקופים", "help.watchlist": "מצב ועריכת רשימת המעקב",
    "help.alerts": "קטגוריות ומסירת התראות", "help.ai": "תצפיות AI, זהות, עלות והיסטוריה",
    "help.live": "מוכנות, סיכון, התאמה ומצב LIVE לכל משתמש",
    "menu.help": "עזרה ופקודות", "menu.analyze": "ניתוח שוק", "menu.scanner": "דירוג הזדמנויות",
    "menu.watchlist": "שווקים במעקב", "menu.trade": "שחזור עסקה ויומן", "menu.copy": "סקירת העתקת PAPER",
    "menu.positions": "פוזיציות PAPER", "menu.signal_rankings": "דירוג אותות", "menu.research": "מרכז מחקר",
    "menu.profile": "פרופיל ותוכנית", "menu.premium": "תוכניות ויכולות",
    "menu.start": "פתיחת Liquidity Vision", "menu.journal": "יומן מסחר",
    "menu.rankings": "דירוג אותות", "menu.settings": "הגדרות", "menu.alerts": "התראות",
    "watchlist.empty": "רשימת המעקב ריקה.", "watchlist.error": "נתוני השוק אינם זמינים זמנית",
    "watchlist.checked": "נבדק: {value}", "watchlist.waiting": "ממתין למחזור הראשון",
    "scanner.no_results": "אין הזדמנות שמתאימה למסנן.",
    "scanner.disclaimer": "דירוג אנליטי בלבד; הציונים אינם הסתברויות או הוראות ביצוע.",
})
AR.update({
    "help.disclaimer": "دعم قرار قائم على الأدلة — وليس صلاحية تداول أو وعداً بالربح.",
    "help.market": "تحليل السوق والبنية والسيولة والبيانات العامة",
    "help.trading": "قائمة المراقبة والسجل وإعادة العرض وصفقات PAPER", "help.copy": "إعداد وتنفيذ وإحصاءات نسخ PAPER",
    "help.intelligence": "الترتيب والجودة والتعارضات واستشارة AI", "help.research": "بحث الميزة والمجموعات والتحقق المستقبلي",
    "help.system": "صحة الخدمة وحداثة البيانات", "help.account": "الملف واللغة والإعدادات والاستخدام والخطط",
    "help.premium": "إمكانات Free وPro وElite", "help.settings": "اللغة والتنبيهات وطريقة العرض",
    "help.scanner": "فرص مرتبة ومرشحات شفافة", "help.watchlist": "حالة قائمة المراقبة وتحريرها",
    "help.alerts": "فئات التنبيه والتسليم", "help.ai": "ملاحظات AI والهوية والتكلفة والسجل",
    "help.live": "جاهزية ومخاطر وتسوية وحالة LIVE لكل مستخدم",
    "menu.help": "المساعدة والأوامر", "menu.analyze": "تحليل سوق", "menu.scanner": "ترتيب الفرص",
    "menu.watchlist": "الأسواق المتابعة", "menu.trade": "إعادة الصفقة والسجل", "menu.copy": "نظرة على نسخ PAPER",
    "menu.positions": "مراكز PAPER", "menu.signal_rankings": "ترتيب الإشارات", "menu.research": "مركز الأبحاث",
    "menu.profile": "الملف والخطة", "menu.premium": "الخطط والإمكانات",
    "menu.start": "فتح Liquidity Vision", "menu.journal": "سجل التداول",
    "menu.rankings": "ترتيب الإشارات", "menu.settings": "الإعدادات", "menu.alerts": "التنبيهات",
    "watchlist.empty": "قائمة المراقبة فارغة.", "watchlist.error": "بيانات السوق غير متاحة مؤقتاً",
    "watchlist.checked": "آخر فحص: {value}", "watchlist.waiting": "بانتظار الدورة الأولى",
    "scanner.no_results": "لا توجد فرصة تطابق المرشح.",
    "scanner.disclaimer": "ترتيب تحليلي فقط؛ الدرجات ليست احتمالات أو تعليمات تنفيذ.",
})

EN.update({
    "lifecycle.TRIGGERED": "Price entered the preferred entry zone",
    "lifecycle.ACTIVE": "Setup activated", "lifecycle.TP1": "TP1 reached",
    "lifecycle.TP2": "TP2 reached", "lifecycle.TP3": "TP3 reached — lifecycle complete",
    "lifecycle.STOP": "Stop Loss reached", "lifecycle.BREAKEVEN": "Closed at break even",
    "lifecycle.INVALIDATED": "Setup invalidated before activation",
    "lifecycle.EXPIRED": "Setup expired without activation",
    "notify.signal_id": "Signal ID", "notify.price": "Price", "notify.status": "Status",
    "notify.current_move": "Current move", "notify.current_r": "Current R",
    "notify.duration": "Duration", "notify.next_reaction": "Next: waiting for a directional reaction candle.",
    "notify.entry": "Entry", "notify.stop": "Stop", "notify.targets": "Targets",
    "notify.next_target": "Next target", "notify.final_target": "Final target",
    "notify.break_even_active": "Stop automatically moved to Break Even.",
    "notify.realized": "Realized result", "notify.history": "Historical exact-setup context",
    "notify.samples": "Samples", "notify.reliability": "Reliability",
    "notify.state": "State", "notify.action": "Action", "notify.risk_used": "Risk used",
    "notify.to_stop": "Remaining to stop", "notify.risk_protected": "Risk protection",
    "notify.capital_at_risk": "Capital at risk", "notify.historical_model": "Historical model",
    "notify.commentary": "Commentary", "notify.update": "UPDATE",
    "notify.confidence": "Confidence", "notify.full_history": "Full history",
    "notify.monitoring": "The trade remains under live monitoring.",
    "alert.provider.title": "Market-data degradation",
    "alert.provider.body": "{symbol} · {source} is temporarily degraded. Independent sources remain usable; missing evidence is shown as unavailable.",
    "alert.live.title": "LIVE safety alert · {exchange}",
    "alert.live.body": "Connection {account} is suspended. Reason: {reason}. New entries are blocked; existing positions were not auto-closed.",
    "help.live.lifecycle": "Safe lifecycle: NOT_CONNECTED → READ_ONLY_CONNECTED → PREFLIGHT_READY → LIVE_CERTIFIED → LIVE_ENABLED.",
    "help.live.boundary": "Connecting keys, certification, Premium, owner access, or AI does not enable trading. Only explicit user activation after every gate can enable LIVE.",
    "live.risk_warning": "Trading can lose money. Leverage magnifies losses. API automation can act quickly. Never grant withdrawal permission; you control the exchange account.",
})

RU.update({
    "lifecycle.TRIGGERED": "Цена вошла в предпочтительную зону входа", "lifecycle.ACTIVE": "Сетап активирован",
    "lifecycle.TP1": "TP1 достигнут", "lifecycle.TP2": "TP2 достигнут",
    "lifecycle.TP3": "TP3 достигнут — цикл завершён", "lifecycle.STOP": "Достигнут Stop Loss",
    "lifecycle.BREAKEVEN": "Закрыто в безубыток", "lifecycle.INVALIDATED": "Сетап отменён до активации",
    "lifecycle.EXPIRED": "Сетап истёк без активации", "notify.signal_id": "ID сигнала",
    "notify.price": "Цена", "notify.status": "Статус", "notify.current_move": "Текущее движение",
    "notify.current_r": "Текущий R", "notify.duration": "Длительность", "notify.entry": "Вход",
    "notify.stop": "Стоп", "notify.targets": "Цели", "notify.next_target": "Следующая цель",
    "notify.final_target": "Финальная цель", "notify.state": "Состояние", "notify.action": "Действие",
    "notify.risk_used": "Использованный риск", "notify.to_stop": "До стопа",
    "notify.historical_model": "Историческая модель", "notify.commentary": "Комментарий",
    "notify.update": "ОБНОВЛЕНИЕ", "notify.confidence": "Уверенность", "notify.full_history": "Полная история",
    "notify.monitoring": "Сделка остаётся под наблюдением.", "alert.provider.title": "Ухудшение рыночных данных",
    "alert.provider.body": "{symbol} · {source} временно недоступен. Независимые источники продолжают работать; отсутствующие данные отмечаются как недоступные.",
    "alert.live.title": "Предупреждение безопасности LIVE · {exchange}",
    "alert.live.body": "Подключение {account} приостановлено. Причина: {reason}. Новые входы заблокированы; открытые позиции не закрывались автоматически.",
    "help.live.lifecycle": "Безопасный цикл: NOT_CONNECTED → READ_ONLY_CONNECTED → PREFLIGHT_READY → LIVE_CERTIFIED → LIVE_ENABLED.",
    "help.live.boundary": "Подключение ключей, сертификация, Premium, владелец или AI не включают торговлю. LIVE включается только явным действием пользователя после всех проверок.",
    "live.risk_warning": "Торговля может привести к убыткам. Плечо увеличивает потери. API действует быстро. Никогда не давайте право вывода средств; счёт контролируете вы.",
})

UK.update({
    "lifecycle.TRIGGERED": "Ціна увійшла в бажану зону входу", "lifecycle.ACTIVE": "Сетап активовано",
    "lifecycle.TP1": "TP1 досягнуто", "lifecycle.TP2": "TP2 досягнуто",
    "lifecycle.TP3": "TP3 досягнуто — цикл завершено", "lifecycle.STOP": "Досягнуто Stop Loss",
    "lifecycle.BREAKEVEN": "Закрито в беззбиток", "lifecycle.INVALIDATED": "Сетап скасовано до активації",
    "lifecycle.EXPIRED": "Сетап завершився без активації", "notify.signal_id": "ID сигналу",
    "notify.price": "Ціна", "notify.status": "Статус", "notify.current_move": "Поточний рух",
    "notify.current_r": "Поточний R", "notify.duration": "Тривалість", "notify.entry": "Вхід",
    "notify.stop": "Стоп", "notify.targets": "Цілі", "notify.next_target": "Наступна ціль",
    "notify.final_target": "Фінальна ціль", "notify.state": "Стан", "notify.action": "Дія",
    "notify.risk_used": "Використаний ризик", "notify.to_stop": "До стопа",
    "notify.historical_model": "Історична модель", "notify.commentary": "Коментар",
    "notify.update": "ОНОВЛЕННЯ", "notify.confidence": "Впевненість", "notify.full_history": "Повна історія",
    "notify.monitoring": "Угода залишається під наглядом.", "alert.provider.title": "Погіршення ринкових даних",
    "alert.provider.body": "{symbol} · {source} тимчасово недоступне. Незалежні джерела працюють; відсутні дані позначено як недоступні.",
    "alert.live.title": "Попередження безпеки LIVE · {exchange}",
    "alert.live.body": "Підключення {account} призупинено. Причина: {reason}. Нові входи заблоковано; відкриті позиції автоматично не закривались.",
    "help.live.lifecycle": "Безпечний цикл: NOT_CONNECTED → READ_ONLY_CONNECTED → PREFLIGHT_READY → LIVE_CERTIFIED → LIVE_ENABLED.",
    "help.live.boundary": "Ключі, сертифікація, Premium, власник або AI не вмикають торгівлю. LIVE вмикає лише користувач після всіх перевірок.",
    "live.risk_warning": "Торгівля може спричинити збитки. Плече їх збільшує. API діє швидко. Ніколи не надавайте право виведення; рахунок контролюєте ви.",
})

HE.update({
    "lifecycle.TRIGGERED": "המחיר נכנס לאזור הכניסה המועדף", "lifecycle.ACTIVE": "התרחיש הופעל",
    "lifecycle.TP1": "TP1 הושג", "lifecycle.TP2": "TP2 הושג", "lifecycle.TP3": "TP3 הושג — המחזור הושלם",
    "lifecycle.STOP": "Stop Loss הושג", "lifecycle.BREAKEVEN": "נסגר באיזון",
    "lifecycle.INVALIDATED": "התרחיש נפסל לפני הפעלה", "lifecycle.EXPIRED": "התרחיש פג ללא הפעלה",
    "notify.signal_id": "מזהה אות", "notify.price": "מחיר", "notify.status": "מצב",
    "notify.current_move": "תנועה נוכחית", "notify.current_r": "R נוכחי", "notify.duration": "משך",
    "notify.entry": "כניסה", "notify.stop": "עצירה", "notify.targets": "יעדים",
    "notify.next_target": "היעד הבא", "notify.final_target": "היעד האחרון", "notify.state": "מצב",
    "notify.action": "פעולה", "notify.risk_used": "סיכון שנוצל", "notify.to_stop": "מרחק לעצירה",
    "notify.historical_model": "מודל היסטורי", "notify.commentary": "הערה", "notify.update": "עדכון",
    "notify.confidence": "ביטחון", "notify.full_history": "היסטוריה מלאה",
    "notify.monitoring": "העסקה נשארת במעקב.", "alert.provider.title": "פגיעה בנתוני השוק",
    "alert.provider.body": "{symbol} · {source} אינו זמין זמנית. מקורות עצמאיים נשארים פעילים; מידע חסר מסומן כלא זמין.",
    "alert.live.title": "התראת בטיחות LIVE · {exchange}",
    "alert.live.body": "החיבור {account} הושעה. סיבה: {reason}. כניסות חדשות חסומות; פוזיציות קיימות לא נסגרו אוטומטית.",
    "help.live.lifecycle": "מחזור בטוח: NOT_CONNECTED → READ_ONLY_CONNECTED → PREFLIGHT_READY → LIVE_CERTIFIED → LIVE_ENABLED.",
    "help.live.boundary": "חיבור מפתחות, הסמכה, Premium, בעלים או AI אינם מפעילים מסחר. רק המשתמש מפעיל LIVE לאחר כל הבדיקות.",
    "live.risk_warning": "מסחר עלול לגרום להפסד. מינוף מגדיל הפסדים. API פועל במהירות. אין לתת הרשאת משיכה; החשבון בשליטתך.",
})

AR.update({
    "lifecycle.TRIGGERED": "دخل السعر منطقة الدخول المفضلة", "lifecycle.ACTIVE": "تم تفعيل الإعداد",
    "lifecycle.TP1": "تم بلوغ TP1", "lifecycle.TP2": "تم بلوغ TP2", "lifecycle.TP3": "تم بلوغ TP3 — اكتملت الدورة",
    "lifecycle.STOP": "تم بلوغ Stop Loss", "lifecycle.BREAKEVEN": "أُغلق عند التعادل",
    "lifecycle.INVALIDATED": "أُلغي الإعداد قبل التفعيل", "lifecycle.EXPIRED": "انتهى الإعداد دون تفعيل",
    "notify.signal_id": "معرّف الإشارة", "notify.price": "السعر", "notify.status": "الحالة",
    "notify.current_move": "الحركة الحالية", "notify.current_r": "R الحالي", "notify.duration": "المدة",
    "notify.entry": "الدخول", "notify.stop": "الإيقاف", "notify.targets": "الأهداف",
    "notify.next_target": "الهدف التالي", "notify.final_target": "الهدف الأخير", "notify.state": "الحالة",
    "notify.action": "الإجراء", "notify.risk_used": "المخاطرة المستخدمة", "notify.to_stop": "المتبقي للإيقاف",
    "notify.historical_model": "النموذج التاريخي", "notify.commentary": "تعليق", "notify.update": "تحديث",
    "notify.confidence": "الثقة", "notify.full_history": "السجل الكامل",
    "notify.monitoring": "تظل الصفقة قيد المراقبة.", "alert.provider.title": "تدهور بيانات السوق",
    "alert.provider.body": "{symbol} · {source} غير متاح مؤقتاً. تبقى المصادر المستقلة صالحة؛ وتُعرض البيانات المفقودة كغير متاحة.",
    "alert.live.title": "تنبيه أمان LIVE · {exchange}",
    "alert.live.body": "تم تعليق الاتصال {account}. السبب: {reason}. مُنعت المداخل الجديدة؛ ولم تُغلق المراكز الحالية تلقائياً.",
    "help.live.lifecycle": "المسار الآمن: NOT_CONNECTED → READ_ONLY_CONNECTED → PREFLIGHT_READY → LIVE_CERTIFIED → LIVE_ENABLED.",
    "help.live.boundary": "ربط المفاتيح أو الاعتماد أو Premium أو المالك أو AI لا يفعّل التداول. لا يفعّل LIVE إلا المستخدم بعد اجتياز كل الضوابط.",
    "live.risk_warning": "قد يسبب التداول خسائر. الرافعة تضخمها. يعمل API بسرعة. لا تمنح إذن السحب أبداً؛ أنت تتحكم في حسابك.",
})

# v10.4 safety-critical LIVE and asynchronous alert surfaces. Technical state,
# command and strategy identifiers remain stable and are rendered as LTR islands.
EN.update({
    "live.private_only": "This LIVE action is available only in a private chat.",
    "live.fail_closed": "The request failed closed. No new exposure was created.",
    "live.copy.title": "LIVE copy settings · {exchange}",
    "live.copy.enabled": "Enabled", "live.copy.symbols": "Symbols",
    "live.copy.filters": "Strategies / timeframes / directions",
    "live.copy.minimum_quality": "Minimum Quality", "live.copy.sizing": "Sizing",
    "live.copy.ceilings": "Exposure / leverage ceilings",
    "live.copy.boundary": "These preferences are subordinate to server risk, authoritative daily PnL, reconciliation and kill switches.",
    "live.daily.title": "LIVE daily PnL · UTC {bucket}", "live.state": "State",
    "live.daily.values": "Realized / fees / unrealized", "live.daily.loss_basis": "Loss basis",
    "live.source": "Source", "live.observed": "Observed",
    "live.performance.title": "LIVE performance · separate from PAPER",
    "live.performance.executions": "Executions / filled / rejected",
    "live.performance.fees": "Known execution fees",
    "live.performance.authoritative": "Latest authoritative realized / fees",
    "live.performance.queue": "Queue states",
    "live.performance.boundary": "PAPER and LIVE metrics are never merged.",
    "live.emergency.preview": "LIVE emergency-close preview · {exchange}",
    "live.emergency.fingerprint": "Account fingerprint",
    "live.emergency.exposure": "Estimated exposure",
    "live.emergency.warning": "This attempts reduce-only closure and is separate from the kill switch. Confirm before expiry:",
    "live.emergency.no_pending": "No matching pending emergency-close confirmation.",
    "live.emergency.result": "Emergency-close result", "live.emergency.submissions": "Submissions",
    "live.emergency.remaining": "remaining positions",
    "live.emergency.truth": "No closure is assumed until exchange state confirms it.",
    "live.preflight.title": "LIVE preflight · {exchange}",
    "live.preflight.credentials": "Credentials present", "live.preflight.confirmed": "Two-step confirmed",
    "live.preflight.enabled": "Account enabled", "live.preflight.kill": "Connection safety switch",
    "live.preflight.unresolved": "Unresolved/retry executions",
    "live.preflight.limits": "Max order / exposure / leverage", "live.preflight.readiness": "Readiness",
    "live.preflight.reasons": "Reasons",
    "live.preflight.boundary": "LIVE remains fail-closed until every server-side gate passes. The connection safety switch remains armed before activation.",
    "live.reconciliation.title": "LIVE reconciliation · {exchange}",
    "live.reconciliation.mismatches": "Mismatches", "live.reconciliation.blocked": "New entries blocked",
    "live.reconciliation.truth": "Exchange state is authoritative; economic mismatches are never silently repaired.",
    "live.enable.title": "Explicit LIVE activation · {exchange}",
    "live.enable.boundary": "Connecting credentials, Premium, PAPER copy and certification do not activate LIVE.",
    "live.enable.on": "LIVE is now ON for connection {account}.",
    "live.enable.off": "LIVE remains OFF: {reason}",
    "notify.position_secured": "Position secured", "notify.stop_to_break_even": "Stop moved to Break Even",
    "notify.break_even_explainer": "If price returns to entry, the lifecycle closes as BREAKEVEN instead of STOP.",
    "alert.watch.title": "Watch update", "alert.watch.bias": "Bias",
    "alert.watch.recommendation": "Recommendation", "alert.watch.quality": "Quality / Readiness",
    "alert.watch.strategy": "Strategy", "alert.watch.regime": "Regime",
    "alert.watch.PRICE_MOVE": "Material price move detected", "alert.watch.DIRECTION_CHANGE": "Directional bias changed",
    "alert.watch.READINESS_CHANGE": "Entry readiness changed", "alert.watch.QUALITY_CHANGE": "Signal Quality changed",
    "alert.watch.WALL_REMOVED": "A bounded liquidity wall disappeared", "alert.watch.WALL_REPLENISHED": "A bounded liquidity wall replenished",
    "alert.watch.LIQUIDITY_SWEEP": "A bounded liquidity sweep was observed", "alert.watch.ORDER_BOOK_WALL_APPEARS": "A bounded liquidity concentration appeared",
    "alert.watch.MICROSTRUCTURE_CHANGE": "Microstructure state changed", "alert.watch.FUNDING_EXTREME": "Funding entered an extreme percentile",
    "alert.watch.OI_ACCELERATION": "Open interest is accelerating", "alert.watch.STRUCTURE_BREAK": "Market structure changed",
    "alert.watch.ENTRY_ZONE": "Price entered the preferred entry zone",
})

RU.update({
    "live.private_only": "Это действие LIVE доступно только в личном чате.", "live.fail_closed": "Запрос отклонён безопасно. Новая позиция не создана.",
    "live.copy.title": "Настройки LIVE-копирования · {exchange}", "live.copy.enabled": "Включено", "live.copy.symbols": "Символы",
    "live.copy.filters": "Стратегии / таймфреймы / направления", "live.copy.minimum_quality": "Минимальное качество", "live.copy.sizing": "Размер",
    "live.copy.ceilings": "Лимиты экспозиции / плеча", "live.copy.boundary": "Настройки подчиняются серверному риску, подтверждённому дневному PnL, сверке и аварийным переключателям.",
    "live.daily.title": "Дневной PnL LIVE · UTC {bucket}", "live.state": "Состояние", "live.daily.values": "Реализовано / комиссии / нереализовано",
    "live.daily.loss_basis": "База дневного убытка", "live.source": "Источник", "live.observed": "Время наблюдения",
    "live.performance.title": "Результаты LIVE · отдельно от PAPER", "live.performance.executions": "Исполнения / заполнено / отклонено",
    "live.performance.fees": "Известные комиссии", "live.performance.authoritative": "Последние подтверждённые реализовано / комиссии",
    "live.performance.queue": "Состояния очереди", "live.performance.boundary": "Метрики PAPER и LIVE не объединяются.",
    "live.emergency.preview": "Предпросмотр аварийного закрытия LIVE · {exchange}", "live.emergency.fingerprint": "Отпечаток счёта",
    "live.emergency.exposure": "Оценочная экспозиция", "live.emergency.warning": "Будет предпринята только reduce-only попытка закрытия, отдельно от kill switch. Подтвердите до истечения срока:",
    "live.emergency.no_pending": "Подходящего ожидающего подтверждения нет.", "live.emergency.result": "Результат аварийного закрытия",
    "live.emergency.submissions": "Отправлено", "live.emergency.remaining": "оставшиеся позиции", "live.emergency.truth": "Закрытие не считается выполненным до подтверждения состояния биржей.",
    "live.preflight.title": "Проверка LIVE · {exchange}", "live.preflight.credentials": "Ключи подключены", "live.preflight.confirmed": "Двухэтапное подтверждение",
    "live.preflight.enabled": "Счёт активирован", "live.preflight.kill": "Защитный переключатель подключения", "live.preflight.unresolved": "Неразрешённые/повторные исполнения",
    "live.preflight.limits": "Макс. ордер / экспозиция / плечо", "live.preflight.readiness": "Готовность", "live.preflight.reasons": "Причины",
    "live.preflight.boundary": "LIVE остаётся заблокированным до прохождения всех серверных проверок. До активации защитный переключатель подключения остаётся включён.",
    "live.reconciliation.title": "Сверка LIVE · {exchange}", "live.reconciliation.mismatches": "Расхождения", "live.reconciliation.blocked": "Новые входы заблокированы",
    "live.reconciliation.truth": "Состояние биржи авторитетно; экономические расхождения не исправляются молча.",
    "live.enable.title": "Явная активация LIVE · {exchange}", "live.enable.boundary": "Ключи, Premium, PAPER-копирование и сертификация не активируют LIVE.",
    "live.enable.on": "LIVE включён для подключения {account}.", "live.enable.off": "LIVE остаётся выключенным: {reason}",
    "notify.position_secured": "Позиция защищена", "notify.stop_to_break_even": "Стоп перенесён в безубыток", "notify.break_even_explainer": "При возврате к входу цикл завершится как BREAKEVEN, а не STOP.",
    "alert.watch.title": "Обновление наблюдения", "alert.watch.bias": "Смещение", "alert.watch.recommendation": "Рекомендация", "alert.watch.quality": "Качество / готовность",
    "alert.watch.strategy": "Стратегия", "alert.watch.regime": "Режим", "alert.watch.PRICE_MOVE": "Обнаружено существенное движение цены", "alert.watch.DIRECTION_CHANGE": "Изменилось направление",
    "alert.watch.READINESS_CHANGE": "Изменилась готовность входа", "alert.watch.QUALITY_CHANGE": "Изменилось качество сигнала", "alert.watch.WALL_REMOVED": "Ограниченная стена ликвидности исчезла",
    "alert.watch.WALL_REPLENISHED": "Стена ликвидности пополнилась", "alert.watch.LIQUIDITY_SWEEP": "Наблюдался ограниченный съём ликвидности", "alert.watch.ORDER_BOOK_WALL_APPEARS": "Появилась концентрация ликвидности",
    "alert.watch.MICROSTRUCTURE_CHANGE": "Изменилась микроструктура", "alert.watch.FUNDING_EXTREME": "Фандинг вошёл в экстремальный процентиль", "alert.watch.OI_ACCELERATION": "Открытый интерес ускоряется",
    "alert.watch.STRUCTURE_BREAK": "Изменилась структура рынка", "alert.watch.ENTRY_ZONE": "Цена вошла в предпочтительную зону входа",
})

UK.update({
    "live.private_only": "Ця дія LIVE доступна лише в приватному чаті.", "live.fail_closed": "Запит безпечно відхилено. Нову позицію не створено.",
    "live.copy.title": "Налаштування LIVE-копіювання · {exchange}", "live.copy.enabled": "Увімкнено", "live.copy.symbols": "Символи", "live.copy.filters": "Стратегії / таймфрейми / напрямки",
    "live.copy.minimum_quality": "Мінімальна якість", "live.copy.sizing": "Розмір", "live.copy.ceilings": "Ліміти експозиції / плеча", "live.copy.boundary": "Налаштування підпорядковані серверному ризику, підтвердженому денному PnL, звірці та аварійним перемикачам.",
    "live.daily.title": "Денний PnL LIVE · UTC {bucket}", "live.state": "Стан", "live.daily.values": "Реалізовано / комісії / нереалізовано", "live.daily.loss_basis": "База денного збитку", "live.source": "Джерело", "live.observed": "Час спостереження",
    "live.performance.title": "Результати LIVE · окремо від PAPER", "live.performance.executions": "Виконання / заповнено / відхилено", "live.performance.fees": "Відомі комісії", "live.performance.authoritative": "Останні підтверджені реалізовано / комісії", "live.performance.queue": "Стани черги", "live.performance.boundary": "Метрики PAPER та LIVE не об’єднуються.",
    "live.emergency.preview": "Перегляд аварійного закриття LIVE · {exchange}", "live.emergency.fingerprint": "Відбиток рахунку", "live.emergency.exposure": "Орієнтовна експозиція", "live.emergency.warning": "Буде виконано лише reduce-only спробу закриття, окремо від kill switch. Підтвердьте до завершення строку:", "live.emergency.no_pending": "Відповідного очікуваного підтвердження немає.", "live.emergency.result": "Результат аварійного закриття", "live.emergency.submissions": "Надіслано", "live.emergency.remaining": "позицій залишилось", "live.emergency.truth": "Закриття не вважається виконаним до підтвердження біржею.",
    "live.preflight.title": "Перевірка LIVE · {exchange}", "live.preflight.credentials": "Ключі підключено", "live.preflight.confirmed": "Двоетапне підтвердження", "live.preflight.enabled": "Рахунок активовано", "live.preflight.kill": "Захисний перемикач підключення", "live.preflight.unresolved": "Невирішені/повторні виконання", "live.preflight.limits": "Макс. ордер / експозиція / плече", "live.preflight.readiness": "Готовність", "live.preflight.reasons": "Причини", "live.preflight.boundary": "LIVE залишається заблокованим до проходження всіх серверних перевірок. До активації захисний перемикач підключення залишається ввімкненим.",
    "live.reconciliation.title": "Звірка LIVE · {exchange}", "live.reconciliation.mismatches": "Розбіжності", "live.reconciliation.blocked": "Нові входи заблоковано", "live.reconciliation.truth": "Стан біржі авторитетний; економічні розбіжності не виправляються мовчки.",
    "live.enable.title": "Явна активація LIVE · {exchange}", "live.enable.boundary": "Ключі, Premium, PAPER-копіювання та сертифікація не активують LIVE.", "live.enable.on": "LIVE увімкнено для підключення {account}.", "live.enable.off": "LIVE залишається вимкненим: {reason}",
    "notify.position_secured": "Позицію захищено", "notify.stop_to_break_even": "Стоп перенесено в беззбиток", "notify.break_even_explainer": "При поверненні до входу цикл завершиться як BREAKEVEN, а не STOP.",
    "alert.watch.title": "Оновлення спостереження", "alert.watch.bias": "Ухил", "alert.watch.recommendation": "Рекомендація", "alert.watch.quality": "Якість / готовність", "alert.watch.strategy": "Стратегія", "alert.watch.regime": "Режим", "alert.watch.PRICE_MOVE": "Виявлено істотний рух ціни", "alert.watch.DIRECTION_CHANGE": "Змінився напрямок", "alert.watch.READINESS_CHANGE": "Змінилася готовність входу", "alert.watch.QUALITY_CHANGE": "Змінилася якість сигналу", "alert.watch.WALL_REMOVED": "Обмежена стіна ліквідності зникла", "alert.watch.WALL_REPLENISHED": "Стіна ліквідності поповнилась", "alert.watch.LIQUIDITY_SWEEP": "Спостерігався обмежений знім ліквідності", "alert.watch.ORDER_BOOK_WALL_APPEARS": "З’явилась концентрація ліквідності", "alert.watch.MICROSTRUCTURE_CHANGE": "Змінилась мікроструктура", "alert.watch.FUNDING_EXTREME": "Фандинг увійшов в екстремальний процентиль", "alert.watch.OI_ACCELERATION": "Відкритий інтерес прискорюється", "alert.watch.STRUCTURE_BREAK": "Змінилась структура ринку", "alert.watch.ENTRY_ZONE": "Ціна увійшла в бажану зону входу",
})

HE.update({
    "live.private_only": "פעולת LIVE זו זמינה רק בצ׳אט פרטי.", "live.fail_closed": "הבקשה נחסמה בבטחה. לא נוצרה חשיפה חדשה.",
    "live.copy.title": "הגדרות העתקת LIVE · {exchange}", "live.copy.enabled": "מופעל", "live.copy.symbols": "סמלים", "live.copy.filters": "אסטרטגיות / טווחים / כיוונים", "live.copy.minimum_quality": "איכות מינימלית", "live.copy.sizing": "גודל", "live.copy.ceilings": "תקרות חשיפה / מינוף", "live.copy.boundary": "ההעדפות כפופות לסיכון השרת, PnL יומי מאומת, התאמה ומתגי חירום.",
    "live.daily.title": "PnL יומי של LIVE · UTC {bucket}", "live.state": "מצב", "live.daily.values": "ממומש / עמלות / לא ממומש", "live.daily.loss_basis": "בסיס הפסד יומי", "live.source": "מקור", "live.observed": "נצפה",
    "live.performance.title": "ביצועי LIVE · בנפרד מ־PAPER", "live.performance.executions": "ביצועים / מולאו / נדחו", "live.performance.fees": "עמלות ידועות", "live.performance.authoritative": "מימוש / עמלות מאומתים אחרונים", "live.performance.queue": "מצבי תור", "live.performance.boundary": "מדדי PAPER ו־LIVE אינם מתמזגים.",
    "live.emergency.preview": "תצוגת סגירת חירום LIVE · {exchange}", "live.emergency.fingerprint": "טביעת חשבון", "live.emergency.exposure": "חשיפה משוערת", "live.emergency.warning": "זהו ניסיון סגירה reduce-only, בנפרד ממתג החסימה. יש לאשר לפני התפוגה:", "live.emergency.no_pending": "לא נמצא אישור חירום ממתין מתאים.", "live.emergency.result": "תוצאת סגירת חירום", "live.emergency.submissions": "שליחות", "live.emergency.remaining": "פוזיציות שנותרו", "live.emergency.truth": "אין להניח שהסגירה הושלמה עד שאמת הבורסה מאשרת זאת.",
    "live.preflight.title": "בדיקת LIVE · {exchange}", "live.preflight.credentials": "פרטי גישה קיימים", "live.preflight.confirmed": "אישור דו־שלבי", "live.preflight.enabled": "חשבון מופעל", "live.preflight.kill": "מתג בטיחות חיבור", "live.preflight.unresolved": "ביצועים לא פתורים/חוזרים", "live.preflight.limits": "מקס׳ הוראה / חשיפה / מינוף", "live.preflight.readiness": "מוכנות", "live.preflight.reasons": "סיבות", "live.preflight.boundary": "LIVE נשאר חסום עד שכל בדיקות השרת עוברות. מתג בטיחות החיבור נשאר פעיל לפני ההפעלה.",
    "live.reconciliation.title": "התאמת LIVE · {exchange}", "live.reconciliation.mismatches": "אי־התאמות", "live.reconciliation.blocked": "כניסות חדשות חסומות", "live.reconciliation.truth": "מצב הבורסה הוא המקור הקובע; אי־התאמות כלכליות אינן מתוקנות בשקט.",
    "live.enable.title": "הפעלת LIVE מפורשת · {exchange}", "live.enable.boundary": "חיבור פרטים, Premium, העתקת PAPER והסמכה אינם מפעילים LIVE.", "live.enable.on": "LIVE הופעל עבור חיבור {account}.", "live.enable.off": "LIVE נשאר כבוי: {reason}",
    "notify.position_secured": "הפוזיציה מוגנת", "notify.stop_to_break_even": "העצירה הועברה לאיזון", "notify.break_even_explainer": "אם המחיר יחזור לכניסה, המחזור ייסגר כ־BREAKEVEN ולא STOP.",
    "alert.watch.title": "עדכון מעקב", "alert.watch.bias": "הטיה", "alert.watch.recommendation": "המלצה", "alert.watch.quality": "איכות / מוכנות", "alert.watch.strategy": "אסטרטגיה", "alert.watch.regime": "משטר", "alert.watch.PRICE_MOVE": "זוהתה תנועת מחיר מהותית", "alert.watch.DIRECTION_CHANGE": "הכיוון השתנה", "alert.watch.READINESS_CHANGE": "מוכנות הכניסה השתנתה", "alert.watch.QUALITY_CHANGE": "איכות האות השתנתה", "alert.watch.WALL_REMOVED": "קיר נזילות תחום נעלם", "alert.watch.WALL_REPLENISHED": "קיר נזילות התמלא מחדש", "alert.watch.LIQUIDITY_SWEEP": "נצפתה סריקת נזילות תחומה", "alert.watch.ORDER_BOOK_WALL_APPEARS": "הופיע ריכוז נזילות תחום", "alert.watch.MICROSTRUCTURE_CHANGE": "מצב המיקרו־מבנה השתנה", "alert.watch.FUNDING_EXTREME": "המימון נכנס לאחוזון קיצון", "alert.watch.OI_ACCELERATION": "הריבית הפתוחה מואצת", "alert.watch.STRUCTURE_BREAK": "מבנה השוק השתנה", "alert.watch.ENTRY_ZONE": "המחיר נכנס לאזור הכניסה המועדף",
})

AR.update({
    "live.private_only": "إجراء LIVE هذا متاح في محادثة خاصة فقط.", "live.fail_closed": "فشل الطلب بأمان. لم يتم إنشاء تعرض جديد.",
    "live.copy.title": "إعدادات نسخ LIVE · {exchange}", "live.copy.enabled": "مفعّل", "live.copy.symbols": "الرموز", "live.copy.filters": "الاستراتيجيات / الأطر / الاتجاهات", "live.copy.minimum_quality": "الحد الأدنى للجودة", "live.copy.sizing": "الحجم", "live.copy.ceilings": "حدود التعرض / الرافعة", "live.copy.boundary": "تخضع التفضيلات لمخاطر الخادم وPnL اليومي الموثوق والتسوية ومفاتيح الإيقاف.",
    "live.daily.title": "PnL اليومي لـ LIVE · UTC {bucket}", "live.state": "الحالة", "live.daily.values": "المحقق / الرسوم / غير المحقق", "live.daily.loss_basis": "أساس الخسارة اليومية", "live.source": "المصدر", "live.observed": "وقت الرصد",
    "live.performance.title": "أداء LIVE · منفصل عن PAPER", "live.performance.executions": "التنفيذات / الممتلئة / المرفوضة", "live.performance.fees": "رسوم التنفيذ المعروفة", "live.performance.authoritative": "آخر محقق / رسوم موثوقة", "live.performance.queue": "حالات الطابور", "live.performance.boundary": "لا تُدمج مقاييس PAPER وLIVE.",
    "live.emergency.preview": "معاينة الإغلاق الطارئ LIVE · {exchange}", "live.emergency.fingerprint": "بصمة الحساب", "live.emergency.exposure": "التعرض المقدر", "live.emergency.warning": "هذه محاولة إغلاق reduce-only منفصلة عن مفتاح الإيقاف. أكّد قبل انتهاء الصلاحية:", "live.emergency.no_pending": "لا يوجد تأكيد طارئ معلّق مطابق.", "live.emergency.result": "نتيجة الإغلاق الطارئ", "live.emergency.submissions": "الإرسالات", "live.emergency.remaining": "المراكز المتبقية", "live.emergency.truth": "لا يُفترض الإغلاق حتى تؤكده حالة المنصة.",
    "live.preflight.title": "فحص LIVE · {exchange}", "live.preflight.credentials": "بيانات الاعتماد موجودة", "live.preflight.confirmed": "تأكيد بخطوتين", "live.preflight.enabled": "الحساب مفعّل", "live.preflight.kill": "مفتاح أمان الاتصال", "live.preflight.unresolved": "تنفيذات غير محلولة/معادة", "live.preflight.limits": "أقصى أمر / تعرض / رافعة", "live.preflight.readiness": "الجاهزية", "live.preflight.reasons": "الأسباب", "live.preflight.boundary": "يبقى LIVE مغلقاً حتى تمر كل ضوابط الخادم. يبقى مفتاح أمان الاتصال مفعلاً قبل التنشيط.",
    "live.reconciliation.title": "تسوية LIVE · {exchange}", "live.reconciliation.mismatches": "عدم التطابق", "live.reconciliation.blocked": "المداخل الجديدة محظورة", "live.reconciliation.truth": "حالة المنصة هي المرجع؛ ولا تُصلح الفروق الاقتصادية بصمت.",
    "live.enable.title": "تنشيط LIVE الصريح · {exchange}", "live.enable.boundary": "ربط البيانات أو Premium أو نسخ PAPER أو الاعتماد لا ينشّط LIVE.", "live.enable.on": "تم تشغيل LIVE للاتصال {account}.", "live.enable.off": "يبقى LIVE متوقفاً: {reason}",
    "notify.position_secured": "تم تأمين المركز", "notify.stop_to_break_even": "نُقل الإيقاف إلى التعادل", "notify.break_even_explainer": "إذا عاد السعر للدخول تُغلق الدورة كـ BREAKEVEN بدلاً من STOP.",
    "alert.watch.title": "تحديث المراقبة", "alert.watch.bias": "التحيز", "alert.watch.recommendation": "التوصية", "alert.watch.quality": "الجودة / الجاهزية", "alert.watch.strategy": "الاستراتيجية", "alert.watch.regime": "النظام", "alert.watch.PRICE_MOVE": "رُصد تحرك سعري جوهري", "alert.watch.DIRECTION_CHANGE": "تغير الاتجاه", "alert.watch.READINESS_CHANGE": "تغيرت جاهزية الدخول", "alert.watch.QUALITY_CHANGE": "تغيرت جودة الإشارة", "alert.watch.WALL_REMOVED": "اختفى جدار سيولة محدود", "alert.watch.WALL_REPLENISHED": "تجدد جدار السيولة", "alert.watch.LIQUIDITY_SWEEP": "رُصد مسح سيولة محدود", "alert.watch.ORDER_BOOK_WALL_APPEARS": "ظهر تركز سيولة محدود", "alert.watch.MICROSTRUCTURE_CHANGE": "تغيرت حالة البنية الدقيقة", "alert.watch.FUNDING_EXTREME": "دخل التمويل نطاقاً مئوياً متطرفاً", "alert.watch.OI_ACCELERATION": "الفائدة المفتوحة تتسارع", "alert.watch.STRUCTURE_BREAK": "تغير هيكل السوق", "alert.watch.ENTRY_ZONE": "دخل السعر منطقة الدخول المفضلة",
})

TRANSLATIONS: dict[str, dict[str, str]] = {"en": EN, "ru": RU, "uk": UK, "he": HE, "ar": AR}
V104_PRIMARY_KEYS = frozenset(
    key for key in EN
    if key.startswith(("live.", "lifecycle.", "alert.watch.", "alert.provider.",
                       "alert.live.", "help.live."))
)


class LocalizationService:
    def __init__(self) -> None:
        self.preferences = UserPreferenceService()

    @staticmethod
    def normalize_language(value: str | None) -> str | None:
        raw = str(value or "").strip().lower()
        candidate = LANGUAGE_ALIASES.get(raw, raw)
        return candidate if candidate in SUPPORTED_LANGUAGES else None

    def language(self, telegram_id: int | None) -> str:
        if telegram_id is None:
            return "en"
        return self.normalize_language(self.preferences.get(telegram_id).get("language")) or "en"

    def set_language(self, telegram_id: int, language: str) -> str:
        normalized = self.normalize_language(language)
        if normalized is None:
            raise ValueError("UNSUPPORTED_LANGUAGE")
        self.preferences.update(telegram_id, language=normalized)
        return normalized

    def t(self, key: str, *, language: str = "en", **values: Any) -> str:
        locale = self.normalize_language(language) or "en"
        localized = TRANSLATIONS.get(locale, {}).get(key)
        template = localized or EN.get(key) or EN["common.unavailable"]
        if localized is None and locale != "en":
            logging.warning("localization_missing_key locale=%s key=%s fallback=en", locale, key)
        if key not in EN:
            logging.warning("localization_unknown_key locale=%s key=%s", locale, key)
        try:
            return template.format_map(_SafeValues(values))
        except (ValueError, KeyError):
            return EN["common.unavailable"]

    def user_t(self, telegram_id: int | None, key: str, **values: Any) -> str:
        return self.t(key, language=self.language(telegram_id), **values)

    @staticmethod
    def is_rtl(language: str) -> bool:
        return language in RTL_LANGUAGES

    def market_token(self, value: Any, *, language: str, html: bool = False) -> str:
        rendered = escape(str(value)) if html else str(value)
        if self.is_rtl(language):
            rendered = f"\u2066{rendered}\u2069"
        return f"<code>{rendered}</code>" if html else rendered

    def ltr(self, value: Any, *, language: str, html: bool = False) -> str:
        return self.market_token(value, language=language, html=html)

    def freshness(self, value: str | None, *, language: str) -> str:
        if not value:
            return self.t("watchlist.waiting", language=language)
        try:
            timestamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            if timestamp.tzinfo is None:
                timestamp = timestamp.replace(tzinfo=timezone.utc)
            seconds = max(0, int((datetime.now(timezone.utc) - timestamp).total_seconds()))
        except (TypeError, ValueError):
            return self.t("common.unavailable", language=language)
        age = f"{seconds}s" if seconds < 60 else f"{seconds // 60}m" if seconds < 3600 else f"{seconds // 3600}h"
        key = "common.stale" if seconds > 180 else "common.updated"
        return self.t(key, language=language, age=self.market_token(age, language=language))

    @staticmethod
    def explicit_coverage(language: str, keys: set[str] | frozenset[str] | None = None) -> float:
        selected = set(keys or EN)
        if not selected:
            return 100.0
        translated = sum(key in TRANSLATIONS.get(language, {}) for key in selected)
        return round(translated / len(selected) * 100, 2)

    @staticmethod
    def coverage_report(keys: set[str] | frozenset[str] | None = None) -> dict[str, Any]:
        selected = sorted(set(keys or EN))
        locales = {}
        for locale in SUPPORTED_LANGUAGES:
            missing = [key for key in selected if key not in TRANSLATIONS.get(locale, {})]
            locales[locale] = {"translated": len(selected) - len(missing),
                               "total": len(selected),
                               "coverage_pct": round((len(selected) - len(missing)) / max(1, len(selected)) * 100, 2),
                               "missing": missing}
        return {"total_core_keys": len(selected), "locales": locales,
                "fallback_order": "SELECTED_LOCALE_THEN_ENGLISH", "translation_key_leak": False}


class _SafeValues(dict):
    def __missing__(self, key: str) -> str:
        return ""
