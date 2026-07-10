def ema(prices, period):

    k = 2 / (period + 1)

    values = []

    value = prices[0]

    for price in prices:

        value = price * k + value * (1 - k)

        values.append(value)

    return values


def macd(prices):

    ema12 = ema(prices, 12)

    ema26 = ema(prices, 26)

    line = []

    for i in range(len(prices)):

        line.append(ema12[i] - ema26[i])

    signal = ema(line, 9)

    histogram = []

    for i in range(len(line)):

        histogram.append(line[i] - signal[i])

    return line[-1], signal[-1], histogram[-1]