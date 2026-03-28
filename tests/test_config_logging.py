import logging

import hr_breaker.config as config_module


def test_setup_logging_uses_millisecond_precision(monkeypatch):
    root = logging.getLogger()
    original_handlers = list(root.handlers)
    for handler in list(root.handlers):
        root.removeHandler(handler)

    try:
        monkeypatch.setenv("LOG_LEVEL_GENERAL", "INFO")
        monkeypatch.setenv("LOG_LEVEL", "INFO")
        config_module.setup_logging()

        assert root.handlers, "Expected setup_logging() to install a handler"
        formatter = root.handlers[0].formatter
        assert formatter is not None
        assert formatter._fmt == "%(asctime)s.%(msecs)03d [%(levelname)s] %(name)s - %(message)s"
        assert formatter.datefmt == "%H:%M:%S"
    finally:
        for handler in list(root.handlers):
            root.removeHandler(handler)
        for handler in original_handlers:
            root.addHandler(handler)
