from textblob import TextBlob


def analyze_sentiment(text: str) -> float:
    """Return TextBlob polarity score in [-1.0, 1.0]. 0.0 on empty input."""
    if not text or not text.strip():
        return 0.0
    return TextBlob(text).sentiment.polarity
