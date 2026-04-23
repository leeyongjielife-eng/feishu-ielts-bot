#!/usr/bin/env python3
import os
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from typing import Dict

import google.generativeai as genai
from openai import APIConnectionError, APITimeoutError, RateLimitError


GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-pro")


def _is_retryable_error(err: Exception) -> bool:
    if isinstance(
        err,
        (
            TimeoutError,
            FuturesTimeoutError,
            APITimeoutError,
            APIConnectionError,
            RateLimitError,
        ),
    ):
        return True

    status_code = getattr(err, "status_code", None)
    if status_code == 429 or (isinstance(status_code, int) and 500 <= status_code <= 599):
        return True

    msg = str(err).lower()
    retryable_keywords = [
        "timed out",
        "timeout",
        "network",
        "connection",
        "read tcp",
        "temporarily unavailable",
        "429",
        "500",
        "502",
        "503",
        "504",
    ]
    return any(k in msg for k in retryable_keywords)


def _call_gemini(prompt: str) -> str:
    if not GEMINI_API_KEY:
        raise RuntimeError("GEMINI_API_KEY is missing in environment variables")

    genai.configure(api_key=GEMINI_API_KEY)
    model = genai.GenerativeModel(GEMINI_MODEL)

    executor = ThreadPoolExecutor(max_workers=1)
    future = executor.submit(model.generate_content, prompt, request_options={"timeout": 40})
    try:
        response = future.result(timeout=45)
    except FuturesTimeoutError as exc:
        future.cancel()
        raise RuntimeError("gemini request timed out after 45s") from exc
    finally:
        executor.shutdown(wait=False, cancel_futures=True)

    return (getattr(response, "text", "") or "").strip()


def call_ai_with_fallback(prompt: str) -> Dict:
    started = time.perf_counter()
    try:
        text = _call_gemini(prompt)
        return {
            "ok": True,
            "provider_used": "gemini",
            "fallback_triggered": False,
            "latency_ms": int((time.perf_counter() - started) * 1000),
            "text": text,
        }
    except Exception as err:
        # Fast one-time retry for transient timeout/network hiccups.
        if _is_retryable_error(err):
            try:
                text = _call_gemini(prompt)
                return {
                    "ok": True,
                    "provider_used": "gemini",
                    "fallback_triggered": False,
                    "latency_ms": int((time.perf_counter() - started) * 1000),
                    "text": text,
                }
            except Exception as retry_err:
                return {
                    "ok": False,
                    "provider_used": "none",
                    "fallback_triggered": False,
                    "latency_ms": int((time.perf_counter() - started) * 1000),
                    "error": f"gemini_error={err}; retry_error={retry_err}",
                    "retryable": _is_retryable_error(retry_err),
                }
        return {
            "ok": False,
            "provider_used": "none",
            "fallback_triggered": False,
            "latency_ms": int((time.perf_counter() - started) * 1000),
            "error": f"gemini_error={err}",
            "retryable": _is_retryable_error(err),
        }
