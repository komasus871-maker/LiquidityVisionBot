import json
from aiogram import Router, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message, CallbackQuery

from keyboards.analyze import analyze_keyboard
from keyboards.analysis_actions import analysis_actions_keyboard
from keyboards.universal_analyze import timeframe_keyboard

from services.market import Market
from services.analyzer import Analyzer
from services.report import Report
from services.signal_recorder import SignalRecorder
from services.explainer import Explainer
from services.probability_engine import ProbabilityEngine
from services.similarity_report import SimilarityReport
from services.symbol_resolver import SymbolResolver
from services.user_watchlist import UserWatchlist

router = Router()

market = Market()
analyzer = Analyzer()
report = Report()
recorder = SignalRecorder()
explainer = Explainer()
probability_engine = ProbabilityEngine()
similarity_report = SimilarityReport()
resolver = SymbolResolver(market)
user_watchlist = UserWatchlist()


class UniversalAnalyzeState(StatesGroup):
    waiting_symbol = State()


async def _run_analysis(symbol: str, timeframe: str = "1h"):
    df = await market.get_klines(symbol, interval=timeframe)
    analysis = analyzer.analyze(df)
    setup_key = recorder._setup_key(analysis)
    analysis["timeframe"] = timeframe
    return probability_engine.enrich(
        analysis,
        symbol=symbol,
        timeframe=timeframe,
        setup_key=setup_key,
    )


async def _send_analysis(message: Message, symbol: str, timeframe: str, user_id: int, chat_id: int):
    analysis = await _run_analysis(symbol, timeframe)
    signal_id = recorder.record(
        symbol=symbol,
        timeframe=timeframe,
        analysis=analysis,
        owner_telegram_id=user_id,
        notification_chat_id=chat_id,
    )
    analysis["signal_id"] = signal_id
    await message.answer(
        report.build(analysis),
        parse_mode="HTML",
        reply_markup=analysis_actions_keyboard(symbol, timeframe),
    )


@router.message(F.text == "📊 Analyze")
async def analyze_menu(message: Message):
    await message.answer(
        "📊 Выберите монету или нажмите «🔍 Analyze Coin» для любого тикера:",
        reply_markup=analyze_keyboard(),
    )


@router.message(F.text == "🔍 Analyze Coin")
async def universal_analyze_start(message: Message, state: FSMContext):
    await state.set_state(UniversalAnalyzeState.waiting_symbol)
    await message.answer(
        "🔍 <b>Введите тикер монеты</b>\n\n"
        "Примеры OKX Futures: <code>BTC</code>, <code>SUI</code>, <code>TAO</code>, "
        "<code>PEPE</code> или <code>BTC-USDT-SWAP</code>.\n\n"
        "Для отмены: /cancel",
        parse_mode="HTML",
    )


