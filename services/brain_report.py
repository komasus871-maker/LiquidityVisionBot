class BrainReport:

    def build(self, brain):

        text = ""

        for reason in brain["reasons"]:

            text += reason + "\n"

        return f"""

━━━━━━━━━━━━━━━━━━

🧠 Liquidity Vision Brain

{text}

━━━━━━━━━━━━━━━━━━

🎯 Probability

{brain["probability"]}%

━━━━━━━━━━━━━━━━━━

🚀 Signal

{brain["signal"]}

"""