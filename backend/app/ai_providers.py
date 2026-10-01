"""Chat providers a user can pick in the settings menu.

Claude goes through the Anthropic SDK; the others all speak the OpenAI
chat-completions format, so they share one client with a different base_url.
Default model ids are only suggestions — the user can type any model id their
provider offers.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Provider:
    id: str
    label: str
    default_model: str
    key_url: str
    base_url: str | None = None  # None = native SDK default endpoint
    openai_compatible: bool = True
    custom: bool = False  # user supplies base_url, api format and model


PROVIDERS: dict[str, Provider] = {
    p.id: p
    for p in [
        Provider(
            id="anthropic",
            label="Claude (Anthropic)",
            default_model="claude-opus-5-5",
            key_url="https://console.anthropic.com/settings/keys",
            openai_compatible=False,
        ),
        Provider(
            id="openai",
            label="ChatGPT (OpenAI)",
            default_model="gpt-5",
            key_url="https://platform.openai.com/api-keys",
        ),
        Provider(
            id="gemini",
            label="Gemini (Google)",
            default_model="gemini-2.5-flash",
            key_url="https://aistudio.google.com/apikey",
            base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
        ),
        Provider(
            id="mistral",
            label="Mistral",
            default_model="mistral-large-latest",
            key_url="https://console.mistral.ai/api-keys",
            base_url="https://api.mistral.ai/v1",
        ),
        Provider(
            id="custom",
            label="Custom",
            default_model="",
            key_url="",
            custom=True,
        ),
    ]
}

DEFAULT_PROVIDER = "anthropic"
API_FORMATS = ("openai", "anthropic")
