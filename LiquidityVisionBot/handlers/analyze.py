from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery

from keyboards.analyze import analyze_keyboard
from keyboards.analysis_actions import analysis_actions

from services.market import Market
from services.analyzer import Analyzer
from services.report import Report
from services.signal_recorder import SignalRecorder
from services.probability_foundation import ProbabilityFoundation

router = Router()

market = Market()
analyzer = Analyzer()
report = Report()
recorder = SignalRecorder()
probability = ProbabilityFoundation(recorder.history)


@router.message(F.text == "📊 Analyze")
async def analyze_menu(message: Message):

    await message.answer(

        "📊 Выберите монету:",

        reply_markup=analyze_keyboard()

    )


@router.callback_query(F.data.startswith("analyze_"))
async def analyze_callback(callback: CallbackQuery):

    symbol = callback.data.replace(

        "analyze_",

        ""

    )

    try:

        df = await market.get_klines(symbol)

        analysis = analyzer.analyze(df)
        setup_key = recorder._setup_key(analysis)
        stats = probability.for_setup(setup_key, "1h", analysis["direction"])
        analysis["historical_probability"] = probability.as_dict(stats)
        signal_id = recorder.record(symbol=symbol, timeframe="1h", analysis=analysis, owner_telegram_id=callback.from_user.id, notification_chat_id=callback.message.chat.id)
        analysis["signal_id"] = signal_id

        text = report.build(analysis)

        await callback.message.edit_text(

            text,

            parse_mode="HTML",
            reply_markup=analysis_actions(symbol, signal_id)

        )

    except Exception as e:

        await callback.message.answer(

            f"❌ Ошибка\n\n{e}"

        )

    await callback.answer()


@router.message(Command("analyze"))
async def analyze(message: Message):

    args = message.text.split()

    if len(args) < 2:

        await message.answer(

            "Использование:\n"

            "/analyze BTC\n"

            "/analyze ETH\n"

            "/analyze SOL"

        )

        return

    symbol = args[1].upper()

    try:

        df = await market.get_klines(symbol)

        analysis = analyzer.analyze(df)
        setup_key = recorder._setup_key(analysis)
        stats = probability.for_setup(setup_key, "1h", analysis["direction"])
        analysis["historical_probability"] = probability.as_dict(stats)
        signal_id = recorder.record(symbol=symbol, timeframe="1h", analysis=analysis, owner_telegram_id=message.from_user.id, notification_chat_id=message.chat.id)
        analysis["signal_id"] = signal_id

        text = report.build(analysis)

        await message.answer(

            text,

            parse_mode="HTML",
            reply_markup=analysis_actions(symbol, signal_id)

        )

    except Exception as e:

        await message.answer(

            f"❌ Ошибка\n\n{e}"

        )