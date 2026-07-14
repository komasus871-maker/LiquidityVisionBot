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
from services.analysis_runtime import run_analysis
from services.decision_quality import DecisionQualityEngine
from services.market_context import MarketContextEngine

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
decision_quality = DecisionQualityEngine()
market_context = MarketContextEngine()

# Immutable in-process snapshots keep Explain/Similar consistent with the exact
# analysis the user received. Refresh is the only action that recalculates.
_ANALYSIS_SNAPSHOTS: dict[tuple[int, str, str], dict] = {}

def _snapshot_key(user_id: int, symbol: str, timeframe: str) -> tuple[int, str, str]:
    return (int(user_id), symbol.upper(), timeframe.lower())




def _get_snapshot(user_id: int, symbol: str, timeframe: str):
    return _ANALYSIS_SNAPSHOTS.get(_snapshot_key(user_id, symbol, timeframe))


async def _snapshot_or_run(user_id: int, symbol: str, timeframe: str):
    key = _snapshot_key(user_id, symbol, timeframe)
    analysis = _ANALYSIS_SNAPSHOTS.get(key)
    if analysis is None:
        analysis = await _run_analysis(symbol, timeframe)
        _ANALYSIS_SNAPSHOTS[key] = analysis.copy()
    return analysis


class UniversalAnalyzeState(StatesGroup):
    waiting_symbol = State()


async def _run_analysis(symbol: str, timeframe: str = "1h"):
    symbol = symbol.upper()
    df = await market.get_klines(symbol, interval=timeframe)
    analysis = await run_analysis(analyzer, df)
    analysis["timeframe"] = timeframe
    analysis["symbol"] = symbol

    btc_analysis = None
    if symbol != "BTC":
        try:
            btc_df = await market.get_klines("BTC", interval=timeframe)
            btc_analysis = await run_analysis(analyzer, btc_df)
        except Exception:
            btc_analysis = None
    analysis = market_context.enrich(analysis, symbol=symbol, btc=btc_analysis)
    setup_key = recorder._setup_key(analysis)
    analysis = probability_engine.enrich(
        analysis, symbol=symbol, timeframe=timeframe, setup_key=setup_key,
    )
    return decision_quality.enrich(analysis)


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
    _ANALYSIS_SNAPSHOTS[_snapshot_key(user_id, symbol, timeframe)] = analysis.copy()
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
    raw = (message.text or "").strip()
    if not raw:
        await message.answer("❌ Введите тикер или /cancel.")
        return

    # Commands must keep working even while the FSM is waiting for a plain
    # ticker. This prevents `/analyze TAO` from being parsed as one invalid
    # symbol after the Analyze Coin button was pressed earlier.
    normalized = raw.lstrip("-").strip()
    if normalized.lower().startswith("/analyze"):
        parts = normalized.split()
        if len(parts) < 2:
            await message.answer("Использование: /analyze BTC или /analyze SUI 4h")
            return
        timeframe = parts[2].lower() if len(parts) >= 3 else "1h"
        if timeframe not in {"15m", "1h", "4h", "1d"}:
            await message.answer("Поддерживаемые таймфреймы: 15m, 1h, 4h, 1d")
            return
        await state.clear()
        try:
            resolved = await resolver.resolve(parts[1], interval=timeframe)
            progress = await message.answer(f"🔄 Анализирую <b>{resolved.display}</b> · {timeframe.upper()}…", parse_mode="HTML")
            await _send_analysis(message, resolved.base, timeframe, message.from_user.id, message.chat.id)
            await progress.delete()
        except Exception as exc:
            await message.answer(f"❌ Ошибка\n\n{exc}")
        return

    try:
        resolved = await resolver.resolve(raw, interval="1h")
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


@router.callback_query((F.data.startswith("refresh:") | F.data.startswith("refresh_")))
async def refresh_callback(callback: CallbackQuery):
    raw = callback.data
    if raw.startswith("refresh:"):
        _, symbol, timeframe = raw.split(":", 2)
    else:
        symbol, timeframe = raw.replace("refresh_", "").upper(), "1h"
    await callback.answer("Обновляю анализ…")
    try:
        analysis = await _run_analysis(symbol, timeframe)
        signal_id = recorder.record(
            symbol=symbol, timeframe=timeframe, analysis=analysis,
            owner_telegram_id=callback.from_user.id,
            notification_chat_id=callback.message.chat.id,
        )
        analysis["signal_id"] = signal_id
        _ANALYSIS_SNAPSHOTS[_snapshot_key(callback.from_user.id, symbol, timeframe)] = analysis.copy()
        await callback.message.edit_text(
            report.build(analysis), parse_mode="HTML",
            reply_markup=analysis_actions_keyboard(symbol, timeframe),
        )
    except Exception as exc:
        await callback.message.answer(f"❌ Не удалось обновить анализ\n\n{exc}")


