from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

router = Router()


@router.message(Command("help"))
async def help_command(message: Message):
    await message.answer("""
📚 <b>Liquidity Vision Commands</b>

<b>Market intelligence</b>
/analyze BTC
/price BTC
/performance
/portfolio
/dna
/insights

<b>Trade lifecycle</b>
/trade 12
/trade 12 close
/trade all close
/trade stats audit
/closeall

<b>Account</b>
/profile
/premium
/help
""", parse_mode="HTML")
