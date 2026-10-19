"""Central configuration + tiny .env loader (no external deps)."""
import os
import math
from pathlib import Path

def _load_dotenv() -> None:
    env_path = Path(__file__).resolve().parent.parent / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        os.environ.setdefault(key.strip(), val.strip())

_load_dotenv()

# --- Foundry Local (on-device) ---
FOUNDRY_ENDPOINT = os.getenv("FOUNDRY_ENDPOINT", "").rstrip("/")  # auto-discovered if empty
JUDGE_MODEL = os.getenv("JUDGE_MODEL", "phi-4-mini")

def _judge_timeout_seconds() -> float:
    try:
        value = float(os.getenv("JUDGE_TIMEOUT_SECONDS", "30"))
    except ValueError:
        raise ValueError("JUDGE_TIMEOUT_SECONDS must be a finite positive number.") from None
    if not math.isfinite(value) or value <= 0:
        raise ValueError("JUDGE_TIMEOUT_SECONDS must be a finite positive number.")
    return value

JUDGE_TIMEOUT_SECONDS = _judge_timeout_seconds()
LOCAL_MODEL = os.getenv("LOCAL_MODEL", "phi-4-mini")
# Used to SIMULATE the cloud tier when no Azure creds are configured:
CLOUD_SIM_MODEL = os.getenv("CLOUD_SIM_MODEL", "phi-4-mini")
BRAND_NAME = os.getenv("BRAND_NAME", "Use AI in the Right Place")

# --- Cloud tier (Azure AI Foundry / Azure OpenAI) ---
AZURE_OPENAI_ENDPOINT = os.getenv("AZURE_OPENAI_ENDPOINT", "").rstrip("/")
AZURE_OPENAI_API_KEY = os.getenv("AZURE_OPENAI_API_KEY", "")
AZURE_OPENAI_DEPLOYMENT = os.getenv("AZURE_OPENAI_DEPLOYMENT", "")
# If no API key is set but an endpoint+deployment are, use Entra ID (az login / DefaultAzureCredential).
AZURE_USE_AAD = os.getenv("AZURE_USE_AAD", "false")  # "true" requires an authenticated credential

def azure_openai_base_url() -> str:
    """Normalize a resource, v1, or responses URL to the OpenAI v1 base URL."""
    endpoint = AZURE_OPENAI_ENDPOINT.removesuffix("/responses")
    if not endpoint.endswith("/openai/v1"):
        endpoint = f"{endpoint}/openai/v1"
    return f"{endpoint}/"

def cloud_is_real() -> bool:
    if not (AZURE_OPENAI_ENDPOINT and AZURE_OPENAI_DEPLOYMENT):
        return False
    return bool(AZURE_OPENAI_API_KEY) or _use_aad()

def _use_aad() -> bool:
    return AZURE_USE_AAD.lower() == "true"

# --- Demo defaults ---
DEFAULT_BUDGET_KB = float(os.getenv("DEFAULT_BUDGET_KB", "40"))

# --- Impact estimation (for local-vs-cloud savings meter) ---
# Coefficients are intentionally exposed as env vars so hackathon judges can tune assumptions.
EST_USD_PER_KB = float(os.getenv("EST_USD_PER_KB", "0.00008"))
EST_USD_PER_CLOUD_REQ = float(os.getenv("EST_USD_PER_CLOUD_REQ", "0.0030"))
EST_CO2G_PER_KB = float(os.getenv("EST_CO2G_PER_KB", "0.25"))
EST_CO2G_PER_CLOUD_REQ = float(os.getenv("EST_CO2G_PER_CLOUD_REQ", "1.8"))
USD_TO_EUR = float(os.getenv("USD_TO_EUR", "0.92"))
