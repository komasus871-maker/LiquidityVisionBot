from aiogram import Router, F
from aiogram.types import CallbackQuery

from database.signal_history import SignalHistory
from services.analyzer import Analyzer
from services.explain_pro import ExplainPro
from services.market import Market
from services.probability_foundation import ProbabilityFoundation
from services.signal_recorder import SignalRecorder

router = Router()
market = Market()
analyzer = Analyzer()
explainer = ExplainPro()
history = SignalHistory()
probability = ProbabilityFoundation(history)
recorder = SignalRecorder(history)


@router.callback_query(F.data.startswith("explain_"))
async def explain_callback(callback: CallbackQuery):
    _, symbol, raw_id = callback.data.split("_", 2)
    signal_id = int(raw_id or 0)
    try:
        if signal_id:
            stored = history.get_by_id(signal_id)
        else:
            stored = None

        df = await market.get_klines(symbol)
        analysis = analyzer.analyze(df)
        setup_key = recorder._setup_key(analysis)
        stats = probability.for_setup(setup_key, "1h", analysis["direction"])
        analysis["historical_probability"] = probability.as_dict(stats)
        if stored:
            analysis["signal_id"] = stored["id"]

        await callback.message.answer(explainer.build(analysis), parse_mode="HTML")
    except Exception as exc:
        await callback.message.answer(f"❌ Explain Pro error\n\n{exc}")
    await callback.answer()
