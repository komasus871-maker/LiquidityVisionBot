from __future__ import annotations

from html import escape
from datetime import datetime, timezone
from typing import Any

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

TRANSLATIONS: dict[str, dict[str, str]] = {"en": EN, "ru": RU, "uk": UK, "he": HE, "ar": AR}


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
        template = TRANSLATIONS.get(locale, {}).get(key) or EN.get(key) or EN["common.unavailable"]
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


class _SafeValues(dict):
    def __missing__(self, key: str) -> str:
        return ""
