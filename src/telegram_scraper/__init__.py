import warnings

# Suppress Telethon's experimental async sessions warning
warnings.filterwarnings(
    "ignore",
    message=".*async sessions support is an experimental feature.*",
    category=UserWarning,
)