@router.callback_query((F.data.startswith("explain:") | F.data.startswith("explain_")))
async def explain_callback(callback: CallbackQuery):
    raw = callback.data
    if raw.startswith("explain:"):
        _, symbol, timeframe = raw.split(":", 2)
    else:
        symbol, timeframe = raw.replace("explain_", "").upper(), "1h"
    await callback.answer("Готовлю объяснение…")
    try:
        analysis = await _snapshot_or_run(callback.from_user.id, symbol, timeframe)
        await callback.message.answer(
            explainer.build(analysis, symbol), parse_mode="HTML", disable_web_page_preview=True,
        )
    except Exception as exc:
        await callback.message.answer(f"❌ Не удалось построить Explain Pro\n\n{exc}")


@router.callback_query(F.data.startswith("whynot:"))
async def why_not_callback(callback: CallbackQuery):
    _, symbol, timeframe = callback.data.split(":", 2)
    await callback.answer("Объясняю, почему вход отложен…")
    try:
        analysis = await _snapshot_or_run(callback.from_user.id, symbol, timeframe)
        await callback.message.answer(report.why_not(analysis), parse_mode="HTML")
    except Exception as exc:
        await callback.message.answer(f"❌ Не удалось открыть Why NOT\n\n{exc}")


@router.callback_query(F.data.startswith("technical:"))
async def technical_callback(callback: CallbackQuery):
    _, symbol, timeframe = callback.data.split(":", 2)
    await callback.answer("Открываю technical details…")
    try:
        analysis = await _snapshot_or_run(callback.from_user.id, symbol, timeframe)
        await callback.message.answer(report.technical(analysis), parse_mode="HTML")
    except Exception as exc:
        await callback.message.answer(f"❌ Не удалось открыть Technical Details\n\n{exc}")


@router.callback_query(F.data.startswith("scenarios:"))
async def scenarios_callback(callback: CallbackQuery):
    _, symbol, timeframe = callback.data.split(":", 2)
    await callback.answer("Открываю сценарии…")
    try:
        analysis = await _snapshot_or_run(callback.from_user.id, symbol, timeframe)
        await callback.message.answer(report.scenarios(analysis), parse_mode="HTML")
    except Exception as exc:
        await callback.message.answer(f"❌ Не удалось открыть Scenario Map\n\n{exc}")


@router.callback_query(F.data.startswith("history:"))
async def history_callback(callback: CallbackQuery):
    _, symbol, timeframe = callback.data.split(":", 2)
    await callback.answer("Открываю историю…")
    try:
        analysis = await _snapshot_or_run(callback.from_user.id, symbol, timeframe)
        await callback.message.answer(report.history(analysis), parse_mode="HTML")
    except Exception as exc:
        await callback.message.answer(f"❌ Не удалось открыть Historical Intelligence\n\n{exc}")


@router.callback_query((F.data.startswith("similar:") | F.data.startswith("similar_")))
async def similar_callback(callback: CallbackQuery):
    raw = callback.data
    if raw.startswith("similar:"):
        _, symbol, timeframe = raw.split(":", 2)
    else:
        symbol, timeframe = raw.replace("similar_", "").upper(), "1h"
    await callback.answer("Ищу похожие сетапы…")
    try:
        analysis = await _snapshot_or_run(callback.from_user.id, symbol, timeframe)
        await callback.message.answer(
            similarity_report.build(symbol, analysis), parse_mode="HTML", disable_web_page_preview=True,
        )
    except Exception as exc:
        await callback.message.answer(f"❌ Не удалось найти похожие сетапы\n\n{exc}")


@router.callback_query(F.data.startswith("watch:"))
async def watch_callback(callback: CallbackQuery):
    _, symbol, timeframe = callback.data.split(":", 2)
    added = user_watchlist.add(callback.from_user.id, symbol, timeframe)
    if added:
        await callback.answer("Добавлено в Watchlist ⭐", show_alert=True)
    else:
        await callback.answer("Уже находится в Watchlist", show_alert=True)


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

    import json
    lines = ["⭐ <b>Your Watchlist</b>", ""]
    for index, row in enumerate(rows, 1):
        snapshot = {}
        try:
            snapshot = json.loads(row.get("snapshot_json") or "{}")
        except (TypeError, ValueError):
            pass
        status = snapshot.get("execution_status") or "INITIALIZING"
        direction = float(snapshot.get("direction_score") or 0)
        readiness = float(snapshot.get("readiness") or 0)
        checked = row.get("last_checked_at") or row.get("updated_at") or "ещё не проверено"
        error = row.get("last_error")
        lines.append(f"{index}. <b>{row['symbol']}</b> · {row['timeframe'].upper()}")
        lines.append(f"   {status} · Dir {direction:.1f} · Ready {readiness:.1f}")
        lines.append(f"   Проверено: <code>{checked}</code>")
        if error:
            lines.append(f"   ⚠️ {str(error)[:160]}")
    lines.append("\nИспользуйте /analyze ТИКЕР ТФ для быстрого обновления.")
    await message.answer("\n".join(lines), parse_mode="HTML")


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
