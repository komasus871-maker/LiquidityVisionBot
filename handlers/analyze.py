from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery

from keyboards.analyze import analyze_keyboard
from keyboards.analysis_actions import analysis_actions_keyboard

from services.market import Market
from services.analyzer import Analyzer
from services.report import Report
from services.signal_recorder import SignalRecorder
from services.explainer import Explainer

router = Router()

market = Market()
analyzer = Analyzer()
report = Report()
recorder = SignalRecorder()
explainer = Explainer()


async def _run_analysis(symbol: str):
    df = await market.get_klines(symbol)
    return analyzer.analyze(df)


@router.message(F.text == "📊 Analyze")
async def analyze_menu(message: Message):
    await message.answer("📊 Выберите монету:", reply_markup=analyze_keyboard())


@router.callback_query(F.data.startswith("analyze_"))
async def analyze_callback(callback: CallbackQuery):
    symbol = callback.data.replace("analyze_", "").upper()
    await callback.answer("Анализирую…")
    try:
        analysis = await _run_analysis(symbol)
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
            reply_markup=analysis_actions_keyboard(symbol),
        )
    except Exception as e:
        await callback.message.answer(f"❌ Ошибка\n\n{e}")


@router.callback_query(F.data.startswith("refresh_"))
async def refresh_callback(callback: CallbackQuery):
    symbol = callback.data.replace("refresh_", "").upper()
    await callback.answer("Обновляю анализ…")
    try:
        analysis = await _run_analysis(symbol)
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
            reply_markup=analysis_actions_keyboard(symbol),
        )
    except Exception as e:
        await callback.message.answer(f"❌ Не удалось обновить анализ\n\n{e}")


@router.callback_query(F.data.startswith("explain_"))
async def explain_callback(callback: CallbackQuery):
    symbol = callback.data.replace("explain_", "").upper()
    await callback.answer("Готовлю объяснение…")
    try:
        analysis = await _run_analysis(symbol)
        await callback.message.answer(
            explainer.build(analysis, symbol),
            parse_mode="HTML",
            disable_web_page_preview=True,
        )
    except Exception as e:
        await callback.message.answer(f"❌ Не удалось построить Explain Pro\n\n{e}")


@router.message(Command("analyze"))
async def analyze(message: Message):
    args = message.text.split()
    if len(args) < 2:
        await message.answer("Использование:\n/analyze BTC\n/analyze ETH\n/analyze SOL")
        return

    symbol = args[1].upper()
    try:
        analysis = await _run_analysis(symbol)
        signal_id = recorder.record(
            symbol=symbol,
            timeframe="1h",
            analysis=analysis,
            owner_telegram_id=message.from_user.id,
            notification_chat_id=message.chat.id,
        )
        analysis["signal_id"] = signal_id
        await message.answer(
            report.build(analysis),
            parse_mode="HTML",
            reply_markup=analysis_actions_keyboard(symbol),
        )
    except Exception as e:
        await message.answer(f"❌ Ошибка\n\n{e}")
