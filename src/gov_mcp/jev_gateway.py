"""Explicit, bounded Jev-only Vercel transport. No chat models or automatic fallback."""
from copy import deepcopy
from decimal import Decimal, InvalidOperation
import json
import os
from pathlib import Path
import time
import urllib.error
import urllib.request

from .classification import ClassificationError, validate_response
from .json_output import object_from_json

ENDPOINT = "https://ai-gateway.vercel.sh/typesafe/v1/systemone"
MODEL = "jev-1.13.0"
GATEWAY_MODEL = "typesafe-ai/jev-1.13.0"


def safe_http_diagnostic(error):
    """Keep only fixed diagnostic categories; never persist upstream messages or keys."""
    try:
        raw = error.read(16385)
        if len(raw) > 16384: return "ERROR_BODY_TOO_LARGE"
        value = json.loads(raw)
        detail = value.get("error", value)
        if not isinstance(detail, dict): return "UNRECOGNIZED_ERROR"
        content = json.dumps(detail).lower()
        if "customer_verification_required" in content: return "CUSTOMER_VERIFICATION_REQUIRED"
        if "restrictedmodels" in content or ("free tier" in content and ("access" in content or "model" in content)):
            return "FREE_TIER_MODEL_RESTRICTED"
        if "model_not_available_on_plan" in content: return "MODEL_NOT_AVAILABLE_ON_PLAN"
        if "access_denied" in content: return "ACCESS_DENIED"
        if "insufficient" in content and ("credit" in content or "balance" in content): return "INSUFFICIENT_CREDITS"
        return "UNRECOGNIZED_JSON_ERROR"
    except (ValueError, TypeError, AttributeError, OSError):
        return "NON_JSON_ERROR"


def load_gateway_key():
    """Read only the explicitly designated server credential; never echo it."""
    key = os.environ.get("AI_GATEWAY_API_KEY", "")
    path = Path(__file__).resolve().parents[2]/".runtime/jev/credentials.json"
    if not key and path.exists(): key = json.loads(path.read_text("utf-8")).get("AI_GATEWAY_API_KEY", "")
    return key


class VercelJevProvider:
    name = "vercel_jev_live"

    def __init__(self, api_key=None, *, opener=None, max_calls=32):
        if type(max_calls) is not int or not 1 <= max_calls <= 32:
            raise ValueError("INVALID_CALL_BUDGET")
        self.api_key = load_gateway_key() if api_key is None else api_key
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *args, **kwargs): return None
        self.open = opener or urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect()).open
        self.max_calls, self.attempts = max_calls, 0
        self.receipts, self.blocked_error = [], None
        self.known_cost = Decimal("0")

    def check_ready(self, model):
        if model != MODEL: raise ClassificationError("MODEL_NOT_PINNED")
        if not isinstance(self.api_key, str) or not self.api_key.strip(): raise ClassificationError("MISSING_API_KEY")
        if self.blocked_error: raise ClassificationError(self.blocked_error)
        if self.attempts >= self.max_calls or self.known_cost >= Decimal("0.10"):
            raise ClassificationError("TEST_BUDGET_EXHAUSTED")

    def evaluate(self, state, questions, model):
        self.check_ready(model)
        if not isinstance(questions, dict) or not 1 <= len(questions) <= 32: raise ClassificationError("INVALID_REQUEST")
        # Pinned model, pinned origin, no fallback models, no proxies or redirects.
        body = json.dumps({"model": GATEWAY_MODEL, "state": state, "questions": questions,
                           "providerOptions": {"gateway": {"only": ["typesafe-ai"]}}},
                          ensure_ascii=False, allow_nan=False).encode("utf-8")
        if len(body) > 16000: raise ClassificationError("MODEL_INPUT_TOO_LARGE")
        request = urllib.request.Request(ENDPOINT, data=body, method="POST", headers={
            "Authorization": "Bearer "+self.api_key, "Content-Type": "application/json"})
        start = time.perf_counter()
        self.attempts += 1
        receipt = {"attempt": self.attempts, "endpoint": ENDPOINT, "requested_model": model,
                   "transport_model": GATEWAY_MODEL, "input_bytes": len(body), "status": "error", "cost_usd": None}
        try:
            with self.open(request, timeout=30) as response:
                receipt["http_status"] = response.status
                raw = response.read(2*1024*1024+1)
                if len(raw) > 2*1024*1024: raise ClassificationError("INVALID_RESPONSE")
                value = object_from_json(raw)
            # Preserve the actual returned model instead of relabelling an alias/fallback.
            served = value.get("model")
            receipt["served_model"] = served
            gateway = value.get("provider_metadata", {}).get("gateway", {})
            routing = gateway.get("routing", {})
            if served != GATEWAY_MODEL or routing.get("finalProvider", "typesafe-ai") != "typesafe-ai":
                raise ClassificationError("UNEXPECTED_SERVED_MODEL")
            for key in ("generationId",):
                if isinstance(gateway.get(key), str): receipt[key] = gateway[key]
            cost = gateway.get("cost")
            if cost is not None:
                try: amount = Decimal(str(cost))
                except InvalidOperation: raise ClassificationError("INVALID_RESPONSE") from None
                if not amount.is_finite() or amount < 0: raise ClassificationError("INVALID_RESPONSE")
                self.known_cost += amount
                receipt["cost_usd"] = str(amount)
            normalized = deepcopy(value)
            normalized["model"] = MODEL
            normalized = validate_response(normalized, questions, MODEL)
            receipt.update(status="ok", usage=normalized["usage"])
            return normalized
        except urllib.error.HTTPError as error:
            receipt["http_status"] = error.code
            receipt["upstream_reason"] = safe_http_diagnostic(error)
            code = {401: "AUTHENTICATION_FAILED", 402: "PAYMENT_REQUIRED", 403: "ACCESS_DENIED", 429: "RATE_LIMITED"}.get(error.code, "UPSTREAM_HTTP_ERROR")
            error.close()
            self.blocked_error = code
            receipt["error_code"] = code
            raise ClassificationError(code) from None
        except ClassificationError as error:
            self.blocked_error = receipt["error_code"] = str(error)
            raise
        except (TimeoutError, urllib.error.URLError):
            self.blocked_error = receipt["error_code"] = "TRANSPORT_UNAVAILABLE"
            raise ClassificationError("TRANSPORT_UNAVAILABLE") from None
        except (ValueError, KeyError, TypeError, AttributeError, RecursionError):
            self.blocked_error = receipt["error_code"] = "INVALID_RESPONSE"
            raise ClassificationError("INVALID_RESPONSE") from None
        finally:
            receipt["duration_ms"] = round((time.perf_counter()-start)*1000, 2)
            self.receipts.append(receipt)
