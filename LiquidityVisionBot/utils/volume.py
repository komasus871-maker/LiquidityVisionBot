def relative_volume(df, period=20):

    avg = df["volume"].rolling(period).mean()

    return df["volume"] / avg