@router.message(Command("cancel"))
async def cancel_universal(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("Отменено.")


@router.message(UniversalAnalyzeState.waiting_symbol)
async def universal_symbol_received(message: Message, state: FSMContext):
    try:
        resolved = await resolver.resolve(message.text, interval="1h")
    except ValueError as exc:
        await message.answer(f"❌ {exc}\n\nПопробуйте другой тикер или /cancel.")
        return

    await state.clear()
    await message.answer(
        f"⏱ Выберите таймфрейм для <b>{resolved.display}</b>:",
        parse_mode="HTML",
        reply_markup=timeframe_keyboard(resolved.base),
    )


@router.callback_query(F.data.startswith("ua:"))
async def universal_timeframe_callback(callback: CallbackQuery):
    _, raw_symbol, timeframe = callback.data.split(":", 2)
    await callback.answer("Анализирую…")
    try:
        resolved = await resolver.resolve(raw_symbol, interval=timeframe)
        await _send_analysis(
            callback.message,
            resolved.base,
            timeframe,
            callback.from_user.id,
            callback.message.chat.id,
        )
    except Exception as exc:
        await callback.message.answer(f"❌ Ошибка анализа\n\n{exc}")


@router.callback_query(F.data.startswith("analyze_"))
async def analyze_callback(callback: CallbackQuery):
    symbol = callback.data.replace("analyze_", "").upper()
    await callback.answer("Анализирую…")
    try:
        await _send_analysis(
            callback.message,
            symbol,
            "1h",
            callback.from_user.id,
            callback.message.chat.id,
        )
    except Exception as exc:
        await callback.message.answer(f"❌ Ошибка\n\n{exc}")


@router.callback_query(F.data.startswith("refresh_"))
async def refresh_callback(callback: CallbackQuery):
    symbol = callback.data.replace("refresh_", "").upper()
    await callback.answer("Обновляю анализ…")
    try:
        analysis = await _run_analysis(symbol, "1h")
        signal_id = recorder.record(
            symbol=symbol,
            timeframe="1h",
            analysis=analysis,
            owner_telegram_id=callback.from_user.id,
            notification_chat_id=callback.message.chat.id,
        )
        analysis["signal_id"] = signal_id
        await callback.message.edit_text(
            report.build(analysis),
            parse_mode="HTML",
            reply_markup=analysis_actions_keyboard(symbol, "1h"),
        )
    except Exception as exc:
        await callback.message.answer(f"❌ Не удалось обновить анализ\n\n{exc}")


@router.callback_query(F.data.startswith("explain_"))
async def explain_callback(callback: CallbackQuery):
    symbol = callback.data.replace("explain_", "").upper()
    await callback.answer("Готовлю объяснение…")
    try:
        analysis = await _run_analysis(symbol, "1h")
        await callback.message.answer(
            explainer.build(analysis, symbol),
            parse_mode="HTML",
            disable_web_page_preview=True,
        )
    except Exception as exc:
        await callback.message.answer(f"❌ Не удалось построить Explain Pro\n\n{exc}")


@router.callback_query(F.data.startswith("similar_"))
async def similar_callback(callback: CallbackQuery):
    symbol = callback.data.replace("similar_", "").upper()
    await callback.answer("Ищу похожие сетапы…")
    try:
        analysis = await _run_analysis(symbol, "1h")
        await callback.message.answer(
            similarity_report.build(symbol, analysis),
            parse_mode="HTML",
            disable_web_page_preview=True,
        )
    except Exception as exc:
        await callback.message.answer(f"❌ Не удалось найти похожие сетапы\n\n{exc}")


@router.callback_query(F.data.startswith("watch:"))
async def watch_callback(callback: CallbackQuery):
    _, symbol, timeframe = callback.data.split(":", 2)
    added = user_watchlist.add(callback.from_user.id, symbol, timeframe)
    if added:
        await callback.answer("Добавлено. Автомониторинг запущен ⭐", show_alert=True)
    else:
        await callback.answer("Уже отслеживается", show_alert=True)


@router.callback_query(F.data.startswith("unwatch:"))
async def unwatch_callback(callback: CallbackQuery):
    _, symbol, timeframe = callback.data.split(":", 2)
    removed = user_watchlist.remove(callback.from_user.id, symbol, timeframe)
    user_watchlist.clear_state(callback.from_user.id, symbol, timeframe)
    await callback.answer("Удалено из Watchlist" if removed else "Уже удалено", show_alert=True)
    await callback.message.answer("⭐ Watchlist обновлён. Нажмите кнопку Watchlist, чтобы открыть актуальный список.")


@router.message(F.text == "⭐ Watchlist")
async def watchlist_view(message: Message):
    rows = user_watchlist.list(message.from_user.id)
    if not rows:
        await message.answer(
            "⭐ <b>Watchlist пуст</b>\n\n"
            "Откройте анализ монеты и нажмите кнопку <b>⭐ Watch</b>.",
            parse_mode="HTML",
        )
        return

    from aiogram.utils.keyboard import InlineKeyboardBuilder
    lines = ["⭐ <b>Your Watchlist · Auto Monitor</b>", ""]
    kb = InlineKeyboardBuilder()
    for index, row in enumerate(rows, 1):
        status = "Initializing"
        score = "—"
        ready = "—"
        if row["snapshot_json"]:
            try:
                snap = json.loads(row["snapshot_json"])
                status = snap.get("execution_status") or "WATCHLIST"
                score = f"{float(snap.get('direction_score') or 0):.1f}"
                ready = f"{float(snap.get('readiness') or 0):.1f}"
            except Exception:
                pass
        lines.append(
            f"{index}. <b>{row['symbol']}</b> · {row['timeframe'].upper()}\n"
            f"   {status} · Direction {score} · Ready {ready}"
        )
        kb.button(text=f"🗑 {row['symbol']} {row['timeframe'].upper()}", callback_data=f"unwatch:{row['symbol']}:{row['timeframe']}")
    kb.adjust(2)
    lines.append("\nБот проверяет список автоматически и пишет только при существенных изменениях.")
    await message.answer("\n".join(lines), parse_mode="HTML", reply_markup=kb.as_markup())


@router.message(Command("analyze"))
async def analyze(message: Message):
    args = message.text.split()
    if len(args) < 2:
        await message.answer(
            "Использование:\n"
            "/analyze BTC\n"
            "/analyze SUI 4h\n"
            "/analyze PEPE 15m"
        )
        return

    timeframe = args[2].lower() if len(args) >= 3 else "1h"
    if timeframe not in {"15m", "1h", "4h", "1d"}:
        await message.answer("Поддерживаемые таймфреймы: 15m, 1h, 4h, 1d")
        return

    try:
        resolved = await resolver.resolve(args[1], interval=timeframe)
        await _send_analysis(
            message,
            resolved.base,
            timeframe,
            message.from_user.id,
            message.chat.id,
        )
    except Exception as exc:
        await message.answer(f"❌ Ошибка\n\n{exc}")
